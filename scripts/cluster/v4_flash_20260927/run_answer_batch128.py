#!/usr/bin/env python3
"""Resumable answer-only production queue in the existing eight-GPU allocation."""
import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import fcntl
import json
import math
import os
from pathlib import Path
import re
import signal
import threading
import time

import benchmark_concurrency as benchmark
import run_answer_smoke as base

ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_answer_batch128_20260928')
ENDPOINT = base.KEY.parent / 'endpoint.json'
CONFIG = dict(run_id='pkulaw_v4_answer_batch128_20260928', service_job_id='402816',
              base_url='http://dgx-040:18044/v1', model='deepseek_v4_flash', thinking=False,
              temperature=.3, initial_max_tokens=3000, timeout_s=1800, workers=128)
TERMINAL_STATUSES = {'ok', 'truncated', 'refusal', 'content_filter', 'empty', 'interface_error', 'needs_reconcile'}
RETRY_POLICY = ('Legacy bounds: <=3 HTTP attempts x <=3 error/empty rounds; one global 3000->6000 length increase; '
                '<=18 persisted request intents/task. Transport uncertainty, missing response, HTTP408/504: '
                'needs_reconcile, no automatic redispatch. 429 Retry-After/default60s; 503 bounded backoff.')


def validate(data):
    if set(data) - (set(CONFIG) | {'tasks', 'source_manifest_sha256'}):
        raise ValueError('Unexpected input fields')
    for key, value in CONFIG.items():
        if data.get(key) != value or type(data.get(key)) is not type(value):
            raise ValueError('Frozen production parameter mismatch: ' + key)
    if 'source_manifest_sha256' in data and not re.fullmatch('[0-9a-f]{64}', data['source_manifest_sha256']):
        raise ValueError('Invalid source manifest hash')
    tasks, keys, groups = data.get('tasks'), set(), Counter()
    if not isinstance(tasks, list) or not tasks:
        raise ValueError('Nonempty task list required')
    cases = {'primary': set(), 'secondary': set()}
    for task in tasks:
        if set(task) != base.TASK_FIELDS or task['split'] not in cases:
            raise ValueError('Task fields differ from original answering schema')
        if any(not isinstance(task[k], str) or not task[k] for k in base.TASK_FIELDS - {'messages'}):
            raise ValueError('Invalid task provenance/string fields')
        key = tuple(task[k] for k in ('split', 'case_id', 'question_id'))
        if key in keys:
            raise ValueError('Duplicate task primary key')
        keys.add(key)
        groups[(task['split'], task['case_id'])] += 1
        cases[task['split']].add(task['case_id'])
        messages = task['messages']
        if not isinstance(messages, list) or len(messages) != 2:
            raise ValueError('Original system/user messages required')
        for message, role in zip(messages, ('system', 'user')):
            if set(message) != {'role', 'content'} or message['role'] != role or not isinstance(message['content'], str):
                raise ValueError('Invalid original messages')
        if base.digest(base.canonical(messages)) != task['prompt_hash']:
            raise ValueError('Prompt hash mismatch')
        if base.digest(task['question_text'].encode()) != task['question_hash']:
            raise ValueError('Question hash mismatch')
        if task['question_text'] not in messages[1]['content']:
            raise ValueError('Question absent from original prompt')
        for name in ('prompt_hash', 'source_case_text_hash', 'masked_case_text_hash', 'question_hash'):
            if not re.fullmatch('[0-9a-f]{64}', task[name]):
                raise ValueError('Invalid provenance hash')
    if any(count != 5 for count in groups.values()) or cases['primary'] & cases['secondary']:
        raise ValueError('Input must contain distinct complete five-question cases')


def add_usage(counter, raw):
    if not isinstance(raw, dict):
        return
    for key, value in raw.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            counter[key] += value
    details = raw.get('completion_tokens_details') or {}
    if 'reasoning_tokens' not in raw and isinstance(details, dict):
        value = details.get('reasoning_tokens')
        if isinstance(value, (int, float)):
            counter['reasoning_tokens'] += value


