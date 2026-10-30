#!/usr/bin/env python3
"""Authorized primary-only answer batch; immutable pilot is reused, never changed."""
from __future__ import annotations

import argparse
from collections import Counter, deque
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
from contextlib import contextmanager
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
import run as pilot

OUT = pilot.ROOT / 'data/pkulaw_primary_three_answers_full_20260929'
BUDGET = 40.0
PREPAID = 0.128728
WORKERS = 8
TIMEOUT = 300
BILLING = pilot.OUT / 'billing_reconciliation_20260929T104751774736Z.json'
FULL_INPUT_SHA = 'e765eedf46011ed9ca71e075c6235f5ef75ec0e1cae4bedbcae368d84d272f0f'
read, write, sha, canonical, freeze, now = pilot.read, pilot.write, pilot.sha, pilot.canonical, pilot.freeze, pilot.now


def copy_frozen(source, target):
    data = Path(source).read_bytes()
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        if target.read_bytes() != data:
            raise ValueError('Frozen copy differs: ' + str(target))
    else:
        with target.open('xb') as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    return sha(data)


def validate_scope(tasks, exclusions):
    if len(exclusions) != 18 or len(tasks) != 3225:
        raise ValueError('Expected 3225 primary answers and eighteen exclusions')
    if len({pilot.task_id(t) for t in tasks}) != 3225:
        raise ValueError('Duplicate primary task identity')
    cases = Counter(t['case_id'] for t in tasks)
    questions = Counter((t['case_id'], t['question_id']) for t in tasks)
    if (len(cases) != 215 or set(cases.values()) != {15} or
            len(questions) != 1075 or set(questions.values()) != {3} or
            Counter(t['model'] for t in tasks) != {m: 1075 for m in pilot.MODELS}):
        raise ValueError('Expected 215 cases, five questions, exactly three original models')
    for task in tasks:
        if task['split'] != 'primary' or task['case_id'] in exclusions:
            raise ValueError('Secondary or excluded case is outside this authorization')
        if (sha(canonical(task['messages'])) != task['prompt_hash'] or
                sha(task['question_text']) != task['question_hash']):
            raise ValueError('Frozen prompt provenance mismatch')


def code_identity():
    return {str(p.relative_to(pilot.ROOT)): sha(p.read_bytes()) for p in
            (Path(__file__).resolve(), Path(pilot.__file__).resolve(),
             Path(sys.modules[pilot.GuardedTransport.__module__].__file__).resolve())}


