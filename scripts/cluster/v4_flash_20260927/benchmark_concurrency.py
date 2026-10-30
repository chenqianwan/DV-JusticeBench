#!/usr/bin/env python3
"""Isolated 256-token performance probes, never formal experiment answers."""
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
import subprocess
import threading
import time
import urllib.error
import urllib.request

import run_answer_smoke as base

ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_concurrency_benchmark_20260927')
ENDPOINT = base.KEY.parent / 'endpoint.json'
CONFIG = dict(model='deepseek_v4_flash', base_url='http://dgx-040:18044/v1',
              service_job_id='402816', temperature=0.3, max_tokens=256, thinking=False, timeout_s=1800)
CONCURRENCIES = (16, 32, 64, 128)


def validate(data):
    if set(data) != set(CONFIG) | {'run_id', 'tasks'}:
        raise ValueError('Unexpected benchmark input fields')
    if not isinstance(data['run_id'], str) or not data['run_id']:
        raise ValueError('Missing run identifier')
    for key, value in CONFIG.items():
        if data.get(key) != value or type(data.get(key)) is not type(value):
            raise ValueError('Benchmark parameter mismatch: ' + key)
    if len(data['tasks']) != 256:
        raise ValueError('Exactly 256 frozen benchmark tasks required')
    ids = set()
    for task in data['tasks']:
        if set(task) != {'task_id', 'case_id', 'question_id', 'messages', 'prompt_hash'}:
            raise ValueError('Unexpected benchmark task fields')
        if any(not isinstance(task[k], str) or not task[k] for k in ('task_id', 'case_id', 'question_id')):
            raise ValueError('Invalid benchmark identifiers')
        if task['task_id'] in ids:
            raise ValueError('Duplicate benchmark task identifier')
        ids.add(task['task_id'])
        messages = task['messages']
        if not isinstance(messages, list) or len(messages) != 2:
            raise ValueError('Frozen system/user prompt required')
        for message, role in zip(messages, ('system', 'user')):
            if set(message) != {'role', 'content'} or message['role'] != role or not isinstance(message['content'], str):
                raise ValueError('Invalid prompt structure')
        if task['prompt_hash'] != base.digest(base.canonical(messages)):
            raise ValueError('Benchmark prompt hash mismatch')


def check_endpoint(endpoint, concurrency):
    expected = {'status': 'ready', 'job_id': '402816', 'model': CONFIG['model'], 'hostname': 'dgx-040'}
    if any(endpoint.get(k) != v for k, v in expected.items()):
        raise ValueError('Service endpoint identity/readiness mismatch')
    cap = endpoint.get('max_num_seqs')
    if type(cap) is not int or cap < concurrency:
        raise ValueError('Verified service max_num_seqs is below requested concurrency')
    return cap


def parse_metrics(text):
    values = {}
    for line in text.splitlines():
        match = re.match(r'^([\w:]+)(?:\{.*\})?\s+([-+\d.eE]+)(?:\s|$)', line)
        if match:
            value = float(match.group(2))
            if math.isfinite(value):
                values.setdefault(match.group(1), []).append(value)
    def collect(names, maximum=False):
        for name in names:
            if name in values:
                return max(values[name]) if maximum else sum(values[name])
        return None
    return {'running': collect(['vllm:num_requests_running']),
            'waiting': collect(['vllm:num_requests_waiting']),
            'kv_cache_usage': collect(['vllm:kv_cache_usage_perc', 'vllm:gpu_cache_usage_perc'], True),
            'preemptions_total': collect(['vllm:num_preemptions_total']),
            'prompt_tokens_total': collect(['vllm:prompt_tokens_total']),
            'generation_tokens_total': collect(['vllm:generation_tokens_total']),
            'prefix_cache_queries': collect(['vllm:prefix_cache_queries_total', 'vllm:prefix_cache_queries']),
            'prefix_cache_hits': collect(['vllm:prefix_cache_hits_total', 'vllm:prefix_cache_hits'])}


class HTTP(base.HTTP):
    def __call__(self, payload):
        request = urllib.request.Request(CONFIG['base_url'] + '/chat/completions',
                    data=base.canonical(payload), method='POST', headers={
                        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key})
        try:
            response = self.opener.open(request, timeout=CONFIG['timeout_s'])
        except urllib.error.HTTPError as error:
            response = error
        with response:
            headers = {k.lower(): v.replace(self.key, '[REDACTED]') for k, v in response.headers.items()
                       if k.lower() in {'retry-after', 'x-request-id', 'request-id'}}
            return response.code, headers, response.read().decode('utf-8', errors='replace').replace(self.key, '[REDACTED]')


