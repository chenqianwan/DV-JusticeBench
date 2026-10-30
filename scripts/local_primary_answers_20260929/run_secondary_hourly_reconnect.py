#!/usr/bin/env python3
"""Hourly, append-only continuation of provably never-sent secondary answers.

Only reconnect performs read-only probes; only the identity-checked worker sends
POSTs. Failed probes consume the same one-hour opportunity as successful probes.
Every batch inherits the complete conservative ledger, never a fresh budget.
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
import run_secondary_continuation_20260930 as continuation

base = continuation.base
pilot, previous = base.pilot, base.previous
read, write, freeze, sha, canonical, now = base.read, base.write, base.freeze, base.sha, base.canonical, base.now
COORDINATOR = pilot.ROOT / 'data/pkulaw_secondary_three_answers_hourly_20260930'
INITIAL_SOURCES = (base.SOURCE, base.OUT, continuation.OUT)
BUDGET, COOLDOWN_SECONDS = 350.0, 3600
WORKERS, WARM_WORKERS, WARM_SUCCESSES = 4, 1, 8


def code_identity():
    return dict(continuation.code_identity(), **{
        str(Path(__file__).resolve().relative_to(pilot.ROOT)): sha(Path(__file__).read_bytes())})


def source_roots(coordinator):
    roots = [*INITIAL_SOURCES, *sorted((coordinator / 'batches').glob('batch_*'))]
    if len({p.resolve() for p in roots}) != len(roots):
        raise ValueError('Duplicate source root')
    for root in roots:
        if not (root / 'authorization.json').is_file():
            raise ValueError('Incomplete batch preparation requires audit')
    return roots


def source_locks(roots):
    return [path for root in roots for path in (root / 'control.lock', root / 'results/run.lock')]


def live_client(roots):
    for root in reversed(roots):
        for intent in reversed(sorted((root / 'sessions').glob('*.intent.json'))):
            session = intent.name.removesuffix('.intent.json')
            for suffix in ('start', 'launch'):
                path = root / 'sessions' / (session + '.' + suffix + '.json')
                if path.exists() and previous.is_alive(read(path)):
                    return dict(source=str(root), session=session, **read(path))
    return None


def validate_source(root):
    if root == INITIAL_SOURCES[0]:
        return base.old.load(root)
    if root == INITIAL_SOURCES[1]:
        return base.load(root)
    if root == INITIAL_SOURCES[2]:
        return continuation.load(root)
    return load_batch(root, check_sources=False)


def authentication_failure(response):
    return (response.get('http_status') in (401,403)
            and pilot.classify(response)[0] not in ('content_filter','refusal'))


def audit_sources(roots):
    """Caller holds every ancestor lock. No request is inferred to be unsent."""
    if list(roots[:len(INITIAL_SOURCES)]) != list(INITIAL_SOURCES):
        raise ValueError('Missing original batch lineage')
    original_tasks, _ = base.old.load(roots[0])
    original = {pilot.task_id(t): t for t in original_tasks}
    if len(original) != 30060:
        raise ValueError('Original scope differs')
    hashes, terminal, attempted, ledgers = {}, {}, set(), []
    counts, by_model = Counter(), {m: Counter() for m in pilot.MODELS}
    isolated_nonterminal = []
    for root in roots:
        tasks, auth = validate_source(root)
        if root not in INITIAL_SOURCES:
            saved_audit = read(root/'source_audit.json')
            if any(hashes.get(rel) != digest for rel,digest in saved_audit['files_sha256'].items()):
                raise ValueError('Hourly ancestor fingerprint chain changed')
        previous.previous_guard(root, True)
        for intent in (root/'sessions').glob('*.intent.json'):
            receipt = intent.with_name(intent.name.replace('.intent.json','.exit.json'))
            if not receipt.exists():
                raise ValueError('Ancestor lacks actual exit receipt')
            exit_row = read(receipt)
            if exit_row.get('error_type') is not None or exit_row.get('exit_code') not in (0,2):
                raise ValueError('Ancestor exit requires manual diagnosis')
        if list((root/'sessions').glob('*.startup_failure.*.json')):
            raise ValueError('Ancestor startup failure requires manual diagnosis')
        summary, ledger = read(root / 'results/summary.json'), read(root / 'results/budget_ledger.json')
        if summary['in_flight'] or ledger['reservations'] or ledger['stopped']:
            raise ValueError('Ancestor not quiescent or budget stopped')
        if ledger['budget_usd'] != BUDGET:
            raise ValueError('Ancestor budget differs')
        expected_prepaid = ledgers[-1]['accounted_including_prepaid_usd'] if ledgers else 0
        if ledger['prepaid_usd'] != expected_prepaid or auth['prepaid_usd'] != expected_prepaid:
            raise ValueError('Conservative accounting chain broken')
        ledgers.append(ledger)
        source_counts, _, done, _ = previous.scan(tasks, root / 'results')
        if dict(source_counts) != summary['counts'] or len(done) != summary['terminal']:
            raise ValueError('Ancestor terminal summary differs from receipts')
        metadata = [root / 'authorization.json', root / 'input_tasks.json', root / 'results/summary.json',
                    root / 'results/budget_ledger.json', *sorted((root / 'sessions').glob('*.json')),
                    *sorted((root / 'results/events').glob('*.json'))]
        for name in auth.get('files_sha256', {}):
            metadata.append(root / name)
        for path in metadata:
            hashes[str(path.relative_to(pilot.ROOT))] = sha(path.read_bytes())
        for task in tasks:
            tid = pilot.task_id(task)
            if original.get(tid) != task:
                raise ValueError('Source task or scientific payload changed')
            directory = root / 'results' / tid
            receipts = list(directory.glob('attempt_*.request.json')) + list(directory.glob('attempt_*.response.json'))
            if any(authentication_failure(read(p)) for p in receipts if p.name.endswith('.response.json')):
                raise ValueError('Ancestor authentication failure requires attention')
            if receipts:
                if tid in attempted:
                    raise ValueError('Duplicate attempted answer across lineage')
                attempted.add(tid)
            if tid in done:
                if tid in terminal:
                    raise ValueError('Duplicate terminal across lineage')
                row = read(directory / 'terminal.json')
                terminal[tid] = dict(source=str(root), status=row['status'], model=task['model'])
                counts[row['status']] += 1
                by_model[task['model']][row['status']] += 1
            elif receipts:
                isolated_nonterminal.append(tid)
            if directory.exists():
                for path in directory.iterdir():
                    if path.is_file():
                        hashes[str(path.relative_to(pilot.ROOT))] = sha(path.read_bytes())
    excluded = set(terminal) | attempted
    pending = [t for t in original_tasks if pilot.task_id(t) not in excluded]
    prepaid = ledgers[-1]['accounted_including_prepaid_usd']
    if prepaid >= BUDGET:
        raise ValueError('Cumulative budget exhausted')
    return pending, dict(audited_at=now(), source_roots=[str(p) for p in roots],
        files_sha256=hashes, prior_terminal=terminal, prior_attempted_ids=sorted(attempted),
        isolated_nonterminal_ids=sorted(isolated_nonterminal), counts=dict(counts),
        by_model={m:dict(v) for m,v in by_model.items()},
        never_sent_ids=[pilot.task_id(t) for t in pending], prepaid_usd=prepaid,
        original_tasks=30060, all_prior_unknowns_preserved=True)


def prepare_batch(out, market_path, tasks, audit, coordinator):
    pilot.validate_market(read(market_path))
    if out.exists():
        raise ValueError('Batch directory already exists')
    freeze(out / 'input_tasks.json', dict(tasks=tasks))
    freeze(out / 'source_audit.json', audit)
    previous.copy_frozen(market_path, out / 'market_snapshot.json')
    freeze(out / 'authorization.json', dict(created_at=now(), scope='secondary_hourly_never_sent_only',
        authorization='User 2026-09-30: reconnect now and attempt reconnection hourly when stopped',
        coordinator=str(coordinator.resolve()), tasks=len(tasks), original_tasks=30060,
        budget_usd=BUDGET, prepaid_usd=audit['prepaid_usd'], per_model_workers=WORKERS,
        warm_workers=WARM_WORKERS, warm_successes=WARM_SUCCESSES,
        cooldown_seconds=COOLDOWN_SECONDS, in_worker_reconnections=0,
        temperature=.3, initial_max_tokens=3000, length_retry_max_tokens=6000, timeout=300,
        route='direct', thinking_parameter_omitted=True, scoring_performed=False,
        unknowns_never_automatically_replayed=True,
        files_sha256={name:sha((out/name).read_bytes()) for name in
                      ('input_tasks.json', 'source_audit.json', 'market_snapshot.json')},
        code_sha256=code_identity()))


def load_batch(out, check_sources=True):
    auth = read(out / 'authorization.json')
    expected = dict(scope='secondary_hourly_never_sent_only', original_tasks=30060,
        budget_usd=BUDGET, per_model_workers=WORKERS, warm_workers=WARM_WORKERS,
        warm_successes=WARM_SUCCESSES, cooldown_seconds=COOLDOWN_SECONDS,
        in_worker_reconnections=0, temperature=.3, initial_max_tokens=3000,
        length_retry_max_tokens=6000, timeout=300, route='direct',
        thinking_parameter_omitted=True, scoring_performed=False,
        unknowns_never_automatically_replayed=True)
    if any(auth.get(k) != v for k,v in expected.items()) or auth['code_sha256'] != code_identity():
        raise ValueError('Frozen hourly protocol/code differs')
    for name, digest in auth['files_sha256'].items():
        if sha((out/name).read_bytes()) != digest:
            raise ValueError('Frozen batch artifact changed')
    audit = read(out / 'source_audit.json')
    if check_sources:
        for root in audit['source_roots']:
            previous.previous_guard(Path(root), True)
        for rel, digest in audit['files_sha256'].items():
            if sha((pilot.ROOT/rel).read_bytes()) != digest:
                raise ValueError('Preserved source artifact changed')
    tasks = read(out / 'input_tasks.json')['tasks']
    selected = {pilot.task_id(t) for t in tasks}
    excluded = set(audit['prior_terminal']) | set(audit['prior_attempted_ids'])
    original = {pilot.task_id(t):t for t in read(INITIAL_SOURCES[0]/'input_tasks.json')['tasks']}
    if (len(tasks) != auth['tasks'] or len(tasks) != len(selected)
            or selected != set(audit['never_sent_ids']) or selected & excluded
            or any(original.get(pilot.task_id(t)) != t for t in tasks)
            or auth['prepaid_usd'] != audit['prepaid_usd']):
        raise ValueError('Batch selection/science/cost differs')
    return tasks, auth


def cooldown_remaining(coordinator):
    attempts = sorted((coordinator/'reconnect_attempts').glob('*.intent.json'))
    if not attempts:
        return 0
    elapsed = time.time() - read(attempts[-1])['started_epoch']
    return max(0, COOLDOWN_SECONDS-elapsed)


def choose_market(roots, coordinator, env, endpoint):
    for root in reversed(roots):
        path = root/'market_snapshot.json'
        if path.exists():
            try:
                pilot.validate_market(read(path), endpoint)
                return path
            except ValueError:
                pass
    path = Path(pilot.market_snapshot(coordinator/'market_snapshots', env)['snapshot'])
    pilot.validate_market(read(path), endpoint)
    return path


def reconnect(coordinator, env):
    """One audited hourly opportunity. Never starts if any ancestor is alive."""
    with previous.exclusive(coordinator/'control.lock'):
        for outcome in (coordinator/'reconnect_attempts').glob('*.outcome.json'):
            if read(outcome).get('needs_attention'):
                return dict(launched=False,needs_attention=True,reason='Prior authentication failure requires manual resolution')
        roots = source_roots(coordinator)
        live = live_client(roots)
        if live:
            return dict(launched=False, already_running=True, client=live)
        remaining = cooldown_remaining(coordinator)
        if remaining:
            return dict(launched=False, cooldown_remaining_s=round(remaining,3))
        with ExitStack() as stack:
            for path in [coordinator/'run.lock', *source_locks(roots)]:
                stack.enter_context(previous.exclusive(path))
            tasks, audit = audit_sources(roots)
            if not tasks:
                return dict(launched=False, no_never_sent_tasks=True, counts=audit['counts'])
            session = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            attempt = coordinator/'reconnect_attempts'/session
            freeze(attempt.with_suffix('.intent.json'), dict(started_at=now(), started_epoch=time.time(),
                source_roots=[str(p) for p in roots], tasks=len(tasks), prepaid_usd=audit['prepaid_usd']))
            outcome = dict(launched=False, session=session)
            try:
                endpoint,key = pilot.credentials(env)
                base.configure_route(endpoint,'direct')
                first = base.health(endpoint,key)
                outcome['first_probe'] = first
                if not first['ok']:
                    if first.get('http_status') in (401,403):
                        outcome['needs_attention'] = True
                    return outcome
                time.sleep(5)
                second = base.health(endpoint,key)
                outcome['second_probe'] = second
                if not second['ok']:
                    if second.get('http_status') in (401,403):
                        outcome['needs_attention'] = True
                    return outcome
                market = choose_market(roots, coordinator, env, endpoint)
                out = coordinator/'batches'/('batch_'+session)
                prepare_batch(out, market, tasks, audit, coordinator)
                prefix = out/'sessions'/session
                freeze(prefix.with_suffix('.intent.json'), dict(created_at=now(), resume=False,
                    reconnect_attempt=str(attempt.with_suffix('.intent.json')),
                    authorization_sha256=sha((out/'authorization.json').read_bytes())))
                command = [sys.executable,'-B',str(Path(__file__).resolve()),'_worker',
                    '--coordinator',str(coordinator.resolve()),'--out',str(out.resolve()),
                    '--xhub-env',str(env.resolve()),'--session',session]
                try:
                    with prefix.with_suffix('.log').open('xb') as log:
                        child = subprocess.Popen(command,cwd=pilot.ROOT,stdin=subprocess.DEVNULL,
                            stdout=log,stderr=log,start_new_session=True,close_fds=True)
                    freeze(prefix.with_suffix('.launch.json'),dict(launched_at=now(),
                        **previous.identity_pid(child.pid),command=command))
                except BaseException as exc:
                    if 'child' not in locals():
                        freeze(prefix.with_suffix('.exit.json'),dict(completed_at=now(),exit_code=1,
                            error_type=type(exc).__name__,child_created=False))
                    raise
                write(coordinator/'latest.json',dict(updated_at=now(),batch=str(out),session=session))
                outcome.update(launched=True,pid=child.pid,batch=str(out),tasks=len(tasks),
                    prepaid_usd=audit['prepaid_usd'],per_model_warm_workers=WARM_WORKERS,
                    per_model_max_workers=WORKERS)
                return outcome
            except BaseException as exc:
                outcome['error_type'] = type(exc).__name__
                raise
            finally:
                freeze(attempt.with_suffix('.outcome.json'),dict(completed_at=now(),**outcome))


class HourlyRunner(base.RecoveryRunner):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.global_unknown_streak = 0

    def capacity(self,model):
        with self.guard:
            warm = WORKERS if self.successes[model] >= WARM_SUCCESSES else WARM_WORKERS
            return min(WORKERS,warm,self.limits.get(model,WORKERS))

    def task(self,task):
        result = super().task(task)
        tripped = False
        if result:
            responses = [read(p) for p in (self.results/pilot.task_id(task)).glob('attempt_*.response.json')]
            if any(authentication_failure(row) for row in responses):
                self.stop.set()
                self.event('authentication_failure_requires_attention')
            with self.guard:
                self.global_unknown_streak = self.global_unknown_streak+1 if result['status']=='needs_reconcile' else 0
                if self.global_unknown_streak >= 3:
                    self.stop.set()
                    tripped = True
            if tripped:
                self.event('hourly_network_circuit_open',reason='three_consecutive_unknown_requests_global')
        return result

    def recover_connection(self):
        self.stop.set()
        self.event('network_reconnect_deferred_to_hourly_controller')
        return False


def worker(out,env,session,coordinator):
    prefix = out/'sessions'/session
    code,error_type,owns = 1,None,False
    audit = read(out/'source_audit.json')
    roots = [Path(p) for p in audit['source_roots']]
    with ExitStack() as stack:
        for path in [coordinator/'run.lock',*source_locks(roots),out/'control.lock',out/'results/run.lock']:
            for attempt in range(200):
                try:
                    stack.enter_context(previous.exclusive(path))
                    break
                except ValueError:
                    if attempt == 199:
                        raise
                    time.sleep(.1)
        try:
            if prefix.with_suffix('.start.json').exists() or prefix.with_suffix('.exit.json').exists():
                raise ValueError('Session already owned')
            current = previous.identity_pid(os.getpid())
            launcher = read(prefix.with_suffix('.launch.json'))
            if any(current[k] != launcher[k] for k in ('pid','process_start')):
                raise ValueError('Worker identity differs')
            tasks,auth = load_batch(out)
            if auth['coordinator'] != str(coordinator.resolve()):
                raise ValueError('Coordinator identity differs')
            if read(prefix.with_suffix('.intent.json'))['authorization_sha256'] != sha((out/'authorization.json').read_bytes()):
                raise ValueError('Launch authorization differs')
            counts,by_model,done,_ = previous.scan(tasks,out/'results',True)
            freeze(prefix.with_suffix('.start.json'),dict(started_at=now(),**current))
            owns = True
            endpoint,key = pilot.credentials(env)
            base.configure_route(endpoint,'direct')
            market = read(out/'market_snapshot.json')
            pilot.validate_market(market,endpoint)
            stop = threading.Event()
            for sig in (signal.SIGINT,signal.SIGTERM):
                signal.signal(sig,lambda *_:stop.set())
            transport = pilot.GuardedTransport(endpoint,key,out/'results',market['prices_usd_per_million'],
                market['quota_per_unit'],BUDGET,prepaid_usd=auth['prepaid_usd'])
            runner = HourlyRunner(tasks,out/'results',transport,endpoint,key,stop)
            def checkpoint(result,inflight):
                if result:
                    counts[result['status']] += 1
                    by_model[result['model']][result['status']] += 1
                    print(json.dumps({k:result[k] for k in ('model','question_id','status','attempts')},ensure_ascii=False),flush=True)
                n = sum(counts.values())
                write(out/'results/summary.json',dict(updated_at=now(),session=session,tasks=len(tasks),
                    terminal=n,counts=dict(counts),by_model={m:dict(v) for m,v in by_model.items()},
                    in_flight=inflight,pending=len(tasks)-n-inflight,stopped=stop.is_set(),
                    network_paused=runner.pause.is_set(),budget=transport.snapshot(),
                    model_concurrency={m:runner.capacity(m) for m in pilot.MODELS},
                    rate_limit_responses=dict(runner.rate_limits),server_error_responses=dict(runner.server_errors),
                    global_unknown_streak=runner.global_unknown_streak,scoring_performed=False))
            checkpoint(None,0)
            base.schedule(tasks,runner,done,checkpoint)
            code = 0 if sum(counts.values()) == len(tasks) and not counts['needs_reconcile'] else 2
            checkpoint(None,0)
        except BaseException as exc:
            error_type = type(exc).__name__
            raise
        finally:
            if owns:
                freeze(prefix.with_suffix('.exit.json'),dict(completed_at=now(),exit_code=code,
                    error_type=error_type,**previous.identity_pid(os.getpid())))
            else:
                freeze(prefix.with_name(session+'.startup_failure.'+str(time.time_ns())+'.json'),
                    dict(at=now(),error_type=error_type,owns_session=False))
    return dict(exit_code=code)


def status(coordinator):
    roots = source_roots(coordinator)
    counts,by_model = Counter(),{m:Counter() for m in pilot.MODELS}
    latest,ledger = None,None
    for root in roots:
        if (root/'results/summary.json').exists():
            summary = previous.status(root)
        else:
            tasks = read(root/'input_tasks.json')['tasks']
            local,models,done,unknown = previous.scan(tasks,root/'results')
            summary = dict(tasks=len(tasks),terminal=len(done),counts=dict(local),
                by_model={m:dict(v) for m,v in models.items()},in_flight=0,
                pending=len(tasks)-len(done),needs_reconcile=unknown,startup_pending=True)
        counts.update(summary.get('counts',{}))
        for model,values in summary.get('by_model',{}).items():
            by_model[model].update(values)
        path = root/'results/budget_ledger.json'
        if path.exists():
            ledger = read(path)
        latest = dict(batch=str(root),**summary)
    inflight = latest.get('in_flight',0)
    return dict(at=now(),original_tasks=30060,terminal=sum(counts.values()),counts=dict(counts),
        by_model={m:dict(v) for m,v in by_model.items()},in_flight=inflight,
        never_started_or_isolated_nonterminal=30060-sum(counts.values())-inflight,
        cumulative_budget=ledger,latest=latest,live_client=live_client(roots),
        cooldown_remaining_s=round(cooldown_remaining(coordinator),3),scoring_performed=False)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('command',choices=['reconnect','status','_worker'])
    parser.add_argument('--coordinator',type=Path,default=COORDINATOR)
    parser.add_argument('--out',type=Path)
    parser.add_argument('--xhub-env',type=Path,default=pilot.ENV)
    parser.add_argument('--session')
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'reconnect':
        result = reconnect(args.coordinator,args.xhub_env)
    elif args.command == 'status':
        result = status(args.coordinator)
    else:
        if not args.out or not args.session or '/' in args.session or '.' in args.session:
            parser.error('Valid --out and --session required')
        result = worker(args.out,args.xhub_env,args.session,args.coordinator)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        print(json.dumps(dict(error_type=type(exc).__name__,details='See durable receipts; credential-bearing exception suppressed')),file=sys.stderr)
        raise SystemExit(1)
