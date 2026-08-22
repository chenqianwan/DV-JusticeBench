#!/usr/bin/env python3
"""Select a reproducible second 20-case block for 40-case cross judging."""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path
from typing import Dict, List, Tuple

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEMOGRAPHICS = ROOT / "data/Case_Demographics_108cases.xlsx"
CURRENT20 = ROOT / "data/additional_20_cases_20260819.json"
REMAINING68 = ROOT / "data/experiment1_remaining68_xhub_20260821/combined_results.json"
QUESTIONS = ROOT / "data/108个案例_新标准评估_完整版_最终版.xlsx"
CASES = ROOT / "data/cases/cases.json"
OUTPUT_MANIFEST = ROOT / "data/cross_judge_expansion20_cases_20260822.json"
OUTPUT_SOURCE = ROOT / "data/cross_judge_expansion20_source_20260822.json"
SEED = "AI-Law-cross-judge-double-20-v1-20260822"


def load_json(path: Path):
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, value) -> None:
    with path.open("w", encoding="utf-8") as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)


def allocate_quotas(
    population: Counter,
    current: Counter,
    available: Counter,
    add_count: int,
) -> Dict[str, int]:
    """DP minimum squared proportion error for the final 40-case sample."""
    causes = sorted(population)
    final_n = sum(current.values()) + add_count
    pop_n = sum(population.values())
    states: Dict[int, Tuple[float, List[int]]] = {0: (0.0, [])}
    for cause in causes:
        updated: Dict[int, Tuple[float, List[int]]] = {}
        upper = min(int(available.get(cause, 0)), add_count)
        for used, (cost, allocations) in states.items():
            for quota in range(upper + 1):
                total = used + quota
                if total > add_count:
                    break
                final_share = (current.get(cause, 0) + quota) / final_n
                population_share = population[cause] / pop_n
                candidate = cost + (final_share - population_share) ** 2
                prior = updated.get(total)
                candidate_allocations = allocations + [quota]
                if prior is None or candidate < prior[0] - 1e-15 or (
                    abs(candidate - prior[0]) <= 1e-15 and candidate_allocations < prior[1]
                ):
                    updated[total] = (candidate, candidate_allocations)
        states = updated
    if add_count not in states:
        raise RuntimeError("no feasible quota allocation")
    return dict(zip(causes, states[add_count][1]))


def main() -> int:
    demographics = pd.read_excel(DEMOGRAPHICS, sheet_name="108案例列表")
    demographics["案例ID"] = demographics["案例ID"].astype(str)
    demographics["案由"] = demographics["案由"].fillna("未标注").astype(str)
    cause_by_case = dict(zip(demographics["案例ID"], demographics["案由"]))
    title_by_case = dict(zip(demographics["案例ID"], demographics["案例标题"].fillna("").astype(str)))
    population = Counter(demographics["案由"])

    current_manifest = load_json(CURRENT20)
    current_ids = [item["case_id"] for item in current_manifest["cases"]]
    current = Counter(cause_by_case[case_id] for case_id in current_ids)

    remaining = load_json(REMAINING68)
    candidate_ids = list(remaining["completed_case_ids"])
    if len(candidate_ids) != 68 or set(candidate_ids) & set(current_ids):
        raise RuntimeError("remaining68 source is not a disjoint 68-case candidate pool")
    available = Counter(cause_by_case[case_id] for case_id in candidate_ids)
    quotas = allocate_quotas(population, current, available, 20)

    selected_ids: List[str] = []
    for cause in sorted(quotas):
        members = [case_id for case_id in candidate_ids if cause_by_case[case_id] == cause]
        members.sort(key=lambda case_id: hashlib.sha256(f"{SEED}|{case_id}".encode()).hexdigest())
        selected_ids.extend(members[: quotas[cause]])

    if len(selected_ids) != 20 or len(set(selected_ids)) != 20:
        raise RuntimeError("selection did not produce 20 unique cases")

    question_frame = pd.read_excel(QUESTIONS)
    questions_by_case = Counter(question_frame["案例ID"].astype(str))
    cases = load_json(CASES)
    manifest_cases = []
    for case_id in selected_ids:
        case = cases[case_id]
        case_text = str(case.get("content", case.get("case_text", "")) or "")
        judgment = str(case.get("judge_decision", "") or "")
        if questions_by_case[case_id] != 5 or not case_text or not judgment:
            raise RuntimeError(f"invalid source data for {case_id}")
        manifest_cases.append(
            {
                "case_id": case_id,
                "title": title_by_case.get(case_id, ""),
                "cause": cause_by_case[case_id],
                "question_count": 5,
                "case_text_chars": len(case_text),
                "judgment_chars": len(judgment),
                "selection_hash": hashlib.sha256(f"{SEED}|{case_id}".encode()).hexdigest(),
            }
        )

    final_counts = current + Counter(cause_by_case[case_id] for case_id in selected_ids)
    manifest = {
        "manifest_version": "1.0",
        "created_at": "2026-08-22",
        "purpose": "Second disjoint 20-case block doubling final-evaluator cross judging from 20 to 40 cases",
        "selection_seed": SEED,
        "population_case_count": 108,
        "candidate_case_count": 68,
        "existing_cross_judge_case_count": 20,
        "selected_case_count": 20,
        "final_cross_judge_case_count": 40,
        "selection_method": "Allocate integer counts by cause of action to minimize unweighted squared deviation between the final 40-case cause proportions and the 108-case population; select within strata by ascending SHA256(seed|case_id).",
        "cause_quotas": {cause: quota for cause, quota in quotas.items() if quota},
        "final_40_cause_counts": dict(sorted(final_counts.items())),
        "validation": {
            "overlap_with_existing_cross_judge_20": len(set(selected_ids) & set(current_ids)),
            "duplicate_case_ids": len(selected_ids) - len(set(selected_ids)),
            "cases_with_exactly_five_questions": sum(questions_by_case[case_id] == 5 for case_id in selected_ids),
            "cases_with_nonempty_case_and_judgment": len(manifest_cases),
        },
        "sources": {
            "population_and_strata": str(DEMOGRAPHICS.resolve()),
            "existing_cross_judge_20": str(CURRENT20.resolve()),
            "candidate_raw_results": str(REMAINING68.resolve()),
        },
        "cases": manifest_cases,
    }
    save_json(OUTPUT_MANIFEST, manifest)

    selected_raw = {case_id: remaining["per_case_raw_results"][case_id] for case_id in selected_ids}
    filtered_source = {
        "status": "PASS",
        "created_at": "2026-08-22",
        "selection_manifest": str(OUTPUT_MANIFEST.resolve()),
        "source_remaining68": str(REMAINING68.resolve()),
        "completed_case_ids": selected_ids,
        "completed_case_count": 20,
        "completed_answer_count": 500,
        "per_case_raw_results": selected_raw,
    }
    save_json(OUTPUT_SOURCE, filtered_source)
    print(OUTPUT_MANIFEST)
    print(OUTPUT_SOURCE)
    print(json.dumps(manifest["cause_quotas"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
