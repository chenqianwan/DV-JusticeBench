"""Fix 4: 用专家审阅版的合并脱敏 prompt，一次调用脱敏三个字段
输入: pipeline_ready_cases.json (原文) + 已完成 masked_cases.jsonl 里的 case_id 清单
输出: masked_cases_v3.jsonl（新，含 person_map；旧文件会被备份）
"""
import hashlib
import json
import re
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path("/aifs4su/hansirui/boqin/abuse/DV-JusticeBench")
sys.path.insert(0, str(REPO))

from utils.deepseek_api import DeepSeekAPI

CASE_FILE = REPO / "data/pkulaw_2023_2025_release/pipeline_ready_cases.json"
MAX_CASE_TEXT = 8000  # 与阶段一保持一致

# 专家审阅版 prompt
UNIFIED_MASK_PROMPT = """你是一位中文法律文书脱敏专家。请对以下同一个案件的三个字段（标题、案情、裁判理由）进行统一脱敏处理，并保证三个字段之间的编号与身份完全一致。

【统一脱敏规则】

1. 人名替换（跨字段一致）
   - 所有真实人名替换为"某男N"或"某女N"（N 从 1 开始编号，保留性别）。
   - **同一个自然人在三个字段中必须使用相同编号**。请在处理前先通读三个字段，为每个人分配唯一编号，再统一替换。
   - 若原文只出现"某"或"某某"这种已经半匿名的人名，也要重新给编号（如"曹某"→"某男1"）。
   - 性别判断：能够明确指向该自然人的亲属或身份关系，如"丈夫""妻子""父亲""母亲""儿子""女儿""男方""女方"等；优先根据文中语境（如"母亲""丈夫""坐月子""婚生子""生育""孕育"等）推断，不要仅凭姓名猜测；能够明确指向该自然人的生理或生育语境，如"怀孕""分娩""坐月子"等，但仅在该词明确以该自然人为行为或状态主体时使用；若语境不足，参考名字最后一个字的常见性别（无法判断时用"某人N"）。
   - 保留角色标签：如"原告某女1"、"被告某男1"、"审判长某男2"。
   - **原被告身份不可颠倒**：以案情"当事人"或"审理经过"段落里的角色标注为准，裁判理由中的表述必须与之对齐。原告、被告、上诉人、被上诉人、申请人、被申请人等程序角色应按照原文对应诉讼阶段保留。

2. 地名替换
   - 省级："北京市"、"广东省"、"内蒙古自治区" 等 → "某省"
   - 市级："上海市黄浦区"、"深圳市南山区" → "某市某区"
   - 具体法院："北京市第二中级人民法院"、"广东省东莞市第一人民法院" → **整体替换为"某人民法院"**（保留级别信息）
   - 街道、小区、门牌号 → "某路"、"某小区"、"某号"

3. 时间替换
   - 具体年月日、时分秒 → "某年"、"某月"、"某日"、"某时"、"某分"
   - 案件编号"（2024）京行终6147号" → "（某年）某号"
   - 文书编号"海政复决字〔2023〕314号" → "某字〔某年〕某号"

4. 敏感信息
   - 身份证号、电话号码 → "XXX"
   - 网址 → 直接删除

5. 金额与数字（严禁脱敏）
   - 所有货币、赔偿、抚养费、诉讼费等数字**完整保留**。
   - **重要**：人物编号与相邻金额之间必须留空格或标点，防止粘连。例如："某女1"后紧跟"65000元"必须写成 "某女1 65000元" 或 "某女1向某男2支付 65000 元"，禁止连成 "某女165000元"。

6. 其他
   - 保留法律术语、条文引用、程序结构，不改动逻辑。
   - 民族（汉族、朝鲜族、回族等）保留。
   - 不添加任何说明或注释。

【输出格式】

严格输出如下 JSON（不要 markdown 代码块，不要额外说明）：

{{
  "person_map": {{
    "曹某": "某男1",
    "田某某": "某女1",
    "余忆频": "某男2"
  }},
  "title_masked": "...",
  "case_text_masked": "...",
  "judge_decision_masked": "..."
}}

person_map 用于审计，包含所有识别到的人名及其分配的编号。

【输入】

标题:
{title}

案情:
{case_text}

裁判理由:
{judge_decision}
"""


_json_pat = re.compile(r"\{.*\}", re.DOTALL)


