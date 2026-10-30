#!/usr/bin/env python3
"""V2 scoring queue: fix only completeness guard; retain original legacy science."""
import argparse
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import threading
import time

import benchmark_concurrency as benchmark
import run_answer_smoke as base
import run_answer_batch128 as answering
import run_selfscore_smoke as legacy

ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_selfscore_batch128_v2_20260928')
ENDPOINT = base.KEY.parent / 'endpoint.json'
CONFIG = dict(run_id='pkulaw_v4_selfscore_batch128_v2_20260928', service_job_id='402816',
              base_url='http://dgx-040:18044/v1', model='deepseek_v4_flash', thinking=True,
              temperature=.3, initial_max_tokens=3000, timeout_s=1800, workers=128)
TASK_FIELDS = legacy.TASK_FIELDS | {'answer', 'answer_hash', 'answer_status', 'answer_terminal_sha256',
                                   'answering_model', 'scoring_model', 'scoring_prompt_hash'}
OMIT_TERMINAL = {'case_text_masked', 'judge_decision_masked', 'answer'}
TERMINAL_STATUSES = answering.TERMINAL_STATUSES | {'parse_incomplete', 'parse_error'}
BUNDLE_SHA256 = '6af07139a06994b3192e450e5fa79f23e69851b889e999a99187efaabc81d9ad'


def complete_format(text, dimensions):
    """Validity only: find unique real dimension headings, never prose mentions."""
    names = list(dimensions)
    pattern = r'(?m)^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*)?【('
    pattern += '|'.join(re.escape(name) for name in names + ['错误标记'])
    pattern += r')】(?:\*\*)?'
    headings = list(re.finditer(pattern, text))
    missing = []
    for name in names:
        positions = [i for i, match in enumerate(headings) if match.group(1) == name]
        if len(positions) != 1 or text.count('【' + name + '】') != 1:
            missing.append(name)
            continue
        index = positions[0]
        start = headings[index].end()
        end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
        # The score must immediately follow its heading, not a later explanation.
        # Use the exact rubric's plain 得分 form: the legacy parser otherwise
        # may skip bold/bare numbers and capture another dimension's score.
        score = re.match(
            r'\s*(?:\*\*)?得分[：:]\s*([+-]?\d+(?:\.\d+)?)(?:[ \t]*分)?'
            r'(?=\s|[*，。；;]|$)', text[start:end])
        if not score or re.fullmatch(r'[0-4]', score.group(1)) is None:
            missing.append(name)
    return missing



def validate(data):
    if set(data) != set(CONFIG) | {'bundle_sha256', 'upstream_answers_sha256', 'tasks'}:
        raise ValueError('V18 scoring input fields mismatch')
    for key, value in CONFIG.items():
        if data.get(key) != value or type(data.get(key)) is not type(value):
            raise ValueError('Frozen scoring parameter mismatch: ' + key)
    if data['bundle_sha256'] != BUNDLE_SHA256 or not re.fullmatch('[0-9a-f]{64}', data['upstream_answers_sha256']):
        raise ValueError('Frozen legacy bundle/upstream answer hash mismatch')
    if not isinstance(data['tasks'], list) or not data['tasks']:
        raise ValueError('No scoring tasks')
    keys, cases, groups = set(), {'primary': set(), 'secondary': set()}, Counter()
    for task in data['tasks']:
        if set(task) != TASK_FIELDS or task['split'] not in cases:
            raise ValueError('V18 scoring task fields/split mismatch')
        if any(not isinstance(value, str) or not value for value in task.values()):
            raise ValueError('Scoring fields must be nonempty strings')
        key = tuple(task[k] for k in ('split', 'case_id', 'question_id'))
        if key in keys:
            raise ValueError('Duplicate scoring primary key')
        keys.add(key)
        cases[task['split']].add(task['case_id'])
        groups[(task['split'], task['case_id'])] += 1
        if (task['answer_status'] != 'ok' or task['answering_model'] != CONFIG['model']
                or task['scoring_model'] != CONFIG['model'] or not task['answer'].strip()):
            raise ValueError('Only verified complete V4 answers may enter self-scoring')
        for raw, hashed in (('question_text', 'question_hash'), ('case_text_masked', 'masked_case_text_hash'),
                            ('judge_decision_masked', 'judge_decision_hash'), ('answer', 'answer_hash')):
            if base.digest(task[raw].encode()) != task[hashed]:
                raise ValueError('Scoring source string hash mismatch: ' + raw)
        for key, value in task.items():
            if key.endswith('_hash') or key.endswith('_sha256'):
                if not re.fullmatch('[0-9a-f]{64}', value):
                    raise ValueError('Invalid scoring provenance hash: ' + key)
    if (any(count not in (4, 5) for count in groups.values())
            or sum(count == 4 for count in groups.values()) > 2 or cases['primary'] & cases['secondary']):
        raise ValueError('Scoring input needs five-question cases, except up to two four-question reused cases')