class Sampler:
    def __init__(self, key):
        self.key = key
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def get(self, path):
        request = urllib.request.Request(CONFIG['base_url'].removesuffix('/v1') + path,
                                        headers={'Authorization': 'Bearer ' + self.key})
        with self.opener.open(request, timeout=3) as response:
            return response.status, response.read().decode(errors='replace').replace(self.key, '[REDACTED]')

    def __call__(self):
        sample = {'at': base.now(), 'healthy': False, 'gpu_anomalies': []}
        try:
            sample['health_status'], _ = self.get('/health')
            sample['healthy'] = sample['health_status'] == 200
        except Exception as error:
            sample['health_error'] = type(error).__name__
        try:
            status, metrics = self.get('/metrics')
            sample['metrics_status'], sample['metrics_raw'] = status, metrics
            sample['metrics'] = parse_metrics(metrics)
        except Exception as error:
            sample['metrics_error'] = type(error).__name__
        try:
            command = ['nvidia-smi', '--query-gpu=index,name,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu',
                       '--format=csv,noheader,nounits']
            gpu = subprocess.run(command, capture_output=True, text=True, timeout=5)
            sample['gpu_returncode'] = gpu.returncode
            sample['gpu_raw'] = gpu.stdout.replace(self.key, '[REDACTED]')
            if gpu.returncode:
                sample['gpu_error'] = gpu.stderr.replace(self.key, '[REDACTED]')[:1000]
                sample['gpu_anomalies'].append('nvidia_smi_failed')
            else:
                sample['gpus'] = []
                for line in gpu.stdout.splitlines():
                    row = [part.strip() for part in line.split(',')]
                    if len(row) == 7:
                        sample['gpus'].append(dict(zip(('index', 'name', 'utilization_pct', 'memory_used_mib',
                                                       'memory_total_mib', 'power_w', 'temperature_c'), row)))
                        try:
                            if float(row[-1]) >= 90:
                                sample['gpu_anomalies'].append('gpu_temperature_ge_90c')
                        except ValueError:
                            pass
        except FileNotFoundError:
            sample['gpu_error'] = 'nvidia_smi_unavailable'
        except Exception as error:
            sample['gpu_error'] = type(error).__name__
            sample['gpu_anomalies'].append('gpu_probe_failed')
        return sample


def percentile(values, fraction):
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    lo, hi = math.floor(position), math.ceil(position)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (position - lo)