def parse_mask_json(raw: str) -> dict:
    raw = raw.strip()
    if raw.startswith("```"):
        raw = re.sub(r"^```(?:json)?\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        m = _json_pat.search(raw)
        if m:
            return json.loads(m.group(0))
        raise


def sha256(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def mask_one(ds: DeepSeekAPI, cid: str, case: dict) -> dict:
    title = case.get("title", "")
    case_text = case.get("case_text", "")
    judge = case.get("judge_decision", "")
    orig_len = len(case_text)
    truncated = orig_len > MAX_CASE_TEXT
    if truncated:
        case_text = case_text[:MAX_CASE_TEXT]

    prompt = UNIFIED_MASK_PROMPT.format(
        title=title, case_text=case_text, judge_decision=judge
    )
    messages = [{"role": "user", "content": prompt}]
    t0 = time.time()
    resp = ds._make_request(messages, temperature=0.3, max_tokens=8000, auto_retry_on_truncate=True)
    elapsed = time.time() - t0
    if not resp or "choices" not in resp:
        raise RuntimeError("empty response")
    raw = resp["choices"][0]["message"]["content"]
    parsed = parse_mask_json(raw)

    for k in ("title_masked", "case_text_masked", "judge_decision_masked"):
        if k not in parsed:
            raise RuntimeError(f"missing field: {k}")

    return {
        "case_id": cid,
        "title_masked": parsed["title_masked"],
        "case_text_masked": parsed["case_text_masked"],
        "judge_decision_masked": parsed["judge_decision_masked"],
        "person_map": parsed.get("person_map", {}),
        "case_text_truncated_from": orig_len if truncated else None,
        "source_title_hash": sha256(title),
        "source_case_text_hash": sha256(case_text),
        "source_judge_decision_hash": sha256(judge),
        "t_mask_s": round(elapsed, 1),
    }


def load_done(path: Path) -> set:
    if not path.exists():
        return set()
    return {json.loads(l)["case_id"] for l in open(path) if l.strip()}


def process_dir(name: str, existing_masked: Path, out_masked: Path,
                out_failures: Path, out_checkpoint: Path, workers: int = 20):
    """基于旧 masked_cases.jsonl 里的 case_id 清单跑 Fix 4"""
    print(f"\n=== {name} ===")
    target_ids = sorted({json.loads(l)["case_id"] for l in open(existing_masked)})
    done_ids = load_done(out_masked)
    remaining = [cid for cid in target_ids if cid not in done_ids]
    print(f"目标 {len(target_ids)} / 已完成 {len(done_ids)} / 剩余 {len(remaining)}", flush=True)

    if not remaining:
        return

    cases = json.load(open(CASE_FILE))
    ds = DeepSeekAPI()
    lock = threading.Lock()
    counters = {"ok": 0, "fail": 0}
    t_start = time.time()

    def _work(cid):
        for attempt in range(3):
            try:
                r = mask_one(ds, cid, cases[cid])
                r["attempts"] = attempt + 1
                return ("ok", cid, r)
            except Exception as e:
                if attempt == 2:
                    return ("fail", cid, {"case_id": cid, "error": str(e)[:200], "attempts": 3})
                time.sleep(2 * (attempt + 1))

    with open(out_masked, "a", encoding="utf-8") as fmask, \
         open(out_failures, "a", encoding="utf-8") as ffail:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_work, cid) for cid in remaining]
            for i, fut in enumerate(as_completed(futs), 1):
                status, cid, r = fut.result()
                with lock:
                    if status == "ok":
                        fmask.write(json.dumps(r, ensure_ascii=False) + "\n")
                        fmask.flush()
                        counters["ok"] += 1
                    else:
                        ffail.write(json.dumps(r, ensure_ascii=False) + "\n")
                        ffail.flush()
                        counters["fail"] += 1
                if i % 25 == 0 or i == len(futs):
                    elapsed = time.time() - t_start
                    rate = i / elapsed
                    eta = (len(futs) - i) / rate if rate > 0 else 0
                    print(f"  {i}/{len(futs)} ok={counters['ok']} fail={counters['fail']} "
                          f"{rate*60:.1f}/min eta {eta/60:.1f}min", flush=True)

    total = len(done_ids) + counters["ok"]
    ck = {
        "state": "paused_before_answering",
        "prepared_case_count": total,
        "failed_case_count": counters["fail"],
        "prompt_version": "fix4_expert_v1",
        "unified_masking": True,
        "updated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    out_checkpoint.write_text(json.dumps(ck, ensure_ascii=False, indent=2), encoding="utf-8")


def smoke(n: int = 3):
    """从 primary 抽 n 案跑一遍，验证解析和输出正确"""
    cases = json.load(open(CASE_FILE))
    primary_ids = [json.loads(l)["case_id"] for l in open(REPO / "data/pkulaw_filter/prepare_output/masked_cases.jsonl")][:n]
    ds = DeepSeekAPI()
    for cid in primary_ids:
        print(f"\n--- {cid} ---")
        try:
            r = mask_one(ds, cid, cases[cid])
            print(f"person_map: {r['person_map']}")
            print(f"title:    {r['title_masked'][:80]}")
            print(f"case[..200]: {r['case_text_masked'][:200]}")
            print(f"judge[..200]: {r['judge_decision_masked'][:200]}")
            print(f"耗时: {r['t_mask_s']}s")
        except Exception as e:
            print(f"FAILED: {e}")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "smoke"
    if mode == "smoke":
        smoke(3)
        return
    # 全量
    for src_dir, name in [
        ("prepare_output", "primary"),
        ("prepare_output_secondary", "secondary"),
    ]:
        base = REPO / f"data/pkulaw_filter/{src_dir}"
        old = base / "masked_cases.jsonl"
        new = base / "masked_cases_fix4.jsonl"
        fail = base / "failures_fix4.jsonl"
        ck = base / "checkpoint_fix4.json"
        process_dir(name, old, new, fail, ck, workers=int(sys.argv[2]) if len(sys.argv) > 2 else 20)


if __name__ == "__main__":
    main()
