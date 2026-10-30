#!/usr/bin/env python3
"""Resume final-stage cross-family judging; reuse historical scores and inputs.

No questions, answers or masked text are generated. The legacy evaluation and
HTTP retry code are imported unchanged. Missing historical masked inputs are
explicitly excluded from dispatch, never replaced by original case text.
"""
from __future__ import annotations

import argparse
import concurrent.futures as futures
import fcntl
import inspect
import json
import os
import signal
import statistics
import subprocess
import sys
import threading
import time
from collections import Counter, defaultdict
from pathlib import Path

from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_experiment1_smoke_xhub as legacy_transport
from scripts.cross_judge_budget_guard import GuardedTransport
from scripts.run_cross_family_judging_sample20_xhub import (
    ANSWER_FAMILY, JUDGES, DIMENSIONS, answer_id, task_id, atomic_write_json,
    load_json, now_iso, sha256_text, file_sha256, make_reused_result,
    public_answer, result_is_valid, run_new_task,
)
from utils.evaluator import AnswerEvaluator

OUT = ROOT / 'data/experiment1_cross_judge_full108_xhub_20260908'
OLD40 = ROOT / 'data/experiment1_cross_judge_sample40_xhub_20260822/combined_results.json'
SOURCES = [ROOT / 'data/experiment1_additional20_xhub_20260819/combined_results.json',
           ROOT / 'data/experiment1_remaining68_xhub_20260821/combined_results.json']
PILOT_MARKET = ROOT / 'data/cross_judge_budget_pilot_20260908/market_snapshot.json'


def read_sources():
    old = load_json(OLD40)
    answers = {}
    for source in SOURCES:
        for cid, raw_name in load_json(source)['per_case_raw_results'].items():
            raw = load_json(Path(raw_name))
            for row in raw['rows']:
                condition = row['condition']
                inp = raw['input']['condition_inputs'][condition]
                qn = int(row['question_number'])
                aid = answer_id(cid, qn, condition)
                a = dict(answer_id=aid, case_id=cid, case_title=raw['input'].get('case_title', ''),
                         masked_title=inp.get('masked_title', ''), question_number=qn,
                         question=row['question'], answer_condition=condition,
                         answer_family=ANSWER_FAMILY[condition],
                         answer_operational_model_id=row.get('operational_model_id', ''),
                         answer=row['generation']['content'], masked_case_text=inp['masked_case_text'],
                         masked_judgment=inp['masked_judgment'], input_hash=inp['input_hash'],
                         source_raw_path=str(Path(raw_name).resolve()), source_row=row,
                         original_deepseek_evaluation=row['evaluation'],
                         original_deepseek_total=row['evaluation'].get('总分'))
                for field, key in [('question','question_sha256'), ('answer','answer_sha256'),
                                   ('masked_case_text','masked_case_sha256'), ('masked_judgment','masked_judgment_sha256')]:
                    assert isinstance(a[field], str) and a[field].strip(), f'{aid}: empty {field}'
                    a[key] = sha256_text(a[field])
                assert aid not in answers
                answers[aid] = a
    assert len(answers) == 2200 and len({a['case_id'] for a in answers.values()}) == 88
    # Existing 40 are directly reused; this only ensures the same answer is keyed.
    for a in old['answers']:
        assert answers[a['answer_id']]['answer_sha256'] == a['answer_sha256']
    return list(answers.values()), old


def frozen_hashes():
    return {name: sha256_text(inspect.getsource(method)) for name, method in {
        'evaluation_prompt_source_sha256': AnswerEvaluator._call_evaluation_api,
        'legacy_flag_parser_source_sha256': AnswerEvaluator._detect_flags_legacy,
        'legacy_penalty_source_sha256': AnswerEvaluator._apply_penalty_for_flags,
    }.items()}


