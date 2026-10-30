#!/usr/bin/env python3
"""Offline, fail-closed receipt audit and immutable merge for the completed batch.

No inference, SSH, scoring, input repair or original-file writes are performed.
"""
import argparse
from collections import Counter
from datetime import datetime
import json
from pathlib import Path

from prepare_answer_smoke import canonical, sha
from prepare_full_ds_pipeline import file_sha, freeze_chunks, require
from prepare_answer_batch128 import CONFIG, KEY_FIELDS, NETWORK_FIELDS, network_task, verify_reuse


REPO = Path(__file__).resolve().parents[3]
BASE = REPO / 'data/pkulaw_v4_answer_batch128_20260928'
SNAPSHOT = BASE / 'fetched/completed_attempt001_rsync'


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def key(row):
    return tuple(row[name] for name in KEY_FIELDS)


def numeric_usage(value):
    require(isinstance(value, dict), 'Missing usage')
    result = {k: v for k, v in value.items()
              if isinstance(v, (int, float)) and not isinstance(v, bool)}
    for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
        require(type(value.get(name)) is int and value[name] >= 0, 'Invalid token count')
    require(value['prompt_tokens'] + value['completion_tokens'] == value['total_tokens'],
            'Token total is inconsistent')
    return result


def evidence(path, base):
    return {'path': str(path.relative_to(base)), 'sha256': file_sha(path), 'bytes': path.stat().st_size}


