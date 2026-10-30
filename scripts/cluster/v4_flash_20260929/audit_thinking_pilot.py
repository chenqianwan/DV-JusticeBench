#!/usr/bin/env python3
"""Read-only audit of a fetched ten-question thinking pilot; never calls APIs."""
import argparse
from collections import Counter, defaultdict
import contextlib
import datetime as dt
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sys

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / 'v4_flash_20260927'
SOURCE = HERE.parents[2] / 'data/pkulaw_v4_thinking_pipeline_20260929'
sys.path.insert(0, str(OLD))
import run_answer_smoke as base
import run_selfscore_smoke as legacy
import run_selfscore_batch128_v2 as guard

MAPPING_SHA = 'ac70b5739bd2fa471441ff8c970709c15643df16030b831ed8a19d9136e969e2'
BUNDLE_SHA = '6af07139a06994b3192e450e5fa79f23e69851b889e999a99187efaabc81d9ad'
INPUT_SHA = '046f6f374a2d6f64dca1df6ee7044a3c532e6a4b95e01474ee47d797a8a6f5e9'
MODEL = 'deepseek_v4_flash'


def digest(data):
    return hashlib.sha256(data).hexdigest()


def canonical(data):
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def require(test, message):
    if not test:
        raise ValueError(message)


def key(row):
    return tuple(row[name] for name in ('split', 'case_id', 'question_id'))


def usage_add(total, usage):
    if not isinstance(usage, dict):
        return
    for name, value in usage.items():
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total[name] += value
    details = usage.get('completion_tokens_details') or {}
    if 'reasoning_tokens' not in usage and isinstance(details, dict):
        value = details.get('reasoning_tokens')
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            total['reasoning_tokens'] += value


def content_of(record):
    body = record.get('raw_response')
    if not isinstance(body, dict):
        return None
    choices = body.get('choices')
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        return None
    message = choices[0].get('message') or {}
    if not isinstance(message, dict):
        return None
    return {'content': str(message.get('content') or ''),
            'reasoning_content': str(message.get('reasoning_content') or message.get('reasoning') or ''),
            'finish_reason': choices[0].get('finish_reason'),
            'response_model': body.get('model'), 'response_id': body.get('id'),
            'refusal': message.get('refusal')}


def score_messages(bundle, task, answer):
    capture = legacy.Capture()
    with contextlib.redirect_stdout(io.StringIO()):
        legacy.evaluate(bundle, capture, task, answer)
    return capture.prompts[0], bundle.analysis_messages(capture.prompts[0], None)


