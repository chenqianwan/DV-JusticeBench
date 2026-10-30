#!/usr/bin/env python3
"""Audited continuation of never-sent secondary tasks after recovery exhaustion.

Frozen original/recovery scripts and all receipts remain untouched. No ambiguous
request is selected. Network recovery history and conservative cost carry over.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
import json

sys.path.insert(0,str(Path(__file__).resolve().parent))
import run_secondary_recovery as base
pilot,previous=base.pilot,base.previous
read,write,freeze,sha,canonical,now=base.read,base.write,base.freeze,base.sha,base.canonical,base.now
SOURCES=(base.SOURCE,base.OUT)
OUT=pilot.ROOT/'data/pkulaw_secondary_three_answers_continuation_20260930_1405'
WORKERS=4
BUDGET=350.0
PRIOR_SUCCEEDED_RECOVERIES=3
LIFETIME_RECOVERY_LIMIT=4


def code_identity():
    return dict(base.code_identity(),**{str(Path(__file__).resolve().relative_to(pilot.ROOT)):sha(Path(__file__).read_bytes())})


def source_locks():
    return [p for source in SOURCES for p in (source/'control.lock',source/'results/run.lock')]


def audit_sources():
    base.old.load(SOURCES[0]);base.load(SOURCES[1])
    all_tasks=read(SOURCES[0]/'input_tasks.json')['tasks']
    original={pilot.task_id(t):t for t in all_tasks}
    terminal,hashes,counts,by_model={}, {},Counter(),{m:Counter() for m in pilot.MODELS}
    ledgers=[]
    for source in SOURCES:
        previous.previous_guard(source,True)
        summary=read(source/'results/summary.json');ledger=read(source/'results/budget_ledger.json')
        if summary['in_flight'] or ledger['reservations'] or ledger['stopped']:
            raise ValueError('Source still running or budget stopped')
        ledgers.append(ledger)
        tasks=read(source/'input_tasks.json')['tasks']
        local_counts,_,done,unresolved=previous.scan(tasks,source/'results')
        for p in [source/'authorization.json',source/'input_tasks.json',source/'results/summary.json',source/'results/budget_ledger.json',
                  *sorted((source/'sessions').glob('*.json')),*sorted((source/'results/events').glob('*.json'))]:
            hashes[str(p.relative_to(pilot.ROOT))]=sha(p.read_bytes())
        for task in tasks:
            tid=pilot.task_id(task)
            if original.get(tid)!=task:raise ValueError('Source task identity changed')
            directory=source/'results'/tid
            if tid in done:
                if tid in terminal:raise ValueError('Two source terminals for one answer')
                row=read(directory/'terminal.json')
                terminal[tid]=dict(source=str(source),status=row['status'],model=task['model'])
                counts[row['status']]+=1;by_model[task['model']][row['status']]+=1
                for p in directory.iterdir():
                    if p.is_file():hashes[str(p.relative_to(pilot.ROOT))]=sha(p.read_bytes())
            elif list(directory.glob('attempt_*.request.json')) or list(directory.glob('attempt_*.response.json')):
                raise ValueError('Nonterminal attempted request cannot be continued')
    if ledgers[1]['prepaid_usd']!=ledgers[0]['accounted_including_prepaid_usd']:
        raise ValueError('Source cost chain broken')
    events=[read(p) for p in sorted((SOURCES[1]/'results/events').glob('*.json'))]
    successes=sum(e.get('kind')=='network_recovered_unsent_only' for e in events)
    pauses=[e.get('cycle') for e in events if e.get('kind')=='network_pause']
    if successes!=PRIOR_SUCCEEDED_RECOVERIES or max(pauses,default=0)!=4 or not any(e['kind']=='network_recovery_exhausted' for e in events):
        raise ValueError('Expected exact documented exhaustion history')
    pending=[t for t in all_tasks if pilot.task_id(t) not in terminal]
    if len(pending)!=16072 or len(terminal)!=13988 or counts['needs_reconcile']!=69:
        raise ValueError('Expected exact 14:05 continuation partition')
    return pending,dict(audited_at=now(),source_roots=[str(p) for p in SOURCES],files_sha256=hashes,
        prior_terminal=terminal,counts=dict(counts),by_model={m:dict(v) for m,v in by_model.items()},
        never_sent_ids=[pilot.task_id(t) for t in pending],prepaid_usd=ledgers[-1]['accounted_including_prepaid_usd'],
        prior_network_recoveries_completed=successes,prior_pause_attempts=pauses,
        prior_exhaustion_preserved=True)


def prepare(out,market):
    with ExitStack() as stack:
        for p in source_locks():stack.enter_context(previous.exclusive(p))
        tasks,audit=audit_sources()
        pilot.validate_market(read(market))
        if audit['prepaid_usd']>=BUDGET:raise ValueError('Budget exhausted')
        freeze(out/'input_tasks.json',dict(tasks=tasks))
        freeze(out/'source_audit.json',audit)
        previous.copy_frozen(market,out/'market_snapshot.json')
        freeze(out/'authorization.json',dict(created_at=now(),scope='secondary_never_sent_continuation',
            authorization='2026-09-30 14:05 hourly monitor: safely continue verified never-sent tasks under existing authorization',
            tasks=16072,original_tasks=30060,retained_decided=13919,deferred_unknown=69,
            budget_usd=BUDGET,prepaid_usd=audit['prepaid_usd'],per_model_workers=WORKERS,
            inherited_network_recoveries=PRIOR_SUCCEEDED_RECOVERIES,
            lifetime_network_recovery_limit=LIFETIME_RECOVERY_LIMIT,
            recovery_amendment='One explicitly recorded additional network reconnect cycle, 3 -> 4 lifetime; no prior counter or paid retry reset',
            temperature=.3,initial_max_tokens=3000,length_retry_max_tokens=6000,timeout=300,
            route='direct',thinking_parameter_omitted=True,scoring_performed=False,
            files_sha256={n:sha((out/n).read_bytes()) for n in ('input_tasks.json','source_audit.json','market_snapshot.json')},
            code_sha256=code_identity()))
    return dict(prepared=True,tasks=len(tasks),prepaid_usd=audit['prepaid_usd'],budget_usd=BUDGET,
                retained_decided=13919,deferred_unknown=69,per_model_workers=WORKERS)


def load(out):
    ident=read(out/'authorization.json')
    expected=dict(scope='secondary_never_sent_continuation',tasks=16072,original_tasks=30060,
                  retained_decided=13919,deferred_unknown=69,budget_usd=BUDGET,per_model_workers=WORKERS,
                  inherited_network_recoveries=PRIOR_SUCCEEDED_RECOVERIES,lifetime_network_recovery_limit=LIFETIME_RECOVERY_LIMIT,
                  temperature=.3,initial_max_tokens=3000,length_retry_max_tokens=6000,timeout=300,
                  route='direct',thinking_parameter_omitted=True,scoring_performed=False)
    if any(ident.get(k)!=v for k,v in expected.items()) or ident['code_sha256']!=code_identity():raise ValueError('Frozen continuation differs')
    for name,digest in ident['files_sha256'].items():
        if sha((out/name).read_bytes())!=digest:raise ValueError('Continuation artifact changed')
    audit=read(out/'source_audit.json')
    for source in SOURCES:previous.previous_guard(source,True)
    for rel,digest in audit['files_sha256'].items():
        if sha((pilot.ROOT/rel).read_bytes())!=digest:raise ValueError('Preserved source artifact changed')
    if ident['prepaid_usd']!=audit['prepaid_usd']:raise ValueError('Prior cost not preserved')
    tasks=read(out/'input_tasks.json')['tasks']
    original={pilot.task_id(t):t for t in read(SOURCES[0]/'input_tasks.json')['tasks']}
    selected={pilot.task_id(t) for t in tasks}
    if (len(tasks)!=16072 or len(selected)!=len(tasks) or selected!=set(audit['never_sent_ids'])
            or selected & set(audit['prior_terminal'])):raise ValueError('Selection overlaps prior result')
    if any(original[pilot.task_id(t)]!=t for t in tasks):raise ValueError('Science changed')
    return tasks,ident


class ContinuationRunner(base.RecoveryRunner):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.recovery_cycles=PRIOR_SUCCEEDED_RECOVERIES
        for p in self.results.glob('events/*.json'):
            row=read(p)
            if row.get('kind')=='network_pause':self.recovery_cycles=max(self.recovery_cycles,row['cycle'])
    def capacity(self,model):
        return min(WORKERS,super().capacity(model))
    def recover_connection(self):
        if self.recovery_cycles>=LIFETIME_RECOVERY_LIMIT:
            self.event('network_recovery_exhausted',lifetime_cycles=self.recovery_cycles)
            self.stop.set();return False
        self.recovery_cycles+=1
        self.event('network_pause',cycle=self.recovery_cycles,previous_cycles_preserved=PRIOR_SUCCEEDED_RECOVERIES)
        for delay in (30,60,120,300,300):
            if self.stop.wait(delay):return False
            probe=base.health(self.base,self.key);self.event('network_probe',probe=probe)
            if probe['ok']:
                if self.stop.wait(5):return False
                confirm=base.health(self.base,self.key);self.event('network_probe_confirmation',probe=confirm)
                if confirm['ok']:
                    with self.guard:self.streak.clear();self.successes.clear()
                    self.pause.clear();self.event('network_recovered_unsent_only',cycle=self.recovery_cycles)
                    return True
        self.event('network_recovery_exhausted',lifetime_cycles=self.recovery_cycles)
        self.stop.set();return False


def launch(out,env,resume=False):
    with ExitStack() as stack:
        for p in [*source_locks(),out/'control.lock',out/'results/run.lock']:stack.enter_context(previous.exclusive(p))
        tasks,ident=load(out);previous.previous_guard(out,resume)
        _,_,done,_=previous.scan(tasks,out/'results',True)
        if len(done)==len(tasks):return dict(launched=False,complete=True)
        market=read(out/'market_snapshot.json');base_url,key=pilot.credentials(env)
        base.configure_route(base_url,'direct');pilot.validate_market(market,base_url)
        probe=base.health(base_url,key)
        if not probe['ok']:raise ValueError('Read-only network preflight failed')
        session=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');prefix=out/'sessions'/session
        write(prefix.with_suffix('.intent.json'),dict(created_at=now(),resume=resume,preflight=probe,
              authorization_sha256=sha((out/'authorization.json').read_bytes())),True)
        command=[sys.executable,'-B',str(Path(__file__).resolve()),'_worker','--out',str(out.resolve()),'--xhub-env',str(env.resolve()),'--session',session]
        try:
            with prefix.with_suffix('.log').open('xb') as log:
                child=subprocess.Popen(command,cwd=pilot.ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,start_new_session=True,close_fds=True)
            write(prefix.with_suffix('.launch.json'),dict(launched_at=now(),**previous.identity_pid(child.pid),command=command),True)
        except BaseException as exc:
            if 'child' not in locals():write(prefix.with_suffix('.exit.json'),dict(completed_at=now(),exit_code=1,error_type=type(exc).__name__,child_created=False),True)
            raise
    return dict(launched=True,session=session,pid=child.pid,remaining=len(tasks)-len(done),per_model_workers=WORKERS,prepaid_usd=ident['prepaid_usd'])


def worker(out,env,session):
    prefix=out/'sessions'/session;code,error_type,owns=1,None,False
    with ExitStack() as stack:
        for p in [*source_locks(),out/'results/run.lock']:
            for attempt in range(150):
                try:stack.enter_context(previous.exclusive(p));break
                except ValueError:
                    if attempt==149:raise
                    time.sleep(.1)
        try:
            if prefix.with_suffix('.start.json').exists() or prefix.with_suffix('.exit.json').exists():raise ValueError('Session already owned')
            current=previous.identity_pid(os.getpid());launcher=read(prefix.with_suffix('.launch.json'))
            if any(current[k]!=launcher[k] for k in ('pid','process_start')):raise ValueError('Worker identity differs')
            tasks,ident=load(out)
            if read(prefix.with_suffix('.intent.json'))['authorization_sha256']!=sha((out/'authorization.json').read_bytes()):raise ValueError('Launch authorization differs')
            counts,by_model,done,_=previous.scan(tasks,out/'results',True)
            write(prefix.with_suffix('.start.json'),dict(started_at=now(),**current),True);owns=True
            market=read(out/'market_snapshot.json');base_url,key=pilot.credentials(env)
            base.configure_route(base_url,'direct');pilot.validate_market(market,base_url)
            stop=threading.Event()
            for sig in (signal.SIGINT,signal.SIGTERM):signal.signal(sig,lambda *_:stop.set())
            transport=pilot.GuardedTransport(base_url,key,out/'results',market['prices_usd_per_million'],market['quota_per_unit'],BUDGET,prepaid_usd=ident['prepaid_usd'])
            runner=ContinuationRunner(tasks,out/'results',transport,base_url,key,stop)
            def checkpoint(result,inflight):
                if result:
                    counts[result['status']]+=1;by_model[result['model']][result['status']]+=1
                    print(json.dumps({k:result[k] for k in ('model','question_id','status','attempts')},ensure_ascii=False),flush=True)
                n=sum(counts.values())
                write(out/'results/summary.json',dict(updated_at=now(),session=session,tasks=len(tasks),terminal=n,
                    counts=dict(counts),by_model={m:dict(v) for m,v in by_model.items()},in_flight=inflight,pending=len(tasks)-n-inflight,
                    stopped=stop.is_set(),network_paused=runner.pause.is_set(),budget=transport.snapshot(),
                    model_concurrency={m:runner.capacity(m) for m in pilot.MODELS},rate_limit_responses=dict(runner.rate_limits),
                    server_error_responses=dict(runner.server_errors),lifetime_network_recovery_cycles=runner.recovery_cycles,
                    scoring_performed=False,retained_source_terminal=ident['retained_decided'],deferred_source_unknown=ident['deferred_unknown']))
            checkpoint(None,0);base.schedule(tasks,runner,done,checkpoint)
            code=0 if sum(counts.values())==len(tasks) and not counts['needs_reconcile'] else 2
            checkpoint(None,0)
        except BaseException as exc:error_type=type(exc).__name__;raise
        finally:
            if owns:write(prefix.with_suffix('.exit.json'),dict(completed_at=now(),exit_code=code,error_type=error_type,**previous.identity_pid(os.getpid())),True)
            else:write(prefix.with_name(session+'.startup_failure.'+str(time.time_ns())+'.json'),dict(at=now(),error_type=error_type,owns_session=False),True)
    return dict(exit_code=code)


def status(out):
    if (out/'results/summary.json').exists():return previous.status(out)
    tasks,_=load(out);counts,by_model,done,unknown=previous.scan(tasks,out/'results')
    return dict(tasks=len(tasks),terminal=len(done),counts=dict(counts),by_model={m:dict(v) for m,v in by_model.items()},pending=len(tasks)-len(done),needs_reconcile=unknown)


def main():
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('command',choices=['prepare','launch','resume','status','_worker'])
    parser.add_argument('--out',type=Path,default=OUT);parser.add_argument('--market',type=Path)
    parser.add_argument('--xhub-env',type=Path,default=pilot.ENV);parser.add_argument('--session')
    args=parser.parse_args();os.umask(0o077)
    if args.command=='prepare':
        if not args.market:parser.error('--market required')
        result=prepare(args.out,args.market)
    elif args.command=='status':result=status(args.out)
    elif args.command=='_worker':
        if not args.session or '/' in args.session or '.' in args.session:parser.error('Invalid session')
        result=worker(args.out,args.xhub_env,args.session)
    else:result=launch(args.out,args.xhub_env,args.command=='resume')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    try:main()
    except BaseException as exc:
        print(json.dumps(dict(error_type=type(exc).__name__,details='See durable receipts; credential-bearing exception suppressed')),file=sys.stderr)
        raise SystemExit(1)
