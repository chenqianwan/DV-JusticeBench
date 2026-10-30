#!/usr/bin/env python3
"""Offline guard-only v2: reuse both returned scores and freeze the remaining work.

Never changes v1 inputs, raw receipts, legacy scoring code or model outputs.
"""
import argparse
import ast
from collections import Counter
import json
from pathlib import Path
import re

from prepare_answer_smoke import canonical, sha
from prepare_full_ds_pipeline import freeze_chunks, file_sha, require
from prepare_selfscore_batch128 import historical_capture, TASK_FIELDS, KEY_FIELDS, key, json_bytes


REPO = Path(__file__).resolve().parents[3]
V1 = REPO / 'data/pkulaw_v4_selfscore_batch128_20260928'
SNAPSHOT = V1 / 'fetched/20260928T150612404103'
OUT = REPO / 'data/pkulaw_v4_selfscore_batch128_v2_20260928'
CONFIG = {
    'run_id': OUT.name, 'service_job_id': '402816', 'base_url': 'http://dgx-040:18044/v1',
    'model': 'deepseek_v4_flash', 'thinking': True, 'temperature': .3,
    'initial_max_tokens': 3000, 'timeout_s': 1800, 'workers': 128,
}
OMIT_TERMINAL = {'case_text_masked', 'judge_decision_masked', 'answer'}


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def pure_guard(path):
    source = path.read_text(encoding='utf-8')
    node = next(node for node in ast.parse(source).body
                if isinstance(node, ast.FunctionDef) and node.name == 'complete_format')
    namespace = {'re': re}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace['complete_format'], sha(ast.get_source_segment(source, node))


def score_usage(usage):
    result = {k: v for k, v in usage.items()
              if isinstance(v, (int, float)) and not isinstance(v, bool)}
    details = usage.get('completion_tokens_details') or {}
    if 'reasoning_tokens' not in usage and isinstance(details, dict):
        value = details.get('reasoning_tokens')
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            result['reasoning_tokens'] = value
    return result