def prepare(out, market_path):
    """Only copies verified local artifacts. No credentials or HTTP are accessed."""
    source_manifest = read(pilot.OUT / 'manifest.json')
    full = (pilot.OUT / 'full_tasks.json').read_bytes()
    if sha(full) != source_manifest['full_tasks_sha256'] or sha(full) != FULL_INPUT_SHA:
        raise ValueError('Pilot full input changed')
    tasks = json.loads(full)['tasks']
    exclusions = {x['case_id'] for x in read(pilot.SOURCE / 'excluded_cases.json')}
    validate_scope(tasks, exclusions)
    market = read(market_path)
    pilot.validate_market(market)
    bill = read(BILLING)
    if (float(bill['actual_usd']) != PREPAID or bill['matched_calls'] != 15 or
            not bill['logs_equal_key_debit']):
        raise ValueError('Verified prepaid pilot billing mismatch')
    previous = sorted((pilot.OUT / 'pilot_results').glob('session_*.start.json'))
    if not previous or not previous[-1].with_name(previous[-1].name.replace('.start.', '.exit.')).exists():
        raise ValueError('Pilot must have an auditable exit before reusing it')
    if read(previous[-1].with_name(previous[-1].name.replace('.start.', '.exit.')))['exit_code'] != 0:
        raise ValueError('Pilot did not finish successfully')
    if is_alive(read(previous[-1])):
        raise ValueError('Pilot client is still alive')
    pilot_tasks = pilot.load_pilot(pilot.OUT)
    full_by_id = {pilot.task_id(t): t for t in tasks}
    for task in pilot_tasks:
        tid = pilot.task_id(task)
        source = pilot.OUT / 'pilot_results' / tid
        terminal = read(source / 'terminal.json')
        if (full_by_id.get(tid) != task or terminal['status'] != 'ok' or
                terminal['task_sha256'] != sha(canonical(task)) or not terminal['answer'].strip()):
            raise ValueError('Pilot answer cannot be reused: ' + tid)
        if read(source / 'frozen_input.json') != task:
            raise ValueError('Pilot frozen input differs')
        requests = list(source.glob('attempt_*.request.json'))
        if not requests or any(not r.with_name(r.name.replace('.request.', '.response.')).exists() for r in requests):
            raise ValueError('Pilot raw receipt is incomplete')
        for request in requests:
            req = read(request)
            maximum = req['payload']['max_tokens']
            if (maximum not in (3000, 6000) or req['payload'] != pilot.payload(task, maximum) or
                    req['payload_sha256'] != sha(canonical(req['payload'])) or req['task_id'] != tid):
                raise ValueError('Pilot request payload differs')
            response = request.with_name(request.name.replace('.request.', '.response.'))
            for item in (request, response):
                if bill['local_receipt_hashes'].get(str(item.relative_to(pilot.OUT))) != sha(item.read_bytes()):
                    raise ValueError('Pilot billing receipt fingerprint differs')
        response = read(sorted(requests)[-1].with_name(sorted(requests)[-1].name.replace('.request.', '.response.')))
        classification, answer, finish = pilot.classify(response)
        if (classification != 'ok' or answer != terminal['answer'] or finish != terminal['finish_reason'] or
                response['raw_response']['model'] != terminal['response_model']):
            raise ValueError('Pilot terminal is not its preserved response')
    out.mkdir(parents=True, exist_ok=True)
    files = {}
    def copy(src, relative):
        files[str(relative)] = copy_frozen(src, out / relative)
    copy(pilot.OUT / 'full_tasks.json', Path('input_tasks.json'))
    copy(pilot.SOURCE / 'excluded_cases.json', Path('excluded_cases.json'))
    copy(market_path, Path('market_snapshot.json'))
    copy(BILLING, Path('reused_pilot_audit/billing_reconciliation.json'))
    copy(pilot.OUT / 'manifest.json', Path('reused_pilot_audit/manifest.json'))
    for task in pilot_tasks:
        tid = pilot.task_id(task)
        for src in sorted((pilot.OUT / 'pilot_results' / tid).iterdir()):
            if src.is_file():
                copy(src, Path('results') / tid / src.name)
    for src in sorted((pilot.OUT / 'pilot_results').glob('http_receipts/*.json')):
        copy(src, Path('reused_pilot_audit/http_receipts') / src.name)
    for name in ('budget_ledger.json', 'run_identity.json', 'summary.json'):
        copy(pilot.OUT / 'pilot_results' / name, Path('reused_pilot_audit') / name)
    for src in sorted((pilot.OUT / 'pilot_results').glob('session_*.json')):
        copy(src, Path('reused_pilot_audit') / src.name)
    identity = dict(schema_version=1, scope='primary_215_answering_only', full_run_authorized=True,
                    authorization='User: DS cluster full; other three models primary answers, 2026-09-29',
                    cases=215, questions=1075, tasks=3225, reused_pilot_answers=15, new_network_tasks=3210,
                    scoring_calls=0, masking_calls=0, question_generation_calls=0,
                    models=list(pilot.MODELS), temperature=.3, max_tokens=[3000, 6000],
                    thinking_parameter_omitted=True, budget_usd=BUDGET, prepaid_usd=PREPAID,
                    budget_note='Conservative engineering stop selected within authorized work; not a user-quoted budget.',
                    per_model_workers=WORKERS, timeout=TIMEOUT, code_sha256=code_identity(),
                    files_sha256=files, source_manifest_sha256=sha((pilot.OUT / 'manifest.json').read_bytes()))
    freeze(out / 'authorization.json', identity)
    return dict(prepared=True, output=str(out), tasks=3225, reused=15, new_network=3210, budget_usd=BUDGET)


def load(out, check_code=True):
    identity = read(out / 'authorization.json')
    expected = dict(scope='primary_215_answering_only', full_run_authorized=True, tasks=3225,
                    budget_usd=BUDGET, prepaid_usd=PREPAID, per_model_workers=WORKERS, timeout=TIMEOUT)
    if any(identity.get(k) != v for k, v in expected.items()):
        raise ValueError('Authorization scope/budget/parameters changed')
    if check_code and identity['code_sha256'] != code_identity():
        raise ValueError('Frozen runner or helper changed; do not modify a running batch')
    for name, digest in identity['files_sha256'].items():
        if sha((out / name).read_bytes()) != digest:
            raise ValueError('Frozen artifact changed: ' + name)
    tasks = read(out / 'input_tasks.json')['tasks']
    validate_scope(tasks, {x['case_id'] for x in read(out / 'excluded_cases.json')})
    return tasks, identity


