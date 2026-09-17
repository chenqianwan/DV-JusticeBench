"""用 fix4 masked case_text 重跑 2248 案的出题"""
import json
import shutil
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

REPO = Path("/aifs4su/hansirui/boqin/abuse/DV-JusticeBench")
sys.path.insert(0, str(REPO))

from utils.deepseek_api import DeepSeekAPI


def process_dir(mask_path: Path, questions_path: Path, workers: int = 25):
    if questions_path.exists():
        backup = questions_path.with_name(questions_path.stem + "_v1" + questions_path.suffix)
        shutil.copy2(questions_path, backup)
        print(f"[备份] {questions_path.name} → {backup.name}")

    masked = [json.loads(l) for l in open(mask_path) if l.strip()]
    print(f"[目标] {len(masked)} 案 in {mask_path.parent.name}")

    ds = DeepSeekAPI()
    lock = threading.Lock()
    counters = {"ok": 0, "fail": 0}
    t_start = time.time()

    def _work(m):
        cid = m["case_id"]
        for attempt in range(3):
            try:
                t0 = time.time()
                questions = ds.generate_questions(m["case_text_masked"] or "", num_questions=5)
                if len(questions) < 5:
                    raise RuntimeError(f"only got {len(questions)} questions")
                elapsed = time.time() - t0
                return ("ok", cid, {
                    "case_id": cid,
                    "questions": [{"question_id": f"{cid}_q{i+1}", "question": q} for i, q in enumerate(questions[:5])],
                    "t_q_s": round(elapsed, 1),
                    "attempts": attempt + 1,
                })
            except Exception as e:
                if attempt == 2:
                    return ("fail", cid, {"case_id": cid, "error": str(e)[:200], "attempts": 3})
                time.sleep(2 * (attempt + 1))

    with open(questions_path, "w", encoding="utf-8") as fout:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futs = [ex.submit(_work, m) for m in masked]
            for i, fut in enumerate(as_completed(futs), 1):
                status, cid, result = fut.result()
                with lock:
                    if status == "ok":
                        fout.write(json.dumps(result, ensure_ascii=False) + "\n")
                        fout.flush()
                        counters["ok"] += 1
                    else:
                        counters["fail"] += 1
                if i % 50 == 0 or i == len(futs):
                    elapsed = time.time() - t_start
                    rate = i / elapsed
                    eta = (len(futs) - i) / rate if rate > 0 else 0
                    print(f"  {i}/{len(futs)} ok={counters['ok']} fail={counters['fail']} "
                          f"{rate*60:.1f}/min eta {eta/60:.1f}min", flush=True)

    print(f"[{mask_path.parent.name}] 完成: ok={counters['ok']} fail={counters['fail']} "
          f"总耗时 {(time.time()-t_start)/60:.1f} min")


def main():
    process_dir(
        REPO / "data/pkulaw_filter/prepare_output/masked_cases_fix4.jsonl",
        REPO / "data/pkulaw_filter/prepare_output/questions_fix4.jsonl",
        workers=25,
    )
    process_dir(
        REPO / "data/pkulaw_filter/prepare_output_secondary/masked_cases_fix4.jsonl",
        REPO / "data/pkulaw_filter/prepare_output_secondary/questions_fix4.jsonl",
        workers=25,
    )


if __name__ == "__main__":
    main()
