#!/usr/bin/env python3
"""Frozen primary three-model answer-only pilot. No network unless market/run/resume.

The full manifest is prepared for audit only. This version can execute ONLY the
frozen one-case / fifteen-answer pilot; it has no full-run authorization switch.
"""
from __future__ import annotations

import argparse
import ast
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.cross_judge_budget_guard import GuardedTransport, BudgetStop

MODELS = ('gpt-4o', 'gemini-2.5-flash', 'qwen-max')
SOURCE = ROOT / 'data/pkulaw_v4_answer_batch128_20260928'
OUT = ROOT / 'data/pkulaw_primary_three_answers_20260929'
ENV = ROOT.parent / 'AI_Council/.env'
MAX_ATTEMPTS = 18
TERMINAL = {'ok', 'refusal', 'content_filter', 'truncated', 'empty', 'interface_error', 'needs_reconcile'}


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def read(path):
    return json.loads(Path(path).read_text())


def write(path, obj, exclusive=False):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    value = canonical(obj) + b'\n'
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name, dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as f:
            f.write(value)
            f.flush()
            os.fsync(f.fileno())
        if exclusive:
            os.link(temporary, path)
        else:
            os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def freeze(path, obj):
    if Path(path).exists():
        if read(path) != obj:
            raise ValueError('Frozen file differs: ' + str(path))
    else:
        write(path, obj, exclusive=True)


def task_id(task):
    return 'task_' + sha(canonical([task[k] for k in ('split', 'case_id', 'question_id', 'model')]))[:24]


def payload(task, maximum):
    # No thinking, reasoning-effort, title or judgment field is added.
    return {'model': task['model'], 'messages': task['messages'],
            'temperature': 0.3, 'max_tokens': maximum}


def prepare(out=OUT):
    manifest = read(SOURCE / 'input_manifest.json')
    mapping_bytes = (SOURCE / 'task_mapping.jsonl').read_bytes()
    if sha(mapping_bytes) != manifest['task_mapping_sha256']:
        raise ValueError('Frozen DS task mapping hash mismatch')
    excluded = {x['case_id'] for x in read(SOURCE / 'excluded_cases.json')}
    if len(excluded) != 18:
        raise ValueError('Expected eighteen permanently excluded cases')
    source_script = ROOT / 'scripts/run_experiment1_smoke_xhub.py'
    source = source_script.read_text()
    node = next(n for n in ast.parse(source).body if isinstance(n, ast.FunctionDef) and n.name == 'analysis_messages')
    history = subprocess.run(['git', 'show', '06b533d5d695348623c018f95ef5f4809f7ae7ea:scripts/run_experiment1_smoke_xhub.py'],
                             cwd=ROOT, check=True, capture_output=True, text=True).stdout
    old = next(n for n in ast.parse(history).body if isinstance(n, ast.FunctionDef) and n.name == 'analysis_messages')
    if ast.dump(node) != ast.dump(old):
        raise ValueError('Original 108-case answer prompt changed')
    from typing import List, Dict, Optional
    namespace = {'List': List, 'Dict': Dict, 'Optional': Optional}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(source_script), 'exec'), namespace)
    build_messages = namespace['analysis_messages']
    retained = [json.loads(line) for line in mapping_bytes.splitlines() if line]
    if len(retained) != 11095 or any(t['case_id'] in excluded for t in retained):
        raise ValueError('Formal retained release mismatch')
    questions = sorted([t for t in retained if t['split'] == 'primary'], key=lambda t: (t['case_id'], t['question_id']))
    counts = Counter(t['case_id'] for t in questions)
    if len(questions) != 1075 or len(counts) != 215 or set(counts.values()) != {5}:
        raise ValueError('Expected primary 215 complete cases / 1075 questions')
    tasks = []
    for row in questions:
        msgs = row['answer_messages']
        if (msgs != build_messages(row['case_text_masked'], row['question_text']) or
                sha(canonical(msgs)) != row['answer_prompt_hash'] or
                sha(row['question_text']) != row['question_hash'] or
                row['judge_decision_masked'] in msgs[1]['content']):
            raise ValueError('Prompt/hash/judgment isolation mismatch')
        for model in MODELS:
            task = {k: row[k] for k in ('split', 'case_id', 'question_id', 'question_text',
                    'question_hash', 'source_case_text_hash', 'masked_case_text_hash')}
            task.update(model=model, messages=msgs, prompt_hash=row['answer_prompt_hash'])
            tasks.append(task)
    pilot_case = questions[0]['case_id']
    pilot = [t for t in tasks if t['case_id'] == pilot_case]
    cfg = {'stage': 'answering_only', 'temperature': 0.3, 'initial_max_tokens': 3000,
           'length_retry_max_tokens': 6000, 'thinking_parameter_omitted': True,
           'models': list(MODELS), 'full_run_authorized': False}
    freeze(out / 'full_tasks.json', dict(cfg, tasks=tasks))
    freeze(out / 'pilot_tasks.json', dict(cfg, tasks=pilot))
    audit = dict(source_mapping_sha256=sha(mapping_bytes), original_answer_prompt_source_sha256=sha(ast.get_source_segment(source, node)),
                 excluded_cases=18, primary_cases=215, primary_questions=1075, full_answers=3225,
                 pilot_case_id=pilot_case, pilot_answers=15, pilot_selection='first case_id in frozen primary; before output observation',
                 full_tasks_sha256=sha((out / 'full_tasks.json').read_bytes()),
                 pilot_tasks_sha256=sha((out / 'pilot_tasks.json').read_bytes()),
                 source_fields_unchanged=True, scoring_calls=0, question_generation_calls=0, masking_calls=0,
                 full_run_authorized=False, api_calls=0)
    freeze(out / 'manifest.json', audit)
    return audit


