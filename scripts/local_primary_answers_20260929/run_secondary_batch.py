#!/usr/bin/env python3
"""Authorized secondary-only answers. Reuses immutable primary science helpers."""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_primary_batch as previous
pilot = previous.pilot
read, write, freeze, sha, canonical, now = pilot.read, pilot.write, pilot.freeze, pilot.sha, pilot.canonical, pilot.now
OUT = pilot.ROOT / 'data/pkulaw_secondary_three_answers_full_20260929'
BUDGET, WORKERS, TIMEOUT = 350.0, 8, 300
SCOPE = 'secondary_2004_answering_only'
MAPPING_SHA = 'ac70b5739bd2fa471441ff8c970709c15643df16030b831ed8a19d9136e969e2'


def code_identity():
    paths = (Path(__file__).resolve(), Path(previous.__file__).resolve(), Path(pilot.__file__).resolve(),
             Path(sys.modules[pilot.GuardedTransport.__module__].__file__).resolve())
    return {str(p.relative_to(pilot.ROOT)): sha(p.read_bytes()) for p in paths}


def validate_scope(tasks, excluded):
    if len(excluded) != 18 or len(tasks) != 30060 or len({pilot.task_id(t) for t in tasks}) != 30060:
        raise ValueError('Expected 30060 distinct answers and eighteen exclusions')
    cases = Counter(t['case_id'] for t in tasks)
    questions = Counter((t['case_id'], t['question_id']) for t in tasks)
    if (len(cases) != 2004 or set(cases.values()) != {15} or len(questions) != 10020 or
            set(questions.values()) != {3} or Counter(t['model'] for t in tasks) != {m: 10020 for m in pilot.MODELS}):
        raise ValueError('Expected 2004 cases, five questions, three exact models')
    for t in tasks:
        if t['split'] != 'secondary' or t['case_id'] in excluded:
            raise ValueError('Primary/excluded task outside authorization')
        if sha(canonical(t['messages'])) != t['prompt_hash'] or sha(t['question_text']) != t['question_hash']:
            raise ValueError('Frozen input provenance mismatch')


def prepare(out, market_path):
    mapping = (pilot.SOURCE / 'task_mapping.jsonl').read_bytes()
    manifest = read(pilot.SOURCE / 'input_manifest.json')
    if sha(mapping) != MAPPING_SHA or sha(mapping) != manifest['task_mapping_sha256']:
        raise ValueError('Formal mapping changed')
    excluded = {x['case_id'] for x in read(pilot.SOURCE / 'excluded_cases.json')}
    retained = [json.loads(line) for line in mapping.splitlines() if line]
    if len(retained) != 11095 or any(r['case_id'] in excluded for r in retained):
        raise ValueError('Formal retained release differs')
    prompt_file = pilot.ROOT / 'scripts/run_experiment1_smoke_xhub.py'
    prompt_source = prompt_file.read_text()
    history = subprocess.run(['git', 'show', '06b533d5d695348623c018f95ef5f4809f7ae7ea:scripts/run_experiment1_smoke_xhub.py'],
                             cwd=pilot.ROOT, check=True, capture_output=True, text=True).stdout
    def find(text):
        return next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == 'analysis_messages')
    node = find(prompt_source)
    if ast.dump(node) != ast.dump(find(history)):
        raise ValueError('108-case answer prompt changed')
    from typing import List, Dict, Optional
    namespace = {'List': List, 'Dict': Dict, 'Optional': Optional}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(prompt_file), 'exec'), namespace)
    tasks = []
    for row in sorted((r for r in retained if r['split'] == 'secondary'), key=lambda r: (r['case_id'], r['question_id'])):
        messages = row['answer_messages']
        if (messages != namespace['analysis_messages'](row['case_text_masked'], row['question_text']) or
                sha(canonical(messages)) != row['answer_prompt_hash'] or
                sha(row['case_text_masked']) != row['masked_case_text_hash'] or
                not row['judge_decision_masked'] or row['judge_decision_masked'] in messages[1]['content']):
            raise ValueError('Prompt/hash/judgment-isolation mismatch')
        for model in pilot.MODELS:
            task = {k: row[k] for k in ('split', 'case_id', 'question_id', 'question_text', 'question_hash',
                                       'source_case_text_hash', 'masked_case_text_hash')}
            tasks.append(dict(task, model=model, messages=messages, prompt_hash=row['answer_prompt_hash']))
    validate_scope(tasks, excluded)
    market = read(market_path)
    pilot.validate_market(market)
    cfg = dict(stage='answering_only', full_run_authorized=True, temperature=.3,
               initial_max_tokens=3000, length_retry_max_tokens=6000, thinking_parameter_omitted=True,
               models=list(pilot.MODELS), tasks=tasks)
    freeze(out / 'input_tasks.json', cfg)
    files = {'input_tasks.json': sha((out / 'input_tasks.json').read_bytes())}
    for source, name in [(pilot.SOURCE / 'excluded_cases.json', 'excluded_cases.json'), (market_path, 'market_snapshot.json')]:
        files[name] = previous.copy_frozen(source, out / name)
    identity = dict(schema_version=1, scope=SCOPE, full_run_authorized=True,
        authorization='User: continue and finish the other three models secondary answers, 2026-09-29',
        cases=2004, questions=10020, tasks=30060, new_network_tasks=30060, reused_answers=0,
        scoring_calls=0, masking_calls=0, question_generation_calls=0, models=list(pilot.MODELS),
        temperature=.3, max_tokens=[3000, 6000], thinking_parameter_omitted=True,
        budget_usd=BUDGET, prepaid_usd=0, per_model_workers=WORKERS, timeout=TIMEOUT,
        budget_note='Engineering protection selected within authorized whole-secondary work; not a user-quoted cap. No inherited $40/$70/$100 cap.',
        engineering_note='An unknown request is isolated and never resent. Three consecutive unknowns on one model stop further dispatch. Scientific payload and bounded retries unchanged.',
        source_mapping_sha256=MAPPING_SHA, source_prompt_sha256=sha(ast.get_source_segment(prompt_source, node)),
        files_sha256=files, code_sha256=code_identity())
    freeze(out / 'authorization.json', identity)
    return dict(prepared=True, cases=2004, questions=10020, answers=30060, budget_usd=BUDGET)