def prepare(output):
    v1_input_bytes = (V1 / 'input_scoring.json').read_bytes()
    source = json.loads(v1_input_bytes)
    v1_manifest_bytes = (V1 / 'input_manifest.json').read_bytes()
    manifest_v1 = json.loads(v1_manifest_bytes)
    require(sha(v1_input_bytes) == manifest_v1['input_scoring_sha256'], 'V1 scoring input hash differs')
    require({k: v for k, v in source.items() if k in CONFIG} == dict(CONFIG, run_id=V1.name),
            'V1 scientific/runtime parameters changed')
    for name, record in manifest_v1['files'].items():
        require(file_sha(V1 / name) == record['sha256'] and (V1 / name).stat().st_size == record['bytes'],
                'V1 frozen file changed: ' + name)
    bundle_bytes = (V1 / 'legacy_scoring_bundle.py').read_bytes()
    bundle_hash = sha(bundle_bytes)
    require(bundle_hash == source['bundle_sha256'] == manifest_v1['bundle_sha256']
            == '6af07139a06994b3192e450e5fa79f23e69851b889e999a99187efaabc81d9ad',
            'Historical scoring bundle changed')
    bundle, Capture, evaluate, adapter_hashes = historical_capture(bundle_bytes)
    guard_path = Path(__file__).with_name('run_selfscore_batch128_v2.py')
    guard, guard_hash = pure_guard(guard_path)
    original_guard, original_guard_hash = pure_guard(Path(__file__).with_name('run_selfscore_smoke.py'))
    identity_path, summary_path = SNAPSHOT / 'results/run_identity.json', SNAPSHOT / 'results/summary.json'
    exit_path = SNAPSHOT / 'dispatches/attempt-001.exit.json'
    identity, summary, exit_record = load(identity_path), load(summary_path), load(exit_path)
    require(identity['input_sha256'] == sha(v1_input_bytes) and identity['bundle_sha256'] == bundle_hash
            and identity['upstream_answers_sha256'] == source['upstream_answers_sha256']
            and identity['parameters'] == dict(CONFIG, run_id=V1.name), 'V1 execution identity differs')
    for name, field in [('run_selfscore_batch128.py', 'script_sha256'),
                         ('run_selfscore_smoke.py', 'legacy_adapter_sha256'),
                         ('run_answer_smoke.py', 'helper_sha256'),
                         ('run_answer_batch128.py', 'queue_sha256'),
                         ('benchmark_concurrency.py', 'monitor_sha256')]:
        require(file_sha(Path(__file__).with_name(name)) == identity[field], 'V1 deployed helper changed: ' + name)
    require(summary['completed'] == summary['actual_attempts'] == summary['recorded_responses'] == 2
            and summary['counts'] == {'ok': 1, 'parse_incomplete': 1}
            and summary['pending'] == 11093 and summary['inflight'] == summary['unmatched_intents'] == 0
            and summary['session_max_new_tasks'] == 2 and exit_record['exit_code'] == 0,
            'V1 acceptance segment is not complete/closed as expected')
    full_tasks = source['tasks']
    require(len(full_tasks) == len({key(t) for t in full_tasks}) == 11095
            and all(set(t) == TASK_FIELDS for t in full_tasks), 'V1 V18 coverage differs')
    reused_tasks = full_tasks[:2]
    require([t['split'] for t in reused_tasks] == ['primary', 'secondary'], 'Expected one acceptance task per split')
    reused_keys = {key(t) for t in reused_tasks}
    pending = [t for t in full_tasks if key(t) not in reused_keys]
    require(len(pending) == 11093 and Counter(t['split'] for t in pending) == {'primary': 1074, 'secondary': 10019},
            'Remaining scoring counts differ')
    expected_dirs = {'task_' + sha(canonical(list(key(t))))[:24] for t in reused_tasks}
    require({p.name for p in (SNAPSHOT / 'results').glob('task_*')} == expected_dirs,
            'Snapshot contains missing/additional scored tasks')
    evidence_files, reused, audits, usage = [], [], [], Counter()
    for path, name in [(identity_path, 'run_identity.json'), (summary_path, 'summary.json'),
                       (exit_path, 'attempt-001.exit.json')]:
        evidence_files.append(('reused_evidence/' + name, path.read_bytes()))
    for task in reused_tasks:
        dirname = 'task_' + sha(canonical(list(key(task))))[:24]
        directory = SNAPSHOT / 'results' / dirname
        require([p.name for p in directory.glob('attempt_*.request.json')] == ['attempt_001.request.json']
                and [p.name for p in directory.glob('attempt_*.response.json')] == ['attempt_001.response.json'],
                'Acceptance task has an unexpected request history')
        paths = {name: directory / name for name in
                 ('frozen_input.json', 'attempt_001.request.json', 'attempt_001.response.json', 'terminal.json')}
        frozen, request, response, terminal = (load(paths[name]) for name in paths)
        capture = Capture()
        evaluator, _ = evaluate(bundle, capture, task, task['answer'])
        inner = capture.prompts[0]
        messages = bundle.analysis_messages(inner, None)
        require(sha(canonical(messages)) == task['scoring_prompt_hash'], 'Frozen legacy scoring prompt differs')
        hashes = {str(limit): sha(canonical(dict(model=CONFIG['model'], messages=messages, temperature=.3,
                    max_tokens=limit, chat_template_kwargs={'thinking': True}))) for limit in (3000, 6000)}
        frozen_expected = {k: v for k, v in task.items() if k not in OMIT_TERMINAL}
        frozen_expected.update(messages=messages, inner_prompt_sha256=sha(inner), request_sha256_by_token_limit=hashes)
        require(frozen == frozen_expected, 'Original frozen score input differs')
        payload = dict(model=CONFIG['model'], messages=messages, temperature=.3,
                       max_tokens=3000, chat_template_kwargs={'thinking': True})
        require(request['attempt'] == 1 and request['payload'] == payload
                and sha(canonical(request['payload'])) == hashes['3000'], 'Original API score payload differs')
        body = response['raw_response']
        require(json.loads(response['raw_body']) == body and response['http_status'] == 200
                and response['classification'] == 'ok' and response['finish_reason'] == 'stop'
                and body['model'] == CONFIG['model'] and not body.get('error'), 'Original scoring response failed')
        require(len(body['choices']) == 1 and body['choices'][0]['finish_reason'] == 'stop', 'Incomplete score response')
        message = body['choices'][0]['message']
        content = message['content']
        thinking = message.get('reasoning_content') or message.get('reasoning') or ''
        require(isinstance(content, str) and content.strip() and not message.get('refusal')
                and isinstance(thinking, str), 'Empty/refused score cannot be reused')
        require({k: terminal.get(k) for k in task if k not in OMIT_TERMINAL}
                == {k: v for k, v in task.items() if k not in OMIT_TERMINAL}, 'Score provenance differs')
        require(terminal['raw_evaluation'] == content and terminal['reasoning_content'] == thinking
                and terminal['finish_reason'] == 'stop' and terminal['requested_model'] == terminal['response_model'] == CONFIG['model']
                and terminal['temperature'] == .3 and terminal['thinking'] is True
                and terminal['max_tokens'] == 3000 and terminal['length_retried'] is False
                and terminal['attempts'] == terminal['request_intents'] == terminal['recorded_responses'] == 1
                and terminal['last_error'] is None and terminal['parse_error'] is None
                and terminal['evaluation_parser'] == 'legacy' and terminal['score_zero_imputed'] is False,
                'Original scoring terminal status/parameters differ')
        require(terminal['request_sha256_by_token_limit'] == hashes
                and terminal['response_id'] == body['id'] == response['request_id']
                and terminal['usage'] == score_usage(body['usage'])
                and terminal['final_response_usage'] == response['usage'] == body['usage']
                and terminal['elapsed_s'] == response['elapsed_s'], 'Original scoring ID/usage/duration differs')
        replay = Capture({'answer': content, 'thinking': thinking}, expected=inner)
        evaluator, evaluation = evaluate(bundle, replay, task, task['answer'])
        require(evaluation == terminal['legacy_evaluation'], 'Original legacy evaluation did not replay exactly')
        require(guard(content, evaluator.scoring_criteria) == [], 'New guard does not accept the actual five score headings')
        headers = re.findall(r'(?m)^\s*【([^】]+)】得分[：:]\s*([0-4])分\s*$', content)
        expected_scores = [3, 3, 4, 4, 1] if task['split'] == 'primary' else [4, 4, 4, 4, 4]
        require([name for name, _ in headers] == list(evaluator.scoring_criteria)
                and [int(value) for _, value in headers] == expected_scores
                and list(evaluation['各维度得分'].values()) == expected_scores
                and evaluation['总分'] == sum(expected_scores), 'Acceptance heading/legacy scores differ')
        old_missing = original_guard(content, evaluator.scoring_criteria)
        require(old_missing == terminal['missing_or_invalid_dimensions'], 'Original guard failure did not reproduce')
        if task['split'] == 'primary':
            require(terminal['status'] == 'parse_incomplete' and terminal['evaluation'] is None and old_missing,
                    'Primary is not the known guard false-negative')
            reason = 'Original validity guard matched dimension names in introductory prose; all five actual bracketed score headings are present. Restore the unchanged legacy result offline.'
        else:
            require(terminal['status'] == 'ok' and terminal['evaluation'] == evaluation and not old_missing,
                    'Secondary is not the original valid score')
            reason = 'Reuse the already valid score without changing raw text, legacy parsing or numeric evaluation.'
        receipt_hashes = {}
        for name, path in paths.items():
            data = path.read_bytes()
            relative = 'reused_evidence/' + dirname + '/' + name
            evidence_files.append((relative, data))
            receipt_hashes[name] = {'path': relative, 'sha256': sha(data), 'bytes': len(data)}
        reuse_source = {
            'run_id': V1.name, 'task_directory': dirname,
            'terminal_sha256': receipt_hashes['terminal.json']['sha256'],
            'original_status': terminal['status'], 'original_missing_or_invalid_dimensions': old_missing,
            'original_input_sha256': sha(v1_input_bytes), 'reason': reason,
            'guard_only_offline_recovery': task['split'] == 'primary', 'model_request_reissued': False,
            'raw_evaluation_sha256': sha(content), 'reasoning_content_sha256': sha(thinking),
            'legacy_evaluation_sha256': sha(canonical(evaluation)),
            'original_guard_source_sha256': original_guard_hash, 'new_guard_source_sha256': guard_hash,
            'receipts': receipt_hashes,
        }
        row = dict(terminal, status='ok', evaluation=evaluation, missing_or_invalid_dimensions=[],
                   reused=True, reuse_source=reuse_source)
        # The only original terminal fields permitted to differ are guard status,
        # missing-dimension diagnostics and exposing the already stored legacy result.
        require(all(row[k] == value for k, value in terminal.items()
                    if k not in {'status', 'evaluation', 'missing_or_invalid_dimensions'}),
                'Recovery changed an original scientific field')
        reused.append(row)
        audits.append({**{k: task[k] for k in KEY_FIELDS}, 'total': evaluation['总分'],
                       'dimension_scores': evaluation['各维度得分'], **reuse_source})
        usage.update(score_usage(body['usage']))
    require(dict(usage) == summary['usage_totals'], 'Reused usage does not match completed v1 segment')
    all_keys = {key(task) for task in pending} | {key(row) for row in reused}
    require(not ({key(task) for task in pending} & {key(row) for row in reused})
            and all_keys == {key(task) for task in full_tasks}
            and len({k[1] for k in all_keys}) == 2219
            and set(Counter(k[:2] for k in all_keys).values()) == {5}, 'Network/reused coverage differs')
    excluded_bytes = (V1 / 'excluded_cases.json').read_bytes()
    excluded = json.loads(excluded_bytes)
    excluded_ids = {row['case_id'] for row in excluded}
    require(len(excluded_ids) == 18 and not excluded_ids & {k[1] for k in all_keys}, 'Permanent exclusions changed')
    network_per_case = Counter(key(task)[:2] for task in pending)
    require(Counter(network_per_case.values()) == {5: 2217, 4: 2}, 'Partial network case sizes differ')
    output.mkdir(parents=True, exist_ok=True)
    require(not output.is_symlink(), 'Output cannot be a symlink')
    files = {}
    for name, content in [('legacy_scoring_bundle.py', bundle_bytes), ('excluded_cases.json', excluded_bytes),
                          ('reused_tasks.json', json_bytes(reused_tasks))]:
        files[name] = freeze_chunks(output / name, [content])
    files['reused_scores.jsonl'] = freeze_chunks(output / 'reused_scores.jsonl',
        ((canonical(row) + '\n').encode('utf-8') for row in reused))
    for name, content in evidence_files:
        target = output / name
        target.parent.mkdir(parents=True, exist_ok=True)
        files[name] = freeze_chunks(target, [content])
    def input_chunks():
        top = dict(CONFIG, bundle_sha256=bundle_hash, upstream_answers_sha256=source['upstream_answers_sha256'])
        yield (canonical(top)[:-1] + ',"tasks":[').encode('utf-8')
        for index, task in enumerate(pending):
            yield ((',' if index else '') + canonical(task)).encode('utf-8')
        yield b']}\n'
    files['input_scoring.json'] = freeze_chunks(output / 'input_scoring.json', input_chunks())
    manifest = {
        'run_id': CONFIG['run_id'], 'schema': 'V18', 'stage': 'self_scoring_guard_only_v2', 'state': 'prepared_not_started',
        'cases': 2219, 'questions': 11095, 'combined_expected_scores': 11095,
        'network_tasks': 11093, 'reused_scores': 2, 'reused_cases': 2,
        'cases_by_split': {'primary': 215, 'secondary': 2004},
        'questions_by_split': {'primary': 1075, 'secondary': 10020},
        'network_questions_by_split': {'primary': 1074, 'secondary': 10019},
        'network_cases_by_question_count': {'5': 2217, '4': 2},
        'combined_questions_per_case': 5, 'permanently_excluded_cases': 18,
        'permanently_excluded_questions': 90, 'excluded_case_ids': sorted(excluded_ids),
        'source_v1_run': V1.name, 'source_snapshot': str(SNAPSHOT.relative_to(REPO)),
        'source_v1_input_sha256': sha(v1_input_bytes), 'source_v1_manifest_sha256': sha(v1_manifest_bytes),
        'upstream_answers_sha256': source['upstream_answers_sha256'],
        'bundle_sha256': bundle_hash, 'input_scoring_sha256': files['input_scoring.json']['sha256'],
        'reused_scores_sha256': files['reused_scores.jsonl']['sha256'],
        'reused_tasks_sha256': files['reused_tasks.json']['sha256'],
        'runtime_parameters': CONFIG, 'task_fields': sorted(TASK_FIELDS),
        'prompt_parameters_bundle_answers_unchanged': True, 'evaluation_parser': 'legacy',
        'only_guard_changed': True, 'guard_source_file': str(guard_path.relative_to(REPO)),
        'guard_source_sha256': guard_hash, 'original_guard_source_sha256': original_guard_hash,
        **adapter_hashes,
        'guard_change': 'Validity check locates actual bracketed score headings instead of dimension mentions in introductory prose. Does not change legacy parsing, numerical scores, error flags or deductions.',
        'reuse_verification': audits, 'reused_historical_usage': dict(usage),
        'usage_note': 'Two scores already generated in v1. Their usage is historical, not new v2 API consumption.',
        'source_terminal_overwritten': False, 'primary_original_status_preserved_in_reuse_source': True,
        'network_task_contents_identical_to_v1': True, 'network_task_order': 'Original v1 order with exactly the two acceptance task keys removed.',
        'reuse_policy': 'Do not resend the two accepted API requests; offline recovery only. Merge these two records with v2 network results by split/case_id/question_id.',
        'failure_policy': 'Operational/guard failures remain separate from valid zero scores. Structured refusal/truncation/error is never treated as successful evaluation.',
        'answers_regenerated': 0, 'questions_regenerated': 0, 'old_scoring_requests_reissued': 0,
        'api_calls_during_preparation': 0, 'ssh_calls': 0, 'files': files,
    }
    freeze_chunks(output / 'input_manifest.json', [json_bytes(manifest)])
    report = '\n'.join([
        '# 全量自评分 v2：仅修正格式检查', '',
        '| 项目 | 结果 / 设置 |', '|---|---|',
        '| 最终范围 | 2,219 案 / 11,095 题，每案 5 题 |',
        '| v2 待评分 | 11,093 题（primary 1,074；secondary 10,019） |',
        '| 已返回并复用 | 2 题，不重新请求模型 |',
        '| primary 先行题 | 原始五维 3 / 3 / 4 / 4 / 1，原 legacy 总分 15 原样复用 |',
        '| secondary 先行题 | 原始五维 4 / 4 / 4 / 4 / 4，原 legacy 总分 20 原样复用 |',
        '| 修复范围 | 仅输出格式有效性检查；避免前言维度名称造成误报 |',
        '| 未改变 | 原 prompt、模型、采样、完整答案、legacy 解析及扣分规则 |',
        '| 永久排除 | 18 脏案 / 90 题，不回填 |',
        '| 并发 / 等待 | 128 / 1,800 秒 |',
        '| temperature / thinking / 输出上限 | 0.3 / true / 3,000→6,000（截断补长一次） |',
        '| 准备新增 API / SSH | 0 / 0 |', '',
        'primary 的原始接口正常 stop，五个真实【维度】标题均有明确得分；旧格式检查误匹配前言中的维度名称，标为 parse_incomplete。v2 只将原已算出的 legacy_evaluation 暴露为有效 evaluation，不重算成另一套分数，不改原始文本。',
        '原 v1 terminal、请求、响应和旧失败状态均保留；reused_scores.jsonl 中 reuse_source 记录原 status、terminal 完整字节哈希和修复原因。secondary 评分内容原样保留。', '',
        '## 文件', '',
        '- `input_scoring.json`：待跑 11,093 个原 V18 任务，只有 run_id 和任务集合与 v1 不同。',
        '- `reused_tasks.json`：两条先行题的完整原 V18 输入。',
        '- `reused_scores.jsonl`：两条复用评分及原状态、证据哈希。',
        '- `reused_evidence/`：原始请求、回执、终态和 v1 退出证据的逐字副本。',
        '- `legacy_scoring_bundle.py`、`excluded_cases.json`：与 v1 完全同字节。',
        '- `input_manifest.json`：联合覆盖、参数、哈希和 guard-only 说明。', '',
        '两案各有 1 题已复用，因此 v2 网络列表允许这两案各剩 4 题；与复用文件合并后，全部 2,219 案均为 5 题。',
        '本次是 DeepSeek V4 Flash 自评分，不是多模型互评或专家复核。准备完成不代表剩余评分已完成。', '',
        '输入 SHA-256：`' + files['input_scoring.json']['sha256'] + '`',
        '原评分 bundle SHA-256：`' + bundle_hash + '`', '',
    ])
    freeze_chunks(output / 'RUN.md', [report.encode('utf-8')])
    print(json.dumps({'output': str(output), 'network_tasks': 11093, 'reused_scores': 2,
                      'combined_scores': 11095, 'cases': 2219,
                      'input_scoring_sha256': files['input_scoring.json']['sha256'],
                      'input_bytes': files['input_scoring.json']['bytes'],
                      'reused_scores_sha256': files['reused_scores.jsonl']['sha256'],
                      'input_manifest_sha256': file_sha(output / 'input_manifest.json'),
                      'bundle_sha256': bundle_hash, 'guard_source_sha256': guard_hash,
                      'api_calls': 0, 'ssh_calls': 0}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=OUT)
    args = parser.parse_args()
    require(args.output_dir.resolve() not in {V1.resolve(), SNAPSHOT.resolve(), (SNAPSHOT / 'results').resolve()},
            'Cannot write into immutable v1 data')
    prepare(args.output_dir)


if __name__ == '__main__':
    main()