def credentials(env):
    from dotenv import dotenv_values
    cfg = dotenv_values(env)
    base, key = str(cfg.get('UNIFIED_API_BASE_URL') or '').rstrip('/'), str(cfg.get('UNIFIED_API_KEY') or '')
    parsed = urlsplit(base)
    if parsed.scheme != 'https' or not parsed.hostname or not key:
        raise ValueError('Expected configured HTTPS XHub endpoint and credential')
    return base, key


def validate_market(market, base=None, max_age_s=86400):
    if (set(market['prices_usd_per_million']) != set(MODELS) or
            set(market['catalog_present']) != set(MODELS)):
        raise ValueError('All exact three model IDs must be available and priced')
    age = (datetime.now(timezone.utc) - datetime.fromisoformat(market['checked_at'])).total_seconds()
    if not -60 <= age <= max_age_s:
        raise ValueError('Market snapshot stale; read-only refresh required')
    if base is not None and market['endpoint'] != base:
        raise ValueError('Pricing endpoint differs from execution endpoint')
    unit = market['quota_per_unit']
    group = market['conservative_group_ratio']
    if not all(isinstance(x, (int, float)) and math.isfinite(x) and x > 0 for x in (unit, group)):
        raise ValueError('Invalid pricing unit/group ratio')
    for model in MODELS:
        spec = market['pricing_entries'][model]
        if spec['quota_type'] != 0 or spec['model_name'] != model:
            raise ValueError('Only explicit token-metered pricing is supported')
        rate = spec['model_ratio'] * 1e6 / unit * group
        expected = [rate, rate * spec['completion_ratio']]
        actual = market['prices_usd_per_million'][model]
        if (len(actual) != 2 or any(not math.isfinite(x) or x <= 0 for x in actual) or
                any(not math.isclose(a, b, rel_tol=1e-10) for a, b in zip(actual, expected))):
            raise ValueError('Price does not match preserved provider metadata')


