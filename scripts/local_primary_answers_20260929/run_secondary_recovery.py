#!/usr/bin/env python3
"""Versioned secondary recovery: immutable science, bounded adaptive dispatch."""
from __future__ import annotations

import argparse
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
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
from urllib.parse import urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_secondary_batch as old

previous, pilot = old.previous, old.pilot
read, write, freeze, sha, canonical, now = old.read, old.write, old.freeze, old.sha, old.canonical, old.now
SOURCE = old.OUT
OUT = pilot.ROOT / 'data/pkulaw_secondary_three_answers_recovery_20260930'
WORKERS, WARM_WORKERS, WARM_SUCCESSES = 16, 8, 8
BUDGET = 350.0


def code_identity():
    return dict(old.code_identity(), **{str(Path(__file__).resolve().relative_to(pilot.ROOT)): sha(Path(__file__).read_bytes())})


def source_audit():
    tasks, identity = old.load(SOURCE)
    previous.previous_guard(SOURCE, True)
    counts, by_model, done, unknown = previous.scan(tasks, SOURCE / 'results')
    summary = read(SOURCE / 'results/summary.json')
    ledger = read(SOURCE / 'results/budget_ledger.json')
    if summary['in_flight'] or ledger['reservations'] or ledger['stopped']:
        raise ValueError('Source not quiescent or budget stopped')
    hashes = {str(p.relative_to(SOURCE)): sha(p.read_bytes()) for p in
              (SOURCE/'authorization.json', SOURCE/'results/summary.json', SOURCE/'results/budget_ledger.json')}
    retained, unsent = [], []
    for task in tasks:
        tid = pilot.task_id(task)
        path = SOURCE/'results'/tid/'terminal.json'
        if tid in done:
            for artifact in path.parent.iterdir():
                if artifact.is_file():
                    hashes[str(artifact.relative_to(SOURCE))] = sha(artifact.read_bytes())
            if tid not in unknown:
                retained.append(tid)
        else:
            if list((SOURCE/'results'/tid).glob('attempt_*.request.json')):
                raise ValueError('Nonterminal source request must be reconciled')
            unsent.append(tid)
    return tasks, dict(source=str(SOURCE), counts=dict(counts),
        by_model={m:dict(v) for m,v in by_model.items()}, retained=retained, unsent=unsent,
        unresolved=unknown, files_sha256=hashes,
        prepaid_usd=ledger['accounted_including_prepaid_usd'], audited_at=now())


