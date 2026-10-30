#!/usr/bin/env python3
"""Score only the ten frozen V4 answers with the unchanged legacy evaluator."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import threading
import time
import urllib.error
import urllib.request

import run_answer_smoke as base

ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_selfscore_smoke_20260927')
UPSTREAM = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_answering_smoke_20260927_wait900/results')
CONFIG = dict(run_id='pkulaw_v4_selfscore_smoke_20260927', service_job_id='402816',
              base_url='http://dgx-040:18044/v1', model='deepseek_v4_flash', thinking=True,
              temperature=0.3, initial_max_tokens=3000, timeout_s=900, workers=2)
ANSWER_INPUT_HASH = '37bdeb51af6ffdd7f03ae13b0f3d2158128f7830fa1d3f3780c4821dfa5814da'
TASK_FIELDS = {'split', 'case_id', 'question_id', 'question_text', 'source_case_text_hash',
               'masked_case_text_hash', 'question_hash', 'answer_prompt_hash', 'case_text_masked',
               'judge_decision_masked', 'judge_decision_hash'}


def validate(data):
    if set(data) != set(CONFIG) | {'answer_results_dir', 'answer_input_sha256', 'tasks'}:
        raise ValueError('Scoring input fields do not match frozen schema')
    for key, expected in CONFIG.items():
        if data.get(key) != expected or type(data.get(key)) is not type(expected):
            raise ValueError('Frozen scoring parameter mismatch: ' + key)
    if data['answer_results_dir'] != str(UPSTREAM) or data['answer_input_sha256'] != ANSWER_INPUT_HASH:
        raise ValueError('Unexpected upstream answer run')
    tasks = data['tasks']
    if len(tasks) != 10:
        raise ValueError('Exactly ten scoring tasks required')
    keys, counts, cases = set(), Counter(), {'primary': set(), 'secondary': set()}
    for task in tasks:
        if set(task) != TASK_FIELDS or task['split'] not in cases:
            raise ValueError('Invalid scoring task fields/split')
        if any(not isinstance(task[k], str) or not task[k] for k in TASK_FIELDS):
            raise ValueError('Scoring fields must be nonempty strings')
        key = tuple(task[k] for k in ('split', 'case_id', 'question_id'))
        if key in keys:
            raise ValueError('Duplicate scoring task')
        keys.add(key)
        counts[task['split']] += 1
        cases[task['split']].add(task['case_id'])
        for raw, hashed in (('question_text', 'question_hash'), ('case_text_masked', 'masked_case_text_hash'),
                            ('judge_decision_masked', 'judge_decision_hash')):
            if base.digest(task[raw].encode()) != task[hashed]:
                raise ValueError('Scoring source hash mismatch: ' + raw)
        for key in ('source_case_text_hash', 'masked_case_text_hash', 'question_hash',
                    'answer_prompt_hash', 'judge_decision_hash'):
            if not re.fullmatch('[0-9a-f]{64}', task[key]):
                raise ValueError('Invalid scoring provenance hash')
    if counts != {'primary': 5, 'secondary': 5} or any(len(c) != 1 for c in cases.values()):
        raise ValueError('One case/five questions per split required')
    if cases['primary'] & cases['secondary']:
        raise ValueError('Primary/secondary cases must differ')


class Capture:
    provider = 'deepseek'

    def __init__(self, response=None, expected=None):
        self.response = response or {'answer': '', 'thinking': ''}
        self.expected, self.prompts = expected, []

    def analyze_case(self, case_text, question=None, use_thinking=True):
        if question is not None or use_thinking is not True:
            raise ValueError('Legacy evaluation did not request DeepSeek thinking')
        if self.expected is not None and case_text != self.expected:
            raise ValueError('Offline replay prompt mismatch')
        self.prompts.append(case_text)
        return self.response


def evaluate(bundle, api, task, answer):
    evaluator = bundle.AnswerEvaluator(api=api, flag_parser_version='legacy')
    result = evaluator.evaluate_answer(ai_answer=answer, judge_decision=task['judge_decision_masked'],
                                      question=task['question_text'], case_text=task['case_text_masked'])
    if len(api.prompts) != 1:
        raise ValueError('Expected exactly one legacy scoring API call')
    return evaluator, result


def complete_format(text, dimensions):
    """A validity guard only: never changes legacy parsing or calculated scores."""
    missing = []
    for dimension in dimensions:
        match = re.search(re.escape(dimension), text)
        if not match:
            missing.append(dimension)
            continue
        remainder = text[match.end():]
        boundaries = [re.search(re.escape(other), remainder) for other in dimensions if other != dimension]
        boundaries.append(re.search('【错误标记】', remainder))
        end = min((m.start() for m in boundaries if m), default=len(remainder))
        score = re.search(r'(?:得分[：:]\s*(\d+)|(\d+)\s*分)', remainder[:end])
        if not score or int(score.group(1) or score.group(2)) not in range(5):
            missing.append(dimension)
    return missing


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
            body = response.read().decode('utf-8', errors='replace').replace(self.key, '[REDACTED]')
            return response.code, headers, body


def stats(directory):
    receipts = [json.loads(p.read_text()) for p in sorted(directory.glob('attempt_*.response.json'))]
    usage = Counter()
    for receipt in receipts:
        raw = receipt.get('usage') or {}
        if not isinstance(raw, dict):
            continue
        for key, value in raw.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                usage[key] += value
        if 'reasoning_tokens' not in raw:
            details = raw.get('completion_tokens_details') or {}
            if isinstance(details, dict) and isinstance(details.get('reasoning_tokens'), (int, float)):
                usage['reasoning_tokens'] += details['reasoning_tokens']
    return receipts, dict(usage), len(list(directory.glob('attempt_*.request.json')))


class Runner(base.Runner):
    def __init__(self, input_bytes, output_dir, transport, bundle, bundle_hash,
                 sleep=time.sleep, stop=None, upstream=None, wait_timeout=10800):
        self.data = json.loads(input_bytes)
        validate(self.data)
        self.output, self.transport, self.bundle = Path(output_dir), transport, bundle
        self.sleep, self.stop = sleep, stop or threading.Event()
        self.upstream = Path(upstream) if upstream is not None else UPSTREAM
        self.wait_timeout = wait_timeout
        self.identity = {'input_sha256': base.digest(input_bytes),
                         'script_sha256': base.digest(Path(__file__).read_bytes()),
                         'helper_sha256': base.digest(Path(base.__file__).read_bytes()),
                         'bundle_sha256': bundle_hash, 'parameters': CONFIG,
                         'answer_input_sha256': ANSWER_INPUT_HASH, 'http_limit_per_task': base.MAX_HTTP}

    def wait_for_answers(self):
        deadline = time.monotonic() + self.wait_timeout
        while not self.stop.is_set():
            path = self.upstream / 'summary.json'
            if path.exists():
                summary = json.loads(path.read_text())
                counts = summary.get('counts', {})
                if summary.get('tasks') == 10 and counts.get('pending', 0) == 0 and sum(counts.values()) == 10:
                    identity = json.loads((self.upstream / 'run_identity.json').read_text())
                    if identity.get('input_sha256') != ANSWER_INPUT_HASH:
                        raise ValueError('Upstream frozen input hash mismatch')
                    answers = {}
                    for task in self.data['tasks']:
                        path = self.upstream / self.task_dir(task).name / 'terminal.json'
                        raw = path.read_bytes()
                        answer = json.loads(raw)
                        pairs = [('split', 'split'), ('case_id', 'case_id'), ('question_id', 'question_id'),
                                 ('question_text', 'question_text'), ('source_case_text_hash', 'source_case_text_hash'),
                                 ('masked_case_text_hash', 'masked_case_text_hash'), ('question_hash', 'question_hash'),
                                 ('prompt_hash', 'answer_prompt_hash')]
                        if any(answer.get(old) != task[new] for old, new in pairs):
                            raise ValueError('Upstream task provenance mismatch')
                        if answer.get('requested_model') != CONFIG['model'] or answer.get('thinking') is not False:
                            raise ValueError('Upstream answer model/condition mismatch')
                        if answer.get('temperature') != CONFIG['temperature']:
                            raise ValueError('Upstream answer temperature mismatch')
                        if answer.get('status') in (None, 'pending'):
                            raise ValueError('Upstream task is not terminal')
                        if not isinstance(answer.get('answer'), str):
                            raise ValueError('Upstream answer must be a string')
                        if answer['status'] == 'ok' and (not answer['answer'].strip() or answer.get('finish_reason') != 'stop'):
                            raise ValueError('Upstream successful answer has invalid content/finish')
                        answers[self.task_dir(task).name] = (answer, base.digest(raw))
                    return answers
            if time.monotonic() >= deadline:
                return None
            self.sleep(min(15, max(0, deadline - time.monotonic())))
        return None

    def freeze(self, answers):
        frozen = {}
        for task in self.data['tasks']:
            directory = self.task_dir(task)
            directory.mkdir(exist_ok=True)
            answer, receipt_hash = answers[directory.name]
            item = dict(task, answer=answer['answer'], answer_hash=base.digest(answer['answer'].encode()),
                        answer_status=answer['status'], answer_terminal_sha256=receipt_hash,
                        answering_model=answer['requested_model'], scoring_model=CONFIG['model'])
            if answer['status'] == 'ok':
                capture = Capture()
                evaluate(self.bundle, capture, task, answer['answer'])
                item['inner_prompt'] = capture.prompts[0]
                item['messages'] = self.bundle.analysis_messages(item['inner_prompt'], None)
                item['scoring_prompt_hash'] = base.digest(base.canonical(item['messages']))
            path = directory / 'frozen_input.json'
            if path.exists():
                if json.loads(path.read_text()) != item:
                    raise ValueError('Frozen answer/scoring messages changed; refusing resume')
            else:
                base.atomic(path, item, exclusive=True)
            frozen[directory.name] = item
        return frozen

    def task(self, task):
        directory = self.task_dir(task)
        terminal = directory / 'terminal.json'
        if terminal.exists():
            return json.loads(terminal.read_text())
        item = self.frozen[directory.name]
        result = {k: item[k] for k in ('split', 'case_id', 'question_id', 'question_text', 'answer_hash',
                    'answer_status', 'answer_terminal_sha256', 'answer_prompt_hash', 'judge_decision_hash',
                    'source_case_text_hash', 'masked_case_text_hash', 'question_hash', 'answering_model', 'scoring_model')}
        result.update(status='blocked_answer', legacy_evaluation=None, evaluation=None,
                      scoring_prompt_hash=item.get('scoring_prompt_hash'), raw_evaluation='', reasoning_content='')
        number, maximum, length_retried, last = 0, 3000, False, {}
        if item['answer_status'] == 'ok':
            status, content, finish = 'error', '', None
            try:
                for outer in range(3):
                    while True:
                        for http_try in range(3):
                            number += 1
                            payload = {'model': CONFIG['model'], 'messages': item['messages'],
                                       'temperature': 0.3, 'max_tokens': maximum,
                                       'chat_template_kwargs': {'thinking': True}}
                            last = self.request(directory, number, payload)
                            status, content, finish = base.classify(last)
                            if status != 'error' or number >= base.MAX_HTTP:
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
                    if status not in ('error', 'empty') or number >= base.MAX_HTTP:
                        break
                    if outer < 2:
                        self.sleep(2)
            except base.UncertainRequest as error:
                status, content, finish = 'interrupted', '', None
                last = {'error': str(error)}
            except base.StopDispatch:
                return None
            result.update(status=status, raw_evaluation=content, finish_reason=finish, last_error=last.get('error'))
            body = last.get('raw_response') or {}
            choices = body.get('choices') or [] if isinstance(body, dict) else []
            message = choices[0].get('message') or {} if choices and isinstance(choices[0], dict) else {}
            reasoning = message.get('reasoning_content') or message.get('reasoning') or ''
            reasoning = reasoning if isinstance(reasoning, str) else ''
            result['reasoning_content'] = reasoning
            if status == 'ok':
                try:
                    replay = Capture({'answer': content, 'thinking': reasoning}, expected=item['inner_prompt'])
                    evaluator, evaluation = evaluate(self.bundle, replay, task, item['answer'])
                    result['legacy_evaluation'] = evaluation
                    result['missing_or_invalid_dimensions'] = complete_format(content, evaluator.scoring_criteria)
                    if result['missing_or_invalid_dimensions']:
                        result['status'] = 'parse_incomplete'
                    else:
                        result['evaluation'] = evaluation
                except Exception as error:
                    result['status'], result['parse_error'] = 'parse_error', type(error).__name__
        receipts, usage, attempts = stats(directory)
        final = receipts[-1] if receipts else {}
        body = final.get('raw_response') or {}
        body = body if isinstance(body, dict) else {}
        headers = final.get('headers', {})
        result.update(usage=usage, final_response_usage=final.get('usage'), attempts=attempts,
                      recorded_responses=len(receipts), elapsed_s=round(sum(r.get('elapsed_s', 0) for r in receipts), 3),
                      requested_model=CONFIG['model'], response_model=body.get('model'), response_id=body.get('id'),
                      request_id=headers.get('x-request-id') or headers.get('request-id'),
                      max_tokens=maximum, length_retried=length_retried, temperature=0.3, thinking=True,
                      completed_at=base.now(), score_zero_imputed=False)
        base.atomic(terminal, result, exclusive=True)
        return result

    def summarize(self, started, state='running'):
        rows, counts, usage, attempts, responses = [], Counter(), Counter(), 0, 0
        for task in self.data['tasks']:
            directory = self.task_dir(task)
            path = directory / 'terminal.json'
            if path.exists():
                row = json.loads(path.read_text())
                rows.append(row)
                counts[row['status']] += 1
            else:
                counts['pending'] += 1
            receipts, task_usage, task_attempts = stats(directory)
            attempts += task_attempts
            responses += len(receipts)
            usage.update(task_usage)
        for split in ('primary', 'secondary'):
            base.atomic(self.output / ('scores_deepseek_v4_flash_' + split + '.jsonl'),
                        ''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in rows if r['split'] == split), raw=True)
        base.atomic(self.output / 'failures.jsonl', ''.join(json.dumps(r, ensure_ascii=False) + '\n'
                    for r in rows if r['status'] != 'ok'), raw=True)
        summary = dict(run_id=CONFIG['run_id'], state=state, cases=2, tasks=10, completed=len(rows),
                       success=counts['ok'], failed=len(rows) - counts['ok'], counts=dict(counts),
                       actual_attempts=attempts, recorded_responses=responses,
                       uncertain_request_intents=attempts - responses, usage_totals=dict(usage),
                       current_session_walltime_s=round(time.monotonic() - started, 3), updated_at=base.now(),
                       answering_model=CONFIG['model'], scoring_model=CONFIG['model'], self_scoring=True,
                       questions_regenerated=False, answers_regenerated=False, paid_api_used=False,
                       evaluation_parser='legacy', zero_imputation=False,
                       retry_policy='3 outer error/empty attempts; <=3 HTTP per round; one 3000->6000 increase; <=18 HTTP intents per task')
        base.atomic(self.output / 'summary.json', summary)
        return summary

    def run(self):
        self.output.mkdir(parents=True, exist_ok=True)
        with (self.output / '.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            path = self.output / 'run_identity.json'
            if path.exists():
                if json.loads(path.read_text()) != self.identity:
                    raise ValueError('Resume refused: scoring input/script/helper/bundle changed')
            else:
                if any(p.name != '.lock' for p in self.output.iterdir()):
                    raise ValueError('Nonempty scoring output without identity')
                base.atomic(path, self.identity, exclusive=True)
            started = time.monotonic()
            self.summarize(started, 'waiting_for_answers')
            answers = self.wait_for_answers()
            if answers is None:
                return self.summarize(started, 'stopped' if self.stop.is_set() else 'upstream_wait_timeout')
            self.frozen = self.freeze(answers)
            try:
                with ThreadPoolExecutor(max_workers=2) as pool:
                    futures = [pool.submit(self.task, task) for task in self.data['tasks']]
                    for future in as_completed(futures):
                        future.result()
                        self.summarize(started)
            finally:
                summary = self.summarize(started, 'stopped' if self.stop.is_set() else 'finished')
            return summary


def load_bundle(root):
    manifest = json.loads((root / 'input_manifest.json').read_text())
    path = root / 'legacy_scoring_bundle.py'
    actual = base.digest(path.read_bytes())
    if actual != manifest['bundle_sha256']:
        raise ValueError('Legacy bundle hash mismatch')
    spec = importlib.util.spec_from_file_location('legacy_scoring_bundle', path)
    bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundle)
    return bundle, actual


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    args = parser.parse_args()
    if os.environ.get('SLURM_JOB_ID') != CONFIG['service_job_id']:
        parser.error('Run only inside existing allocation 402816')
    if args.input.absolute() != ROOT / 'input_scoring.json' or args.input.resolve() != ROOT / 'input_scoring.json':
        parser.error('Unexpected scoring input path')
    if args.output_dir.absolute() != ROOT / 'results' or args.output_dir.resolve() != ROOT / 'results':
        parser.error('Unexpected scoring output path')
    if base.KEY.is_symlink() or base.KEY.stat().st_uid != os.getuid() or base.KEY.stat().st_mode & 0o077:
        parser.error('Unsafe service key file')
    key = base.KEY.read_text().strip()
    if len(key) < 32:
        parser.error('Invalid service key')
    bundle, bundle_hash = load_bundle(ROOT)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    runner = Runner(args.input.read_bytes(), args.output_dir, HTTP(key), bundle, bundle_hash,
                    sleep=lambda seconds: stop.wait(seconds), stop=stop)
    print(json.dumps(runner.run(), ensure_ascii=False))


if __name__ == '__main__':
    main()
