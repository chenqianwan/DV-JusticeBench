#!/usr/bin/env python3
"""Freeze two answer-only cases from the verified clean release; no API calls."""
import ast
import hashlib
import json
from pathlib import Path
import subprocess
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[3]
DATA = REPO / 'data/pkulaw_clean_release_20260925'
OUT = REPO / 'data/pkulaw_v4_answer_smoke_20260927'
SOURCE = 'scripts/run_experiment1_smoke_xhub.py'
SOURCE_COMMIT = '06b533d5d695348623c018f95ef5f4809f7ae7ea'


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode('utf-8')).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def frozen_messages():
    current = (REPO / SOURCE).read_text()
    previous = subprocess.run(['git', 'show', SOURCE_COMMIT + ':' + SOURCE], cwd=REPO,
                              check=True, capture_output=True, text=True).stdout
    def function(source):
        return next(n for n in ast.parse(source).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'analysis_messages')
    node = function(current)
    assert ast.dump(node) == ast.dump(function(previous)), 'Historical answer prompt changed'
    namespace = {'List': List, 'Dict': Dict, 'Optional': Optional}
    # Compile only the previously inspected pure prompt function, not the API runner.
    exec(compile(ast.Module(body=[node], type_ignores=[]), SOURCE, 'exec'), namespace)
    return namespace['analysis_messages'], sha(ast.get_source_segment(current, node))


def write_once(path, obj):
    content = (json.dumps(obj, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    if path.exists():
        assert path.read_bytes() == content, 'Refusing to change frozen input: ' + str(path)
    else:
        with path.open('xb') as stream:
            stream.write(content)


def main():
    manifest = json.loads((DATA / 'manifest.json').read_text())
    messages, prompt_source_hash = frozen_messages()
    classifications = {x['case_id']: x for x in
                       map(json.loads, (DATA / 'case_manifest.jsonl').read_text().splitlines())}
    tasks, selections, file_hashes = [], [], {}
    for split, expected in [('primary', 217), ('secondary', 2020)]:
        tables = {}
        for stem in ['masked_cases_fix4', 'questions_fix4']:
            name = split + '/' + stem + '.jsonl'
            content = (DATA / name).read_bytes()
            file_hashes[name] = sha(content)
            assert file_hashes[name] == manifest['files'][name]['sha256'], name + ': hash mismatch'
            rows = [json.loads(line) for line in content.splitlines() if line.strip()]
            table = {row['case_id']: row for row in rows}
            assert len(rows) == len(table) == expected, name + ': count mismatch'
            tables[stem] = table
        cases, questions = tables['masked_cases_fix4'], tables['questions_fix4']
        assert cases.keys() == questions.keys()
        for cid, record in questions.items():
            assert cases[cid]['case_text_masked'].strip()
            assert len(record['questions']) == 5
            assert len({q['question_id'] for q in record['questions']}) == 5
            assert all(q['question'].strip() for q in record['questions'])
        # Deterministic length-median selection, before observing any model output.
        ordered = sorted(cases.values(), key=lambda c: (len(c['case_text_masked']), c['case_id']))
        case = ordered[len(ordered) // 2]
        cid, text = case['case_id'], case['case_text_masked']
        selections.append({'split': split, 'case_id': cid, 'split_cases': expected,
                           'case_text_chars': len(text), 'questions': 5,
                           'original_label': classifications[cid]['original_label']})
        for question in questions[cid]['questions']:
            payload_messages = messages(text, question['question'])
            # This only checks field isolation, not semantic correctness of source questions.
            judgment = case['judge_decision_masked']
            assert not judgment or judgment not in payload_messages[1]['content']
            tasks.append({'split': split, 'case_id': cid, 'question_id': question['question_id'],
                          'messages': payload_messages, 'prompt_hash': sha(canonical(payload_messages)),
                          'source_case_text_hash': case['source_case_text_hash'],
                          'masked_case_text_hash': sha(text), 'question_hash': sha(question['question']),
                          'question_text': question['question']})
    assert len(tasks) == 10 and len({t['question_id'] for t in tasks}) == 10
    request = {'run_id': 'pkulaw_v4_answer_smoke_20260927', 'service_job_id': '402816',
               'base_url': 'http://dgx-040:18044/v1', 'model': 'deepseek_v4_flash',
               'thinking': False, 'temperature': 0.3, 'initial_max_tokens': 3000,
               'timeout_s': 180, 'workers': 2, 'tasks': tasks}
    audit = {'stage': 'answering_only_smoke', 'selected': selections,
             'selection_rule': 'One length-median case per delivery split, sorted by length then case_id',
             'available_cases': 2237, 'available_questions': 11185, 'planned_cases': 2, 'planned_answers': 10,
             'source_release': DATA.name, 'source_commit': SOURCE_COMMIT,
             'source_file_hashes': file_hashes, 'release_manifest_sha256': sha((DATA/'manifest.json').read_bytes()),
             'prompt_source': SOURCE + ':analysis_messages', 'prompt_source_sha256': prompt_source_hash,
             'prompt_matches_source_commit': True, 'source_case_text_unchanged': True,
             'questions_unchanged': True, 'judge_decision_field_sent': False,
             'title_field_added': False, 'masking_calls': 0, 'question_generation_calls': 0,
             'scoring_calls': 0, 'cross_judging_calls': 0, 'full_dataset_authorized_by_this_manifest': False,
             'authorization_note': 'User authorized trying primary/secondary answers with local V4 service; explicitly answer-only. Pilot limited to 2 cases / 10 questions.',
             'model_change': 'Local original DeepSeek-V4-Flash preview, explicit non-thinking; not historical V3.2',
             'retry_policy': 'Historical executable behavior: 3000 -> 6000 once on length; <=3 HTTP tries/round; <=3 outer tries for error/empty; no retry merely to overcome refusal.',
             'handoff_discrepancy': 'Engineer handoff describes 3000->8000->16000; actual historical client doubles 3000->6000. This pilot follows executable behavior.',
             'quality_limit': 'Structural clean release, not full expert semantic validation. Existing xxx substitutions and question wording retained exactly.'}
    OUT.mkdir(parents=True, exist_ok=True)
    write_once(OUT/'input_tasks.json', request)
    audit['input_tasks_sha256'] = sha((OUT/'input_tasks.json').read_bytes())
    write_once(OUT/'input_manifest.json', audit)
    print(json.dumps({'output': str(OUT), 'selections': selections,
                      'input_tasks_sha256': audit['input_tasks_sha256'], 'api_calls': 0}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
