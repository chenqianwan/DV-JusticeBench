#!/usr/bin/env python3
"""One user-authorized retry round for exactly 116 isolated secondary answers.

Waits for the original queue to exit naturally. Never edits an old result or
ledger; inherits the final cumulative budget before any new paid request.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_secondary_hourly_reconnect48 as source

base, pilot, previous = source.base, source.pilot, source.previous
read, write, freeze, sha, canonical, now = source.read, source.write, source.freeze, source.sha, source.canonical, source.now
OUT = pilot.ROOT / 'data/pkulaw_secondary_unknown_once_20260930'
SCOPE_AUDIT = pilot.ROOT / 'outputs/secondary_unknown_retry_20260930/local_scope_audit_20260930T132224Z.json'
EXPECTED = {'gpt-4o': 37, 'gemini-2.5-flash': 34, 'qwen-max': 45}
MIN_AGE_SECONDS = 3600


def code_identity():
    return dict(source.code_identity(), **{
        str(Path(__file__).resolve().relative_to(pilot.ROOT)): sha(Path(__file__).read_bytes())})


def epoch(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def unknown_snapshot(roots, original, requested_ids=None):
    candidates, seen = {}, set()
    for root in roots:
        for path in sorted((root/'results').glob('task_*/terminal.json')):
            tid = path.parent.name
            if tid in seen or tid not in original:
                raise ValueError('Duplicate/out-of-scope original terminal')
            seen.add(tid)
            if requested_ids is not None and tid not in requested_ids:
                continue
            row = read(path)
            if row['task_sha256'] != sha(canonical(original[tid])):
                raise ValueError('Original task fingerprint differs')
            if row['status'] != 'needs_reconcile':
                continue
            requests = sorted(path.parent.glob('attempt_*.request.json'))
            responses = sorted(path.parent.glob('attempt_*.response.json'))
            if len(requests) != 1 or len(responses) != 1:
                raise ValueError('This authorization requires one recorded old network attempt')
            req, res = read(requests[0]), read(responses[0])
            if (req['task_id'] != tid or req['payload'] != pilot.payload(original[tid], 3000)
                    or pilot.classify(res)[0] != 'needs_reconcile' or not res.get('transport_error')):
                raise ValueError('Unknown candidate has reusable or non-network response')
            age = time.time() - epoch(res['completed_at'])
            if age < MIN_AGE_SECONDS:
                raise ValueError('Old attempt is too recent for this bounded retry round')
            candidates[tid] = dict(task_id=tid, source=str(root), model=original[tid]['model'],
                old_completed_at=res['completed_at'], old_transport_error=res['transport_error'],
                local_request_returned=True, reusable_local_answer=False,
                provider_execution_state='not_exposed_by_known_API',
                original_charge_may_have_occurred=True,
                receipt_sha256={str(p.relative_to(pilot.ROOT)): sha(p.read_bytes())
                                for p in path.parent.iterdir() if p.is_file()})
    if Counter(v['model'] for v in candidates.values()) != EXPECTED:
        raise ValueError('Exact user-discussed 116-answer unknown set changed')
    if requested_ids is not None and set(candidates) != set(requested_ids):
        raise ValueError('Requested unknown task identity set differs')
    return candidates


def prepare(out, scope_audit=None):
    with previous.exclusive(out/'control.lock'):
        if (out/'authorization.json').exists():
            tasks, _ = load(out)
            return dict(already_prepared=True, tasks=len(tasks))
        roots = source.source_roots(source.COORDINATOR)
        original_tasks, _ = base.old.load(roots[0])
        original = {pilot.task_id(t): t for t in original_tasks}
        requested_ids = None
        if scope_audit is not None:
            scope = read(scope_audit)
            requested_ids = {entry['task_id'] for entry in scope['tasks']}
            if scope['count'] != 116 or len(requested_ids) != 116 or scope['by_model'] != EXPECTED:
                raise ValueError('Original user-discussed scope audit differs')
        selected = unknown_snapshot(roots, original, requested_ids)
        tasks = [original[tid] for tid in sorted(selected)]
        freeze(out/'input_tasks.json', dict(tasks=tasks))
        freeze(out/'reconciliation.json', dict(at=now(), tasks=selected,
            scope_audit=str(scope_audit) if scope_audit else None,
            scope_audit_sha256=sha(scope_audit.read_bytes()) if scope_audit else None,
            new_authorization='User: 网络异常的那一批重跑一次',
            note='One fresh retry round despite original result/billing uncertainty; never claim originals were not sent.'))
        freeze(out/'authorization.json', dict(at=now(), scope='secondary_exact116_unknown_one_round',
            tasks=116, by_model=EXPECTED, requested_ids=sorted(selected), original_tasks=30060,
            budget_usd=350.0, budget_inheritance='final original cumulative ledger after all source clients exit',
            per_model_workers=16, temperature=.3, initial_max_tokens=3000,
            length_retry_max_tokens=6000, timeout=300, scoring_performed=False,
            automatic_second_unknown_round=False, code_sha256=code_identity(),
            files_sha256={name:sha((out/name).read_bytes()) for name in ('input_tasks.json','reconciliation.json')}))
        return dict(prepared=True, tasks=116, by_model=EXPECTED, waiting_for_original_queue=True)


def load(out):
    auth = read(out/'authorization.json')
    expected = dict(scope='secondary_exact116_unknown_one_round', tasks=116, by_model=EXPECTED,
        original_tasks=30060, budget_usd=350.0, per_model_workers=16, temperature=.3,
        initial_max_tokens=3000, length_retry_max_tokens=6000, timeout=300,
        scoring_performed=False, automatic_second_unknown_round=False)
    if any(auth.get(k) != v for k,v in expected.items()) or auth['code_sha256'] != code_identity():
        raise ValueError('Retry protocol/code differs from frozen authorization')
    for name,digest in auth['files_sha256'].items():
        if sha((out/name).read_bytes()) != digest:
            raise ValueError('Retry plan changed')
    tasks = read(out/'input_tasks.json')['tasks']
    original = {pilot.task_id(t):t for t in read(source.INITIAL_SOURCES[0]/'input_tasks.json')['tasks']}
    if (len(tasks) != 116 or len({pilot.task_id(t) for t in tasks}) != 116
            or {pilot.task_id(t) for t in tasks} != set(auth['requested_ids'])
            or any(original.get(pilot.task_id(t)) != t for t in tasks)):
        raise ValueError('Retry selection or scientific payload changed')
    for entry in read(out/'reconciliation.json')['tasks'].values():
        for rel,digest in entry['receipt_sha256'].items():
            if sha((pilot.ROOT/rel).read_bytes()) != digest:
                raise ValueError('Old unknown receipt changed')
    return tasks, auth


def retry_done(tasks, results):
    counts, models, done, _ = previous.scan(tasks, results)
    for task in tasks:
        tid = pilot.task_id(task)
        directory = results/tid
        requests = list(directory.glob('attempt_*.request.json'))
        if requests and tid not in done:
            raise ValueError('Retry has a nonterminal sent request; manual reconciliation required')
        if any(not p.with_name(p.name.replace('.request.', '.response.')).exists() for p in requests):
            raise ValueError('Retry request missing response; never resend')
    # Every terminal, including a NEW unknown, is done for this ONE retry round.
    return counts, models, done


def launch(out, env, resume=False):
    with previous.exclusive(out/'control.lock'), previous.exclusive(out/'results/run.lock'):
        tasks, auth = load(out)
        previous.previous_guard(out, resume)
        counts, _, done = retry_done(tasks, out/'results')
        if len(done) == len(tasks):
            return dict(launched=False, complete=True, counts=dict(counts))
        if resume:
            exits = sorted((out/'sessions').glob('*.exit.json'))
            if read(exits[-1]).get('error_type') is not None:
                raise ValueError('Prior retry exit requires engineering diagnosis')
            ledger = read(out/'results/budget_ledger.json') if (out/'results/budget_ledger.json').exists() else None
            if ledger and (ledger['reservations'] or ledger['stopped']):
                raise ValueError('Retry ledger not quiescent or budget stopped')
        session = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        prefix = out/'sessions'/session
        freeze(prefix.with_suffix('.intent.json'), dict(at=now(), resume=resume,
            authorization_sha256=sha((out/'authorization.json').read_bytes())))
        command = [sys.executable,'-B',str(Path(__file__).resolve()),'_worker',
                   '--out',str(out),'--xhub-env',str(env),'--session',session]
        try:
            with prefix.with_suffix('.log').open('xb') as log:
                child = subprocess.Popen(command,cwd=pilot.ROOT,stdin=subprocess.DEVNULL,
                    stdout=log,stderr=log,start_new_session=True,close_fds=True)
            freeze(prefix.with_suffix('.launch.json'),dict(at=now(),
                **previous.identity_pid(child.pid),command=command))
        except BaseException as exc:
            if 'child' not in locals():
                freeze(prefix.with_suffix('.exit.json'),dict(at=now(),exit_code=1,
                    error_type=type(exc).__name__,child_created=False))
            raise
    return dict(launched=True,pid=child.pid,session=session,tasks=116,
                phase='waiting_for_original_queue',resuming_unattempted_only=resume)


def wait_for_source(out, stop, stack):
    deadline = time.monotonic()+7200
    while not stop.is_set():
        state = source.status(source.COORDINATOR)
        write(out/'wait_status.json',dict(at=now(),phase='waiting_for_original_queue',
            source_live=state['live_client'],pending=state['never_started_or_isolated_nonterminal'],
            in_flight=state['in_flight'],source_counts=state['counts']))
        if not state['live_client']:
            roots = source.source_roots(source.COORDINATOR)
            candidate = ExitStack()
            try:
                for path in [source.COORDINATOR/'control.lock', source.COORDINATOR/'run.lock',
                             *source.source_locks(roots)]:
                    candidate.enter_context(previous.exclusive(path))
            except ValueError:
                candidate.close()
            else:
                if source.live_client(roots):
                    candidate.close()
                else:
                    try:
                        pending, audit = source.audit_sources(roots)
                    except BaseException:
                        candidate.close()
                        raise
                    if pending or audit['isolated_nonterminal_ids']:
                        candidate.close()
                    else:
                        stack.enter_context(candidate)
                        return roots, audit
        if time.monotonic() >= deadline:
            write(out/'wait_status.json',dict(at=now(),phase='source_wait_timed_out_cleanly',
                no_retry_POST_sent=True,resume_allowed_after_source_is_ready=True))
            return None, None
        stop.wait(30)
    return None, None


def prepare_source_snapshot(out, tasks, roots, audit):
    selected = {pilot.task_id(t) for t in tasks}
    reconciliation = read(out/'reconciliation.json')['tasks']
    for tid in selected:
        old = audit['prior_terminal'].get(tid)
        if not old or old['status'] != 'needs_reconcile' or old['source'] != reconciliation[tid]['source']:
            raise ValueError('Selected original result changed; do not overwrite a success')
    if set(audit['prior_terminal']) != {pilot.task_id(t) for t in read(roots[0]/'input_tasks.json')['tasks']}:
        raise ValueError('Incomplete original coverage')
    target = out/'source_snapshot.json'
    if target.exists():
        saved = read(target)
        if (saved['files_sha256'] != audit['files_sha256'] or saved['prepaid_usd'] != audit['prepaid_usd']
                or saved['source_roots'] != audit['source_roots']):
            raise ValueError('Original frozen accounting/source snapshot changed')
    else:
        freeze(target,audit)
        previous.copy_frozen(roots[-1]/'market_snapshot.json',out/'market_snapshot.json')
    return audit['prepaid_usd']


def merge(out):
    tasks, _ = load(out)
    audit = read(out/'source_snapshot.json')
    index = {tid:dict(v,terminal_path=str(Path(v['source'])/'results'/tid/'terminal.json'),
                     replacement_round=0) for tid,v in audit['prior_terminal'].items()}
    counts, _, done = retry_done(tasks,out/'results')
    for task in tasks:
        tid=pilot.task_id(task)
        if tid not in done: continue
        if index[tid]['status'] != 'needs_reconcile':
            raise ValueError('Merge cannot replace an original non-unknown terminal')
        row=read(out/'results'/tid/'terminal.json')
        index[tid]=dict(source=str(out),status=row['status'],model=task['model'],replacement_round=1,
            terminal_path=str(out/'results'/tid/'terminal.json'),original_terminal=index[tid]['terminal_path'])
    if len(index)!=30060 or Counter(v['model'] for v in index.values())!={m:10020 for m in pilot.MODELS}:
        raise ValueError('Merged coverage differs')
    grouped={m:Counter(v['status'] for v in index.values() if v['model']==m) for m in pilot.MODELS}
    write(out/'merged_index.json',index)
    report=dict(at=now(),tasks=30060,retry_tasks=116,retry_terminal=len(done),retry_counts=dict(counts),
        counts=dict(Counter(v['status'] for v in index.values())),by_model={m:dict(v) for m,v in grouped.items()},
        cumulative_budget=read(out/'results/budget_ledger.json'),original_results_retained=True,scoring_performed=False)
    write(out/'merged_summary.json',report)
    return report


def worker(out,env,session):
    prefix=out/'sessions'/session
    code,error,owns=1,None,False
    stop=threading.Event()
    with ExitStack() as stack:
        for _ in range(100):
            try:
                stack.enter_context(previous.exclusive(out/'results/run.lock'))
                break
            except ValueError:
                time.sleep(.1)
        else:
            raise ValueError('Retry worker lock unavailable')
        try:
            if prefix.with_suffix('.start.json').exists() or prefix.with_suffix('.exit.json').exists():
                raise ValueError('Session already owned')
            tasks,auth=load(out)
            identity=previous.identity_pid(os.getpid())
            launcher=read(prefix.with_suffix('.launch.json'))
            if any(identity[k]!=launcher[k] for k in ('pid','process_start')):
                raise ValueError('Retry process identity mismatch')
            if read(prefix.with_suffix('.intent.json'))['authorization_sha256']!=sha((out/'authorization.json').read_bytes()):
                raise ValueError('Retry launch authorization mismatch')
            freeze(prefix.with_suffix('.start.json'),dict(at=now(),**identity))
            owns=True
            for sig in (signal.SIGINT,signal.SIGTERM):
                signal.signal(sig,lambda *_:stop.set())
            roots,audit=wait_for_source(out,stop,stack)
            if audit is None:
                code=2
                return dict(exit_code=code)
            prepaid=prepare_source_snapshot(out,tasks,roots,audit)
            counts,models,done=retry_done(tasks,out/'results')
            endpoint,key=pilot.credentials(env)
            base.configure_route(endpoint,'direct')
            probe=base.health(endpoint,key)
            write(out/'health_probe.json',dict(at=now(),probe=probe))
            if not probe['ok']:
                code=2
                return dict(exit_code=code,network_probe_failed=True)
            market=read(out/'market_snapshot.json')
            pilot.validate_market(market,endpoint)
            transport=pilot.GuardedTransport(endpoint,key,out/'results',market['prices_usd_per_million'],
                market['quota_per_unit'],350.0,prepaid_usd=prepaid)
            runner=source.HourlyRunner(tasks,out/'results',transport,endpoint,key,stop)
            def checkpoint(result,inflight):
                if result:
                    counts[result['status']]+=1
                    models[result['model']][result['status']]+=1
                n=sum(counts.values())
                write(out/'results/summary.json',dict(at=now(),session=session,phase='retry_running',tasks=116,
                    terminal=n,counts=dict(counts),by_model={m:dict(v) for m,v in models.items()},
                    in_flight=inflight,pending=116-n-inflight,stopped=stop.is_set(),
                    network_paused=runner.pause.is_set(),model_concurrency={m:runner.capacity(m) for m in pilot.MODELS},
                    budget=transport.snapshot(),scoring_performed=False))
            checkpoint(None,0)
            base.schedule(tasks,runner,done,checkpoint)
            checkpoint(None,0)
            merge(out)
            code=0 if sum(counts.values())==116 and not counts['needs_reconcile'] else 2
        except BaseException as exc:
            error=type(exc).__name__
            raise
        finally:
            if owns:
                freeze(prefix.with_suffix('.exit.json'),dict(at=now(),exit_code=code,error_type=error,
                    **previous.identity_pid(os.getpid())))
            else:
                freeze(prefix.with_name(session+'.startup_failure.json'),dict(at=now(),error_type=error,owns_session=False))
    return dict(exit_code=code)


def status(out):
    tasks,_=load(out)
    counts,models,done=retry_done(tasks,out/'results') if not list((out/'sessions').glob('*.start.json')) else previous.scan(tasks,out/'results')[:3]
    live=None
    for intent in reversed(sorted((out/'sessions').glob('*.intent.json'))):
        for suffix in ('start','launch'):
            path=intent.with_name(intent.name.replace('.intent.',f'.{suffix}.'))
            if path.exists() and previous.is_alive(read(path)):
                live=read(path)
                break
        if live: break
    summary=read(out/'results/summary.json') if (out/'results/summary.json').exists() else None
    return dict(at=now(),tasks=116,counts=dict(counts),by_model={m:dict(v) for m,v in models.items()},
        terminal=len(done),remaining=116-len(done),live_client=live,
        summary=summary,wait_status=read(out/'wait_status.json') if (out/'wait_status.json').exists() else None,
        merged=read(out/'merged_summary.json') if (out/'merged_summary.json').exists() else None)


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('command',choices=['prepare','launch','resume','status','merge','_worker'])
    parser.add_argument('--out',type=Path,default=OUT)
    parser.add_argument('--xhub-env',type=Path,default=pilot.ENV)
    parser.add_argument('--session')
    parser.add_argument('--scope-audit',type=Path,default=SCOPE_AUDIT)
    args=parser.parse_args()
    os.umask(0o077)
    if args.command=='prepare': result=prepare(args.out,args.scope_audit)
    elif args.command in ('launch','resume'): result=launch(args.out,args.xhub_env,args.command=='resume')
    elif args.command=='status': result=status(args.out)
    elif args.command=='merge':
        with previous.exclusive(args.out/'results/run.lock'): result=merge(args.out)
    else:
        if not args.session or any(x in args.session for x in ('/','.')): parser.error('Valid session required')
        result=worker(args.out,args.xhub_env,args.session)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try: main()
    except BaseException as exc:
        print(json.dumps(dict(error_type=type(exc).__name__,details='Inspect durable receipts; sensitive exception text suppressed')),file=sys.stderr)
        raise SystemExit(1)