def load(out, check_code=True):
    identity = read(out / 'authorization.json')
    expected = dict(scope=SCOPE, full_run_authorized=True, tasks=30060, budget_usd=BUDGET,
                    prepaid_usd=0, per_model_workers=WORKERS, timeout=TIMEOUT)
    if any(identity.get(k) != v for k, v in expected.items()):
        raise ValueError('Frozen authorization changed')
    if check_code and identity['code_sha256'] != code_identity():
        raise ValueError('Frozen runner/helper changed')
    for name, digest in identity['files_sha256'].items():
        if sha((out / name).read_bytes()) != digest:
            raise ValueError('Frozen artifact changed: ' + name)
    tasks = read(out / 'input_tasks.json')['tasks']
    validate_scope(tasks, {x['case_id'] for x in read(out / 'excluded_cases.json')})
    return tasks, identity


class LocalStop:
    """Per-request ambiguity must not cancel unrelated questions."""
    def __init__(self, shared):
        self.local, self.shared = threading.Event(), shared
    def set(self):
        self.local.set()
    def is_set(self):
        return self.local.is_set() or self.shared.is_set()


class IsolatedRunner:
    def __init__(self, tasks, results, transport, base, key, stop, timeout=TIMEOUT):
        self.tasks, self.results, self.transport = tasks, results, transport
        self.base, self.key, self.stop, self.timeout = base, key, stop, timeout
        self.unknown_streak, self.lock = Counter(), threading.Lock()
    def task(self, task):
        inner = pilot.Runner(self.tasks, self.results, self.transport, self.base, self.key,
                             LocalStop(self.stop), self.timeout)
        result = inner.task(task)
        if self.transport.stopped.is_set():
            self.stop.set()
        if result:
            with self.lock:
                model = task['model']
                self.unknown_streak[model] = self.unknown_streak[model] + 1 if result['status'] == 'needs_reconcile' else 0
                if self.unknown_streak[model] >= 3:
                    self.stop.set()
                    path = self.results / 'circuit_breaker.json'
                    if not path.exists():
                        freeze(path, dict(model=model, reason='three_consecutive_unknown_requests', at=now()))
        return result


def launch(out, env, resume=False):
    with previous.exclusive(out / 'control.lock'), previous.exclusive(out / 'results/run.lock'):
        tasks, identity = load(out)
        previous.previous_guard(out, resume)
        _, _, done, _ = previous.scan(tasks, out / 'results', reject_unknown=True)
        if len(done) == len(tasks):
            return dict(complete=True, terminal=len(done), launched=False)
        pilot.validate_market(read(out / 'market_snapshot.json'))
        session = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        base = out / 'sessions' / session
        write(base.with_suffix('.intent.json'), dict(created_at=now(), resume=resume,
              authorization_sha256=sha((out / 'authorization.json').read_bytes())), True)
        command = [sys.executable, '-B', str(Path(__file__).resolve()), '_worker', '--out', str(out.resolve()),
                   '--xhub-env', str(env.resolve()), '--session', session]
        try:
            with base.with_suffix('.log').open('xb') as log:
                child = subprocess.Popen(command, cwd=pilot.ROOT, stdin=subprocess.DEVNULL, stdout=log,
                                         stderr=log, start_new_session=True, close_fds=True)
            write(base.with_suffix('.launch.json'), dict(launched_at=now(), **previous.identity_pid(child.pid), command=command), True)
        except BaseException as exc:
            if 'child' not in locals():
                write(base.with_suffix('.exit.json'), dict(completed_at=now(), exit_code=1, error_type=type(exc).__name__, child_created=False), True)
            raise
    return dict(launched=True, session=session, pid=child.pid, remaining=len(tasks)-len(done), per_model_workers=WORKERS, budget_usd=BUDGET)


