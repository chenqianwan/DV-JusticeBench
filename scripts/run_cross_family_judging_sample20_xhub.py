#!/usr/bin/env python3
"""Cross-family judging for the frozen additional-20 Experiment-1 sample.

Only the final evaluation stage is executed. Existing answers, per-condition
masked case inputs, questions, and reference judgments are reused byte-for-byte.
Each answer receives three blind ratings from model families other than the
answering family. Existing DeepSeek ratings are reused where they are eligible.

The runner is restartable: every rating is written atomically to its own file.
Rerunning the same command skips valid completed ratings and retries only missing
or failed tasks.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import inspect
import json
import os
import statistics
import sys
import threading
import time
from collections import Counter, defaultdict
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.run_experiment1_smoke_xhub import (  # noqa: E402
    XHubClient,
    analysis_messages,
    pipeline_call_snapshot,
    sha256_text,
)
from utils.evaluator import AnswerEvaluator  # noqa: E402


DEFAULT_SOURCE = ROOT / "data/experiment1_additional20_xhub_20260819/combined_results.json"
DEFAULT_OUTPUT = ROOT / "data/experiment1_cross_judge_sample20_xhub_20260822"
DEFAULT_XHUB_ENV = Path(
    os.environ.get("XHUB_ENV_FILE", str(ROOT.parent / "AI_Council" / ".env"))
).expanduser()

DIMENSIONS = [
    "规范依据相关性",
    "涵摄链条对齐度",
    "价值衡量与同理心对齐度",
    "关键事实与争点覆盖度",
    "裁判结论与救济配置一致性",
]

ANSWER_FAMILY = {
    "GPT-4o": "GPT",
    "Gemini 2.5 Flash": "Gemini",
    "Qwen-Max": "Qwen",
    "DeepSeek thinking": "DeepSeek",
    "DeepSeek non-thinking": "DeepSeek",
}

JUDGES = {
    "GPT-4o": {"family": "GPT", "model_id": "gpt-4o", "thinking": None},
    "Gemini 2.5 Flash": {
        "family": "Gemini",
        "model_id": "gemini-2.5-flash",
        "thinking": None,
    },
    "Qwen-Max": {"family": "Qwen", "model_id": "qwen-max", "thinking": None},
    "DeepSeek v3.2": {
        "family": "DeepSeek",
        "model_id": "deepseek-v3.2",
        "thinking": True,
    },
}

XHUB_PRICES_USD_PER_MILLION = {
    "gpt-4o": (2.5, 10.0),
    "gemini-2.5-flash": (0.3, 2.499),
    "qwen-max": (0.8348, 3.3392),
    "deepseek-v3.2": (0.28, 0.42),
}


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds")


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
    with temporary.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def task_id(answer_id: str, judge_label: str) -> str:
    prefix = hashlib.sha256(f"{answer_id}|{judge_label}".encode("utf-8")).hexdigest()[:20]
    return f"rating_{prefix}"


def answer_id(case_id: str, question_number: int, condition: str) -> str:
    return f"{case_id}::q{question_number}::{condition}"


def usage_cost(model: str, usage: Dict[str, Any]) -> float:
    prices = XHUB_PRICES_USD_PER_MILLION.get(model)
    if not prices:
        return 0.0
    prompt_price, completion_price = prices
    return (
        float(usage.get("prompt_tokens") or 0) * prompt_price
        + float(usage.get("completion_tokens") or 0) * completion_price
    ) / 1_000_000


class LegacyJudgeAdapter:
    """Expose the legacy evaluator API while routing to a selected xhub judge."""

    def __init__(
        self,
        client: XHubClient,
        *,
        model_id: str,
        family: str,
        thinking: Optional[bool],
    ):
        self.client = client
        self.model_id = model_id
        self.provider = "deepseek" if family == "DeepSeek" else family.lower()
        self.thinking = thinking
        self.last_call: Optional[Dict[str, Any]] = None
        self.calls: List[Dict[str, Any]] = []

    def analyze_case(
        self,
        case_text: str,
        question: Optional[str] = None,
        use_thinking: bool = False,
    ) -> Dict[str, str]:
        request_kwargs = {
            "model": self.model_id,
            "messages": analysis_messages(case_text, question),
            "max_tokens": 3000,
            "temperature": 0.3,
            "thinking": self.thinking,
            "stage": "cross_family_scoring",
        }
        result = self.client.chat(**request_kwargs)
        self.last_call = result
        self.calls.append(result)
        if not result["ok"]:
            raise RuntimeError(result["error"])

        # Preserve the historical empty-visible-answer recovery behavior.
        if not result["content"].strip():
            for empty_retry in range(1, 4):
                result = self.client.chat(**request_kwargs)
                self.last_call = result
                self.calls.append(result)
                if result["ok"] and result["content"].strip():
                    break
                if empty_retry < 3:
                    time.sleep(2)
            if not result["ok"]:
                raise RuntimeError(result["error"])
            if not result["content"].strip():
                raise RuntimeError("API返回content为空，重试3次后仍失败")

        return {
            "answer": result["content"],
            "thinking": result.get("reasoning_content", ""),
        }


def parse_layers(evaluator: AnswerEvaluator, evaluation: Dict[str, Any]) -> Dict[str, Any]:
    detailed = str(evaluation.get("详细评价") or "")
    raw_scores = evaluator._parse_scores(detailed)  # exact legacy parser
    threshold_scores = evaluator._apply_threshold_rules(deepcopy(raw_scores), detailed)
    return {
        "raw_scores": raw_scores,
        "threshold_scores": threshold_scores,
        "final_scores": evaluation.get("各维度得分") or {},
        "errors": evaluation.get("错误详情") or {},
    }


def load_source_answers(source_path: Path) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    combined = load_json(source_path)
    raw_paths = combined.get("per_case_raw_results") or {}
    if len(raw_paths) != 20:
        raise SystemExit(f"source must contain exactly 20 cases; found {len(raw_paths)}")

    answers: List[Dict[str, Any]] = []
    for case_id, raw_name in raw_paths.items():
        raw_path = Path(raw_name)
        raw = load_json(raw_path)
        condition_inputs = raw.get("input", {}).get("condition_inputs") or {}
        rows = raw.get("rows") or []
        if len(rows) != 25:
            raise SystemExit(f"{case_id}: expected 25 answer rows, found {len(rows)}")
        for row in rows:
            condition = str(row.get("condition") or "")
            if condition not in ANSWER_FAMILY:
                raise SystemExit(f"{case_id}: unknown answer condition {condition!r}")
            condition_input = condition_inputs.get(condition) or {}
            generation = row.get("generation") or {}
            answer = str(generation.get("content") or "")
            question = str(row.get("question") or "")
            masked_case = str(condition_input.get("masked_case_text") or "")
            masked_judgment = str(condition_input.get("masked_judgment") or "")
            if not all([answer.strip(), question.strip(), masked_case.strip(), masked_judgment.strip()]):
                raise SystemExit(f"{case_id}/{condition}/q{row.get('question_number')}: empty source field")
            qn = int(row.get("question_number") or 0)
            aid = answer_id(case_id, qn, condition)
            answers.append(
                {
                    "answer_id": aid,
                    "case_id": case_id,
                    "case_title": raw.get("input", {}).get("case_title") or "",
                    "masked_title": condition_input.get("masked_title") or "",
                    "question_number": qn,
                    "question": question,
                    "answer_condition": condition,
                    "answer_family": ANSWER_FAMILY[condition],
                    "answer_operational_model_id": row.get("operational_model_id") or "",
                    "answer": answer,
                    "masked_case_text": masked_case,
                    "masked_judgment": masked_judgment,
                    "input_hash": condition_input.get("input_hash") or "",
                    "question_sha256": sha256_text(question),
                    "answer_sha256": sha256_text(answer),
                    "masked_case_sha256": sha256_text(masked_case),
                    "masked_judgment_sha256": sha256_text(masked_judgment),
                    "source_raw_path": str(raw_path.resolve()),
                    "source_row": row,
                }
            )
    if len(answers) != 500:
        raise SystemExit(f"source must contain exactly 500 answers; found {len(answers)}")
    if len({item['answer_id'] for item in answers}) != len(answers):
        raise SystemExit("source answer identifiers are not unique")
    return combined, answers


def build_tasks(answers: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    tasks: List[Dict[str, Any]] = []
    for answer in answers:
        eligible = [
            (label, spec)
            for label, spec in JUDGES.items()
            if spec["family"] != answer["answer_family"]
        ]
        if len(eligible) != 3:
            raise SystemExit(f"{answer['answer_id']}: expected 3 cross-family judges")
        for judge_label, judge in eligible:
            tid = task_id(answer["answer_id"], judge_label)
            task_source = "reused_existing" if judge["family"] == "DeepSeek" else "new_api"
            tasks.append(
                {
                    "task_id": tid,
                    "answer_id": answer["answer_id"],
                    "case_id": answer["case_id"],
                    "question_number": answer["question_number"],
                    "answer_condition": answer["answer_condition"],
                    "answer_family": answer["answer_family"],
                    "judge_label": judge_label,
                    "judge_family": judge["family"],
                    "judge_model_id": judge["model_id"],
                    "judge_thinking": judge["thinking"],
                    "source": task_source,
                    "input_hash": answer["input_hash"],
                    "question_sha256": answer["question_sha256"],
                    "answer_sha256": answer["answer_sha256"],
                    "masked_case_sha256": answer["masked_case_sha256"],
                    "masked_judgment_sha256": answer["masked_judgment_sha256"],
                }
            )
    if len(tasks) != 1500:
        raise SystemExit(f"expected 1500 rating tasks, found {len(tasks)}")
    if Counter(item["source"] for item in tasks) != {"new_api": 1200, "reused_existing": 300}:
        raise SystemExit("unexpected new/reused task counts")
    return tasks


def result_is_valid(path: Path, task: Dict[str, Any]) -> bool:
    if not path.exists():
        return False
    try:
        result = load_json(path)
    except Exception:
        return False
    if result.get("status") != "success" or result.get("task_id") != task["task_id"]:
        return False
    if result.get("answer_sha256") != task["answer_sha256"]:
        return False
    evaluation = result.get("evaluation") or {}
    scores = evaluation.get("各维度得分") or {}
    return all(dimension in scores for dimension in DIMENSIONS)


def public_answer(answer: Dict[str, Any]) -> Dict[str, Any]:
    return {key: value for key, value in answer.items() if key != "source_row"}


def make_reused_result(task: Dict[str, Any], answer: Dict[str, Any]) -> Dict[str, Any]:
    source_row = answer["source_row"]
    scoring = source_row.get("scoring") or {}
    evaluation = source_row.get("evaluation") or {}
    if scoring.get("requested_model") != "deepseek-v3.2" or not scoring.get("ok"):
        raise RuntimeError(f"{task['task_id']}: ineligible existing DeepSeek receipt")
    parser = AnswerEvaluator(api=object(), flag_parser_version="legacy")
    layers = parse_layers(parser, evaluation)
    return {
        **task,
        "status": "success",
        "completed_at": now_iso(),
        "reuse_verified": {
            "requested_model": scoring.get("requested_model"),
            "response_model": scoring.get("response_model"),
            "source_prompt_template_hash": "0a97d478a532c0463e0cb6c09f244c16108dd8ec046e7686541a04852592627f",
            "source_flag_parser_version": "legacy",
            "source_input_hash": answer["input_hash"],
        },
        "evaluation": evaluation,
        **layers,
        "scoring": scoring,
        "attempt_history": [
            {
                "task_attempt": 1,
                "status": "reused_existing",
                "scoring": pipeline_call_snapshot(scoring),
                "error": "",
            }
        ],
    }


def run_new_task(
    task: Dict[str, Any],
    answer: Dict[str, Any],
    client: XHubClient,
) -> Dict[str, Any]:
    attempt_history: List[Dict[str, Any]] = []
    last_error = ""
    last_call: Optional[Dict[str, Any]] = None
    judge = JUDGES[task["judge_label"]]

    for task_attempt in range(1, 4):
        try:
            adapter = LegacyJudgeAdapter(
                client,
                model_id=judge["model_id"],
                family=judge["family"],
                thinking=judge["thinking"],
            )
            evaluator = AnswerEvaluator(api=adapter, flag_parser_version="legacy")
            evaluation = evaluator.evaluate_answer(
                ai_answer=answer["answer"],
                judge_decision=answer["masked_judgment"],
                question=answer["question"],
                case_text=answer["masked_case_text"],
            )
            last_call = adapter.last_call
            if not last_call or not last_call.get("ok"):
                raise RuntimeError((last_call or {}).get("error") or "missing scoring receipt")
            layers = parse_layers(evaluator, evaluation)
            if not all(dimension in layers["raw_scores"] for dimension in DIMENSIONS):
                raise RuntimeError("评分文本没有完整解析出五个维度")
            attempt_history.append(
                {
                    "task_attempt": task_attempt,
                    "status": "success",
                    "scoring": pipeline_call_snapshot(last_call),
                    "error": "",
                }
            )
            return {
                **task,
                "status": "success",
                "completed_at": now_iso(),
                "evaluation": evaluation,
                **layers,
                "scoring": last_call,
                "attempt_history": attempt_history,
            }
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
            attempt_history.append(
                {
                    "task_attempt": task_attempt,
                    "status": "failed",
                    "scoring": pipeline_call_snapshot(last_call),
                    "error": last_error,
                }
            )
            if task_attempt < 3:
                time.sleep(task_attempt)

    return {
        **task,
        "status": "failed",
        "completed_at": now_iso(),
        "error": last_error,
        "scoring": last_call,
        "attempt_history": attempt_history,
    }


def state_snapshot(tasks: List[Dict[str, Any]], ratings_dir: Path) -> Dict[str, Any]:
    counts = Counter()
    by_judge: Dict[str, Counter] = defaultdict(Counter)
    for task in tasks:
        path = ratings_dir / f"{task['task_id']}.json"
        if result_is_valid(path, task):
            status = "completed"
        elif path.exists():
            status = "failed"
        else:
            status = "pending"
        counts[status] += 1
        by_judge[task["judge_label"]][status] += 1
    return {
        "updated_at": now_iso(),
        "total": len(tasks),
        "completed": counts["completed"],
        "pending": counts["pending"],
        "failed": counts["failed"],
        "by_judge": {label: dict(count) for label, count in sorted(by_judge.items())},
    }


def combine_results(
    *,
    source_path: Path,
    source_sha: str,
    answers: List[Dict[str, Any]],
    tasks: List[Dict[str, Any]],
    ratings_dir: Path,
    output_path: Path,
    endpoint_host: str,
) -> Dict[str, Any]:
    task_lookup = {task["task_id"]: task for task in tasks}
    ratings = []
    for task in tasks:
        path = ratings_dir / f"{task['task_id']}.json"
        if not result_is_valid(path, task):
            continue
        ratings.append(load_json(path))

    ratings_by_answer: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for rating in ratings:
        ratings_by_answer[rating["answer_id"]].append(rating)

    answer_records: List[Dict[str, Any]] = []
    for answer in answers:
        eligible = sorted(
            ratings_by_answer.get(answer["answer_id"], []),
            key=lambda item: item["judge_label"],
        )
        consensus: Dict[str, Any] = {}
        if len(eligible) == 3:
            dimension_scores = {
                dimension: statistics.median(
                    float(item["evaluation"]["各维度得分"][dimension])
                    for item in eligible
                )
                for dimension in DIMENSIONS
            }
            totals = [float(item["evaluation"]["总分"]) for item in eligible]
            consensus = {
                "method": "three_cross_family_judges_median_after_legacy_penalty",
                "total_score": round(statistics.median(totals), 4),
                "percentage_score": round(statistics.median(totals) * 5, 4),
                "dimension_scores": {key: round(value, 4) for key, value in dimension_scores.items()},
                "judge_total_scores": {
                    item["judge_label"]: float(item["evaluation"]["总分"])
                    for item in eligible
                },
                "judge_score_range": round(max(totals) - min(totals), 4),
                "error_vote_counts": {
                    level: sum(bool((item.get("errors") or {}).get(level)) for item in eligible)
                    for level in ("微小错误", "明显错误", "重大错误")
                },
            }
        source_evaluation = answer["source_row"].get("evaluation") or {}
        answer_records.append(
            {
                **public_answer(answer),
                "original_deepseek_evaluation": source_evaluation,
                "original_deepseek_total": source_evaluation.get("总分"),
                "cross_family_rating_task_ids": [item["task_id"] for item in eligible],
                "cross_family_rating_count": len(eligible),
                "consensus": consensus,
            }
        )

    usage_by_judge: Dict[str, Dict[str, Any]] = {}
    total_cost = 0.0
    for label in JUDGES:
        selected = [item for item in ratings if item["judge_label"] == label and item["source"] == "new_api"]
        usage = {
            "prompt_tokens": sum(int((item.get("scoring") or {}).get("usage", {}).get("prompt_tokens") or 0) for item in selected),
            "completion_tokens": sum(int((item.get("scoring") or {}).get("usage", {}).get("completion_tokens") or 0) for item in selected),
            "reasoning_tokens": sum(int((item.get("scoring") or {}).get("usage", {}).get("reasoning_tokens") or 0) for item in selected),
            "total_tokens": sum(int((item.get("scoring") or {}).get("usage", {}).get("total_tokens") or 0) for item in selected),
        }
        cost = sum(
            usage_cost(item["judge_model_id"], (item.get("scoring") or {}).get("usage") or {})
            for item in selected
        )
        total_cost += cost
        usage_by_judge[label] = {
            "new_api_ratings": len(selected),
            "usage": usage,
            "estimated_xhub_list_cost_usd": round(cost, 6),
        }

    combined = {
        "metadata": {
            "updated_at": now_iso(),
            "endpoint_host": endpoint_host,
            "source_path": str(source_path.resolve()),
            "source_sha256": source_sha,
            "sample": "additional20_frozen",
            "case_count": 20,
            "question_count": 100,
            "answer_count": 500,
            "rating_count_expected": 1500,
            "new_api_rating_count_expected": 1200,
            "reused_deepseek_rating_count_expected": 300,
            "same_family_ratings_excluded": True,
            "temperature": 0.3,
            "max_tokens": 3000,
            "flag_parser_version": "legacy",
            "consensus_method": "three_cross_family_judges_median_after_legacy_penalty",
            "credential_serialized": False,
        },
        "completion": {
            "ratings_completed": len(ratings),
            "answers_with_three_ratings": sum(item["cross_family_rating_count"] == 3 for item in answer_records),
            "complete": len(ratings) == 1500 and all(item["cross_family_rating_count"] == 3 for item in answer_records),
        },
        "judge_matrix": {
            condition: [label for label, spec in JUDGES.items() if spec["family"] != family]
            for condition, family in ANSWER_FAMILY.items()
        },
        "usage_by_judge": usage_by_judge,
        "estimated_xhub_list_cost_usd": round(total_cost, 6),
        "estimated_xhub_list_cost_cny_at_7_3": round(total_cost * 7.3, 4),
        "answers": answer_records,
        "rating_files": {
            item["task_id"]: str((ratings_dir / f"{item['task_id']}.json").resolve())
            for item in ratings
        },
        "task_count": len(task_lookup),
    }
    atomic_write_json(output_path, combined)
    return combined


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--xhub-env", type=Path, default=DEFAULT_XHUB_ENV)
    parser.add_argument("--workers", type=int, default=16)
    parser.add_argument("--limit", type=int, default=0, help="Run at most N pending new API tasks; 0 means all.")
    args = parser.parse_args()

    cfg = dotenv_values(args.xhub_env)
    api_key = str(cfg.get("UNIFIED_API_KEY") or "")
    base_url = str(cfg.get("UNIFIED_API_BASE_URL") or "").rstrip("/")
    if not api_key or not base_url:
        raise SystemExit("xhub env must define UNIFIED_API_KEY and UNIFIED_API_BASE_URL")

    source_path = args.source.resolve()
    output_dir = args.output_dir.resolve()
    ratings_dir = output_dir / "ratings"
    ratings_dir.mkdir(parents=True, exist_ok=True)
    source_sha = file_sha256(source_path)
    source_combined, answers = load_source_answers(source_path)
    tasks = build_tasks(answers)
    answer_lookup = {item["answer_id"]: item for item in answers}

    client = XHubClient(api_key, base_url, timeout=180.0)
    catalog = client.list_models()
    available = set(catalog.get("models") or [])
    missing = [spec["model_id"] for spec in JUDGES.values() if spec["model_id"] not in available]
    if not catalog.get("ok") or missing:
        raise SystemExit(f"xhub catalog check failed or required models absent: {missing}")

    manifest = {
        "created_at": now_iso(),
        "source_path": str(source_path),
        "source_sha256": source_sha,
        "source_prompt_template_hashes": sorted(
            {
                str((load_json(Path(path)).get("metadata") or {}).get("prompt_template_hash") or "")
                for path in (source_combined.get("per_case_raw_results") or {}).values()
            }
        ),
        "evaluation_prompt_source_sha256": sha256_text(inspect.getsource(AnswerEvaluator._call_evaluation_api)),
        "legacy_flag_parser_source_sha256": sha256_text(inspect.getsource(AnswerEvaluator._detect_flags_legacy)),
        "legacy_penalty_source_sha256": sha256_text(inspect.getsource(AnswerEvaluator._apply_penalty_for_flags)),
        "answer_count": len(answers),
        "task_count": len(tasks),
        "task_source_counts": dict(Counter(item["source"] for item in tasks)),
        "judge_matrix": {
            condition: [label for label, spec in JUDGES.items() if spec["family"] != family]
            for condition, family in ANSWER_FAMILY.items()
        },
        "parameters": {"temperature": 0.3, "max_tokens": 3000, "flag_parser_version": "legacy"},
        "catalog_check": {
            "ok": catalog.get("ok"),
            "http_status": catalog.get("http_status"),
            "required_models_present": not missing,
        },
        "tasks": tasks,
    }
    manifest_path = output_dir / "manifest.json"
    if manifest_path.exists():
        old_manifest = load_json(manifest_path)
        if old_manifest.get("source_sha256") != source_sha or old_manifest.get("tasks") != tasks:
            raise SystemExit("existing manifest differs from current frozen source/tasks")
    else:
        atomic_write_json(manifest_path, manifest)

    # Materialize the 300 eligible historical DeepSeek ratings first.
    reused_written = 0
    for task in tasks:
        if task["source"] != "reused_existing":
            continue
        path = ratings_dir / f"{task['task_id']}.json"
        if result_is_valid(path, task):
            continue
        result = make_reused_result(task, answer_lookup[task["answer_id"]])
        atomic_write_json(path, result)
        reused_written += 1

    pending = [
        task
        for task in tasks
        if task["source"] == "new_api"
        and not result_is_valid(ratings_dir / f"{task['task_id']}.json", task)
    ]
    if args.limit > 0:
        pending = pending[: args.limit]

    state_path = output_dir / "state.json"
    initial_state = state_snapshot(tasks, ratings_dir)
    atomic_write_json(state_path, initial_state)
    print(
        f"[start] reused_written={reused_written} completed={initial_state['completed']}/1500 "
        f"pending_new_this_run={len(pending)} workers={args.workers}",
        flush=True,
    )

    completed_this_run = 0
    failed_this_run = 0
    started = time.perf_counter()

    def worker(task: Dict[str, Any]) -> Dict[str, Any]:
        result = run_new_task(task, answer_lookup[task["answer_id"]], client)
        atomic_write_json(ratings_dir / f"{task['task_id']}.json", result)
        return result

    if pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {executor.submit(worker, task): task for task in pending}
            for future in concurrent.futures.as_completed(futures):
                task = futures[future]
                try:
                    result = future.result()
                except Exception as exc:  # defensive; worker normally serializes failures
                    result = {
                        **task,
                        "status": "failed",
                        "completed_at": now_iso(),
                        "error": f"worker crash: {type(exc).__name__}: {exc}",
                    }
                    atomic_write_json(ratings_dir / f"{task['task_id']}.json", result)
                completed_this_run += result.get("status") == "success"
                failed_this_run += result.get("status") != "success"
                done = completed_this_run + failed_this_run
                if done % 10 == 0 or done == len(pending):
                    snapshot = state_snapshot(tasks, ratings_dir)
                    atomic_write_json(state_path, snapshot)
                    elapsed = max(time.perf_counter() - started, 0.001)
                    rate = done / elapsed * 60
                    print(
                        f"[progress] run={done}/{len(pending)} all={snapshot['completed']}/1500 "
                        f"failed={snapshot['failed']} rate={rate:.1f}/min",
                        flush=True,
                    )

    final_state = state_snapshot(tasks, ratings_dir)
    atomic_write_json(state_path, final_state)
    combined = combine_results(
        source_path=source_path,
        source_sha=source_sha,
        answers=answers,
        tasks=tasks,
        ratings_dir=ratings_dir,
        output_path=output_dir / "combined_results.json",
        endpoint_host=urlparse(base_url).netloc,
    )
    print(
        f"[done] ratings={final_state['completed']}/1500 "
        f"answers={combined['completion']['answers_with_three_ratings']}/500 "
        f"failed={final_state['failed']} estimated_cost_usd=${combined['estimated_xhub_list_cost_usd']:.4f}",
        flush=True,
    )
    return 0 if combined["completion"]["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