def read_rows(path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            require(bool(line.strip()), 'Blank JSONL row: ' + str(path))
            yield json.loads(line)


def audit_terminal(task, directory, base):
    """Verify every original request/receipt and its exact final terminal record."""
    expected_hashes = {str(limit): sha(canonical({
        'model': CONFIG['model'], 'messages': task['messages'], 'temperature': .3,
        'max_tokens': limit, 'chat_template_kwargs': {'thinking': False}})) for limit in (3000, 6000)}
    frozen_path = directory / 'frozen_input.json'
    require(load(frozen_path) == dict(task, request_sha256_by_token_limit=expected_hashes),
            directory.name + ': frozen complete request differs')
    terminal_path = directory / 'terminal.json'
    terminal = load(terminal_path)
    require({k: terminal.get(k) for k in task if k != 'messages'}
            == {k: v for k, v in task.items() if k != 'messages'}, 'Terminal task/provenance mismatch')
    require(terminal['request_sha256_by_token_limit'] == expected_hashes, 'Terminal request hashes differ')
    reqs = sorted(directory.glob('attempt_*.request.json'))
    responses = sorted(directory.glob('attempt_*.response.json'))
    require(len(reqs) == len(responses) and len(reqs) in (1, 2),
            'This completed release must have one attempt or one truncation extension per task')
    require([p.name for p in reqs] == ['attempt_%03d.request.json' % i for i in range(1, len(reqs) + 1)],
            'Request sequence has gaps or duplicates')
    usage, finishes, elapsed, receipts, last = Counter(), Counter(), 0.0, [], None
    for number, (request_path, response_path) in enumerate(zip(reqs, responses), 1):
        require(response_path.name == 'attempt_%03d.response.json' % number, 'Unpaired response')
        request, response = load(request_path), load(response_path)
        limit = 3000 if number == 1 else 6000
        payload = {'model': CONFIG['model'], 'messages': task['messages'], 'temperature': .3,
                   'max_tokens': limit, 'chat_template_kwargs': {'thinking': False}}
        require(request['attempt'] == number and request['payload'] == payload
                and sha(canonical(payload)) == expected_hashes[str(limit)], 'Original API payload changed')
        body = response['raw_response']
        require(json.loads(response['raw_body']) == body and response['http_status'] == 200,
                'Raw/parsed/HTTP receipt disagreement')
        require(body.get('model') == CONFIG['model'] and not body.get('error'), 'Response model/error mismatch')
        require(isinstance(body.get('choices'), list) and len(body['choices']) == 1, 'Unexpected response choices')
        choice = body['choices'][0]
        message = choice.get('message')
        require(isinstance(message, dict) and not message.get('refusal'), 'Structured refusal is not success')
        finish = choice.get('finish_reason')
        require(response['finish_reason'] == finish, 'Recorded finish reason mismatch')
        if number < len(reqs):
            require(finish in ('length', 'max_tokens') and response['classification'] == 'truncated',
                    'An intermediate response is not the permitted length retry')
        else:
            require(finish == 'stop' and response['classification'] == 'ok'
                    and isinstance(message.get('content'), str) and message['content'].strip(),
                    'Final refusal/truncation/empty/error cannot be counted as success')
        require(body['usage'] == response['usage'], 'Raw/recorded token usage differs')
        require(body['usage']['completion_tokens'] <= limit, 'Completion exceeds requested length cap')
        usage.update(numeric_usage(body['usage']))
        finishes[finish] += 1
        require(isinstance(response.get('elapsed_s'), (int, float)) and response['elapsed_s'] >= 0,
                'Invalid request duration')
        elapsed += response['elapsed_s']
        receipts.extend([evidence(request_path, base), evidence(response_path, base)])
        last = response
    body = last['raw_response']
    require(terminal['status'] == 'ok' and terminal['finish_reason'] == 'stop'
            and terminal['answer'] == body['choices'][0]['message']['content'], 'Terminal answer/status mismatch')
    require(all(terminal[name] == CONFIG['model'] for name in ('model', 'requested_model', 'response_model'))
            and terminal['temperature'] == .3 and terminal['thinking'] is False, 'Terminal model/parameters differ')
    require(terminal['attempts'] == terminal['request_intents'] == len(reqs)
            and terminal['max_tokens'] == (3000 if len(reqs) == 1 else 6000)
            and terminal['length_retried'] == (len(reqs) == 2), 'Terminal length/attempt count mismatch')
    require(terminal['usage'] == dict(usage) and terminal['final_response_usage'] == body['usage']
            and terminal['elapsed_s'] == round(elapsed, 3), 'Terminal usage/duration mismatch')
    require(terminal['response_id'] == body['id'] == last['request_id'] and terminal['last_error'] is None,
            'Terminal response ID/error differs')
    require(terminal['scoring_performed'] is False and terminal['score_zero_imputed'] is False,
            'Unexpected scoring or imputed scores')
    return terminal, usage, finishes, {
        'task_directory': str(directory.relative_to(base)),
        'frozen_input': evidence(frozen_path, base), 'terminal': evidence(terminal_path, base),
        'receipts': receipts, 'answer_sha256': sha(terminal['answer']),
    }


def audit_and_merge(base, snapshot, output):
    manifest = load(base / 'input_manifest.json')
    stage, identity, summary = (load(snapshot / name) for name in
                                ('stage_manifest.json', 'results/run_identity.json', 'results/summary.json'))
    require(file_sha(base / 'input_manifest.json') == stage['files']['input_manifest.json']['sha256'],
            'Staged preparation manifest differs')
    for relative, record in manifest['files'].items():
        path = base / relative
        require(path.resolve().is_relative_to(base.resolve()), 'Manifest path escapes release')
        require(path.stat().st_size == record['bytes'] and file_sha(path) == record['sha256'],
                'Frozen input/receipt hash mismatch: ' + relative)
    input_bytes = (base / 'input_tasks.json').read_bytes()
    data = json.loads(input_bytes)
    require(sha(input_bytes) == identity['input_sha256'] == stage['files']['input_tasks.json']['sha256']
            == manifest['input_tasks_sha256'], 'Network input hashes differ')
    require({k: v for k, v in data.items() if k != 'tasks'} == CONFIG == identity['parameters'],
            'Frozen runtime configuration differs')
    for filename, identity_key in [('run_answer_batch128.py', 'script_sha256'),
                                   ('run_answer_smoke.py', 'helper_sha256'),
                                   ('benchmark_concurrency.py', 'monitor_sha256')]:
        require(file_sha(Path(__file__).parent / filename) == identity[identity_key]
                == stage['files'][filename]['sha256'], 'Deployed runner/helper identity mismatch')
    mapping = {}
    for row in read_rows(base / 'task_mapping.jsonl'):
        require(key(row) not in mapping, 'Duplicate business mapping key')
        require(sha(row['case_text_masked']) == row['masked_case_text_hash']
                and sha(row['judge_decision_masked']) == row['judge_decision_hash']
                and sha(row['question_text']) == row['question_hash']
                and sha(canonical(row['answer_messages'])) == row['answer_prompt_hash'],
                'Business mapping string/prompt hash mismatch')
        mapping[key(row)] = row
    require(len(mapping) == 11095 and len({k[1] for k in mapping}) == 2219, 'Wrong final target size')
    require(set(Counter(k[:2] for k in mapping).values()) == {5}, 'Not five questions per case')
    excluded = load(base / 'excluded_cases.json')
    excluded_ids = {r['case_id'] for r in excluded}
    require(len(excluded) == len(excluded_ids) == 18 and sum(len(r['question_ids']) for r in excluded) == 90
            and all(r['permanently_excluded'] is True for r in excluded)
            and not (excluded_ids & {k[1] for k in mapping}), 'Permanent exclusion mismatch')
    tasks = {}
    for task in data['tasks']:
        require(set(task) == NETWORK_FIELDS and key(task) not in tasks and key(task) in mapping
                and task == network_task(mapping[key(task)]), 'Network task differs from original business mapping')
        tasks[key(task)] = task
    require(len(tasks) == 11085, 'Wrong new-answer count')
    expected_dirs = {'task_' + sha(canonical(list(k)))[:24] for k in tasks}
    results = snapshot / 'results'
    require({p.name for p in results.glob('task_*')} == expected_dirs, 'Missing/unexpected task directories')
    terminals, merged, usage, finishes, attempts, extended = {}, {}, Counter(), Counter(), 0, 0
    for task_key, task in tasks.items():
        directory = results / ('task_' + sha(canonical(list(task_key)))[:24])
        terminal, tokens, reasons, receipt_evidence = audit_terminal(task, directory, base)
        terminals[task_key] = terminal
        merged[task_key] = dict(terminal, reused=False, answer_origin='formal_batch128', audit_evidence=receipt_evidence)
        usage.update(tokens); finishes.update(reasons)
        attempts += terminal['attempts']; extended += terminal['length_retried']
    exported = {}
    for split in ('primary', 'secondary'):
        for row in read_rows(results / ('answers_deepseek_v4_flash_' + split + '.jsonl')):
            row_key = key(row)
            require(row['split'] == split and row_key not in exported and row_key in terminals
                    and row == terminals[row_key], 'Export differs from original terminal')
            exported[row_key] = row
    require(exported.keys() == terminals.keys() and not (results / 'failures.jsonl').read_text().strip(),
            'Incomplete exports or recorded failures')
    require(attempts == summary['actual_attempts'] == summary['recorded_responses'] == 11189
            and extended == summary['length_retries'] == 104, 'Attempt/length counts differ')
    require(dict(usage) == summary['usage_totals'] and summary['counts'] == {'ok': 11085}
            and summary['status'] == 'completed' and summary['success'] == summary['completed'] == 11085
            and summary['pending'] == summary['failed'] == summary['needs_reconcile'] == summary['unmatched_intents'] == 0,
            'Summary disagrees with audited receipts')
    require(summary['network_tasks'] == 11085 and summary['reused_existing_answers'] == 10
            and summary['reused_answers_imported'] is False and summary['combined_expected_answers'] == 11095,
            'Summary reuse/network denominators differ')
    # Reuses the fail-closed preparation verifier against the original wait900
    # request, raw body, parsed body, terminal, parameters and fixed task mapping.
    verified_reused, _, _, old_usage, reuse_contract = verify_reuse(mapping)
    reused = list(read_rows(base / 'reused_answers.jsonl'))
    require(reused == verified_reused, 'Frozen reused answers differ from reverified original receipts')
    for row in reused:
        require(key(row) not in merged and key(row) in mapping, 'Duplicate or foreign reused task')
        merged[key(row)] = dict(row, answer_origin='reused_wait900')
    require(merged.keys() == mapping.keys(), 'Merged keys do not exactly cover business mapping')
    ordered = [merged[k] for k in mapping]
    require(Counter(r['split'] for r in ordered) == {'primary': 1075, 'secondary': 10020}
            and all(r['status'] == 'ok' and r['finish_reason'] == 'stop' for r in ordered), 'Merged outcome mismatch')
    wall = summary['walltime_s']
    clock_delta = (datetime.fromisoformat(summary['updated_at']) - datetime.fromisoformat(summary['started_at'])).total_seconds()
    require(isinstance(wall, (int, float)) and wall > 0 and abs(clock_delta - wall) < 1,
            'Wall-clock duration inconsistent with timestamps')
    combined_usage = Counter(usage); combined_usage.update(old_usage)
    rounded_wall = round(wall)
    wall_label = f'{rounded_wall // 3600}小时{rounded_wall % 3600 // 60}分{rounded_wall % 60}秒'
    output.mkdir(parents=True, exist_ok=True)
    require(not output.is_symlink(), 'Final output cannot be a symlink')
    files = {}
    for split in ('primary', 'secondary', 'all'):
        name = 'answers_deepseek_v4_flash_' + split + '.jsonl'
        rows = (r for r in ordered if split == 'all' or r['split'] == split)
        files[name] = freeze_chunks(output / name, ((canonical(r) + '\n').encode('utf-8') for r in rows))
    audit = {
        'run_id': CONFIG['run_id'], 'audit_status': 'passed', 'stage': 'answering_only',
        'source_snapshot': str(snapshot.relative_to(base)),
        'permanently_excluded_cases': 18, 'permanently_excluded_questions': 90,
        'cases': 2219, 'answers': 11095, 'cases_by_split': {'primary': 215, 'secondary': 2004},
        'answers_by_split': {'primary': 1075, 'secondary': 10020},
        'new_network_answers': 11085, 'historical_reused_answers': 10,
        'final_status_counts': {'ok': 11095}, 'final_finish_reason_counts': {'stop': 11095},
        'final_failed_answers': 0, 'final_structured_refusals': 0, 'final_truncated_answers': 0,
        'new_network_request_receipt_pairs': attempts, 'all_attempt_finish_reason_counts': dict(finishes),
        'length_extended_answers': extended, 'length_extended_then_completed': extended,
        'duplicate_keys': 0, 'excluded_case_overlap': 0, 'mapping_exact_coverage': True,
        'raw_body_parsed_response_terminal_export_consistent': True,
        'all_network_requests_match_original_frozen_payload': True,
        'new_network_usage_all_attempts': dict(usage), 'historical_reused_usage': old_usage,
        'combined_usage_for_provenance_not_new_consumption': dict(combined_usage),
        'usage_unit': 'API-reported tokens; all attempts including 104 truncated first responses. Historical reused tokens are not new consumption.',
        'network_walltime_s': wall, 'network_walltime_rounded': wall_label,
        'network_started_at_utc': summary['started_at'], 'network_finished_at_utc': summary['updated_at'],
        'time_unit_note': 'Elapsed wall-clock seconds, not the sum of concurrent per-request durations. Historical smoke time is not added.',
        'runtime_parameters': CONFIG, 'max_observed_inflight': summary['max_inflight'],
        'scientific_prompt_parameters_unchanged_from_answer_smoke': True,
        'engineering_changes_from_reused_smoke': {'workers': [2, 128], 'timeout_s': [900, 1800]},
        'reuse_contract': reuse_contract,
        'scoring_performed': False, 'cross_judging_performed': False,
        'interpretation_limit': 'Normal generation is not legal correctness; prose-only refusals without structured refusal markers are retained under the unchanged historical parser. Remaining cases have not all undergone semantic leakage review.',
        'cost_note': 'This is a local GPU service run, not a pricing or zero-cost claim. Token counts are not dollar charges.',
        'source_evidence': {name: evidence(base / name, base) for name in
                            ('input_tasks.json', 'input_manifest.json', 'task_mapping.jsonl', 'reused_answers.jsonl', 'excluded_cases.json')},
        'snapshot_evidence': {name: evidence(snapshot / name, base) for name in
                              ('stage_manifest.json', 'results/run_identity.json', 'results/summary.json')},
        'auditor_sha256': file_sha(Path(__file__)), 'files': files,
        'audit_api_calls': 0, 'audit_ssh_calls': 0, 'original_files_modified': False,
    }
    files_summary = freeze_chunks(output / 'audit_summary.json',
        [(json.dumps(audit, ensure_ascii=False, indent=2) + '\n').encode('utf-8')])
    report = '\n'.join([
        '# DeepSeek V4 Flash 全量回答结果', '',
        '| 项目 | 结果 |', '|---|---|',
        '| 正式保留 | 2,219 案，11,095 题，每案 5 题 |',
        '| primary | 215 案，1,075 条回答 |',
        '| secondary | 2,004 案，10,020 条回答 |',
        '| 永久排除 | 18 案，90 题；不修复、不回填 |',
        '| 本次新生成 / 历史核验复用 | 11,085 / 10 条 |',
        '| 最终结果 | 11,095 条全部正常结束（ok / stop） |',
        '| 最终接口失败 / 结构化拒答或过滤 / 截断 | 0 / 0 / 0（现有结构化状态口径） |',
        '| 初次截断补长 | 104 条，均从 3,000 提至 6,000 后完成 |',
        '| 本次请求及回执 | 11,189 对，完整匹配 |',
        '| 新生成墙钟耗时 | ' + wall_label + ' |',
        '| 并发 | 128，实测峰值 128 |',
        '| 模型与参数 | deepseek_v4_flash；temperature=0.3；thinking=false |',
        '| Prompt 与长度规则 | 沿用原回答阶段；初始 3,000，截断补长一次至 6,000 |',
        '| 本轮评分 / 互评 | 未执行 / 未执行 |', '',
        '## Token 用量', '',
        '| 范围 | 输入 tokens | 输出 tokens | 合计 tokens |', '|---|---:|---:|---:|',
        '| 本次网络请求（含截断尝试） | ' + ' | '.join(f'{usage[k]:,}' for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')) + ' |',
        '| 复用的历史 10 条 | ' + ' | '.join(f'{old_usage[k]:,}' for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')) + ' |',
        '| 合并后的溯源总量 | ' + ' | '.join(f'{combined_usage[k]:,}' for k in ('prompt_tokens', 'completion_tokens', 'total_tokens')) + ' |', '',
        '历史 10 条的 token 不属于本次新增消耗；token 数不等于美元费用。耗时为新网络任务墙钟时间，不是并发请求耗时相加。', '',
        '## 数据文件', '',
        '- `answers_deepseek_v4_flash_all.jsonl`：全部 11,095 条回答。',
        '- `answers_deepseek_v4_flash_primary.jsonl`：primary 回答。',
        '- `answers_deepseek_v4_flash_secondary.jsonl`：secondary 回答。',
        '- `audit_summary.json`：计数、参数、哈希和核验口径。', '',
        '已核验：唯一键与任务映射完全一致，无重复、无排除案混入；原请求、原始回执、终态和导出一致；历史 10 条再次核验后合并。保留原始文件，未改写输入或答案。', '',
        '“正常结束”仅表示生成完成，不代表法律正确。未加启发式文本拒答检测；自然语言拒答仍按历史规则原样保留。排除 18 案不等于其余案件已通过全面语义泄漏审查。', '',
    ])
    report_info = freeze_chunks(output / 'RESULTS.md', [report.encode('utf-8')])
    print(json.dumps({'output': str(output), 'cases': 2219, 'answers': 11095, 'audit': 'passed',
                      'files': dict(files, **{'audit_summary.json': files_summary, 'RESULTS.md': report_info}),
                      'api_calls': 0, 'ssh_calls': 0}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base', type=Path, default=BASE)
    parser.add_argument('--snapshot', type=Path, default=SNAPSHOT)
    parser.add_argument('--output-dir', type=Path, default=BASE / 'final')
    args = parser.parse_args()
    require(args.output_dir.resolve() not in {args.base.resolve(), args.snapshot.resolve(),
                                             (args.snapshot / 'results').resolve()}, 'Cannot write into source directories')
    audit_and_merge(args.base, args.snapshot, args.output_dir)


if __name__ == '__main__':
    main()