def worker(out, env, session):
    base = out / 'sessions' / session
    code, error_type, owns_start = 1, None, False
    lock = (out / 'results/run.lock').open('a+')
    try:
        for _ in range(100):
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                time.sleep(.1)
        else:
            raise ValueError('Worker startup lock unavailable')
        if base.with_suffix('.start.json').exists() or base.with_suffix('.exit.json').exists():
            raise ValueError('Session already has owner/exit')
        launcher = read(base.with_suffix('.launch.json'))
        current = previous.identity_pid(os.getpid())
        if any(launcher[k] != current[k] for k in ('pid', 'process_start')):
            raise ValueError('Worker process identity differs')
        tasks, identity = load(out)
        if read(base.with_suffix('.intent.json'))['authorization_sha256'] != sha((out / 'authorization.json').read_bytes()):
            raise ValueError('Launch authorization differs')
        counts, by_model, done, _ = previous.scan(tasks, out / 'results', reject_unknown=True)
        write(base.with_suffix('.start.json'), dict(started_at=now(), **current), True)
        owns_start = True
        market = read(out / 'market_snapshot.json')
        api_base, key = pilot.credentials(env)
        pilot.validate_market(market, api_base)
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        transport = pilot.GuardedTransport(api_base, key, out / 'results', market['prices_usd_per_million'], market['quota_per_unit'], BUDGET, prepaid_usd=0)
        runner = IsolatedRunner(tasks, out / 'results', transport, api_base, key, stop)
        def checkpoint(result, inflight):
            if result:
                counts[result['status']] += 1
                by_model[result['model']][result['status']] += 1
                print(json.dumps({k: result[k] for k in ('model', 'question_id', 'status', 'attempts')}, ensure_ascii=False), flush=True)
            terminal = sum(counts.values())
            write(out / 'results/summary.json', dict(updated_at=now(), session=session, tasks=len(tasks), terminal=terminal,
                  counts=dict(counts), by_model={k: dict(v) for k,v in by_model.items()}, in_flight=inflight,
                  pending=len(tasks)-terminal-inflight, stopped=stop.is_set(), scope=SCOPE,
                  budget=transport.snapshot(), scoring_performed=False))
        checkpoint(None, 0)
        previous.bounded_schedule(tasks, runner, done, checkpoint, workers=WORKERS)
        code = 0 if sum(counts.values()) == len(tasks) and not counts['needs_reconcile'] else 2
        checkpoint(None, 0)
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        if owns_start:
            write(base.with_suffix('.exit.json'), dict(completed_at=now(), exit_code=code, error_type=error_type,
                  **previous.identity_pid(os.getpid())), True)
        else:
            write(base.with_name(session + '.startup_failure.' + str(os.getpid()) + '.' + datetime.now(timezone.utc).strftime('%H%M%S%f') + '.json'),
                  dict(completed_at=now(), error_type=error_type, owns_session=False), True)
        lock.close()
    return dict(exit_code=code)


def status(out):
    if (out / 'results/summary.json').exists():
        return previous.status(out)
    tasks, _ = load(out)
    counts, by_model, done, unresolved = previous.scan(tasks, out / 'results')
    return dict(tasks=len(tasks), terminal=len(done), counts=dict(counts), pending=len(tasks)-len(done),
                by_model={k:dict(v) for k,v in by_model.items()}, needs_reconcile=unresolved)


def main():
    parser = argparse.ArgumentParser(__doc__)
    parser.add_argument('command', choices=['prepare', 'launch', 'resume', 'status', '_worker'])
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--market', type=Path)
    parser.add_argument('--xhub-env', type=Path, default=pilot.ENV)
    parser.add_argument('--session')
    args = parser.parse_args()
    os.umask(0o077)
    if args.command == 'prepare':
        if not args.market:
            parser.error('prepare requires --market')
        result = prepare(args.out, args.market)
    elif args.command == 'status':
        result = status(args.out)
    elif args.command == '_worker':
        if not args.session or '/' in args.session or '.' in args.session:
            parser.error('Valid internal session required')
        result = worker(args.out, args.xhub_env, args.session)
    else:
        result = launch(args.out, args.xhub_env, args.command == 'resume')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except BaseException as exc:
        print(json.dumps({'error_type': type(exc).__name__, 'details': 'See durable artifacts; credential-bearing exception suppressed.'}), file=sys.stderr)
        raise SystemExit(1)
