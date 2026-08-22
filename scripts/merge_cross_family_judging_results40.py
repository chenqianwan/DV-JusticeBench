#!/usr/bin/env python3
"""Merge the two disjoint 20-case cross-judge blocks into a 40-case dataset."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Dict


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIRST = ROOT / "data/experiment1_cross_judge_sample20_xhub_20260822/combined_results.json"
DEFAULT_SECOND = ROOT / "data/experiment1_cross_judge_expansion20_xhub_20260822/combined_results.json"
DEFAULT_OUTPUT = ROOT / "data/experiment1_cross_judge_sample40_xhub_20260822/combined_results.json"


def load_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def add_usage(left: Dict[str, Any], right: Dict[str, Any]) -> Dict[str, int]:
    return {
        key: int(left.get(key) or 0) + int(right.get(key) or 0)
        for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens")
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--first", type=Path, default=DEFAULT_FIRST)
    parser.add_argument("--second", type=Path, default=DEFAULT_SECOND)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    first = load_json(args.first)
    second = load_json(args.second)
    for label, data in (("first", first), ("second", second)):
        if not data.get("completion", {}).get("complete"):
            raise SystemExit(f"{label} block is incomplete")
        if len(data.get("answers") or []) != 500:
            raise SystemExit(f"{label} block does not contain 500 answers")

    first_cases = {item["case_id"] for item in first["answers"]}
    second_cases = {item["case_id"] for item in second["answers"]}
    if len(first_cases) != 20 or len(second_cases) != 20 or first_cases & second_cases:
        raise SystemExit("blocks are not two disjoint 20-case sets")

    answers = first["answers"] + second["answers"]
    answer_ids = [item["answer_id"] for item in answers]
    if len(answers) != 1000 or len(set(answer_ids)) != 1000:
        raise SystemExit("merged answers are not 1,000 unique units")

    rating_files = {**first["rating_files"], **second["rating_files"]}
    if len(rating_files) != 3000:
        raise SystemExit("merged rating files are not 3,000 unique units")
    if first["judge_matrix"] != second["judge_matrix"]:
        raise SystemExit("judge matrices differ")

    usage_by_judge = {}
    for judge in first["usage_by_judge"]:
        a = first["usage_by_judge"][judge]
        b = second["usage_by_judge"][judge]
        usage_by_judge[judge] = {
            "new_api_ratings": int(a["new_api_ratings"]) + int(b["new_api_ratings"]),
            "usage": add_usage(a["usage"], b["usage"]),
            "estimated_xhub_list_cost_usd": round(
                float(a["estimated_xhub_list_cost_usd"]) + float(b["estimated_xhub_list_cost_usd"]), 6
            ),
        }

    total_cost = float(first["estimated_xhub_list_cost_usd"]) + float(second["estimated_xhub_list_cost_usd"])
    merged = {
        "metadata": {
            "updated_at": second["metadata"]["updated_at"],
            "endpoint_host": second["metadata"]["endpoint_host"],
            "source_paths": [str(args.first.resolve()), str(args.second.resolve())],
            "sample": "two_disjoint_stratified_20_case_blocks",
            "case_count": 40,
            "question_count": 200,
            "answer_count": 1000,
            "rating_count_expected": 3000,
            "new_api_rating_count_expected": 2400,
            "reused_deepseek_rating_count_expected": 600,
            "same_family_ratings_excluded": True,
            "temperature": 0.3,
            "max_tokens": 3000,
            "flag_parser_version": "legacy",
            "consensus_method": "three_cross_family_judges_median_after_legacy_penalty",
            "credential_serialized": False,
            "case_blocks": {
                "existing20": sorted(first_cases),
                "expansion20": sorted(second_cases),
            },
        },
        "completion": {
            "ratings_completed": 3000,
            "answers_with_three_ratings": 1000,
            "complete": True,
        },
        "judge_matrix": first["judge_matrix"],
        "usage_by_judge": usage_by_judge,
        "estimated_xhub_list_cost_usd": round(total_cost, 6),
        "estimated_xhub_list_cost_cny_at_7_3": round(total_cost * 7.3, 4),
        "answers": answers,
        "rating_files": rating_files,
        "task_count": 3000,
    }
    save_json(args.output, merged)
    print(args.output.resolve())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
