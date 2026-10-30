#!/usr/bin/env python3
"""Freeze all clean scientific inputs for answering then self-scoring, offline.

No API, SSH, model loading, remasking, question generation or inference occurs.
Runtime concurrency and service routing are deliberately not scientific inputs.
"""
import argparse
import ast
from collections import Counter
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile

from prepare_answer_smoke import SOURCE_COMMIT, canonical, sha
from prepare_selfscore_smoke import extract


REPO = Path(__file__).resolve().parents[3]
RELEASE = REPO / 'data/pkulaw_clean_release_20260925'
SMOKE = REPO / 'data/pkulaw_v4_selfscore_smoke_20260927'
OUT = REPO / 'data/pkulaw_v4_full_pipeline_20260927'
SPLITS = {'primary': 217, 'secondary': 2020}
TASK_FIELDS = {
    'split', 'case_id', 'question_id', 'question_text', 'case_text_masked',
    'judge_decision_masked', 'source_case_text_hash', 'masked_case_text_hash',
    'question_hash', 'judge_decision_hash', 'answer_messages', 'answer_prompt_hash',
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def freeze_chunks(path, chunks):
    """Publish a complete file once; identical reruns never overwrite it."""
    require(not path.is_symlink(), 'Output cannot be a symlink: ' + str(path))
    fd, temporary = tempfile.mkstemp(prefix='.' + path.name + '.', dir=path.parent)
    digest, size = hashlib.sha256(), 0
    try:
        with os.fdopen(fd, 'wb') as stream:
            for chunk in chunks:
                require(isinstance(chunk, bytes), 'Frozen chunks must be bytes')
                stream.write(chunk)
                digest.update(chunk)
                size += len(chunk)
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            require(path.is_file() and path.stat().st_size == size
                    and file_sha(path) == digest.hexdigest(),
                    'Refusing to replace different frozen artifact: ' + str(path))
        else:
            os.link(temporary, path)
            dirfd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(dirfd)
            finally:
                os.close(dirfd)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {'sha256': digest.hexdigest(), 'bytes': size}


def frozen_bundle():
    evaluator = extract('utils/evaluator.py', 'AnswerEvaluator', ast.ClassDef)
    messages = extract('scripts/run_experiment1_smoke_xhub.py',
                       'analysis_messages', ast.FunctionDef)
    expected = ('# Generated verbatim from audited historical AST nodes; no network imports.\n'
                '# Source commit: ' + SOURCE_COMMIT + '\n'
                'from typing import Dict, List, Optional\nimport re\nai_api = None\n\n'
                + messages + '\n\n' + evaluator + '\n').encode('utf-8')
    existing = (SMOKE / 'legacy_scoring_bundle.py').read_bytes()
    smoke_manifest = json.loads((SMOKE / 'input_manifest.json').read_text())
    require(existing == expected, 'Existing scoring bundle differs from verified AST sources')
    require(sha(existing) == smoke_manifest['bundle_sha256'], 'Smoke bundle hash mismatch')
    namespace = {}
    # Only identical, audited pure definitions and standard-library imports.
    exec(compile(existing, 'legacy_scoring_bundle.py', 'exec'), namespace)
    require(namespace['AnswerEvaluator'](api=object(), flag_parser_version='legacy')
            .flag_parser_version == 'legacy', 'Legacy parser binding failed')
    return existing, namespace['analysis_messages'], {
        'evaluator_class_sha256': sha(evaluator),
        'analysis_messages_sha256': sha(messages),
        'source_smoke_bundle_sha256': sha(existing),
    }


def load_split(split, expected, release_manifest):
    tables, hashes = {}, {}
    for stem in ('masked_cases_fix4', 'questions_fix4'):
        name = split + '/' + stem + '.jsonl'
        content = (RELEASE / name).read_bytes()
        digest = sha(content)
        require(digest == release_manifest['files'][name]['sha256'], name + ': checksum mismatch')
        require(content.endswith(b'\n'), name + ': missing terminal newline')
        lines = content.splitlines()
        require(all(line.strip() for line in lines), name + ': blank JSONL line')
        rows = [json.loads(line) for line in lines]
        require(all(isinstance(row, dict) and isinstance(row.get('case_id'), str)
                    and row['case_id'] for row in rows), name + ': invalid record identifiers')
        table = {row['case_id']: row for row in rows}
        require(len(rows) == len(table) == expected, name + ': duplicate ID or unexpected count')
        tables[stem] = table
        hashes[name] = digest
    cases, questions = tables['masked_cases_fix4'], tables['questions_fix4']
    require(cases.keys() == questions.keys(), split + ': unmatched case/question IDs')
    return cases, questions, hashes


def character_stats(values):
    ordered = sorted(values)
    require(bool(ordered), 'Cannot summarize empty character lengths')
    def percentile(p):
        return ordered[max(0, math.ceil(p * len(ordered)) - 1)]
    return {'case_count': len(ordered), 'min': ordered[0],
            'p50': percentile(0.50), 'p95': percentile(0.95), 'max': ordered[-1]}


def prepare(output):
    source_manifest_bytes = (RELEASE / 'manifest.json').read_bytes()
    source_manifest = json.loads(source_manifest_bytes)
    require(source_manifest['source_commit'] == SOURCE_COMMIT, 'Unexpected clean source commit')
    require(source_manifest['retained_cases'] == 2237
            and source_manifest['retained_questions'] == 11185
            and source_manifest['retained_by_delivery_split'] == SPLITS,
            'Unexpected clean release counts')
    bundle, messages, code_hashes = frozen_bundle()
    tasks, all_cases, all_questions = [], set(), set()
    file_hashes, text_lengths, judge_lengths, split_stats, large_judgments = {}, [], [], {}, []
    leakage = {}
    original_name = 'data/pkulaw_2023_2025_release/pipeline_ready_cases.json'
    original_bytes = (REPO / original_name).read_bytes()
    require(sha(original_bytes) == source_manifest['inputs'][original_name]['sha256'],
            'Original full-source checksum mismatch during leakage audit')
    original_cases = json.loads(original_bytes)
    for split, expected in SPLITS.items():
        cases, questions, hashes = load_split(split, expected, source_manifest)
        file_hashes.update(hashes)
        require(not (all_cases & cases.keys()), 'Case ID appears in both splits')
        all_cases.update(cases)
        split_text, split_judge = [], []
        for case_id in sorted(cases):
            case = cases[case_id]
            text, judge = case.get('case_text_masked'), case.get('judge_decision_masked')
            require(isinstance(text, str) and text.strip(), case_id + ': empty masked facts')
            require(isinstance(judge, str) and judge.strip(), case_id + ': empty masked judgment')
            source_hash = case.get('source_case_text_hash')
            require(isinstance(source_hash, str) and len(source_hash) == 64
                    and set(source_hash) <= set('0123456789abcdef'),
                    case_id + ': invalid source facts hash')
            five = questions[case_id].get('questions')
            require(isinstance(five, list) and len(five) == 5, case_id + ': expected five questions')
            require(all(isinstance(q, dict) and isinstance(q.get('question_id'), str)
                        and q['question_id'] and isinstance(q.get('question'), str)
                        and q['question'].strip() for q in five), case_id + ': invalid question')
            require(len({q['question_id'] for q in five}) == 5,
                    case_id + ': duplicate question ID')
            require(len({q['question'] for q in five}) == 5,
                    case_id + ': duplicate question text')
            split_text.append(len(text))
            split_judge.append(len(judge))
            if len(judge) > 25000:
                large_judgments.append({'split': split, 'case_id': case_id,
                                        'case_text_chars': len(text), 'judge_decision_chars': len(judge),
                                        'questions': 5, 'action': 'retained_unmodified'})
            for question in five:
                question_id, question_text = question['question_id'], question['question']
                require(question_id not in all_questions, 'Duplicate global question ID: ' + question_id)
                all_questions.add(question_id)
                answer_messages = messages(text, question_text)
                # Exact matching is an audit warning, never an unapproved text repair.
                # The inherited masked facts themselves may contain the judgment.
                if judge in answer_messages[1]['content']:
                    if case_id not in leakage:
                        original = original_cases[case_id]
                        original_text = original['case_text']
                        original_judge = original['judge_decision']
                        leakage[case_id] = {
                            'split': split, 'case_id': case_id,
                            'match_chars': len(judge), 'case_text_masked_chars': len(text),
                            'judge_decision_masked_chars': len(judge),
                            'full_masked_judgment_in_masked_facts': judge in text,
                            'masked_facts_match_offset': text.find(judge),
                            'masked_facts_match_count': text.count(judge),
                            'full_original_judgment_in_original_facts':
                                bool(original_judge) and original_judge in original_text,
                            'original_facts_match_offset': original_text.find(original_judge)
                                if original_judge else None,
                            'original_case_text_chars': len(original_text),
                            'original_judge_decision_chars': len(original_judge),
                            'original_case_text_hash': sha(original_text),
                            'original_judge_decision_hash': sha(original_judge),
                            'masked_case_text_hash': sha(text), 'judge_decision_hash': sha(judge),
                            'affected_question_ids': [], 'judgment_in_question_ids': [],
                            'action': 'retain_all_inputs_verbatim; exclude_from_pressure_test_selection; formal_execution_pending_review',
                        }
                    leakage[case_id]['affected_question_ids'].append(question_id)
                    if judge in question_text:
                        leakage[case_id]['judgment_in_question_ids'].append(question_id)
                task = {
                    'split': split, 'case_id': case_id, 'question_id': question_id,
                    'question_text': question_text, 'case_text_masked': text,
                    'judge_decision_masked': judge, 'source_case_text_hash': source_hash,
                    'masked_case_text_hash': sha(text), 'question_hash': sha(question_text),
                    'judge_decision_hash': sha(judge), 'answer_messages': answer_messages,
                    'answer_prompt_hash': sha(canonical(answer_messages)),
                }
                require(set(task) == TASK_FIELDS, 'Task schema changed')
                tasks.append(task)
        split_stats[split] = {'case_text_chars': character_stats(split_text),
                              'judge_decision_chars': character_stats(split_judge)}
        text_lengths.extend(split_text)
        judge_lengths.extend(split_judge)
    require(len(all_cases) == 2237 and len(tasks) == len(all_questions) == 11185,
            'Final case/question counts mismatch')
    require(Counter(t['split'] for t in tasks) == {'primary': 1085, 'secondary': 10100},
            'Final split task counts mismatch')
    output.mkdir(parents=True, exist_ok=True)
    require(not output.is_symlink(), 'Output directory cannot be a symlink')
    bundle_artifact = freeze_chunks(output / 'legacy_scoring_bundle.py', [bundle])
    task_artifact = freeze_chunks(output / 'input_tasks.jsonl',
                                 ((canonical(task) + '\n').encode('utf-8') for task in tasks))
    leakage_cases = sorted(leakage.values(), key=lambda item: (item['split'], item['case_id']))
    leakage_audit = {
        'kind': 'potential_judgment_leakage_exact_match_audit',
        'source_release': RELEASE.name, 'source_commit': SOURCE_COMMIT,
        'source_file_hashes': file_hashes,
        'original_source': original_name, 'original_source_sha256': sha(original_bytes),
        'audited_cases': len(all_cases), 'audited_questions': len(tasks),
        'flagged_cases': len(leakage_cases),
        'flagged_questions': sum(len(item['affected_question_ids']) for item in leakage_cases),
        'flagged_by_split': dict(Counter(item['split'] for item in leakage_cases)),
        'method': 'Exact complete masked judgment substring search in the unchanged historical answer messages; separately compare the complete original judgment against original facts.',
        'comparison_limit': 'Before/after checks use each version of the full judgment independently. They establish text containment, not the precise causal mechanism or legal-semantic leakage severity.',
        'interpretation': 'These masked answer inputs already contain the complete masked judgment. The prompt builder did not append a judgment field. Original full-judgment absence does not prove absence of every prior judgment excerpt.',
        'coverage_limit': 'Does not detect paraphrased or partial judgment leakage; unflagged cases are not certified leakage-free.',
        'cases': leakage_cases,
        'policy': 'Preserve all 2237 cases and 11185 questions unchanged. Do not auto-exclude, truncate, remask or regenerate. Pressure tests must avoid flagged cases and are not formal answers; formal execution requires resolving this input warning.',
        'api_calls': 0,
    }
    leakage_artifact = freeze_chunks(output / 'judgment_leakage_audit.json',
        [(json.dumps(leakage_audit, ensure_ascii=False, indent=2) + '\n').encode('utf-8')])
    manifest = {
        'run_id': 'pkulaw_v4_full_pipeline_20260927',
        'stage': 'frozen_full_clean_inputs_for_answering_then_self_scoring',
        'state': 'inputs_frozen_not_executed', 'api_calls': 0,
        'source_commit': SOURCE_COMMIT, 'source_release': RELEASE.name,
        'release_manifest_sha256': sha(source_manifest_bytes), 'source_file_hashes': file_hashes,
        'planned_cases': 2237, 'planned_answers': 11185, 'planned_scores': 11185,
        'cases_by_split': SPLITS, 'questions_by_split': {'primary': 1085, 'secondary': 10100},
        'questions_per_case': 5, 'task_fields': sorted(TASK_FIELDS),
        'task_order': 'primary then secondary; case_id ascending; original question array order',
        'input_tasks_sha256': task_artifact['sha256'],
        'bundle_sha256': bundle_artifact['sha256'],
        'files': {'input_tasks.jsonl': task_artifact, 'legacy_scoring_bundle.py': bundle_artifact,
                  'judgment_leakage_audit.json': leakage_artifact},
        **code_hashes,
        'canonical_json': 'UTF-8; ensure_ascii=False; sort_keys=True; separators=(comma,colon)',
        'answer_prompt_source': 'scripts/run_experiment1_smoke_xhub.py:analysis_messages',
        'scoring_source': 'utils/evaluator.py:AnswerEvaluator', 'flag_parser_version': 'legacy',
        'prompt_and_penalty_implementation_unchanged': True,
        'source_text_and_questions_unchanged': True, 'bundle_byte_identical_to_smoke': True,
        'answer_input_fields': ['case_text_masked', 'question_text'],
        'judge_decision_field_sent_to_answering': False, 'title_field_added_to_answering': False,
        'scoring_uses_full_masked_judgment': True, 'scoring_case_chars': 2000,
        'remasking_calls': 0, 'question_generation_calls': 0, 'cross_judging_calls': 0,
        'execution_settings': 'Concurrency, service routing and scheduling must be frozen separately before execution.',
        'authorization_note': 'User authorized all 217 primary and 2020 secondary delivery cases, answering then DeepSeek self-scoring; this preparation performs no inference.',
        'existing_smoke_policy': 'All scientific tasks are retained. Downstream may reuse verified matching smoke results; this script neither loads nor overwrites result files.',
        'grouping_note': 'Delivery primary contains 194 original primary labels and 23 secondary labels; it is not a relabeling.',
        'quality_limit': 'Structurally clean release; no claim of full expert semantic validation.',
        'potential_judgment_leakage': {
            'case_count': len(leakage_cases),
            'question_count': leakage_audit['flagged_questions'],
            'cases_by_split': leakage_audit['flagged_by_split'],
            'cases': [{key: item[key] for key in ('split', 'case_id', 'match_chars',
                       'full_masked_judgment_in_masked_facts',
                       'full_original_judgment_in_original_facts')}
                      for item in leakage_cases],
            'audit_file': 'judgment_leakage_audit.json',
            'audit_sha256': leakage_artifact['sha256'],
            'truncated_or_excluded': False,
            'execution_gate': 'Formal run pending resolution of the input audit warning; pressure tests avoid these cases and do not constitute formal answers.',
        },
        'character_stats': {'unit': 'Python len(str), per case not repeated per question',
                            'percentile_method': 'nearest_rank_ceil_p_times_n',
                            'all': {'case_text_chars': character_stats(text_lengths),
                                    'judge_decision_chars': character_stats(judge_lengths)},
                            'by_split': split_stats},
        'potential_large_context': {
            'rule': 'len(judge_decision_masked) > 25000 characters',
            'case_count': len(large_judgments), 'question_count': len(large_judgments) * 5,
            'cases': large_judgments, 'truncated_or_excluded': False,
            'note': 'Character heuristic only, not tokenization; retain full judgment and audit any actual context failure.',
        },
    }
    manifest_data = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    freeze_chunks(output / 'input_manifest.json', [manifest_data])
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, default=OUT)
    args = parser.parse_args()
    manifest = prepare(args.output_dir)
    print(json.dumps({'output': str(args.output_dir.resolve()), 'cases': manifest['planned_cases'],
                      'tasks': manifest['planned_answers'], 'api_calls': 0,
                      'input_tasks_sha256': manifest['input_tasks_sha256'],
                      'bundle_sha256': manifest['bundle_sha256'],
                      'character_stats': manifest['character_stats'],
                      'large_judgment_cases': manifest['potential_large_context']['case_count'],
                      'potential_judgment_leakage_cases': manifest['potential_judgment_leakage']['case_count']},
                     ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