def messages_for(bundle, task):
    capture = legacy.Capture()
    legacy.evaluate(bundle, capture, task, task['answer'])
    inner = capture.prompts[0]
    messages = bundle.analysis_messages(inner, None)
    if base.digest(base.canonical(messages)) != task['scoring_prompt_hash']:
        raise ValueError('Scoring prompt differs from the pre-frozen legacy prompt')
    return inner, messages


def load_bundle(root):
    path = root / 'legacy_scoring_bundle.py'
    actual = base.digest(path.read_bytes())
    manifest = json.loads((root / 'input_manifest.json').read_bytes())
    if actual != BUNDLE_SHA256 or actual != manifest['bundle_sha256']:
        raise ValueError('Legacy bundle bytes changed')
    spec = importlib.util.spec_from_file_location('frozen_legacy_scoring_bundle', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, actual


class Runner(answering.Runner):
    """Reuse the established lock, queue, HTTP receipts, monitoring and pause logic."""
    def __init__(self, input_bytes, output_dir, transport, sampler, endpoint, bundle, bundle_hash, *,
                 resume=False, max_new_tasks=None, stop=None, sleep=None, reuse_identity=None):
        self.data = json.loads(input_bytes)
        validate(self.data)
        if bundle_hash != self.data['bundle_sha256']:
            raise ValueError('Loaded bundle does not match frozen scoring input')
        if max_new_tasks is not None and (type(max_new_tasks) is not int or max_new_tasks < 1):
            raise ValueError('max-new-tasks must be positive')
        self.bundle, self.output, self.sampler, self.endpoint = bundle, Path(output_dir), sampler, endpoint
        self.resume, self.max_new_tasks, self.reused_count = resume, max_new_tasks, 2
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
        self.tasks_by_dir = {self.task_dir(task).name: task for task in self.data['tasks']}
        self.identity = dict(input_sha256=base.digest(input_bytes), script_sha256=base.digest(Path(__file__).read_bytes()),
                             helper_sha256=base.digest(Path(base.__file__).read_bytes()),
                             queue_sha256=base.digest(Path(answering.__file__).read_bytes()),
                             legacy_adapter_sha256=base.digest(Path(legacy.__file__).read_bytes()),
                             monitor_sha256=base.digest(Path(benchmark.__file__).read_bytes()),
                             bundle_sha256=bundle_hash, upstream_answers_sha256=self.data['upstream_answers_sha256'],
                             parameters=CONFIG, tasks=len(self.data['tasks']), retry_policy=answering.RETRY_POLICY,
                             schema_version='V18', evaluation_parser='legacy', format_guard_version='bracket_headings_v2',
                             reused_evidence=reuse_identity)

    def task(self, task):
        directory = self.task_dir(task)
        directory.mkdir(exist_ok=True)
        terminal = directory / 'terminal.json'
        if terminal.exists():
            return json.loads(terminal.read_text())
        inner, messages = messages_for(self.bundle, task)
        frozen = {k: v for k, v in task.items() if k not in OMIT_TERMINAL}
        hashes = {str(limit): base.digest(base.canonical(dict(model=CONFIG['model'], messages=messages,
                    temperature=.3, max_tokens=limit, chat_template_kwargs={'thinking': True}))) for limit in (3000, 6000)}
        frozen.update(messages=messages, inner_prompt_sha256=base.digest(inner.encode()),
                      request_sha256_by_token_limit=hashes)
        path = directory / 'frozen_input.json'
        if path.exists():
            if json.loads(path.read_text()) != frozen:
                raise ValueError('Frozen score request or source answer changed')
        else:
            base.atomic(path, frozen, exclusive=True)
        number, maximum, retried, last = 0, 3000, False, {}
        status, content, finish = 'interface_error', '', None
        try:
            for outer in range(3):
                while True:
                    for http_try in range(3):
                        number += 1
                        payload = dict(model=CONFIG['model'], messages=messages, temperature=.3,
                                       max_tokens=maximum, chat_template_kwargs={'thinking': True})
                        last = self.request(directory, number, payload)
                        status, content, finish = base.classify(last)
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
            status, content, finish = 'needs_reconcile', '', None
            last = {'error': str(error)}
            self.halt('request_needs_reconciliation')
        except base.StopDispatch:
            return None
        if status == 'error':
            status = 'interface_error'
        receipts, usage, attempts = legacy.stats(directory)
        final = receipts[-1] if receipts else {}
        body = final.get('raw_response') or {}
        body = body if isinstance(body, dict) else {}
        choices = body.get('choices') or []
        message = choices[0].get('message') or {} if choices and isinstance(choices[0], dict) else {}
        message = message if isinstance(message, dict) else {}
        reasoning = message.get('reasoning_content') or message.get('reasoning') or ''
        reasoning = reasoning if isinstance(reasoning, str) else ''
        result = {k: v for k, v in task.items() if k not in OMIT_TERMINAL}
        result.update(status=status, raw_evaluation=content, reasoning_content=reasoning,
                      legacy_evaluation=None, evaluation=None, missing_or_invalid_dimensions=[], parse_error=None)
        if status == 'ok':
            try:
                replay = legacy.Capture({'answer': content, 'thinking': reasoning}, expected=inner)
                evaluator, evaluation = legacy.evaluate(self.bundle, replay, task, task['answer'])
                result['legacy_evaluation'] = evaluation
                result['missing_or_invalid_dimensions'] = complete_format(content, evaluator.scoring_criteria)
                if result['missing_or_invalid_dimensions']:
                    result['status'] = 'parse_incomplete'
                else:
                    result['evaluation'] = evaluation
            except Exception as error:
                result['status'], result['parse_error'] = 'parse_error', type(error).__name__
        headers = final.get('headers', {})
        result.update(requested_model=CONFIG['model'], response_model=body.get('model'),
                      response_id=body.get('id'), request_id=headers.get('x-request-id') or headers.get('request-id'),
                      finish_reason=finish, max_tokens=maximum, length_retried=retried,
                      request_intents=attempts, attempts=attempts, recorded_responses=len(receipts),
                      temperature=.3, thinking=True, usage=usage, final_response_usage=final.get('usage'),
                      elapsed_s=round(sum(r.get('elapsed_s', 0) for r in receipts), 3),
                      last_error=last.get('error'), completed_at=base.now(), request_sha256_by_token_limit=hashes,
                      self_scoring=True, answers_regenerated=False, evaluation_parser='legacy', score_zero_imputed=False)
        base.atomic(terminal, result, exclusive=True)
        return result

    def load_existing(self):
        for directory in self.output.glob('task_*'):
            if directory.name not in self.tasks_by_dir:
                raise ValueError('Unexpected self-scoring task directory')
            task = self.tasks_by_dir[directory.name]
            requests = list(directory.glob('attempt_*.request.json'))
            self.intents += len(requests)
            self.length_retries += any(json.loads(p.read_text())['payload']['max_tokens'] == 6000 for p in requests)
            for path in directory.glob('attempt_*.response.json'):
                self.responses += 1
                answering.add_usage(self.usage, json.loads(path.read_text()).get('usage'))
            terminal = directory / 'terminal.json'
            if not terminal.exists():
                continue
            result = json.loads(terminal.read_text())
            if (any(result.get(k) != v for k, v in task.items() if k not in OMIT_TERMINAL)
                    or result.get('status') not in TERMINAL_STATUSES
                    or result.get('requested_model') != CONFIG['model']
                    or result.get('temperature') != .3 or result.get('thinking') is not True
                    or result.get('evaluation_parser') != 'legacy' or result.get('score_zero_imputed') is not False):
                raise ValueError('Existing scoring terminal provenance/parameters invalid')
            if result['status'] == 'ok':
                inner, _ = messages_for(self.bundle, task)
                replay = legacy.Capture({'answer': result['raw_evaluation'], 'thinking': result['reasoning_content']}, expected=inner)
                evaluator, expected = legacy.evaluate(self.bundle, replay, task, task['answer'])
                if (result.get('finish_reason') != 'stop' or result.get('response_model') != CONFIG['model']
                        or not result['raw_evaluation'].strip()
                        or complete_format(result['raw_evaluation'], evaluator.scoring_criteria)
                        or result.get('evaluation') != expected or result.get('legacy_evaluation') != expected):
                    raise ValueError('Existing score is not a complete original-legacy evaluation')
            elif result.get('evaluation') is not None:
                raise ValueError('Failed scoring terminal must not contain a valid evaluation')
            self.results[directory.name] = result
            self.counts[result['status']] += 1

    def checkpoint(self, status):
        with self.guard:
            if self.stop.is_set() and not self.stop_reasons:
                self.stop_reasons.append('external_stop_requested')
            completed = len(self.results)
            record = dict(run_id=CONFIG['run_id'], status=status, updated_at=base.now(), started_at=self.started_at,
                          tasks=len(self.data['tasks']), network_tasks=len(self.data['tasks']),
                          reused_existing_scores=2, reused_scores_imported=False,
                          combined_expected_scores=len(self.data['tasks']) + 2,
                          combined_completed=completed + 2, combined_success=self.counts['ok'] + 2,
                          completed=completed, new_completed=self.new_completed, success=self.counts['ok'],
                          failed=completed - self.counts['ok'], needs_reconcile=self.counts['needs_reconcile'],
                          pending=len(self.data['tasks']) - completed, counts=dict(self.counts), inflight=self.inflight,
                          max_inflight=self.peak, workers=128, actual_attempts=self.intents,
                          recorded_responses=self.responses, unmatched_intents=self.intents - self.responses,
                          length_retries=self.length_retries, usage_totals=dict(self.usage),
                          walltime_s=round(time.monotonic() - self.started, 3), stop_reasons=list(self.stop_reasons),
                          sampler_count=self.samples, retry_policy=answering.RETRY_POLICY, paid_api_used=False,
                          formal_scoring=True, self_scoring=True, answering_model=CONFIG['model'], scoring_model=CONFIG['model'],
                          thinking=True, temperature=.3, answers_regenerated=False, evaluation_parser='legacy',
                          zero_imputation=False, session_max_new_tasks=self.max_new_tasks)
            base.atomic(self.output / 'run_state.json', record)
            base.atomic(self.output / 'summary.json', record)
            return record

    def export(self):
        for split in ('primary', 'secondary'):
            rows = (self.results[self.task_dir(task).name] for task in self.data['tasks']
                    if task['split'] == split and self.task_dir(task).name in self.results)
            base.atomic(self.output / ('scores_deepseek_v4_flash_' + split + '.jsonl'),
                        ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows), raw=True)
        base.atomic(self.output / 'failures.jsonl', ''.join(json.dumps(row, ensure_ascii=False) + '\n'
                    for row in self.results.values() if row['status'] != 'ok'), raw=True)


