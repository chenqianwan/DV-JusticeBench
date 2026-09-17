"""对现有 masked_cases.jsonl 做后处理：
Fix 2: 数字粘连 —— 在"某男/女N"和后接的多位数字之间插空格
Fix 3: 地名残留 —— "PROV+市/省/县 + 人民法院" 替换为"某人民法院"

原文件重命名为 *_v1.jsonl，覆盖生成新的 masked_cases.jsonl
"""
import json
import re
import shutil
import sys
from pathlib import Path

REPO = Path("/aifs4su/hansirui/boqin/abuse/DV-JusticeBench")

TARGETS = [
    REPO / "data/pkulaw_filter/prepare_output/masked_cases_fix4.jsonl",
    REPO / "data/pkulaw_filter/prepare_output_secondary/masked_cases_fix4.jsonl",
]

PROVINCES = [
    "北京", "上海", "天津", "重庆", "河北", "山西", "辽宁", "吉林", "黑龙江",
    "江苏", "浙江", "安徽", "福建", "江西", "山东", "河南", "湖北", "湖南",
    "广东", "海南", "四川", "贵州", "云南", "陕西", "甘肃", "青海", "台湾",
    "内蒙古", "广西", "西藏", "宁夏", "新疆", "香港", "澳门",
]

# Fix 2: 某男/女 后紧跟 4+ 位数字 → 在编号与金额之间插空格
# 例："某女165000" → "某女1 65000"；"某男285518" → "某男2 85518"
NUMBER_FUSION = re.compile(r'(某[男女])(\d)(\d{3,})')

# Fix 3: 具名法院 → 某人民法院
# 匹配: [省名] 后可选 (省/市/自治区) 后任意中文 后 "人民法院"
def make_court_pattern():
    prov_alt = "|".join(PROVINCES)
    return re.compile(rf'({prov_alt})(?:省|市|自治区|自治州)?[一-龥]*?人民法院')

COURT_PATTERN = make_court_pattern()

# 另外单独把落单的省份名替换为"某省"（针对像"江苏省某县人民法院"这种前半段残留）
PROVINCE_STANDALONE = re.compile(r'(' + '|'.join(PROVINCES) + r')(省|自治区)')
CITY_STANDALONE = re.compile(r'(' + '|'.join(PROVINCES) + r')(市|自治州)')


def clean_field(text: str) -> tuple[str, dict]:
    """返回 (清洗后文本, 修改统计)"""
    if not text:
        return text, {}
    stats = {}
    orig = text

    # Fix 2: 数字粘连
    text, n = NUMBER_FUSION.subn(r'\1\2 \3', text)
    if n: stats["number_fusion"] = n

    # Fix 3: 具名法院
    text, n = COURT_PATTERN.subn('某人民法院', text)
    if n: stats["court_name"] = n

    # 单独省份/市名（在其他上下文里的残留）
    text, n = PROVINCE_STANDALONE.subn(r'某\2', text)
    if n: stats["province"] = n

    text, n = CITY_STANDALONE.subn(r'某\2', text)
    if n: stats["city"] = n

    return text, stats


def process_file(path: Path):
    if not path.exists():
        print(f"[跳过] {path} 不存在")
        return
    backup = path.with_name(path.stem + "_v1" + path.suffix)
    shutil.copy2(path, backup)
    print(f"[备份] {path.name} → {backup.name}")

    total_stats = {}
    n_records = 0
    n_modified = 0
    with open(path, "r", encoding="utf-8") as fin:
        lines = fin.readlines()
    with open(path, "w", encoding="utf-8") as fout:
        for line in lines:
            if not line.strip():
                continue
            d = json.loads(line)
            n_records += 1
            case_modified = False
            for field in ("title_masked", "case_text_masked", "judge_decision_masked"):
                new_text, stats = clean_field(d.get(field) or "")
                if stats:
                    d[field] = new_text
                    case_modified = True
                    for k, v in stats.items():
                        total_stats[k] = total_stats.get(k, 0) + v
            if case_modified:
                n_modified += 1
            fout.write(json.dumps(d, ensure_ascii=False) + "\n")

    print(f"[{path.parent.name}] {n_records} 案，其中 {n_modified} 案被修改")
    for k, v in total_stats.items():
        print(f"    {k}: {v} 处替换")


if __name__ == "__main__":
    for p in TARGETS:
        process_file(p)
