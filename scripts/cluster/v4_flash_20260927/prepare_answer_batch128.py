#!/usr/bin/env python3
"""Freeze the authorized answer-only release, offline and without overwrites.

The 18 adjudication-contaminated cases are permanently excluded from this
experiment. Original releases and receipts remain untouched. Only the exact
historical fact/question messages enter the network task file; the separate
business mapping retains judgments for a later, separately authorized stage.
"""
import argparse
import ast
from collections import Counter
import json
from pathlib import Path

from prepare_answer_smoke import canonical, frozen_messages, sha, SOURCE_COMMIT
from prepare_full_ds_pipeline import freeze_chunks, require, file_sha, TASK_FIELDS


REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / 'data/pkulaw_v4_full_pipeline_20260927'
SMOKE = REPO / 'data/pkulaw_v4_answer_smoke_20260927_wait900'
OUT = REPO / 'data/pkulaw_v4_answer_batch128_20260928'
CONFIG = {
    'run_id': OUT.name, 'service_job_id': '402816',
    'base_url': 'http://dgx-040:18044/v1', 'model': 'deepseek_v4_flash',
    'thinking': False, 'temperature': 0.3, 'initial_max_tokens': 3000,
    'timeout_s': 1800, 'workers': 128,
}
NETWORK_FIELDS = {
    'split', 'case_id', 'question_id', 'messages', 'prompt_hash',
    'source_case_text_hash', 'masked_case_text_hash', 'question_hash', 'question_text',
}
KEY_FIELDS = ('split', 'case_id', 'question_id')


def key(task):
    return tuple(task[field] for field in KEY_FIELDS)


def network_task(task):
    result = {field: task[field] for field in NETWORK_FIELDS - {'messages', 'prompt_hash'}}
    result.update(messages=task['answer_messages'], prompt_hash=task['answer_prompt_hash'])
    require(set(result) == NETWORK_FIELDS, 'Network task schema mismatch')
    return result


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def jsonl_chunks(rows):
    for row in rows:
        yield (canonical(row) + '\n').encode('utf-8')


def network_chunks(tasks):
    # Stream one task at a time instead of building another 70+ MB JSON string.
    yield (canonical(CONFIG)[:-1] + ',"tasks":[').encode('utf-8')
    for index, task in enumerate(tasks):
        yield ((',' if index else '') + canonical(task)).encode('utf-8')
    yield b']}\n'


def evidence_file(relative, source_path):
    data = source_path.read_bytes()
    return relative, data, {'sha256': sha(data), 'bytes': len(data)}


