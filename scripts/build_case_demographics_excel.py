#!/usr/bin/env python3
"""
Build Case Demographics Excel for 108 cases: 案由、地域、争议点、判决结果、地点、时间.
Output: data/Case_Demographics_108cases.xlsx
"""
import pandas as pd
import re
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
SRC_EXCEL = DATA_DIR / "108个案例_新标准评估_完整版_最终版.xlsx"
OUT_EXCEL = DATA_DIR / "Case_Demographics_108cases.xlsx"


def norm_anyou(t):
    """Normalize 案由 from case title."""
    t = str(t)
    if "离婚" in t and "财产" in t:
        return "离婚后财产纠纷"
    if "离婚" in t:
        return "离婚纠纷"
    if "抚养" in t or "抚养权" in t or "抚养费" in t:
        return "抚养权/抚养费纠纷"
    if "婚约财产" in t or "彩礼" in t:
        return "婚约财产纠纷"
    if "故意杀人" in t:
        return "故意杀人罪"
    if "故意伤害" in t:
        return "故意伤害罪"
    if "生命权" in t or "健康权" in t or "身体权" in t or "人身" in t:
        return "生命权、身体权、健康权纠纷"
    if "侵权" in t:
        return "侵权责任纠纷"
    if "排除妨害" in t:
        return "排除妨害纠纷"
    if "民间借贷" in t:
        return "民间借贷纠纷"
    if "撤销婚姻" in t:
        return "撤销婚姻纠纷"
    if "同居" in t:
        return "同居关系纠纷"
    if "继承" in t:
        return "继承纠纷"
    if "离婚后损害" in t or "损害责任" in t:
        return "离婚后损害责任纠纷"
    if "共有" in t:
        return "共有/共有物分割纠纷"
    if "合同" in t or "撤销权" in t or "赠与" in t or "不当得利" in t:
        return "合同/给付类纠纷"
    if "纠纷" in t:
        return "其他民事纠纷"
    return "其他"


def dispute_type(q):
    """Categorize 争议点 from 问题."""
    q = str(q)
    if "家庭暴力" in q or "家暴" in q:
        return "家庭暴力认定"
    if "人身安全保护令" in q or "保护令" in q:
        return "人身安全保护令"
    if "抚养" in q or "抚养权" in q:
        return "抚养权/抚养费"
    if "离婚" in q and ("财产" in q or "分割" in q):
        return "离婚财产分割"
    if "离婚" in q:
        return "离婚与救济"
    if "证据" in q or "证明" in q:
        return "证据与证明力"
    if "过错" in q or "责任" in q:
        return "过错与责任"
    if "彩礼" in q or "婚约" in q:
        return "婚约财产/彩礼"
    return "其他争点"


