#!/usr/bin/env python3
"""Independent nonthinking answer + unchanged thinking-judge rerun.

Original outputs are read-only evidence. This version preserves legacy coupled
retries and scoring, but explicitly sends generation thinking=False. Durable
intents, raw receipts, endpoint locks and genuine drained exits prevent replay
of unknown calls. No service/allocation administration is implemented.
"""
import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
import datetime as dt
import fcntl
import hashlib
import math
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
import urllib.error
import urllib.request

# Locally share the frozen helpers; deployment stages the same bytes beside us.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
if not (HERE / 'run_answer_smoke.py').exists():
    sys.path.insert(1, str(HERE.parent / 'v4_flash_20260927'))

import benchmark_concurrency as benchmark
import run_answer_smoke as base
import run_answer_batch128 as answering
import run_selfscore_smoke as legacy
import run_selfscore_batch128_v2 as guard_module

BASE_URLS = {'ep146': 'http://10.33.4.146:18044/v1', 'ep152': 'http://10.33.4.152:18044/v1'}
TOTAL_QUESTIONS = 11095
CONFIG = {}


def configure(run_id, endpoint_id):
    if endpoint_id not in BASE_URLS or not re.fullmatch(r'pkulaw_admin_ds_nt_rerun_20261004(?:_[A-Za-z0-9_-]+)?', run_id):
        raise ValueError('Invalid rerun/endpoint identity')
    CONFIG.clear()
    CONFIG.update(run_id=run_id, service_job_id='admin_managed', base_url=BASE_URLS[endpoint_id],
                  model='deepseek_v4_flash', thinking=False, scoring_thinking=True, temperature=.3,
                  initial_max_tokens=3000, timeout_s=1800, workers=128)
    return dict(CONFIG)


RETRY_CONTRACT = 'legacy108_coupled'
BUNDLE_SHA256 = '6af07139a06994b3192e450e5fa79f23e69851b889e999a99187efaabc81d9ad'
TASK_FIELDS = base.TASK_FIELDS
MAPPING_FIELDS = legacy.TASK_FIELDS | {'answer_messages'}
MAX_HTTP = 90  # 3 whole-question rounds x (6 generation + 4*6 scoring requests).
TERMINAL_STATUSES = answering.TERMINAL_STATUSES | {'parse_incomplete', 'parse_error', 'protocol_error'}
RETRY_POLICY = ('legacy108_coupled: <=3 whole-question rounds; score failure regenerates answer; '
                'each chat <=3 HTTP attempts per token tier, independent 3000->6000 once; '
                'scoring adapter initial chat plus <=3 additional empty-content chats. '
                'Transport uncertainty/missing response/408/504 requires reconciliation; '
                'no automatic redispatch of unknown work.')


def key(task):
    return tuple(task[k] for k in ('split', 'case_id', 'question_id'))


def validate(data):
    if set(data) - (set(CONFIG) | {'tasks', 'source_manifest_sha256'}):
        raise ValueError('Unexpected thinking input fields')
    for name, value in CONFIG.items():
        if data.get(name) != value or type(data.get(name)) is not type(value):
            raise ValueError('Frozen thinking parameter mismatch: ' + name)
    if 'source_manifest_sha256' in data and not re.fullmatch('[0-9a-f]{64}', data['source_manifest_sha256']):
        raise ValueError('Invalid source manifest hash')
    if not isinstance(data.get('tasks'), list) or not data['tasks']:
        raise ValueError('Nonempty thinking task list required')
    seen, groups, cases = set(), Counter(), {'primary': set(), 'secondary': set()}
    for task in data['tasks']:
        if set(task) != TASK_FIELDS or task['split'] not in cases:
            raise ValueError('Network task must contain only original answering fields')
        if any(not isinstance(task[name], str) or not task[name] for name in TASK_FIELDS - {'messages'}):
            raise ValueError('Invalid answering task fields')
        if key(task) in seen:
            raise ValueError('Duplicate task key')
        seen.add(key(task))
        groups[(task['split'], task['case_id'])] += 1
        cases[task['split']].add(task['case_id'])
        messages = task['messages']
        if (not isinstance(messages, list) or len(messages) != 2 or
                any(not isinstance(m, dict) or set(m) != {'role', 'content'} or m['role'] != role
                    or not isinstance(m['content'], str) for m, role in zip(messages, ('system', 'user')))):
            raise ValueError('Original system/user messages required')
        if base.digest(base.canonical(messages)) != task['prompt_hash']:
            raise ValueError('Answer prompt hash mismatch')
        if base.digest(task['question_text'].encode()) != task['question_hash']:
            raise ValueError('Question hash mismatch')
        for name in ('prompt_hash', 'question_hash', 'masked_case_text_hash', 'source_case_text_hash'):
            if not re.fullmatch('[0-9a-f]{64}', task[name]):
                raise ValueError('Invalid task hash: ' + name)
    if any(n != 5 for n in groups.values()) or cases['primary'] & cases['secondary']:
        raise ValueError('Require disjoint complete five-question cases')


