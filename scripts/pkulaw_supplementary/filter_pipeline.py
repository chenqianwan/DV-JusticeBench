"""
DV-JusticeBench 案例混合筛选：Stage1 关键词粗筛 + Stage2 模型精筛（三分类）
输出：filter_output/{stage1_kept,stage2_classified,stage2_failed}.jsonl
"""
import json
import re
import sys
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path("/aifs4su/hansirui/boqin/abuse/DV-JusticeBench")
sys.path.insert(0, str(REPO))

from utils.deepseek_api import DeepSeekAPI

CASE_FILE = REPO / "data/pkulaw_2023_2025_release/pipeline_ready_cases.json"
OUT_DIR = REPO / "data/pkulaw_filter"
OUT_DIR.mkdir(exist_ok=True, parents=True)

KEYWORDS = ("家庭暴力", "家暴")

CLASSIFY_PROMPT = """你是一位中国婚姻家事法律专家。请判断以下判决书中，"家庭暴力"是否是本案的核心法律争议之一，并且法院是否对此做了实质性法律说理。

【判断标准】
- primary：家庭暴力构成本案的核心争议之一，且法院在裁判理由中围绕该问题进行了实质分析、评价或明确认定。包括但不限于：是否构成家庭暴力、行为危险性、是否应采取人身安全保护措施、是否影响离婚过错或损害赔偿、是否影响子女抚养安排、是否构成量刑或责任加重因素等。
- secondary：家庭暴力与本案事实存在一定关联，例如作为案件背景被提及，但并非主要争议焦点，法院也未就家庭暴力的构成、法律后果或相关救济作出专门、实质性的说理。
- irrelevant：判决书虽出现"家庭暴力"或相关表述，但仅属于一般性引用、无关案件背景，或未涉及本案当事人的具体主张、证据及争议。法院未对家庭暴力事实予以评价或作出法律判断。

【辅助标记】
- court_addressed_dv: true / false（法院是否在裁判理由中对家暴做过实质分析）
- dispute_type: 涉及家暴的具体争议类型，多选：
    是否构成家暴 / 人身安全保护令 / 离婚事由 / 子女抚养影响 /
    精神/物质损害赔偿 / 刑事量刑 / 其他

【严格输出 JSON，不要额外说明】
{{
  "label": "primary" | "secondary" | "irrelevant",
  "court_addressed_dv": true | false,
  "dispute_types": [...],
  "confidence": 0.0-1.0,
  "reason": "一句话依据，20字以内"
}}

案例标题：{title}
案情：{case_text}
裁判理由：{judge_decision}
"""


def stage1_keyword(cases: dict) -> dict:
    kept = {}
    for cid, case in cases.items():
        text = case.get("title", "") + case.get("case_text", "") + case.get("judge_decision", "")
        if any(kw in text for kw in KEYWORDS):
            kept[cid] = case
    return kept


_json_pat = re.compile(r"\{.*\}", re.DOTALL)


def parse_classification(raw: str) -> dict:
    """尝试从模型输出里抠出 JSON 对象"""
    raw = raw.strip()
    # 剥掉 markdown 代码块
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


def classify_one(ds: DeepSeekAPI, cid: str, case: dict) -> dict:
    prompt = CLASSIFY_PROMPT.format(
        title=case.get("title", ""),
        case_text=case.get("case_text", ""),
        judge_decision=case.get("judge_decision", ""),
    )
    messages = [{"role": "user", "content": prompt}]
    t0 = time.time()
    resp = ds._make_request(messages, temperature=0.1, max_tokens=512, auto_retry_on_truncate=True)
    elapsed = time.time() - t0

    if not resp or "choices" not in resp:
        raise RuntimeError("empty response")

    raw = resp["choices"][0]["message"]["content"]
    result = parse_classification(raw)
    result["case_id"] = cid
    result["elapsed_s"] = round(elapsed, 2)
    return result


