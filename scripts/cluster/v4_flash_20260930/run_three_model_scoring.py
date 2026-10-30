#!/usr/bin/env python3
"""Score frozen external-model answers with the unchanged legacy DS evaluator.

No answers are regenerated. Model-qualified task keys isolate all evidence and
resume state; uncertain requests require reconciliation, never blind retries.
"""
import argparse
from collections import Counter
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'v4_flash_20260927'))
import benchmark_concurrency as benchmark
import run_answer_smoke as base
import run_answer_batch128 as answering
import run_selfscore_smoke as legacy
import run_selfscore_batch128_v2 as guard_module

ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_three_model_scoring_20260930')
ENDPOINT = base.KEY.parent / 'endpoint.json'
CONFIG = dict(run_id='pkulaw_three_model_ds_scoring_20260930', service_job_id='402816',
              base_url='http://dgx-040:18044/v1', model='deepseek_v4_flash', thinking=True,
              temperature=.3, initial_max_tokens=3000, timeout_s=1800, workers=128)
ANSWER_MODELS = ('gpt-4o', 'gemini-2.5-flash', 'qwen-max')
KEY_FIELDS = ('answering_model', 'split', 'case_id', 'question_id')
TASK_FIELDS = guard_module.TASK_FIELDS
OMIT_TERMINAL = guard_module.OMIT_TERMINAL
TERMINAL_STATUSES = guard_module.TERMINAL_STATUSES
BUNDLE_SHA256 = guard_module.BUNDLE_SHA256
complete_format = guard_module.complete_format
messages_for = guard_module.messages_for
RELEASE_FILES = ('input_scoring.json', 'legacy_scoring_bundle.py', 'excluded_cases.json',
                 'answer_provenance.jsonl', 'excluded_answers.jsonl', 'source_release_audit.json')


def key(task):
    return tuple(task[name] for name in KEY_FIELDS)


def model_split_counts(tasks):
    counts = Counter((task['answering_model'], task['split']) for task in tasks)
    return {model: {split: counts[model, split] for split in ('primary', 'secondary')}
            for model in ANSWER_MODELS}


def validate(data):
    if set(data) != set(CONFIG) | {'bundle_sha256', 'upstream_answers_sha256', 'tasks'}:
        raise ValueError('Three-model scoring input fields mismatch')
    for name, value in CONFIG.items():
        if data.get(name) != value or type(data.get(name)) is not type(value):
            raise ValueError('Frozen scoring parameter mismatch: ' + name)
    if data['bundle_sha256'] != BUNDLE_SHA256 or not re.fullmatch('[0-9a-f]{64}', data['upstream_answers_sha256']):
        raise ValueError('Frozen bundle/upstream answer hash mismatch')
    if not isinstance(data['tasks'], list) or not data['tasks']:
        raise ValueError('No scoring tasks')
    keys, groups, cases = set(), Counter(), {'primary': set(), 'secondary': set()}
    sources = {}
    for task in data['tasks']:
        if set(task) != TASK_FIELDS or task['split'] not in cases:
            raise ValueError('Scoring task fields/split mismatch')
        if any(not isinstance(value, str) or not value for value in task.values()):
            raise ValueError('Scoring fields must be nonempty strings')
        if task['answering_model'] not in ANSWER_MODELS:
            raise ValueError('Unexpected answering model')
        if key(task) in keys:
            raise ValueError('Duplicate model-qualified scoring key')
        keys.add(key(task))
        groups[(task['answering_model'], task['split'], task['case_id'])] += 1
        cases[task['split']].add(task['case_id'])
        if task['answer_status'] != 'ok' or task['scoring_model'] != CONFIG['model'] or not task['answer'].strip():
            raise ValueError('Only verified normal existing answers may be scored')
        for raw, hashed in (('question_text', 'question_hash'), ('case_text_masked', 'masked_case_text_hash'),
                            ('judge_decision_masked', 'judge_decision_hash'), ('answer', 'answer_hash')):
            if base.digest(task[raw].encode()) != task[hashed]:
                raise ValueError('Scoring source string hash mismatch: ' + raw)
        for name, value in task.items():
            if name.endswith('_hash') or name.endswith('_sha256'):
                if not re.fullmatch('[0-9a-f]{64}', value):
                    raise ValueError('Invalid scoring provenance hash: ' + name)
        source_key = (task['split'], task['case_id'], task['question_id'])
        source = {name: task[name] for name in legacy.TASK_FIELDS}
        if source_key in sources and sources[source_key] != source:
            raise ValueError('Models do not share identical frozen question/case/judgment provenance')
        sources[source_key] = source
    if any(not 1 <= count <= 5 for count in groups.values()) or cases['primary'] & cases['secondary']:
        raise ValueError('Require distinct splits and at most five accepted answers per model/case')