def verify_mapping(tasks, task_mapping, bundle):
    """Bind judgments only to the local scorer; reconstruct every answering prompt."""
    rows = list(task_mapping.values()) if isinstance(task_mapping, dict) else list(task_mapping)
    mapping = {key(row): row for row in rows}
    if len(mapping) != len(rows) or set(mapping) != {key(t) for t in tasks}:
        raise ValueError('Mapping coverage differs from frozen network tasks')
    for task in tasks:
        row = mapping[key(task)]
        if set(row) != MAPPING_FIELDS:
            raise ValueError('Mapping schema differs from frozen source')
        for name in TASK_FIELDS - {'messages', 'prompt_hash'}:
            if row[name] != task[name]:
                raise ValueError('Network/mapping provenance mismatch: ' + name)
        for raw, hashed in (('case_text_masked', 'masked_case_text_hash'),
                            ('judge_decision_masked', 'judge_decision_hash')):
            if not isinstance(row[raw], str) or not row[raw] or base.digest(row[raw].encode()) != row[hashed]:
                raise ValueError('Mapping source hash mismatch: ' + raw)
        if (row['answer_messages'] != task['messages'] or row['answer_prompt_hash'] != task['prompt_hash']
                or bundle.analysis_messages(row['case_text_masked'], row['question_text']) != task['messages']):
            raise ValueError('Answer messages differ from the exact historical prompt')
        if row['judge_decision_masked'] in task['messages'][1]['content']:
            raise ValueError('Judgment appears in answering prompt')
    return mapping


def load_bundle(root):
    path = Path(root) / 'legacy_scoring_bundle.py'
    actual = base.digest(path.read_bytes())
    if actual != BUNDLE_SHA256:
        raise ValueError('Frozen legacy scoring bundle changed')
    spec = importlib.util.spec_from_file_location('thinking_frozen_legacy_scoring_bundle', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, actual


class HTTP:
    def __init__(self):
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self, payload):
        request = urllib.request.Request(CONFIG['base_url'] + '/chat/completions',
            data=base.canonical(payload), method='POST', headers={'Content-Type': 'application/json'})
        try:
            response = self.opener.open(request, timeout=CONFIG['timeout_s'])
        except urllib.error.HTTPError as error:
            response = error
        with response:
            headers = {k.lower(): v for k, v in response.headers.items()
                       if k.lower() in {'retry-after', 'x-request-id', 'request-id'}}
            return response.code, headers, response.read().decode('utf-8', errors='replace')


class LegacyCallFailure(Exception):
    pass


class ProtocolMismatch(Exception):
    pass


class EvidenceIntegrityError(Exception):
    pass


class ObservedResults(dict):
    """Maintain stage counters on insertion, not by scanning completed answers."""
    def __init__(self, owner):
        super().__init__()
        self.owner = owner

    def __setitem__(self, name, row):
        if name in self:
            raise EvidenceIntegrityError('A terminal result must never be replaced')
        super().__setitem__(name, row)
        self.owner.answer_counts[row.get('answer_status', 'not_completed')] += 1
        self.owner.scoring_counts[row.get('scoring_status', 'not_completed')] += 1


def call_content(record):
    body = record.get('raw_response') or {}
    choices = body.get('choices') or [] if isinstance(body, dict) else []
    choice = choices[0] if choices and isinstance(choices[0], dict) else {}
    message = choice.get('message') or {}
    message = message if isinstance(message, dict) else {}
    return dict(content=str(message.get('content') or ''),
                reasoning_content=str(message.get('reasoning_content') or message.get('reasoning') or ''),
                finish_reason=choice.get('finish_reason'), response_model=body.get('model'),
                response_id=body.get('id'), refusal=message.get('refusal'))


