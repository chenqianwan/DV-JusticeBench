"""最终质量扫描 fix4 输出（脱敏后处理 + 出题重跑之后）"""
import json
import sys
from pathlib import Path

sys.path.insert(0, "/tmp/dvj-smoke")
from quality_scan import (check_truncated_question, check_number_fusion,
                          check_residual_location, check_role_inversion)

REPO = Path("/aifs4su/hansirui/boqin/abuse/DV-JusticeBench")


def scan(name, mask_path, q_path):
    masked = [json.loads(l) for l in open(mask_path) if l.strip()]
    questions = [json.loads(l) for l in open(q_path) if l.strip()]
    n = len(masked)

    # Q1 问题截断
    trunc_cases = 0
    trunc_qs = 0
    for qd in questions:
        bad = [q for q in qd["questions"] if check_truncated_question(q["question"])]
        if bad:
            trunc_cases += 1
            trunc_qs += len(bad)

    fusion = sum(1 for c in masked if any(check_number_fusion(c.get(f) or "")
                 for f in ("title_masked", "case_text_masked", "judge_decision_masked")))
    loc = sum(1 for c in masked if any(check_residual_location(c.get(f) or "")
              for f in ("title_masked", "case_text_masked", "judge_decision_masked")))
    role = sum(1 for c in masked if check_role_inversion(c) is not None)

    print(f"\n=== {name} ({n} 案) ===")
    print(f"  问题起句截断   {trunc_cases:4d} ({trunc_cases/n*100:.1f}%)  受影响问题 {trunc_qs}")
    print(f"  数字粘连       {fusion:4d} ({fusion/n*100:.1f}%)")
    print(f"  地名残留       {loc:4d} ({loc/n*100:.1f}%)")
    print(f"  原被告漂移     {role:4d} ({role/n*100:.1f}%)")
    return {"n": n, "trunc": trunc_cases, "fusion": fusion, "loc": loc, "role": role}


def main():
    p = scan("Primary (fix4)",
             REPO/"data/pkulaw_filter/prepare_output/masked_cases_fix4.jsonl",
             REPO/"data/pkulaw_filter/prepare_output/questions_fix4.jsonl")
    s = scan("Secondary (fix4)",
             REPO/"data/pkulaw_filter/prepare_output_secondary/masked_cases_fix4.jsonl",
             REPO/"data/pkulaw_filter/prepare_output_secondary/questions_fix4.jsonl")

    print(f"\n=== 与最初 v1 对比（primary 231 案 / secondary 2022 案） ===")
    old = {"trunc_p": 51, "trunc_s": 615, "fusion_p": 26, "fusion_s": 126,
           "loc_p": 8, "loc_s": 16, "role_p": 87, "role_s": 942}
    old_n_p = 231; old_n_s = 2022

    def fmt(cnt, n):
        return f"{cnt}/{n} ({cnt/n*100:.1f}%)"

    print(f"{'问题':<20} {'v1 primary':>18} {'fix4 primary':>18} {'v1 secondary':>18} {'fix4 secondary':>18}")
    print(f"{'-'*20} {'-'*18} {'-'*18} {'-'*18} {'-'*18}")
    rows = [
        ("问题起句截断", "trunc_p", "trunc_s", "trunc"),
        ("数字粘连", "fusion_p", "fusion_s", "fusion"),
        ("地名残留", "loc_p", "loc_s", "loc"),
        ("原被告漂移", "role_p", "role_s", "role"),
    ]
    for label, ok_p, ok_s, key_new in rows:
        cells = [
            fmt(old[ok_p], old_n_p),
            fmt(p[key_new], p["n"]),
            fmt(old[ok_s], old_n_s),
            fmt(s[key_new], s["n"]),
        ]
        print(f"{label:<20} " + " ".join(f"{c:>18}" for c in cells))


if __name__ == "__main__":
    main()
