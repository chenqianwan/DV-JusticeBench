#!/usr/bin/env python3
"""Build an auditable, read-only-source Markdown snapshot of the 88-case ratings.

No model calls, rescoring, or writes to the input/legacy data are performed.
An optional JSON override index maps task IDs to independently retried rating files.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import redirect_stdout
from datetime import datetime
import hashlib
import io
import itertools
import json
import math
from pathlib import Path
import statistics
import subprocess
import sys
from zoneinfo import ZoneInfo


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data/experiment1_cross_judge_full108_xhub_20260908"
CONDITIONS = ["GPT-4o", "Gemini 2.5 Flash", "Qwen-Max", "DeepSeek thinking", "DeepSeek non-thinking"]
JUDGES = ["GPT-4o", "Gemini 2.5 Flash", "Qwen-Max", "DeepSeek v3.2"]
SHORT = {"GPT-4o": "GPT-4o", "Gemini 2.5 Flash": "Gemini", "Qwen-Max": "Qwen-Max", "DeepSeek v3.2": "DeepSeek", "DeepSeek thinking": "DeepSeek thinking", "DeepSeek non-thinking": "DeepSeek non-thinking"}
LABELS = {"valid": "有效评分", "still_refused": "仍拒评", "interface_refusal": "接口拒评", "transport_failed": "接口失败", "unusable": "不可用", "missing": "未取得"}


def numeric(value):
    return isinstance(value, (float, int)) and not isinstance(value, bool) and math.isfinite(value)


def message_hash(messages):
    encoded = json.dumps(messages, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def average(values):
    return statistics.mean(values) if values else None


def fmt(value, digits=3):
    return "NA" if value is None else f"{value:.{digits}f}"


def rate(count, total):
    return f"{count}（{count / total * 100:.2f}%）" if total else "0（NA）"


def ranks(values):
    order = sorted(range(len(values)), key=lambda i: values[i])
    result = [0.0] * len(values)
    i = 0
    while i < len(order):
        j = i + 1
        while j < len(order) and values[order[j]] == values[order[i]]:
            j += 1
        rank = (i + 1 + j) / 2
        for ix in order[i:j]:
            result[ix] = rank
        i = j
    return result


def spearman(x, y):
    if len(x) < 2:
        return None
    rx, ry = ranks(x), ranks(y)
    mx, my = statistics.mean(rx), statistics.mean(ry)
    numerator = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    denominator = math.sqrt(sum((a - mx) ** 2 for a in rx) * sum((b - my) ** 2 for b in ry))
    return numerator / denominator if denominator else None


def table(headers, rows):
    result = ["| " + " | ".join(headers) + " |", "| " + " | ".join("---" for _ in headers) + " |"]
    result.extend("| " + " | ".join(str(cell).replace("|", "\\|").replace("\n", " ") for cell in row) + " |" for row in rows)
    return "\n".join(result)


class Snapshot:
    def __init__(self):
        self.files = {}
        self.cache = {}

    def read(self, filename):
        path = Path(filename).resolve()
        key = str(path)
        if key not in self.cache:
            content = path.read_bytes()
            self.files[key] = {"path": key, "sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content)}
            self.cache[key] = json.loads(content)
        return self.cache[key]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "outputs/20260908_cross_judge_current")
    parser.add_argument("--override-index", type=Path, action="append", default=[], help="JSON task_id -> rating path mapping, or an object with rating_files mapping")
    args = parser.parse_args()
    snapshot = Snapshot()
    try:
        process_snapshot = subprocess.run(["ps", "-axo", "pid=,command="], capture_output=True, text=True, check=True).stdout
        transport_retry_running = any("run_cross_judging_full108_xhub.py" in line and "--retry-transport-only" in line for line in process_snapshot.splitlines())
    except (OSError, subprocess.SubprocessError):
        transport_retry_running = None
    started_at = datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds")
    manifest = snapshot.read(DATA / "manifest_ready88.json")
    answers = snapshot.read(DATA / "source_answers_ready88.json")
    retry_manifest = snapshot.read(DATA / "gemini_refusal_retry43_20260908/manifest.json")
    retry_index = snapshot.read(DATA / "gemini_refusal_retry43_20260908/retry_results_index.json")
    refusal_ids = {task["task_id"] for task in retry_manifest["tasks"]}
    retry_records = {row["task_id"]: row for row in retry_index["ratings"]}
    assert len(refusal_ids) == 43
    assert all(retry_records[task_id]["retry_outcome"] == "still_refused" for task_id in refusal_ids)
    overrides = {}
    for index_path in args.override_index:
        index = snapshot.read(index_path)
        overrides.update(index.get("rating_files", index))

    # HTTP 400 alone does not establish refusal. Match the original request messages
    # exactly to a physical receipt with an explicit provider refusal code.
    refusal_receipts = defaultdict(list)
    for receipt_path in sorted((DATA / "http_receipts").glob("*.json")):
        try:
            receipt = json.loads(receipt_path.read_bytes())
        except (json.JSONDecodeError, OSError):
            continue  # A running worker may not yet have finished writing this receipt.
        code = ((receipt.get("response") or {}).get("error") or {}).get("code")
        if receipt.get("http_status") == 400 and code in ("content_filter", "prompt_blocked"):
            request = receipt.get("request") or {}
            key = (request.get("model"), message_hash(request.get("messages")))
            refusal_receipts[key].append({"path": str(receipt_path.resolve()), "code": code})
    sys.path.insert(0, str(ROOT))
    with redirect_stdout(io.StringIO()):
        from scripts.pilot_cross_judging_budget_xhub import payload_for

    by_answer = {answer["answer_id"]: {"source": answer, "ratings": {}, "consensus": None} for answer in answers}
    assert len(by_answer) == 2200 and len(manifest["tasks"]) == 6600
    task_rows = []
    for task in manifest["tasks"]:
        task_id = task["task_id"]
        rating_path = Path(overrides[task_id]) if task_id in overrides else (Path(task["prior_rating_path"]) if task["source"] == "reused_cross40" else DATA / "ratings" / f"{task_id}.json")
        record = snapshot.read(rating_path) if rating_path.exists() else {}
        if record:
            assert record["task_id"] == task_id
            assert record["answer_id"] == task["answer_id"]
        raw_total = (record.get("evaluation") or {}).get("总分")
        error = record.get("error") or ""
        refusal_evidence = []
        if "400 Client Error" in error:
            source_answer = by_answer[task["answer_id"]]["source"]
            with redirect_stdout(io.StringIO()):
                expected_messages = payload_for(source_answer)
            refusal_evidence = refusal_receipts.get((task["judge_model_id"], message_hash(expected_messages)), [])
            for evidence in refusal_evidence:
                snapshot.read(evidence["path"])
        if task_id in refusal_ids:
            # The user approved retaining these as repeated refusals, never zero scores.
            retry_record = retry_records[task_id]
            retried = snapshot.read(retry_record["retry_rating_path"])
            assert retried.get("task_id") == task_id
            status, reason = "still_refused", "Gemini 43 条原拒评逐条重试后仍拒评；legacy 解析 0 不作为有效分数。"
        elif record.get("status") == "success" and numeric(raw_total):
            status, reason = "valid", "沿用原始评价总分（含原惩罚规则）；真实 0 分保留，不重新解析。"
        elif refusal_evidence:
            codes = ", ".join(sorted({evidence["code"] for evidence in refusal_evidence}))
            status, reason = "interface_refusal", f"接口拒评（HTTP 400；{codes}）；已按原模型及完整 messages 精确匹配物理回执，未取得评分。"
        elif any(token in error for token in ("ProxyError", "ReadTimeout", "ConnectTimeout", "ConnectionError", "Timeout")):
            status, reason = "transport_failed", error
        elif not record:
            status, reason = "missing", "没有评分结果文件。"
        else:
            status, reason = "unusable", error or "无有效数值总分或记录未成功。"
        row = {"task_id": task_id, "answer_id": task["answer_id"], "case_id": task["case_id"], "question_number": task["question_number"], "answer_condition": task["answer_condition"], "judge_label": task["judge_label"], "manifest_source": task["source"], "rating_path": str(rating_path.resolve()), "rating_sha256": snapshot.files[str(rating_path.resolve())]["sha256"] if record else None, "raw_status": record.get("status"), "raw_total": raw_total, "analysis_total": raw_total if status == "valid" else None, "status": status, "status_zh": LABELS[status], "reason": reason}
        if task_id in refusal_ids:
            row["retry_path"] = retry_records[task_id]["retry_rating_path"]
            row["retry_outcome"] = "still_refused"
        if refusal_evidence:
            row["refusal_receipt_evidence"] = refusal_evidence
        task_rows.append(row)
        entry = by_answer[task["answer_id"]]
        assert task["judge_label"] not in entry["ratings"]
        entry["ratings"][task["judge_label"]] = row

    all_cases = sorted({answer["case_id"] for answer in answers})
    assert len(all_cases) == 88
    case_answers = defaultdict(list)
    for entry in by_answer.values():
        source = entry["source"]
        assert len(entry["ratings"]) == 3
        assert set(entry["ratings"]) == set(manifest["judge_matrix"][source["answer_condition"]])
        valid = [row["analysis_total"] for row in entry["ratings"].values() if row["status"] == "valid"]
        entry["valid_count"] = len(valid)
        entry["consensus"] = statistics.median(valid) if len(valid) == 3 else None
        case_answers[source["case_id"]].append(entry)
    assert all(len(entries) == 25 for entries in case_answers.values())
    complete_cases = [case for case in all_cases if all(e["consensus"] is not None and numeric(e["source"].get("original_deepseek_total")) for e in case_answers[case])]
    complete_set = set(complete_cases)
    counts = Counter(row["status"] for row in task_rows)
    new_api_n = sum(row["manifest_source"] == "new_api" for row in task_rows)
    new_api_transport_failure_n = sum(row["manifest_source"] == "new_api" and row["status"] == "transport_failed" for row in task_rows)
    by_judge = {judge: Counter(row["status"] for row in task_rows if row["judge_label"] == judge) for judge in JUDGES}
    by_condition = {condition: Counter(row["status"] for row in task_rows if row["answer_condition"] == condition) for condition in CONDITIONS}
    summary = []
    for condition in CONDITIONS:
        entries = [entry for entry in by_answer.values() if entry["source"]["answer_condition"] == condition]
        complete = [entry for entry in entries if entry["source"]["case_id"] in complete_set]
        per_case = []
        for case in complete_cases:
            five = [entry for entry in complete if entry["source"]["case_id"] == case]
            assert len(five) == 5
            per_case.append({"case_id": case, "original": statistics.mean(entry["source"]["original_deepseek_total"] for entry in five), "consensus": statistics.mean(entry["consensus"] for entry in five)})
        original = average([row["original"] for row in per_case])
        consensus = average([row["consensus"] for row in per_case])
        all_complete = [entry for entry in entries if entry["consensus"] is not None]
        individually_complete_cases = {case for case in all_cases if len([entry for entry in all_complete if entry["source"]["case_id"] == case]) == 5}
        summary.append({"condition": condition, "complete_case_n": len(complete_cases), "complete_answer_n": len(complete), "original_mean": original, "consensus_mean": consensus, "paired_difference_mean": consensus - original if consensus is not None and original is not None else None, "all_available_three_valid_answer_n": len(all_complete), "all_available_three_valid_answer_mean": average([entry["consensus"] for entry in all_complete]), "condition_complete_case_n": len(individually_complete_cases), "case_means": per_case})
    for metric, target in (("original_mean", "original_rank"), ("consensus_mean", "consensus_rank")):
        sorted_values = sorted({row[metric] for row in summary if row[metric] is not None}, reverse=True)
        for row in summary:
            row[target] = sorted_values.index(row[metric]) + 1 if row[metric] is not None else None
    matrix = []
    for condition in CONDITIONS:
        matrix.append({"condition": condition, "judges": {judge: {"n": len(values := [row["analysis_total"] for row in task_rows if row["answer_condition"] == condition and row["judge_label"] == judge and row["status"] == "valid"]), "mean": average(values), "assigned": judge in manifest["judge_matrix"][condition]} for judge in JUDGES}})
    agreement = []
    for judge1, judge2 in itertools.combinations(JUDGES, 2):
        pairs = [(entry["ratings"][judge1]["analysis_total"], entry["ratings"][judge2]["analysis_total"]) for entry in by_answer.values() if judge1 in entry["ratings"] and judge2 in entry["ratings"] and entry["ratings"][judge1]["status"] == "valid" and entry["ratings"][judge2]["status"] == "valid"]
        x, y = [pair[0] for pair in pairs], [pair[1] for pair in pairs]
        agreement.append({"judge1": judge1, "judge2": judge2, "common_answer_n": len(pairs), "spearman": spearman(x, y), "mean_absolute_difference": average([abs(a - b) for a, b in pairs])})

    ended_at = datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds")
    source_files = sorted(snapshot.files.values(), key=lambda row: row["path"])
    rules = {"score": "原始 evaluation.总分，0–20 分；沿用 legacy 惩罚和解析，不重评分。", "missing": "43 条 Gemini still_refused 与接口拒评/失败不填 0；无效总分为 null。有效评分为 0 时保留。", "consensus": "每答案必须有三名跨家族裁判的有效总分，取三总分中位数；不足三份不计算中位数。", "main_comparison": "五条件共享完整案件集合：每案全部 25 答案有三有效裁判且原 DeepSeek 分数可用。先每案每条件平均五题，再案件等权平均；原评与互评严格同样本。", "descriptive": "按裁判矩阵及 all-available 统计的观测单位是答案/评分；不作独立样本推断。", "agreement": "每对裁判仅用共同有效评分的答案；Spearman 并列值平均秩，MAD=平均绝对差；描述性，无 p 值。", "human": "本导出不包含已完成的专家评分；不将模型互评标为专家校验。"}
    analysis = {"snapshot_started_at": started_at, "updated_at": ended_at, "population_cases": manifest["population_cases"], "ready_cases": 88, "blocked_cases": manifest["blocked_historical_cases"], "reused_cases": manifest["reused_cross_judged_cases"], "new_cases": manifest["new_case_count"], "answer_n": len(answers), "planned_rating_n": len(task_rows), "status_counts": dict(counts), "status_counts_by_judge": {k: dict(v) for k, v in by_judge.items()}, "status_counts_by_condition": {k: dict(v) for k, v in by_condition.items()}, "answers_with_three_valid_judges": sum(entry["valid_count"] == 3 for entry in by_answer.values()), "complete_case_ids": complete_cases, "excluded_case_ids": [case for case in all_cases if case not in complete_set], "main_comparison": summary, "who_rates_whom": matrix, "judge_agreement": agreement, "statistical_rules": rules, "source_files": source_files, "generator_path": str(Path(__file__).resolve()), "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    analysis["transport_retry_running"] = transport_retry_running
    analysis["new_api_planned_task_n"] = new_api_n
    analysis["new_api_transport_failure_n"] = new_api_transport_failure_n
    analysis["new_api_transport_failure_rate"] = new_api_transport_failure_n / new_api_n
    analysis["all_task_transport_failure_rate"] = counts["transport_failed"] / len(task_rows)
    overlay = {"updated_at": ended_at, "source_snapshot_file": "analysis_snapshot.json", "rule": rules["missing"], "records": task_rows}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    analysis_path = args.output_dir / "analysis_snapshot.json"
    overlay_path = args.output_dir / "rating_status_overlay.json"
    analysis_path.write_text(json.dumps(analysis, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    overlay_path.write_text(json.dumps(overlay, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    valid_answer_n = analysis["answers_with_three_valid_judges"]
    refusal_n = counts["still_refused"] + counts["interface_refusal"]
    error_n = counts["transport_failed"] + counts["unusable"] + counts["missing"]
    lines = ["# 88 案最终评价互评数据", "", f"快照时间：{ended_at}（UTC+08:00）。本文件是当前可用数据导出，不代表 108 案全部完成。", "", "## 1. 覆盖范围与数据状态", "", table(["项目", "数量 / 说明"], [["原计划 / 已具备互评输入", "108 案 / 88 案"], ["当前 88 案来源", "40 案已有互评复用 + 48 案扩展"], ["未纳入的原始历史案件", "20 案：缺少可恢复的互评输入；未补造数据"], ["答案 / 计划互评", "2,200 答案（88 × 5 题 × 5 条件）/ 6,600 份评分（每答案 3 名裁判）"], ["有效评分", f"{counts['valid']:,} / 6,600（{counts['valid'] / 6600 * 100:.2f}%）"], ["拒评 / 接口失败及其他不可用", f"{refusal_n} / {error_n}"], ["三名有效裁判齐全的答案", f"{valid_answer_n:,} / 2,200"], ["五条件共享完整案件", f"{len(complete_cases)} / 88 案（{len(complete_cases) * 25:,} 个答案）"]]), "", "43 条 Gemini 重试结果已记录为“仍拒评”：原始 legacy 解析值保留在审计数据中，分析分数设为 NA，不作为 0 分。真实有效 0 分仍保留。旧的 combined_results/state 中 success 与完成数未作覆盖，不能直接当作本报告的有效评分数。", "", "## 2. 各裁判有效性", "", "百分比以该裁判计划评分数为分母；“接口失败及其他”含网络错误、不可用结果和未取得结果。", "", table(["裁判", "计划", "有效评分", "仍拒评", "接口拒评", "接口失败及其他"], [[SHORT[judge], sum(by_judge[judge].values()), rate(by_judge[judge]["valid"], sum(by_judge[judge].values())), rate(by_judge[judge]["still_refused"], sum(by_judge[judge].values())), rate(by_judge[judge]["interface_refusal"], sum(by_judge[judge].values())), rate(sum(by_judge[judge][s] for s in ["transport_failed", "unusable", "missing"]), sum(by_judge[judge].values()))] for judge in JUDGES]), "", "## 3. 谁评谁：有效总分均值（评分数 n）", "", "分值范围 0–20，沿用原始惩罚规则；行是作答条件，列是裁判。— 表示按设计不分配同家族裁判，不是缺失。各单元格使用各自有效评分，不能直接用裁判整体均值比较宽严。", "", table(["作答条件"] + [SHORT[judge] for judge in JUDGES], [[row["condition"]] + [(f"{fmt(row['judges'][judge]['mean'])}（n={row['judges'][judge]['n']}）" if row["judges"][judge]["assigned"] else "—") for judge in JUDGES] for row in matrix]), "", "## 4. 原 DeepSeek 评分与互评：同案件主比较", "", f"只纳入五条件共同完整的 {len(complete_cases)} 案；每案每条件 5 题均有三份有效互评分。先取每答案三名裁判的总分中位数，再平均同案 5 题，最后按案件等权汇总。原 DeepSeek 评分使用完全相同的案件、问题和答案。差值 = 互评 − 原评；均为 0–20 分。", "", table(["作答条件", "案件 n / 答案 n", "原评均值", "原评排名", "互评中位数均值", "互评排名", "配对差值"], [[row["condition"], f"{row['complete_case_n']} / {row['complete_answer_n']}", fmt(row["original_mean"]), row["original_rank"], fmt(row["consensus_mean"]), row["consensus_rank"], fmt(row["paired_difference_mean"])] for row in summary]), "", "仅描述均值和排序，不把同案五题当作独立案件，也不据此声称显著差异。", "", "### 可用答案的补充描述（各条件样本不完全相同）", "", table(["作答条件", "三裁判齐全答案 n", "本条件完整案件 n", "全部可用三裁判中位数的答案均值"], [[row["condition"], row["all_available_three_valid_answer_n"], row["condition_complete_case_n"], fmt(row["all_available_three_valid_answer_mean"])] for row in summary]), "", "此表按所有可用答案直接平均，分母可能不同、同案权重可能不同；主结论以同案件比较表为准。", "", "## 5. 裁判间一致性（描述性）", "", "每对裁判仅使用双方都有效评分的共同答案。Spearman 衡量排序相关，MAD 为总分的平均绝对差（0–20 分单位）；相关不等于分值一致。不同裁判对的答案构成不同，且同案多题存在相关性，因此不计算独立答案假设下的 p 值。", "", table(["裁判对", "共同答案 n", "Spearman ρ", "MAD"], [[f"{SHORT[row['judge1']]} × {SHORT[row['judge2']]}", row["common_answer_n"], fmt(row["spearman"]), fmt(row["mean_absolute_difference"])] for row in agreement]), "", "## 6. 使用边界与审计文件", "", "- 本次仅导出当前可用的 88 案模型互评；原 20 案缺输入，不得将本表称为 108 案全量结果。", "- 本导出不含已完成的专家评分，不能视作专家验证完成。", "- 拒评和接口失败是缺失数据，不是低质量答案的 0 分；缺失可能非随机，完整案件筛选也可能引入偏倚。", "- 原评分 prompt、模型和 legacy 解析 / 错误惩罚均保留；包括原解析形成的异常低分在内，不在此次导出中擅自修正规则。", "- 原 DeepSeek 评分在部分非 DeepSeek 作答条件中被复用于跨家族互评，故原评与互评并非完全独立；DeepSeek 作答条件的互评不含 DeepSeek 裁判。", f"- [结构化统计与源文件 SHA-256]({analysis_path.resolve()})：包含共享完整案件 ID、案件级均值、统计规则和每份源文件哈希。", f"- [逐评分状态覆盖层]({overlay_path.resolve()})：6,600 条 task_id、raw_total、analysis_total、状态和原因；不覆盖原数据。", "", "## 7. 逐答案互评分明细", "", "每个案件 25 行。数值为原始惩罚后总分；“—”为设计不分配；“仍拒评 / 接口拒评 / 接口失败”等表示 NA。原评为原 DeepSeek 总分；互评为三份有效总分的中位数，不足三份记 NA。仅含脱敏案件 ID，不重复输出原始案文或个人姓名。", ""]
    # Keep the human-facing document concise; the JSON overlay contains every score.
    lines = lines[:lines.index("## 7. 逐答案互评分明细")]
    progress_note = "网络故障补跑仍在运行；以下为读取期间的当前快照，完成后需重新生成。" if transport_retry_running else "本快照未检测到网络故障补跑进程。" if transport_retry_running is False else "未能确认网络故障补跑进程状态。"
    lines[4:4] = [progress_note, ""]
    note = f"拒评 {refusal_n} 条已作为终态记录（分析为 NA），与可重试网络故障分开。当前最终任务状态的网络失败率：{new_api_transport_failure_n}/{new_api_n} = {new_api_transport_failure_n / new_api_n * 100:.4f}%（新增 API 任务为分母）；{counts['transport_failed']}/6600 = {counts['transport_failed'] / 6600 * 100:.4f}%（全部计划评分为分母）。这里不是物理 HTTP 请求失败率，也不将历次失败重试重复计数。"
    position = lines.index("## 2. 各裁判有效性")
    lines[position:position] = [note, ""]
    report_path = args.output_dir / "88案_最终评价互评数据.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"report": str(report_path.resolve()), "analysis": str(analysis_path.resolve()), "overlay": str(overlay_path.resolve()), "status_counts": dict(counts), "complete_cases": len(complete_cases), "three_valid_answers": valid_answer_n, "main_means": [{k: v for k, v in row.items() if k != "case_means"} for row in summary]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