def prepare(out, market_path, reconciliation_path, route):
    """New exact manifest only; never edits original terminal, ledger or code."""
    with previous.exclusive(SOURCE/'control.lock'), previous.exclusive(SOURCE/'results/run.lock'):
        tasks, audit = source_audit()
        market = read(market_path)
        pilot.validate_market(market)
        approved = set()
        reconciliation = read(reconciliation_path) if reconciliation_path else {'tasks':[]}
        for entry in reconciliation['tasks']:
            if entry.get('safe_retry') is True:
                tid = entry['task_id']
                if tid not in audit['unresolved'] or not entry.get('reason'):
                    raise ValueError('Reconciliation outside exact unknown set')
                source_dir=SOURCE/'results'/tid
                required={str(p.relative_to(SOURCE)) for p in source_dir.iterdir() if p.is_file()}
                if (not required or set(entry['receipt_sha256']) != required
                        or str((source_dir/'terminal.json').relative_to(SOURCE)) not in required):
                    raise ValueError('Reconciliation must fingerprint all exact task receipts')
                for rel, digest in entry['receipt_sha256'].items():
                    path = SOURCE/rel
                    if path.resolve() != path or SOURCE not in path.parents or sha(path.read_bytes()) != digest:
                        raise ValueError('Reconciliation receipt changed')
                approved.add(tid)
        selected = set(audit['unsent']) | approved
        remaining = sorted((t for t in tasks if pilot.task_id(t) in selected),
                           key=lambda t:(pilot.task_id(t) not in approved,t['case_id'],t['question_id'],t['model']))
        if len(remaining) != len(selected) or set(audit['retained']) & selected:
            raise ValueError('Recovery selection or retained overlap')
        if audit['prepaid_usd'] >= BUDGET:
            raise ValueError('Source already exhausted budget')
        freeze(out/'input_tasks.json', dict(tasks=remaining, scientific_protocol='original frozen secondary payload'))
        freeze(out/'source_audit.json', audit)
        freeze(out/'reconciliation.json', reconciliation)
        previous.copy_frozen(market_path, out/'market_snapshot.json')
        files = {n:sha((out/n).read_bytes()) for n in
                 ('input_tasks.json','source_audit.json','reconciliation.json','market_snapshot.json')}
        freeze(out/'authorization.json', dict(created_at=now(), scope='secondary_recovery_answering_only',
            authorization='User 2026-09-30: continue running; may increase concurrency',
            tasks=len(remaining), original_tasks=30060, retained=len(audit['retained']),
            source_unknown=len(audit['unresolved']), approved_retry_ids=sorted(approved),
            deferred_unknown_ids=sorted(set(audit['unresolved'])-approved), source=str(SOURCE),
            budget_usd=BUDGET, prepaid_usd=audit['prepaid_usd'], per_model_workers=WORKERS,
            warm_workers=WARM_WORKERS, warm_successes=WARM_SUCCESSES, timeout=300,
            route=route, temperature=.3, initial_max_tokens=3000, length_retry_max_tokens=6000,
            thinking_parameter_omitted=True, scoring_performed=False, code_sha256=code_identity(),
            files_sha256=files, unknowns_never_automatically_replayed=True))
    return dict(prepared=True,tasks=len(remaining),retained=len(audit['retained']),
                approved_retries=len(approved),deferred_unknown=len(audit['unresolved'])-len(approved),
                prepaid_usd=audit['prepaid_usd'],budget_usd=BUDGET,maximum_concurrency=48)


def load(out, check_source=True):
    identity = read(out/'authorization.json')
    expected = dict(scope='secondary_recovery_answering_only', source=str(SOURCE), budget_usd=BUDGET,
                    per_model_workers=WORKERS,warm_workers=WARM_WORKERS,warm_successes=WARM_SUCCESSES,
                    timeout=300,temperature=.3,initial_max_tokens=3000,length_retry_max_tokens=6000,
                    thinking_parameter_omitted=True,scoring_performed=False)
    if any(identity.get(k)!=v for k,v in expected.items()) or identity['code_sha256']!=code_identity():
        raise ValueError('Frozen recovery code/protocol differs')
    for name,digest in identity['files_sha256'].items():
        if sha((out/name).read_bytes())!=digest:
            raise ValueError('Recovery manifest changed')
    audit=read(out/'source_audit.json')
    if check_source:
        old.load(SOURCE)
        previous.previous_guard(SOURCE,True)
        for rel,digest in audit['files_sha256'].items():
            if sha((SOURCE/rel).read_bytes())!=digest:
                raise ValueError('Preserved source changed')
    tasks=read(out/'input_tasks.json')['tasks']
    original={pilot.task_id(t):t for t in read(SOURCE/'input_tasks.json')['tasks']}
    selected=set(audit['unsent']) | set(identity['approved_retry_ids'])
    if (len(tasks)!=identity['tasks'] or len(tasks)!=len(selected)
            or {pilot.task_id(t) for t in tasks}!=selected or selected & set(audit['retained'])):
        raise ValueError('Selection duplicates or overwrites source')
    if identity['prepaid_usd']!=audit['prepaid_usd']:
        raise ValueError('Prior accounting was not carried forward')
    for t in tasks:
        if t!=original[pilot.task_id(t)]:
            raise ValueError('Scientific task changed')
    return tasks,identity


def configure_route(base,route):
    # Only this worker's XHub host is affected, not global proxy/VPN settings.
    if route=='direct':
        hostname=urlsplit(base).hostname
        for name in ('NO_PROXY','no_proxy'):
            values=[x for x in os.environ.get(name,'').split(',') if x]
            if hostname not in values: values.append(hostname)
            os.environ[name]=','.join(values)