def prepare(out):
    answers, old = read_sources()
    # The original 20 may be added only through a separate explicit ready-source
    # manifest, not by silently relaxing completeness of historical inputs.
    tasks = []
    for a in sorted(answers, key=lambda a: (a['case_id'], a['question_number'], a['answer_condition'])):
        for label, judge in JUDGES.items():
            if judge['family'] == a['answer_family']:
                continue
            tid = task_id(a['answer_id'], label)
            prior_path = old['rating_files'].get(tid)
            source = 'reused_cross40' if prior_path else ('reused_existing' if judge['family'] == 'DeepSeek' else 'new_api')
            tasks.append(dict(task_id=tid, answer_id=a['answer_id'], case_id=a['case_id'],
                              question_number=a['question_number'], answer_condition=a['answer_condition'],
                              answer_family=a['answer_family'], judge_label=label, judge_family=judge['family'],
                              judge_model_id=judge['model_id'], judge_thinking=judge['thinking'], source=source,
                              prior_rating_path=prior_path,
                              **{k: a[k] for k in ['input_hash','question_sha256','answer_sha256','masked_case_sha256','masked_judgment_sha256']}))
    hashes = frozen_hashes()
    previous = load_json(ROOT / 'data/experiment1_cross_judge_sample20_xhub_20260822/manifest.json')
    assert all(previous[k] == v for k, v in hashes.items()), 'Frozen scoring code changed'
    manifest = dict(created_at=now_iso(), population_cases=108, dispatchable_cases=88,
                    reused_cross_judged_cases=40, new_case_count=48, blocked_historical_cases=20,
                    blocked_reason='Original 20 workbook has answers/scores but original masked case/judgment inputs are not yet available.',
                    answer_count=len(answers), task_count=len(tasks),
                    task_source_counts=dict(Counter(t['source'] for t in tasks)),
                    hashes=hashes, source_files={str(p):file_sha256(p) for p in SOURCES+[OLD40]},
                    judge_matrix={c:[l for l,j in JUDGES.items() if j['family']!=f] for c,f in ANSWER_FAMILY.items()},
                    parameters=dict(temperature=0.3,max_tokens=3000,flag_parser_version='legacy',
                                    task_attempts=3,legacy_http_and_empty_retries='unchanged'), tasks=tasks)
    assert manifest['task_source_counts'] == {'new_api':2880, 'reused_existing':720, 'reused_cross40':3000}
    path = out/'manifest_ready88.json'
    if path.exists():
        frozen = load_json(path)
        assert frozen['tasks'] == tasks and frozen['hashes'] == hashes
        manifest = frozen
    else:
        atomic_write_json(path, manifest)
        atomic_write_json(out/'source_answers_ready88.json', answers)
        atomic_write_json(out/'market_snapshot.json', load_json(PILOT_MARKET))
    lookup = {a['answer_id']:a for a in answers}
    for t in tasks:
        if t['source'] == 'reused_cross40':
            assert result_is_valid(Path(t['prior_rating_path']), t)
        elif t['source'] == 'reused_existing':
            target = out/'ratings'/f"{t['task_id']}.json"
            if not result_is_valid(target,t):
                atomic_write_json(target,make_reused_result(t,lookup[t['answer_id']]))
    return manifest, answers


def rating_path(out, task):
    return Path(task['prior_rating_path']) if task['source']=='reused_cross40' else out/'ratings'/f"{task['task_id']}.json"


def select_pending_tasks(out, tasks, *, transport_only=False):
    """Dispatch scope only; evaluator and each task's retries are unchanged."""
    pending=[]
    for task in tasks:
        if task['source']!='new_api':
            continue
        path=rating_path(out,task)
        if result_is_valid(path,task):
            continue
        error=str(load_json(path).get('error','')) if path.exists() else ''
        if transport_only and not any(kind in error for kind in (
                'ProxyError:', 'ReadTimeout:', 'ConnectTimeout:', 'ConnectionError:', 'Timeout:')):
            continue
        if any(word in error.lower() for word in ['content_filter','contentfilter','content filter','safety','policy']):
            continue
        pending.append(task)
    return pending