def verify_release(root, input_bytes, bundle):
    """Bind the two offline-reconciled scores and exact production coverage."""
    data = json.loads(input_bytes)
    validate(data)
    manifest = json.loads((root / 'input_manifest.json').read_bytes())
    files = manifest['files']
    if base.digest(input_bytes) != files['input_scoring.json']['sha256']:
        raise ValueError('Production scoring input differs from manifest')
    raw_tasks = (root / 'reused_tasks.json').read_bytes()
    raw_scores = (root / 'reused_scores.jsonl').read_bytes()
    for name, raw in (('reused_tasks.json', raw_tasks), ('reused_scores.jsonl', raw_scores)):
        if base.digest(raw) != files[name]['sha256']:
            raise ValueError('Reused score evidence hash mismatch: ' + name)
    reused_tasks = json.loads(raw_tasks)
    reused_scores = [json.loads(line) for line in raw_scores.splitlines() if line.strip()]
    if len(reused_tasks) != 2 or len(reused_scores) != 2:
        raise ValueError('Exactly two original pilot scores must be reused')
    key = lambda task: tuple(task[k] for k in ('split', 'case_id', 'question_id'))
    network_counts = Counter(task['split'] for task in data['tasks'])
    if len(data['tasks']) != 11093 or network_counts != {'primary': 1074, 'secondary': 10019}:
        raise ValueError('Unexpected exact production network scoring counts')
    combined = data['tasks'] + reused_tasks
    validate(dict(data, tasks=combined))
    groups = Counter((task['split'], task['case_id']) for task in combined)
    if (len(groups) != 2219 or any(value != 5 for value in groups.values())
            or Counter(task['split'] for task in reused_tasks) != {'primary': 1, 'secondary': 1}):
        raise ValueError('Combined network/reused scores must cover 2219 complete cases')
    if set(manifest.get('excluded_case_ids', [])) & {task['case_id'] for task in combined}:
        raise ValueError('Excluded case entered scoring release')
    scores = {key(row): row for row in reused_scores}
    if len(scores) != 2 or set(scores) != {key(task) for task in reused_tasks}:
        raise ValueError('Reused score primary keys differ from original tasks')
    for task in reused_tasks:
        result = scores[key(task)]
        if (any(result.get(k) != v for k, v in task.items() if k not in OMIT_TERMINAL)
                or result.get('status') != 'ok' or result.get('finish_reason') != 'stop'
                or result.get('response_model') != CONFIG['model']
                or result.get('requested_model') != CONFIG['model']
                or result.get('temperature') != .3 or result.get('thinking') is not True
                or result.get('reused') is not True or result.get('score_zero_imputed') is not False):
            raise ValueError('Reused score provenance or complete response invalid')
        inner, _ = messages_for(bundle, task)
        replay = legacy.Capture({'answer': result['raw_evaluation'], 'thinking': result['reasoning_content']}, expected=inner)
        evaluator, expected = legacy.evaluate(bundle, replay, task, task['answer'])
        if (complete_format(result['raw_evaluation'], evaluator.scoring_criteria)
                or result.get('evaluation') != expected or result.get('legacy_evaluation') != expected):
            raise ValueError('Reused score differs from complete original-legacy replay')
    return dict(reused_tasks_sha256=base.digest(raw_tasks), reused_scores_sha256=base.digest(raw_scores),
                input_manifest_sha256=base.digest((root / 'input_manifest.json').read_bytes()), count=2)