class Runner(answering.Runner):
    def __init__(self, input_bytes, output_dir, transport, sampler, endpoint, bundle, bundle_hash,
                 task_mapping, *, resume=False, max_new_tasks=None, stop=None, sleep=None,
                 mapping_sha256=None, release_identity=None):
        self.data = json.loads(input_bytes)
        validate(self.data)
        if bundle_hash != BUNDLE_SHA256:
            raise ValueError('Wrong loaded legacy bundle')
        if max_new_tasks is not None and (type(max_new_tasks) is not int or max_new_tasks < 1):
            raise ValueError('max-new-tasks must be positive')
        self.bundle = bundle
        self.mapping = verify_mapping(self.data['tasks'], task_mapping, bundle)
        self.output, self.sampler, self.endpoint = Path(output_dir), sampler, endpoint
        self.resume, self.max_new_tasks, self.reused_count = resume, max_new_tasks, 0
        self.stop = stop or threading.Event()
        self.sleep = sleep or self.stop.wait
        self.guard, self.monitor_done = threading.RLock(), threading.Event()
        self.local = threading.local()
        self.underlying_transport, self.transport = transport, self.tracked_transport
        self.answer_counts, self.scoring_counts = Counter(), Counter()
        self.results, self.counts, self.usage = ObservedResults(self), Counter(), Counter()
        self.inflight, self.peak, self.intents, self.responses = 0, 0, 0, 0
        self.length_retries, self.new_completed, self.samples = 0, 0, 0
        self.stop_reasons, self.metrics_missing, self.last_preempt = [], 0, None
        self.started, self.started_at = None, None
        self.tasks_by_dir = {self.task_dir(t).name: t for t in self.data['tasks']}
        self.identity = dict(input_sha256=base.digest(input_bytes),
            script_sha256=base.digest(Path(__file__).read_bytes()),
            helper_sha256=base.digest(Path(base.__file__).read_bytes()),
            queue_sha256=base.digest(Path(answering.__file__).read_bytes()),
            monitor_sha256=base.digest(Path(benchmark.__file__).read_bytes()),
            legacy_adapter_sha256=base.digest(Path(legacy.__file__).read_bytes()),
            audit_guard_sha256=base.digest(Path(guard_module.__file__).read_bytes()),
            bundle_sha256=bundle_hash, parameters=CONFIG, tasks=len(self.data['tasks']),
            task_mapping_sha256=mapping_sha256 or base.digest(base.canonical(list(self.mapping.values()))),
            retry_contract=RETRY_CONTRACT, retry_policy=RETRY_POLICY, http_limit_per_task=MAX_HTTP,
            release_identity=release_identity, schema_version='admin_nonthinking_coupled_rerun_v1')

    def request(self, directory, number, payload, context=None):
        """Persist intent before network and never guess whether an unknown call ran."""
        if number > MAX_HTTP:
            raise EvidenceIntegrityError('Legacy whole-task HTTP bound exceeded')
        req = directory / ('attempt_%03d.request.json' % number)
        res = directory / ('attempt_%03d.response.json' % number)
        if req.exists():
            try:
                saved = json.loads(req.read_text())
            except (ValueError, TypeError) as error:
                raise EvidenceIntegrityError('Malformed recorded intent') from error
            if saved['payload'] != payload or saved.get('context') != context:
                raise EvidenceIntegrityError('Recorded request differs from deterministic replay')
            if not res.exists():
                raise base.UncertainRequest('Intent exists without response; reconcile before replay')
            try:
                record = json.loads(res.read_text())
            except (ValueError, TypeError) as error:
                raise EvidenceIntegrityError('Malformed recorded response') from error
        else:
            if res.exists():
                raise EvidenceIntegrityError('Orphan response')
            if self.stop.is_set():
                raise base.StopDispatch()
            base.atomic(req, dict(started_at=base.now(), attempt=number, payload=payload, context=context), exclusive=True)
            started = time.monotonic()
            record = dict(completed_at=None)
            try:
                status, headers, body = self.transport(payload)
                record.update(http_status=status, headers=headers, raw_body=body)
                try:
                    record['raw_response'] = json.loads(body)
                except (ValueError, TypeError):
                    record['error'] = 'invalid_json_response'
            except Exception as error:
                record['error'] = 'transport_' + type(error).__name__
            record.update(completed_at=base.now(), elapsed_s=round(time.monotonic() - started, 3), context=context)
            parsed = record.get('raw_response')
            if isinstance(parsed, dict):
                record['usage'] = parsed.get('usage')
                record['request_id'] = parsed.get('id') or record.get('headers', {}).get('x-request-id')
            record['classification'], _, record['finish_reason'] = base.classify(record)
            base.atomic(res, record, exclusive=True)
            with self.guard:
                self.responses += 1
                answering.add_usage(self.usage, record.get('usage'))
                if payload['max_tokens'] == 6000 and (context or {}).get('http_attempt') == 1:
                    self.length_retries += 1
        if str(record.get('error', '')).startswith('transport_') or record.get('http_status') in (408, 504):
            raise base.UncertainRequest('Transport/gateway timeout may have executed; reconciliation required')
        return record

    def chat(self, directory, state, messages, stage, pipeline_round, adapter_call=0):
        """Exact XHubClient.chat loop, with cluster transport and durable receipts."""
        attempts, maximum = [], 3000
        for length_round in (1, 2):
            truncated = False
            payload = dict(model=CONFIG['model'], messages=messages, temperature=.3,
                           max_tokens=maximum, chat_template_kwargs={'thinking': stage == 'scoring'})
            for http_attempt in (1, 2, 3):
                state['number'] += 1
                context = dict(stage=stage, pipeline_round=pipeline_round, adapter_call=adapter_call,
                               length_round=length_round, http_attempt=http_attempt)
                record = self.request(directory, state['number'], payload, context)
                attempts.append(state['number'])
                http = record.get('http_status')
                if http == 429:
                    try:
                        delay = max(0, min(3600, int(record.get('headers', {}).get('retry-after', 60))))
                    except (TypeError, ValueError):
                        delay = 60
                    self.sleep(delay)
                    continue
                body = record.get('raw_response')
                if http != 200 or not isinstance(body, dict):
                    if http_attempt < 3:
                        self.sleep(http_attempt)
                        continue
                    break
                if body.get('model') != CONFIG['model']:
                    self.halt('response_model_mismatch')
                    raise ProtocolMismatch('response_model_mismatch')
                content = call_content(record)
                if stage == 'generation' and content['reasoning_content'].strip():
                    self.halt('nonthinking_generation_returned_reasoning')
                    raise ProtocolMismatch('nonthinking_generation_returned_reasoning')
                truncated = content['finish_reason'] in ('length', 'max_tokens')
                if truncated and length_round == 1:
                    maximum = 6000
                    break
                return dict(ok=True, stage=stage, **content, max_tokens=maximum,
                            status=base.classify(record)[0], attempt_numbers=attempts,
                            last_response_file='attempt_%03d.response.json' % state['number'], error='')
            if truncated and length_round == 1:
                continue
            break
        return dict(ok=False, stage=stage, content='', reasoning_content='', finish_reason=None,
                    response_model=None, max_tokens=maximum, status='interface_error',
                    attempt_numbers=attempts, error='interface_error_after_bounded_attempts')

    def score_adapter(self, directory, state, task, answer, pipeline_round):
        capture = legacy.Capture()
        legacy.evaluate(self.bundle, capture, task, answer)
        inner = capture.prompts[0]
        messages = self.bundle.analysis_messages(inner, None)
        calls = []
        result = self.chat(directory, state, messages, 'scoring', pipeline_round, adapter_call=0)
        calls.append(result)
        state['scoring_calls'] = calls
        if not result['ok']:
            raise LegacyCallFailure(result['error'])
        if not result['content'].strip():
            for retry in (1, 2, 3):
                result = self.chat(directory, state, messages, 'scoring', pipeline_round, adapter_call=retry)
                calls.append(result)
                if result['ok'] and result['content'].strip():
                    break
                if retry < 3:
                    self.sleep(2)
            if not result['ok']:
                raise LegacyCallFailure(result['error'])
            if not result['content'].strip():
                raise LegacyCallFailure('scoring_visible_content_empty_after_three_additional_chats')
        replay = legacy.Capture({'answer': result['content'], 'thinking': result['reasoning_content']}, expected=inner)
        evaluator, evaluation = legacy.evaluate(self.bundle, replay, task, answer)
        # A validator is observational: even a guard defect must not regenerate
        # an answer or change the legacy scoring/retry decisions.
        try:
            invalid = guard_module.complete_format(result['content'], evaluator.scoring_criteria)
            audit_error = None
        except Exception as error:
            invalid, audit_error = list(evaluator.scoring_criteria), type(error).__name__
        return result, evaluation, invalid, {
            'inner_prompt_sha256': base.digest(inner.encode()), 'messages_sha256': base.digest(base.canonical(messages)),
            'adapter_calls': calls, 'format_guard_error': audit_error}

    def zero_evaluation(self, error):
        dimensions = self.bundle.AnswerEvaluator(api=object(), flag_parser_version='legacy').scoring_criteria
        return {'总分': 0.0, '百分制': 0.0, '各维度得分': {name: 0.0 for name in dimensions},
                '详细评价': f'处理失败：{error}（已重试3次）', '错误标记': '',
                '错误详情': {'微小错误': [], '明显错误': [], '重大错误': []}, '分档': '处理失败'}

    def task(self, network_task):
        directory = self.task_dir(network_task)
        directory.mkdir(exist_ok=True)
        terminal = directory / 'terminal.json'
        if terminal.exists():
            return json.loads(terminal.read_text())
        task = self.mapping[key(network_task)]
        frozen = dict(network_task, judge_decision_hash=task['judge_decision_hash'],
                      retry_contract=RETRY_CONTRACT, bundle_sha256=BUNDLE_SHA256)
        path = directory / 'frozen_input.json'
        if path.exists():
            if json.loads(path.read_text()) != frozen:
                raise ValueError('Frozen answering/scoring provenance changed')
        else:
            base.atomic(path, frozen, exclusive=True)
        state = dict(number=0, scoring_calls=[])
        history, generation, scoring, evaluation, audit, invalid = [], None, None, None, {}, []
        error, terminal_status, legacy_success = '', 'interface_error', False
        try:
            for pipeline_round in (1, 2, 3):
                round_generation, round_scoring = None, None
                state['scoring_calls'] = []
                try:
                    round_generation = self.chat(directory, state, network_task['messages'], 'generation', pipeline_round)
                    generation = round_generation
                    if not generation['ok']:
                        raise LegacyCallFailure(generation['error'])
                    if not generation['content'].strip():
                        raise LegacyCallFailure('answer_visible_content_empty')
                    round_scoring, evaluation, invalid, audit = self.score_adapter(
                        directory, state, task, generation['content'], pipeline_round)
                    scoring = round_scoring
                    legacy_success = True
                    error = ''
                    history.append(dict(pipeline_attempt=pipeline_round, status='success', error='',
                        generation=round_generation, scoring=round_scoring, scoring_calls=state['scoring_calls']))
                    break
                except LegacyCallFailure as exc:
                    error = str(exc)
                    history.append(dict(pipeline_attempt=pipeline_round, status='failed', error=error,
                        generation=round_generation, scoring=round_scoring, scoring_calls=state['scoring_calls']))
                    if pipeline_round < 3:
                        self.sleep(2)
                except (base.UncertainRequest, ProtocolMismatch, base.StopDispatch,
                        EvidenceIntegrityError, OSError):
                    raise
                except Exception as exc:
                    # Match whole-question retry on evaluator errors without exposing case/key data.
                    error = 'evaluation_' + type(exc).__name__
                    history.append(dict(pipeline_attempt=pipeline_round, status='failed', error=error,
                        generation=round_generation, scoring=round_scoring, scoring_calls=state['scoring_calls']))
                    if pipeline_round < 3:
                        self.sleep(2)
            if not legacy_success:
                evaluation = self.zero_evaluation(error)
                terminal_status = 'empty' if 'empty' in error else 'interface_error'
        except base.UncertainRequest as exc:
            terminal_status, error = 'needs_reconcile', str(exc)
            self.halt('request_needs_reconciliation')
        except ProtocolMismatch as exc:
            terminal_status, error = 'protocol_error', str(exc)
        except base.StopDispatch:
            return None
        if legacy_success:
            # This guard changes neither the parser nor retries; retain its exact output separately.
            terminal_status = next((call['status'] for call in (generation, scoring)
                                    if call['status'] != 'ok'), 'parse_incomplete' if invalid else 'ok')
            if terminal_status == 'error':
                terminal_status = 'protocol_error'
        receipts, usage, attempts = legacy.stats(directory)
        result = {name: value for name, value in network_task.items() if name != 'messages'}
        result.update(judge_decision_hash=task['judge_decision_hash'], status=terminal_status,
            condition='DeepSeek nonthinking rerun', answering_model=CONFIG['model'], scoring_model=CONFIG['model'],
            requested_model=CONFIG['model'], temperature=.3, thinking=False, scoring_thinking=True,
            answer=(generation or {}).get('content', ''), answer_reasoning_content=(generation or {}).get('reasoning_content', ''),
            answer_hash=base.digest((generation or {}).get('content', '').encode()),
            answer_status=(generation or {}).get('status', 'not_completed'),
            answer_finish_reason=(generation or {}).get('finish_reason'),
            raw_evaluation=(scoring or {}).get('content', ''), reasoning_content=(scoring or {}).get('reasoning_content', ''),
            scoring_status=(scoring or {}).get('status', 'not_completed'),
            finish_reason=(scoring or generation or {}).get('finish_reason'),
            response_model=(scoring or generation or {}).get('response_model'),
            legacy_pipeline_success=legacy_success, legacy_evaluation=evaluation,
            evaluation=evaluation if terminal_status == 'ok' else None,
            valid_for_complete_case_analysis=terminal_status == 'ok', missing_or_invalid_dimensions=invalid,
            score_zero_imputed=False, legacy_failure_zero_imputed=not legacy_success and evaluation is not None,
            pipeline_attempt=len(history), pipeline_attempt_history=history, generation=generation, scoring=scoring,
            incomplete_round_scoring_calls=state['scoring_calls'] if not legacy_success else [], scoring_audit=audit,
            processing_error=error, evaluation_parser='legacy', format_guard_version='audit_only_bracket_headings_v2',
            retry_contract=RETRY_CONTRACT, request_intents=attempts, attempts=attempts,
            recorded_responses=len(receipts), usage=usage,
            elapsed_s=round(sum(r.get('elapsed_s', 0) for r in receipts), 3), completed_at=base.now())
        base.atomic(terminal, result, exclusive=True)
        return result

    def load_existing(self):
        for directory in self.output.glob('task_*'):
            if directory.name not in self.tasks_by_dir:
                raise ValueError('Unexpected thinking task directory')
            task = self.tasks_by_dir[directory.name]
            requests = list(directory.glob('attempt_*.request.json'))
            if len(requests) > MAX_HTTP:
                raise ValueError('Recorded task exceeds frozen HTTP limit')
            self.intents += len(requests)
            for path in requests:
                request = json.loads(path.read_text())
                self.length_retries += (request['payload']['max_tokens'] == 6000 and
                                        request.get('context', {}).get('http_attempt') == 1)
            for path in directory.glob('attempt_*.response.json'):
                if not path.with_name(path.name.replace('.response.json', '.request.json')).exists():
                    raise ValueError('Orphan persisted response')
                self.responses += 1
                answering.add_usage(self.usage, json.loads(path.read_text()).get('usage'))
            terminal = directory / 'terminal.json'
            if not terminal.exists():
                continue
            result = json.loads(terminal.read_text())
            if (any(result.get(k) != v for k, v in task.items() if k != 'messages')
                    or result.get('status') not in TERMINAL_STATUSES
                    or result.get('thinking') is not False or result.get('scoring_thinking') is not True
                    or result.get('temperature') != .3 or result.get('retry_contract') != RETRY_CONTRACT
                    or result.get('judge_decision_hash') != self.mapping[key(task)]['judge_decision_hash']):
                raise ValueError('Existing thinking terminal provenance invalid')
            if result['status'] == 'ok':
                if (not result['answer'].strip() or result['answer_finish_reason'] != 'stop'
                        or result['finish_reason'] != 'stop' or result['response_model'] != CONFIG['model']):
                    raise ValueError('Existing successful result is incomplete')
                if (result.get('answer_hash') != base.digest(result['answer'].encode())
                        or result.get('answer_reasoning_content', '').strip()):
                    raise ValueError('Existing nonthinking answer identity/mode invalid')
                evaluator, expected = legacy.evaluate(self.bundle,
                    legacy.Capture({'answer': result['raw_evaluation'], 'thinking': result['reasoning_content']}),
                    self.mapping[key(task)], result['answer'])
                if (expected != result['evaluation'] or expected != result['legacy_evaluation']
                        or guard_module.complete_format(result['raw_evaluation'], evaluator.scoring_criteria)):
                    raise ValueError('Existing score differs from unchanged legacy replay')
            elif result.get('evaluation') is not None:
                raise ValueError('Invalid terminal must not claim a valid score')
            if result['status'] == 'needs_reconcile':
                self.halt('existing_request_needs_reconciliation')
            self.results[directory.name] = result
            self.counts[result['status']] += 1

    def checkpoint(self, status):
        with self.guard:
            if self.stop.is_set() and not self.stop_reasons:
                self.stop_reasons.append('external_stop_requested')
            completed = len(self.results)
            record = dict(run_id=CONFIG['run_id'], status=status, updated_at=base.now(), started_at=self.started_at,
                tasks=len(self.data['tasks']), network_tasks=len(self.data['tasks']), reused_existing_answers=0,
                completed=completed, new_completed=self.new_completed, success=self.counts['ok'],
                failed=completed-self.counts['ok'], needs_reconcile=self.counts['needs_reconcile'],
                pending=len(self.data['tasks'])-completed, counts=dict(self.counts), inflight=self.inflight,
                max_inflight=self.peak, workers=128, actual_attempts=self.intents, recorded_responses=self.responses,
                unmatched_intents=self.intents-self.responses, length_retries=self.length_retries,
                usage_totals=dict(self.usage), walltime_s=round(time.monotonic()-self.started, 3),
                stop_reasons=list(self.stop_reasons), sampler_count=self.samples, retry_contract=RETRY_CONTRACT,
                retry_policy=RETRY_POLICY, paid_api_used=False, formal_answers=True, formal_scoring=True,
                self_scoring=True, thinking=False, scoring_thinking=True, temperature=.3,
                answer_counts=dict(self.answer_counts), scoring_counts=dict(self.scoring_counts),
                successful_answers=self.answer_counts['ok'], valid_scores=self.counts['ok'],
                session_max_new_tasks=self.max_new_tasks)
            base.atomic(self.output/'run_state.json', record)
            base.atomic(self.output/'summary.json', record)
            return record

    def export(self):
        for split in ('primary', 'secondary'):
            rows = (self.results[self.task_dir(task).name] for task in self.data['tasks']
                    if task['split'] == split and self.task_dir(task).name in self.results)
            base.atomic(self.output/('nonthinking_rerun_pipeline_'+split+'.jsonl'),
                ''.join(json.dumps(row, ensure_ascii=False)+'\n' for row in rows), raw=True)
        base.atomic(self.output/'failures.jsonl', ''.join(json.dumps(row, ensure_ascii=False)+'\n'
                    for row in self.results.values() if row['status'] != 'ok'), raw=True)

    def sample(self):
        """Read-only admin HTTP telemetry; GPU observations are not invented."""
        try:
            record = self.sampler()
        except Exception as error:
            record = dict(at=base.now(), healthy=False, probe_error=type(error).__name__)
        with self.guard:
            base.atomic(self.output/'samples'/('sample_%06d.json' % self.samples), record, exclusive=True)
            self.samples += 1
        if record.get('healthy') is not True:
            self.halt('admin_service_health_failure')
        metrics = record.get('metrics') or {}
        fields = ('running', 'waiting', 'kv_cache_usage', 'preemptions_total')
        valid = all(isinstance(metrics.get(k), (int, float)) and math.isfinite(metrics[k]) for k in fields)
        self.metrics_missing = 0 if valid else self.metrics_missing + 1
        if self.metrics_missing >= 3:
            self.halt('critical_metrics_missing_three_samples')
        if valid:
            if metrics['kv_cache_usage'] >= .85:
                self.halt('kv_cache_at_or_above_85_percent')
            if self.last_preempt is not None and metrics['preemptions_total'] > self.last_preempt:
                self.halt('new_server_preemption')
            self.last_preempt = metrics['preemptions_total']
        return record

    def run(self):
        """One genuine process owner, immutable start/exit and explicit resume."""
        self.output.mkdir(parents=True, exist_ok=True)
        if self.output.resolve() != self.output.absolute():
            raise ValueError('Canonical output path required')
        with (self.output/'.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            identity_path = self.output/'run_identity.json'
            sessions = self.output.parent/'sessions'
            if identity_path.exists():
                if not self.resume:
                    raise ValueError('Existing rerun requires explicit --resume')
                if json.loads(identity_path.read_bytes()) != self.identity:
                    raise ValueError('Frozen rerun identity mismatch')
                verify_prior_sessions(sessions)
                # Sent-but-nonterminal work cannot become a fresh task on resume.
                for directory in self.output.glob('task_*'):
                    if not (directory/'terminal.json').exists():
                        raise ValueError('Incomplete prior task requires reconciliation; never redispatch')
                self.load_existing()
                if self.counts['needs_reconcile']:
                    raise ValueError('Prior unknown request requires reconciliation')
            else:
                if self.resume or any(p.name != '.lock' for p in self.output.iterdir()):
                    raise ValueError('Unexpected existing files or missing previous run')
                base.atomic(identity_path, self.identity, exclusive=True)
            sessions.mkdir(exist_ok=True)
            (self.output/'samples').mkdir(exist_ok=True)
            self.samples = len(list((self.output/'samples').glob('sample_*.json')))
            session = sessions/getattr(self, 'session_id', dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
            session.mkdir()
            self.started, self.started_at = time.monotonic(), base.now()
            base.atomic(session/'start.json', dict(at=base.now(), identity=process_identity(os.getpid()),
                run_id=CONFIG['run_id'], base_url=CONFIG['base_url'], resume=self.resume,
                input_sha256=self.identity['input_sha256']), exclusive=True)
            pending_tasks = [task for task in self.data['tasks'] if self.task_dir(task).name not in self.results]
            if self.max_new_tasks is not None:
                pending_tasks = pending_tasks[:self.max_new_tasks]
            monitor, final, failure, futures_finished, drained = None, None, None, False, False
            try:
                sample = self.sample()
                if not server_idle(sample):
                    self.halt('admin_endpoint_not_healthy_idle_at_start')
                self.checkpoint('starting')
                monitor = threading.Thread(target=self.monitor, daemon=True)
                monitor.start()
                iterator = iter(pending_tasks)
                with ThreadPoolExecutor(max_workers=128) as pool:
                    pending = set()
                    exhausted = False
                    while pending or (not exhausted and not self.stop.is_set()):
                        while not self.stop.is_set() and len(pending) < 128:
                            task = next(iterator, None)
                            if task is None:
                                exhausted = True
                                break
                            pending.add(pool.submit(self.task, task))
                        if not pending:
                            break
                        done, pending = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                        for future in done:
                            try:
                                result = future.result()
                                if result is not None:
                                    with self.guard:
                                        directory = self.task_dir(result).name
                                        if directory in self.results:
                                            raise EvidenceIntegrityError('Duplicate terminal insertion')
                                        self.results[directory] = result
                                        self.counts[result['status']] += 1
                                        self.new_completed += 1
                                        if (self.counts['interface_error'] + self.counts['needs_reconcile']) / max(1, len(self.results)) > .10:
                                            self.halt('interface_failure_rate_above_10_percent')
                            except Exception as error:
                                self.halt('runner_exception_' + type(error).__name__)
                                failure = failure or error
                futures_finished = True
            except BaseException as error:
                failure = failure or error
                self.halt('client_exception_' + type(error).__name__)
            finally:
                self.monitor_done.set()
                if monitor:
                    monitor.join(timeout=20)
                # The result is actual idle telemetry, not inferred from an empty client queue.
                try:
                    final_sample = self.sample()
                    drained = server_idle(final_sample) and self.inflight == 0
                except Exception as error:
                    failure = failure or error
                try:
                    self.export()
                    status = ('failed' if failure else 'paused' if self.stop.is_set() else
                              'segment_completed' if len(self.results) < len(self.data['tasks']) else
                              'completed_with_failures' if self.counts['ok'] != len(self.data['tasks']) else 'completed')
                    final = self.checkpoint(status)
                    base.atomic(session/'exit.json', dict(at=base.now(), identity=process_identity(os.getpid()),
                        status=status, local_futures_finished=futures_finished, drained=drained,
                        monitor_finished=monitor is None or not monitor.is_alive(),
                        completed=len(self.results), corpus_complete=len(self.results) == len(self.data['tasks']),
                        inflight=self.inflight, unmatched_intents=self.intents-self.responses,
                        needs_reconcile=self.counts['needs_reconcile'],
                        error_type=type(failure).__name__ if failure else None,
                        summary=final), exclusive=True)
                except Exception:
                    # Missing real exit evidence deliberately blocks automatic resume.
                    raise
            if failure:
                raise failure
            return final


def process_identity(pid):
    path = Path('/proc')/str(pid)/'stat'
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None
    rest = raw[raw.rfind(')')+2:].split()
    return dict(pid=pid, start_ticks=rest[19], state=rest[0])


def verify_prior_sessions(sessions):
    if not sessions.is_dir():
        raise ValueError('Missing genuine prior session')
    directories = sorted(sessions.iterdir())
    if not directories:
        raise ValueError('No genuine prior session')
    for directory in directories:
        start = json.loads((directory/'start.json').read_bytes())
        identity = start['identity']
        if not identity or type(identity.get('pid')) is not int or not identity.get('start_ticks'):
            raise ValueError('Invalid true process identity')
        actual = process_identity(identity['pid'])
        if actual and actual['start_ticks'] == identity['start_ticks'] and actual['state'] != 'Z':
            raise ValueError('Prior client is still alive')
        receipt = json.loads((directory/'exit.json').read_bytes())
        if (receipt.get('local_futures_finished') is not True or receipt.get('drained') is not True
                or receipt.get('monitor_finished') is not True or receipt.get('inflight') != 0
                or receipt.get('unmatched_intents') != 0 or receipt.get('needs_reconcile') != 0):
            raise ValueError('Prior client lacks genuinely drained, reconciled exit')
        if any(receipt['identity'][k] != identity[k] for k in ('pid', 'start_ticks')):
            raise ValueError('Exit and start identities differ')


class Sampler:
    def __init__(self, base_url):
        if base_url not in BASE_URLS.values():
            raise ValueError('Unapproved endpoint')
        self.server = base_url[:-3]
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def __call__(self):
        value = dict(at=base.now(), base_url=self.server+'/v1', healthy=False,
                     gpu_observation='not_available_via_admin_http', metrics={})
        for name, path in (('health', '/health'), ('metrics', '/metrics')):
            try:
                with self.opener.open(self.server+path, timeout=5) as response:
                    body = response.read().decode('utf-8', errors='replace')
                    value[name+'_http_status'] = response.code
                    if name == 'health':
                        value['healthy'] = response.code == 200
                    else:
                        value['metrics'] = benchmark.parse_metrics(body)
                        value['metrics_raw'] = body
            except Exception as error:
                value[name+'_error'] = type(error).__name__
        return value


def server_idle(sample):
    metrics = sample.get('metrics') or {}
    return (sample.get('healthy') is True and metrics.get('running') == 0
            and metrics.get('waiting') == 0)


def code_paths():
    return {'runner.py': Path(__file__).resolve(), 'run_answer_smoke.py': Path(base.__file__),
            'run_answer_batch128.py': Path(answering.__file__),
            'benchmark_concurrency.py': Path(benchmark.__file__),
            'run_selfscore_smoke.py': Path(legacy.__file__),
            'run_selfscore_batch128_v2.py': Path(guard_module.__file__)}


def safe_file(root, relative):
    if not isinstance(relative, str) or Path(relative).is_absolute() or '..' in Path(relative).parts:
        raise ValueError('Frozen relative path required')
    path = root/relative
    if path.resolve() != path or not path.is_file():
        raise ValueError('Missing frozen regular file or symlink')
    return path


def file_sha(path):
    hasher = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024*1024), b''):
            hasher.update(chunk)
    return hasher.hexdigest()


