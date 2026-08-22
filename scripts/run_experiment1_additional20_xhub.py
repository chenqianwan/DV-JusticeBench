#!/usr/bin/env python3
"""Run a frozen Experiment-1 manifest with resumable per-case execution."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import math
import os
import statistics
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "data/additional_20_cases_20260819.json"
DEFAULT_OUTPUT_DIR = ROOT / "data/experiment1_additional20_xhub_20260819"
DEFAULT_REPORT = ROOT / "实验1_新增20case_五条件运行报告_20260819.md"
CASE_RUNNER = ROOT / "scripts/run_experiment1_smoke_xhub.py"
CONDITION_ORDER = [
    "GPT-4o",
    "Gemini 2.5 Flash",
    "Qwen-Max",
    "DeepSeek thinking",
    "DeepSeek non-thinking",
]
XHUB_PRICES_USD_PER_MILLION = {
    "gpt-4o": (2.5, 10.0),
    "gemini-2.5-flash": (0.3, 2.499),
    "qwen-max": (0.8348, 3.3392),
    "deepseek-v3.2": (0.458, 0.687),
}


def now_hk() -> str:
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def atomic_write_text(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(value, encoding="utf-8")
    os.replace(temporary, path)


def load_json(path: Path) -> Dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_case_result(path: Path, expected_case_id: str) -> Tuple[bool, str, Optional[Dict[str, Any]]]:
    if not path.exists():
        return False, "raw_results.json does not exist", None
    try:
        result = load_json(path)
    except Exception as exc:
        return False, f"cannot parse raw result: {exc}", None
    if str((result.get("input") or {}).get("case_id")) != expected_case_id:
        return False, "case_id mismatch", result
    if (result.get("input") or {}).get("masking_scope") != "per-condition":
        return False, "masking_scope is not per-condition", result
    if int((result.get("input") or {}).get("masking_run_count") or 0) != 5:
        return False, "masking_run_count is not 5", result
    masking_calls = (result.get("metadata") or {}).get("masking_calls") or []
    if len(masking_calls) != 15:
        return False, f"expected 15 masking calls, found {len(masking_calls)}", result
    for call_index, call in enumerate(masking_calls, start=1):
        if not call.get("ok"):
            return False, f"masking call {call_index}/15 failed: {call.get('error') or 'unknown error'}", result
    rows = result.get("rows") or []
    if len(rows) != 25:
        return False, f"expected 25 rows, found {len(rows)}", result
    pairs = {(row.get("condition"), row.get("question_number")) for row in rows}
    expected_pairs = {(condition, question) for condition in CONDITION_ORDER for question in range(1, 6)}
    if pairs != expected_pairs:
        return False, "condition/question matrix is incomplete", result
    for row in rows:
        if not (row.get("generation") or {}).get("ok"):
            return False, f"generation failed: {row.get('condition')} Q{row.get('question_number')}", result
        if not (row.get("scoring") or {}).get("ok"):
            return False, f"scoring failed: {row.get('condition')} Q{row.get('question_number')}", result
        if row.get("processing_error"):
            return False, f"processing_error: {row.get('condition')} Q{row.get('question_number')}", result
        score = (row.get("evaluation") or {}).get("总分")
        if not isinstance(score, (int, float)) or not 0 <= float(score) <= 20:
            return False, f"invalid score: {row.get('condition')} Q{row.get('question_number')}", result
    if (result.get("metadata") or {}).get("flag_parser_version") != "legacy":
        return False, "flag parser is not legacy", result
    return True, "PASS", result


def usage_cost(model: str, usage: Dict[str, Any]) -> float:
    prices = XHUB_PRICES_USD_PER_MILLION.get(model)
    if not prices:
        return 0.0
    input_price, output_price = prices
    return (
        float(usage.get("prompt_tokens") or 0) * input_price
        + float(usage.get("completion_tokens") or 0) * output_price
    ) / 1_000_000


def add_usage(target: Dict[str, int], usage: Dict[str, Any]) -> None:
    for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens"):
        target[key] += int(usage.get(key) or 0)


def call_records(row: Dict[str, Any], stage: str) -> Iterable[Dict[str, Any]]:
    history = row.get("pipeline_attempt_history") or []
    if history:
        for attempt in history:
            receipt = attempt.get(stage)
            if receipt:
                yield receipt
        return
    receipt = row.get(stage)
    if receipt:
        yield receipt


def summarize_completed(
    manifest: Dict[str, Any],
    output_dir: Path,
    statuses: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    cases_by_id = {str(item["case_id"]): item for item in manifest["cases"]}
    completed_results: Dict[str, Dict[str, Any]] = {}
    for case_id in cases_by_id:
        raw_path = output_dir / "cases" / case_id / "raw_results.json"
        valid, _, result = validate_case_result(raw_path, case_id)
        if valid and result:
            completed_results[case_id] = result

    condition_scores: Dict[str, List[float]] = {condition: [] for condition in CONDITION_ORDER}
    condition_case_means: Dict[str, List[float]] = {condition: [] for condition in CONDITION_ORDER}
    case_condition_means: Dict[str, Dict[str, float]] = {}
    stage_usage = {
        "masking": {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0},
        "generation": {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0},
        "scoring": {"prompt_tokens": 0, "completion_tokens": 0, "reasoning_tokens": 0, "total_tokens": 0},
    }
    estimated_cost = {"masking": 0.0, "generation": 0.0, "scoring": 0.0}
    retries = {
        "masking_http": 0,
        "generation_http": 0,
        "scoring_http": 0,
        "whole_pipeline_extra_attempts": 0,
        "failed_whole_case_attempts_captured": 0,
    }

    for case_id, result in completed_results.items():
        by_condition: Dict[str, List[float]] = {condition: [] for condition in CONDITION_ORDER}
        for call in (result.get("metadata") or {}).get("masking_calls") or []:
            usage = call.get("usage") or {}
            add_usage(stage_usage["masking"], usage)
            estimated_cost["masking"] += usage_cost(str(call.get("requested_model") or "deepseek-v3.2"), usage)
            retries["masking_http"] += int(call.get("retry_count") or 0)
        for row in result.get("rows") or []:
            condition = str(row["condition"])
            score = float(row["evaluation"]["总分"])
            condition_scores[condition].append(score)
            by_condition[condition].append(score)
            retries["whole_pipeline_extra_attempts"] += max(0, int(row.get("pipeline_attempt") or 1) - 1)
            for stage in ("generation", "scoring"):
                for call in call_records(row, stage):
                    usage = call.get("usage") or {}
                    add_usage(stage_usage[stage], usage)
                    model = str(call.get("requested_model") or (row.get(stage) or {}).get("requested_model") or "")
                    estimated_cost[stage] += usage_cost(model, usage)
                    retries[f"{stage}_http"] += int(call.get("retry_count") or 0)
        case_condition_means[case_id] = {}
        for condition in CONDITION_ORDER:
            if by_condition[condition]:
                value = statistics.mean(by_condition[condition])
                case_condition_means[case_id][condition] = round(value, 4)
                condition_case_means[condition].append(value)

    failed_attempt_ledger_path = output_dir / "failed_attempt_usage_ledger.json"
    failed_attempt_entries: List[Dict[str, Any]] = []
    if failed_attempt_ledger_path.exists():
        failed_attempt_entries = load_json(failed_attempt_ledger_path).get("entries") or []
        for entry in failed_attempt_entries:
            for stage in ("masking", "generation", "scoring"):
                captured = (entry.get("stages") or {}).get(stage) or {}
                add_usage(stage_usage[stage], captured)
                estimated_cost[stage] += float(captured.get("cost_usd") or 0)
        retries["failed_whole_case_attempts_captured"] = len(failed_attempt_entries)

    condition_summary = []
    for condition in CONDITION_ORDER:
        scores = condition_scores[condition]
        case_means = condition_case_means[condition]
        condition_summary.append(
            {
                "condition": condition,
                "case_count": len(case_means),
                "question_count": len(scores),
                "mean_score": round(statistics.mean(scores), 4) if scores else None,
                "case_mean_sd": round(statistics.stdev(case_means), 4) if len(case_means) > 1 else None,
                "min_score": round(min(scores), 4) if scores else None,
                "max_score": round(max(scores), 4) if scores else None,
            }
        )

    all_scores = [score for scores in condition_scores.values() for score in scores]
    completed_ids = [str(item["case_id"]) for item in manifest["cases"] if str(item["case_id"]) in completed_results]
    failed_ids = [
        str(item["case_id"])
        for item in manifest["cases"]
        if statuses.get(str(item["case_id"]), {}).get("status") == "failed"
    ]
    running_ids = [
        str(item["case_id"])
        for item in manifest["cases"]
        if statuses.get(str(item["case_id"]), {}).get("status") == "running"
    ]
    pending_ids = [
        str(item["case_id"])
        for item in manifest["cases"]
        if str(item["case_id"]) not in completed_results
        and str(item["case_id"]) not in failed_ids
        and str(item["case_id"]) not in running_ids
    ]
    cost_total = sum(estimated_cost.values())
    token_total = sum(stage_usage[stage]["total_tokens"] for stage in stage_usage)
    return {
        "updated_at": now_hk(),
        "manifest_sha256": sha256_file(Path(manifest["manifest_path"])),
        "expected_case_count": len(manifest["cases"]),
        "completed_case_count": len(completed_ids),
        "completed_case_ids": completed_ids,
        "failed_case_ids": failed_ids,
        "running_case_ids": running_ids,
        "pending_case_ids": pending_ids,
        "expected_answer_count": len(manifest["cases"]) * 25,
        "completed_answer_count": len(all_scores),
        "overall_mean_score": round(statistics.mean(all_scores), 4) if all_scores else None,
        "condition_summary": condition_summary,
        "case_condition_means": case_condition_means,
        "stage_usage": stage_usage,
        "total_tokens_including_failed_pipeline_attempts": token_total,
        "estimated_xhub_list_cost_usd": round(cost_total, 6),
        "estimated_xhub_list_cost_cny_at_7_3": round(cost_total * 7.3, 4),
        "estimated_cost_by_stage_usd": {key: round(value, 6) for key, value in estimated_cost.items()},
        "retries": retries,
        "failed_attempt_usage_ledger": str(failed_attempt_ledger_path.resolve()) if failed_attempt_entries else None,
        "status": "PASS" if len(completed_ids) == len(manifest["cases"]) else "RUNNING" if running_ids else "PARTIAL",
        "per_case_raw_results": {
            case_id: str((output_dir / "cases" / case_id / "raw_results.json").resolve())
            for case_id in completed_ids
        },
    }


def fmt(value: Optional[float], digits: int = 2) -> str:
    return "—" if value is None else f"{value:.{digits}f}"


def build_report(manifest: Dict[str, Any], aggregate: Dict[str, Any], output_dir: Path) -> str:
    case_meta = {str(item["case_id"]): item for item in manifest["cases"]}
    lines = [
        f"# 实验1：{aggregate['expected_case_count']}-case五条件运行报告",
        "",
        f"> 更新时间：{aggregate['updated_at']}  ",
        "> endpoint：`api3.xhub.chat`  ",
        "> 脱敏语义：每个 case × 每个模型条件独立脱敏；条件内5题共享  ",
        "> 评分：`deepseek-v3.2` thinking + legacy解析/扣分  ",
        f"> 状态：**{aggregate['status']}**",
        "",
        "## 1. 当前进度",
        "",
        "| 项目 | 预期 | 已完成 |",
        "|---|---:|---:|",
        f"| cases | {aggregate['expected_case_count']} | {aggregate['completed_case_count']} |",
        f"| 回答与评分单元 | {aggregate['expected_answer_count']} | {aggregate['completed_answer_count']} |",
        f"| 最终失败 cases | 0 | {len(aggregate['failed_case_ids'])} |",
        "",
    ]
    if aggregate["running_case_ids"]:
        lines.append("正在运行：" + "、".join(f"`{case_id}`" for case_id in aggregate["running_case_ids"]) + "。")
        lines.append("")
    if aggregate["failed_case_ids"]:
        lines.append("当前失败：" + "、".join(f"`{case_id}`" for case_id in aggregate["failed_case_ids"]) + "。")
        lines.append("")

    lines.extend(
        [
            "## 2. 条件级结果",
            "",
            "| 条件 | 完成 cases | 完成题数 | 均分/20 | case均分SD | 最低–最高 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for item in aggregate["condition_summary"]:
        score_range = "—" if item["min_score"] is None else f"{item['min_score']:.2f}–{item['max_score']:.2f}"
        lines.append(
            f"| {item['condition']} | {item['case_count']} | {item['question_count']} | {fmt(item['mean_score'])} | {fmt(item['case_mean_sd'])} | {score_range} |"
        )
    lines.extend(
        [
            "",
            "## 3. case × 条件均分",
            "",
            "| case_id | 案由 | GPT-4o | Gemini | Qwen-Max | DS thinking | DS non-thinking | 五条件均分 |",
            "|---|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for item in manifest["cases"]:
        case_id = str(item["case_id"])
        values = aggregate["case_condition_means"].get(case_id)
        if not values:
            continue
        ordered = [values.get(condition) for condition in CONDITION_ORDER]
        combined = statistics.mean(value for value in ordered if value is not None)
        lines.append(
            f"| `{case_id}` | {item['cause']} | "
            + " | ".join(fmt(value) for value in ordered)
            + f" | {combined:.2f} |"
        )

    usage = aggregate["stage_usage"]
    costs = aggregate["estimated_cost_by_stage_usd"]
    lines.extend(
        [
            "",
            "## 4. Token、费用与重试",
            "",
            "| 阶段 | prompt tokens | completion tokens | total tokens | xhub当前list价估算(USD) |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for stage, label in (("masking", "脱敏"), ("generation", "回答生成"), ("scoring", "自动评分")):
        lines.append(
            f"| {label} | {usage[stage]['prompt_tokens']:,} | {usage[stage]['completion_tokens']:,} | {usage[stage]['total_tokens']:,} | ${costs[stage]:.4f} |"
        )
    lines.extend(
        [
            f"| **合计** | **{sum(usage[s]['prompt_tokens'] for s in usage):,}** | **{sum(usage[s]['completion_tokens'] for s in usage):,}** | **{aggregate['total_tokens_including_failed_pipeline_attempts']:,}** | **${aggregate['estimated_xhub_list_cost_usd']:.4f}** |",
            "",
            f"人民币参考：约 ¥{aggregate['estimated_xhub_list_cost_cny_at_7_3']:.2f}（按 xhub status 的 7.3 换算；最终以账户账单为准）。",
            "",
            "| 重试类型 | 次数 |",
            "|---|---:|",
            f"| 脱敏HTTP内部重试 | {aggregate['retries']['masking_http']} |",
            f"| 回答HTTP内部重试 | {aggregate['retries']['generation_http']} |",
            f"| 评分HTTP内部重试 | {aggregate['retries']['scoring_http']} |",
            f"| 整题额外attempt | {aggregate['retries']['whole_pipeline_extra_attempts']} |",
            f"| 已捕获的失败整案attempt | {aggregate['retries']['failed_whole_case_attempts_captured']} |",
            "",
            "费用合计包含最终保留结果、整题内部失败重试，以及已写入失败整案台账的调用；在 raw 写出前即中止的脱敏整案无法取得完整 token 回执，未计入上述 token/list价。",
            "",
            "## 5. 复现文件",
            "",
            f"- batch state：`{(output_dir / 'batch_state.json').resolve()}`",
            f"- 聚合 JSON：`{(output_dir / 'combined_results.json').resolve()}`",
            f"- 各 case 原始结果与日志：`{(output_dir / 'cases').resolve()}`",
            f"- 失败整案用量台账：`{(output_dir / 'failed_attempt_usage_ledger.json').resolve()}`",
            f"- 抽样 manifest：`{Path(manifest['manifest_path']).resolve()}`",
            "- 文件不包含 xhub API key。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--max-case-workers", type=int, default=4)
    parser.add_argument("--per-case-workers", type=int, default=10)
    parser.add_argument("--max-case-attempts", type=int, default=2)
    parser.add_argument("--case-timeout-seconds", type=int, default=5400)
    parser.add_argument("--force", action="store_true")
    parser.add_argument(
        "--status-only",
        action="store_true",
        help="Reconcile completed raw results, mark all other cases pending, write reports, and exit without API calls.",
    )
    args = parser.parse_args()

    manifest = load_json(args.manifest)
    manifest["manifest_path"] = str(args.manifest.resolve())
    cases = manifest.get("cases") or []
    case_ids = [str(item["case_id"]) for item in cases]
    if not case_ids or len(set(case_ids)) != len(case_ids):
        raise SystemExit("manifest must contain at least one case_id and all case_ids must be unique")
    if not CASE_RUNNER.exists():
        raise SystemExit(f"case runner not found: {CASE_RUNNER}")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    state_path = args.output_dir / "batch_state.json"
    combined_path = args.output_dir / "combined_results.json"
    statuses: Dict[str, Dict[str, Any]] = {}
    if state_path.exists():
        try:
            statuses = load_json(state_path).get("cases") or {}
        except Exception:
            statuses = {}
    state_lock = threading.Lock()

    def persist() -> None:
        aggregate = summarize_completed(manifest, args.output_dir, statuses)
        atomic_write_json(
            state_path,
            {
                "updated_at": now_hk(),
                "manifest_path": str(args.manifest.resolve()),
                "manifest_sha256": sha256_file(args.manifest),
                "case_runner_path": str(CASE_RUNNER.resolve()),
                "case_runner_sha256": sha256_file(CASE_RUNNER),
                "max_case_workers": args.max_case_workers,
                "per_case_workers": args.per_case_workers,
                "cases": statuses,
            },
        )
        aggregate["manifest_sha256"] = sha256_file(args.manifest)
        aggregate["case_runner_sha256"] = sha256_file(CASE_RUNNER)
        atomic_write_json(combined_path, aggregate)
        atomic_write_text(args.report, build_report(manifest, aggregate, args.output_dir))

    def run_case(case_id: str) -> Dict[str, Any]:
        case_dir = args.output_dir / "cases" / case_id
        raw_path = case_dir / "raw_results.json"
        report_path = case_dir / "report.md"
        log_path = case_dir / "run.log"
        case_dir.mkdir(parents=True, exist_ok=True)
        if not args.force:
            valid, reason, _ = validate_case_result(raw_path, case_id)
            if valid:
                print(f"[case skip] {case_id} existing=PASS", flush=True)
                return {
                    "status": "completed",
                    "completed_at": now_hk(),
                    "case_attempts": int(statuses.get(case_id, {}).get("case_attempts") or 0),
                    "exit_code": 0,
                    "validation": reason,
                    "raw_result": str(raw_path.resolve()),
                    "report": str(report_path.resolve()),
                    "log": str(log_path.resolve()),
                    "resumed": True,
                }

        last_error = ""
        attempts_already = int(statuses.get(case_id, {}).get("case_attempts") or 0)
        for local_attempt in range(1, args.max_case_attempts + 1):
            case_attempt = attempts_already + local_attempt
            started_at = now_hk()
            print(f"[case start] {case_id} case_attempt={case_attempt}", flush=True)
            with state_lock:
                statuses[case_id] = {
                    "status": "running",
                    "started_at": started_at,
                    "case_attempts": case_attempt,
                    "raw_result": str(raw_path.resolve()),
                    "report": str(report_path.resolve()),
                    "log": str(log_path.resolve()),
                }
                persist()
            command = [
                sys.executable,
                str(CASE_RUNNER),
                "--case-id",
                case_id,
                "--masking-scope",
                "per-condition",
                "--workers",
                str(args.per_case_workers),
                "--output-dir",
                str(case_dir),
                "--report",
                str(report_path),
            ]
            with log_path.open("a", encoding="utf-8") as log:
                log.write(f"\n[BATCH] started_at={started_at} case_attempt={case_attempt}\n")
                log.write("[BATCH] command=" + " ".join(command) + "\n")
                log.flush()
                try:
                    completed = subprocess.run(
                        command,
                        cwd=ROOT,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                        timeout=args.case_timeout_seconds,
                        check=False,
                    )
                    exit_code = int(completed.returncode)
                    last_error = "" if exit_code == 0 else f"case runner exit code {exit_code}"
                except subprocess.TimeoutExpired:
                    exit_code = 124
                    last_error = f"case timeout after {args.case_timeout_seconds}s"
                    log.write("[BATCH] " + last_error + "\n")
            valid, validation_reason, _ = validate_case_result(raw_path, case_id)
            if exit_code == 0 and valid:
                print(f"[case done] {case_id} case_attempt={case_attempt} validation=PASS", flush=True)
                return {
                    "status": "completed",
                    "started_at": started_at,
                    "completed_at": now_hk(),
                    "case_attempts": case_attempt,
                    "exit_code": exit_code,
                    "validation": validation_reason,
                    "raw_result": str(raw_path.resolve()),
                    "report": str(report_path.resolve()),
                    "log": str(log_path.resolve()),
                    "resumed": False,
                }
            last_error = f"{last_error}; validation={validation_reason}".strip("; ")
            print(f"[case retry] {case_id} case_attempt={case_attempt} error={last_error}", flush=True)
            if local_attempt < args.max_case_attempts:
                time.sleep(5)

        return {
            "status": "failed",
            "completed_at": now_hk(),
            "case_attempts": attempts_already + args.max_case_attempts,
            "exit_code": exit_code,
            "validation": last_error,
            "raw_result": str(raw_path.resolve()),
            "report": str(report_path.resolve()),
            "log": str(log_path.resolve()),
            "resumed": False,
        }

    if args.status_only:
        reconciled: Dict[str, Dict[str, Any]] = {}
        for case_id in case_ids:
            previous = statuses.get(case_id, {})
            raw_path = args.output_dir / "cases" / case_id / "raw_results.json"
            valid, reason, _ = validate_case_result(raw_path, case_id)
            if valid:
                reconciled[case_id] = {
                    **previous,
                    "status": "completed",
                    "validation": "PASS",
                    "raw_result": str(raw_path.resolve()),
                }
            else:
                reconciled[case_id] = {
                    "status": "pending",
                    "case_attempts": int(previous.get("case_attempts") or 0),
                    "validation": reason,
                    "pause_reason": "batch paused; no case process is running",
                }
        statuses.clear()
        statuses.update(reconciled)
        persist()
        print(
            f"[status only] completed={sum(item['status'] == 'completed' for item in statuses.values())}/{len(case_ids)}; "
            "all non-completed cases marked pending",
            flush=True,
        )
        return 0

    with state_lock:
        for case_id in case_ids:
            statuses.setdefault(case_id, {"status": "pending", "case_attempts": 0})
        persist()

    finished = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.max_case_workers)) as executor:
        future_to_case = {executor.submit(run_case, case_id): case_id for case_id in case_ids}
        for future in concurrent.futures.as_completed(future_to_case):
            case_id = future_to_case[future]
            try:
                status = future.result()
            except Exception as exc:
                status = {
                    "status": "failed",
                    "completed_at": now_hk(),
                    "case_attempts": int(statuses.get(case_id, {}).get("case_attempts") or 0),
                    "exit_code": 1,
                    "validation": f"batch worker exception: {type(exc).__name__}: {exc}",
                }
            with state_lock:
                statuses[case_id] = status
                persist()
            finished += 1
            print(
                f"[batch progress] finished={finished}/{len(case_ids)} "
                f"case={case_id} status={status['status']}",
                flush=True,
            )

    with state_lock:
        persist()
    final_aggregate = load_json(combined_path)
    print(
        f"[batch done] status={final_aggregate['status']} "
        f"cases={final_aggregate['completed_case_count']}/{final_aggregate['expected_case_count']} "
        f"answers={final_aggregate['completed_answer_count']}/{final_aggregate['expected_answer_count']}",
        flush=True,
    )
    print(f"[batch done] report={args.report}", flush=True)
    return 0 if final_aggregate["completed_case_count"] == len(case_ids) else 1


if __name__ == "__main__":
    raise SystemExit(main())