def health(base,key):
    import requests
    started=time.monotonic()
    try:
        response=requests.get(base+'/models',headers={'Authorization':'Bearer '+key},timeout=15,allow_redirects=False)
        if response.status_code!=200:
            return dict(ok=False,http_status=response.status_code,elapsed_s=round(time.monotonic()-started,3))
        names={r['id'] for r in response.json()['data']}
        return dict(ok=set(pilot.MODELS)<=names,http_status=200,elapsed_s=round(time.monotonic()-started,3))
    except Exception as exc:
        return dict(ok=False,error_type=type(exc).__name__,elapsed_s=round(time.monotonic()-started,3))


class RecoveryRunner:
    def __init__(self,tasks,results,transport,base,key,stop):
        self.tasks,self.results,self.transport=tasks,results,transport
        self.base,self.key,self.stop=base,key,stop
        self.guard=threading.Lock()
        self.streak,self.successes,self.limits,self.rate_limits=Counter(),Counter(),{},Counter()
        self.server_errors=Counter()
        self.server_windows={m:deque(maxlen=100) for m in pilot.MODELS}
        self.pause=threading.Event()
        self.events=0
        self.recovery_cycles=0
    def event(self,kind,**kwargs):
        with self.guard:
            self.events+=1
            write(self.results/'events'/('event_'+str(time.time_ns())+'_'+str(self.events)+'.json'),
                  dict(at=now(),kind=kind,**kwargs),True)
    def capacity(self,model):
        with self.guard:
            return min(self.limits.get(model,WORKERS), WORKERS if self.successes[model]>=WARM_SUCCESSES else WARM_WORKERS)
    def task(self,task):
        inner=pilot.Runner(self.tasks,self.results,self.transport,self.base,self.key,old.LocalStop(self.stop),300)
        result=inner.task(task)
        if self.transport.stopped.is_set(): self.stop.set()
        if result:
            model=task['model']
            responses=[read(p) for p in (self.results/pilot.task_id(task)).glob('attempt_*.response.json')]
            with self.guard:
                self.successes[model]+=result['status']=='ok'
                self.streak[model]=self.streak[model]+1 if result['status']=='needs_reconcile' else 0
                limits=sum(r.get('http_status')==429 for r in responses)
                self.rate_limits[model]+=limits
                self.server_errors[model]+=sum(r.get('http_status')==503 for r in responses)
                self.server_windows[model].extend(r.get('http_status')==503 for r in responses)
                if result['status']=='needs_reconcile' or limits:
                    self.successes[model]=0
                    self.limits[model]=min(self.limits.get(model,WORKERS),8)
                if self.rate_limits[model]>=3: self.limits[model]=min(self.limits.get(model,WORKERS),8)
                if self.rate_limits[model]>=12: self.limits[model]=4
                recent=self.server_windows[model]
                if len(recent)>=20 and sum(recent)/len(recent)>=.1: self.limits[model]=min(self.limits.get(model,WORKERS),8)
                if len(recent)>=20 and sum(recent)/len(recent)>=.25: self.limits[model]=4
                if self.streak[model]>=3: self.pause.set()
        return result
    def recover_connection(self):
        # Never repeats an ambiguous POST; only GET probes resume unsent work.
        self.recovery_cycles+=1
        self.event('network_pause',cycle=self.recovery_cycles)
        if self.recovery_cycles>3:
            self.event('network_recovery_exhausted'); self.stop.set(); return False
        for delay in (30,60,120,300,300):
            if self.stop.wait(delay): return False
            probe=health(self.base,self.key)
            self.event('network_probe',probe=probe)
            if probe['ok']:
                # Require a second independent successful GET before dispatch.
                if self.stop.wait(5): return False
                confirm=health(self.base,self.key)
                self.event('network_probe_confirmation',probe=confirm)
                if confirm['ok']:
                    with self.guard:
                        self.streak.clear()
                        self.successes.clear()
                        for model in pilot.MODELS:self.limits[model]=min(self.limits.get(model,WORKERS),8)
                    self.pause.clear()
                    self.event('network_recovered_unsent_only')
                    return True
        self.event('network_recovery_exhausted'); self.stop.set(); return False