def summarize(out, manifest, answers, state, budget=None):
    rows, by_answer, paths = [], defaultdict(list), {}
    counts = Counter()
    for task in manifest['tasks']:
        p = rating_path(out,task)
        if result_is_valid(p,task):
            r = load_json(p)
            rows.append(r); by_answer[task['answer_id']].append(r); paths[task['task_id']]=str(p)
            counts['completed'] += 1
            if task['source'] == 'new_api': counts['new_completed'] += 1
        elif p.exists(): counts['failed'] += 1
        else: counts['pending'] += 1
    records=[]
    for a in answers:
        ratings=by_answer[a['answer_id']]
        consensus={}
        if len(ratings)==3:
            totals=[float(r['evaluation']['总分']) for r in ratings]
            consensus=dict(method='three_cross_family_judges_median_after_legacy_penalty',
                           total_score=statistics.median(totals),percentage_score=statistics.median(totals)*5,
                           dimension_scores={d:statistics.median(float(r['evaluation']['各维度得分'][d]) for r in ratings) for d in DIMENSIONS},
                           judge_total_scores={r['judge_label']:r['evaluation']['总分'] for r in ratings},
                           judge_score_range=max(totals)-min(totals),
                           error_vote_counts={level:sum(bool((r.get('errors')or{}).get(level)) for r in ratings) for level in ('微小错误','明显错误','重大错误')})
        records.append(dict(public_answer(a), cross_family_rating_count=len(ratings),
                            cross_family_rating_task_ids=[r['task_id'] for r in ratings],consensus=consensus))
    snapshot=dict(updated_at=now_iso(),pid=os.getpid(),phase=state,population_cases=108,
                  ready_cases=88,reused_cases=40,new_cases=48,blocked_cases=20,total=6600,
                  **{k:counts[k] for k in ['completed','new_completed','failed','pending']},
                  expected_new_api=2880,budget=budget,
                  full_population_complete=False,
                  ready_cohort_complete=counts['completed']==6600,
                  cases_with_all_ratings=sum(sum(len(by_answer[a['answer_id']]) for a in answers if a['case_id']==cid)==75 for cid in {a['case_id'] for a in answers}))
    atomic_write_json(out/'state.json',snapshot)
    atomic_write_json(out/'combined_results_ready88.json',dict(metadata=snapshot,
        completion=dict(ratings_completed=len(rows),answers_with_three_ratings=sum(r['cross_family_rating_count']==3 for r in records),
                        complete=counts['completed']==6600,full108_complete=False),
        judge_matrix=manifest['judge_matrix'],answers=records,rating_files=paths,task_count=6600))
    return snapshot