class Runner(base.Runner):
    def __init__(self, input_bytes, output_dir, concurrency, transport, sampler, endpoint, stop=None):
        self.data = json.loads(input_bytes)
        validate(self.data)
        if concurrency not in CONCURRENCIES:
            raise ValueError('Concurrency must be 16, 32, 64, or 128')
        self.concurrency, self.tasks = concurrency, self.data['tasks'][:2 * concurrency]
        self.output = Path(output_dir) / ('c%03d' % concurrency)
        self.transport, self.sampler, self.endpoint = transport, sampler, endpoint
        self.stop = stop or threading.Event()
        self.guard, self.monitor_done = threading.RLock(), threading.Event()
        self.inflight, self.peak, self.completed, self.failed = 0, 0, 0, 0
        self.stop_reasons, self.samples, self.results = [], [], {}
        self.identity = dict(input_sha256=base.digest(input_bytes), script_sha256=base.digest(Path(__file__).read_bytes()),
                             helper_sha256=base.digest(Path(base.__file__).read_bytes()), parameters=CONFIG,
                             concurrency=concurrency, tasks=len(self.tasks))

    def task_dir(self, task):
        return self.output / ('task_' + base.digest(task['task_id'].encode())[:24])

    def halt(self, reason):
        with self.guard:
            if reason not in self.stop_reasons:
                self.stop_reasons.append(reason)
            self.stop.set()

    def state(self):
        with self.guard:
            base.atomic(self.output / 'run_state.json', dict(updated_at=base.now(), concurrency=self.concurrency,
                        intended_tasks=len(self.tasks), completed=self.completed, failed=self.failed,
                        inflight=self.inflight, max_inflight=self.peak, stop_reasons=list(self.stop_reasons),
                        stop_requested=self.stop.is_set(), formal_experiment=False))

    def sample(self):
        try:
            sample = self.sampler()
        except Exception as error:
            sample = dict(at=base.now(), healthy=False, probe_error=type(error).__name__)
        with self.guard:
            index = len(self.samples)
            base.atomic(self.output / ('sample_%04d.json' % index), sample, exclusive=True)
            self.samples.append(sample)
        if not sample.get('healthy'):
            self.halt('service_health_failure')
        if sample.get('gpu_anomalies'):
            self.halt('gpu_anomaly')
        self.state()

    def monitor(self):
        while not self.monitor_done.wait(5):
            self.sample()

    def task(self, task):
        directory = self.task_dir(task)
        directory.mkdir(exist_ok=True)
        terminal = directory / 'terminal.json'
        if terminal.exists():
            return json.loads(terminal.read_text())
        payload = {'model': CONFIG['model'], 'messages': task['messages'], 'max_tokens': 256,
                   'temperature': 0.3, 'chat_template_kwargs': {'thinking': False}, 'stream': False}
        with self.guard:
            if self.stop.is_set():
                return None
            self.inflight += 1
            self.peak = max(self.peak, self.inflight)
        self.state()
        try:
            try:
                receipt = self.request(directory, 1, payload)
                status, content, finish = base.classify(receipt)
                if status == 'truncated' and content.strip():
                    status = 'ok_length'
                elif status == 'ok':
                    status = 'ok_stop'
            except base.UncertainRequest:
                receipt, status, content, finish = {}, 'interrupted', '', None
            except base.StopDispatch:
                return None
            body = receipt.get('raw_response') or {}
            body = body if isinstance(body, dict) else {}
            if status in ('ok_stop', 'ok_length') and body.get('model') != CONFIG['model']:
                status = 'error'
                receipt = dict(receipt, error='response_model_mismatch')
            result = {k: task[k] for k in ('task_id', 'case_id', 'question_id', 'prompt_hash')}
            result.update(status=status, finish_reason=finish, usage=receipt.get('usage') or {},
                          http_status=receipt.get('http_status'), response_id=body.get('id'), response_model=body.get('model'),
                          request_id=receipt.get('headers', {}).get('x-request-id'), latency_s=receipt.get('elapsed_s'),
                          output_chars=len(content), error=receipt.get('error'), completed_at=base.now(),
                          formal_experiment_answer=False, maximum_output_tokens=256, automatic_retries=0)
            if 'out of memory' in json.dumps(body, ensure_ascii=False).lower():
                self.halt('server_out_of_memory')
            base.atomic(terminal, result, exclusive=True)
            return result
        finally:
            with self.guard:
                self.inflight -= 1
            self.state()

    def summarize(self, elapsed):
        if self.stop.is_set() and not self.stop_reasons:
            self.stop_reasons.append('external_stop_requested')
        rows = list(self.results.values())
        ok = [r for r in rows if r['status'] in ('ok_stop', 'ok_length')]
        usage = Counter()
        for row in rows:
            for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
                value = row['usage'].get(key, 0)
                if isinstance(value, (int, float)):
                    usage[key] += value
        valid_output = sum(r['usage'].get('completion_tokens', 0) or 0 for r in ok)
        latency = [r['latency_s'] for r in rows if isinstance(r['latency_s'], (int, float))]
        metrics = [s.get('metrics', {}) for s in self.samples]
        kv = [m['kv_cache_usage'] for m in metrics if m.get('kv_cache_usage') is not None]
        preempt = [m['preemptions_total'] for m in metrics if m.get('preemptions_total') is not None]
        running = [m['running'] for m in metrics if m.get('running') is not None]
        waiting = [m['waiting'] for m in metrics if m.get('waiting') is not None]
        queries = [m['prefix_cache_queries'] for m in metrics if m.get('prefix_cache_queries') is not None]
        hits = [m['prefix_cache_hits'] for m in metrics if m.get('prefix_cache_hits') is not None]
        return dict(run_id=self.data['run_id'], concurrency=self.concurrency, intended_tasks=len(self.tasks),
                    completed=len(rows), successful=len(ok), failed=len(rows) - len(ok),
                    not_dispatched=len(self.tasks) - len(rows), counts=dict(Counter(r['status'] for r in rows)),
                    success_rate=len(ok) / len(rows) if rows else None,
                    intended_completion_rate=len(rows) / len(self.tasks), max_inflight=self.peak,
                    completion_tokens_per_s=valid_output / elapsed if elapsed > 0 else None,
                    successful_requests_per_s=len(ok) / elapsed if elapsed > 0 else None,
                    completed_requests_per_s=len(rows) / elapsed if elapsed > 0 else None,
                    p50_latency_s=percentile(latency, .5), p95_latency_s=percentile(latency, .95),
                    usage_totals=dict(usage), valid_completion_tokens=valid_output, elapsed_s=elapsed,
                    kv_cache_peak=max(kv) if kv else None,
                    preemption_delta=preempt[-1] - preempt[0] if len(preempt) >= 2 else None,
                    server_running_peak=max(running) if running else None,
                    server_waiting_peak=max(waiting) if waiting else None,
                    prefix_cache_queries_delta=queries[-1] - queries[0] if len(queries) >= 2 else None,
                    prefix_cache_hits_delta=hits[-1] - hits[0] if len(hits) >= 2 else None,
                    gpu_anomalies=sorted({a for s in self.samples for a in s.get('gpu_anomalies', [])}),
                    stop_reasons=self.stop_reasons, samples=len(self.samples), completed_at=base.now(),
                    formal_experiment=False, output_cap=256, automatic_retries=0,
                    request_intents=len(list(self.output.glob('task_*/attempt_001.request.json'))),
                    recorded_responses=len(list(self.output.glob('task_*/attempt_001.response.json'))),
                    warm_cache_warning='Each round reuses the first 2N prompts from one fixed ordering; later rounds may benefit from prefix/KV cache warming.')

    def run(self):
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            identity = self.output / 'run_identity.json'
            if identity.exists():
                if json.loads(identity.read_text()) != self.identity:
                    raise ValueError('Benchmark resume identity mismatch')
            else:
                if any(p.name != '.lock' for p in self.output.iterdir()):
                    raise ValueError('Unexpected existing benchmark files')
                base.atomic(identity, self.identity, exclusive=True)
            final = self.output / 'summary.json'
            if final.exists():
                return json.loads(final.read_text())
            # An unfinished round is conservatively finalized, never dispatched a second time.
            existing = list(self.output.glob('task_*/attempt_001.request.json'))
            if existing:
                self.halt('interrupted_round_requires_manual_review_no_redispatch')
                for task in self.tasks:
                    directory = self.task_dir(task)
                    terminal = directory / 'terminal.json'
                    if terminal.exists():
                        self.results[task['task_id']] = json.loads(terminal.read_text())
                previous = self.output / 'run_state.json'
                if previous.exists():
                    self.peak = json.loads(previous.read_text()).get('max_inflight', 0)
                summary = self.summarize(0)
                summary['unfinished_request_intents'] = len(existing) - len(self.results)
                base.atomic(final, summary, exclusive=True)
                return summary
            check_endpoint(self.endpoint, self.concurrency)
            self.sample()
            started = time.monotonic()
            monitoring = threading.Thread(target=self.monitor, daemon=True)
            monitoring.start()
            iterator = iter(self.tasks)
            try:
                with ThreadPoolExecutor(max_workers=self.concurrency) as pool:
                    pending = set()
                    def dispatch():
                        while not self.stop.is_set() and len(pending) < self.concurrency:
                            task = next(iterator, None)
                            if task is None:
                                break
                            pending.add(pool.submit(self.task, task))
                    dispatch()
                    while pending:
                        done, pending = wait(pending, return_when=FIRST_COMPLETED)
                        for future in done:
                            result = future.result()
                            if result is not None:
                                with self.guard:
                                    self.results[result['task_id']] = result
                                    self.completed += 1
                                    self.failed += result['status'] not in ('ok_stop', 'ok_length')
                                    if self.failed / self.completed > .10:
                                        self.halt('failure_rate_above_10_percent')
                                self.state()
                        dispatch()
            finally:
                elapsed = time.monotonic() - started
                self.monitor_done.set()
                monitoring.join(timeout=15)
            self.sample()
            summary = self.summarize(elapsed)
            base.atomic(self.output / 'results.jsonl', ''.join(json.dumps(r, ensure_ascii=False) + '\n'
                        for r in self.results.values()), raw=True)
            base.atomic(final, summary, exclusive=True)
            self.state()
            return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--concurrency', required=True, type=int, choices=CONCURRENCIES)
    args = parser.parse_args()
    if os.environ.get('SLURM_JOB_ID') != '402816':
        parser.error('Use only existing service allocation 402816')
    if args.input.absolute() != ROOT / 'input_benchmark.json' or args.input.resolve() != ROOT / 'input_benchmark.json':
        parser.error('Unexpected benchmark input path')
    if args.output_dir.absolute() != ROOT / 'results' or args.output_dir.resolve() != ROOT / 'results':
        parser.error('Unexpected benchmark output path')
    if base.KEY.is_symlink() or base.KEY.stat().st_uid != os.getuid() or base.KEY.stat().st_mode & 0o077:
        parser.error('Unsafe service key')
    key = base.KEY.read_text().strip()
    if len(key) < 32:
        parser.error('Invalid service key')
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    endpoint = json.loads(ENDPOINT.read_text())
    runner = Runner(args.input.read_bytes(), args.output_dir, args.concurrency, HTTP(key), Sampler(key), endpoint, stop)
    print(json.dumps(runner.run(), ensure_ascii=False))


if __name__ == '__main__':
    main()
