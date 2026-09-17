"""对 2170 剩余 secondary 案跑 prepare 阶段
- case_text 上限 8000 字（覆盖 42 个超长案）
- 输出到独立目录 prepare_output_secondary/
"""
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path("/aifs4su/hansirui/boqin/abuse/DV-JusticeBench")
sys.path.insert(0, str(REPO))
sys.path.insert(0, "/tmp/dvj-smoke")

from prepare_phase import process_case
from utils.data_masking import DataMaskerAPI
from utils.deepseek_api import DeepSeekAPI

CASE_FILE = REPO / "data/pkulaw_2023_2025_release/pipeline_ready_cases.json"
PRIMARY_MASKED = REPO / "data/pkulaw_filter/prepare_output/masked_cases.jsonl"
SECONDARY_SRC = REPO / "data/pkulaw_filter/secondary_cases.json"

OUT_DIR = REPO / "data/pkulaw_filter/prepare_output_secondary"
OUT_DIR.mkdir(parents=True, exist_ok=True)
MASKED = OUT_DIR / "masked_cases.jsonl"
QUESTIONS = OUT_DIR / "questions.jsonl"
FAILURES = OUT_DIR / "failures.jsonl"
CHECKPOINT = OUT_DIR / "checkpoint.json"

MAX_CASE_TEXT = 8000


def load_done(path: Path) -> set:
    if not path.exists():
        return set()
    return {json.loads(l)["case_id"] for l in open(path) if l.strip()}


def main(workers: int = 20):
    cases = json.load(open(CASE_FILE))
    secondary = json.load(open(SECONDARY_SRC))
    primary_done = load_done(PRIMARY_MASKED)

    targets = [cid for cid in secondary if cid not in primary_done]
    done_this = load_done(MASKED)
    remaining = [cid for cid in targets if cid not in done_this]
    print(f"[secondary] 总 {len(secondary)} / primary已完成 {len(primary_done & set(secondary))} / "
          f"本目录已完成 {len(done_this)} / 剩余 {len(remaining)}", flush=True)

    masker = DataMaskerAPI()
    ds = DeepSeekAPI()
    lock = threading.Lock()
    counters = {"ok": 0, "fail": 0}
    t_start = time.time()

    def _work(cid: str):
        case = dict(cases[cid])
        orig_len = len(case.get("case_text", ""))
        truncated = orig_len > MAX_CASE_TEXT
        if truncated:
            case["case_text"] = case["case_text"][:MAX_CASE_TEXT]
        for attempt in range(3):
            try:
                result = process_case(masker, ds, cid, case)
                if truncated:
                    result["masked"]["case_text_truncated_from"] = orig_len
                result["attempts"] = attempt + 1
                return ("ok", cid, result)
            except Exception as e:
                if attempt == 2:
                    return ("fail", cid, {"case_id": cid, "error": str(e)[:200], "attempts": 3})
                time.sleep(2 * (attempt + 1))

    with open(MASKED, "a", encoding="utf-8") as f_mask, \
         open(QUESTIONS, "a", encoding="utf-8") as f_q, \
         open(FAILURES, "a", encoding="utf-8") as f_fail:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_work, cid) for cid in remaining]
            for i, fut in enumerate(as_completed(futs), 1):
                status, cid, result = fut.result()
                with lock:
                    if status == "ok":
                        f_mask.write(json.dumps(result["masked"], ensure_ascii=False) + "\n")
                        f_mask.flush()
                        f_q.write(json.dumps(result["questions"], ensure_ascii=False) + "\n")
                        f_q.flush()
                        counters["ok"] += 1
                    else:
                        f_fail.write(json.dumps(result, ensure_ascii=False) + "\n")
                        f_fail.flush()
                        counters["fail"] += 1
                if i % 25 == 0 or i == len(futs):
                    elapsed = time.time() - t_start
                    rate = i / elapsed
                    eta = (len(futs) - i) / rate if rate > 0 else 0
                    print(f"[secondary] {i}/{len(futs)} ok={counters['ok']} fail={counters['fail']} "
                          f"{rate*60:.1f} case/min  eta {eta/60:.1f} min", flush=True)

    total_done = len(done_this) + counters["ok"]
    ck = {
        "state": "paused_before_answering",
        "target_case_count": len(targets),
        "prepared_case_count": total_done,
        "failed_case_count": counters["fail"],
        "filter_criteria": "label=secondary (excluding those already in primary run)",
        "case_text_max_chars": MAX_CASE_TEXT,
        "outputs": {
            "masked_cases": str(MASKED.relative_to(REPO)),
            "questions": str(QUESTIONS.relative_to(REPO)),
            "failures": str(FAILURES.relative_to(REPO)),
        },
        "next_action": "wait for owner to release paused_before_answering; DO NOT auto-start answering",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    CHECKPOINT.write_text(json.dumps(ck, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[Checkpoint] prepared={total_done}, failed={counters['fail']}")


if __name__ == "__main__":
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    main(workers=workers)
