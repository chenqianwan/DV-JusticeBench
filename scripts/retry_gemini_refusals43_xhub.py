#!/usr/bin/env python3
"""One bounded retry batch for 43 frozen Gemini policy-refusal responses.

Scoring and request/retry functions are reused unchanged. Original ratings stay
untouched. Audit outcomes distinguish returned ratings from legacy 'success'
records that merely contain refusal text and default zero scores.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import fcntl
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections import Counter
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_experiment1_smoke_xhub as legacy_transport
from scripts.cross_judge_budget_guard import GuardedTransport
from scripts.run_cross_family_judging_sample20_xhub import (
    DIMENSIONS, atomic_write_json, file_sha256, load_json, now_iso, run_new_task,
    sha256_text,
)
from scripts.run_cross_judging_full108_xhub import OUT, frozen_hashes, rating_path

PREFIX = 'The prompt could not be submitted. The prompt contains sensitive words'
DEFAULT_RETRY = OUT / 'gemini_refusal_retry43_20260908'


def classify(result):
    """Audit only; never alters the legacy status, score, parser, or retries."""
    if result.get('status') != 'success':
        return 'request_failed'
    detail = str((result.get('evaluation') or {}).get('详细评价') or '')
    if detail.startswith(PREFIX):
        return 'still_refused'
    # This is a structural audit, not a claim that legal reasoning is correct.
    normalized = detail.replace('**', '')
    if all(re.search(rf'(?:【{re.escape(d)}】\s*(?:得分|评分)?|{re.escape(d)}\s*(?:得分|评分))'
                     r'\s*[：:]?\s*[0-4](?:\.\d+)?\s*分', normalized) for d in DIMENSIONS):
        return 'rating_returned'
    return 'unstructured_response_needs_review'


def prepare(retry_out):
    source_manifest = load_json(OUT / 'manifest_ready88.json')
    assert source_manifest['hashes'] == frozen_hashes(), 'Frozen scoring code changed'
    source_answers = load_json(OUT / 'source_answers_ready88.json')
    lookup = {a['answer_id']: a for a in source_answers}
    tasks, answers, originals = [], {}, {}
    for original_task in source_manifest['tasks']:
        if original_task['judge_label'] != 'Gemini 2.5 Flash':
            continue
        path = rating_path(OUT, original_task)
        result = load_json(path)
        detail = str((result.get('evaluation') or {}).get('详细评价') or '')
        if not detail.startswith(PREFIX):
            continue
        assert result['status'] == 'success'
        assert result['evaluation']['总分'] == 0
        answer = lookup[original_task['answer_id']]
        assert answer['answer_family'] != 'Gemini'
        assert original_task['input_hash'] == result['input_hash'] == answer['input_hash']
        for field, digest in [('answer','answer_sha256'), ('question','question_sha256'),
                              ('masked_case_text','masked_case_sha256'), ('masked_judgment','masked_judgment_sha256')]:
            assert sha256_text(answer[field]) == original_task[digest] == result[digest]
        task = dict(original_task, source='new_api_refusal_retry',
                    original_source=original_task['source'], original_rating_path=str(path),
                    original_rating_sha256=file_sha256(path))
        tasks.append(task)
        answers[answer['answer_id']] = answer
        originals[task['task_id']] = result
    assert len(tasks) == 43
    assert sha256_text('\n'.join(sorted(t['task_id'] for t in tasks))) == 'd2dc42cbec58fe772e177b30b33041776a90bf76401d39c89d335f5985e4da92'
    assert Counter(t['original_source'] for t in tasks) == {'new_api':29, 'reused_cross40':14}
    manifest = dict(created_at=now_iso(), task_count=43, unique_answer_count=len(answers),
                    source_counts=dict(Counter(t['original_source'] for t in tasks)),
                    selection='Exact previously observed policy-refusal template, not score-based selection',
                    hashes=source_manifest['hashes'], parameters=source_manifest['parameters'],
                    shared_budget_ledger=str(OUT/'budget_ledger.json'),
                    original_ratings_overwritten=False, legacy_scoring_unchanged=True, tasks=tasks)
    path = retry_out/'manifest.json'
    if path.exists():
        previous=load_json(path)
        assert previous['tasks'] == tasks and previous['hashes'] == manifest['hashes']
        manifest=previous
    else:
        atomic_write_json(path,manifest)
        atomic_write_json(retry_out/'source_answers.json',list(answers.values()))
        for tid,result in originals.items():
            atomic_write_json(retry_out/'original_ratings'/f'{tid}.json',result)
    return manifest,answers


def completed(path, task):
    if not path.exists():
        return False
    result=load_json(path)
    return (result.get('task_id')==task['task_id'] and result.get('answer_sha256')==task['answer_sha256']
            and result.get('retry_outcome') is not None)


def snapshot(retry_out, manifest, phase, budget=None):
    counts=Counter(); records=[]
    for task in manifest['tasks']:
        path=retry_out/'ratings'/f"{task['task_id']}.json"
        if completed(path,task):
            result=load_json(path); outcome=result['retry_outcome']; counts[outcome]+=1
            records.append(dict(task_id=task['task_id'],answer_id=task['answer_id'],
                                original_rating_path=task['original_rating_path'],
                                retry_rating_path=str(path),retry_outcome=outcome,
                                legacy_status=result['status'],legacy_total=(result.get('evaluation') or {}).get('总分')))
        else:
            counts['pending']+=1
    result=dict(updated_at=now_iso(),pid=os.getpid(),phase=phase,total=43,
                completed=43-counts['pending'],
                **{k:counts[k] for k in ['rating_returned','still_refused','request_failed',
                                        'unstructured_response_needs_review','pending']},
                shared_budget=budget,original_records_unchanged=True)
    atomic_write_json(retry_out/'state.json',result)
    atomic_write_json(retry_out/'retry_results_index.json',dict(state=result,ratings=records,
        note='Separate retry version; raw originals and main combined_results_ready88.json remain unchanged.'))
    return result


def main():
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--detach',action='store_true')
    ap.add_argument('--workers',type=int,default=16)
    ap.add_argument('--retry-output',type=Path,default=DEFAULT_RETRY)
    ap.add_argument('--xhub-env',type=Path,default=ROOT.parent/'AI_Council/.env')
    args=ap.parse_args()
    if not 1<=args.workers<=16:ap.error('workers must be 1..16')
    out=args.retry_output.resolve();out.mkdir(parents=True,exist_ok=True)
    if args.detach:
        if not args.execute:ap.error('--detach requires --execute')
        argv=[sys.executable,str(Path(__file__).resolve())]+[x for x in sys.argv[1:] if x!='--detach']
        with (out/'run.log').open('ab',buffering=0) as log:
            proc=subprocess.Popen(argv,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        print(json.dumps(dict(spawned_pid=proc.pid,log=str(out/'run.log'))));return 0
    # Same lock and same budget ledger as all prior full108 calls; no double cap.
    with (OUT/'run.lock').open('a+') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise SystemExit('Another judging process holds the shared run lock.')
        manifest,answers=prepare(out)
        if not args.execute:
            print(json.dumps(snapshot(out,manifest,'prepared'),ensure_ascii=False));return 0
        cfg=dotenv_values(args.xhub_env);market=load_json(OUT/'market_snapshot.json')
        guard=GuardedTransport(cfg['UNIFIED_API_BASE_URL'].rstrip('/'),cfg['UNIFIED_API_KEY'],OUT,
                              market['public_default_group_prices_usd_per_million'],market['quota_per_unit'],
                              110,prepaid_usd=0.929528)
        legacy_transport.requests=guard
        client=legacy_transport.XHubClient(cfg['UNIFIED_API_KEY'],cfg['UNIFIED_API_BASE_URL'].rstrip('/'),timeout=180)
        before=guard.snapshot()
        attempt=dict(started_at=now_iso(),pid=os.getpid(),next_http_id=before['next_id'],
                     list_estimated_usd_before=before['estimated_all_http_usd'],
                     unknown_upper_usd_before=before['unknown_upper_usd'],
                     observed_debit_usd_before=before['observed_debit_usd'])
        atomic_write_json(out/'runs'/f'{os.getpid()}.json',attempt)
        stop=threading.Event()
        for sig in (signal.SIGTERM,signal.SIGINT):signal.signal(sig,lambda *_:stop.set())
        tasks=[t for t in manifest['tasks'] if not completed(out/'ratings'/f"{t['task_id']}.json",t)]
        iterator=iter(tasks);active={};last_snapshot=time.monotonic()
        def worker(task):
            result=run_new_task(task,answers[task['answer_id']],client)
            result['retry_outcome']=classify(result)
            atomic_write_json(out/'ratings'/f"{task['task_id']}.json",result)
            return result
        state=snapshot(out,manifest,'running',guard.snapshot())
        print(json.dumps(dict(event='started',pending=len(tasks),state=state),ensure_ascii=False),flush=True)
        with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            def dispatch():
                while len(active)<args.workers and not stop.is_set() and not guard.stopped.is_set():
                    task=next(iterator,None)
                    if task is None:break
                    active[pool.submit(worker,task)]=task
            dispatch()
            while active:
                done,_=futures.wait(active,timeout=10,return_when=futures.FIRST_COMPLETED)
                for future in done:
                    task=active.pop(future)
                    try:result=future.result()
                    except Exception as exc:
                        result=dict(task,status='failed',completed_at=now_iso(),retry_outcome='request_failed',
                                    error=(type(exc).__name__+': '+str(exc)).replace(cfg['UNIFIED_API_KEY'],'[REDACTED]'))
                        atomic_write_json(out/'ratings'/f"{task['task_id']}.json",result)
                    print(json.dumps(dict(event='rating',time=now_iso(),task_id=task['task_id'],
                                          outcome=result['retry_outcome'],legacy_status=result['status']),ensure_ascii=False),flush=True)
                dispatch()
                if done or time.monotonic()-last_snapshot>=60:
                    snapshot(out,manifest,'running',guard.snapshot());last_snapshot=time.monotonic()
        guard.refresh_balance()
        phase='budget_paused' if guard.stopped.is_set() else 'interrupted' if stop.is_set() else 'retry_batch_finished'
        state=snapshot(out,manifest,phase,guard.snapshot())
        print(json.dumps(dict(event='finished',state=state),ensure_ascii=False),flush=True)
        return 0 if state['rating_returned']==43 else 2


if __name__=='__main__':
    raise SystemExit(main())