def schedule(tasks,runner,done,checkpoint):
    queues={m:deque(t for t in tasks if t['model']==m and pilot.task_id(t) not in done) for m in pilot.MODELS}
    pools={m:ThreadPoolExecutor(max_workers=WORKERS) for m in pilot.MODELS}
    active,error={},None
    try:
        while any(queues.values()) or active:
            if not runner.stop.is_set() and not runner.pause.is_set():
                counts=Counter(active.values())
                for model in pilot.MODELS:
                    while queues[model] and counts[model]<runner.capacity(model) and not runner.stop.is_set() and not runner.pause.is_set():
                        active[pools[model].submit(runner.task,queues[model].popleft())]=model
                        counts[model]+=1
            checkpoint(None,len(active))
            if not active:
                if runner.pause.is_set() and not runner.stop.is_set() and any(queues.values()):
                    runner.recover_connection(); continue
                break
            completed,_=wait(active,timeout=10,return_when=FIRST_COMPLETED)
            for future in completed:
                active.pop(future)
                try: result=future.result()
                except BaseException as exc:
                    runner.stop.set();error=error or exc;result=None
                checkpoint(result,len(active))
        if error:raise error
    finally:
        for pool in pools.values():pool.shutdown(wait=True)


def launch(out,env,resume=False):
    with ExitStack() as stack:
        for p in (SOURCE/'control.lock',SOURCE/'results/run.lock',out/'control.lock',out/'results/run.lock'):
            stack.enter_context(previous.exclusive(p))
        tasks,identity=load(out)
        previous.previous_guard(out,resume)
        _,_,done,_=previous.scan(tasks,out/'results',reject_unknown=True)
        if len(done)==len(tasks):return dict(complete=True,launched=False)
        market=read(out/'market_snapshot.json');pilot.validate_market(market)
        base,key=pilot.credentials(env);configure_route(base,identity['route']);pilot.validate_market(market,base)
        probe=health(base,key)
        if not probe['ok']:raise ValueError('Preflight connection failed: '+json.dumps(probe))
        session=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        prefix=out/'sessions'/session
        write(prefix.with_suffix('.intent.json'),dict(created_at=now(),resume=resume,
              authorization_sha256=sha((out/'authorization.json').read_bytes()),preflight=probe),True)
        command=[sys.executable,'-B',str(Path(__file__).resolve()),'_worker','--out',str(out.resolve()),
                 '--xhub-env',str(env.resolve()),'--session',session]
        try:
            with prefix.with_suffix('.log').open('xb') as log:
                child=subprocess.Popen(command,cwd=pilot.ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,
                                       start_new_session=True,close_fds=True)
            write(prefix.with_suffix('.launch.json'),dict(launched_at=now(),**previous.identity_pid(child.pid),command=command),True)
        except BaseException as exc:
            if 'child' not in locals():write(prefix.with_suffix('.exit.json'),dict(completed_at=now(),exit_code=1,error_type=type(exc).__name__,child_created=False),True)
            raise
    return dict(launched=True,session=session,pid=child.pid,remaining=len(tasks)-len(done),
                warm_concurrency=24,maximum_concurrency=48,budget_usd=BUDGET,prepaid_usd=identity['prepaid_usd'])