def verify_reuse(retained):
    """Require exact task, request, response, terminal, model and token agreement."""
    input_bytes = (SMOKE / 'input_tasks.json').read_bytes()
    source_input = json.loads(input_bytes)
    manifest_bytes = (SMOKE / 'input_manifest.json').read_bytes()
    source_manifest = json.loads(manifest_bytes)
    identity_bytes = (SMOKE / 'results/run_identity.json').read_bytes()
    identity = json.loads(identity_bytes)
    require(sha(input_bytes) == source_manifest['input_tasks_sha256']
            == identity['input_sha256'], 'Reuse input hash mismatch')
    expected = dict(CONFIG, run_id=SMOKE.name, timeout_s=900, workers=2)
    require({k: source_input[k] for k in CONFIG} == expected
            and identity['parameters'] == expected, 'Reuse configuration mismatch')
    require(identity['http_limit_per_task'] == 18, 'Reuse retry cap mismatch')
    base_script = REPO / 'scripts/cluster/v4_flash_20260927/run_answer_smoke.py'
    adapter_script = base_script.with_name('run_answer_smoke_wait900.py')
    require(file_sha(base_script) == identity['script_sha256'], 'Historical smoke runner changed')
    require(file_sha(adapter_script) == identity['adapter_sha256'], 'Historical wait adapter changed')
    # Compile only the inspected, network-free response classifier definition.
    tree = ast.parse(base_script.read_text())
    classify_node = next(node for node in tree.body
                         if isinstance(node, ast.FunctionDef) and node.name == 'classify')
    namespace = {}
    exec(compile(ast.Module(body=[classify_node], type_ignores=[]), str(base_script), 'exec'), namespace)
    classify = namespace['classify']
    tasks = source_input['tasks']
    require(len(tasks) == len({key(t) for t in tasks}) == 10, 'Expected ten unique reuse tasks')
    evidence = [
        evidence_file('reused_evidence/input_tasks.json', SMOKE / 'input_tasks.json'),
        evidence_file('reused_evidence/input_manifest.json', SMOKE / 'input_manifest.json'),
        evidence_file('reused_evidence/run_identity.json', SMOKE / 'results/run_identity.json'),
        evidence_file('reused_evidence/run_answer_smoke.py', base_script),
        evidence_file('reused_evidence/run_answer_smoke_wait900.py', adapter_script),
    ]
    reused, audits, totals = [], [], Counter()
    for task in tasks:
        require(key(task) in retained, 'Reuse task not in retained release')
        full = retained[key(task)]
        require(task == network_task(full), 'Reuse prompt, question or source mapping mismatch')
        task_name = 'task_' + sha(canonical(list(key(task))))[:24]
        directory = SMOKE / 'results' / task_name
        requests = sorted(directory.glob('attempt_*.request.json'))
        responses = sorted(directory.glob('attempt_*.response.json'))
        # These ten known successful receipts each completed on their first 3000-token call.
        # Fail closed if their history changes; do not silently reinterpret a new retry chain.
        require([p.name for p in requests] == ['attempt_001.request.json']
                and [p.name for p in responses] == ['attempt_001.response.json'],
                task_name + ': expected the frozen one-attempt history')
        request = json.loads(requests[0].read_text())
        response = json.loads(responses[0].read_text())
        terminal_path = directory / 'terminal.json'
        terminal = json.loads(terminal_path.read_text())
        payload = {'model': CONFIG['model'], 'messages': task['messages'],
                   'temperature': 0.3, 'max_tokens': 3000,
                   'chat_template_kwargs': {'thinking': False}}
        require(request['attempt'] == 1 and request['payload'] == payload,
                task_name + ': historical API payload differs')
        require(json.loads(response['raw_body']) == response['raw_response'],
                task_name + ': raw and parsed responses differ')
        status, answer, finish = classify(response)
        require(status == response['classification'] == terminal['status'] == 'ok'
                and finish == response['finish_reason'] == terminal['finish_reason'] == 'stop'
                and isinstance(answer, str) and answer.strip() and terminal['answer'] == answer,
                task_name + ': unsuccessful or inconsistent answer')
        body = response['raw_response']
        require(all(terminal[field] == CONFIG['model'] for field in
                    ('model', 'requested_model', 'response_model'))
                and body['model'] == CONFIG['model'], task_name + ': model alias mismatch')
        require(terminal['temperature'] == 0.3 and terminal['thinking'] is False
                and terminal['max_tokens'] == 3000 and terminal['length_retried'] is False
                and terminal['attempts'] == terminal['request_intents'] == 1
                and terminal['last_error'] is None, task_name + ': sampling/length/status mismatch')
        require({field: terminal[field] for field in task if field != 'messages'}
                == {field: value for field, value in task.items() if field != 'messages'},
                task_name + ': terminal identity/provenance mismatch')
        usage = body['usage']
        require(usage == response['usage'] == terminal['final_response_usage'],
                task_name + ': final usage mismatch')
        numeric_usage = {k: v for k, v in usage.items()
                         if isinstance(v, (int, float)) and not isinstance(v, bool)}
        require(numeric_usage == terminal['usage'], task_name + ': cumulative usage mismatch')
        require(all(type(usage[k]) is int and usage[k] >= 0
                    for k in ('prompt_tokens', 'completion_tokens', 'total_tokens'))
                and usage['prompt_tokens'] + usage['completion_tokens'] == usage['total_tokens']
                and usage['completion_tokens'] <= 3000, task_name + ': invalid token accounting')
        require(terminal['response_id'] == body['id'] == response['request_id'],
                task_name + ': response ID mismatch')
        require(terminal['elapsed_s'] == response['elapsed_s'], task_name + ': elapsed time mismatch')
        receipt_files = {}
        for source_path in [requests[0], responses[0], terminal_path]:
            item = evidence_file('reused_evidence/' + task_name + '/' + source_path.name, source_path)
            evidence.append(item)
            receipt_files[source_path.name] = {'path': item[0], **item[2]}
        reuse_source = {
            'run_id': SMOKE.name, 'source_results_directory': str((SMOKE / 'results').relative_to(REPO)),
            'task_directory': task_name, 'source_input_sha256': sha(input_bytes),
            'original_prompt_hash': task['prompt_hash'], 'answer_sha256': sha(answer),
            'receipts': receipt_files,
            'verified': ['exact_task_and_messages', 'raw_body_matches_parsed_response',
                         'request_payload', 'status_ok_finish_stop', 'model_alias',
                         'temperature_0.3', 'thinking_false', 'max_tokens_3000_no_truncation',
                         'terminal_answer', 'token_usage', 'response_id'],
        }
        reused.append(dict(terminal, reused=True, reuse_source=reuse_source))
        audits.append({**{k: task[k] for k in KEY_FIELDS}, **reuse_source})
        totals.update(numeric_usage)
    require(Counter(row['split'] for row in reused) == {'primary': 5, 'secondary': 5},
            'Unexpected reuse split counts')
    return reused, audits, evidence, dict(totals), {
        'source_input_sha256': sha(input_bytes), 'source_manifest_sha256': sha(manifest_bytes),
        'source_identity_sha256': sha(identity_bytes), 'source_runner_sha256': identity['script_sha256'],
        'source_wait_adapter_sha256': identity['adapter_sha256'],
        'checked_model_identity': 'requested/returned alias deepseek_v4_flash; this offline check does not independently fingerprint server weights',
        'sampling_and_historical_length_policy_unchanged': True,
        'length_policy': '3000 initially; one increase to 6000 on truncation; all reused answers stopped normally at 3000',
        'execution_changes': {'workers': [2, 128], 'timeout_s': [900, 1800]},
    }