class Runner(base.Runner):
    def __init__(self, input_bytes, output_dir, transport, sampler, endpoint, *, resume=False,
                 max_new_tasks=None, stop=None, sleep=None, reused_count=10):
        self.data = json.loads(input_bytes)
        validate(self.data)
        if max_new_tasks is not None and (type(max_new_tasks) is not int or max_new_tasks < 1):
            raise ValueError('max-new-tasks must be a positive integer')
        self.output, self.sampler, self.endpoint = Path(output_dir), sampler, endpoint
        self.resume, self.max_new_tasks, self.reused_count = resume, max_new_tasks, reused_count
        self.stop = stop or threading.Event()
        self.sleep = sleep or self.stop.wait
        self.guard, self.monitor_done = threading.RLock(), threading.Event()
        self.local = threading.local()
        self.underlying_transport, self.transport = transport, self.tracked_transport
        self.results, self.counts, self.usage = {}, Counter(), Counter()
        self.inflight, self.peak, self.intents, self.responses = 0, 0, 0, 0
        self.length_retries, self.new_completed, self.samples = 0, 0, 0
        self.stop_reasons, self.metrics_missing, self.last_preempt = [], 0, None
        self.started, self.started_at = None, None
        self.tasks_by_dir = {self.task_dir(t).name: t for t in self.data['tasks']}
        self.identity = dict(input_sha256=base.digest(input_bytes), script_sha256=base.digest(Path(__file__).read_bytes()),
                             helper_sha256=base.digest(Path(base.__file__).read_bytes()),
                             monitor_sha256=base.digest(Path(benchmark.__file__).read_bytes()), parameters=CONFIG,
                             tasks=len(self.data['tasks']), retry_policy=RETRY_POLICY)

    def halt(self, reason):
        with self.guard:
            if reason not in self.stop_reasons:
                self.stop_reasons.append(reason)
            self.stop.set()

    def tracked_transport(self, payload):
        with self.guard:
            self.local.dispatched = True
            self.intents += 1
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
        try:
            return self.underlying_transport(payload)
        finally:
            with self.guard:
                self.inflight -= 1

    def request(self, directory, number, payload):
        req = directory / ('attempt_%03d.request.json' % number)
        res = directory / ('attempt_%03d.response.json' % number)
        had_request, had_response = req.exists(), res.exists()
        self.local.dispatched = False
        try:
            record = super().request(directory, number, payload)
        finally:
            with self.guard:
                if not had_request and req.exists():
                    if not self.local.dispatched:
                        self.intents += 1
                    if payload['max_tokens'] == 6000 and not any(
                            json.loads(p.read_text())['payload']['max_tokens'] == 6000
                            for p in directory.glob('attempt_*.request.json') if p != req):
                        self.length_retries += 1
                if not had_response and res.exists():
                    receipt = json.loads(res.read_text())
                    self.responses += 1
                    add_usage(self.usage, receipt.get('usage'))
        if str(record.get('error', '')).startswith('transport_') or record.get('http_status') in (408, 504):
            raise base.UncertainRequest('Transport/gateway timeout may have executed; manual reconciliation required')
        return record

    def task(self, task):
        directory = self.task_dir(task)
        directory.mkdir(exist_ok=True)
        terminal = directory / 'terminal.json'
        if terminal.exists():
            return json.loads(terminal.read_text())
        frozen = dict(task, request_sha256_by_token_limit={str(limit): base.digest(base.canonical(dict(
            model=CONFIG['model'], messages=task['messages'], temperature=.3, max_tokens=limit,
            chat_template_kwargs={'thinking': False}))) for limit in (3000, 6000)})
        frozen_path = directory / 'frozen_input.json'
        if frozen_path.exists():
            if json.loads(frozen_path.read_text()) != frozen:
                raise ValueError('Frozen complete request hash mismatch')
        else:
            base.atomic(frozen_path, frozen, exclusive=True)
        number, maximum, retried, last = 0, 3000, False, {}
        status, answer, finish = 'interface_error', '', None
        try:
            for outer in range(3):
                while True:
                    for http_try in range(3):
                        number += 1
                        payload = dict(model=CONFIG['model'], messages=task['messages'], temperature=.3,
                                       max_tokens=maximum, chat_template_kwargs={'thinking': False})
                        last = self.request(directory, number, payload)
                        status, answer, finish = base.classify(last)
                        body = last.get('raw_response')
                        if status in ('ok', 'truncated', 'empty') and (
                                not isinstance(body, dict) or body.get('model') != CONFIG['model']):
                            self.halt('response_model_mismatch')
                            status, last = 'interface_error', dict(last, error='response_model_mismatch')
                            break
                        if status != 'error' or number >= base.MAX_HTTP:
                            break
                        if http_try < 2:
                            delay = http_try + 1
                            if last.get('http_status') == 429:
                                try:
                                    delay = max(0, float(last.get('headers', {}).get('retry-after', 60)))
                                except (TypeError, ValueError):
                                    delay = 60
                            elif last.get('http_status') == 503:
                                delay = min(60, 5 * (2 ** http_try))
                            self.sleep(delay)
                    if status == 'truncated' and not retried:
                        maximum, retried = 6000, True
                        continue
                    break
                if status not in ('error', 'empty') or number >= base.MAX_HTTP:
                    break
                if outer < 2:
                    self.sleep(2)
        except base.UncertainRequest as error:
            status, answer, finish = 'needs_reconcile', '', None
            last = {'error': str(error)}
            self.halt('request_needs_reconciliation')
        except base.StopDispatch:
            return None
        if status == 'error':
            status = 'interface_error'
        receipts = [json.loads(p.read_text()) for p in sorted(directory.glob('attempt_*.response.json'))]
        usage = Counter()
        for receipt in receipts:
            add_usage(usage, receipt.get('usage'))
        final = receipts[-1] if receipts else {}
        body = final.get('raw_response') or {}
        body = body if isinstance(body, dict) else {}
        headers = final.get('headers', {})
        result = {k: v for k, v in task.items() if k != 'messages'}
        attempts = len(list(directory.glob('attempt_*.request.json')))
        result.update(model=CONFIG['model'], requested_model=CONFIG['model'], response_model=body.get('model'),
                      response_id=body.get('id'), request_id=headers.get('x-request-id') or headers.get('request-id'),
                      status=status, answer=answer, finish_reason=finish, max_tokens=maximum,
                      length_retried=retried, request_intents=attempts, attempts=attempts,
                      temperature=.3, thinking=False, usage=dict(usage), final_response_usage=final.get('usage'),
                      elapsed_s=round(sum(r.get('elapsed_s', 0) for r in receipts), 3),
                      last_error=last.get('error'), completed_at=base.now(),
                      human_review_note='Nonempty refusal prose retained verbatim; no heuristic refusal detection.',
                      request_sha256_by_token_limit=frozen['request_sha256_by_token_limit'],
                      scoring_performed=False, score_zero_imputed=False)
        base.atomic(terminal, result, exclusive=True)
        return result

    def load_existing(self):
        # Exactly one startup traversal; progress updates never scan the dataset.
        for directory in self.output.glob('task_*'):
            if directory.name not in self.tasks_by_dir:
                raise ValueError('Unexpected task directory in production output')
            task = self.tasks_by_dir[directory.name]
            requests = list(directory.glob('attempt_*.request.json'))
            self.intents += len(requests)
            self.length_retries += any(json.loads(p.read_text())['payload']['max_tokens'] == 6000 for p in requests)
            for path in directory.glob('attempt_*.response.json'):
                self.responses += 1
                add_usage(self.usage, json.loads(path.read_text()).get('usage'))
            terminal = directory / 'terminal.json'
            if terminal.exists():
                result = json.loads(terminal.read_text())
                if (any(result.get(k) != v for k, v in task.items() if k != 'messages')
                        or result.get('status') not in TERMINAL_STATUSES
                        or result.get('requested_model') != CONFIG['model']
                        or result.get('temperature') != .3 or result.get('thinking') is not False):
                    raise ValueError('Existing terminal provenance/parameters invalid')
                if result['status'] == 'ok' and (not result.get('answer', '').strip()
                        or result.get('finish_reason') != 'stop' or result.get('response_model') != CONFIG['model']):
                    raise ValueError('Existing success record is not a complete answer')
                self.results[directory.name] = result
                self.counts[result['status']] += 1

    def checkpoint(self, status):
        with self.guard:
            if self.stop.is_set() and not self.stop_reasons:
                self.stop_reasons.append('external_stop_requested')
            completed = len(self.results)
            record = dict(run_id=CONFIG['run_id'], status=status, updated_at=base.now(), started_at=self.started_at,
                          network_tasks=len(self.data['tasks']), reused_existing_answers=self.reused_count,
                          combined_expected_answers=len(self.data['tasks']) + self.reused_count,
                          reused_answers_imported=False, tasks=len(self.data['tasks']),
                          completed=completed, new_completed=self.new_completed,
                          success=self.counts['ok'], failed=completed - self.counts['ok'],
                          needs_reconcile=self.counts['needs_reconcile'], pending=len(self.data['tasks']) - completed,
                          counts=dict(self.counts), inflight=self.inflight, max_inflight=self.peak,
                          workers=128, actual_attempts=self.intents, recorded_responses=self.responses,
                          unmatched_intents=self.intents - self.responses, length_retries=self.length_retries,
                          usage_totals=dict(self.usage), walltime_s=round(time.monotonic() - self.started, 3),
                          stop_reasons=list(self.stop_reasons), sampler_count=self.samples,
                          retry_policy=RETRY_POLICY, scoring_performed=False, paid_api_used=False,
                          formal_answers=True, session_max_new_tasks=self.max_new_tasks)
            base.atomic(self.output / 'run_state.json', record)
            base.atomic(self.output / 'summary.json', record)
            return record

    def sample(self):
        try:
            sample = self.sampler()
        except Exception as error:
            sample = dict(healthy=False, probe_error=type(error).__name__)
        with self.guard:
            base.atomic(self.output / 'samples' / ('sample_%06d.json' % self.samples), sample, exclusive=True)
            self.samples += 1
        if sample.get('healthy') is not True:
            self.halt('service_health_failure')
        gpus = sample.get('gpus') or []
        gpu_complete = (sample.get('gpu_returncode') == 0 and len(gpus) == 8
                        and len({gpu.get('index') for gpu in gpus}) == 8)
        gpu_fields = ('utilization_pct', 'memory_used_mib', 'memory_total_mib', 'power_w', 'temperature_c')
        for gpu in gpus:
            try:
                gpu_complete = (gpu_complete and bool(gpu.get('name'))
                                and all(math.isfinite(float(gpu[k])) for k in gpu_fields))
            except (KeyError, TypeError, ValueError):
                gpu_complete = False
        if sample.get('gpu_anomalies') or sample.get('gpu_error') or not gpu_complete:
            self.halt('gpu_observation_failure')
        metrics = sample.get('metrics') or {}
        required = ('running', 'waiting', 'kv_cache_usage', 'preemptions_total')
        available = all(isinstance(metrics.get(k), (int, float)) and math.isfinite(metrics[k]) for k in required)
        self.metrics_missing = 0 if available else self.metrics_missing + 1
        if self.metrics_missing >= 3:
            self.halt('critical_metrics_missing_three_samples')
        if available:
            if metrics['kv_cache_usage'] >= .85:
                self.halt('kv_cache_at_or_above_85_percent')
            current = metrics['preemptions_total']
            if self.last_preempt is not None and current > self.last_preempt:
                self.halt('new_server_preemption')
            self.last_preempt = current

    def monitor(self):
        while not self.monitor_done.wait(5):
            try:
                self.sample()
                self.checkpoint('pausing' if self.stop.is_set() else 'running')
            except Exception as error:
                self.halt('monitor_exception_' + type(error).__name__)
                try:
                    self.checkpoint('pausing')
                except Exception:
                    pass
                return

    def export(self):
        # Full answer JSONL materialization happens only on session exit.
        for split in ('primary', 'secondary'):
            rows = (self.results[self.task_dir(t).name] for t in self.data['tasks']
                    if t['split'] == split and self.task_dir(t).name in self.results)
            base.atomic(self.output / ('answers_deepseek_v4_flash_' + split + '.jsonl'),
                        ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), raw=True)
        base.atomic(self.output / 'failures.jsonl', ''.join(json.dumps(r, ensure_ascii=False) + '\n'
                    for r in self.results.values() if r['status'] != 'ok'), raw=True)

    def run(self):
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = self.output / 'run_identity.json'
            if path.exists():
                if not self.resume:
                    raise ValueError('Existing production output requires explicit --resume')
                if json.loads(path.read_text()) != self.identity:
                    raise ValueError('Resume identity mismatch: input/runner/helper/monitor/parameters changed')
            else:
                if self.resume:
                    raise ValueError('No previous production run to resume')
                if any(p.name != '.lock' for p in self.output.iterdir()):
                    raise ValueError('Nonempty output without run identity')
                base.atomic(path, self.identity, exclusive=True)
            self.load_existing()
            for name in ('samples', 'sessions'):
                (self.output / name).mkdir(exist_ok=True)
            self.samples = len(list((self.output / 'samples').glob('sample_*.json')))
            self.started, self.started_at = time.monotonic(), base.now()
            session = self.output / 'sessions' / ('session_%04d.json' % (len(list((self.output / 'sessions').glob('session_*.json'))) + 1))
            pending_tasks = [t for t in self.data['tasks'] if self.task_dir(t).name not in self.results]
            if self.max_new_tasks is not None:
                pending_tasks = pending_tasks[:self.max_new_tasks]
            if not pending_tasks:
                self.export()
                final = self.checkpoint('completed_with_failures' if self.counts['ok'] != len(self.data['tasks']) else 'completed')
                base.atomic(session, final, exclusive=True)
                return final
            benchmark.check_endpoint(self.endpoint, 128)
            self.checkpoint('starting')
            self.sample()
            monitor = threading.Thread(target=self.monitor, daemon=True)
            monitor.start()
            iterator, failure = iter(pending_tasks), None
            try:
                with ThreadPoolExecutor(max_workers=128) as pool:
                    pending = set()
                    def dispatch():
                        while not self.stop.is_set() and len(pending) < 128:
                            task = next(iterator, None)
                            if task is None:
                                break
                            pending.add(pool.submit(self.task, task))
                    dispatch()
                    while pending:
                        done, pending = wait(pending, return_when=FIRST_COMPLETED)
                        for future in done:
                            try:
                                result = future.result()
                            except Exception as error:
                                self.halt('runner_exception_' + type(error).__name__)
                                failure = failure or error
                                continue
                            if result is not None:
                                with self.guard:
                                    key = self.task_dir(result).name
                                    if key not in self.results:
                                        self.results[key] = result
                                        self.counts[result['status']] += 1
                                        self.new_completed += 1
                                    interface_failures = self.counts['interface_error'] + self.counts['needs_reconcile']
                                    if interface_failures / max(1, len(self.results)) > .10:
                                        self.halt('interface_failure_rate_above_10_percent')
                        dispatch()
            finally:
                self.monitor_done.set()
                monitor.join(timeout=15)
                self.export()
                status = ('paused' if self.stop.is_set() else
                          'segment_completed' if len(self.results) < len(self.data['tasks']) else
                          'completed_with_failures' if self.counts['ok'] != len(self.data['tasks']) else 'completed')
                final = self.checkpoint(status)
                base.atomic(session, final, exclusive=True)
            if failure:
                raise failure
            return final


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--max-new-tasks', type=int)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if os.environ.get('SLURM_JOB_ID') != '402816':
        parser.error('Use only the existing allocation 402816')
    if args.input.absolute() != ROOT / 'input_tasks.json' or args.input.resolve() != ROOT / 'input_tasks.json':
        parser.error('Unexpected production input path')
    if args.output_dir.absolute() != ROOT / 'results' or args.output_dir.resolve() != ROOT / 'results':
        parser.error('Unexpected production output path')
    if base.KEY.is_symlink() or base.KEY.stat().st_uid != os.getuid() or base.KEY.stat().st_mode & 0o077:
        parser.error('Unsafe service key file')
    key = base.KEY.read_text().strip()
    if len(key) < 32:
        parser.error('Invalid service key')
    endpoint = json.loads(ENDPOINT.read_text())
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    runner = Runner(args.input.read_bytes(), args.output_dir, benchmark.HTTP(key), benchmark.Sampler(key), endpoint,
                    resume=args.resume, max_new_tasks=args.max_new_tasks, stop=stop)
    print(json.dumps(runner.run(), ensure_ascii=False))


if __name__ == '__main__':
    main()