class Audit:
    def __init__(self, snapshot):
        self.snapshot = snapshot.resolve()
        self.hashes, self.errors, self.rows, self.requests, self.terminals = {}, [], [], [], {}
        self.stage_usage = {'generation': Counter(), 'scoring': Counter()}
        self.stage_elapsed = Counter()

    def read(self, path):
        path = Path(path)
        require(path.resolve() == path and path.is_file(), 'Missing or redirected evidence: ' + str(path))
        raw = path.read_bytes()
        label = str(path.relative_to(self.snapshot)) if self.snapshot in path.parents else str(path)
        self.hashes[label] = {'sha256': digest(raw), 'bytes': len(raw)}
        return json.loads(raw)

    def source(self):
        raw = (SOURCE / 'input_tasks.json').read_bytes()
        mapping = (SOURCE / 'task_mapping.jsonl').read_bytes()
        require(digest(raw) == INPUT_SHA, 'Frozen input hash changed')
        require(digest(mapping) == MAPPING_SHA, 'Frozen mapping hash changed')
        manifest = self.read(SOURCE / 'input_manifest.json')
        for name, item in manifest['files'].items():
            content = (SOURCE / name).read_bytes()
            require((len(content), digest(content)) == (item['bytes'], item['sha256']), 'Frozen file mismatch: ' + name)
        self.data = json.loads(raw)
        self.tasks = self.data['tasks'][:10]
        require(len(self.data['tasks']) == 11095, 'Expected 11095 total tasks')
        require(Counter(t['split'] for t in self.tasks) == {'primary': 5, 'secondary': 5}, 'Pilot split mismatch')
        require(len({key(t)[:2] for t in self.tasks}) == 2, 'Expected two complete five-question cases')
        self.mapping = {key(row): row for row in map(json.loads, mapping.splitlines())}
        path = SOURCE / 'legacy_scoring_bundle.py'
        require(digest(path.read_bytes()) == BUNDLE_SHA, 'Legacy scoring bundle hash changed')
        spec = importlib.util.spec_from_file_location('pilot_audit_frozen_legacy_bundle', path)
        self.bundle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.bundle)
        return manifest

    def task(self, task):
        name = 'task_' + digest(canonical(list(key(task))))[:24]
        directory = self.snapshot / 'results' / name
        source = self.mapping[key(task)]
        require(self.bundle.analysis_messages(source['case_text_masked'], source['question_text']) == task['messages'], 'Answer prompt reconstruction mismatch')
        require(source['judge_decision_masked'] not in task['messages'][1]['content'], 'Judgment leaked into answer prompt')
        frozen = self.read(directory / 'frozen_input.json')
        require(frozen == dict(task, judge_decision_hash=source['judge_decision_hash'],
                              retry_contract='legacy108_coupled', bundle_sha256=BUNDLE_SHA), 'Frozen task mismatch')
        terminal = self.read(directory / 'terminal.json')
        for field, value in task.items():
            if field != 'messages':
                require(terminal.get(field) == value, 'Terminal provenance mismatch: ' + field)
        require(terminal.get('judge_decision_hash') == source['judge_decision_hash'], 'Terminal judgment hash mismatch')
        require(terminal.get('thinking') is True and terminal.get('scoring_thinking') is True and terminal.get('temperature') == .3, 'Terminal mode mismatch')
        require(terminal.get('answering_model') == terminal.get('scoring_model') == MODEL, 'Terminal model mismatch')
        require(terminal.get('retry_contract') == 'legacy108_coupled', 'Terminal contract mismatch')
        paths = sorted(directory.glob('attempt_*.request.json'))
        responses = sorted(directory.glob('attempt_*.response.json'))
        require(1 <= len(paths) <= 90, 'Missing raw receipts or request bound exceeded')
        require([p.name.replace('.request.', '.response.') for p in paths] == [p.name for p in responses], 'Unpaired request/response')
        require([p.name for p in paths] == ['attempt_%03d.request.json' % n for n in range(1, len(paths) + 1)], 'Noncontiguous request numbering')
        records, calls, generations, task_usage = {}, defaultdict(list), {}, Counter()
        elapsed, last_round = 0.0, 0
        for number, request_path in enumerate(paths, 1):
            req = self.read(request_path)
            res = self.read(responses[number - 1])
            records[number] = (req, res)
            context, payload = req['context'], req['payload']
            require(req['attempt'] == number and res['context'] == context, 'Receipt context mismatch')
            require(set(payload) == {'model', 'messages', 'temperature', 'max_tokens', 'chat_template_kwargs'}, 'Unexpected payload field')
            require(payload['model'] == MODEL and payload['temperature'] == .3 and
                    payload['chat_template_kwargs'] == {'thinking': True}, 'Payload mode/model mismatch')
            require(type(payload['max_tokens']) is int and payload['max_tokens'] in (3000, 6000), 'Token limit changed')
            stage, round_no, adapter = context['stage'], context['pipeline_round'], context['adapter_call']
            require(stage in ('generation', 'scoring') and round_no in (1, 2, 3) and round_no >= last_round, 'Invalid stage/round')
            last_round = round_no
            require(adapter in (0, 1, 2, 3) and (stage != 'generation' or adapter == 0), 'Invalid adapter call')
            require(context['http_attempt'] in (1, 2, 3) and context['length_round'] in (1, 2), 'Invalid attempt tier')
            require(payload['max_tokens'] == (3000 if context['length_round'] == 1 else 6000), 'Token tier mismatch')
            call_key = (stage, round_no, adapter)
            previous = calls[call_key]
            if not previous:
                require(context['http_attempt'] == context['length_round'] == 1, 'Chat did not begin at initial tier')
            else:
                prior_req, prior_res = records[previous[-1]]
                prior_context = prior_req['context']
                if context['length_round'] > prior_context['length_round']:
                    require(context['http_attempt'] == 1 and base.classify(prior_res)[0] == 'truncated', 'Invalid 6000-token retry')
                else:
                    require(context['length_round'] == prior_context['length_round'] and
                            context['http_attempt'] == prior_context['http_attempt'] + 1, 'Invalid HTTP retry sequence')
            previous.append(number)
            if stage == 'generation':
                require(payload['messages'] == task['messages'], 'Answer request differs from frozen messages')
                if res.get('http_status') == 200 and content_of(res) is not None:
                    generations[round_no] = content_of(res)['content']
            else:
                require(round_no in generations, 'Scoring has no generation in same round')
                inner, expected = score_messages(self.bundle, source, generations[round_no])
                require(payload['messages'] == expected, 'Scoring messages differ from legacy prompt + answer content')
            if 'raw_response' in res and isinstance(res.get('raw_body'), str):
                require(json.loads(res['raw_body']) == res['raw_response'], 'Raw body differs from parsed response')
            classification, _, finish = base.classify(res)
            require(res.get('classification') == classification and res.get('finish_reason') == finish, 'Response classification mismatch')
            if isinstance(res.get('raw_response'), dict):
                require(res.get('usage') == res['raw_response'].get('usage'), 'Usage differs from raw response')
            usage_add(task_usage, res.get('usage'))
            usage_add(self.stage_usage[stage], res.get('usage'))
            elapsed += res.get('elapsed_s', 0)
            self.stage_elapsed[stage] += res.get('elapsed_s', 0)
            self.requests.append({'split': task['split'], 'question_id': task['question_id'], 'attempt': number,
                                  'stage': stage, 'http_status': res.get('http_status'), 'classification': classification,
                                  'max_tokens': payload['max_tokens'], 'messages_sha256': digest(canonical(payload['messages'])),
                                  'usage': res.get('usage'), 'elapsed_s': res.get('elapsed_s')})
        require(terminal['attempts'] == terminal['request_intents'] == terminal['recorded_responses'] == len(paths), 'Terminal attempt counts differ')
        require(terminal['usage'] == dict(task_usage), 'Terminal usage mismatch')
        require(abs(terminal['elapsed_s'] - round(elapsed, 3)) < .002, 'Terminal duration mismatch')

        def verify_call(call, stage):
            if not call:
                return
            numbers = call.get('attempt_numbers') or []
            require(all(n in records and records[n][0]['context']['stage'] == stage for n in numbers), 'Call references wrong receipts')
            if call.get('ok'):
                require(bool(numbers), 'Successful call lacks receipt')
                req, res = records[numbers[-1]]
                require(call['last_response_file'] == 'attempt_%03d.response.json' % numbers[-1], 'Selected response reference mismatch')
                expected = content_of(res)
                require(expected is not None, 'Selected response is invalid')
                for field, value in expected.items():
                    require(call.get(field) == value, 'Selected call field mismatch: ' + field)
                require(call['status'] == base.classify(res)[0] and call['max_tokens'] == req['payload']['max_tokens'], 'Call classification/token mismatch')

        for history in terminal.get('pipeline_attempt_history', []):
            verify_call(history.get('generation'), 'generation')
            verify_call(history.get('scoring'), 'scoring')
            for call in history.get('scoring_calls', []):
                verify_call(call, 'scoring')
        generation, scoring = terminal.get('generation'), terminal.get('scoring')
        verify_call(generation, 'generation')
        verify_call(scoring, 'scoring')
        require(terminal['answer_status'] == (generation or {}).get('status', 'not_completed') and
                terminal['scoring_status'] == (scoring or {}).get('status', 'not_completed'), 'Stage terminal status differs from selected call')
        require(terminal['answer_finish_reason'] == (generation or {}).get('finish_reason') and
                terminal['finish_reason'] == (scoring or generation or {}).get('finish_reason') and
                terminal['response_model'] == (scoring or generation or {}).get('response_model'), 'Terminal finish/model differs from selected receipt')
        for field, call, source_field in [('answer', generation, 'content'), ('answer_reasoning_content', generation, 'reasoning_content'),
                                          ('raw_evaluation', scoring, 'content'), ('reasoning_content', scoring, 'reasoning_content')]:
            require(terminal.get(field) == (call or {}).get(source_field, ''), 'Terminal text differs from selected receipt: ' + field)
        if terminal.get('legacy_pipeline_success'):
            inner, messages = score_messages(self.bundle, source, terminal['answer'])
            with contextlib.redirect_stdout(io.StringIO()):
                evaluator, result = legacy.evaluate(self.bundle,
                    legacy.Capture({'answer': terminal['raw_evaluation'], 'thinking': terminal['reasoning_content']}, expected=inner), source, terminal['answer'])
            invalid = guard.complete_format(terminal['raw_evaluation'], evaluator.scoring_criteria)
            require(result == terminal['legacy_evaluation'], 'Legacy score replay mismatch')
            require(invalid == terminal['missing_or_invalid_dimensions'], 'Validity guard replay mismatch')
            require(terminal['scoring_audit']['inner_prompt_sha256'] == digest(inner.encode()) and
                    terminal['scoring_audit']['messages_sha256'] == digest(canonical(messages)), 'Scoring prompt audit hash mismatch')
            require(records[scoring['attempt_numbers'][-1]][0]['payload']['messages'] == messages,
                    'Selected scoring receipt does not grade selected answer content')
            expected_status = next((call['status'] for call in (generation, scoring) if call['status'] != 'ok'), 'parse_incomplete' if invalid else 'ok')
            if expected_status == 'error':
                expected_status = 'protocol_error'
            require(terminal['status'] == expected_status, 'Terminal validity/status mismatch')
        else:
            require(terminal['status'] != 'ok', 'Failed legacy pipeline claimed success')
        valid = terminal['status'] == 'ok'
        require(terminal['evaluation'] == (terminal['legacy_evaluation'] if valid else None), 'Invalid score exposed as valid')
        require(terminal['valid_for_complete_case_analysis'] is valid and terminal['score_zero_imputed'] is False, 'Invalid complete-analysis/zero marker')
        self.rows.append({'split': task['split'], 'case_id': task['case_id'], 'question_id': task['question_id'],
                          'status': terminal['status'], 'answer_status': terminal['answer_status'], 'scoring_status': terminal['scoring_status'],
                          'valid_score': valid, 'total_score': (terminal.get('evaluation') or {}).get('总分'),
                          'usage': dict(task_usage), 'elapsed_s': terminal['elapsed_s'], 'attempts': len(paths),
                          'answer_sha256': digest(terminal['answer'].encode()), 'score_text_sha256': digest(terminal['raw_evaluation'].encode())})
        self.terminals[key(task)] = terminal

    def run(self):
        manifest = self.source()
        identity = self.read(self.snapshot / 'results/run_identity.json')
        require(identity['input_sha256'] == INPUT_SHA and identity['task_mapping_sha256'] == MAPPING_SHA and identity['bundle_sha256'] == BUNDLE_SHA, 'Runner release identity mismatch')
        require(identity['parameters'] == manifest['config'] and identity['retry_contract'] == 'legacy108_coupled', 'Runner parameters mismatch')
        scripts = {'script_sha256': HERE / 'run_thinking_pipeline.py', 'helper_sha256': OLD / 'run_answer_smoke.py',
                   'queue_sha256': OLD / 'run_answer_batch128.py', 'monitor_sha256': OLD / 'benchmark_concurrency.py',
                   'legacy_adapter_sha256': OLD / 'run_selfscore_smoke.py', 'audit_guard_sha256': OLD / 'run_selfscore_batch128_v2.py'}
        for field, path in scripts.items():
            require(identity[field] == digest(path.read_bytes()), 'Runner/helper byte hash mismatch: ' + field)
        expected_dirs = {'task_' + digest(canonical(list(key(t))))[:24] for t in self.tasks}
        actual_dirs = {p.name for p in (self.snapshot / 'results').glob('task_*') if p.is_dir()}
        require(actual_dirs == expected_dirs, 'Pilot task coverage differs from expected first ten')
        for task in self.tasks:
            try:
                self.task(task)
            except Exception as error:
                self.errors.append({'question_id': task['question_id'], 'error': str(error), 'type': type(error).__name__})
        summary = self.read(self.snapshot / 'results/summary.json')
        intents = sorted((self.snapshot / 'dispatches').glob('attempt-*.intent.json'))
        require(len(intents) == 1, 'Pilot audit expects a single dispatch before continuation')
        intent = self.read(intents[0])
        exit_record = self.read(intents[0].with_name(intent['dispatch_id'] + '.exit.json'))
        require(intent['max_new_tasks'] == 10 and intent['resume'] is False, 'Dispatch was not the initial ten-task pilot')
        unresolved = {name: summary.get(name) for name in ('inflight', 'needs_reconcile', 'unmatched_intents')}
        total_usage = Counter()
        for usage in self.stage_usage.values():
            total_usage.update(usage)
        counts = dict(Counter(r['status'] for r in self.rows))
        require(summary['counts'] == counts, 'Summary status counts differ from audited terminals')
        require(summary['success'] == summary['valid_scores'] == sum(r['valid_score'] for r in self.rows), 'Summary valid-score count differs')
        require(summary['answer_counts'] == dict(Counter(r['answer_status'] for r in self.rows)) and
                summary['scoring_counts'] == dict(Counter(r['scoring_status'] for r in self.rows)), 'Summary stage counts differ')
        require(summary['usage_totals'] == dict(total_usage), 'Summary usage differs from receipts')
        require(summary['actual_attempts'] == summary['recorded_responses'] == len(self.requests), 'Summary receipt counts differ')
        for split in ('primary', 'secondary'):
            path = self.snapshot / 'results' / ('thinking_pipeline_' + split + '.jsonl')
            require(path.resolve() == path and path.is_file(), 'Missing/redirected consolidated export')
            raw = path.read_bytes()
            self.hashes[str(path.relative_to(self.snapshot))] = {'sha256': digest(raw), 'bytes': len(raw)}
            rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
            expected = [self.terminals[key(t)] for t in self.tasks if t['split'] == split]
            require(rows == expected, 'Consolidated split export differs from audited terminals: ' + split)
        stages = {}
        for stage, field in [('generation', 'answer_status'), ('scoring', 'scoring_status')]:
            stages[stage] = {'terminal_counts': dict(Counter(r[field] for r in self.rows)),
                             'request_counts': dict(Counter(r['classification'] for r in self.requests if r['stage'] == stage)),
                             'usage': dict(self.stage_usage[stage]), 'summed_request_seconds': round(self.stage_elapsed[stage], 3)}
        safe = (not self.errors and len(self.rows) == 10 and all(r['valid_score'] for r in self.rows)
                and all(value == 0 for value in unresolved.values()) and exit_record.get('exit_code') == 0
                and summary.get('pending') == 11085 and summary.get('completed') == 10
                and summary.get('session_max_new_tasks') == 10 and not summary.get('stop_reasons'))
        return {'full_continuation_safe': safe, 'audited_questions': len(self.rows), 'expected_questions': 10,
                'cases_by_split': {s: len({r['case_id'] for r in self.rows if r['split'] == s}) for s in ('primary', 'secondary')},
                'terminal_counts': counts, 'valid_scores': sum(r['valid_score'] for r in self.rows), 'unresolved': unresolved,
                'remaining_unstarted_questions': summary.get('pending'), 'dispatch_exit_code': exit_record.get('exit_code'),
                'walltime_s': summary.get('walltime_s'), 'stages': stages, 'usage_total': dict(total_usage),
                'integrity_errors': self.errors, 'tasks': self.rows, 'requests': self.requests,
                'input_sha256': INPUT_SHA, 'task_mapping_sha256': MAPPING_SHA, 'bundle_sha256': BUNDLE_SHA}