def load_bundle(root):
    path = Path(root) / 'legacy_scoring_bundle.py'
    actual = base.digest(path.read_bytes())
    if actual != BUNDLE_SHA256:
        raise ValueError('Legacy scoring bundle bytes changed')
    spec = importlib.util.spec_from_file_location('three_model_frozen_legacy_scoring_bundle', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, actual


class Runner(guard_module.Runner):
    """Keep the established queue, locks, transport receipts and safety monitoring."""
    def __init__(self, input_bytes, output_dir, transport, sampler, endpoint, bundle, bundle_hash, *,
                 resume=False, max_new_tasks=None, stop=None, sleep=None, release_identity=None):
        self.data = json.loads(input_bytes)
        validate(self.data)
        if bundle_hash != self.data['bundle_sha256']:
            raise ValueError('Loaded bundle does not match frozen scoring input')
        if max_new_tasks is not None and (type(max_new_tasks) is not int or max_new_tasks < 1):
            raise ValueError('max-new-tasks must be positive')
        self.bundle, self.output, self.sampler, self.endpoint = bundle, Path(output_dir), sampler, endpoint
        self.resume, self.max_new_tasks, self.reused_count = resume, max_new_tasks, 0
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
                             schema_version='three_model_scoring_v1', evaluation_parser='legacy', format_guard_version='bracket_headings_v2',
                             release_identity=release_identity, task_key_fields=list(KEY_FIELDS),
                             guard_sha256=base.digest(Path(guard_module.__file__).read_bytes()))

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
                      self_scoring=False, answers_regenerated=False, evaluation_parser='legacy', score_zero_imputed=False)
        base.atomic(terminal, result, exclusive=True)
        return result

    def task_dir(self, task):
        return self.output / ('task_' + base.digest(base.canonical(list(key(task))))[:24])

    def load_existing(self):
        super().load_existing()
        for result in self.results.values():
            if result.get('self_scoring') is not False or result.get('answers_regenerated') is not False:
                raise ValueError('Existing result is not immutable-answer external-model scoring')
            if result['status'] == 'needs_reconcile':
                self.halt('existing_request_needs_reconciliation')

    def checkpoint(self, status):
        with self.guard:
            if self.stop.is_set() and not self.stop_reasons:
                self.stop_reasons.append('external_stop_requested')
            completed = len(self.results)
            accepted = [row for row in self.results.values() if row['status'] == 'ok']
            record = dict(run_id=CONFIG['run_id'], status=status, updated_at=base.now(), started_at=self.started_at,
                          tasks=len(self.data['tasks']), network_tasks=len(self.data['tasks']), reused_existing_scores=0,
                          completed=completed, new_completed=self.new_completed, success=self.counts['ok'],
                          failed=completed-self.counts['ok'], needs_reconcile=self.counts['needs_reconcile'],
                          pending=len(self.data['tasks'])-completed, counts=dict(self.counts), inflight=self.inflight,
                          max_inflight=self.peak, workers=128, actual_attempts=self.intents,
                          recorded_responses=self.responses, unmatched_intents=self.intents-self.responses,
                          length_retries=self.length_retries, usage_totals=dict(self.usage),
                          walltime_s=round(time.monotonic()-self.started, 3), stop_reasons=list(self.stop_reasons),
                          sampler_count=self.samples, retry_policy=answering.RETRY_POLICY, paid_api_used=False,
                          formal_scoring=True, self_scoring=False, answering_models=list(ANSWER_MODELS),
                          scoring_model=CONFIG['model'], thinking=True, temperature=.3, answers_regenerated=False,
                          evaluation_parser='legacy', zero_imputation=False, session_max_new_tasks=self.max_new_tasks,
                          tasks_by_model_split=model_split_counts(self.data['tasks']),
                          completed_by_model_split=model_split_counts(self.results.values()),
                          success_by_model_split=model_split_counts(accepted))
            base.atomic(self.output / 'run_state.json', record)
            base.atomic(self.output / 'summary.json', record)
            return record

    def export(self):
        for model in ANSWER_MODELS:
            for split in ('primary', 'secondary'):
                rows = (self.results[self.task_dir(task).name] for task in self.data['tasks']
                        if task['answering_model'] == model and task['split'] == split
                        and self.task_dir(task).name in self.results)
                base.atomic(self.output / ('scores_' + model + '_' + split + '.jsonl'),
                            ''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows), raw=True)
        base.atomic(self.output / 'failures.jsonl',
                    ''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in self.results.values()
                            if row['status'] != 'ok'), raw=True)