def prepare(output):
    manifest_bytes = (SOURCE / 'input_manifest.json').read_bytes()
    source_manifest = json.loads(manifest_bytes)
    require(source_manifest['source_commit'] == SOURCE_COMMIT, 'Wrong frozen source commit')
    for name, record in source_manifest['files'].items():
        require(file_sha(SOURCE / name) == record['sha256'], 'Frozen source hash mismatch: ' + name)
    audit_bytes = (SOURCE / 'judgment_leakage_audit.json').read_bytes()
    audit = json.loads(audit_bytes)
    review_bytes = (SOURCE / 'judgment_leakage_structural_review.json').read_bytes()
    review = json.loads(review_bytes)
    excluded = {row['case_id']: row for row in review['cases']}
    require(len(excluded) == 18 and set(excluded) == {row['case_id'] for row in audit['cases']},
            'The authorized 18-case exclusion list changed')
    require(Counter(row['split'] for row in excluded.values()) == {'primary': 2, 'secondary': 16},
            'Unexpected exclusion splits')
    messages, prompt_source_hash = frozen_messages()
    require(prompt_source_hash == source_manifest['analysis_messages_sha256'], 'Historical prompt changed')
    retained, excluded_tasks, all_keys, all_cases = {}, [], set(), set()
    retained_counts, all_counts = Counter(), Counter()
    with (SOURCE / 'input_tasks.jsonl').open(encoding='utf-8') as stream:
        for line in stream:
            require(line.strip(), 'Empty source task line')
            task = json.loads(line)
            require(set(task) == TASK_FIELDS and key(task) not in all_keys, 'Invalid source task schema/key')
            all_keys.add(key(task)); all_cases.add(task['case_id']); all_counts[task['case_id']] += 1
            require(sha(task['case_text_masked']) == task['masked_case_text_hash']
                    and sha(task['judge_decision_masked']) == task['judge_decision_hash']
                    and sha(task['question_text']) == task['question_hash'], 'Source string hash mismatch')
            require(messages(task['case_text_masked'], task['question_text']) == task['answer_messages']
                    and sha(canonical(task['answer_messages'])) == task['answer_prompt_hash'],
                    'Historical prompt/input reconstruction mismatch')
            if task['case_id'] in excluded:
                excluded_tasks.append(task)
                continue
            require(task['judge_decision_masked'] not in task['answer_messages'][1]['content'],
                    'Additional complete-judgment containment found; do not silently expand exclusions')
            retained[key(task)] = task
            retained_counts[task['case_id']] += 1
    require(len(all_keys) == 11185 and len(all_cases) == 2237 and set(all_counts.values()) == {5},
            'Source task/case totals changed')
    require(len(retained) == 11095 and len(retained_counts) == 2219
            and set(retained_counts.values()) == {5} and len(excluded_tasks) == 90,
            'Retained case/task counts mismatch')
    require(Counter(t['split'] for t in retained.values()) == {'primary': 1075, 'secondary': 10020},
            'Retained split counts mismatch')
    reused, reuse_audits, receipt_evidence, usage, reuse_contract = verify_reuse(retained)
    reused_keys = {key(t) for t in reused}
    pending = [network_task(task) for k, task in retained.items() if k not in reused_keys]
    require(len(pending) == 11085 and len({t['case_id'] for t in pending}) == 2217,
            'Network work count mismatch')
    require(Counter(t['split'] for t in pending) == {'primary': 1070, 'secondary': 10015},
            'Network split counts mismatch')
    require(len(reused_keys) == 10 and not (reused_keys & {key(t) for t in pending}),
            'Reuse/network tasks overlap')
    exclusion_records = []
    for cid, row in sorted(excluded.items()):
        question_ids = [t['question_id'] for t in excluded_tasks if t['case_id'] == cid]
        require(len(question_ids) == 5, 'Excluded case did not have five questions')
        exclusion_records.append({
            'split': row['split'], 'case_id': cid, 'question_ids': question_ids,
            'permanently_excluded': True, 'scope': 'formal answering release and its downstream experiment',
            'reason': 'Complete masked judgment is appended to the masked answer-input facts.',
            'classification': row['classification'], 'match_start': row['match_start'],
            'match_end_exclusive': row['match_end_exclusive'], 'match_length': row['match_length'],
            'evidence_path': str((SOURCE / 'judgment_leakage_structural_review.json').relative_to(REPO)),
            'evidence_sha256': sha(review_bytes), 'original_files_deleted': False,
        })
    output.mkdir(parents=True, exist_ok=True)
    require(not output.is_symlink(), 'Output directory must not be a symlink')
    files = {}
    files['task_mapping.jsonl'] = freeze_chunks(output / 'task_mapping.jsonl', jsonl_chunks(retained.values()))
    files['input_tasks.json'] = freeze_chunks(output / 'input_tasks.json', network_chunks(pending))
    files['reused_answers.jsonl'] = freeze_chunks(output / 'reused_answers.jsonl', jsonl_chunks(reused))
    files['excluded_cases.json'] = freeze_chunks(output / 'excluded_cases.json', [json_bytes(exclusion_records)])
    for relative, content, expected in receipt_evidence:
        target = output / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        files[relative] = freeze_chunks(target, [content])
        require(files[relative] == expected, 'Copied reuse receipt differs')
    manifest = {
        'run_id': CONFIG['run_id'], 'stage': 'formal_answering_only', 'state': 'prepared_not_started',
        'user_authorization': 'Exclude all 18 flagged dirty cases permanently; run all remaining answers at concurrency 128. This round does not run scoring.',
        'source_commit': SOURCE_COMMIT, 'source_release': SOURCE.name,
        'source_manifest_sha256': sha(manifest_bytes),
        'source_input_tasks_sha256': source_manifest['input_tasks_sha256'],
        'source_judgment_leakage_audit_sha256': sha(audit_bytes),
        'source_structural_review_sha256': sha(review_bytes),
        'source_cases': 2237, 'source_questions': 11185,
        'permanently_excluded_cases': 18, 'permanently_excluded_questions': 90,
        'exclusion_policy': 'Do not repair or reinclude these 18 cases in this experiment; retain historical files only as audit evidence.',
        'excluded_cases': exclusion_records,
        'retained_cases': 2219, 'retained_questions': 11095,
        'cases_by_split': {'primary': 215, 'secondary': 2004},
        'questions_by_split': {'primary': 1075, 'secondary': 10020},
        'reused_cases': 2, 'reused_answers': 10, 'reused_by_split': {'primary': 5, 'secondary': 5},
        'network_cases': 2217, 'network_answers': 11085,
        'network_questions_by_split': {'primary': 1070, 'secondary': 10015},
        'runtime_parameters': CONFIG, 'input_tasks_sha256': files['input_tasks.json']['sha256'],
        'task_mapping_sha256': files['task_mapping.jsonl']['sha256'],
        'reused_answers_sha256': files['reused_answers.jsonl']['sha256'],
        'mapping_fields': sorted(TASK_FIELDS), 'network_task_fields': sorted(NETWORK_FIELDS),
        'mapping_contract': 'All 11095 original full scientific tasks, unmodified, keyed by split/case_id/question_id; includes judge only for later scoring. Never send a mapping record as an API payload.',
        'api_payload_fields': ['model', 'messages', 'temperature', 'max_tokens', 'chat_template_kwargs'],
        'api_messages_contract': 'Exactly historical analysis_messages(masked facts, original question); no separate title/judgment field, no regenerated text.',
        'judge_decision_field_sent': False, 'prompt_source_sha256': prompt_source_hash,
        'source_text_questions_and_prompts_unchanged_for_retained_tasks': True,
        'reuse_contract': reuse_contract, 'reuse_verifications': reuse_audits,
        'reused_historical_usage': usage,
        'usage_note': 'Reused token usage is historical, not new consumption. Earlier timed-out smoke calls are not counted as zero-cost; this release does not rerun them.',
        'retry_policy': 'Historical 3000 -> 6000 once on truncation; <=3 HTTP tries per round and <=3 outer tries for error/empty, maximum 18 intents; do not retry simply to defeat refusal.',
        'interpretation_limit': 'An ok answer means normal generation, not legal correctness. Excluding the 18 exact suffix matches does not certify remaining cases free of partial or semantic leakage.',
        'api_calls_during_preparation': 0, 'ssh_calls_during_preparation': 0,
        'scoring_calls': 0, 'cross_judging_calls': 0, 'remasking_calls': 0, 'question_generation_calls': 0,
        'files': files,
    }
    freeze_chunks(output / 'input_manifest.json', [json_bytes(manifest)])
    run = '\n'.join([
        '# DeepSeek V4 Flash 正式回答：128 并发', '',
        '| 项目 | 数量 / 设置 |', '|---|---|',
        '| 原始冻结集 | 2,237 案 / 11,185 题 |',
        '| 永久排除脏数据 | 18 案 / 90 题；不修复、不回填 |',
        '| 正式保留 | 2,219 案 / 11,095 题 |',
        '| primary / secondary | 215 / 2,004 案 |',
        '| 已核验复用 | 2 案 / 10 条正常回答 |',
        '| 本次网络待跑 | 2,217 案 / 11,085 条回答 |',
        '| 模型 | deepseek_v4_flash |',
        '| 并发 / 客户端超时 | 128 / 1,800 秒 |',
        '| temperature / thinking | 0.3 / false |',
        '| 输出长度规则 | 初始 3,000；截断仅升至 6,000 一次 |',
        '| 本轮阶段 | 只回答，不评分、不互评 |',
        '| 准备时 API / SSH | 0 / 0 |', '',
        '## 文件与执行边界', '',
        '- `input_tasks.json`：只包含待跑的 11,085 题及固定运行配置；API 仅使用其中 messages 与采样参数。',
        '- `task_mapping.jsonl`：保留全部 11,095 题的原始业务映射，含事实、问题、裁判理由及哈希，供后续评分对齐。不得整体发送给回答 API。',
        '- `reused_answers.jsonl`：10 条已验证正常答案。其事实、问题、prompt、模型别名、温度、thinking、输出长度、原始请求/响应/答案和 token 均核对一致。',
        '- `reused_evidence/`：逐字保留旧 wait900 的请求、回执、终态及原 runner 身份证据。仅复用正常 stop 回答，不复用失败/截断或压测片段。',
        '- `excluded_cases.json`：18 案永久排除清单、90 个 question_id、原因及审计证据位置。旧数据未删除。',
        '- `input_manifest.json`：输入、映射、复用和证据哈希，以及各阶段计数。', '',
        '准备脚本：`scripts/cluster/v4_flash_20260927/prepare_answer_batch128.py`。',
        '正式 runner：`scripts/cluster/v4_flash_20260927/run_answer_batch128.py`。',
        '集群部署入口：`scripts/cluster/v4_flash_20260927/dispatch_answer_batch128.py`。',
        '输入准备本身不会调用模型、启动集群任务或进行评分；部署和执行状态应以 runner 的回执为准。', '',
        '10 条旧答案的采样与长度规则不变；本轮只把并发从 2 改为 128、客户端等待从 900 秒延长到 1,800 秒。离线复用核验确认模型请求/响应别名一致，不单独证明服务端权重指纹。', '',
        '18 案排除不等于其余案件已通过全面语义泄漏审查；正常生成也不等于法律正确。后续评分需另行运行，本轮不会启动。', '',
        '## 固定输入哈希', '',
        '- input_tasks.json：`' + files['input_tasks.json']['sha256'] + '`',
        '- task_mapping.jsonl：`' + files['task_mapping.jsonl']['sha256'] + '`',
        '- reused_answers.jsonl：`' + files['reused_answers.jsonl']['sha256'] + '`', '',
    ])
    freeze_chunks(output / 'RUN.md', [run.encode('utf-8')])
    print(json.dumps({'output': str(output), 'retained_cases': 2219, 'retained_questions': 11095,
                      'excluded_cases': 18, 'reused_answers': 10, 'network_answers': 11085,
                      'input_tasks_sha256': manifest['input_tasks_sha256'],
                      'task_mapping_sha256': manifest['task_mapping_sha256'],
                      'reused_answers_sha256': manifest['reused_answers_sha256'],
                      'api_calls': 0, 'ssh_calls': 0}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=OUT)
    args = parser.parse_args()
    require(args.output_dir.resolve() not in {SOURCE.resolve(), SMOKE.resolve()},
            'Refusing to write into an existing source release')
    prepare(args.output_dir)


if __name__ == '__main__':
    main()
