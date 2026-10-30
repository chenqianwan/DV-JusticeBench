#!/usr/bin/env python3
"""Freeze 11,095 completed answers for unchanged legacy self-scoring, offline.

No answering, scoring API, SSH, remasking or question generation is performed.
Existing scientific inputs, answers, receipts and previous scoring runs are read-only.
"""
import argparse
import ast
from collections import Counter
import json
import math
from pathlib import Path
from types import SimpleNamespace

from prepare_answer_smoke import canonical, sha, SOURCE_COMMIT
from prepare_full_ds_pipeline import frozen_bundle, freeze_chunks, file_sha, require


REPO = Path(__file__).resolve().parents[3]
ANSWER = REPO / 'data/pkulaw_v4_answer_batch128_20260928'
FINAL = ANSWER / 'final'
FULL = REPO / 'data/pkulaw_v4_full_pipeline_20260927'
OUT = REPO / 'data/pkulaw_v4_selfscore_batch128_20260928'
CONFIG = {
    'run_id': OUT.name, 'service_job_id': '402816',
    'base_url': 'http://dgx-040:18044/v1', 'model': 'deepseek_v4_flash',
    'thinking': True, 'temperature': .3, 'initial_max_tokens': 3000,
    'timeout_s': 1800, 'workers': 128,
}
RAW_FIELDS = {
    'split', 'case_id', 'question_id', 'question_text', 'source_case_text_hash',
    'masked_case_text_hash', 'question_hash', 'answer_prompt_hash',
    'case_text_masked', 'judge_decision_masked', 'judge_decision_hash',
}
TASK_FIELDS = RAW_FIELDS | {
    'answer', 'answer_hash', 'answer_status', 'answer_terminal_sha256',
    'answering_model', 'scoring_model', 'scoring_prompt_hash',
}
KEY_FIELDS = ('split', 'case_id', 'question_id')


def key(record):
    return tuple(record[name] for name in KEY_FIELDS)


def load(path):
    return json.loads(path.read_text(encoding='utf-8'))


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode('utf-8')


def rows(path):
    with path.open('rb') as stream:
        for raw in stream:
            require(raw.endswith(b'\n') and bool(raw.strip()), 'Invalid JSONL line: ' + str(path))
            yield json.loads(raw), raw[:-1]


def character_stats(lengths):
    ordered = sorted(lengths)
    require(bool(ordered), 'Cannot summarize empty lengths')
    return {'tasks': len(ordered), 'min': ordered[0],
            'p50': ordered[math.ceil(len(ordered) * .50) - 1],
            'p95': ordered[math.ceil(len(ordered) * .95) - 1], 'max': ordered[-1]}


def historical_capture(bundle_bytes):
    namespace = {}
    # frozen_bundle() already verifies these pure definitions against fixed source AST.
    exec(compile(bundle_bytes, 'legacy_scoring_bundle.py', 'exec'), namespace)
    bundle = SimpleNamespace(**namespace)
    path = Path(__file__).with_name('run_selfscore_smoke.py')
    source = path.read_text(encoding='utf-8')
    tree = ast.parse(source)
    selected = [node for node in tree.body
                if (isinstance(node, ast.ClassDef) and node.name == 'Capture')
                or (isinstance(node, ast.FunctionDef) and node.name == 'evaluate')]
    require(len(selected) == 2, 'Cannot find the original offline scoring adapter')
    capture_namespace = {}
    # Extract only Capture/evaluate, not the module imports, HTTP class or runner.
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), capture_namespace)
    return bundle, capture_namespace['Capture'], capture_namespace['evaluate'], {
        node.name + '_source_sha256': sha(ast.get_source_segment(source, node)) for node in selected
    }


def input_chunks(tasks, bundle_hash, answers_hash):
    top = dict(CONFIG, bundle_sha256=bundle_hash, upstream_answers_sha256=answers_hash)
    yield (canonical(top)[:-1] + ',"tasks":[').encode('utf-8')
    for index, task in enumerate(tasks):
        yield ((',' if index else '') + canonical(task)).encode('utf-8')
    yield b']}\n'


