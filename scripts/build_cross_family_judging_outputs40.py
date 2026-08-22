#!/usr/bin/env python3
"""Build the 40-case report and append-only 250-item expert review sample."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import build_cross_family_judging_outputs as base


DEFAULT_INPUT = ROOT / "data/experiment1_cross_judge_sample40_xhub_20260822/combined_results.json"
DEFAULT_PREVIOUS_SAMPLE = ROOT / "outputs/20260822_cross_family_judging_sample20/专家复核样本_125份.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs/20260822_cross_family_judging_sample40"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--previous-sample", type=Path, default=DEFAULT_PREVIOUS_SAMPLE)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    combined = load_json(args.input)
    if not combined.get("completion", {}).get("complete"):
        raise SystemExit("40-case cross-family ratings are incomplete")
    if len(combined.get("answers") or []) != 1000:
        raise SystemExit("40-case input must contain 1,000 answers")

    previous = load_json(args.previous_sample)
    if len(previous) != 125:
        raise SystemExit("previous expert sample must contain 125 items")
    previous_cases = {item["case_id"] for item in previous}
    if len(previous_cases) != 20:
        raise SystemExit("previous expert sample does not cover exactly 20 cases")

    new_answers = [item for item in combined["answers"] if item["case_id"] not in previous_cases]
    new_cases = {item["case_id"] for item in new_answers}
    if len(new_answers) != 500 or len(new_cases) != 20:
        raise SystemExit("could not isolate a disjoint second 20-case block")
    second_block = {
        **combined,
        "answers": new_answers,
    }
    new_sample = base.build_expert_sample(second_block)
    for offset, item in enumerate(new_sample, 126):
        item["sequence"] = offset
        item["blind_id"] = f"HR{offset:03d}"
    sample = previous + new_sample

    keys = [(item["case_id"], item["question_number"], item["answer_condition"]) for item in sample]
    if len(sample) != 250 or len(set(keys)) != 250:
        raise SystemExit("expert review sample must contain 250 unique answer units")
    if Counter(item["answer_condition"] for item in sample) != Counter({condition: 50 for condition in base.CONDITIONS}):
        raise SystemExit("expert review sample must contain 50 items per answer condition")
    if Counter(item["review_stratum"] for item in sample) != Counter({"基础平衡样本": 200, "高分歧加抽样": 50}):
        raise SystemExit("expert review strata must be 200 base plus 50 disagreement items")
    # Append-only guarantee: the first 125 rows are byte-equivalent objects.
    if sample[:125] != previous:
        raise SystemExit("previous expert review sample was altered")

    ratings = base.rating_rows(combined)
    if len(ratings) != 3000:
        raise SystemExit(f"expected 3,000 ratings, found {len(ratings)}")
    markdown = base.build_markdown(combined, ratings, sample)
    replacements = {
        "# 20案最终评价跨模型互评结果": "# 40案最终评价跨模型互评结果",
        "| 案件 | 20 |": "| 案件 | 40 |",
        "| 问题 | 100（每案5题） |": "| 问题 | 200（每案5题） |",
        "| 既有回答 | 500（5种回答条件各100） |": "| 既有回答 | 1,000（5种回答条件各200） |",
        "| 复用原DeepSeek评分 | 300（仅用于GPT/Gemini/Qwen回答） |": "| 复用原DeepSeek评分 | 600（仅用于GPT/Gemini/Qwen回答） |",
        "每案先平均5题，n=20": "每案先平均5题，n=40",
        "| 专家复核总量 | 125份回答 |": "| 专家复核总量 | 250份回答 |",
        "| 基础样本 | 100份：每个问题抽1份回答；五种回答条件各20份 |": "| 基础样本 | 200份：每个问题抽1份回答；五种回答条件各40份 |",
        "| 高分歧加抽样 | 25份：每种回答条件各5份；与基础样本不重叠 |": "| 高分歧加抽样 | 50份：每种回答条件各10份；与基础样本不重叠 |",
        "500份回答的共识分": "1,000份回答的共识分",
    }
    for old, new in replacements.items():
        if old not in markdown:
            raise SystemExit(f"report template marker missing: {old}")
        markdown = markdown.replace(old, new)
    markdown = markdown.replace(" | 0.000 |", " | <0.001 |")

    args.output_dir.mkdir(parents=True, exist_ok=True)
    report_path = args.output_dir / "40案最终评价_跨模型互评结果.md"
    report_path.write_text(markdown, encoding="utf-8")
    save_json(args.output_dir / "专家复核样本_250份.json", sample)
    save_json(
        args.output_dir / "结果统计_机器可读.json",
        {
            "source": str(args.input.resolve()),
            "ratings": ratings,
            "expert_sample_ids": [item["blind_id"] for item in sample],
            "previous_sample_preserved": True,
        },
    )
    print(report_path)
    print(args.output_dir / "专家复核样本_250份.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
