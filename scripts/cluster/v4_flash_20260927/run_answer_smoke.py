#!/usr/bin/env python3
"""Frozen ten-question, answer-only smoke run inside allocation 402816."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import tempfile
import threading
import time
import urllib.error
import urllib.request

ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_answering_smoke_20260927')
KEY = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20260927/api_key')
CONFIG = dict(run_id='pkulaw_v4_answer_smoke_20260927', service_job_id='402816',
              base_url='http://dgx-040:18044/v1', model='deepseek_v4_flash',
              thinking=False, temperature=0.3, initial_max_tokens=3000,
              timeout_s=180, workers=2)
TASK_FIELDS = {'split', 'case_id', 'question_id', 'messages', 'prompt_hash',
               'source_case_text_hash', 'masked_case_text_hash', 'question_hash', 'question_text'}
MAX_HTTP = 18


def digest(value):
    return hashlib.sha256(value).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def atomic(path, value, exclusive=False, raw=False):
    """Atomic durable writes; attempt and terminal records are never overwritten."""
    path = Path(path)
    data = value.encode() if raw else (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    fd, name = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if exclusive:
            os.link(name, path)
        else:
            os.replace(name, path)
        dirfd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def validate(data):
    if set(data) - (set(CONFIG) | {'tasks', 'input_manifest_hash'}):
        raise ValueError('Unexpected input fields')
    for key, expected in CONFIG.items():
        if data.get(key) != expected or type(data.get(key)) is not type(expected):
            raise ValueError('Frozen parameter mismatch: ' + key)
    tasks = data.get('tasks', [])
    if len(tasks) != 10:
        raise ValueError('Exactly ten tasks required')
    keys, cases = set(), {'primary': set(), 'secondary': set()}
    counts = Counter()
    for task in tasks:
        if set(task) != TASK_FIELDS:
            raise ValueError('Unexpected/missing task fields; do not add judgment or title')
        if task['split'] not in cases:
            raise ValueError('Invalid split')
        if any(not isinstance(task[x], str) or not task[x] for x in ('case_id', 'question_id', 'question_text')):
            raise ValueError('Invalid task identifiers/question')
        key = (task['split'], task['case_id'], task['question_id'])
        if key in keys:
            raise ValueError('Duplicate task key')
        keys.add(key)
        cases[task['split']].add(task['case_id'])
        counts[task['split']] += 1
        messages = task['messages']
        if not isinstance(messages, list) or len(messages) != 2:
            raise ValueError('Exactly system/user messages required')
        for message, role in zip(messages, ('system', 'user')):
            if set(message) != {'role', 'content'} or message['role'] != role or not isinstance(message['content'], str):
                raise ValueError('Frozen system/user message format mismatch')
        if digest(canonical(messages)) != task['prompt_hash']:
            raise ValueError('Prompt hash mismatch')
        if digest(task['question_text'].encode()) != task['question_hash']:
            raise ValueError('Question hash mismatch')
        if task['question_text'] not in messages[1]['content']:
            raise ValueError('Question missing from user message')
        for field in ('prompt_hash', 'source_case_text_hash', 'masked_case_text_hash', 'question_hash'):
            if len(task[field]) != 64 or any(c not in '0123456789abcdef' for c in task[field]):
                raise ValueError('Invalid provenance hash')
    if counts != {'primary': 5, 'secondary': 5} or any(len(c) != 1 for c in cases.values()):
        raise ValueError('Require one case/five questions per split')
    if cases['primary'] & cases['secondary']:
        raise ValueError('Split cases must be different')


class HTTP:
    def __init__(self, key):
        self.key = key
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, payload):
        request = urllib.request.Request(CONFIG['base_url'] + '/chat/completions',
                    data=canonical(payload), method='POST', headers={
                        'Content-Type': 'application/json', 'Authorization': 'Bearer ' + self.key})
        try:
            response = self.opener.open(request, timeout=CONFIG['timeout_s'])
        except urllib.error.HTTPError as error:
            response = error
        with response:
            # Never retain headers containing cookies, authorization, or service credentials.
            headers = {k.lower(): v for k, v in response.headers.items()
                       if k.lower() in {'retry-after', 'x-request-id', 'request-id'}}
            body = response.read().decode('utf-8', errors='replace').replace(self.key, '[REDACTED]')
            return response.code, headers, body


def classify(record):
    body = record.get('raw_response')
    if not isinstance(body, dict):
        return 'error', '', None
    error = body.get('error')
    code = error.get('code') or error.get('type') if isinstance(error, dict) else None
    if code in ('content_filter', 'content_policy_violation'):
        return 'content_filter', '', code
    if code in ('refusal', 'model_refusal', 'safety_refusal'):
        return 'refusal', '', code
    if record.get('http_status') != 200 or error:
        return 'error', '', code
    choices = body.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return 'error', '', None
    choice = choices[0]
    message = choice.get('message') or {}
    if not isinstance(message, dict):
        return 'error', '', None
    answer = message.get('content')
    answer = answer if isinstance(answer, str) else ''
    finish = choice.get('finish_reason')
    if message.get('refusal'):
        return 'refusal', answer, finish
    if finish == 'content_filter':
        return 'content_filter', answer, finish
    if finish in ('length', 'max_tokens'):
        return 'truncated', answer, finish
    if finish == 'stop':
        return ('ok' if answer.strip() else 'empty'), answer, finish
    return 'error', answer, finish


class UncertainRequest(Exception):
    pass


class StopDispatch(Exception):
    pass


class Runner:
    def __init__(self, input_bytes, output_dir, transport, sleep=time.sleep, stop=None):
        self.data = json.loads(input_bytes)
        validate(self.data)
        self.output = Path(output_dir)
        self.transport, self.sleep = transport, sleep
        self.stop = stop or threading.Event()
        self.identity = {'input_sha256': digest(input_bytes), 'script_sha256': digest(Path(__file__).read_bytes()),
                         'parameters': CONFIG, 'http_limit_per_task': MAX_HTTP}

    def task_dir(self, task):
        return self.output / ('task_' + digest(canonical([task[k] for k in ('split', 'case_id', 'question_id')]))[:24])

    def request(self, directory, number, payload):
        if number > MAX_HTTP:
            return {'error': 'persisted_http_limit_exceeded'}
        req = directory / ('attempt_%03d.request.json' % number)
        res = directory / ('attempt_%03d.response.json' % number)
        if req.exists():
            saved = json.loads(req.read_text())
            if saved['payload'] != payload:
                raise ValueError('Recorded request differs from replay')
            if not res.exists():
                raise UncertainRequest('Request intent exists without response; manual reconciliation required')
            return json.loads(res.read_text())
        if res.exists():
            raise ValueError('Orphan response')
        if self.stop.is_set():
            raise StopDispatch()
        atomic(req, {'started_at': now(), 'attempt': number, 'payload': payload}, exclusive=True)
        started = time.monotonic()
        record = {'completed_at': None}
        try:
            status, headers, body = self.transport(payload)
            record.update(http_status=status, headers=headers, raw_body=body)
            try:
                record['raw_response'] = json.loads(body)
            except (ValueError, TypeError):
                record['error'] = 'invalid_json_response'
        except Exception as error:
            # Exception type is sufficient, and cannot expose authorization/key contents.
            record['error'] = 'transport_' + type(error).__name__
        record.update(completed_at=now(), elapsed_s=round(time.monotonic() - started, 3))
        parsed = record.get('raw_response')
        if isinstance(parsed, dict):
            record['usage'] = parsed.get('usage')
            record['request_id'] = parsed.get('id') or record.get('headers', {}).get('x-request-id')
        record['classification'], _, record['finish_reason'] = classify(record)
        atomic(res, record, exclusive=True)
        return record

    def task(self, task):
        directory = self.task_dir(task)
        directory.mkdir(exist_ok=True)
        terminal = directory / 'terminal.json'
        if terminal.exists():
            return json.loads(terminal.read_text())
        number, maximum, length_retried = 0, CONFIG['initial_max_tokens'], False
        last, answer, finish = {}, '', None
        status = 'error'
        try:
            for outer in range(3):
                while True:
                    for http_try in range(3):
                        number += 1
                        payload = {'model': CONFIG['model'], 'messages': task['messages'],
                                   'temperature': CONFIG['temperature'], 'max_tokens': maximum,
                                   'chat_template_kwargs': {'thinking': False}}
                        last = self.request(directory, number, payload)
                        status, answer, finish = classify(last)
                        if status != 'error' or number >= MAX_HTTP:
                            break
                        if http_try < 2:
                            delay = http_try + 1
                            if last.get('http_status') == 429:
                                try:
                                    delay = max(0, float(last.get('headers', {}).get('retry-after', 60)))
                                except (TypeError, ValueError):
                                    delay = 60
                            self.sleep(delay)
                    if status == 'truncated' and not length_retried:
                        maximum, length_retried = 6000, True
                        continue
                    break
                if status not in ('error', 'empty') or number >= MAX_HTTP:
                    break
                if outer < 2:
                    self.sleep(2)
        except UncertainRequest as error:
            status, answer, finish = 'interrupted', '', None
            last = {'error': str(error)}
        except StopDispatch:
            return None
        receipts = [json.loads(path.read_text()) for path in sorted(directory.glob('attempt_*.response.json'))]
        cumulative_usage = Counter()
        for receipt in receipts:
            recorded_usage = receipt.get('usage')
            if isinstance(recorded_usage, dict):
                for key, value in recorded_usage.items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        cumulative_usage[key] += value
        final_receipt = receipts[-1] if receipts else {}
        final_body = final_receipt.get('raw_response')
        final_body = final_body if isinstance(final_body, dict) else {}
        final_headers = final_receipt.get('headers', {})
        attempts = len(list(directory.glob('attempt_*.request.json')))
        result = {k: v for k, v in task.items() if k != 'messages'}
        result.update(model=CONFIG['model'], status=status, answer=answer, finish_reason=finish,
                      max_tokens=maximum, length_retried=length_retried,
                      request_intents=attempts, attempts=attempts,
                      requested_model=CONFIG['model'], response_model=final_body.get('model'),
                      response_id=final_body.get('id'),
                      request_id=final_headers.get('x-request-id') or final_headers.get('request-id'),
                      temperature=CONFIG['temperature'], thinking=CONFIG['thinking'],
                      usage=dict(cumulative_usage), final_response_usage=final_receipt.get('usage'),
                      elapsed_s=round(sum(r.get('elapsed_s', 0) for r in receipts), 3),
                      last_error=last.get('error'), completed_at=now(),
                      human_review_note='Nonempty refusal prose is retained verbatim; no heuristic refusal detection.')
        atomic(terminal, result, exclusive=True)
        return result

    def summarize(self, started):
        results, counts, usage, requests, responses = [], Counter(), Counter(), 0, 0
        for task in self.data['tasks']:
            directory = self.task_dir(task)
            terminal = directory / 'terminal.json'
            if terminal.exists():
                result = json.loads(terminal.read_text())
                results.append(result)
                counts[result['status']] += 1
            else:
                counts['pending'] += 1
            requests += len(list(directory.glob('attempt_*.request.json')))
            for path in directory.glob('attempt_*.response.json'):
                responses += 1
                recorded = json.loads(path.read_text()).get('usage')
                if isinstance(recorded, dict):
                    for key, value in recorded.items():
                        if isinstance(value, (int, float)) and not isinstance(value, bool):
                            usage[key] += value
        for split in ('primary', 'secondary'):
            rows = [r for r in results if r['split'] == split]
            atomic(self.output / ('answers_deepseek_v4_flash_' + split + '.jsonl'),
                   ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows), raw=True)
        atomic(self.output / 'failures.jsonl', ''.join(json.dumps(r, ensure_ascii=False) + '\n'
               for r in results if r['status'] != 'ok'), raw=True)
        summary = dict(run_id=CONFIG['run_id'], cases=2, tasks=10, success=counts['ok'],
                       failed=sum(v for k, v in counts.items() if k not in ('ok', 'pending')),
                       counts=dict(counts), actual_attempts=requests, recorded_responses=responses,
                       uncertain_request_intents=requests - responses, usage_totals=dict(usage),
                       current_session_walltime_s=round(time.monotonic() - started, 3),
                       finished_at=now(), scoring_performed=False, paid_api_used=False,
                       attempts_note='Request intents are counted conservatively; unmatched intents may have reached server.')
        atomic(self.output / 'summary.json', summary)
        return summary

    def run(self):
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            manifest = self.output / 'run_identity.json'
            if manifest.exists():
                if json.loads(manifest.read_text()) != self.identity:
                    raise ValueError('Resume refused: input/script/parameters changed')
            else:
                if any(p.name != '.lock' for p in self.output.iterdir()):
                    raise ValueError('Output is not empty and has no identity')
                atomic(manifest, self.identity, exclusive=True)
            started = time.monotonic()
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    list(pool.map(self.task, self.data['tasks']))
            finally:
                summary = self.summarize(started)
            return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('SLURM_JOB_ID') != CONFIG['service_job_id']:
        parser.error('Must run inside existing allocation 402816')
    if args.output_dir.absolute() != ROOT / 'results' or args.output_dir.resolve() != ROOT / 'results':
        parser.error('Output must be the fixed personal smoke results directory')
    if args.input.resolve().parent != ROOT:
        parser.error('Input must be staged directly in the fixed smoke root')
    if KEY.is_symlink() or KEY.stat().st_uid != os.getuid() or KEY.stat().st_mode & 0o077:
        parser.error('Unsafe service key file ownership/permissions')
    key = KEY.read_text().strip()
    if len(key) < 32:
        parser.error('Invalid service key')
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    runner = Runner(args.input.read_bytes(), args.output_dir, HTTP(key), stop=stop,
                    sleep=lambda seconds: stop.wait(seconds))
    print(json.dumps(runner.run(), ensure_ascii=False))


if __name__ == '__main__':
    main()
