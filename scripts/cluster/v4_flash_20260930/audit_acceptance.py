#!/usr/bin/env python3
"""Read-only six-score acceptance audit; no credentials, SSH or model requests.

Original evidence is never modified. The exact deployed runner replays copied
receipts in a temporary directory with a transport that refuses every call.
Legal terminal failures are reported, not silently promoted to valid scores.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import tempfile

import run_three_model_scoring as score

SOURCE = Path(__file__).resolve().parents[3] / 'data/pkulaw_three_model_scoring_20260930'


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    require(path.is_file() and not path.is_symlink(), 'Missing/redirected evidence: ' + str(path))
    return json.loads(path.read_bytes())


def hashed(path):
    return score.base.digest(path.read_bytes())


def task_dir(task):
    return 'task_' + score.base.digest(score.base.canonical(list(score.key(task))))[:24]


def _audit_evidence(data, raw, source, snapshot, bundle, release_identity):
    results, dispatches = snapshot / 'results', snapshot / 'dispatches'
    tasks = data['tasks'][:6]
    expected_pairs = {(model, split) for model in score.ANSWER_MODELS for split in ('primary', 'secondary')}
    require(len(tasks) == 6 and {(task['answering_model'], task['split']) for task in tasks} == expected_pairs,
            'Acceptance order must cover each model/split exactly once')
    manifest = read(source / 'manifest.json')
    require(manifest['acceptance_first_six'] == [{field: task[field] for field in score.KEY_FIELDS} for task in tasks],
            'Manifest acceptance keys differ from frozen input')
    require({path.name for path in results.glob('task_*')} == {task_dir(task) for task in tasks},
            'Snapshot does not contain exactly the first six model-qualified tasks')
    require([path.name for path in sorted(dispatches.glob('attempt-*.intent.json'))] == ['attempt-001.intent.json'],
            'Acceptance audit requires untouched first dispatch only')
    intent = read(dispatches / 'attempt-001.intent.json')
    expected_intent = dict(dispatch_id='attempt-001', max_new_tasks=6, resume=False, job_id='402816', node='dgx-040',
                           scoring_enabled=True, answers_regenerated=False, new_gpu_allocation=False,
                           input_sha256=score.base.digest(raw))
    for field, expected in expected_intent.items():
        require(intent.get(field) == expected, 'Dispatch scope differs: ' + field)
    exit_record = read(dispatches / 'attempt-001.exit.json')
    execution = read(dispatches / 'attempt-001.execution.json')
    wrapper = read(dispatches / 'attempt-001.wrapper.json')
    require(exit_record.get('dispatch_id') == 'attempt-001' and exit_record.get('exit_code') == 0,
            'Acceptance has not produced a real clean exit receipt')
    require(exit_record['runner_identity'] == execution['runner_identity'], 'Exit belongs to another runner')
    require(execution['wrapper_identity'] == wrapper['wrapper_identity'], 'Wrapper identity differs')
    require(execution['max_new_tasks'] == wrapper['max_new_tasks'] == 6
            and execution['resume'] is wrapper['resume'] is False, 'Execution exceeded initial six-task scope')
    rows, terminals, aggregate_usage, attempts, length_retries = [], [], Counter(), 0, 0
    transport_calls = []
    def forbidden_transport(payload):
        transport_calls.append(score.base.digest(score.base.canonical(payload)))
        raise RuntimeError('Audit transport disabled; receipt replay must be complete')
    with tempfile.TemporaryDirectory(prefix='three-model-score-audit-') as temporary:
        replay_output = Path(temporary) / 'replay'
        replay_output.mkdir()
        runner = score.Runner(raw, replay_output, forbidden_transport, None, {}, bundle, score.BUNDLE_SHA256,
                              max_new_tasks=6, sleep=lambda _: None, release_identity=release_identity)
        require(read(results / 'run_identity.json') == runner.identity, 'Frozen runner/helper/input identity differs')
        for task in tasks:
            directory = results / task_dir(task)
            terminal_path = directory / 'terminal.json'
            terminal = read(terminal_path)
            require(terminal.get('status') in score.TERMINAL_STATUSES, 'Unknown terminal classification')
            inner, messages = score.messages_for(bundle, task)
            payloads = {str(limit): dict(model=score.CONFIG['model'], messages=messages, temperature=.3,
                        max_tokens=limit, chat_template_kwargs={'thinking': True}) for limit in (3000, 6000)}
            payload_hashes = {limit: score.base.digest(score.base.canonical(payload)) for limit, payload in payloads.items()}
            expected_frozen = {field: value for field, value in task.items() if field not in score.OMIT_TERMINAL}
            expected_frozen.update(messages=messages, inner_prompt_sha256=score.base.digest(inner.encode()),
                                   request_sha256_by_token_limit=payload_hashes)
            require(read(directory / 'frozen_input.json') == expected_frozen, 'Frozen score prompt/payload changed')
            requests = sorted(directory.glob('attempt_*.request.json'))
            responses = sorted(directory.glob('attempt_*.response.json'))
            require(1 <= len(requests) <= score.base.MAX_HTTP and len(requests) == len(responses),
                    'Missing/extra/unknown request receipt')
            replay_dir = runner.task_dir(task)
            replay_dir.mkdir()
            receipt_hashes = []
            for number, (request_path, response_path) in enumerate(zip(requests, responses), 1):
                require(request_path.name == 'attempt_%03d.request.json' % number
                        and response_path.name == 'attempt_%03d.response.json' % number, 'Nonconsecutive receipt numbering')
                request, response = read(request_path), read(response_path)
                limit = str(request.get('payload', {}).get('max_tokens'))
                require(request.get('attempt') == number and limit in payloads and request['payload'] == payloads[limit],
                        'Request differs from original scoring prompt/model/thinking/temperature')
                body = response.get('raw_response')
                if 'raw_body' in response:
                    try:
                        decoded = json.loads(response['raw_body'])
                    except (ValueError, TypeError):
                        require('raw_response' not in response and response.get('error') == 'invalid_json_response',
                                'Invalid raw response was not preserved/classified accurately')
                    else:
                        require(decoded == body, 'Raw and parsed response differ')
                else:
                    require(str(response.get('error', '')).startswith('transport_'), 'Receipt lacks raw body without transport error')
                status, _, finish = score.base.classify(response)
                require(response.get('classification') == status and response.get('finish_reason') == finish,
                        'Receipt classification changed')
                if isinstance(body, dict):
                    require(response.get('usage') == body.get('usage'), 'Receipt/raw usage differs')
                score.base.atomic(replay_dir / request_path.name, request, exclusive=True)
                score.base.atomic(replay_dir / response_path.name, response, exclusive=True)
                receipt_hashes.append(dict(attempt=number, request_sha256=hashed(request_path),
                                           response_sha256=hashed(response_path), payload_sha256=payload_hashes[limit],
                                           raw_body_sha256=score.base.digest(response['raw_body'].encode())
                                           if isinstance(response.get('raw_body'), str) else None,
                                           classification=status, finish_reason=finish, max_tokens=int(limit)))
            replayed = runner.task(task)
            require(not transport_calls, 'Receipt replay attempted unrecorded work')
            require(replayed is not None, 'Receipt replay stopped before terminal reconstruction')
            require({field: value for field, value in replayed.items() if field != 'completed_at'} ==
                    {field: value for field, value in terminal.items() if field != 'completed_at'},
                    'Terminal differs from exact original runner/parser/penalty/usage replay')
            require(read(replay_dir / 'frozen_input.json') == expected_frozen, 'Replay prompt changed')
            require(terminal['self_scoring'] is False and terminal['answers_regenerated'] is False
                    and terminal['score_zero_imputed'] is False, 'Answer regeneration or score imputation present')
            if terminal['status'] == 'ok':
                evaluation = terminal['evaluation']
                dimensions = evaluation['各维度得分']
                require(evaluation == terminal['legacy_evaluation'], 'Legacy score was replaced')
                require(all(isinstance(value, (int, float)) and not isinstance(value, bool)
                            and math.isfinite(value) and 0 <= value <= 4 for value in dimensions.values()),
                        'Invalid dimension score')
                require(math.isclose(evaluation['总分'], round(sum(dimensions.values()), 2))
                        and math.isclose(evaluation['百分制'], round(sum(dimensions.values()) * 5, 2)),
                        'Total or percentage does not match legacy dimensions')
            else:
                require(terminal['evaluation'] is None, 'Failed terminal contains a valid score')
                evaluation = None
            terminals.append(terminal)
            attempts += terminal['attempts']
            length_retries += terminal['length_retried']
            aggregate_usage.update(terminal['usage'])
            rows.append(dict(**{field: task[field] for field in score.KEY_FIELDS}, status=terminal['status'],
                             score_20=evaluation['总分'] if evaluation else None,
                             score_100=evaluation['百分制'] if evaluation else None,
                             dimensions=evaluation['各维度得分'] if evaluation else None,
                             error_flags=evaluation['错误详情'] if evaluation else None,
                             legacy_score_20=(terminal['legacy_evaluation'] or {}).get('总分'),
                             missing_or_invalid_dimensions=terminal['missing_or_invalid_dimensions'],
                             parse_error=terminal['parse_error'], attempts=terminal['attempts'],
                             length_retried=terminal['length_retried'], usage=terminal['usage'],
                             answer_hash=task['answer_hash'], answer_terminal_sha256=task['answer_terminal_sha256'],
                             scoring_prompt_hash=task['scoring_prompt_hash'], terminal_sha256=hashed(terminal_path),
                             frozen_input_sha256=hashed(directory / 'frozen_input.json'), receipts=receipt_hashes))
    summary = read(results / 'summary.json')
    require(read(results / 'run_state.json') == summary, 'Final run state and summary differ')
    counts = dict(Counter(row['status'] for row in terminals))
    successful = [row for row in terminals if row['status'] == 'ok']
    required = dict(run_id=score.CONFIG['run_id'], tasks=len(data['tasks']), network_tasks=len(data['tasks']),
                    completed=6, new_completed=6, success=len(successful), failed=6-len(successful),
                    needs_reconcile=counts.get('needs_reconcile', 0), pending=len(data['tasks'])-6, counts=counts,
                    inflight=0, actual_attempts=attempts, recorded_responses=attempts, unmatched_intents=0,
                    length_retries=length_retries, thinking=True, temperature=.3, answers_regenerated=False,
                    evaluation_parser='legacy', zero_imputation=False, session_max_new_tasks=6,
                    formal_scoring=True, self_scoring=False, reused_existing_scores=0,
                    tasks_by_model_split=score.model_split_counts(data['tasks']),
                    completed_by_model_split=score.model_split_counts(terminals),
                    success_by_model_split=score.model_split_counts(successful), usage_totals=dict(aggregate_usage))
    for field, expected in required.items():
        require(summary.get(field) == expected, 'Summary accounting mismatch: ' + field)
    require(summary['status'] in ('segment_completed', 'paused'), 'Unexpected acceptance session status')
    require(1 <= summary['max_inflight'] <= 6, 'Six-task acceptance concurrency bound exceeded')
    sessions = sorted((results / 'sessions').glob('session_*.json'))
    require(len(sessions) == 1 and read(sessions[0]) == summary, 'Missing/different acceptance session receipt')
    for model, split in sorted(expected_pairs):
        exported = [json.loads(line) for line in (results / ('scores_' + model + '_' + split + '.jsonl')).read_text().splitlines()]
        require(exported == [row for row in terminals if row['answering_model'] == model and row['split'] == split],
                'Model/split export differs from terminal evidence')
    failures = [json.loads(line) for line in (results / 'failures.jsonl').read_text().splitlines()]
    require({score.key(row): row for row in failures} == {score.key(row): row for row in terminals if row['status'] != 'ok'}
            and len(failures) == 6-len(successful), 'Failure export does not preserve every unsuccessful score')
    sample_paths = sorted((results / 'samples').glob('sample_*.json'))
    require(len(sample_paths) == summary['sampler_count'] and sample_paths, 'Missing monitoring evidence')
    samples = [read(path) for path in sample_paths]
    monitoring_ok = all(sample.get('healthy') is True and sample.get('gpu_returncode') == 0
                        and len(sample.get('gpus') or []) == 8 and not sample.get('gpu_anomalies')
                        and not sample.get('gpu_error') for sample in samples)
    gate = (len(successful) == 6 and summary['needs_reconcile'] == 0 and not summary['stop_reasons']
            and summary['status'] == 'segment_completed' and monitoring_ok)
    return dict(audit_passed=gate, evidence_integrity_passed=True, full_resume_gate='passed' if gate else 'blocked',
                scope='Only the first six existing-answer scoring tasks; not full scoring completion',
                source=str(source), snapshot=str(snapshot), input_scoring_sha256=score.base.digest(raw),
                release_identity=release_identity, bundle_sha256=score.BUNDLE_SHA256,
                run_identity_sha256=hashed(results / 'run_identity.json'), summary_sha256=hashed(results / 'summary.json'),
                dispatch_exit_sha256=hashed(dispatches / 'attempt-001.exit.json'), actual_exit_code=exit_record['exit_code'],
                completed=6, valid_scores=len(successful), counts=counts, needs_reconcile=summary['needs_reconcile'],
                inflight=summary['inflight'], unmatched_intents=summary['unmatched_intents'], remaining_tasks=summary['pending'],
                actual_attempts=attempts, usage_totals=dict(aggregate_usage), length_retries=length_retries,
                monitoring_samples=len(samples), monitoring_ok=monitoring_ok, stop_reasons=summary['stop_reasons'],
                pilot_walltime_s=summary['walltime_s'], self_scoring=False, answers_regenerated=False,
                score_zero_imputation=False, model_requests_by_this_audit=0, cases=rows)


def audit(source, snapshot):
    raw = (source / 'input_scoring.json').read_bytes()
    data = json.loads(raw)
    score.validate(data)
    bundle, _ = score.load_bundle(source)
    identity = score.verify_release(source, raw, bundle)
    return _audit_evidence(data, raw, source, snapshot, bundle, identity)


def self_test():
    from test_three_model_scoring import fixture, BUNDLE, BUNDLE_HASH, FakeHTTP, Sampler, reply
    for valid in (True, False):
        with tempfile.TemporaryDirectory(prefix='three-model-acceptance-selftest-') as temporary:
            root = Path(temporary)
            source, snapshot = root / 'source', root / 'snapshot'
            source.mkdir()
            dispatches = snapshot / 'dispatches'
            dispatches.mkdir(parents=True)
            data = fixture()
            raw = score.base.canonical(data)
            score.base.atomic(source / 'manifest.json', {'acceptance_first_six': [
                {field: task[field] for field in score.KEY_FIELDS} for task in data['tasks'][:6]]})
            http = FakeHTTP(always=reply() if valid else reply('【规范依据相关性】得分：4分'))
            endpoint = dict(status='ready', job_id='402816', model='deepseek_v4_flash', hostname='dgx-040', max_num_seqs=128)
            runner = score.Runner(raw, snapshot / 'results', http, Sampler(), endpoint, BUNDLE, BUNDLE_HASH,
                                  max_new_tasks=6, sleep=lambda _: None, release_identity={})
            runner.run()
            common = dict(dispatch_id='attempt-001', max_new_tasks=6, resume=False, job_id='402816', node='dgx-040')
            process = dict(common, wrapper_identity={'pid': 1, 'started': '1'}, runner_identity={'pid': 2, 'started': '2'})
            score.base.atomic(dispatches / 'attempt-001.intent.json', dict(common, scoring_enabled=True,
                              answers_regenerated=False, new_gpu_allocation=False, input_sha256=score.base.digest(raw)))
            for kind in ('wrapper', 'execution'):
                score.base.atomic(dispatches / ('attempt-001.' + kind + '.json'), process)
            score.base.atomic(dispatches / 'attempt-001.exit.json', dict(dispatch_id='attempt-001', exit_code=0,
                              runner_identity=process['runner_identity']))
            report = _audit_evidence(data, raw, source, snapshot, BUNDLE, {})
            require(report['audit_passed'] is valid and report['evidence_integrity_passed'], 'Wrong synthetic audit result')
            require(len(http.calls) == 6, 'Synthetic runner exceeded six scoring calls')
            terminal_path = next((snapshot / 'results').glob('task_*/terminal.json'))
            terminal = read(terminal_path)
            terminal['usage']['total_tokens'] += 1
            score.base.atomic(terminal_path, terminal)
            try:
                _audit_evidence(data, raw, source, snapshot, BUNDLE, {})
            except ValueError as error:
                require('Terminal differs' in str(error), 'Tamper test failed for unrelated reason')
            else:
                raise AssertionError('Tampered terminal accepted')
    return dict(offline_self_test='passed', model_requests=0, original_data_modified=False,
                cases=['successful_acceptance', 'legal_parse_failure_blocks_resume', 'terminal_usage_tamper'])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=SOURCE)
    parser.add_argument('--snapshot', type=Path)
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if not args.self_test and args.snapshot is None:
        parser.error('--snapshot is required')
    try:
        report = self_test() if args.self_test else audit(args.source.resolve(), args.snapshot.resolve())
    except Exception as error:
        print(json.dumps(dict(audit_passed=False, evidence_integrity_passed=False, full_resume_gate='blocked',
                              error_type=type(error).__name__, error=str(error), model_requests_by_this_audit=0),
                         ensure_ascii=False, indent=2))
        raise SystemExit(2)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report.get('audit_passed') is False:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
