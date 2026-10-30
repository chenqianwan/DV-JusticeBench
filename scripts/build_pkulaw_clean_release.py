#!/usr/bin/env python3
"""Offline, non-destructive release of the audited engineer delivery.

Read a pinned Git commit and the original 4,193-case source. No model calls,
prompt edits, regeneration, legal relabeling, or modification of input files.
"""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile


COMMIT = "06b533d5d695348623c018f95ef5f4809f7ae7ea"
SOURCE_PATH = "data/pkulaw_2023_2025_release/pipeline_ready_cases.json"
SOURCE_SHA256 = "0f10f83a81ea9eddb1b6fa0c46375a4cf8b433718c6a942220921c1050acd95a"
BASE = "data/pkulaw_filter/"
SPLITS = {"primary": "prepare_output", "secondary": "prepare_output_secondary"}
TRUNCATED = {
    "504219691": 8133, "549869757": 9515, "560035279": 13301,
    "560262784": 10759, "563733580": 12101, "563647694": 13947,
}
CONFIRMED = {
    "503672936": ("empty_masked_judgment", "原裁判理由4379字，脱敏裁判理由为空。"),
    "503959989": ("lost_judicial_reasoning", "原裁判理由860字，脱敏后205字；实质说理段丢失，仅余先前一审结果。"),
    "505355373": ("confirmed_question_fact_error", "问题颠倒家暴指控双方，并预设未提出的损害赔偿请求。"),
    "504103311": ("confirmed_question_fact_error", "第5题预设原告已提出损害赔偿请求，原文诉请并无该项。"),
    "550408813": ("confirmed_question_fact_error", "已离婚后的彩礼返还案，第4题却询问是否准予离婚。"),
}
REASONS = {
    "stage1_keyword_excluded": "关键词初筛排除（未做三分类）",
    "stage2_irrelevant": "原分类为 irrelevant",
    "prepare_failed": "准备阶段失败，缺少完整交付",
    "secondary_not_delivered": "secondary 未交付完整材料（原因未明）",
    "source_facts_truncated": "案情输入被截断至8000字",
    "empty_masked_judgment": "脱敏裁判理由为空",
    "lost_judicial_reasoning": "脱敏丢失实质裁判说理",
    "confirmed_question_fact_error": "已确认问题与案件事实不一致",
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def json_bytes(obj):
    return (json.dumps(obj, ensure_ascii=False, indent=2) + "\n").encode()


def jsonl_bytes(rows):
    return b"".join((json.dumps(r, ensure_ascii=False) + "\n").encode() for r in rows)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", default="data/pkulaw_clean_release_20260925")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    out = Path(args.output)
    if not out.is_absolute():
        out = root / out
    # Refuse overwrite; original and previously generated data stay untouched.
    require(not out.exists(), f"Output already exists: {out}")
    inputs = {}

    def git_blob(path):
        data = subprocess.check_output(["git", "show", f"{COMMIT}:{path}"], cwd=root)
        inputs[path] = {"sha256": sha(data), "bytes": len(data)}
        return data

    def rows_from(data):
        raw = data.splitlines(keepends=True)
        require(all(line.strip() for line in raw), "Unexpected blank JSONL row")
        rows = [json.loads(line) for line in raw]
        ids = [row["case_id"] for row in rows]
        require(len(ids) == len(set(ids)), "Duplicate case_id")
        return dict(zip(ids, rows)), dict(zip(ids, raw))

    source_bytes = (root / SOURCE_PATH).read_bytes()
    require(sha(source_bytes) == SOURCE_SHA256, "Original source hash changed")
    source = json.loads(source_bytes)
    require(len(source) == 4193, "Expected 4,193 source cases")
    inputs[SOURCE_PATH] = {"sha256": SOURCE_SHA256, "bytes": len(source_bytes)}
    stage1_list = json.loads(git_blob(BASE + "stage1_kept_case_ids.json"))
    stage1 = set(stage1_list)
    require(len(stage1) == len(stage1_list) == 2594, "Stage1 IDs mismatch")
    classified, _ = rows_from(git_blob(BASE + "stage2_classified.jsonl"))
    failures, _ = rows_from(git_blob(BASE + "prepare_output/failures_fix4.jsonl"))
    secondary_failures, _ = rows_from(git_blob(BASE + "prepare_output_secondary/failures_fix4.jsonl"))
    require(not secondary_failures, "Unexpected secondary failure records")
    require(set(classified) == stage1 <= set(source), "Screening/source IDs mismatch")
    require(len(failures) == 5, "Expected five preparation failures")
    require({cid for cid, s in source.items() if any(
        word in s.get(field, "") for word in ("家庭暴力", "家暴")
        for field in ("title", "case_text", "judge_decision")
    )} == stage1, "Stage1 keyword result changed")

    masks, questions, raw_masks, raw_questions = {}, {}, {}, {}
    delivered_split, all_qids = {}, set()
    for split, folder in SPLITS.items():
        masks[split], raw_masks[split] = rows_from(git_blob(BASE + folder + "/masked_cases_fix4.jsonl"))
        questions[split], raw_questions[split] = rows_from(git_blob(BASE + folder + "/questions_fix4.jsonl"))
        require(set(masks[split]) == set(questions[split]), f"Unpaired {split} records")
        require(len(masks[split]) == {"primary": 226, "secondary": 2022}[split], "Delivery count changed")
        for cid, mask in masks[split].items():
            require(cid in classified and cid not in delivered_split, "Unexpected or duplicate delivered case")
            delivered_split[cid] = split
            s = source[cid]
            for field in ("title", "case_text", "judge_decision"):
                value = s[field]
                if field == "case_text" and mask.get("case_text_truncated_from") is not None:
                    value = value[:8000]
                require(sha(value.encode()) == mask["source_" + field + "_hash"], f"Source hash mismatch: {cid}/{field}")
            qq = questions[split][cid]["questions"]
            require(len(qq) == 5, f"Expected five questions: {cid}")
            require({q["question_id"] for q in qq} == {f"{cid}_q{i}" for i in range(1, 6)}, f"Bad question IDs: {cid}")
            for q in qq:
                require(isinstance(q["question"], str) and q["question"].strip(), f"Empty question: {cid}")
                require(q["question_id"] not in all_qids, "Duplicate question ID")
                all_qids.add(q["question_id"])

    prefix = "pkulaw_fbmcli_c_"
    dirty = {prefix + cid: ("source_facts_truncated", f"原案情{length}字，模型只接收前8000字。")
             for cid, length in TRUNCATED.items()}
    dirty.update({prefix + cid: value for cid, value in CONFIRMED.items()})
    detected_trunc = {cid for split in SPLITS for cid, m in masks[split].items()
                      if m.get("case_text_truncated_from") is not None}
    require(detected_trunc == {prefix + cid for cid in TRUNCATED}, "Truncation set changed")
    require(set(dirty) <= set(delivered_split) and len(dirty) == 11, "Confirmed dirty cases missing")
    require(set(failures).isdisjoint(delivered_split), "Failed case also delivered")

    kept, excluded = [], []
    for cid in source:
        cls = classified.get(cid)
        row = {"case_id": cid, "original_label": cls["label"] if cls else None,
               "original_court_addressed_dv": cls["court_addressed_dv"] if cls else None,
               "original_delivery_split": delivered_split.get(cid)}
        reason, detail = None, None
        if cid not in stage1:
            reason = "stage1_keyword_excluded"
        elif cls["label"] == "irrelevant":
            reason = "stage2_irrelevant"
            require(cid not in delivered_split and cid not in failures, "Irrelevant case prepared")
        elif cid in failures:
            reason = "prepare_failed"
            row["preparation_failure"] = failures[cid]
        elif cid in dirty:
            reason, detail = dirty[cid]
        elif cid not in delivered_split:
            require(cls["label"] == "secondary" and cls["court_addressed_dv"] is False, "Unexplained main-group omission")
            reason = "secondary_not_delivered"
        if reason:
            row.update(status="excluded", exclusion_reason=reason,
                       exclusion_detail=detail or REASONS[reason], original_classification=cls)
            excluded.append(row)
        else:
            split = delivered_split[cid]
            mask = masks[split][cid]
            require(not mask.get("case_text_truncated_from"), "Retained truncated input")
            require(all(isinstance(mask.get(k), str) and mask[k].strip()
                        for k in ("title_masked", "case_text_masked", "judge_decision_masked")), "Retained empty field")
            row.update(status="retained", delivery_split=split,
                       question_ids=[q["question_id"] for q in questions[split][cid]["questions"]],
                       original_classification=cls)
            kept.append(row)
    kept_ids, excluded_ids = {r["case_id"] for r in kept}, {r["case_id"] for r in excluded}
    require(not kept_ids & excluded_ids and kept_ids | excluded_ids == set(source), "Reconciliation failed")
    counts = Counter(r["delivery_split"] for r in kept)
    reason_counts = Counter(r["exclusion_reason"] for r in excluded)
    require(counts == {"primary": 217, "secondary": 2020}, "Unexpected retained counts")
    expected_reasons = dict(zip(REASONS, (1599, 193, 5, 148, 6, 1, 1, 3)))
    require(reason_counts == expected_reasons, "Unexpected exclusion counts")
    composition = {split: dict(Counter(r["original_label"] for r in kept if r["delivery_split"] == split))
                   for split in SPLITS}
    require(composition == {"primary": {"primary": 194, "secondary": 23}, "secondary": {"secondary": 2020}}, "Label composition changed")

    # Build all artifacts in memory after validation; retained JSONL lines stay byte-identical.
    files = {}
    for split in SPLITS:
        selected = {r["case_id"] for r in kept if r["delivery_split"] == split}
        for name, raw in (("masked_cases_fix4.jsonl", raw_masks[split]), ("questions_fix4.jsonl", raw_questions[split])):
            data = b"".join(line for cid, line in raw.items() if cid in selected)
            check, check_raw = rows_from(data)
            require(set(check) == selected, "Export pairing changed")
            require(all(check_raw[cid] == raw[cid] for cid in selected), "Retained row bytes changed")
            files[f"{split}/{name}"] = data
    files["case_manifest.jsonl"] = jsonl_bytes(kept)
    files["excluded/manifest.jsonl"] = jsonl_bytes(excluded)
    files["excluded/source_cases.json"] = json_bytes({cid: s for cid, s in source.items() if cid in excluded_ids})
    for name, raw_group in (("masked_cases_fix4.jsonl", raw_masks), ("questions_fix4.jsonl", raw_questions)):
        files[f"excluded/{name}"] = b"".join(line for split in SPLITS for cid, line in raw_group[split].items() if cid in excluded_ids)
    files["excluded/README.md"] = ("# 统一排除集\n\n"
        "共1,956案。包含无关案件、初筛排除、缺少交付及已确认数据质量错误，不代表这些案件全是法律上 irrelevant。\n\n"
        "`manifest.jsonl` 保存互斥排除原因与原标签；`source_cases.json` 保存原始记录。两个 fix4 JSONL 保存本次排除的11案已有交付，禁止用于当前作答输入。原始资料未删除。\n").encode()
    files["README.md"] = ("# 北大法宝清理后可用交付（2026-09-25）\n\n"
        "仅做离线筛除，不改文本、问题、Prompt、模型或原始标签，不重新生成，不启动作答或评分。\n\n"
        "| 交付分组 | 原交付 | 本轮剔除 | 保留案件 | 问题数 |\n"
        "|---|---:|---:|---:|---:|\n"
        "| primary（工程师主分析集口径） | 226 | 9 | 217 | 1,085 |\n"
        "| secondary | 2,022 | 2 | 2,020 | 10,100 |\n"
        "| 合计 | 2,248 | 11 | 2,237 | 11,185 |\n\n"
        "## 数据文件\n\n"
        "- `primary/` 与 `secondary/` 各含 `masked_cases_fix4.jsonl`、`questions_fix4.jsonl`，按 case_id 关联，每案5题。保留行与原交付逐字节一致。\n"
        "- `case_manifest.jsonl` 保存每案原分类、交付分组和题号。\n"
        "- `manifest.json` 保存源版本、清理规则、计数、文件哈希与核验结果。\n"
        "- 本地 `excluded/` 统一管理排除案；干净数据 ZIP 不含该目录。\n\n"
        "## 分组口径（不可当作重新标注）\n\n"
        "沿用工程师既有分组。primary 主分析集含194个原标签 primary、23个原标签 secondary 且 court_addressed_dv=true；secondary 组为2,020个原标签 secondary。若按原始标签汇总，保留数据是 primary 194、secondary 2,043，合计不变。\n\n"
        "## 全集核对\n\n"
        "4,193 = 保留2,237 + 排除1,956；集合互斥且完整覆盖。\n\n"
        "| 排除原因 | 案件数 |\n|---|---:|\n"
        + "".join(f"| {REASONS[k]} | {reason_counts[k]:,} |\n" for k in REASONS)
        + "\n## 清理边界\n\n"
        "通过全量JSON解析、ID唯一性与配对、每案5道非空题、源字段哈希及输出行字节一致性检查，并剔除已确认的11案问题。没有对全部11,185题重新进行专家语义复核，不能据此宣称剩余数据完全无事实错误。旧正则扫描的疑似角色漂移存在误报，未直接作为剔除依据。\n\n"
        "本包承接工程师 fix4 版本；清理不消除前次审核已指出的准备阶段与108案历史流水线的差异。不可仅凭本包宣称严格复刻旧流水线。作答/评分保持 paused_before_answering，等待明确启动指令。作答输入不得包含裁判理由；裁判理由仅供后续评分参考。\n\n"
        f"固定源版本：`{COMMIT}`。重建：在项目根目录执行 `python3 scripts/build_pkulaw_clean_release.py --output <新的不存在的输出目录>`，无需网络或API。\n").encode()
    manifest = {
        "release": "pkulaw_clean_release_20260925", "source_commit": COMMIT,
        "state": "paused_before_answering", "api_calls": 0,
        "source_cases": 4193, "delivered_cases": 2248, "newly_excluded_delivered_cases": 11,
        "retained_cases": len(kept), "retained_questions": len(kept) * 5,
        "retained_by_delivery_split": dict(counts), "original_labels_by_delivery_split": composition,
        "excluded_cases": len(excluded), "excluded_by_reason": dict(reason_counts),
        "inputs": inputs,
        "confirmed_exclusions": {cid: {"reason": reason, "detail": detail} for cid, (reason, detail) in dirty.items()},
        "rules": ["Preserve engineer delivery groups and original legal labels separately.",
                  "Exclude known truncations, two confirmed defective judgments, three confirmed factual question errors.",
                  "Exclude stage1 rejects, stage2 irrelevant cases and unavailable preparations.",
                  "No reinterpretation of heuristic warnings; no full expert semantic clearance."],
        "checks": {"full_source_reconciliation": True, "disjoint_kept_excluded": True,
                   "json_and_unique_ids": True, "paired_masked_questions": True,
                   "five_nonempty_questions_per_case": True, "source_hashes": True,
                   "retained_rows_byte_identical": True, "full_expert_semantic_review": False},
        "files": {p: {"bytes": len(b), "sha256": sha(b)} for p, b in sorted(files.items())},
        "bundle_excludes": ["excluded/"],
    }
    files["manifest.json"] = json_bytes(manifest)
    out.mkdir(parents=True, exist_ok=False)
    for path, data in files.items():
        target = out / path
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as f:
            f.write(data)
        require(sha(target.read_bytes()) == sha(data), f"Write verification failed: {path}")
    bundle = out / "pkulaw_clean_primary217_secondary2020.zip"
    with zipfile.ZipFile(bundle, "x", compression=zipfile.ZIP_DEFLATED) as z:
        for path, data in sorted(files.items()):
            if not path.startswith("excluded/"):
                info = zipfile.ZipInfo(path, date_time=(2026, 9, 25, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                z.writestr(info, data)
    with zipfile.ZipFile(bundle) as z:
        require(z.testzip() is None, "ZIP CRC failed")
        require(set(z.namelist()) == {p for p in files if not p.startswith("excluded/")}, "ZIP whitelist failed")
        require(all(z.read(p) == files[p] for p in z.namelist()), "ZIP content mismatch")
    require(sha((root / SOURCE_PATH).read_bytes()) == SOURCE_SHA256, "Original source mutated")
    print(json.dumps({"output": str(out), "clean_bundle": str(bundle),
                      "retained": dict(counts), "questions": 11185, "excluded": 1956,
                      "bundle_sha256": sha(bundle.read_bytes())}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
