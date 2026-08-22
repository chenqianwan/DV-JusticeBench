#!/usr/bin/env python3
"""Build the concise Markdown report and frozen expert-review sample."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import statistics
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Dict, Iterable, List

import numpy as np
import pandas as pd
from scipy import stats


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "data/experiment1_cross_judge_sample20_xhub_20260822/combined_results.json"
DEFAULT_OUTPUT_DIR = ROOT / "outputs/20260822_cross_family_judging_sample20"

DIMENSIONS = [
    "规范依据相关性",
    "涵摄链条对齐度",
    "价值衡量与同理心对齐度",
    "关键事实与争点覆盖度",
    "裁判结论与救济配置一致性",
]
CONDITIONS = [
    "GPT-4o",
    "Gemini 2.5 Flash",
    "Qwen-Max",
    "DeepSeek thinking",
    "DeepSeek non-thinking",
]
JUDGES = ["GPT-4o", "Gemini 2.5 Flash", "Qwen-Max", "DeepSeek v3.2"]


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def fmt(value: Any, digits: int = 2) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "—"
    return f"{float(value):.{digits}f}"


def mean_ci(values: Iterable[float]) -> tuple[float, float, float]:
    data = np.asarray(list(values), dtype=float)
    mean = float(np.mean(data))
    if len(data) < 2 or float(np.std(data, ddof=1)) == 0:
        return mean, mean, mean
    half = float(stats.t.ppf(0.975, len(data) - 1) * stats.sem(data))
    return mean, mean - half, mean + half


def holm_adjust(p_values: List[float]) -> List[float]:
    order = np.argsort(p_values)
    adjusted = [1.0] * len(p_values)
    running = 0.0
    n = len(p_values)
    for rank, index in enumerate(order):
        candidate = min(1.0, (n - rank) * float(p_values[index]))
        running = max(running, candidate)
        adjusted[int(index)] = running
    return adjusted


def wilcoxon_p(left: Iterable[float], right: Iterable[float]) -> float:
    a = np.asarray(list(left), dtype=float)
    b = np.asarray(list(right), dtype=float)
    if np.allclose(a, b):
        return 1.0
    try:
        return float(stats.wilcoxon(a, b, alternative="two-sided").pvalue)
    except ValueError:
        return 1.0


def spearman(x: Iterable[float], y: Iterable[float]) -> float:
    values = stats.spearmanr(list(x), list(y), nan_policy="omit")
    return float(values.statistic) if values.statistic is not None else float("nan")


def rating_rows(combined: Dict[str, Any]) -> List[Dict[str, Any]]:
    consensus_by_answer = {
        answer["answer_id"]: float(answer["consensus"]["total_score"])
        for answer in combined["answers"]
    }
    rows = []
    for answer in combined["answers"]:
        for task_id in answer["cross_family_rating_task_ids"]:
            rating_path = Path(combined["rating_files"][task_id])
            rating = load_json(rating_path)
            total = float(rating["evaluation"]["总分"])
            rows.append(
                {
                    "answer_id": answer["answer_id"],
                    "case_id": answer["case_id"],
                    "answer_condition": answer["answer_condition"],
                    "judge": rating["judge_label"],
                    "judge_family": rating["judge_family"],
                    "source": rating["source"],
                    "total": total,
                    "consensus": consensus_by_answer[answer["answer_id"]],
                    "minor": bool((rating.get("errors") or {}).get("微小错误")),
                    "moderate": bool((rating.get("errors") or {}).get("明显错误")),
                    "major": bool((rating.get("errors") or {}).get("重大错误")),
                }
            )
    return rows


def build_expert_sample(combined: Dict[str, Any]) -> List[Dict[str, Any]]:
    answers = combined["answers"]
    case_order: List[str] = []
    for answer in answers:
        if answer["case_id"] not in case_order:
            case_order.append(answer["case_id"])
    answer_lookup = {
        (item["case_id"], int(item["question_number"]), item["answer_condition"]): item
        for item in answers
    }

    base: List[Dict[str, Any]] = []
    for case_index, case_id in enumerate(case_order):
        for question_number in range(1, 6):
            condition = CONDITIONS[(case_index + question_number - 1) % len(CONDITIONS)]
            selected = answer_lookup[(case_id, question_number, condition)]
            base.append({**selected, "review_stratum": "基础平衡样本"})

    base_ids = {item["answer_id"] for item in base}
    targeted: List[Dict[str, Any]] = []
    for condition in CONDITIONS:
        candidates = [
            item for item in answers
            if item["answer_condition"] == condition and item["answer_id"] not in base_ids
        ]

        def priority(item: Dict[str, Any]) -> tuple:
            votes = item["consensus"].get("error_vote_counts") or {}
            major_votes = int(votes.get("重大错误") or 0)
            moderate_votes = int(votes.get("明显错误") or 0)
            major_disagreement = 1 if major_votes in (1, 2) else 0
            moderate_disagreement = 1 if moderate_votes in (1, 2) else 0
            judge_totals = list((item["consensus"].get("judge_total_scores") or {}).values())
            spread_sd = statistics.pstdev(judge_totals) if len(judge_totals) > 1 else 0.0
            old_delta = abs(float(item["consensus"]["total_score"]) - float(item["original_deepseek_total"]))
            tie = hashlib.sha256(item["answer_id"].encode("utf-8")).hexdigest()
            return (
                major_disagreement,
                moderate_disagreement,
                float(item["consensus"].get("judge_score_range") or 0),
                spread_sd,
                old_delta,
                tie,
            )

        candidates.sort(key=priority, reverse=True)
        targeted.extend({**item, "review_stratum": "高分歧加抽样"} for item in candidates[:5])

    selected = base + targeted
    if len(selected) != 125 or len({item["answer_id"] for item in selected}) != 125:
        raise RuntimeError("expert sample must contain 125 unique answer units")
    if Counter(item["answer_condition"] for item in selected) != Counter({condition: 25 for condition in CONDITIONS}):
        raise RuntimeError("expert sample is not balanced 25-per-condition")
    if len({(item["case_id"], item["question_number"]) for item in base}) != 100:
        raise RuntimeError("base sample must cover every question exactly once")

    frozen = []
    for index, item in enumerate(selected, 1):
        ratings = []
        for task_id in item["cross_family_rating_task_ids"]:
            rating = load_json(Path(combined["rating_files"][task_id]))
            ratings.append(
                {
                    "judge_label": rating["judge_label"],
                    "total_score": rating["evaluation"]["总分"],
                    "dimension_scores": rating["evaluation"]["各维度得分"],
                    "errors": rating.get("errors") or {},
                    "detailed_evaluation": rating["evaluation"].get("详细评价") or "",
                }
            )
        frozen.append(
            {
                "sequence": index,
                "blind_id": f"HR{index:03d}",
                "review_stratum": item["review_stratum"],
                "case_id": item["case_id"],
                "masked_title": item["masked_title"],
                "question_number": item["question_number"],
                "question": item["question"],
                "masked_case_text": item["masked_case_text"],
                "masked_judgment": item["masked_judgment"],
                "answer": item["answer"],
                "answer_condition": item["answer_condition"],
                "answer_operational_model_id": item["answer_operational_model_id"],
                "original_deepseek_total": item["original_deepseek_total"],
                "consensus": item["consensus"],
                "machine_ratings": sorted(ratings, key=lambda value: value["judge_label"]),
            }
        )
    return frozen


def build_markdown(combined: Dict[str, Any], ratings: List[Dict[str, Any]], sample: List[Dict[str, Any]]) -> str:
    answer_df = pd.DataFrame(
        [
            {
                "answer_id": item["answer_id"],
                "case_id": item["case_id"],
                "condition": item["answer_condition"],
                "old": float(item["original_deepseek_total"]),
                "consensus": float(item["consensus"]["total_score"]),
                "range": float(item["consensus"]["judge_score_range"]),
                "minor_votes": int(item["consensus"]["error_vote_counts"]["微小错误"]),
                "moderate_votes": int(item["consensus"]["error_vote_counts"]["明显错误"]),
                "major_votes": int(item["consensus"]["error_vote_counts"]["重大错误"]),
                **{
                    f"dim_{dimension}": float(item["consensus"]["dimension_scores"][dimension])
                    for dimension in DIMENSIONS
                },
            }
            for item in combined["answers"]
        ]
    )
    rating_df = pd.DataFrame(ratings)
    case_df = (
        answer_df.groupby(["case_id", "condition"], as_index=False)
        .agg(
            old=("old", "mean"),
            consensus=("consensus", "mean"),
            **{f"dim_{i}": (f"dim_{dimension}", "mean") for i, dimension in enumerate(DIMENSIONS)},
        )
    )

    main_rows = []
    raw_p = []
    for condition in CONDITIONS:
        selected = case_df[case_df["condition"] == condition]
        consensus_mean, ci_low, ci_high = mean_ci(selected["consensus"])
        old_mean = float(selected["old"].mean())
        delta = consensus_mean - old_mean
        p_value = wilcoxon_p(selected["consensus"], selected["old"])
        raw_p.append(p_value)
        main_rows.append(
            {
                "condition": condition,
                "old": old_mean,
                "consensus": consensus_mean,
                "ci_low": ci_low,
                "ci_high": ci_high,
                "delta": delta,
                "p": p_value,
            }
        )
    adjusted = holm_adjust(raw_p)
    for row, value in zip(main_rows, adjusted):
        row["holm"] = value
    old_rank = {row["condition"]: rank for rank, row in enumerate(sorted(main_rows, key=lambda x: x["old"], reverse=True), 1)}
    new_rank = {row["condition"]: rank for rank, row in enumerate(sorted(main_rows, key=lambda x: x["consensus"], reverse=True), 1)}

    lines = [
        "# 20案最终评价跨模型互评结果",
        "",
        "## 1. 实验范围",
        "",
        "| 项目 | 数量 / 口径 |",
        "|---|---:|",
        "| 案件 | 20 |",
        "| 问题 | 100（每案5题） |",
        "| 既有回答 | 500（5种回答条件各100） |",
        "| 每份回答的跨家族评分 | 3 |",
        f"| 互评分总数 | {combined['completion']['ratings_completed']:,} |",
        f"| 新API评分 | {sum(item['new_api_ratings'] for item in combined['usage_by_judge'].values()):,} |",
        "| 复用原DeepSeek评分 | 300（仅用于GPT/Gemini/Qwen回答） |",
        "| 同家族自评 | 主结果排除 |",
        "| 共识分 | 三名跨家族评分者完成旧版错误惩罚后的中位数 |",
        "",
        "## 2. 评分矩阵",
        "",
        "| 回答条件 | 纳入的三名评分模型 |",
        "|---|---|",
    ]
    for condition in CONDITIONS:
        lines.append(f"| {condition} | {'、'.join(combined['judge_matrix'][condition])} |")

    lines.extend(
        [
            "",
            "## 3. 主结果：原DeepSeek单评 vs 跨家族共识",
            "",
            "以下均值和95% CI以案件为统计单位（每案先平均5题，n=20）；分数范围0–20。",
            "",
            "| 回答条件 | 原DS均值 | 互评共识均值（95% CI） | 差值 | 原排名 | 新排名 | 配对Wilcoxon Holm p |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for row in sorted(main_rows, key=lambda item: new_rank[item["condition"]]):
        lines.append(
            f"| {row['condition']} | {fmt(row['old'])} | {fmt(row['consensus'])} "
            f"({fmt(row['ci_low'])}, {fmt(row['ci_high'])}) | {fmt(row['delta'])} | "
            f"{old_rank[row['condition']]} | {new_rank[row['condition']]} | {fmt(row['holm'], 3)} |"
        )

    lines.extend(
        [
            "",
            "## 4. 各评分模型对各回答条件的均分",
            "",
            "同家族单元格不评分；共识列为每份回答三名跨家族评分的中位数。",
            "",
            "| 回答条件 | GPT-4o评 | Gemini评 | Qwen评 | DeepSeek评 | 三方共识 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for condition in CONDITIONS:
        cells = []
        for judge in JUDGES:
            subset = rating_df[(rating_df["answer_condition"] == condition) & (rating_df["judge"] == judge)]
            cells.append(fmt(subset["total"].mean()) if len(subset) else "—")
        consensus = answer_df[answer_df["condition"] == condition]["consensus"].mean()
        lines.append(f"| {condition} | {' | '.join(cells)} | {fmt(consensus)} |")

    lines.extend(
        [
            "",
            "## 5. 共识分的五维结果",
            "",
            "| 回答条件 | 规范依据 | 涵摄链条 | 价值与同理心 | 事实与争点 | 结论与救济 |",
            "|---|---:|---:|---:|---:|---:|",
        ]
    )
    for condition in CONDITIONS:
        selected = case_df[case_df["condition"] == condition]
        cells = [fmt(selected[f"dim_{index}"].mean()) for index in range(5)]
        lines.append(f"| {condition} | {' | '.join(cells)} |")

    lines.extend(
        [
            "",
            "## 6. 评分者尺度与一致性",
            "",
            "| 评分模型 | n | 均分 | 相对三方中位数偏差 | 绝对偏差 | Spearman ρ | 微小/明显/重大错误率 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for judge in JUDGES:
        selected = rating_df[rating_df["judge"] == judge]
        bias = float((selected["total"] - selected["consensus"]).mean())
        mae = float((selected["total"] - selected["consensus"]).abs().mean())
        rho = spearman(selected["total"], selected["consensus"])
        rates = "/".join(f"{100 * selected[level].mean():.1f}%" for level in ["minor", "moderate", "major"])
        lines.append(
            f"| {judge} | {len(selected)} | {fmt(selected['total'].mean())} | {fmt(bias)} | "
            f"{fmt(mae)} | {fmt(rho)} | {rates} |"
        )

    lines.extend(
        [
            "",
            "### 两两评分一致性（共同评分的回答）",
            "",
            "| 评分模型对 | n | Spearman ρ | 平均绝对差 | 差值≤2分 |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for left_index, left in enumerate(JUDGES):
        for right in JUDGES[left_index + 1 :]:
            a = rating_df[rating_df["judge"] == left][["answer_id", "total"]].rename(columns={"total": "left"})
            b = rating_df[rating_df["judge"] == right][["answer_id", "total"]].rename(columns={"total": "right"})
            merged = a.merge(b, on="answer_id")
            if merged.empty:
                continue
            rho = spearman(merged["left"], merged["right"])
            mad = float((merged["left"] - merged["right"]).abs().mean())
            within = float(((merged["left"] - merged["right"]).abs() <= 2).mean() * 100)
            lines.append(f"| {left} vs {right} | {len(merged)} | {fmt(rho)} | {fmt(mad)} | {within:.1f}% |")

    major_disagreement = float(answer_df["major_votes"].isin([1, 2]).mean() * 100)
    majority_major = float((answer_df["major_votes"] >= 2).mean() * 100)
    lines.extend(
        [
            "",
            "## 7. 分歧与专家复核样本",
            "",
            "| 指标 | 结果 |",
            "|---|---:|",
            f"| 三评分者总分极差中位数 | {fmt(answer_df['range'].median())} |",
            f"| 三评分者总分极差P90 | {fmt(answer_df['range'].quantile(0.90))} |",
            f"| 总分极差>4分 | {100 * (answer_df['range'] > 4).mean():.1f}% |",
            f"| 重大错误标记存在分歧 | {major_disagreement:.1f}% |",
            f"| 至少2名评分者标记重大错误 | {majority_major:.1f}% |",
            "| 专家复核总量 | 125份回答 |",
            "| 基础样本 | 100份：每个问题抽1份回答；五种回答条件各20份 |",
            "| 高分歧加抽样 | 25份：每种回答条件各5份；与基础样本不重叠 |",
            "| 专家流程 | 两名法律专家独立盲评；维度差>1、重大错误不一致或总分差>4时进入第三人仲裁 |",
            "",
            "## 8. API用量与成本",
            "",
            "| 评分模型 | 新调用 | 输入tokens | 输出tokens | 估算成本（USD） |",
            "|---|---:|---:|---:|---:|",
        ]
    )
    for judge in JUDGES:
        entry = combined["usage_by_judge"][judge]
        usage = entry["usage"]
        lines.append(
            f"| {judge} | {entry['new_api_ratings']:,} | {usage['prompt_tokens']:,} | "
            f"{usage['completion_tokens']:,} | ${entry['estimated_xhub_list_cost_usd']:.4f} |"
        )
    lines.extend(
        [
            f"| **合计** | **{sum(item['new_api_ratings'] for item in combined['usage_by_judge'].values()):,}** | "
            f"**{sum(item['usage']['prompt_tokens'] for item in combined['usage_by_judge'].values()):,}** | "
            f"**{sum(item['usage']['completion_tokens'] for item in combined['usage_by_judge'].values()):,}** | "
            f"**${combined['estimated_xhub_list_cost_usd']:.4f}** |",
            "",
            f"人民币参考：约 ¥{combined['estimated_xhub_list_cost_cny_at_7_3']:.2f}（按7.3换算；最终以xhub账单为准）。",
            "",
            "## 9. 结论",
            "",
        ]
    )
    old_order = " > ".join(item["condition"] for item in sorted(main_rows, key=lambda x: x["old"], reverse=True))
    new_order = " > ".join(item["condition"] for item in sorted(main_rows, key=lambda x: x["consensus"], reverse=True))
    overall_bias = float((answer_df["consensus"] - answer_df["old"]).mean())
    lines.extend(
        [
            f"- 原DeepSeek单评排名：{old_order}。",
            f"- 跨家族三方共识排名：{new_order}。",
            f"- 500份回答的共识分相对原DeepSeek单评平均变化为 {overall_bias:+.2f} 分。",
            "- 主结果不含任何同家族自评；人工复核样本已按回答模型均衡，并主动覆盖高分歧项目。",
        ]
    )
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    args = parser.parse_args()

    combined = load_json(args.input)
    if not combined.get("completion", {}).get("complete"):
        raise SystemExit("cross-family ratings are incomplete")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    ratings = rating_rows(combined)
    if len(ratings) != 1500:
        raise SystemExit(f"expected 1500 ratings, found {len(ratings)}")
    sample = build_expert_sample(combined)
    markdown = build_markdown(combined, ratings, sample)

    report_path = args.output_dir / "20案最终评价_跨模型互评结果.md"
    report_path.write_text(markdown, encoding="utf-8")
    write_json(args.output_dir / "专家复核样本_125份.json", sample)
    write_json(
        args.output_dir / "结果统计_机器可读.json",
        {
            "source": str(args.input.resolve()),
            "ratings": ratings,
            "expert_sample_ids": [item["blind_id"] for item in sample],
        },
    )
    print(report_path)
    print(args.output_dir / "专家复核样本_125份.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