def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True, type=Path)
    parser.add_argument('--output-dir', required=True, type=Path)
    parser.add_argument('--max-new-tasks', type=int)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if os.environ.get('SLURM_JOB_ID') != '402816':
        parser.error('Use only the existing allocation 402816')
    if args.input.absolute() != ROOT / 'input_scoring.json' or args.input.resolve() != ROOT / 'input_scoring.json':
        parser.error('Unexpected frozen self-scoring input path')
    if args.output_dir.absolute() != ROOT / 'results' or args.output_dir.resolve() != ROOT / 'results':
        parser.error('Unexpected self-scoring output path')
    if base.KEY.is_symlink() or base.KEY.stat().st_uid != os.getuid() or base.KEY.stat().st_mode & 0o077:
        parser.error('Unsafe service key file')
    key = base.KEY.read_text().strip()
    if len(key) < 32:
        parser.error('Invalid service key')
    bundle, bundle_hash = load_bundle(ROOT)
    input_bytes = args.input.read_bytes()
    reuse_identity = verify_release(ROOT, input_bytes, bundle)
    endpoint = json.loads(ENDPOINT.read_text())
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    runner = Runner(input_bytes, args.output_dir, benchmark.HTTP(key), benchmark.Sampler(key), endpoint,
                    bundle, bundle_hash, resume=args.resume, max_new_tasks=args.max_new_tasks, stop=stop,
                    reuse_identity=reuse_identity)
    print(json.dumps(runner.run(), ensure_ascii=False))


if __name__ == '__main__':
    main()
