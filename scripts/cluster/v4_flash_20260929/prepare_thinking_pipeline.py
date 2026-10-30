#!/usr/bin/env python3
"""Freeze existing clean tasks for a new thinking-answer + legacy-score run."""
import json
from collections import Counter
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / 'v4_flash_20260927'
sys.path.insert(0, str(OLD))
from prepare_full_ds_pipeline import freeze_chunks, file_sha, require
from run_answer_smoke import TASK_FIELDS, canonical, digest

REPO = HERE.parents[2]
SOURCE = REPO / 'data/pkulaw_v4_answer_batch128_20260928'
SCORES = REPO / 'data/pkulaw_v4_selfscore_batch128_v2_20260928'
OUT = REPO / 'data/pkulaw_v4_thinking_pipeline_20260929'
MAPPING_SHA = 'ac70b5739bd2fa471441ff8c970709c15643df16030b831ed8a19d9136e969e2'
BUNDLE_SHA = '6af07139a06994b3192e450e5fa79f23e69851b889e999a99187efaabc81d9ad'
CONFIG = dict(run_id='pkulaw_v4_thinking_pipeline_20260929', service_job_id='402816',
              base_url='http://dgx-040:18044/v1', model='deepseek_v4_flash', thinking=True,
              temperature=.3, initial_max_tokens=3000, timeout_s=1800, workers=128)


def main():
    mapping_bytes = (SOURCE / 'task_mapping.jsonl').read_bytes()
    require(digest(mapping_bytes) == MAPPING_SHA, 'Frozen mapping changed')
    records = [json.loads(line) for line in mapping_bytes.splitlines()]
    require(len(records) == 11095, 'Expected all 11095 questions')
    excluded_bytes = (SOURCE / 'excluded_cases.json').read_bytes()
    excluded = json.loads(excluded_bytes)
    ids = {row['case_id'] for row in excluded}
    require(len(ids) == len(excluded) == 18, 'Expected 18 permanent exclusions')
    groups = Counter((r['split'], r['case_id']) for r in records)
    require(set(groups.values()) == {5}, 'Exactly five questions per case')
    require(Counter(s for s, _ in groups) == {'primary': 215, 'secondary': 2004}, 'Split mismatch')
    require(not (ids & {r['case_id'] for r in records}), 'Excluded case reintroduced')
    require(len({(r['split'], r['case_id'], r['question_id']) for r in records}) == 11095, 'Duplicate task')
    first_cases = {s: min(cid for split, cid in groups if split == s) for s in ('primary', 'secondary')}
    records.sort(key=lambda r: (r['case_id'] != first_cases[r['split']], r['split'], r['case_id'], r['question_id']))
    tasks = []
    for row in records:
        task = {k: row[k] for k in TASK_FIELDS - {'messages', 'prompt_hash'}}
        task.update(messages=row['answer_messages'], prompt_hash=row['answer_prompt_hash'])
        require(digest(canonical(task['messages'])) == task['prompt_hash'], 'Message hash mismatch')
        require(digest(row['case_text_masked'].encode()) == row['masked_case_text_hash'], 'Fact hash mismatch')
        require(digest(row['question_text'].encode()) == row['question_hash'], 'Question hash mismatch')
        require(digest(row['judge_decision_masked'].encode()) == row['judge_decision_hash'], 'Judgment hash mismatch')
        require(set(task) == TASK_FIELDS, 'Network schema contains extra fields')
        tasks.append(task)
    require(Counter(t['split'] for t in tasks[:10]) == {'primary': 5, 'secondary': 5}, 'Pilot ordering')
    bundle = (SCORES / 'legacy_scoring_bundle.py').read_bytes()
    require(digest(bundle) == BUNDLE_SHA, 'Frozen evaluator changed')
    OUT.mkdir(mode=0o700, parents=True, exist_ok=True)
    files = {}
    values = {
        'input_tasks.json': canonical(dict(CONFIG, tasks=tasks)) + b'\n',
        'task_mapping.jsonl': mapping_bytes,
        'excluded_cases.json': excluded_bytes,
        'legacy_scoring_bundle.py': bundle,
    }
    for name, raw in values.items():
        files[name] = freeze_chunks(OUT / name, [raw])
    manifest = dict(run_id=CONFIG['run_id'], state='prepared_not_started', cases=2219, questions=11095,
                    cases_by_split={'primary': 215, 'secondary': 2004},
                    questions_by_split={'primary': 1075, 'secondary': 10020},
                    permanently_excluded_cases=18, reused_answers=0,
                    first_ten_cases=first_cases, input_tasks_sha256=files['input_tasks.json']['sha256'],
                    task_mapping_sha256=MAPPING_SHA, bundle_sha256=BUNDLE_SHA,
                    source_manifest_sha256=file_sha(SOURCE / 'input_manifest.json'),
                    retry_contract='legacy108_coupled', files=files, config=CONFIG,
                    existing_nonthinking_results='retained_unmodified_in_statistics',
                    new_answering_condition='DeepSeek thinking', answer_thinking=True, scoring_thinking=True,
                    questions_regenerated=False, masking_repeated=False, paid_api_calls=0)
    freeze_chunks(OUT / 'input_manifest.json', [canonical(manifest) + b'\n'])
    print(json.dumps({k: manifest[k] for k in ['state', 'cases', 'questions', 'cases_by_split', 'first_ten_cases', 'input_tasks_sha256', 'retry_contract']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