def prepare(output):
    answer_manifest_bytes = (ANSWER / 'input_manifest.json').read_bytes()
    answer_manifest = json.loads(answer_manifest_bytes)
    audit_bytes = (FINAL / 'audit_summary.json').read_bytes()
    audit = json.loads(audit_bytes)
    require(audit['audit_status'] == 'passed' and audit['cases'] == 2219 and audit['answers'] == 11095
            and audit['final_status_counts'] == {'ok': 11095}
            and audit['final_finish_reason_counts'] == {'stop': 11095}
            and audit['scoring_performed'] is False, 'Final answer audit is not a completed answer-only release')
    require(sha(answer_manifest_bytes) == audit['source_evidence']['input_manifest.json']['sha256'],
            'Answer preparation manifest differs from final audit')
    for name, record in audit['files'].items():
        require(file_sha(FINAL / name) == record['sha256']
                and (FINAL / name).stat().st_size == record['bytes'], 'Audited answer file changed: ' + name)
    for name in ('task_mapping.jsonl', 'excluded_cases.json', 'reused_answers.jsonl'):
        require(file_sha(ANSWER / name) == answer_manifest['files'][name]['sha256']
                == audit['source_evidence'][name]['sha256'], 'Answer mapping/exclusion/reuse hash differs')
    excluded_bytes = (ANSWER / 'excluded_cases.json').read_bytes()
    excluded = json.loads(excluded_bytes)
    excluded_ids = {row['case_id'] for row in excluded}
    require(len(excluded) == len(excluded_ids) == 18
            and sum(len(row['question_ids']) for row in excluded) == 90
            and all(row['permanently_excluded'] is True for row in excluded), 'Permanent exclusion list changed')
    mappings = {}
    for record, _ in rows(ANSWER / 'task_mapping.jsonl'):
        require(key(record) not in mappings, 'Duplicate business mapping')
        require(record['case_id'] not in excluded_ids, 'Dirty case entered scoring mapping')
        mappings[key(record)] = record
    require(len(mappings) == 11095 and len({k[1] for k in mappings}) == 2219
            and set(Counter(k[:2] for k in mappings).values()) == {5}, 'Incorrect case/question mapping size')
    bundle_bytes, _, code_hashes = frozen_bundle()
    require(bundle_bytes == (FULL / 'legacy_scoring_bundle.py').read_bytes()
            and sha(bundle_bytes) == load(FULL / 'input_manifest.json')['bundle_sha256'],
            'Scoring bundle differs from the frozen full release')
    bundle, Capture, evaluate, adapter_hashes = historical_capture(bundle_bytes)
    answers_hash = audit['files']['answers_deepseek_v4_flash_all.jsonl']['sha256']
    tasks, provenance, seen, origins = [], [], set(), Counter()
    message_lengths, inner_lengths, judgment_lengths = [], [], []
    for answer_record, original_line in rows(FINAL / 'answers_deepseek_v4_flash_all.jsonl'):
        task_key = key(answer_record)
        require(task_key in mappings and task_key not in seen, 'Duplicate/unmapped final answer')
        seen.add(task_key)
        mapping = mappings[task_key]
        require(answer_record['status'] == 'ok' and answer_record['finish_reason'] == 'stop'
                and isinstance(answer_record['answer'], str) and answer_record['answer'].strip(),
                'Only complete frozen answers may be scored')
        require(all(answer_record[field] == CONFIG['model'] for field in
                    ('model', 'requested_model', 'response_model'))
                and answer_record['temperature'] == .3 and answer_record['thinking'] is False,
                'Answer model/sampling differs')
        for field in ('split', 'case_id', 'question_id', 'question_text', 'source_case_text_hash',
                      'masked_case_text_hash', 'question_hash'):
            require(answer_record[field] == mapping[field], 'Answer/mapping provenance differs: ' + field)
        require(answer_record['prompt_hash'] == mapping['answer_prompt_hash'], 'Original answer prompt hash differs')
        for text, hashed in (('case_text_masked', 'masked_case_text_hash'),
                             ('judge_decision_masked', 'judge_decision_hash'), ('question_text', 'question_hash')):
            require(isinstance(mapping[text], str) and mapping[text].strip()
                    and sha(mapping[text]) == mapping[hashed], 'Source text/hash mismatch: ' + text)
        require(bundle.analysis_messages(mapping['case_text_masked'], mapping['question_text'])
                == mapping['answer_messages']
                and sha(canonical(mapping['answer_messages'])) == mapping['answer_prompt_hash'],
                'Historical answer prompt reconstruction differs')
        origin = answer_record['answer_origin']
        if origin == 'formal_batch128':
            receipt = answer_record['audit_evidence']['terminal']
            require(answer_record['reused'] is False, 'Network answer marked reused')
        elif origin == 'reused_wait900':
            receipt = answer_record['reuse_source']['receipts']['terminal.json']
            require(answer_record['reused'] is True, 'Historical answer origin mismatch')
        else:
            raise ValueError('Unknown answer provenance')
        terminal_path = ANSWER / receipt['path']
        require(terminal_path.resolve().is_relative_to(ANSWER.resolve()), 'Terminal path escapes answer release')
        terminal_bytes = terminal_path.read_bytes()
        require(sha(terminal_bytes) == receipt['sha256'] and len(terminal_bytes) == receipt['bytes'],
                'Original answer terminal hash differs')
        terminal = json.loads(terminal_bytes)
        require(key(terminal) == task_key and terminal['answer'] == answer_record['answer']
                and terminal['status'] == 'ok' and terminal['finish_reason'] == 'stop'
                and terminal['prompt_hash'] == mapping['answer_prompt_hash'], 'Original answer terminal differs')
        task = {field: mapping[field] for field in RAW_FIELDS}
        task.update(answer=answer_record['answer'], answer_hash=sha(answer_record['answer']),
                    answer_status='ok', answer_terminal_sha256=sha(terminal_bytes),
                    answering_model=CONFIG['model'], scoring_model=CONFIG['model'])
        # This adapter only captures the original evaluator's prompt; it never calls a service.
        # Its empty response is discarded and is not treated as a measured score.
        capture = Capture()
        evaluator, _ = evaluate(bundle, capture, task, task['answer'])
        inner = capture.prompts[0]
        messages = bundle.analysis_messages(inner, None)
        task['scoring_prompt_hash'] = sha(canonical(messages))
        require(set(task) == TASK_FIELDS and len(task) == 18, 'V18 scoring task schema changed')
        require(task['answer'] in inner and task['judge_decision_masked'] in inner
                and task['question_text'] in inner and task['case_text_masked'][:2000] in inner,
                'Historical prompt did not retain the required full answer/judgment and reference facts')
        require(evaluator.flag_parser_version == 'legacy' and capture.provider == 'deepseek',
                'Historical scorer parser/provider changed')
        tasks.append(task)
        origins[origin] += 1
        message_lengths.append(sum(len(message['content']) for message in messages))
        inner_lengths.append(len(inner)); judgment_lengths.append(len(task['judge_decision_masked']))
        provenance.append({**{field: task[field] for field in KEY_FIELDS},
                           'answer_origin': origin, 'answer_hash': task['answer_hash'],
                           'answer_record_sha256': sha(original_line),
                           'answer_terminal_path': str(terminal_path.relative_to(REPO)),
                           'answer_terminal_sha256': sha(terminal_bytes),
                           'scoring_prompt_hash': task['scoring_prompt_hash'],
                           'scoring_messages_characters': message_lengths[-1],
                           'inner_prompt_characters': len(inner)})
    require(seen == mappings.keys() and origins == {'formal_batch128': 11085, 'reused_wait900': 10},
            'Final answers do not cover all retained cases exactly once')
    require(Counter(task['split'] for task in tasks) == {'primary': 1075, 'secondary': 10020},
            'Unexpected scoring split counts')
    # Only schedule order changes: first formal acceptance tasks cover both splits.
    first_primary = next(task for task in tasks if task['split'] == 'primary')
    first_secondary = next(task for task in tasks if task['split'] == 'secondary')
    first_keys = {key(first_primary), key(first_secondary)}
    tasks = [first_primary, first_secondary] + [task for task in tasks if key(task) not in first_keys]
    require(len(tasks) == len({key(task) for task in tasks}) == 11095, 'Reordering changed task coverage')
    output.mkdir(parents=True, exist_ok=True)
    require(not output.is_symlink(), 'Output directory cannot be a symlink')
    files = {}
    files['legacy_scoring_bundle.py'] = freeze_chunks(output / 'legacy_scoring_bundle.py', [bundle_bytes])
    files['excluded_cases.json'] = freeze_chunks(output / 'excluded_cases.json', [excluded_bytes])
    files['answer_provenance.jsonl'] = freeze_chunks(output / 'answer_provenance.jsonl',
        ((canonical(record) + '\n').encode('utf-8') for record in provenance))
    files['input_scoring.json'] = freeze_chunks(output / 'input_scoring.json',
        input_chunks(tasks, sha(bundle_bytes), answers_hash))
    lengths = {'scoring_messages_characters': character_stats(message_lengths),
               'inner_prompt_characters': character_stats(inner_lengths),
               'full_masked_judgment_characters': character_stats(judgment_lengths)}
    manifest = {
        'run_id': CONFIG['run_id'], 'schema': 'V18', 'stage': 'full_self_scoring_only', 'state': 'prepared_not_started',
        'cases': 2219, 'questions': 11095, 'planned_scoring_tasks': 11095,
        'cases_by_split': {'primary': 215, 'secondary': 2004},
        'questions_by_split': {'primary': 1075, 'secondary': 10020},
        'answer_sources': dict(origins), 'answers_all_verified_ok_stop': True,
        'permanently_excluded_cases': 18, 'permanently_excluded_questions': 90,
        'excluded_case_ids': sorted(excluded_ids), 'excluded_case_overlap': 0,
        'source_commit': SOURCE_COMMIT, 'source_answer_release': ANSWER.name,
        'upstream_answers_sha256': answers_hash, 'upstream_answer_audit_sha256': sha(audit_bytes),
        'upstream_answer_manifest_sha256': sha(answer_manifest_bytes),
        'upstream_mapping_sha256': answer_manifest['task_mapping_sha256'],
        'bundle_sha256': sha(bundle_bytes), 'input_scoring_sha256': files['input_scoring.json']['sha256'],
        **code_hashes, **adapter_hashes,
        'evaluator_source': 'utils/evaluator.py:AnswerEvaluator',
        'outer_prompt_source': 'scripts/run_experiment1_smoke_xhub.py:analysis_messages',
        'bundle_identical_to_historical_frozen_bundle': True,
        'flag_parser_version': 'legacy', 'prompt_and_penalty_implementation_unchanged': True,
        'answering_model': CONFIG['model'], 'scoring_model': CONFIG['model'],
        'relationship': 'DeepSeek V4 Flash judges its own frozen answers; self-scoring, not cross-model mutual evaluation.',
        'runtime_parameters': CONFIG, 'task_fields': sorted(TASK_FIELDS),
        'answer_terminal_sha256_definition': 'SHA-256 of original terminal.json complete file bytes, not merged JSONL row.',
        'scoring_prompt_hash_definition': 'SHA-256 UTF-8 canonical JSON of original analysis_messages(original evaluator inner prompt, None); sort_keys=True, ensure_ascii=False, separators=(comma,colon).',
        'answer_record_sha256_definition': 'Sidecar only: SHA-256 of final merged JSONL original row bytes with one final LF removed.',
        'messages_contract': 'Runner reconstructs each prompt with the same frozen bundle, requires exact scoring_prompt_hash equality, and saves full messages before dispatch.',
        'scoring_uses_full_answer': True, 'scoring_uses_full_masked_judgment': True,
        'scoring_reference_case_chars': 2000, 'new_context_truncation': False,
        'character_lengths': lengths,
        'character_length_note': 'Python Unicode characters, not tokens or a context-window feasibility guarantee; prompt prefix/system text included in messages statistics.',
        'task_order': 'First original primary question, then first original secondary question, then remaining tasks in original business-mapping order.',
        'first_acceptance_tasks': [{field: task[field] for field in KEY_FIELDS} for task in tasks[:2]],
        'acceptance_mode': 'Runner may use --max-new-tasks=2 on this same complete frozen input, then --resume without regenerating scores or answers.',
        'retry_policy': 'Scoring only: initial max_tokens3000, one extension to6000 on truncation; original bounded error/empty retries. No answering retries or regeneration.',
        'failure_policy': 'Keep raw receipts and operational status; structured refusal, truncation, interface/parse failures are not valid zero scores. Legacy numeric parsing and deductions remain unchanged.',
        'old_smoke_scores_reused': 0, 'questions_regenerated': 0, 'answers_regenerated': 0,
        'masking_calls': 0, 'question_generation_calls': 0, 'answer_generation_calls': 0,
        'scoring_api_calls_during_preparation': 0, 'ssh_calls': 0, 'files': files,
    }
    freeze_chunks(output / 'input_manifest.json', [json_bytes(manifest)])
    message_stats = lengths['scoring_messages_characters']
    run = '\n'.join([
        '# DeepSeek V4 Flash 全量自评分', '',
        '| 项目 | 设置 |', '|---|---|',
        '| 评分范围 | 2,219 案 / 11,095 条已完成回答 |',
        '| primary / secondary | 215 / 2,004 案；1,075 / 10,020 条 |',
        '| 答案来源 | 本次完成 11,085 条 + 已核验历史回答 10 条 |',
        '| 永久排除 | 18 个脏案 / 90 题，不修复、不回填 |',
        '| 评分关系 | DeepSeek V4 Flash 评价自己的回答；不是多模型互评 |',
        '| 并发 / 客户端等待 | 128 / 1,800 秒 |',
        '| temperature / thinking | 0.3 / true |',
        '| 输出上限 | 3,000；截断仅补长一次至 6,000 |',
        '| 本轮重新回答 / 重新出题 / 脱敏 | 均不执行 |',
        '| 旧冒烟评分复用 | 0，直接对最终全集准备评分 |',
        '| 准备时 API / SSH | 0 / 0 |', '',
        '## 原评分流程的复原', '',
        '原 `AnswerEvaluator` 和外层 `analysis_messages` 从固定提交逐 AST 核验并原样冻结。量表、五维评分、legacy 解析、错误标记和扣分逻辑不改。',
        '评分输入包含完整冻结回答、原问题、完整脱敏裁判理由；事实参考仍按原实现取前 2,000 字。没有为适配上下文额外截断裁判理由或答案。',
        'prepare 离线构造每条原评分 prompt 并保存哈希；runner 必须按同一 bundle 重建后逐条核对，才能调用模型。', '',
        '| 评分 messages 字符数 | 数量 |', '|---|---:|',
        '| P50 | ' + str(message_stats['p50']) + ' |',
        '| P95 | ' + str(message_stats['p95']) + ' |',
        '| 最大 | ' + str(message_stats['max']) + ' |',
        '字符数不是 token 数，也不等于已通过服务端上下文上限检查。', '',
        '## 文件和运行', '',
        '- `input_scoring.json`：冻结 V18 任务、完整答案和原参考字段；共 11,095 题。',
        '- `legacy_scoring_bundle.py`：原评分实现与原外层 prompt，字节一致复用。',
        '- `input_manifest.json`：源答案、映射、评分输入和实现哈希；参数及复原说明。',
        '- `answer_provenance.jsonl`：每题原 terminal、最终答案行及评分 prompt 哈希。',
        '- `excluded_cases.json`：沿用永久排除清单。', '',
        '准备入口：`scripts/cluster/v4_flash_20260927/prepare_selfscore_batch128.py`。',
        '运行入口：`scripts/cluster/v4_flash_20260927/run_selfscore_batch128.py`。',
        '可先用同一全量输入的 `--max-new-tasks=2` 验收 primary 首题与 secondary 首题，再 `--resume` 完成其余任务；不复用或触发旧 10 题评分脚本的执行入口。', '',
        '本目录创建仅代表输入准备完成，不代表评分已运行。拒答、截断、接口或解析失败须保留独立状态，不应作为有效零分；评分结果也不是专家正确性认定。', '',
        '输入 SHA-256：`' + files['input_scoring.json']['sha256'] + '`',
        '评分 bundle SHA-256：`' + sha(bundle_bytes) + '`', '',
    ])
    freeze_chunks(output / 'RUN.md', [run.encode('utf-8')])
    print(json.dumps({'output': str(output), 'schema': 'V18', 'cases': 2219, 'questions': 11095,
                      'input_scoring_sha256': manifest['input_scoring_sha256'],
                      'input_bytes': files['input_scoring.json']['bytes'], 'bundle_sha256': sha(bundle_bytes),
                      'input_manifest_sha256': file_sha(output / 'input_manifest.json'),
                      'character_lengths': lengths, 'api_calls': 0, 'ssh_calls': 0}, ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=OUT)
    args = parser.parse_args()
    require(args.output_dir.resolve() not in {ANSWER.resolve(), FINAL.resolve(), FULL.resolve()},
            'Cannot write into a frozen source release')
    prepare(args.output_dir)


if __name__ == '__main__':
    main()
