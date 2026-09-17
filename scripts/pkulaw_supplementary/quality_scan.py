"""对 primary + secondary 全量做自动化质量检查
四类问题：
  Q1 问题起句截断（. 、 ) 】 " 等断句符号开头）
  Q2 数字粘连（某男/女 编号 与相邻数字粘连）
  Q3 地名残留（省+市+法院、常见省市名）
  Q4 跨字段编号漂移（原告/被告在 title/case/judge 中角色不一致）
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO = Path("/aifs4su/hansirui/boqin/abuse/DV-JusticeBench")

PRIMARY_MASKED = REPO / "data/pkulaw_filter/prepare_output/masked_cases.jsonl"
PRIMARY_Q = REPO / "data/pkulaw_filter/prepare_output/questions.jsonl"
SECONDARY_MASKED = REPO / "data/pkulaw_filter/prepare_output_secondary/masked_cases.jsonl"
SECONDARY_Q = REPO / "data/pkulaw_filter/prepare_output_secondary/questions.jsonl"


# ---------- 检查器 ----------

TRUNCATION_HEAD_CHARS = set("、）】。，；：」』)]}")
# 问题应以中文实词开头；起句 = "、、" ")" "" 这些属于被截断
def check_truncated_question(q: str) -> bool:
    if not q or not q.strip():
        return True
    s = q.strip()
    head = s[0]
    if head in TRUNCATION_HEAD_CHARS:
        return True
    # 有右括号但没有对应左括号 → 前半段被吃掉了
    left_p = s.find('（')
    right_p = s.find('）')
    if right_p != -1 and (left_p == -1 or left_p > right_p):
        return True
    left_e = s.find('(')
    right_e = s.find(')')
    if right_e != -1 and (left_e == -1 or left_e > right_e):
        return True
    # 有右引号但没有对应左引号
    if '"' in s[:30] and '"' not in s[: s.find('"')]:
        return True
    return False


# 数字粘连：某男/女 后紧接编号，编号后紧接 >= 4 位数字（看着像金额）
NUMBER_FUSION = re.compile(r'某[男女](\d{4,})')  # 编号本身 4 位+ 就是异常

def check_number_fusion(text: str) -> list:
    """返回被粘连的片段"""
    if not text:
        return []
    hits = []
    for m in NUMBER_FUSION.finditer(text):
        # 提取上下文
        s, e = m.start(), m.end()
        ctx = text[max(0, s-8):min(len(text), e+8)]
        hits.append(ctx)
    return hits


# 地名残留：
# 1) 具名省市（中国常见省市名）+ (省|市|县) 后紧跟"人民法院"、"中级"、"高级"、"分院"、"街道"
COMMON_PROVINCES = ["北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
                    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
                    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
                    "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门", "深圳", "杭州",
                    "成都", "苏州", "武汉", "南京", "郑州", "长沙", "青岛", "厦门", "宁波", "东莞"]

def check_residual_location(text: str) -> list:
    """返回残留的地名片段"""
    if not text:
        return []
    hits = []
    # 具名法院
    for p in COMMON_PROVINCES:
        # 例如 "北京市第二中级人民法院" "海南省某市中级人民法院" (前者完整地名+法院，后者省份未脱)
        pattern = rf'{p}(?:省|市|区|县)?[一-龥]*?(?:人民法院|中级|高级|分院|街道|市委)'
        for m in re.finditer(pattern, text):
            hits.append(m.group(0))
    return list(set(hits))


# 跨字段编号漂移：检查"原告"跟着的是"某男1"还是"某女1"，如果两个字段不一致 → 漂移
def check_role_inversion(m: dict) -> str | None:
    """
    在 case_text_masked 和 judge_decision_masked 里各找一次"原告 X" 和 "被告 X"，
    如果两个字段说的原告不是同一个 "某男/女N"，判为漂移
    """
    def extract_role(text, role):
        if not text:
            return None
        # 匹配"原告某女1" "原告：某女1" "原告是某女1"
        m2 = re.search(rf'{role}[：: 是]*(某[男女]\d?)', text)
        return m2.group(1) if m2 else None

    ct = m.get("case_text_masked") or ""
    jd = m.get("judge_decision_masked") or ""

    for role in ["原告", "被告", "上诉人", "被上诉人", "申请人", "被申请人"]:
        r_ct = extract_role(ct, role)
        r_jd = extract_role(jd, role)
        if r_ct and r_jd and r_ct != r_jd:
            return f"{role}: case={r_ct}, judge={r_jd}"
    return None


# ---------- 主流程 ----------

def analyze(name: str, masked_path: Path, questions_path: Path):
    print(f"\n{'='*60}\n{name}\n{'='*60}")

    masked = {json.loads(l)["case_id"]: json.loads(l) for l in open(masked_path)}
    questions = {json.loads(l)["case_id"]: json.loads(l) for l in open(questions_path)}
    n_cases = len(masked)
    print(f"共 {n_cases} 案")

    # Q1 - 问题截断
    trunc_cases = 0
    trunc_q_total = 0
    trunc_examples = []
    for cid, qd in questions.items():
        bad_qs = [q["question"] for q in qd["questions"] if check_truncated_question(q["question"])]
        if bad_qs:
            trunc_cases += 1
            trunc_q_total += len(bad_qs)
            if len(trunc_examples) < 3:
                trunc_examples.append((cid, bad_qs[0]))

    # Q2 - 数字粘连
    fusion_cases = 0
    fusion_total = 0
    fusion_examples = []
    for cid, m in masked.items():
        hits = []
        for field in ("case_text_masked", "judge_decision_masked"):
            hits += check_number_fusion(m.get(field) or "")
        if hits:
            fusion_cases += 1
            fusion_total += len(hits)
            if len(fusion_examples) < 3:
                fusion_examples.append((cid, hits[0]))

    # Q3 - 地名残留
    loc_cases = 0
    loc_examples = []
    for cid, m in masked.items():
        hits = []
        for field in ("title_masked", "case_text_masked", "judge_decision_masked"):
            hits += check_residual_location(m.get(field) or "")
        if hits:
            loc_cases += 1
            if len(loc_examples) < 3:
                loc_examples.append((cid, hits[0]))

    # Q4 - 跨字段编号漂移
    role_cases = 0
    role_examples = []
    for cid, m in masked.items():
        inv = check_role_inversion(m)
        if inv:
            role_cases += 1
            if len(role_examples) < 3:
                role_examples.append((cid, inv))

    # 汇总
    print(f"\n【Q1 问题起句截断】             {trunc_cases:4d}/{n_cases} ({trunc_cases/n_cases*100:.1f}%)  受影响问题 {trunc_q_total}")
    for cid, q in trunc_examples:
        print(f"    · {cid[-10:]}: {q[:80]}")

    print(f"\n【Q2 数字粘连】                {fusion_cases:4d}/{n_cases} ({fusion_cases/n_cases*100:.1f}%)  片段数 {fusion_total}")
    for cid, ex in fusion_examples:
        print(f"    · {cid[-10:]}: ...{ex}...")

    print(f"\n【Q3 地名残留】                {loc_cases:4d}/{n_cases} ({loc_cases/n_cases*100:.1f}%)")
    for cid, ex in loc_examples:
        print(f"    · {cid[-10:]}: {ex}")

    print(f"\n【Q4 原被告角色漂移】           {role_cases:4d}/{n_cases} ({role_cases/n_cases*100:.1f}%)")
    for cid, ex in role_examples:
        print(f"    · {cid[-10:]}: {ex}")

    return {
        "n": n_cases,
        "trunc": trunc_cases, "trunc_q": trunc_q_total,
        "fusion": fusion_cases, "fusion_total": fusion_total,
        "loc": loc_cases,
        "role": role_cases,
    }


def main():
    p = analyze("primary (231 案)", PRIMARY_MASKED, PRIMARY_Q)
    s = analyze("secondary (2022 案)", SECONDARY_MASKED, SECONDARY_Q)

    print(f"\n{'='*60}\n对比汇总\n{'='*60}")
    print(f"{'问题':<25} {'primary':>15} {'secondary':>15}")
    for label, key in [("问题起句截断", "trunc"), ("数字粘连", "fusion"),
                        ("地名残留", "loc"), ("原被告漂移", "role")]:
        pp = f"{p[key]}/{p['n']} ({p[key]/p['n']*100:.1f}%)"
        ss = f"{s[key]}/{s['n']} ({s[key]/s['n']*100:.1f}%)"
        print(f"{label:<25} {pp:>15} {ss:>15}")


if __name__ == "__main__":
    main()