def verify_release(root, input_bytes, bundle):
    """Verify model-qualified coverage and frozen local provenance without network calls."""
    root = Path(root)
    data = json.loads(input_bytes)
    validate(data)
    manifest_bytes = (root / 'manifest.json').read_bytes()
    manifest = json.loads(manifest_bytes)
    if (manifest.get('run_id') != CONFIG['run_id'] or manifest.get('network_tasks') != 33047
            or manifest.get('excluded_answer_count') != 238 or manifest.get('source_answer_count') != 33285
            or manifest.get('bundle_sha256') != BUNDLE_SHA256
            or manifest.get('upstream_answers_sha256') != data['upstream_answers_sha256']):
        raise ValueError('Unexpected three-model release identity/coverage')
    files = manifest['files']
    if not set(RELEASE_FILES) <= set(files):
        raise ValueError('Missing release provenance files')
    for name, item in files.items():
        if Path(name).name != name:
            raise ValueError('Unsafe manifest file name')
        path = root / name
        if path.is_symlink():
            raise ValueError('Redirected release file')
        raw = path.read_bytes()
        if base.digest(raw) != item['sha256'] or len(raw) != item['bytes']:
            raise ValueError('Frozen release file hash/size mismatch: ' + name)
    if base.digest(input_bytes) != files['input_scoring.json']['sha256']:
        raise ValueError('Input bytes differ from frozen release')
    if files['answer_provenance.jsonl']['sha256'] != data['upstream_answers_sha256']:
        raise ValueError('Upstream provenance hash mismatch')
    def rows(name):
        raw = (root / name).read_bytes()
        if not raw.endswith(b'\n'):
            raise ValueError('JSONL must end with a newline: ' + name)
        return [json.loads(line) for line in raw.splitlines() if line.strip()]
    tasks = data['tasks']
    provenance, excluded = rows('answer_provenance.jsonl'), rows('excluded_answers.jsonl')
    task_map = {key(task): task for task in tasks}
    provenance_map = {key(row): row for row in provenance}
    excluded_keys = {key(row) for row in excluded}
    if (len(tasks) != 33047 or len(provenance) != 33047 or len(provenance_map) != len(provenance)
            or set(task_map) != set(provenance_map)):
        raise ValueError('Release scoring/provenance/exclusion counts differ')
    if len(excluded) != 238 or len(excluded_keys) != 238 or set(task_map) & excluded_keys:
        raise ValueError('Excluded answers overlap scoring tasks or contain duplicate keys')
    for task_key, task in task_map.items():
        row = provenance_map[task_key]
        for field in ('answer_hash', 'answer_terminal_sha256', 'scoring_prompt_hash'):
            if row.get(field) != task[field]:
                raise ValueError('Answer provenance differs: ' + field)
        messages_for(bundle, task)
    counts = model_split_counts(tasks)
    if manifest.get('tasks_by_model_split') != counts:
        raise ValueError('Per-model/split network counts mismatch')
    all_keys = set(task_map) | excluded_keys
    if len(all_keys) != 33285 or any(k[0] not in ANSWER_MODELS or k[1] not in ('primary', 'secondary') for k in all_keys):
        raise ValueError('Invalid complete source coverage')
    source_by_model = {model: {(split, cid, qid) for m, split, cid, qid in all_keys if m == model}
                       for model in ANSWER_MODELS}
    first_source = source_by_model[ANSWER_MODELS[0]]
    if any(source != first_source for source in source_by_model.values()):
        raise ValueError('Source model coverage is not identical')
    if Counter(split for split, _, _ in first_source) != {'primary': 1075, 'secondary': 10020}:
        raise ValueError('Unexpected full source split counts')
    groups = Counter((split, cid) for split, cid, _ in first_source)
    if len(groups) != 2219 or set(groups.values()) != {5}:
        raise ValueError('Source must cover all 2219 complete five-question cases')
    excluded_cases = json.loads((root / 'excluded_cases.json').read_bytes())
    excluded_ids = {row['case_id'] for row in excluded_cases}
    if len(excluded_cases) != 18 or len(excluded_ids) != 18 or excluded_ids & {cid for _, cid, _ in first_source}:
        raise ValueError('Permanent case exclusions differ or re-entered scoring')
    return dict(manifest_sha256=base.digest(manifest_bytes),
                upstream_answers_sha256=data['upstream_answers_sha256'],
                network_tasks=len(tasks), excluded_answer_count=len(excluded), source_answer_count=len(all_keys),
                tasks_by_model_split=counts, task_key_fields=list(KEY_FIELDS))


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
        parser.error('Unexpected frozen three-model scoring input path')
    if args.output_dir.absolute() != ROOT / 'results' or args.output_dir.resolve() != ROOT / 'results':
        parser.error('Unexpected three-model scoring output path')
    bundle, bundle_hash = load_bundle(ROOT)
    input_bytes = args.input.read_bytes()
    release_identity = verify_release(ROOT, input_bytes, bundle)
    if base.KEY.is_symlink() or base.KEY.stat().st_uid != os.getuid() or base.KEY.stat().st_mode & 0o077:
        parser.error('Unsafe service key file')
    service_key = base.KEY.read_text().strip()
    if len(service_key) < 32:
        parser.error('Invalid service key')
    endpoint = json.loads(ENDPOINT.read_text())
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    runner = Runner(input_bytes, args.output_dir, benchmark.HTTP(service_key), benchmark.Sampler(service_key),
                    endpoint, bundle, bundle_hash, resume=args.resume, max_new_tasks=args.max_new_tasks,
                    stop=stop, release_identity=release_identity)
    print(json.dumps(runner.run(), ensure_ascii=False))


if __name__ == '__main__':
    main()