def markdown(report):
    lines = ['# DeepSeek thinking 冒烟核验', '',
             '全量继续建议：' + ('可以继续（本报告不执行启动）。' if report['full_continuation_safe'] else '暂不继续，先核对异常。'), '',
             '| 项目 | 结果 |', '|---|---|',
             f"| 已核验题数 | {report.get('audited_questions', 0)} / 10 |",
             f"| 有效评分 | {report.get('valid_scores', 0)} / 10 |",
             f"| 退出码 | {report.get('dispatch_exit_code', '未核验')} |",
             f"| 全量尚未启动 | {report.get('remaining_unstarted_questions', '未核验')} 题 |",
             f"| 冒烟实际墙钟耗时 | {report.get('walltime_s', '未核验')} 秒 |", '',
             '| 阶段 | 完整 | 截断 | 拒答/过滤 | 其他 | 输入 token | 输出 token | 其中 reasoning token |', '|---|---:|---:|---:|---:|---:|---:|---:|']
    for stage, title in [('generation', '回答'), ('scoring', '评分')]:
        data = report.get('stages', {}).get(stage, {})
        counts, usage = data.get('terminal_counts', {}), data.get('usage', {})
        refusal = counts.get('refusal', 0) + counts.get('content_filter', 0)
        other = sum(counts.values()) - counts.get('ok', 0) - counts.get('truncated', 0) - refusal
        lines.append(f"| {title} | {counts.get('ok', 0)} | {counts.get('truncated', 0)} | {refusal} | {other} | {usage.get('prompt_tokens', 0)} | {usage.get('completion_tokens', 0)} | {usage.get('reasoning_tokens', '未提供')} |")
    lines += ['', '评分“完整”表示模型返回完整文本；是否可用于统计另以“有效评分”数量为准。Token 包括本次全部请求与重试；reasoning token 是输出 token 的子项，不重复相加。',
              '', '核验范围：原回答 prompt、评分 prompt、thinking=true、temperature=0.3、3000/6000 token、原解析器重放及异常有效性；检查评分只使用回答 content、不拼接 reasoning。仅“可以继续”表示所有核验通过。']
    if report.get('integrity_errors'):
        lines += ['', '## 异常', ''] + ['- ' + item.get('question_id', '整体') + '：' + item['error'] for item in report['integrity_errors']]
    if report.get('fatal_error'):
        lines += ['', '整体核验未完成：' + report['fatal_error']]
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True, help='Fetched snapshot containing results and dispatches; raw receipts required')
    parser.add_argument('--output-dir', type=Path, required=True, help='New audit directory, never an existing output directory')
    args = parser.parse_args()
    snapshot, output = args.snapshot.absolute(), args.output_dir.absolute()
    require(snapshot.resolve() == snapshot and snapshot.is_dir(), 'Invalid snapshot directory')
    require(output.resolve() == output and not output.exists() and snapshot not in output.parents, 'Output must be a new directory outside the snapshot')
    audit = Audit(snapshot)
    try:
        report = audit.run()
    except Exception as error:
        report = {'full_continuation_safe': False, 'fatal_error': str(error), 'fatal_error_type': type(error).__name__,
                  'integrity_errors': audit.errors, 'tasks': audit.rows, 'audited_questions': len(audit.rows)}
    report.update(audited_at=dt.datetime.now(dt.timezone.utc).isoformat(), snapshot=str(snapshot),
                  source_root=str(SOURCE), read_only_audit=True, new_model_calls=0, file_hashes=audit.hashes)
    output.mkdir(parents=True, mode=0o700)
    for name, content in [('pilot_audit.json', json.dumps(report, ensure_ascii=False, indent=2) + '\n'),
                          ('pilot_audit.md', markdown(report))]:
        with (output / name).open('x') as stream:
            stream.write(content)
    print(json.dumps({name: report.get(name) for name in ('full_continuation_safe', 'audited_questions', 'valid_scores', 'fatal_error')}, ensure_ascii=False))
    return 0 if report['full_continuation_safe'] else 2


if __name__ == '__main__':
    raise SystemExit(main())