def main():
    ap=argparse.ArgumentParser(__doc__)
    ap.add_argument('--execute',action='store_true')
    ap.add_argument('--detach',action='store_true')
    ap.add_argument('--workers',type=int,default=16)
    ap.add_argument('--budget-usd',type=float,default=110)
    ap.add_argument('--retry-transport-only',action='store_true',
                    help='Retry saved proxy/connection/timeout failures only; exclude refusals and unstarted tasks.')
    ap.add_argument('--output-dir',type=Path,default=OUT)
    ap.add_argument('--xhub-env',type=Path,default=ROOT.parent/'AI_Council/.env')
    args=ap.parse_args()
    if not 0<args.budget_usd<=110: ap.error('Authorized total budget must not exceed $110 including pilot.')
    if not 1<=args.workers<=16: ap.error('workers must be 1..16')
    out=args.output_dir.resolve();out.mkdir(parents=True,exist_ok=True)
    if args.detach:
        if not args.execute: ap.error('--detach requires --execute')
        argv=[sys.executable,str(Path(__file__).resolve())]+[a for a in sys.argv[1:] if a!='--detach']
        with (out/'run.log').open('ab',buffering=0) as log:
            proc=subprocess.Popen(argv,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True)
        print(json.dumps({'spawned_pid':proc.pid,'log':str(out/'run.log')}));return 0
    with (out/'run.lock').open('a+') as lock:
        try: fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError: raise SystemExit('A run already holds this output lock; no duplicate calls started.')
        manifest,answers=prepare(out)
        if not args.execute:
            snapshot=summarize(out,manifest,answers,'prepared')
            print(json.dumps(snapshot,ensure_ascii=False));return 0
        cfg=dotenv_values(args.xhub_env)
        market=load_json(out/'market_snapshot.json')
        guard=GuardedTransport(cfg['UNIFIED_API_BASE_URL'].rstrip('/'),cfg['UNIFIED_API_KEY'],out,
                              market['public_default_group_prices_usd_per_million'],market['quota_per_unit'],
                              args.budget_usd,prepaid_usd=0.929528)
        legacy_transport.requests=guard
        client=legacy_transport.XHubClient(cfg['UNIFIED_API_KEY'],cfg['UNIFIED_API_BASE_URL'].rstrip('/'),timeout=180)
        stop=threading.Event()
        for sig in (signal.SIGTERM,signal.SIGINT): signal.signal(sig,lambda *_:stop.set())
        lookup={a['answer_id']:a for a in answers}
        # Each dispatch retains the old three task attempts and HTTP retries.
        # A resumed process does not indefinitely retry a stable content filter.
        pending=select_pending_tasks(out,manifest['tasks'],transport_only=args.retry_transport_only)
        def worker(t):
            p=rating_path(out,t)
            if p.exists():
                old=load_json(p)
                atomic_write_json(out/'prior_attempts'/f"{t['task_id']}_{time.time_ns()}.json",old)
            r=run_new_task(t,lookup[t['answer_id']],client)
            atomic_write_json(p,r)
            return r
        snapshot=summarize(out,manifest,answers,'running',guard.snapshot())
        print(json.dumps({'event':'started','pending_new':len(pending),'workers':args.workers,
                          'retry_transport_only':args.retry_transport_only,'state':snapshot},ensure_ascii=False),flush=True)
        iterator=iter(pending);active={};done=0;last_snapshot=time.monotonic();last_balance=time.monotonic()
        with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            def dispatch():
                while len(active)<args.workers and not stop.is_set() and not guard.stopped.is_set():
                    t=next(iterator,None)
                    if t is None:break
                    active[pool.submit(worker,t)]=t
            dispatch()
            while active:
                finished,_=futures.wait(active,timeout=10,return_when=futures.FIRST_COMPLETED)
                for f in finished:
                    t=active.pop(f)
                    try:r=f.result()
                    except Exception as exc:
                        r=dict(t,status='failed',completed_at=now_iso(),error=(type(exc).__name__+': '+str(exc)).replace(cfg['UNIFIED_API_KEY'],'[REDACTED]'))
                        atomic_write_json(rating_path(out,t),r)
                    done+=1
                    print(json.dumps({'event':'rating','done_this_process':done,'task_id':t['task_id'],
                                      'judge':t['judge_label'],'answer_condition':t['answer_condition'],
                                      'status':r['status'],'time':now_iso()},ensure_ascii=False),flush=True)
                dispatch()
                if time.monotonic()-last_balance>=300:
                    guard.refresh_balance();last_balance=time.monotonic()
                if time.monotonic()-last_snapshot>=60 or (done and done<=3):
                    snapshot=summarize(out,manifest,answers,'stopping' if stop.is_set() else 'running',guard.snapshot())
                    last_snapshot=time.monotonic()
        guard.refresh_balance()
        phase='budget_paused' if guard.stopped.is_set() else 'interrupted' if stop.is_set() else 'ready_cohort_finished'
        final=summarize(out,manifest,answers,phase,guard.snapshot())
        print(json.dumps({'event':'finished','state':final},ensure_ascii=False),flush=True)
        return 0 if final['ready_cohort_complete'] else 2


if __name__=='__main__':
    raise SystemExit(main())