def verify_manifest(root, endpoint_id):
    root = Path(root).absolute()
    if root.resolve() != root:
        raise ValueError('Canonical root required')
    manifest_path = root/'prepared'/'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    if (manifest.get('total_questions') != TOTAL_QUESTIONS or
            manifest.get('case_counts') != {'primary': 215, 'secondary': 2004}
            or set(manifest.get('shards', {})) != set(BASE_URLS)
            or manifest.get('bundle_sha256') != BUNDLE_SHA256):
        raise ValueError('Full frozen 2219-case rerun identity mismatch')
    for name, path in code_paths().items():
        if manifest.get('code_sha256', {}).get(name) != file_sha(path):
            raise ValueError('Frozen code mismatch: '+name)
    exclusion_info = manifest['excluded_cases']
    exclusion_path = safe_file(root, exclusion_info['path'])
    if file_sha(exclusion_path) != exclusion_info['sha256']:
        raise ValueError('Permanent exclusion hash mismatch')
    excluded_rows = json.loads(exclusion_path.read_bytes())
    excluded = {row['case_id'] for row in excluded_rows if row.get('permanently_excluded') is True}
    if len(excluded) != 18:
        raise ValueError('Exactly 18 permanently excluded cases required')
    all_keys, cases, owners, selected = set(), {'primary': set(), 'secondary': set()}, {}, None
    for ep in BASE_URLS:
        shard = manifest['shards'][ep]
        if shard['base_url'] != BASE_URLS[ep]:
            raise ValueError('Endpoint identity mismatch')
        input_path, mapping_path = safe_file(root, shard['input_path']), safe_file(root, shard['mapping_path'])
        if file_sha(input_path) != shard['input_sha256'] or file_sha(mapping_path) != shard['mapping_sha256']:
            raise ValueError('Frozen shard hashes differ')
        raw = input_path.read_bytes()
        data = json.loads(raw)
        configure(manifest['run_id'], ep)
        validate(data)
        if len(data['tasks']) != shard['count']:
            raise ValueError('Shard question count mismatch')
        for task in data['tasks']:
            if key(task) in all_keys or task['case_id'] in excluded:
                raise ValueError('Duplicated or permanently excluded question')
            case_key = task['split'], task['case_id']
            if case_key in owners and owners[case_key] != ep:
                raise ValueError('A case appears on both endpoint shards')
            owners[case_key] = ep
            all_keys.add(key(task))
            cases[task['split']].add(task['case_id'])
        if ep == endpoint_id:
            selected = (raw, mapping_path.read_bytes())
    if len(all_keys) != TOTAL_QUESTIONS or {k: len(v) for k,v in cases.items()} != manifest['case_counts']:
        raise ValueError('Full question/case coverage mismatch')
    if cases['primary'] & cases['secondary']:
        raise ValueError('Split leakage')
    configure(manifest['run_id'], endpoint_id)
    bundle, bundle_hash = load_bundle(root)
    input_bytes, mapping_bytes = selected
    mapping = [json.loads(line) for line in mapping_bytes.splitlines() if line.strip()]
    verify_mapping(json.loads(input_bytes)['tasks'], mapping, bundle)
    identity = dict(manifest_sha256=file_sha(manifest_path), endpoint_id=endpoint_id,
                    complete_rerun_no_reuse=True, code_sha256=manifest['code_sha256'])
    return input_bytes, mapping_bytes, mapping, bundle, bundle_hash, identity


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--endpoint-id', choices=tuple(BASE_URLS), required=True)
    parser.add_argument('--session', required=True)
    parser.add_argument('--resume', action='store_true')
    args = parser.parse_args()
    if not re.fullmatch(r'[A-Za-z0-9_-]{8,80}', args.session):
        parser.error('Unsafe session ID')
    args.root = args.root.absolute()
    endpoint_root = args.root/args.endpoint_id
    if endpoint_root.resolve() != endpoint_root or not endpoint_root.is_dir():
        parser.error('Canonical endpoint root prepared by manager required')
    # The launch manager releases this only after persisting its genuine PID receipt.
    with (endpoint_root/'dispatch.lock').open('a') as launch_lock:
        fcntl.flock(launch_lock, fcntl.LOCK_EX)
    input_bytes, mapping_bytes, mapping, bundle, bundle_hash, identity = verify_manifest(args.root, args.endpoint_id)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    runner = Runner(input_bytes, args.root/args.endpoint_id/'results', HTTP(), Sampler(CONFIG['base_url']),
                    {'base_url': CONFIG['base_url']}, bundle, bundle_hash, mapping,
                    resume=args.resume, stop=stop, mapping_sha256=base.digest(mapping_bytes), release_identity=identity)
    runner.session_id = args.session
    print(json.dumps(runner.run(), ensure_ascii=False))


if __name__ == '__main__':
    main()
