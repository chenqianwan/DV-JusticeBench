#!/usr/bin/env python3
"""Read-only-source, API-free report for old-input 88 + rebuilt-input 20 cases.

The new scores can be incomplete; their full eligible manifest/source roster is
required for the row-level export. Refusals are never zero-imputed and historical
generation failures are not counted as failed rating requests.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import statistics
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
OLD_DIR = ROOT / "outputs/20260908_cross_judge_current"
NEW_DIR = ROOT / "data/experiment1_cross_judge_original20_rebuilt_xhub_20260908"
OUTPUT_DIR = ROOT / "outputs/20260908_cross_judge_108_rebuilt"
CONDITIONS = ["GPT-4o", "Gemini 2.5 Flash", "Qwen-Max", "DeepSeek thinking", "DeepSeek non-thinking"]
JUDGES = ["GPT-4o", "Gemini 2.5 Flash", "Qwen-Max", "DeepSeek v3.2"]
MATRIX = {
    "GPT-4o": JUDGES[1:],
    "Gemini 2.5 Flash": [JUDGES[0], JUDGES[2], JUDGES[3]],
    "Qwen-Max": [JUDGES[0], JUDGES[1], JUDGES[3]],
    "DeepSeek thinking": JUDGES[:3],
    "DeepSeek non-thinking": JUDGES[:3],
}
STATUS_MAP = {
    "valid": "valid", "refused": "refused", "still_refused": "refused",
    "interface_refusal": "refused", "transport_failed": "transport_failed",
    "failed": "failed", "unusable": "failed", "pending": "pending", "missing": "pending",
}
DETAIL_STATUSES = ("valid", "refused", "transport_failed", "truncated", "failed", "missing_input", "pending")
DETAIL_LABELS = dict(zip(DETAIL_STATUSES, ("有效评分", "拒评", "连接失败 / 超时", "评分输出截断", "其他评分失败", "缺少输入", "待评分")))
MASK_CAUSE_LABELS = {"truncated": "脱敏输出截断", "transport_failed": "脱敏连接失败 / 超时",
                     "refused": "脱敏拒绝", "failed": "其他脱敏失败", "pending": "脱敏未完成"}
HASH_KEYS = ("input_hash", "question_sha256", "answer_sha256", "masked_case_sha256", "masked_judgment_sha256", "messages_sha256")
TRANSPORT_TOKENS = ("ProxyError", "ReadTimeout", "ConnectTimeout", "ConnectionError", "Timeout")
PHASE_LABELS = {
    "prepared": "已建立计划", "masking": "重建脱敏输入中", "inputs_prepared": "脱敏输入已整理",
    "mask_phase_finished": "脱敏阶段已结束", "scoring": "互评中", "finishing": "汇总中",
    "finished": "可评任务已处理完", "finished_with_missing": "本轮结束，仍有缺失",
    "budget_paused": "达到预算保护条件，已暂停", "interrupted": "已中断",
    "input_quality_hold": "重建输入存在人物编号冲突，已暂停等待处理确认",
}


def numeric(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def mean(values):
    return statistics.mean(values) if values else None


def fmt(value):
    return "NA" if value is None else f"{value:.3f}"


def table(headers, rows):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ")
    lines = ["| " + " | ".join(map(cell, headers)) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    return "\n".join(lines + ["| " + " | ".join(map(cell, row)) + " |" for row in rows])


class Snapshot:
    def __init__(self):
        self.files = {}
        self.cache = {}

    def fingerprint(self, path):
        path = Path(path).resolve()
        key = str(path)
        if key not in self.files and path.exists():
            raw = path.read_bytes()
            self.files[key] = {"path": key, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        return self.files.get(key)

    def read(self, path, default=None, required=False):
        path = Path(path).resolve()
        key = str(path)
        if key in self.cache:
            return self.cache[key]
        if not path.exists():
            if required:
                raise FileNotFoundError(path)
            return default
        raw = path.read_bytes()
        value = json.loads(raw)
        self.files[key] = {"path": key, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
        self.cache[key] = value
        return value


def normalize_rows(rows, cohort):
    result = []
    task_ids, pairs, design_keys = set(), set(), set()
    for source in rows:
        row = dict(source)
        status = STATUS_MAP.get(row.get("status"))
        if status is None:
            raise ValueError(f"Unknown rating status: {row.get('status')}")
        pair = (row["answer_id"], row["judge_label"])
        if row["task_id"] in task_ids or pair in pairs:
            raise ValueError(f"Duplicate rating: {pair}")
        task_ids.add(row["task_id"])
        pairs.add(pair)
        condition = row["answer_condition"]
        design_key = canonical_key(row)
        if design_key in design_keys:
            raise ValueError(f"Duplicate case/question/condition/judge key: {design_key}")
        design_keys.add(design_key)
        if condition not in MATRIX or row["judge_label"] not in MATRIX[condition]:
            raise ValueError(f"Off-design judge assignment: {pair}")
        if status == "valid" and not numeric(row.get("analysis_total")):
            raise ValueError(f"Valid rating has no finite score: {pair}")
        row["status"] = status
        row["analysis_total"] = row.get("analysis_total") if status == "valid" else None
        row["input_cohort"] = cohort
        row["question_number"] = design_key[1]
        result.append(row)
    return result


def status_counts(rows, expected):
    counts = Counter(row["status"] for row in rows)
    if len(rows) > expected:
        raise ValueError(f"Rating overlay has {len(rows)} rows, above expected {expected}")
    counts["pending"] += expected - len(rows)
    return {key: counts[key] for key in ("valid", "refused", "transport_failed", "failed", "pending")}


def question_number(row):
    if row.get("question_number") is not None:
        return int(row["question_number"])
    match = re.search(r"::q([0-9]+)::", row["answer_id"])
    return int(match.group(1)) if match else None


def canonical_key(row):
    question = question_number(row)
    if question not in range(1, 6):
        raise ValueError(f"Expected question number 1–5: {row.get('answer_id')}")
    return row["case_id"], question, row["answer_condition"], row["judge_label"]


def fill_pending_rows(rows, source_answers, manifest, new_dir):
    """Keep every eligible slot in exports even when its overlay row is absent."""
    known = {canonical_key(row): row for row in rows}
    tasks = manifest.get("tasks", [])
    if not tasks:
        tasks = []
        for source in source_answers:
            for judge in MATRIX[source["answer_condition"]]:
                answer_id = source["answer_id"]
                digest = hashlib.sha256(f"{answer_id}|{judge}".encode()).hexdigest()[:20]
                tasks.append({**{key: source.get(key) for key in ("answer_id", "case_id", "answer_condition", "input_ready", *HASH_KEYS)},
                              "question_number": question_number(source), "judge_label": judge,
                              "task_id": f"rating_{digest}"})
    task_keys = [canonical_key(task) for task in tasks]
    if len(task_keys) != len(set(task_keys)):
        raise ValueError("Duplicate design keys in the new manifest/source")
    if tasks and not set(known).issubset(task_keys):
        raise ValueError("New overlay has tasks outside the frozen manifest/source")
    result = list(rows)
    for task in tasks:
        if canonical_key(task) not in known:
            result.append(dict(task, status="pending", analysis_total=None, raw_status=None, raw_total=None,
                               rating_path=str((new_dir / "ratings" / (task["task_id"] + ".json")).resolve()),
                               reason="No status-overlay row at the report snapshot; no score was inferred.",
                               report_synthesized_pending=True))
    return normalize_rows(result, "rebuilt_inputs_20")


def mask_blockers(snapshot, new_dir, row):
    blockers = []
    slug = re.sub(r"[^A-Za-z0-9_-]", "_", row["answer_condition"])
    for field in ("title", "case_text", "judge_decision"):
        path = new_dir / "mask_parts" / row["case_id"] / slug / (field + ".json")
        record = snapshot.read(path, {})
        if record.get("status") == "success":
            continue
        last_call = (record.get("calls") or [{}])[-1]
        error = str(last_call.get("error") or record.get("error") or "")
        finish_reason = last_call.get("finish_reason")
        if finish_reason in {"length", "max_tokens"}:
            cause = "truncated"
        elif any(token in error for token in TRANSPORT_TOKENS):
            cause = "transport_failed"
        elif str(record.get("value") or "").strip().lower().startswith("the prompt could not be submitted.") or any(
                e.get("error_code") in {"content_filter", "prompt_blocked", "content_policy_violation", "safety_violation"}
                for e in record.get("receipt_evidence", [])):
            cause = "refused"
        else:
            cause = "failed" if record.get("status") == "failed" else "pending"
        blockers.append(dict(field=field, cause=cause, raw_status=record.get("status"),
                             dispatch_count=record.get("dispatch_count", 0), finish_reason=finish_reason,
                             error=error or None, path=str(path.resolve()),
                             sha256=(snapshot.files.get(str(path.resolve())) or {}).get("sha256")))
    return blockers


def merge_details(snapshot, old_rows, new_rows, source_answers, manifest, old_dir, new_dir):
    """Export analysis NA separately from preserved raw totals and source records."""
    source_by_id = {row["answer_id"]: row for row in source_answers}
    tasks_by_id = {row["task_id"]: row for row in manifest.get("tasks", [])}
    records, design_keys, task_ids = [], set(), set()
    for cohort_rows, directory in ((old_rows, old_dir), (new_rows, new_dir)):
        overlay_path = str((directory / "rating_status_overlay.json").resolve())
        for row in cohort_rows:
            key = canonical_key(row)
            if key in design_keys or row["task_id"] in task_ids:
                raise ValueError(f"Duplicate merged rating key: {key}")
            design_keys.add(key)
            task_ids.add(row["task_id"])
            detail = dict(row, canonical_key=list(key), overlay_path=overlay_path,
                          overlay_sha256=(snapshot.files.get(overlay_path) or {}).get("sha256"),
                          detailed_status=row["status"], score_is_missing=row["status"] != "valid")
            source, task = source_by_id.get(row["answer_id"], {}), tasks_by_id.get(row["task_id"], {})
            detail["input_rebuilt"] = row["input_cohort"] == "rebuilt_inputs_20"
            detail["input_provenance"] = "rebuilt_20260908" if detail["input_rebuilt"] else "historical_preserved"
            detail["manifest_source"] = row.get("manifest_source", "new_api" if detail["input_rebuilt"] else "historical_overlay")
            if detail["input_rebuilt"]:
                detail["source_answer_path"] = str((new_dir / "source_answers.json").resolve())
                detail["source_answer_locator"] = {"answer_id": row["answer_id"]}
                detail["manifest_path"] = str((new_dir / "manifest.json").resolve())
                detail.update({name: task.get(name, source.get(name)) for name in HASH_KEYS})
                if row.get("rating_path"):
                    fingerprint = snapshot.fingerprint(row["rating_path"])
                    detail["rating_file_present_at_snapshot"] = fingerprint is not None
                    detail["rating_sha256"] = (fingerprint or {}).get("sha256")
                if row["status"] == "pending" and row.get("input_ready", source.get("input_ready")) is False:
                    detail["detailed_status"] = "missing_input"
                    detail["input_blockers"] = mask_blockers(snapshot, new_dir, row)
                    detail["input_blocking_cause"] = (detail["input_blockers"] or [{}])[0].get("cause", "pending")
                elif row["status"] == "failed" and row.get("rating_path"):
                    record = snapshot.read(Path(row["rating_path"]), {})
                    if (record.get("scoring") or {}).get("finish_reason") in {"length", "max_tokens"}:
                        detail["detailed_status"] = "truncated"
            records.append(detail)
    records.sort(key=lambda row: canonical_key(row))
    return records


def detailed_counts(records):
    counts = Counter(row["detailed_status"] for row in records)
    return {status: counts[status] for status in DETAIL_STATUSES}


def budget_summary(plan, state, ledger, authorization):
    original = parameter(plan, "budget_usd", parameter(plan, "budget", 40))
    state_budget = state.get("budget") or {}
    effective = ledger.get("budget_usd", state_budget.get("budget_usd", original))
    return {
        "frozen_plan_cap_usd": original, "effective_cap_usd": effective,
        "known_token_price_estimate_usd": ledger.get("estimated_all_http_usd", state_budget.get("estimated_all_http_usd")),
        "observed_account_debit_usd": ledger.get("observed_debit_usd", state_budget.get("observed_debit_usd")),
        "account_debit_checked_at": ledger.get("balance_checked_at", state_budget.get("balance_checked_at")),
        "guard_accounted_usd_not_actual_spend": ledger.get("accounted_including_prepaid_usd", state_budget.get("accounted_including_prepaid_usd")),
        "unknown_cost_allowance_usd_not_confirmed_spend": ledger.get("unknown_upper_usd", state_budget.get("unknown_upper_usd")),
        "reserved_inflight_usd_not_actual_spend": ledger.get("reserved_inflight_usd", state_budget.get("reserved_inflight_usd")),
        "ledger_updated_at": ledger.get("updated_at"), "authorization": authorization,
        "scope": "Original-20 supplement only, including its earlier masking, scoring and retries; not a new budget or the old-88 lifetime bill.",
        "account_debit_limitation": "Observed account debit may include concurrent account activity and stops at its last balance check; not an exact isolated experiment invoice.",
    }


def answer_consensus(rows, source_answers=()):
    answers = {}
    for source in source_answers:
        answer_id = source["answer_id"]
        if answer_id in answers:
            raise ValueError(f"Duplicate source answer: {answer_id}")
        answers[answer_id] = {
            "answer_id": answer_id, "case_id": source["case_id"],
            "answer_condition": source["answer_condition"],
            "question_number": question_number(source), "ratings": {}, "consensus": None,
        }
    for row in rows:
        answer_id = row["answer_id"]
        entry = answers.setdefault(answer_id, {
            "answer_id": answer_id, "case_id": row["case_id"],
            "answer_condition": row["answer_condition"],
            "question_number": question_number(row), "ratings": {}, "consensus": None,
        })
        if entry["case_id"] != row["case_id"] or entry["answer_condition"] != row["answer_condition"]:
            raise ValueError(f"Rating/source mismatch: {answer_id}")
        entry["ratings"][row["judge_label"]] = row
    for entry in answers.values():
        expected_judges = set(MATRIX[entry["answer_condition"]])
        valid = {judge: row["analysis_total"] for judge, row in entry["ratings"].items() if row["status"] == "valid"}
        if set(valid) == expected_judges:
            entry["consensus"] = statistics.median(valid.values())
    return answers


def complete_case_summary(answers):
    by_case = defaultdict(list)
    for entry in answers.values():
        by_case[entry["case_id"]].append(entry)
    complete = []
    expected_keys = {(condition, q) for condition in CONDITIONS for q in range(1, 6)}
    for case_id, entries in by_case.items():
        keys = {(e["answer_condition"], e["question_number"]) for e in entries}
        if len(entries) == 25 and keys == expected_keys and all(e["consensus"] is not None for e in entries):
            complete.append(case_id)
    complete.sort()
    summary = []
    for condition in CONDITIONS:
        per_case = [{"case_id": case_id, "consensus": mean([
            entry["consensus"] for entry in by_case[case_id] if entry["answer_condition"] == condition
        ])} for case_id in complete]
        summary.append({"condition": condition, "case_n": len(complete), "answer_n": len(complete) * 5,
                        "mean": mean([row["consensus"] for row in per_case]), "case_means": per_case})
    return {"complete_case_ids": complete, "summary": summary,
            "answers_with_three_valid_judges": sum(e["consensus"] is not None for e in answers.values())}


def score_matrix(rows):
    result = []
    for condition in CONDITIONS:
        judges = {}
        for judge in JUDGES:
            scores = [r["analysis_total"] for r in rows if r["answer_condition"] == condition
                      and r["judge_label"] == judge and r["status"] == "valid"]
            judges[judge] = {"assigned": judge in MATRIX[condition], "n": len(scores), "mean": mean(scores)}
        result.append({"condition": condition, "judges": judges})
    return result


def matrix_rows(matrix):
    return [[row["condition"]] + [
        f"{fmt(row['judges'][judge]['mean'])}（n={row['judges'][judge]['n']}）"
        if row["judges"][judge]["assigned"] else "—" for judge in JUDGES
    ] for row in matrix]


def parameter(plan, key, default):
    return plan.get(key, (plan.get("metadata") or {}).get(key, default))


def build_report(old_dir, new_dir):
    snapshot = Snapshot()
    started = datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds")
    old_analysis = snapshot.read(old_dir / "analysis_snapshot.json", required=True)
    old_overlay = snapshot.read(old_dir / "rating_status_overlay.json", required=True)
    old_rows = normalize_rows(old_overlay["records"], "historical_inputs_88")
    if len(old_rows) != 6600 or len({r["case_id"] for r in old_rows}) != 88:
        raise ValueError("Expected the preserved 88-case, 6,600-rating overlay")
    plan = snapshot.read(new_dir / "plan.json", {})
    state = snapshot.read(new_dir / "state.json", {})
    ledger = snapshot.read(new_dir / "budget_ledger.json", {})
    authorization = snapshot.read(new_dir / "budget_amendments/20260909_user_40_to_70.authorization.json", {})
    recovery_authorization = snapshot.read(new_dir / "recovery_authorization_20260909.json", {})
    token_amendment_path = new_dir / "token_amendment_20260909.json"
    token_amendment = snapshot.read(token_amendment_path, {})
    manifest = snapshot.read(new_dir / "manifest.json", {"tasks": []})
    quality_hold = snapshot.read(new_dir / "quality_hold.json", {})
    source = snapshot.read(new_dir / "source_answers.json", [])
    source = source.get("answers", []) if isinstance(source, dict) else source
    new_overlay = snapshot.read(new_dir / "rating_status_overlay.json", {"records": []})
    new_rows = normalize_rows(new_overlay["records"], "rebuilt_inputs_20")
    new_rows = fill_pending_rows(new_rows, source, manifest, new_dir)
    expected_answers = int(parameter(plan, "expected_valid_answers", 499))
    expected_ratings = int(parameter(plan, "expected_ratings", 1497))
    excluded = parameter(plan, "excluded_generation_failures", 1)
    excluded_n = len(excluded) if isinstance(excluded, (list, dict)) else int(excluded)
    if expected_ratings != expected_answers * 3 or expected_answers + excluded_n != 500:
        raise ValueError("Expected 500 original answer slots, with three ratings per usable answer")
    if source and len(source) != expected_answers:
        raise ValueError("Source answer count does not match the frozen plan")
    old_cases = {r["case_id"] for r in old_rows}
    new_cases = set(plan.get("case_ids", [])) | {a["case_id"] for a in source} | {r["case_id"] for r in new_rows}
    if old_cases & new_cases or len(new_cases) > 20:
        raise ValueError("Old 88 and rebuilt 20 case sets must be disjoint")
    if source:
        source_ids = {a["answer_id"] for a in source}
        if any(row["answer_id"] not in source_ids for row in new_rows):
            raise ValueError("New rating is outside the source-answer cohort")
    all_rows = old_rows + new_rows
    merged_records = merge_details(snapshot, old_rows, new_rows, source, manifest, old_dir, new_dir)
    if len(merged_records) != 6600 + expected_ratings:
        raise ValueError("Cannot export all eligible unique ratings without a full new manifest or source-answer roster")
    token_exception_rows = []
    if token_amendment:
        if token_amendment.get("case_id") not in new_cases or token_amendment.get("field") != "case_text":
            raise ValueError("The masking-token amendment must identify one rebuilt-cohort case_text scope")
        for row in merged_records:
            if row["input_rebuilt"] and row["case_id"] == token_amendment["case_id"]:
                row["masking_token_limit_exception"] = {
                    "authorization_path": str(token_amendment_path.resolve()),
                    "field": token_amendment["field"], "authorized_max_tokens": token_amendment["max_tokens"],
                    "previous_initial_max_tokens": token_amendment["previous_initial_max_tokens"],
                    "previous_retry_max_tokens": token_amendment["previous_retry_max_tokens"],
                    "note": "Authorized exception for rebuilt case_text masking only; not a scoring-token change. Original records/receipts show actual attempts.",
                }
                token_exception_rows.append(row)
    detail_counts = {
        "historical_inputs_88": detailed_counts([r for r in merged_records if not r["input_rebuilt"]]),
        "rebuilt_inputs_20": detailed_counts([r for r in merged_records if r["input_rebuilt"]]),
        "combined": detailed_counts(merged_records),
    }
    mask_causes = Counter(r["input_blocking_cause"] for r in merged_records if r["detailed_status"] == "missing_input")
    missing_input_cases = sorted({r["case_id"] for r in merged_records if r["detailed_status"] == "missing_input"})
    rated_cases = {r["case_id"] for r in merged_records if r["status"] in {"valid", "refused"}}
    old_counts, new_counts = status_counts(old_rows, 6600), status_counts(new_rows, expected_ratings)
    total_counts = {k: old_counts[k] + new_counts[k] for k in old_counts}
    old_answers, new_answers = answer_consensus(old_rows), answer_consensus(new_rows, source)
    old_complete = complete_case_summary(old_answers)
    new_complete = complete_case_summary(new_answers)
    all_complete = complete_case_summary({**old_answers, **new_answers})
    if set(old_complete["complete_case_ids"]) != set(old_analysis["complete_case_ids"]):
        raise ValueError("Old analysis and overlay are not the same complete-case snapshot")
    cohort_counts = {"historical_inputs_88": old_counts, "rebuilt_inputs_20": new_counts, "combined": total_counts}
    by_judge = {}
    for judge in JUDGES:
        old_c = Counter(r["status"] for r in old_rows if r["judge_label"] == judge)
        new_c = Counter(r["status"] for r in new_rows if r["judge_label"] == judge)
        by_judge[judge] = {"old": dict(old_c), "new": dict(new_c), "combined": dict(old_c + new_c)}
    phase = state.get("phase", "尚未开始" if not plan else "已建立补充实验计划")
    if quality_hold.get("active"):
        phase = "input_quality_hold"
    phase_label = PHASE_LABELS.get(phase, phase)
    pending_n = new_counts["pending"]
    pending_input_n = sum(r["status"] == "pending" and r.get("input_ready") is False for r in new_rows)
    pending_scoring_n = sum(r["status"] == "pending" and r.get("input_ready") is True for r in new_rows)
    pending_unknown_n = pending_n - pending_input_n - pending_scoring_n
    failures_n = new_counts["transport_failed"] + new_counts["failed"]
    ready_groups = state.get("mask_groups_ready", 0)
    expected_groups = state.get("mask_groups_expected", 100)
    expected_total = 6600 + expected_ratings
    all_inputs_ready = ready_groups == expected_groups and all(r.get("input_ready") is True for r in new_rows)
    completed_attempts = (all_inputs_ready and not quality_hold.get("active")
                          and total_counts["valid"] + total_counts["refused"] == expected_total)
    if completed_attempts:
        progress = f"108 案已完成互评尝试：{expected_total:,} 个可评任务均取得有效评分或明确拒评；{total_counts['refused']:,} 条拒评仍为 NA，不等同于所有任务均取得有效评分。"
    elif not pending_n and not failures_n:
        progress = "20 案可评任务已有有效评分或明确拒评，但输入准备状态尚未核对齐全；暂不宣称 108 案全部完成。"
    elif phase == "input_quality_hold":
        progress = "已暂停：首案重建文本的人物编号与旧回答不一致，尚未启动本批评分；不能称 108 案全量互评完成。"
    elif not pending_n:
        progress = "20 案任务已尝试，仍有接口异常或其他失败，尚未全部处理完成。"
    else:
        progress = "20 案补充互评尚未完成；以下为当前快照，不能称 108 案全量互评完成。"
    matrix_all, matrix_new, matrix_old = score_matrix(all_rows), score_matrix(new_rows), score_matrix(old_rows)
    cost_summary = budget_summary(plan, state, ledger, authorization)
    budget = cost_summary["effective_cap_usd"]
    direct_scoring_with_unresolved_aliases = (
        quality_hold.get("scoring_policy") == "as_generated_no_entity_correction"
        and quality_hold.get("identity_conflict_resolved") is False
        and not quality_hold.get("active")
    )
    analysis = {
        "snapshot_started_at": started, "updated_at": datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds"),
        "old_snapshot_at": old_analysis.get("updated_at"), "new_overlay_at": new_overlay.get("updated_at"),
        "phase": phase, "phase_zh": phase_label, "progress": progress, "population_cases": 108,
        "completed_mutual_evaluation_attempts": completed_attempts,
        "all_eligible_scores_available": total_counts["valid"] == expected_total,
        "input_cohorts": {"historical_inputs": 88, "rebuilt_inputs": 20},
        "cases_with_valid_or_refused_ratings": len(rated_cases), "missing_input_case_ids": missing_input_cases,
        "answer_slots": 2700, "excluded_generation_failures": excluded_n,
        "scorable_answers": 2200 + expected_answers, "planned_rating_slots_before_exclusion": 8100,
        "excluded_rating_slots_due_to_generation_failure": excluded_n * 3,
        "scorable_rating_tasks": expected_total, "cohort_status_counts": cohort_counts,
        "cohort_detailed_status_counts": detail_counts,
        "missing_input_causes_rating_slots_not_api_requests": dict(mask_causes),
        "by_judge": by_judge, "old_complete_case_analysis": old_complete,
        "new_complete_case_analysis": new_complete, "pooled_complete_case_analysis": all_complete,
        "who_rates_whom_all_available": matrix_all, "who_rates_whom_rebuilt20": matrix_new,
        "who_rates_whom_historical88": matrix_old,
        "mask_groups_ready": ready_groups, "mask_groups_expected": expected_groups,
        "new_pending_breakdown": {"waiting_for_inputs": pending_input_n, "waiting_for_scores": pending_scoring_n,
                                  "not_created_or_unspecified": pending_unknown_n},
        "new_budget_cap_usd": budget, "state_budget": state.get("budget"),
        "new_budget_details": cost_summary,
        "recovery_authorization": recovery_authorization,
        "masking_token_amendment": token_amendment,
        "masking_token_amendment_path": str(token_amendment_path.resolve()) if token_amendment else None,
        "masking_token_exception_rating_slots": len(token_exception_rows),
        "merged_detail": {"file": "merged_rating_details.json", "records": len(merged_records),
                          "unique_key_fields": ["case_id", "question_number", "answer_condition", "judge_label"],
                          "unique_keys": len(merged_records), "missing_score_json_value": None,
                          "excluded_generation_failures": excluded},
        "merged_records": merged_records,
        "quality_hold": quality_hold,
        "direct_scoring_with_unresolved_aliases": direct_scoring_with_unresolved_aliases,
        "rules": {
            "validity": "Reuse supplied status overlays and original parsed scores; refusals/errors/pending are NA, never imputed as zero.",
            "consensus": "Each answer requires all three assigned cross-family judges; consensus is their total-score median.",
            "complete_case": "Same complete cases for all five conditions: exactly five questions per condition, all with three valid judges. First average five questions per condition within each case, then weight cases equally.",
            "rebuilt_input": "Historical 20 questions/answers are reused but masked inputs rebuilt. All assigned judges are rerun; no comparison to old DS scores is labeled as a model-only paired intervention.",
            "masking_token_exception": "If masking_token_amendment is present, only its named case_text masking scopes use the authorized longer output limit and extra persisted retries. Title/judgment masking, scoring requests, prompts, models, original questions/answers, parsing and penalties remain unchanged. This report does not describe all masking parameters as identical.",
            "generation_failure": "One historical generation failure excludes one answer and its three rating slots; not a refusal, transport error, or zero score.",
            "human": "Expert verification is not completed by this model-only report.",
        },
        "source_files": list(snapshot.files.values()),
        "generator_path": str(Path(__file__).resolve()), "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
    }
    count_row = lambda name, expected, c: [name, f"{expected:,}"] + [f"{c[k]:,}" for k in DETAIL_STATUSES]
    lines = [
        "# 108 案最终评价互评：20 案重建输入补充", "",
        f"快照：{analysis['updated_at']}（UTC+08:00）；补充实验阶段：{phase_label}。", "", progress, "",
        "## 1. 范围与进度", "",
        table(["项目", "数量 / 说明"], [
            ["目标案件", "108 案 = 88 案保留旧输入 + 20 案重建脱敏输入"],
            ["已有有效评分或明确拒评的案件", f"{len(rated_cases)} / 108；缺输入案件 {len(missing_input_cases)}"],
            ["原始答案槽位 / 可评答案", f"2,700 / {2200 + expected_answers:,}"],
            ["原始评分槽位 / 可评任务", f"8,100 / {expected_total:,}（每答案 3 个跨家族评分模型）"],
            ["历史生成失败", f"{excluded_n} 条答案；排除其 {excluded_n * 3} 个评分槽位，不算拒评或评分 API 故障"],
            ["20 案脱敏单元准备", f"{ready_groups} / {expected_groups}"],
            ["20 案待处理任务细分", f"等待输入 {pending_input_n}；输入就绪待评分 {pending_scoring_n}；未创建 / 未标明 {pending_unknown_n}"],
            ["三名有效评分齐全的答案", f"88 案：{old_complete['answers_with_three_valid_judges']:,}；20 案：{new_complete['answers_with_three_valid_judges']:,}"],
            ["20 案累计预算上限", f"${budget:g}（含此前本批费用；原冻结计划 ${cost_summary['frozen_plan_cap_usd']:g} 保留为历史）"],
        ]), "",
        table(["输入组", "可评任务"] + [DETAIL_LABELS[k] for k in DETAIL_STATUSES], [
            count_row("88 案旧输入", 6600, detail_counts["historical_inputs_88"]),
            count_row("20 案重建输入", expected_ratings, detail_counts["rebuilt_inputs_20"]),
            count_row("合计", expected_total, detail_counts["combined"]),
        ]), "",
        f"有效评分覆盖率为 {total_counts['valid'] / expected_total * 100:.2f}%（分母包含缺输入和待评分任务，并非已调用接口成功率）；当前评分连接失败 / 超时占可评任务 {total_counts['transport_failed'] / expected_total * 100:.2f}%。脱敏失败导致的缺输入与评分 API 故障分开计数；每个缺输入评分槽位不代表一次已发生的 API 请求。", "",
        "## 2. 本次改变与保留", "",
        "20 案保留历史问题和有效回答，重建脱敏输入后，再由所有对应评分模型对各答案的同一份重建输入评分。旧 88 案结果保留。脱敏沿用 DeepSeek v3.2 及原 prompt；若有经授权的输出长度例外，则在下表单独列出。评分 prompt、模型、参数、评分标准、解析与扣分规则保持不变。", "",
        "20 案不复用旧 DeepSeek 分数作为本次同输入评分，也不将“旧输入的 DS 分数 vs 新输入互评”称为仅更换评分模型的配对比较。", "",
        "## 3. 各评分模型当前结果", "",
        table(["评分模型", "88 案有效", "20 案有效", "合计拒评", "合计连接失败 / 超时", "合计其他失败"], [
            [judge, by_judge[judge]["old"].get("valid", 0), by_judge[judge]["new"].get("valid", 0),
             by_judge[judge]["combined"].get("refused", 0), by_judge[judge]["combined"].get("transport_failed", 0),
             by_judge[judge]["combined"].get("failed", 0)] for judge in JUDGES
        ]), "",
        "## 4. 谁评价谁：合并可用评分均值（n）", "",
        "行是作答模型，列是评分模型；0–20 分，沿用原惩罚后总分。— 为按设计不评分同家族；NA 为尚无有效评分。各格使用自身有效样本，不能以列均值直接比较模型评分宽严。", "",
        table(["作答条件"] + JUDGES, matrix_rows(matrix_all)), "",
        "## 5. 同案件条件比较：按输入来源分层", "",
        "仅使用五条件共同完整案件：每案 25 个答案均有 3 份有效评分。先取每答案三分中位数，再平均同案同条件 5 题，最后案件等权平均；三列采用各自组内相同案件集合。", "",
        table(["作答条件", f"88 案旧输入（完整 n={len(old_complete['complete_case_ids'])}）",
               f"20 案重建输入（完整 n={len(new_complete['complete_case_ids'])}）",
               f"合并描述（完整 n={len(all_complete['complete_case_ids'])}）"], [
            [condition, fmt(old_complete["summary"][i]["mean"]), fmt(new_complete["summary"][i]["mean"]),
             fmt(all_complete["summary"][i]["mean"])] for i, condition in enumerate(CONDITIONS)
        ]), "",
        "合并列是不同输入来源案件的分层合并描述，不是重建输入效果估计。历史生成失败所在案件不满足完整案件条件；缺失及完整案件筛选均可能产生选择偏倚。", "",
        "## 6. 20 案重建输入：谁评价谁", "",
        table(["作答条件"] + JUDGES, matrix_rows(matrix_new)), "",
        "## 7. 88 案旧输入：谁评价谁", "",
        table(["作答条件"] + JUDGES, matrix_rows(matrix_old)), "",
        "## 8. 本批 20 案费用", "",
        table(["口径", "美元 / 说明"], [
            ["当前累计上限", f"${budget:g}；含此前本批脱敏、评分及重试"],
            ["按返回 token 和价格计算的已知费用", fmt(cost_summary["known_token_price_estimate_usd"])],
            ["账户已观察扣减", f"{fmt(cost_summary['observed_account_debit_usd'])}；截至 {cost_summary['account_debit_checked_at'] or '未记录'}"],
            ["预算保护计入额", f"{fmt(cost_summary['guard_accounted_usd_not_actual_spend'])}；含保守准备，不是实际支出"],
            ["未知费用准备 / 在途预留", f"{fmt(cost_summary['unknown_cost_allowance_usd_not_confirmed_spend'])} / {fmt(cost_summary['reserved_inflight_usd_not_actual_spend'])}；不是已确认支出"],
        ]), "",
        "账户扣减仅到最后余额检查时点，可能包含账户其他并发用量，不能视为本实验精确账单；上表不把旧 88 案历史费用计入本批 20 案上限。", "",
        "## 9. 使用边界", "",
        "拒评、接口异常和待处理均不填 0；有效评分本身为 0 时保留。每案多题存在相关性，本报告仅给描述统计，不报告以答案为独立案件计算的显著性。专家复核尚未完成，模型互评不能替代专家验证。", "",
        f"逐条合并明细另存 merged_rating_details.json：{expected_total:,} 条唯一“案件 × 题号 × 作答条件 × 评分模型”记录，保留旧 / 重建输入来源、原始状态与总分、分析状态与分数（缺失为 null）、原始评分路径和可用输入哈希。analysis_snapshot.json 含分组计数、共同完整案件 ID、案件级均值、统计规则和来源 SHA-256；原 88 案报告及所有原始评分文件均未覆盖。", "",
    ]
    if mask_causes:
        position = lines.index("## 2. 本次改变与保留")
        lines[position:position] = [
            table(["缺输入的直接原因", "受影响评分槽位"],
                  [[MASK_CAUSE_LABELS.get(cause, cause), count] for cause, count in sorted(mask_causes.items())]), "",
            "缺输入案件 ID：" + "、".join(missing_input_cases) + "。", "",
        ]
    if direct_scoring_with_unresolved_aliases:
        position = lines.index("## 2. 本次改变与保留") + 2
        lines[position:position] = [
            "已知输入限制：首案重建脱敏文本的人物编号与旧回答存在冲突，尚未修正。经明确确认，本批按生成后的输入直接评分；人物编号不一致可能影响事实一致性得分，需专家复核，不能把相关差异全部归因于评分模型。原始脱敏输出、旧问题和回答均保留。", "",
        ]
    if token_amendment:
        position = lines.index("## 3. 各评分模型当前结果")
        limits = token_amendment.get("dispatch_limits", {})
        limit_values = sorted(set(limits.values())) if isinstance(limits, dict) else []
        limit_text = "、".join(str(value) for value in limit_values) or "见授权记录"
        lines[position:position] = [
            table(["经授权的脱敏参数例外", "范围 / 说明"], [
                ["唯一案件", token_amendment["case_id"]],
                ["步骤", f"5 个作答条件各自的事实正文（case_text）脱敏；涉及 {len(token_exception_rows)} 个可评评分槽位"],
                ["输出 token 上限", f"原始请求 {token_amendment['previous_initial_max_tokens']:,}（原截断重试 {token_amendment['previous_retry_max_tokens']:,}）→ 本案获准 {token_amendment['max_tokens']:,}"],
                ["追加重试", f"保留原 4 次外层尝试记录，再允许每个获批 scope 最多 3 次；累计 dispatch 上限 {limit_text}，不是重新清零"],
                ["不变部分", "标题与判决脱敏设置、脱敏模型和 prompt、评分模型与全部评分参数、历史问题回答、解析扣分规则不变"],
            ]), "",
            "该例外用于处理原输出长度不足导致的事实正文截断，不代表全部脱敏参数完全相同。授权、历史失败与后续收据均保留；此案仍属于“20 案重建输入”层，并在逐条明细中标记。", "",
        ]
    return analysis, "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-dir", type=Path, default=OLD_DIR)
    parser.add_argument("--new-dir", type=Path, default=NEW_DIR)
    parser.add_argument("--output-dir", type=Path, default=OUTPUT_DIR)
    args = parser.parse_args()
    if any(args.output_dir.resolve().is_relative_to(path.resolve()) for path in (args.old_dir, args.new_dir)):
        raise ValueError("Output must not overwrite an input directory")
    analysis, markdown = build_report(args.old_dir, args.new_dir)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    report = args.output_dir / "108案_互评补充结果.md"
    report.write_text(markdown, encoding="utf-8")
    records = analysis.pop("merged_records")
    details = dict(schema_version=1, updated_at=analysis["updated_at"],
                   unique_key_fields=analysis["merged_detail"]["unique_key_fields"],
                   expected_eligible_ratings=analysis["scorable_rating_tasks"],
                   excluded_generation_failures=analysis["merged_detail"]["excluded_generation_failures"],
                   cohort_detailed_status_counts=analysis["cohort_detailed_status_counts"],
                   masking_token_amendment=analysis["masking_token_amendment"],
                   masking_token_amendment_path=analysis["masking_token_amendment_path"],
                   rules=analysis["rules"], source_files=analysis["source_files"], records=records)
    detail_path = args.output_dir / "merged_rating_details.json"
    detail_path.write_text(json.dumps(details, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    analysis["merged_detail"].update(path=str(detail_path.resolve()), sha256=hashlib.sha256(detail_path.read_bytes()).hexdigest())
    (args.output_dir / "analysis_snapshot.json").write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"report": str(report.resolve()), "phase": analysis["phase"],
                      "status": analysis["cohort_status_counts"], "lines": len(markdown.splitlines())}, ensure_ascii=False))


if __name__ == "__main__":
    main()
