#!/usr/bin/env python3
"""Read the original 20-case workbook without altering or regenerating inputs.

The legacy workbook retains answers and scores but does not retain masked
facts/reference judgments. Missing input fields stay null and are NOT runnable.
This importer never makes API calls and never substitutes unmasked case text.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

import openpyxl


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WORKBOOK = ROOT / "data/results_20260112_unified_e8fd22b9/20个案例_统一评估结果_108cases.xlsx"
DEFAULT_OUTPUT = ROOT / "data/experiment1_cross_judge_full108_xhub_20260908/original20_answers.json"
CONDITIONS = {
    "GPT-4o": ("GPT-4o", "GPT"),
    "Gemini 2.5 Flash": ("Gemini 2.5 Flash", "Gemini"),
    "Qwen-Max": ("Qwen-Max", "Qwen"),
    "DeepSeek": ("DeepSeek thinking", "DeepSeek"),
    "DeepSeek-NoThinking": ("DeepSeek non-thinking", "DeepSeek"),
}
DIMENSIONS = [
    "规范依据相关性", "涵摄链条对齐度", "价值衡量与同理心对齐度",
    "关键事实与争点覆盖度", "裁判结论与救济配置一致性",
]


def sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def import_answers(workbook: Path) -> dict:
    wb = openpyxl.load_workbook(workbook, read_only=True, data_only=True)
    answers = []
    for sheet_name, (condition, family) in CONDITIONS.items():
        rows = iter(wb[sheet_name].values)
        headers = next(rows)
        for excel_row, values in enumerate(rows, start=2):
            row = dict(zip(headers, values))
            case_id = row["案例ID"]
            qn = int(row["问题编号"])
            question, answer = row["问题"], row["AI回答"]
            if not all(isinstance(t, str) and t.strip() for t in (case_id, question, answer)):
                raise ValueError(f"Missing source answer/question: {sheet_name}!{excel_row}")
            evaluation = {
                "总分": row["总分"],
                "百分制": row["百分制"],
                "分档": row["分档"],
                "各维度得分": {d: row[f"{d}_得分"] for d in DIMENSIONS},
                "详细评价": row["详细评价"],
                "评价Thinking": row["评价Thinking"],
                "错误标记": row["错误标记"],
                "错误详情": {
                    severity: [row[severity]] if row[severity] else []
                    for severity in ("微小错误", "明显错误", "重大错误")
                },
            }
            answers.append({
                "answer_id": f"{case_id}::q{qn}::{condition}",
                "case_id": case_id,
                "case_title": row["案例标题"],
                "masked_title": row["案例标题（脱敏）"],
                "question_number": qn,
                "question": question,
                "answer_condition": condition,
                "answer_family": family,
                "answer_operational_model_id": None,
                "answer": answer,
                "masked_case_text": None,
                "masked_judgment": None,
                "input_hash": None,
                "question_sha256": sha(question),
                "answer_sha256": sha(answer),
                "masked_case_sha256": None,
                "masked_judgment_sha256": None,
                "source_raw_path": str(workbook.resolve()),
                "source_sheet": sheet_name,
                "source_excel_row": excel_row,
                "source_inputs_missing": ["masked_case_text", "masked_judgment"],
                "ready_for_cross_judging": False,
                "source_processing_error": row["处理错误"],
                "source_answer_is_processing_error": bool(row["处理错误"]),
                "original_deepseek_evaluation": evaluation,
                "original_deepseek_total": evaluation["总分"],
                "original_deepseek_missing_score_fields": [
                    d for d in DIMENSIONS if evaluation["各维度得分"][d] is None
                ],
                "original_deepseek_model_provenance": {
                    "workbook_evaluation_api_label": row["评估API"],
                    "requested_model_id": None,
                    "response_model_id": None,
                    "note": "Workbook stores provider label only; no API receipt or pinned model version.",
                },
                "source_row": {
                    "condition": condition,
                    "question_number": qn,
                    "question": question,
                    "generation": {"content": answer, "reasoning_content": row["AI回答Thinking"]},
                    "evaluation": evaluation,
                    "scoring": {
                        "ok": None,
                        "requested_model": None,
                        "response_model": None,
                        "historical_provider_label": row["评估API"],
                        "receipt_available": False,
                    },
                    "historical_workbook_row": row,
                },
            })
    wb.close()
    counts = Counter(a["case_id"] for a in answers)
    assert len(answers) == 500 and len(counts) == 20
    assert set(counts.values()) == {25}
    assert len({a["answer_id"] for a in answers}) == 500
    assert {a["question_number"] for a in answers} == {1, 2, 3, 4, 5}
    answers.sort(key=lambda a: (a["case_id"], a["question_number"], a["answer_condition"]))
    return {
        "metadata": {
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_workbook": str(workbook.resolve()),
            "source_workbook_sha256": hashlib.sha256(workbook.read_bytes()).hexdigest(),
            "case_count": 20,
            "answer_count": 500,
            "ready_for_cross_judging_count": 0,
            "answers_missing_historical_dimension_scores": sum(
                bool(a["original_deepseek_missing_score_fields"]) for a in answers
            ),
            "source_processing_error_count": sum(
                a["source_answer_is_processing_error"] for a in answers
            ),
            "source_inputs_missing": ["masked_case_text", "masked_judgment"],
            "notes": [
                "Original questions, answers, final scores and detailed evaluations copied unchanged from cells.",
                "Workbook error-detail cells retained whole, not reparsed or rescored.",
                "Actual operational model IDs and historical masked inputs are not saved in this workbook.",
                "Raw facts/reference judgments and later smoke-generated inputs are not substituted.",
            ],
        },
        "case_ids": sorted(counts),
        "answers": answers,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workbook", type=Path, default=DEFAULT_WORKBOOK)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    payload = import_answers(args.workbook)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    tmp = args.output.with_suffix(args.output.suffix + f".{os.getpid()}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(args.output)
    print(json.dumps({"output": str(args.output), **payload["metadata"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
