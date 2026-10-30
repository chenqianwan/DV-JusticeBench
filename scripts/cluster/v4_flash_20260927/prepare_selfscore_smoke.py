#!/usr/bin/env python3
"""Freeze unchanged historical evaluation code and references for ten saved answers."""
import ast
import json
from pathlib import Path
import subprocess

from prepare_answer_smoke import SOURCE_COMMIT, sha, write_once

REPO = Path(__file__).resolve().parents[3]
ANSWER = REPO / 'data/pkulaw_v4_answer_smoke_20260927_wait900'
RELEASE = REPO / 'data/pkulaw_clean_release_20260925'
OUT = REPO / 'data/pkulaw_v4_selfscore_smoke_20260927'


def extract(path, name, kind):
    current = (REPO / path).read_text()
    previous = subprocess.run(['git', 'show', SOURCE_COMMIT+':'+path], cwd=REPO,
                              capture_output=True, text=True, check=True).stdout
    def select(source):
        return next(node for node in ast.parse(source).body
                    if isinstance(node, kind) and node.name == name)
    node = select(current)
    assert ast.dump(node) == ast.dump(select(previous)), 'Historical scoring implementation changed'
    return ast.get_source_segment(current, node)


def main():
    original_input = (ANSWER/'input_tasks.json').read_bytes()
    answer_manifest = json.loads((ANSWER/'input_manifest.json').read_text())
    assert sha(original_input) == answer_manifest['input_tasks_sha256']
    assert sha(original_input) == '37bdeb51af6ffdd7f03ae13b0f3d2158128f7830fa1d3f3780c4821dfa5814da'
    original = json.loads(original_input)
    source_manifest = json.loads((RELEASE/'manifest.json').read_text())
    evaluator = extract('utils/evaluator.py', 'AnswerEvaluator', ast.ClassDef)
    messages = extract('scripts/run_experiment1_smoke_xhub.py', 'analysis_messages', ast.FunctionDef)
    bundle = ('# Generated verbatim from audited historical AST nodes; no network imports.\n'
              '# Source commit: '+SOURCE_COMMIT+'\n'
              'from typing import Dict, List, Optional\nimport re\nai_api = None\n\n'
              +messages+'\n\n'+evaluator+'\n')
    namespace = {}
    exec(compile(bundle, 'legacy_scoring_bundle.py', 'exec'), namespace)
    assert namespace['AnswerEvaluator'](api=object(), flag_parser_version='legacy').flag_parser_version == 'legacy'
    cases, file_hashes = {}, {}
    for split in ('primary', 'secondary'):
        name = split+'/masked_cases_fix4.jsonl'
        data = (RELEASE/name).read_bytes()
        file_hashes[name] = sha(data)
        assert file_hashes[name] == source_manifest['files'][name]['sha256']
        cases[split] = {r['case_id']: r for r in map(json.loads, data.splitlines())}
    tasks = []
    for task in original['tasks']:
        case = cases[task['split']][task['case_id']]
        text, judge = case['case_text_masked'], case['judge_decision_masked']
        assert judge.strip() and sha(text) == task['masked_case_text_hash']
        assert case['source_case_text_hash'] == task['source_case_text_hash']
        assert namespace['analysis_messages'](text, task['question_text']) == task['messages']
        tasks.append({k: task[k] for k in ('split', 'case_id', 'question_id', 'question_text',
                      'source_case_text_hash', 'masked_case_text_hash', 'question_hash')})
        tasks[-1].update(answer_prompt_hash=task['prompt_hash'], case_text_masked=text,
                         judge_decision_masked=judge, judge_decision_hash=sha(judge))
    assert len(tasks) == 10
    inputs = dict(run_id=OUT.name, service_job_id='402816', base_url=original['base_url'],
                  model=original['model'], thinking=True, temperature=0.3,
                  initial_max_tokens=3000, timeout_s=900, workers=2,
                  answer_results_dir='/aifs4su/hansirui_3rd/chenlong/dvjustice_answering_smoke_20260927_wait900/results',
                  answer_input_sha256=sha(original_input), tasks=tasks)
    OUT.mkdir(exist_ok=True)
    bundle_path = OUT/'legacy_scoring_bundle.py'
    if bundle_path.exists():
        assert bundle_path.read_text() == bundle
    else:
        with bundle_path.open('x') as stream:
            stream.write(bundle)
    write_once(OUT/'input_scoring.json', inputs)
    manifest = dict(stage='self_scoring_only_after_answer_completion', cases=2, questions=10,
                    answer_model=original['model'], scoring_model=original['model'],
                    relationship='DeepSeek V4 Flash judges DeepSeek V4 Flash; not cross-model judging',
                    user_authorized_additional_stage=True, source_commit=SOURCE_COMMIT,
                    evaluator_source='utils/evaluator.py:AnswerEvaluator',
                    evaluator_class_sha256=sha(evaluator), analysis_messages_sha256=sha(messages),
                    bundle_sha256=sha(bundle_path.read_bytes()),
                    input_scoring_sha256=sha((OUT/'input_scoring.json').read_bytes()),
                    source_file_hashes=file_hashes, flag_parser_version='legacy',
                    prompt_and_penalty_implementation_unchanged=True,
                    score_thinking=True, answer_thinking=False,
                    scoring_uses_full_masked_judgment=True, scoring_case_chars=2000,
                    upstream_answer_sha256=sha(original_input),
                    upstream_inputs_and_answers_read_only=True,
                    retry_note='Retry scoring only; never regenerate the already frozen answer.',
                    refusal_failure_note='Keep raw response and operational status; parser fallback zero is not a valid measured score.',
                    scope='Only the existing two cases and ten answers; no full-dataset run, no remasking or question generation.')
    write_once(OUT/'input_manifest.json', manifest)
    print(json.dumps({'output':str(OUT),'cases':2,'questions':10,
                      'bundle_sha256':manifest['bundle_sha256'],
                      'input_sha256':manifest['input_scoring_sha256'],'api_calls':0},ensure_ascii=False))


if __name__ == '__main__':
    main()