def stage2_classify(candidates: dict, workers: int = 30, resume: bool = True):
    out_ok = OUT_DIR / "stage2_classified.jsonl"
    out_fail = OUT_DIR / "stage2_failed.jsonl"

    done_ids = set()
    if resume and out_ok.exists():
        for line in out_ok.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    done_ids.add(json.loads(line)["case_id"])
                except Exception:
                    pass

    remaining = {cid: case for cid, case in candidates.items() if cid not in done_ids}
    print(f"[Stage2] 候选 {len(candidates)} / 已完成 {len(done_ids)} / 剩余 {len(remaining)}")

    if not remaining:
        return

    ds = DeepSeekAPI()
    write_lock = threading.Lock()
    counters = {"ok": 0, "fail": 0}
    t_start = time.time()

    def _work(cid, case):
        for attempt in range(3):
            try:
                result = classify_one(ds, cid, case)
                result["attempts"] = attempt + 1
                return ("ok", cid, result)
            except Exception as e:
                if attempt == 2:
                    return ("fail", cid, {"case_id": cid, "error": str(e)[:200], "attempts": 3})
                time.sleep(2 * (attempt + 1))

    with open(out_ok, "a", encoding="utf-8") as fok, open(out_fail, "a", encoding="utf-8") as ffail:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_work, cid, case) for cid, case in remaining.items()]
            for i, fut in enumerate(as_completed(futs), 1):
                status, cid, result = fut.result()
                with write_lock:
                    if status == "ok":
                        fok.write(json.dumps(result, ensure_ascii=False) + "\n")
                        fok.flush()
                        counters["ok"] += 1
                    else:
                        ffail.write(json.dumps(result, ensure_ascii=False) + "\n")
                        ffail.flush()
                        counters["fail"] += 1
                if i % 50 == 0 or i == len(futs):
                    elapsed = time.time() - t_start
                    rate = i / elapsed
                    eta = (len(futs) - i) / rate if rate > 0 else 0
                    print(
                        f"[Stage2] {i}/{len(futs)}  ok={counters['ok']} fail={counters['fail']}  "
                        f"{rate:.1f} req/s  eta {eta:.0f}s",
                        flush=True,
                    )

    print(f"[Stage2] 完成: ok={counters['ok']} fail={counters['fail']}")


def summarize():
    out_ok = OUT_DIR / "stage2_classified.jsonl"
    counts = {"primary": 0, "secondary": 0, "irrelevant": 0}
    court_ok = 0
    conf_ge_07 = 0
    for line in out_ok.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        d = json.loads(line)
        counts[d.get("label", "irrelevant")] = counts.get(d.get("label", "irrelevant"), 0) + 1
        if d.get("court_addressed_dv"):
            court_ok += 1
        if d.get("confidence", 0) >= 0.7:
            conf_ge_07 += 1
    total = sum(counts.values())
    print("\n=== Stage2 结果汇总 ===")
    for k, v in counts.items():
        print(f"  {k:12s}: {v:5d}  ({v/total*100:.1f}%)")
    print(f"  court_addressed_dv=true: {court_ok}")
    print(f"  confidence >= 0.7:       {conf_ge_07}")


def main(mode="all", pilot_n=20):
    print(f"[加载] {CASE_FILE}")
    cases = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    print(f"[总案例] {len(cases)}")

    # Stage 1
    print("\n=== Stage 1: 关键词粗筛 ===")
    kept = stage1_keyword(cases)
    print(f"关键词命中: {len(kept)}  (剔除 {len(cases) - len(kept)})")
    stage1_out = OUT_DIR / "stage1_kept_case_ids.json"
    stage1_out.write_text(json.dumps(sorted(kept.keys()), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"[保存] {stage1_out}")

    # Stage 2
    print("\n=== Stage 2: 模型三分类 ===")
    if mode == "pilot":
        pilot = dict(list(kept.items())[:pilot_n])
        print(f"pilot 模式，只跑前 {pilot_n} 案")
        stage2_classify(pilot, workers=min(pilot_n, 20), resume=False)
    else:
        stage2_classify(kept, workers=40)

    summarize()


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "all"
    main(mode)