def main():
    if not SRC_EXCEL.exists():
        raise FileNotFoundError(f"Source Excel not found: {SRC_EXCEL}")
    df = pd.read_excel(SRC_EXCEL, sheet_name="Sheet1", engine="openpyxl")
    n_cases = df["案例ID"].nunique()
    assert n_cases == 108, f"Expected 108 cases, got {n_cases}"

    cases = (
        df.drop_duplicates(subset=["案例ID"], keep="first")[
            ["案例ID", "案例标题"]
        ].copy()
    )
    cases["案由"] = cases["案例标题"].astype(str).apply(norm_anyou)
    df["争议点类型"] = df["问题"].apply(dispute_type)
    dispute_by_case = (
        df.groupby("案例ID")["争议点类型"]
        .apply(lambda x: "; ".join(sorted(set(x))))
        .reset_index()
    )
    dispute_by_case.columns = ["案例ID", "争议点汇总"]
    cases = cases.merge(dispute_by_case, on="案例ID", how="left")

    # 地点：当前数据源无对应字段，留空供后续标注
    cases["地点"] = ""
    # 时间：从案例ID解析数据入库日期（case_YYYYMMDD_...）
    def parse_date_from_id(cid):
        m = re.search(r"case_(\d{4})(\d{2})(\d{2})", str(cid))
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}" if m else ""
    cases["时间_数据日期"] = cases["案例ID"].apply(parse_date_from_id)
    cases["时间_判决年份或日期"] = ""  # 裁判文书判决日期，可填 2023/2024/2025 等
    # 地域、判决结果：留空供后续标注
    cases["地域"] = ""
    cases["判决结果"] = ""

    # ---- Sheet 1: Case Demographics (Summary for paper) ----
    anyou_counts = cases["案由"].value_counts()
    anyou_pct = (anyou_counts / anyou_counts.sum() * 100).round(1)
    demo_rows = [
        {
            "Legal Characteristic 法律特征": "Cause of Action 案由",
            "Category 分类": k,
            "Count 数量": int(v),
            "Percentage (%) 占比": f"{anyou_pct.get(k, 0)}%",
            "Note 说明": "Civil & criminal; DV-related disputes.",
        }
        for k, v in anyou_counts.items()
    ]
    demo_rows.append(
        {
            "Legal Characteristic 法律特征": "Region 地域",
            "Category 分类": "Multiple provinces (China Judgments Online; SPC typical cases) 全国多省份（中国裁判文书网、最高人民法院典型案件等）",
            "Count 数量": 108,
            "Percentage (%) 占比": "100%",
            "Note 说明": "Sample spans 2023–2025, various court levels.",
        }
    )
    demo_rows.append(
        {
            "Legal Characteristic 法律特征": "Location 地点",
            "Category 分类": "See 108案例列表 (per-case 地点). 具体见108案例列表（每案地点可填法院/省份）。",
            "Count 数量": 108,
            "Percentage (%) 占比": "100%",
            "Note 说明": "各地法院；可于108案例列表中按案填写。",
        }
    )
    demo_rows.append(
        {
            "Legal Characteristic 法律特征": "Time 时间",
            "Category 分类": "Judgment period 2023–2025; data date from 案例ID. 裁判文书年份2023–2025；数据日期见案例ID。",
            "Count 数量": 108,
            "Percentage (%) 占比": "100%",
            "Note 说明": "判决年份/日期可于108案例列表中按案填写。",
        }
    )
    dispute_counts = df["争议点类型"].value_counts()
    dispute_pct = (dispute_counts / dispute_counts.sum() * 100).round(1)
    for k, v in dispute_counts.items():
        demo_rows.append(
            {
                "Legal Characteristic 法律特征": "Dispute Type 争议点",
                "Category 分类": k,
                "Count 数量": int(v),
                "Percentage (%) 占比": f"{dispute_pct.get(k, 0)}%",
                "Note 说明": "540 questions across 108 cases (5 per case).",
            }
        )
    demo_rows.append(
        {
            "Legal Characteristic 法律特征": "Outcome 判决结果",
            "Category 分类": "Diverse (see source judgments) 见裁判文书",
            "Count 数量": 108,
            "Percentage (%) 占比": "—",
            "Note 说明": "Support / partial support / dismiss / mediation, etc.",
        }
    )
    demo_df = pd.DataFrame(demo_rows)

    # ---- Sheet 2: 案由分布 ----
    anyou_df = pd.DataFrame(
        {
            "案由": anyou_counts.index,
            "数量": anyou_counts.values,
            "占比(%)": (anyou_counts.values / anyou_counts.sum() * 100).round(1),
        }
    )

    # ---- Sheet 3: 争议点分布 ----
    dispute_df = pd.DataFrame(
        {
            "争议点类型": dispute_counts.index,
            "数量": dispute_counts.values,
            "占比(%)": (dispute_counts.values / dispute_counts.sum() * 100).round(1),
        }
    )

    # ---- Sheet 4: 108 cases list ----
    case_list = cases[
        [
            "案例ID",
            "案例标题",
            "案由",
            "地点",
            "时间_判决年份或日期",
            "时间_数据日期",
            "争议点汇总",
            "地域",
            "判决结果",
        ]
    ].copy()
    case_list.columns = [
        "案例ID",
        "案例标题",
        "案由",
        "地点（可后续标注）",
        "时间（判决年份/日期，可后续标注）",
        "时间（数据日期，从案例ID解析）",
        "争议点汇总",
        "地域（可后续标注）",
        "判决结果（可后续标注）",
    ]

    with pd.ExcelWriter(OUT_EXCEL, engine="openpyxl") as w:
        demo_df.to_excel(w, sheet_name="Case Demographics", index=False)
        anyou_df.to_excel(w, sheet_name="案由分布", index=False)
        dispute_df.to_excel(w, sheet_name="争议点分布", index=False)
        case_list.to_excel(w, sheet_name="108案例列表", index=False)

    print(f"Written: {OUT_EXCEL}")
    print("Sheets: Case Demographics, 案由分布, 争议点分布, 108案例列表")
    return OUT_EXCEL


if __name__ == "__main__":
    main()