def worker(out,env,session):
    prefix=out/'sessions'/session
    code,error_type,owns=1,None,False
    with ExitStack() as stack:
        # Parent holds locks while writing the durable launch identity.
        for p in (SOURCE/'control.lock',SOURCE/'results/run.lock',out/'results/run.lock'):
            for attempt in range(150):
                try:
                    stack.enter_context(previous.exclusive(p));break
                except ValueError:
                    if attempt==149:raise
                    time.sleep(.1)
        try:
            if prefix.with_suffix('.start.json').exists() or prefix.with_suffix('.exit.json').exists():raise ValueError('Session already owned')
            current=previous.identity_pid(os.getpid());launcher=read(prefix.with_suffix('.launch.json'))
            if any(current[k]!=launcher[k] for k in ('pid','process_start')):raise ValueError('Worker identity differs')
            tasks,identity=load(out)
            if read(prefix.with_suffix('.intent.json'))['authorization_sha256']!=sha((out/'authorization.json').read_bytes()):raise ValueError('Launch authorization differs')
            counts,by_model,done,_=previous.scan(tasks,out/'results',True)
            write(prefix.with_suffix('.start.json'),dict(started_at=now(),**current),True);owns=True
            market=read(out/'market_snapshot.json');base,key=pilot.credentials(env)
            configure_route(base,identity['route']);pilot.validate_market(market,base)
            stop=threading.Event()
            for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
            transport=pilot.GuardedTransport(base,key,out/'results',market['prices_usd_per_million'],market['quota_per_unit'],BUDGET,prepaid_usd=identity['prepaid_usd'])
            runner=RecoveryRunner(tasks,out/'results',transport,base,key,stop)
            def checkpoint(result,inflight):
                if result:
                    counts[result['status']]+=1;by_model[result['model']][result['status']]+=1
                    print(json.dumps({k:result[k] for k in ('model','question_id','status','attempts')},ensure_ascii=False),flush=True)
                n=sum(counts.values())
                write(out/'results/summary.json',dict(updated_at=now(),session=session,tasks=len(tasks),terminal=n,
                    counts=dict(counts),by_model={m:dict(v) for m,v in by_model.items()},in_flight=inflight,pending=len(tasks)-n-inflight,
                    stopped=stop.is_set(),network_paused=runner.pause.is_set(),budget=transport.snapshot(),
                    model_concurrency={m:runner.capacity(m) for m in pilot.MODELS},
                    rate_limit_responses=dict(runner.rate_limits),server_error_responses=dict(runner.server_errors),
                    scoring_performed=False,retained_source_terminal=identity['retained'],
                    deferred_source_unknown=len(identity['deferred_unknown_ids'])))
            checkpoint(None,0)
            schedule(tasks,runner,done,checkpoint)
            code=0 if sum(counts.values())==len(tasks) and not counts['needs_reconcile'] else 2
            checkpoint(None,0)
        except BaseException as exc:
            error_type=type(exc).__name__;raise
        finally:
            if owns:write(prefix.with_suffix('.exit.json'),dict(completed_at=now(),exit_code=code,error_type=error_type,**previous.identity_pid(os.getpid())),True)
            else:write(prefix.with_name(session+'.startup_failure.'+str(time.time_ns())+'.json'),dict(at=now(),error_type=error_type,owns_session=False),True)
    return dict(exit_code=code)


def status(out):
    if (out/'results/summary.json').exists():
        value=previous.status(out)
    else:
        tasks,_=load(out)
        counts,by_model,done,unknown=previous.scan(tasks,out/'results')
        value=dict(tasks=len(tasks),terminal=len(done),counts=dict(counts),pending=len(tasks)-len(done),
                   by_model={m:dict(v) for m,v in by_model.items()},needs_reconcile=unknown)
    if (out/'authorization.json').exists():
        ident=read(out/'authorization.json')
        value.update(original_tasks=ident['original_tasks'],retained_source_terminal=ident['retained'],
                     deferred_source_unknown=len(ident['deferred_unknown_ids']))
    return value


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('command',choices=['prepare','launch','resume','status','_worker'])
    parser.add_argument('--out',type=Path,default=OUT)
    parser.add_argument('--market',type=Path)
    parser.add_argument('--reconciliation',type=Path)
    parser.add_argument('--route',choices=['system','direct'],default='system')
    parser.add_argument('--xhub-env',type=Path,default=pilot.ENV)
    parser.add_argument('--session')
    args=parser.parse_args();os.umask(0o077)
    if args.command=='prepare':
        if not args.market:parser.error('--market required')
        result=prepare(args.out,args.market,args.reconciliation,args.route)
    elif args.command=='status':result=status(args.out)
    elif args.command=='_worker':
        if not args.session or '/' in args.session or '.' in args.session:parser.error('Invalid session')
        result=worker(args.out,args.xhub_env,args.session)
    else:result=launch(args.out,args.xhub_env,args.command=='resume')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except BaseException as exc:
        print(json.dumps(dict(error_type=type(exc).__name__,details='Credential-bearing exception suppressed; inspect durable receipts')),file=sys.stderr)
        raise SystemExit(1)