def market_snapshot(out, env):
    """GETs only. Print selected model prices and numeric balance, never credentials."""
    import requests
    base, key = credentials(env)
    parsed = urlsplit(base)
    origin = parsed.scheme + '://' + parsed.netloc
    def get(url, auth=False):
        response = requests.get(url, headers={'Authorization': 'Bearer ' + key} if auth else {}, timeout=30)
        if response.status_code != 200:
            raise RuntimeError('Read-only market endpoint returned HTTP ' + str(response.status_code))
        return response.json()
    catalog = get(base + '/models', True)
    pricing = get(origin + '/api/pricing')
    status = get(origin + '/api/status')['data']
    present = {x['id'] for x in catalog['data']} & set(MODELS)
    entries = {x['model_name']: x for x in pricing['data'] if x['model_name'] in MODELS}
    group_ratios = pricing.get('group_ratio')
    if not isinstance(group_ratios, dict) or not group_ratios:
        raise ValueError('Missing provider group pricing; cannot safely price pilot')
    ratios = list(group_ratios.values())
    if not all(isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0 for x in ratios):
        raise ValueError('Unrecognized provider group pricing')
    # Discount ignored and highest published group multiplier reserved.
    group = max([1.0] + ratios)
    prices = {}
    for name, spec in entries.items():
        rate = spec['model_ratio'] * 1e6 / status['quota_per_unit'] * group
        prices[name] = [rate, rate * spec['completion_ratio']]
    snapshot = dict(schema_version=1, checked_at=now(), endpoint=base, catalog_present=sorted(present),
                    quota_per_unit=status['quota_per_unit'], pricing_entries=entries, group_ratio=group_ratios,
                    conservative_group_ratio=group, prices_usd_per_million=prices,
                    sources=[origin + '/api/pricing', origin + '/api/status', base + '/models'],
                    metering_note='Published highest group ratio; cache discounts ignored; not a billing invoice.')
    validate_market(snapshot, base)
    try:
        usage = get(origin + '/api/usage/token/', True)['data']
        snapshot['balance'] = {k: v for k, v in usage.items() if k in ('total_used', 'total_available', 'total_granted') and isinstance(v, (int, float))}
    except Exception as exc:
        snapshot['balance_error_type'] = type(exc).__name__
    target = out / ('market_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    write(target, snapshot, exclusive=True)
    return {'snapshot': str(target), 'checked_at': snapshot['checked_at'], 'models': sorted(present),
            'prices_usd_per_million': prices, 'conservative_group_ratio': group, 'balance': snapshot.get('balance')}


def load_pilot(out):
    manifest = read(out / 'manifest.json')
    content = (out / 'pilot_tasks.json').read_bytes()
    if sha(content) != manifest['pilot_tasks_sha256']:
        raise ValueError('Pilot input changed')
    tasks = json.loads(content)['tasks']
    if (len(tasks) != 15 or len({t['case_id'] for t in tasks}) != 1 or
            Counter(t['model'] for t in tasks) != {m: 5 for m in MODELS} or
            len({task_id(t) for t in tasks}) != 15):
        raise ValueError('Only the frozen one-case fifteen-answer pilot is authorized')
    for t in tasks:
        if sha(canonical(t['messages'])) != t['prompt_hash'] or sha(t['question_text']) != t['question_hash']:
            raise ValueError('Pilot provenance differs')
    return tasks


def estimate(tasks, market):
    totals = Counter()
    for t in tasks:
        inp = len(json.dumps(t['messages'], ensure_ascii=False).encode()) + 1024
        pin, pout = market['prices_usd_per_million'][t['model']]
        totals['initial_request_upper_usd'] += (inp * pin + 3000 * pout) / 1e6
        totals['one_length_retry_upper_usd'] += (2 * inp * pin + 9000 * pout) / 1e6
        totals['all_attempts_reservation_upper_usd'] += 2 * MAX_ATTEMPTS * (inp * pin + 6000 * pout) / 1e6
    return dict(totals, note='UTF-8 byte upper estimates, not measured tokens; maximum includes all bounded retries.')


def classify(record):
    code = record.get('http_status', 0)
    body = record.get('raw_response')
    if record.get('transport_error') or code in (408, 504):
        return 'needs_reconcile', '', None
    if not isinstance(body, dict):
        return 'error', '', None
    error = body.get('error') or {}
    ecode = error.get('code') or error.get('type') if isinstance(error, dict) else None
    if ecode in ('content_filter', 'content_policy_violation'):
        return 'content_filter', '', None
    if ecode in ('refusal', 'model_refusal', 'safety_refusal'):
        return 'refusal', '', None
    if code != 200:
        return 'error', '', None
    choices = body.get('choices') or []
    choice = choices[0] if choices else {}
    msg = choice.get('message') or {}
    answer = msg.get('content') or ''
    finish = choice.get('finish_reason')
    if msg.get('refusal'):
        return 'refusal', answer or str(msg['refusal']), finish
    if finish in ('content_filter', 'safety'):
        return 'content_filter', answer, finish
    if finish in ('length', 'max_tokens'):
        return 'truncated', answer, finish
    if not isinstance(answer, str) or not answer.strip():
        return 'empty', '', finish
    if finish not in ('stop', 'end_turn'):
        return 'interface_error', answer, finish
    return 'ok', answer, finish


class Runner:
    def __init__(self, tasks, directory, transport, base, key, stop=None, timeout=300, sleep=time.sleep):
        self.tasks, self.out, self.transport = tasks, directory, transport
        self.base, self.key, self.stop, self.timeout, self.sleep = base, key, stop or threading.Event(), timeout, sleep

    def redact(self, text):
        return str(text).replace(self.key, '[REDACTED]') if self.key else str(text)

    def request(self, task, directory, number, maximum):
        req = directory / ('attempt_%03d.request.json' % number)
        res = directory / ('attempt_%03d.response.json' % number)
        body = payload(task, maximum)
        # Candidate intent is durable before the guard reserves a physical call.
        # A budget-denied candidate gets an explicit not_sent receipt.
        write(req, dict(created_at=now(), payload=body, payload_sha256=sha(canonical(body)), task_id=task_id(task)), True)
        started = time.monotonic()
        try:
            response = self.transport.post(self.base + '/chat/completions', json=body,
                    headers={'Authorization': 'Bearer ' + self.key, 'Content-Type': 'application/json'}, timeout=self.timeout)
            raw = self.redact(response.text)
            try:
                parsed = json.loads(raw)
            except ValueError:
                parsed = None
            record = dict(http_status=response.status_code, raw_body=raw, raw_body_sha256=sha(raw), raw_response=parsed,
                          headers={k.lower(): v for k, v in response.headers.items() if k.lower() in ('retry-after', 'x-request-id', 'request-id')},
                          usage=parsed.get('usage') if isinstance(parsed, dict) else None)
        except BudgetStop:
            record = dict(not_sent=True, error='budget_guard_stop')
            self.stop.set()
        except Exception as exc:
            record = dict(transport_error=type(exc).__name__, error=self.redact(exc))
            self.stop.set()
        record.update(completed_at=now(), elapsed_s=round(time.monotonic() - started, 3))
        write(res, record, True)
        return record

    def task(self, task):
        directory = self.out / task_id(task)
        directory.mkdir(parents=True, exist_ok=True)
        terminal = directory / 'terminal.json'
        if terminal.exists():
            result = read(terminal)
            if result['task_sha256'] != sha(canonical(task)) or result['status'] not in TERMINAL:
                raise ValueError('Existing terminal identity/status differs')
            return result
        if list(directory.glob('attempt_*.request.json')):
            raise ValueError('Nonterminal attempted task needs offline reconciliation; never automatically replay')
        if self.stop.is_set():
            return None
        freeze(directory / 'frozen_input.json', task)
        maximum, length_retried, number = 3000, False, 0
        status, answer, finish, record = 'error', '', None, {}
        for outer in range(3):
            while True:
                for http_try in range(3):
                    if self.stop.is_set():
                        return None
                    number += 1
                    record = self.request(task, directory, number, maximum)
                    if record.get('not_sent'):
                        return None
                    status, answer, finish = classify(record)
                    if status == 'needs_reconcile':
                        self.stop.set()
                    if status != 'error' or number >= MAX_ATTEMPTS:
                        break
                    if http_try < 2:
                        delay = http_try + 1
                        if record.get('http_status') == 429:
                            try:
                                delay = max(1, min(300, float(record['headers'].get('retry-after', 60))))
                            except (ValueError, TypeError):
                                delay = 60
                        self.sleep(delay)
                if status == 'truncated' and not length_retried:
                    maximum, length_retried = 6000, True
                    continue
                break
            if status not in ('error', 'empty') or number >= MAX_ATTEMPTS:
                break
            if outer < 2:
                self.sleep(2)
        status = 'interface_error' if status == 'error' else status
        usage = Counter()
        for p in sorted(directory.glob('attempt_*.response.json')):
            for k, v in (read(p).get('usage') or {}).items():
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    usage[k] += v
        body = record.get('raw_response') or {}
        result = {k: v for k, v in task.items() if k != 'messages'}
        result.update(task_sha256=sha(canonical(task)), status=status, answer=answer, finish_reason=finish,
                      requested_model=task['model'], response_model=body.get('model'), response_id=body.get('id'),
                      temperature=.3, thinking_parameter_omitted=True, max_tokens=maximum,
                      length_retried=length_retried, attempts=number, usage=dict(usage), completed_at=now(),
                      scoring_performed=False, response_model_alias_differs=body.get('model') != task['model'])
        write(terminal, result, True)
        return result


def process_identity():
    start = subprocess.run(['ps', '-p', str(os.getpid()), '-o', 'lstart='], capture_output=True, text=True, check=True).stdout.strip()
    return {'pid': os.getpid(), 'process_start': start, 'command': [str(Path(__file__).resolve()), *sys.argv[1:]]}


def status(out):
    tasks = load_pilot(out)
    results = out / 'pilot_results'
    counts = Counter()
    for task in tasks:
        path = results / task_id(task) / 'terminal.json'
        if path.exists():
            counts[read(path)['status']] += 1
    value = dict(scope='pilot_only', tasks=15, terminal=sum(counts.values()), counts=dict(counts),
                 pending=15 - sum(counts.values()), scoring_performed=False)
    if (results / 'run_identity.json').exists():
        value['identity'] = read(results / 'run_identity.json')
    exits = sorted(results.glob('session_*.exit.json'))
    if exits:
        value['last_exit'] = read(exits[-1])
    if (results / 'budget_ledger.json').exists():
        ledger = read(results / 'budget_ledger.json')
        value['budget'] = {k: ledger.get(k) for k in ('budget_usd', 'accounted_including_prepaid_usd', 'estimated_all_http_usd', 'unknown_upper_usd', 'reserved_inflight_usd', 'stopped')}
    return value


def execute(args):
    if args.budget_usd is None or not math.isfinite(args.budget_usd) or args.budget_usd <= 0:
        raise ValueError('Explicit positive --budget-usd required; no previous budget inherited')
    tasks = load_pilot(args.out)
    market = read(args.market)
    validate_market(market)
    results = args.out / 'pilot_results'
    results.mkdir(parents=True, exist_ok=True)
    with (results / 'run.lock').open('a+') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another pilot client holds the run lock')
        identity = dict(pilot_input_sha256=sha((args.out / 'pilot_tasks.json').read_bytes()),
                        runner_sha256=sha(Path(__file__).read_bytes()), budget_guard_sha256=sha(Path(sys.modules[GuardedTransport.__module__].__file__).read_bytes()),
                        market_sha256=sha(args.market.read_bytes()), budget_usd=args.budget_usd,
                        per_model_workers=args.workers, timeout=args.timeout, scope='pilot_15_only')
        existing = (results / 'run_identity.json').exists()
        if existing != (args.command == 'resume'):
            raise ValueError('Use run only for a new queue and explicit resume only for an existing queue')
        if existing:
            if read(results / 'run_identity.json') != identity:
                raise ValueError('Frozen identity/budget/parameters changed')
            starts = sorted(results.glob('session_*.start.json'))
            if starts and not starts[-1].with_name(starts[-1].name.replace('.start.', '.exit.')).exists():
                raise ValueError('Previous client lacks exit receipt; investigate actual exit before resuming')
        # Before any credential access/network, reject all unresolved task intents.
        for task in tasks:
            directory = results / task_id(task)
            requests = list(directory.glob('attempt_*.request.json'))
            if requests and not (directory / 'terminal.json').exists():
                raise ValueError('Nonterminal attempted task requires reconciliation: ' + directory.name)
            if (directory / 'terminal.json').exists() and read(directory / 'terminal.json')['status'] == 'needs_reconcile':
                raise ValueError('Unknown request requires reconciliation: ' + directory.name)
        base, key = credentials(args.xhub_env)
        validate_market(market, base)
        freeze(results / 'run_identity.json', identity)
        freeze(results / 'market_snapshot.json', market)
        session = 'session_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        write(results / (session + '.start.json'), dict(started_at=now(), **process_identity()), True)
        stop = threading.Event()
        for sig in (signal.SIGINT, signal.SIGTERM):
            signal.signal(sig, lambda *_: stop.set())
        exit_code, error_type = 1, None
        try:
            transport = GuardedTransport(base, key, results, market['prices_usd_per_million'], market['quota_per_unit'], args.budget_usd, prepaid_usd=0)
            runner = Runner(tasks, results, transport, base, key, stop, args.timeout)
            # Three independent bounded pools prevent one model monopolizing slots.
            pools = {m: ThreadPoolExecutor(max_workers=args.workers) for m in MODELS}
            try:
                futures = [pools[t['model']].submit(runner.task, t) for t in tasks]
                for future in as_completed(futures):
                    try:
                        result = future.result()
                    except BaseException:
                        # Stop unsent queued work immediately, before waiting for
                        # already-running calls to finish and persist receipts.
                        stop.set()
                        raise
                    if result:
                        print(json.dumps({k: result[k] for k in ('model', 'question_id', 'status', 'attempts')}, ensure_ascii=False), flush=True)
                    write(results / 'summary.json', status(args.out))
            finally:
                for pool in pools.values():
                    pool.shutdown(wait=True)
            summary = status(args.out)
            exit_code = 0 if summary['terminal'] == 15 and not summary['counts'].get('needs_reconcile') else 2
        except BaseException as exc:
            stop.set()
            error_type = type(exc).__name__
            raise
        finally:
            write(results / (session + '.exit.json'), dict(completed_at=now(), exit_code=exit_code, error_type=error_type), True)
            write(results / 'summary.json', status(args.out))
        return status(args.out)


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('command', choices=['prepare', 'market', 'estimate', 'status', 'run', 'resume'])
    ap.add_argument('--out', type=Path, default=OUT)
    ap.add_argument('--xhub-env', type=Path, default=ENV)
    ap.add_argument('--market', type=Path)
    ap.add_argument('--budget-usd', type=float)
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--timeout', type=float, default=300)
    args = ap.parse_args()
    if not 1 <= args.workers <= 16 or not 0 < args.timeout <= 1800:
        ap.error('workers 1..16 per model; timeout 0..1800 seconds')
    if args.command in ('estimate', 'run', 'resume') and args.market is None:
        ap.error('--market snapshot required')
    if args.command == 'prepare':
        result = prepare(args.out)
    elif args.command == 'market':
        result = market_snapshot(args.out, args.xhub_env)
    elif args.command == 'estimate':
        market = read(args.market)
        validate_market(market)
        result = estimate(load_pilot(args.out), market)
    elif args.command == 'status':
        result = status(args.out)
    else:
        result = execute(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        # Credential-bearing request exceptions are never printed verbatim here.
        print(json.dumps({'error_type': type(exc).__name__, 'error': str(exc) if isinstance(exc, (ValueError, BudgetStop)) else 'See durable receipts; details suppressed for credential safety.'}), file=sys.stderr)
        raise SystemExit(1)
