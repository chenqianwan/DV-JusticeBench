#!/usr/bin/env python3
"""Convert PKULaw bulk-downloaded plain-text judgments into pipeline inputs."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


DOC_ID_RE = re.compile(r"\((FBMCLI\.[A-Z]\.[0-9]+)\)", re.IGNORECASE)
CASE_NO_RE = re.compile(r"[（(](\d{4})[）)][^\n，。；;]{1,80}?号")
DATE_RE = re.compile(r"(20\d{2})[年.-](\d{1,2})[月.-](\d{1,2})日?")
REASONING_PATTERNS = (
    re.compile(r"本院经审查认为[，,：:]"),
    re.compile(r"本院经审理认为[，,：:]"),
    re.compile(r"本院审理认为[，,：:]"),
    re.compile(r"本院认为[，,：:]"),
    re.compile(r"^本院意见(?:本院认为)?", re.M),
    re.compile(r"^法院认为(?:[：:]|[\u3000 \t])", re.M),
)
DECISION_PATTERNS = (
    re.compile(r"判决如下[：:]"),
    re.compile(r"裁定如下[：:]"),
    re.compile(r"决定如下[：:]"),
    re.compile(r"^裁判结果(?:[：:]|[\u3000 \t])", re.M),
    re.compile(r"^判决主文(?:[：:]|[\u3000 \t])", re.M),
    re.compile(r"^裁定主文(?:[：:]|[\u3000 \t])", re.M),
)
JUDGMENT_END_RE = re.compile(
    r"^\s*(?:落款\s*$|审\s*判\s*长|审\s*判\s*员|书\s*记\s*员|"
    r"法官助理|法条链接|附法律依据)",
    re.M,
)


def normalize_text(raw: str) -> str:
    text = raw.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    lines = [line.rstrip() for line in text.splitlines()]
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()
    return "\n".join(lines)


def dedupe_doubled_title(title: str) -> str:
    title = title.strip()
    if len(title) % 2 == 0 and title[: len(title) // 2] == title[len(title) // 2 :]:
        return title[: len(title) // 2]
    return title


def chinese_date(text: str) -> str:
    matches = list(DATE_RE.finditer(text))
    if matches:
        year, month, day = matches[-1].groups()
        return f"{year}-{int(month):02d}-{int(day):02d}"

    digit_map = str.maketrans("〇零一二三四五六七八九", "00123456789")
    match = re.search(r"([二〇零一二三四五六七八九]{4})年([一二三四五六七八九十]{1,3})月([一二三四五六七八九十]{1,3})日", text)
    if not match:
        return ""

    def cn_number(value: str) -> int:
        if value == "十":
            return 10
        if "十" in value:
            left, right = value.split("十", 1)
            return (int(left.translate(digit_map)) if left else 1) * 10 + (int(right.translate(digit_map)) if right else 0)
        return int(value.translate(digit_map))

    year = int(match.group(1).translate(digit_map))
    return f"{year:04d}-{cn_number(match.group(2)):02d}-{cn_number(match.group(3)):02d}"


def split_case_and_judgment(content: str) -> tuple[str, str]:
    """Split facts from judicial reasoning/result using the legacy pipeline boundary."""
    starts: list[int] = []
    for pattern in (*REASONING_PATTERNS, *DECISION_PATTERNS):
        match = pattern.search(content)
        if match:
            starts.append(match.start())

    if not starts:
        return content, ""

    judgment_start = min(starts)
    case_text = content[:judgment_start].strip()
    judgment_tail = content[judgment_start:]
    end_match = JUDGMENT_END_RE.search(judgment_tail)
    judge_decision = (
        judgment_tail[: end_match.start()].strip()
        if end_match
        else judgment_tail.strip()
    )
    return case_text, judge_decision


def parse_case(path: Path, source_root: Path) -> dict[str, Any]:
    content = normalize_text(path.read_text(encoding="utf-8", errors="replace"))
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    relative_source = path.relative_to(source_root)
    filename_match = DOC_ID_RE.search(path.name)
    doc_id = filename_match.group(1).upper() if filename_match else path.stem
    case_id = "pkulaw_" + re.sub(r"[^A-Za-z0-9]+", "_", doc_id).strip("_").lower()

    title = dedupe_doubled_title(lines[0]) if lines else path.stem
    court = next((line for line in lines[1:5] if line.endswith("人民法院")), "")
    case_no_match = CASE_NO_RE.search(content[:1500])
    case_no = case_no_match.group(0) if case_no_match else ""
    case_number_year = int(case_no_match.group(1)) if case_no_match else None
    decision_year = (
        int(relative_source.parts[0])
        if relative_source.parts and relative_source.parts[0].isdigit()
        else None
    )
    case_type = "刑事案件" if "刑事判决书" in content[:1000] else "民事案件" if "民事判决书" in content[:1000] else ""
    if not case_type:
        case_type = "刑事案件" if "刑" in case_no or "刑事" in title else "民事案件"
    case_text, judge_decision = split_case_and_judgment(content)
    case_date = chinese_date(content[-1000:])

    return {
        "id": case_id,
        "case_id": case_id,
        "record_id": doc_id,
        "doc_id": doc_id,
        "source_database": "北大法宝",
        "title": title,
        # `run_experiment1_smoke_xhub.py` prefers `content` over `case_text`, so
        # both must contain facts only; otherwise the reference judgment leaks
        # into the answering prompt.
        "content": case_text,
        "case_text": case_text,
        "judge_decision": judge_decision,
        "case_date": case_date,
        "metadata": {
            "source_database": "北大法宝",
            "record_id": doc_id,
            "case_no": case_no,
            "court": court,
            "year": decision_year,
            "decision_year": decision_year,
            "case_number_year": case_number_year,
            "case_type": case_type,
            "document_type": "判决书",
            "search_keyword": "家庭暴力",
            "source_file": str(relative_source),
            "full_text_chars": len(content),
            "case_text_chars": len(case_text),
            "judge_decision_chars": len(judge_decision),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    input_dir = args.input_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    records: dict[str, dict[str, Any]] = {}
    duplicate_ids: list[str] = []
    errors: list[dict[str, str]] = []
    for path in sorted(input_dir.rglob("*.txt")):
        try:
            record = parse_case(path, input_dir)
            if record["id"] in records:
                duplicate_ids.append(record["id"])
                continue
            records[record["id"]] = record
        except Exception as exc:  # keep the import recoverable for later batches
            errors.append({"file": str(path), "error": str(exc)})

    jsonl_path = output_dir / "pkulaw_cases.jsonl"
    cases_path = output_dir / "cases.json"
    runnable_jsonl_path = output_dir / "pipeline_ready_cases.jsonl"
    runnable_cases_path = output_dir / "pipeline_ready_cases.json"
    report_path = output_dir / "conversion_report.json"
    with jsonl_path.open("w", encoding="utf-8") as handle:
        for record in records.values():
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    cases_path.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    runnable_records = {
        record_id: record
        for record_id, record in records.items()
        if record.get("case_text", "").strip()
        and record.get("judge_decision", "").strip()
    }
    with runnable_jsonl_path.open("w", encoding="utf-8") as handle:
        for record in runnable_records.values():
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    runnable_cases_path.write_text(
        json.dumps(runnable_records, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    missing_judgment_ids = [
        record_id
        for record_id, record in records.items()
        if not record.get("judge_decision", "").strip()
    ]
    report_path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "input_dir": str(input_dir),
                "case_count": len(records),
                "pipeline_ready_case_count": len(runnable_records),
                "missing_judge_decision_count": len(missing_judgment_ids),
                "missing_judge_decision_ids": missing_judgment_ids,
                "duplicate_count": len(duplicate_ids),
                "duplicate_ids": duplicate_ids,
                "error_count": len(errors),
                "errors": errors,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "case_count": len(records),
                "pipeline_ready_case_count": len(runnable_records),
                "missing_judge_decision_count": len(missing_judgment_ids),
                "duplicate_count": len(duplicate_ids),
                "error_count": len(errors),
            },
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()