def scan(tasks, results, reject_unknown=False):
    counts, by_model, done, unresolved = Counter(), {m: Counter() for m in pilot.MODELS}, set(), []
    for task in tasks:
        tid = pilot.task_id(task)
        directory = results / tid
        terminal = directory / 'terminal.json'
        requests = list(directory.glob('attempt_*.request.json'))
        if terminal.exists():
            value = read(terminal)
            if value['task_sha256'] != sha(canonical(task)) or value['status'] not in pilot.TERMINAL:
                raise ValueError('Terminal identity/status differs: ' + tid)
            counts[value['status']] += 1
            by_model[task['model']][value['status']] += 1
            done.add(tid)
            if value['status'] == 'needs_reconcile':
                unresolved.append(tid)
        elif requests:
            unresolved.append(tid)
        if any(not r.with_name(r.name.replace('.request.', '.response.')).exists() for r in requests):
            unresolved.append(tid)
    if reject_unknown and unresolved:
        raise ValueError('Unknown/nonterminal request requires reconciliation; not resent: ' + sorted(set(unresolved))[0])
    return counts, by_model, done, sorted(set(unresolved))


@contextmanager
def exclusive(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise ValueError('Another batch client holds lock: ' + path.name)
        yield handle


def identity_pid(pid):
    result = subprocess.run(['ps', '-p', str(pid), '-o', 'lstart='], capture_output=True, text=True)
    return dict(pid=pid, process_start=result.stdout.strip())


def is_alive(record):
    return bool(record.get('process_start') and identity_pid(record['pid'])['process_start'] == record['process_start'])


def previous_guard(out, resume):
    sessions = out / 'sessions'
    intents = sorted(sessions.glob('*.intent.json'))
    if bool(intents) != resume:
        raise ValueError('Use launch for a fresh queue; explicit resume for an existing queue')
    if intents:
        session = intents[-1].name.removesuffix('.intent.json')
        for suffix in ('launch', 'start'):
            path = sessions / (session + '.' + suffix + '.json')
            if path.exists() and is_alive(read(path)):
                raise ValueError('Previous batch client is still alive')
        if not (sessions / (session + '.exit.json')).exists():
            raise ValueError('Previous client lacks exit receipt; verify actual exit before recovery')


def launch(out, env, resume=False):
    with exclusive(out / 'control.lock'), exclusive(out / 'results/run.lock'):
        tasks, identity = load(out)
        previous_guard(out, resume)
        _, _, done, _ = scan(tasks, out / 'results', reject_unknown=True)
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
                child = subprocess.Popen(command, cwd=pilot.ROOT, stdin=subprocess.DEVNULL,
                                         stdout=log, stderr=log, start_new_session=True, close_fds=True)
            record = dict(launched_at=now(), **identity_pid(child.pid), command=command)
            write(base.with_suffix('.launch.json'), record, True)
        except BaseException as exc:
            # A failed Popen has no child; uncertain post-Popen failures retain the missing-exit guard.
            if 'child' not in locals():
                write(base.with_suffix('.exit.json'), dict(completed_at=now(), exit_code=1,
                      error_type=type(exc).__name__, child_created=False), True)
            raise
    return dict(launched=True, session=session, pid=child.pid, reused_or_terminal=len(done),
                remaining=len(tasks)-len(done), per_model_workers=WORKERS, budget_usd=BUDGET)


def bounded_schedule(tasks, runner, done, checkpoint, workers=WORKERS):
    """At most workers futures per model, never thousands of queued futures."""
    queues = {m: deque(t for t in tasks if t['model'] == m and pilot.task_id(t) not in done) for m in pilot.MODELS}
    pools = {m: ThreadPoolExecutor(max_workers=workers) for m in pilot.MODELS}
    active, error = {}, None
    try:
        while any(queues.values()) or active:
            if not runner.stop.is_set():
                counts = Counter(active.values())
                for model in pilot.MODELS:
                    while queues[model] and counts[model] < workers and not runner.stop.is_set():
                        active[pools[model].submit(runner.task, queues[model].popleft())] = model
                        counts[model] += 1
            if not active:
                break
            checkpoint(None, len(active))
            completed, _ = wait(active, timeout=10, return_when=FIRST_COMPLETED)
            for future in completed:
                active.pop(future)
                try:
                    result = future.result()
                except BaseException as exc:
                    runner.stop.set()
                    error = error or exc
                    result = None
                checkpoint(result, len(active))
        if error:
            raise error
    finally:
        for pool in pools.values():
            pool.shutdown(wait=True)


def worker(out, env, session):
    session_base = out / 'sessions' / session
    code, error_type = 1, None
    # Parent owns this lock until launch record is durable; bounded startup wait only.
    lock = (out / 'results/run.lock').open('a+')
    acquired, owns_start = False, False
    try:
        for _ in range(100):
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
                break
            except BlockingIOError:
                time.sleep(.1)
        if not acquired:
            raise ValueError('Worker startup lock unavailable')
        if session_base.with_suffix('.start.json').exists() or session_base.with_suffix('.exit.json').exists():
            raise ValueError('Session already has an owner or exit; never reuse a session')
        launcher = read(session_base.with_suffix('.launch.json'))
        current = identity_pid(os.getpid())
        if launcher['pid'] != current['pid'] or launcher['process_start'] != current['process_start']:
            raise ValueError('Internal worker does not match launched process identity')
        tasks, identity = load(out)
        intent = read(session_base.with_suffix('.intent.json'))
        if intent['authorization_sha256'] != sha((out / 'authorization.json').read_bytes()):
            raise ValueError('Launch authorization differs')
        counts, by_model, done, _ = scan(tasks, out / 'results', reject_unknown=True)
        write(session_base.with_suffix('.start.json'), dict(started_at=now(), **identity_pid(os.getpid())), True)
        owns_start = True
        market = read(out / 'market_snapshot.json')
        base, key = pilot.credentials(env)
        pilot.validate_market(market, base)
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        transport = pilot.GuardedTransport(base, key, out / 'results', market['prices_usd_per_million'],
                    market['quota_per_unit'], BUDGET, prepaid_usd=PREPAID)
        runner = pilot.Runner(tasks, out / 'results', transport, base, key, stop, TIMEOUT)
        def checkpoint(result, inflight):
            if result:
                counts[result['status']] += 1
                by_model[result['model']][result['status']] += 1
                print(json.dumps({k: result[k] for k in ('model', 'question_id', 'status', 'attempts')}, ensure_ascii=False), flush=True)
            terminal = sum(counts.values())
            write(out / 'results/summary.json', dict(updated_at=now(), session=session, tasks=len(tasks),
                  reused_pilot=15, terminal=terminal, counts=dict(counts), by_model={k:dict(v) for k,v in by_model.items()},
                  in_flight=inflight, pending=len(tasks)-terminal-inflight, stopped=stop.is_set(),
                  scope=identity['scope'], budget=transport.snapshot(), scoring_performed=False))
        checkpoint(None, 0)
        bounded_schedule(tasks, runner, done, checkpoint)
        code = 0 if sum(counts.values()) == len(tasks) and not counts['needs_reconcile'] else 2
        checkpoint(None, 0)
    except BaseException as exc:
        error_type = type(exc).__name__
        raise
    finally:
        if owns_start:
            write(session_base.with_suffix('.exit.json'), dict(completed_at=now(), exit_code=code,
                  error_type=error_type, **identity_pid(os.getpid())), True)
        else:
            failure = session_base.with_name(session + '.startup_failure.' + str(os.getpid()) + '.' +
                      datetime.now(timezone.utc).strftime('%H%M%S%f') + '.json')
            write(failure, dict(completed_at=now(), error_type=error_type, owns_session=False,
                  note='Startup diagnostic only; not a client exit receipt.'), True)
        lock.close()
    return dict(exit_code=code)


def status(out):
    summary = read(out / 'results/summary.json') if (out / 'results/summary.json').exists() else None
    if summary is None:
        tasks, _ = load(out)
        counts, by_model, done, unknown = scan(tasks, out / 'results')
        summary = dict(tasks=len(tasks), terminal=len(done), counts=dict(counts), pending=len(tasks)-len(done),
                       by_model={k:dict(v) for k,v in by_model.items()}, needs_reconcile=unknown)
    intents = sorted((out / 'sessions').glob('*.intent.json'))
    if intents:
        base = intents[-1].with_name(intents[-1].name.removesuffix('.intent.json'))
        for suffix in ('launch', 'start', 'exit'):
            path = base.with_suffix('.' + suffix + '.json')
            if path.exists():
                summary['last_' + suffix] = read(path)
                if suffix in ('launch', 'start'):
                    summary[suffix + '_alive'] = is_alive(read(path))
    # A live summary may be up to ten seconds old; never infer failures from it.
    return summary


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
            parser.error('Internal worker requires a valid session identifier')
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
