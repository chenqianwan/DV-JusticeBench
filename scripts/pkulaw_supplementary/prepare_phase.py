"""
Prepare 阶段：对 231 案（放宽档筛选集）做 DS 脱敏 + 出题
- 输入: data/pkulaw_filter/stage2_classified.jsonl (取 primary + secondary&court_dv=true)
- 输出:
    data/pkulaw_filter/prepare_output/masked_cases.jsonl
    data/pkulaw_filter/prepare_output/questions.jsonl
    data/pkulaw_filter/prepare_output/failures.jsonl
    data/pkulaw_filter/prepare_output/checkpoint.json
- 完成后写 paused_before_answering 检查点，不自动进入作答阶段
"""
import hashlib
import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path("/aifs4su/hansirui/boqin/abuse/DV-JusticeBench")
sys.path.insert(0, str(REPO))

from utils.data_masking import DataMaskerAPI
from utils.deepseek_api import DeepSeekAPI

CASE_FILE = REPO / "data/pkulaw_2023_2025_release/pipeline_ready_cases.json"
CLASSIFIED = REPO / "data/pkulaw_filter/stage2_classified.jsonl"
OUT_DIR = REPO / "data/pkulaw_filter/prepare_output"
OUT_DIR.mkdir(parents=True, exist_ok=True)

MASKED_JSONL = OUT_DIR / "masked_cases.jsonl"
QUESTIONS_JSONL = OUT_DIR / "questions.jsonl"
FAILURES_JSONL = OUT_DIR / "failures.jsonl"
CHECKPOINT = OUT_DIR / "checkpoint.json"


def load_target_case_ids() -> list:
    """放宽档：primary + (secondary AND court_addressed_dv=true)"""
    records = [json.loads(l) for l in open(CLASSIFIED).read().splitlines() if l.strip()]
    target = [
        r["case_id"] for r in records
        if r.get("label") == "primary"
        or (r.get("label") == "secondary" and r.get("court_addressed_dv"))
    ]
    return sorted(target)


def sha256(s: str) -> str:
    return hashlib.sha256((s or "").encode("utf-8")).hexdigest()


def load_done_ids(path: Path) -> set:
    if not path.exists():
        return set()
    done = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            try:
                done.add(json.loads(line)["case_id"])
            except Exception:
                pass
    return done


def process_case(masker: DataMaskerAPI, ds: DeepSeekAPI, cid: str, case: dict) -> dict:
    """脱敏 3 字段 + 生成 5 题，返回聚合结果"""
    t0 = time.time()
    masked = masker.mask_case_with_api(case)
    t_mask = time.time() - t0

    # 兜底：脱敏失败时抛异常让重试逻辑接管
    if masked.get("case_text_masked") is None or masked.get("judge_decision_masked") is None:
        raise RuntimeError("masking returned None")

    t1 = time.time()
    questions = ds.generate_questions(
        case_text=masked["case_text_masked"],
        num_questions=5,
    )
    t_q = time.time() - t1

    if len(questions) < 5:
        raise RuntimeError(f"only got {len(questions)} questions")

    return {
        "masked": {
            "case_id": cid,
            "title_masked": masked.get("title_masked"),
            "case_text_masked": masked.get("case_text_masked"),
            "judge_decision_masked": masked.get("judge_decision_masked"),
            "source_title_hash": sha256(case.get("title", "")),
            "source_case_text_hash": sha256(case.get("case_text", "")),
            "source_judge_decision_hash": sha256(case.get("judge_decision", "")),
            "t_mask_s": round(t_mask, 1),
        },
        "questions": {
            "case_id": cid,
            "question_source_mask_hash": sha256(masked["case_text_masked"]),
            "questions": [
                {"question_id": f"{cid}_q{i+1}", "question": q}
                for i, q in enumerate(questions[:5])
            ],
            "t_q_s": round(t_q, 1),
        },
    }


def main(workers: int = 30, limit: int | None = None):
    cases = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    target_ids = load_target_case_ids()
    if limit:
        target_ids = target_ids[:limit]
    print(f"[目标] {len(target_ids)} 案 (放宽档筛选)")

    # 恢复：已成功的跳过
    done_ids = load_done_ids(MASKED_JSONL) & load_done_ids(QUESTIONS_JSONL)
    remaining = [cid for cid in target_ids if cid not in done_ids]
    print(f"[已完成] {len(done_ids)}  [剩余] {len(remaining)}")

    if not remaining:
        write_checkpoint(target_ids, len(done_ids), 0)
        return

    masker = DataMaskerAPI()
    ds = DeepSeekAPI()
    lock = threading.Lock()
    counters = {"ok": 0, "fail": 0}
    t_start = time.time()

    def _work(cid: str):
        for attempt in range(3):
            try:
                result = process_case(masker, ds, cid, cases[cid])
                result["attempts"] = attempt + 1
                return ("ok", cid, result)
            except Exception as e:
                if attempt == 2:
                    return ("fail", cid, {"case_id": cid, "error": str(e)[:200], "attempts": 3})
                time.sleep(2 * (attempt + 1))

    with open(MASKED_JSONL, "a", encoding="utf-8") as f_mask, \
         open(QUESTIONS_JSONL, "a", encoding="utf-8") as f_q, \
         open(FAILURES_JSONL, "a", encoding="utf-8") as f_fail:

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
                if i % 10 == 0 or i == len(futs):
                    elapsed = time.time() - t_start
                    rate = i / elapsed
                    eta = (len(futs) - i) / rate if rate > 0 else 0
                    print(f"[Prepare] {i}/{len(futs)} ok={counters['ok']} fail={counters['fail']} "
                          f"{rate*60:.1f} case/min  eta {eta/60:.1f} min", flush=True)

    total_done = len(done_ids) + counters["ok"]
    write_checkpoint(target_ids, total_done, counters["fail"])


def write_checkpoint(target_ids, done, failed):
    ck = {
        "state": "paused_before_answering",
        "target_case_count": len(target_ids),
        "prepared_case_count": done,
        "failed_case_count": failed,
        "filter_criteria": "primary OR (secondary AND court_addressed_dv=true)",
        "outputs": {
            "masked_cases": str(MASKED_JSONL.relative_to(REPO)),
            "questions": str(QUESTIONS_JSONL.relative_to(REPO)),
            "failures": str(FAILURES_JSONL.relative_to(REPO)),
        },
        "next_action": "wait for owner to release paused_before_answering; DO NOT auto-start answering",
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    CHECKPOINT.write_text(json.dumps(ck, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\n[Checkpoint] {CHECKPOINT}")
    print(json.dumps(ck, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    workers = int(sys.argv[1]) if len(sys.argv) > 1 else 30
    limit = int(sys.argv[2]) if len(sys.argv) > 2 else None
    main(workers=workers, limit=limit)
