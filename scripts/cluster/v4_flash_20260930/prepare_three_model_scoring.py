#!/usr/bin/env python3
"""Freeze existing XHub answers for the unchanged cluster scoring stage, offline.

No answering/scoring requests, SSH, source-file edits or classification changes.
"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
OLD = REPO / 'scripts/cluster/v4_flash_20260927'
sys.path.insert(0, str(OLD))
from prepare_answer_smoke import canonical, sha
from prepare_full_ds_pipeline import freeze_chunks, file_sha, require
from prepare_selfscore_batch128 import historical_capture, RAW_FIELDS, TASK_FIELDS, json_bytes

OUT = REPO / 'data/pkulaw_three_model_scoring_20260930'
PRIMARY = REPO / 'data/pkulaw_primary_three_answers_full_20260929'
SECONDARY = REPO / 'data/pkulaw_secondary_unknown_once_20260930'
AUDIT = REPO / 'outputs/three_model_cluster_scoring_20260930/independent_input_audit.json'
MAPPING = REPO / 'data/pkulaw_v4_answer_batch128_20260928/task_mapping.jsonl'
BUNDLE = REPO / 'data/pkulaw_v4_selfscore_batch128_v2_20260928/legacy_scoring_bundle.py'
BUNDLE_HASH = '6af07139a06994b3192e450e5fa79f23e69851b889e999a99187efaabc81d9ad'
MODELS = ('gpt-4o', 'gemini-2.5-flash', 'qwen-max')
KEY_FIELDS = ('answering_model', 'split', 'case_id', 'question_id')
CONFIG = dict(run_id='pkulaw_three_model_ds_scoring_20260930', service_job_id='402816', base_url='http://dgx-040:18044/v1',
              model='deepseek_v4_flash', thinking=True, temperature=.3,
              initial_max_tokens=3000, timeout_s=1800, workers=128)
EXPECTED = {
    'gpt-4o': {'primary': 997, 'secondary': 9870},
    'gemini-2.5-flash': {'primary': 1074, 'secondary': 10019},
    'qwen-max': {'primary': 1075, 'secondary': 10012},
}


def load(path):
    return json.loads(path.read_bytes())


def source_id(row):
    return 'task_' + sha(canonical([row[k] for k in ('split', 'case_id', 'question_id', 'model')]))[:24]


def key(row):
    return tuple(row[k] for k in KEY_FIELDS)


def prepare(output):
    audit_bytes = AUDIT.read_bytes()
    audit = json.loads(audit_bytes)
    require(audit['checks']['issues_count'] == 0 and audit['eligible_for_scoring'] == 33047,
            'Independent source audit failed')
    for name, expected in audit['source_file_sha256'].items():
        require(file_sha(REPO / name) == expected, 'Audited source changed: ' + name)
    bundle_bytes = BUNDLE.read_bytes()
    require(sha(bundle_bytes) == BUNDLE_HASH, 'Historical rubric bundle changed')
    bundle, Capture, evaluate, adapter_hashes = historical_capture(bundle_bytes)
    mapping = {}
    with MAPPING.open('rb') as stream:
        for line in stream:
            row = json.loads(line)
            k = tuple(row[n] for n in ('split', 'case_id', 'question_id'))
            require(k not in mapping, 'Duplicate mapping key')
            for text, digest in [('case_text_masked', 'masked_case_text_hash'),
                                 ('judge_decision_masked', 'judge_decision_hash'),
                                 ('question_text', 'question_hash')]:
                require(sha(row[text]) == row[digest], 'Mapping text hash mismatch')
            require(sha(canonical(row['answer_messages'])) == row['answer_prompt_hash'],
                    'Mapping answer prompt hash mismatch')
            mapping[k] = row
    require(len(mapping) == 11095, 'Wrong mapping question count')
    excluded_bytes = (PRIMARY / 'excluded_cases.json').read_bytes()
    excluded_cases = {r['case_id'] for r in json.loads(excluded_bytes)}
    require(len(excluded_cases) == 18 and not excluded_cases & {k[1] for k in mapping},
            'Permanent dirty-case exclusion changed')
    primary_input = load(PRIMARY / 'input_tasks.json')
    sources = []
    for row in primary_input['tasks']:
        task_id = source_id(row)
        sources.append((task_id, PRIMARY / 'results' / task_id / 'terminal.json', None))
    require(len(sources) == 3225, 'Wrong primary input count')
    for task_id, row in load(SECONDARY / 'merged_index.json').items():
        sources.append((task_id, Path(row['terminal_path']), row))
    require(len(sources) == 33285, 'Wrong combined answer count')
    tasks, provenance, exclusions, fingerprint = [], [], [], []
    source_keys, counts, source_counts, status_counts = set(), Counter(), Counter(), Counter()
    for task_id, path, index_row in sources:
        require(path.is_file() and not path.is_symlink() and path.resolve().is_relative_to(REPO),
                'Invalid source terminal path')
        raw = path.read_bytes()
        terminal = json.loads(raw)
        require(source_id(terminal) == task_id and terminal['model'] in MODELS, 'Source task identity differs')
        if index_row is not None:
            require(index_row['status'] == terminal['status'] and index_row['model'] == terminal['model'],
                    'Merged source terminal differs from its index')
        k3 = tuple(terminal[n] for n in ('split', 'case_id', 'question_id'))
        require(k3 in mapping and terminal['case_id'] not in excluded_cases, 'Unknown/dirty question')
        mapped = mapping[k3]
        for field in ('split', 'case_id', 'question_id', 'question_text', 'source_case_text_hash',
                      'masked_case_text_hash', 'question_hash'):
            require(terminal[field] == mapped[field], 'Answer/mapping source differs: ' + field)
        require(terminal['prompt_hash'] == mapped['answer_prompt_hash'], 'Answer prompt differs from mapping')
        identity = dict(answering_model=terminal['model'], split=k3[0], case_id=k3[1], question_id=k3[2])
        k4 = key(identity)
        require(k4 not in source_keys, 'Duplicate final source answer')
        source_keys.add(k4)
        source_counts[(terminal['model'], k3[0])] += 1
        status_counts[(terminal['model'], k3[0], terminal['status'])] += 1
        source = dict(identity, task_id=task_id, source_terminal_path=str(path.relative_to(REPO)),
                      answer_terminal_sha256=sha(raw), source_task_sha256=terminal['task_sha256'],
                      answer_status=terminal['status'], requested_model=terminal.get('requested_model'),
                      response_model=terminal.get('response_model'), response_id=terminal.get('response_id'),
                      finish_reason=terminal.get('finish_reason'), length_retried=terminal.get('length_retried', False))
        if index_row:
            source['replacement_round'] = index_row.get('replacement_round', 0)
            if index_row.get('original_terminal'):
                source['original_terminal'] = index_row['original_terminal']
        if terminal['status'] != 'ok':
            require(terminal['status'] in {'content_filter', 'needs_reconcile'}, 'Unexpected new exception class')
            source.update(scoring_performed=False, score_zero_imputed=False,
                          exclusion_reason='No completed eligible answer; preserve original terminal classification.')
            exclusions.append(source)
            continue
        answer = terminal['answer']
        require(isinstance(answer, str) and answer.strip() and terminal['finish_reason'] in ('stop', 'end_turn'),
                'Invalid supposedly successful answer')
        task = {field: mapped[field] for field in RAW_FIELDS}
        task.update(answer=answer, answer_hash=sha(answer), answer_status='ok',
                    answer_terminal_sha256=sha(raw), answering_model=terminal['model'],
                    scoring_model=CONFIG['model'])
        capture = Capture()
        evaluator, _ = evaluate(bundle, capture, task, answer)
        require(len(capture.prompts) == 1 and evaluator.flag_parser_version == 'legacy',
                'Historical scorer adapter changed')
        prompt = capture.prompts[0]
        require(answer in prompt and mapped['judge_decision_masked'] in prompt
                and mapped['question_text'] in prompt and mapped['case_text_masked'][:2000] in prompt,
                'Original prompt omitted answer or reference input')
        task['scoring_prompt_hash'] = sha(canonical(bundle.analysis_messages(prompt, None)))
        require(set(task) == TASK_FIELDS, 'Wrong scoring task fields')
        source.update(answer_hash=task['answer_hash'], scoring_prompt_hash=task['scoring_prompt_hash'])
        response_path = path.with_name('attempt_%03d.response.json' % terminal['attempts'])
        source['final_answer_response_path'] = str(response_path.relative_to(REPO))
        source['final_answer_response_sha256'] = file_sha(response_path)
        tasks.append(task)
        provenance.append(source)
        counts[(terminal['model'], k3[0])] += 1
        fingerprint.append(dict(task_id=task_id, split=k3[0], case_id=k3[1], question_id=k3[2],
                                model=terminal['model'], terminal_path=str(path.relative_to(REPO)),
                                terminal_sha256=sha(raw), answer_sha256=sha(answer),
                                task_sha256=terminal['task_sha256']))
    require(len(tasks) == 33047 and len(exclusions) == 238, 'Wrong eligible/excluded answer totals')
    require(Counter(r['answer_status'] for r in exclusions) == {'content_filter': 230, 'needs_reconcile': 8},
            'Exception counts changed')
    require(sha(canonical(sorted(fingerprint, key=lambda r: r['task_id'])))
            == audit['eligible_provenance_fingerprint']['sha256'], 'Independent answer fingerprint differs')
    for model in MODELS:
        for split, expected in EXPECTED[model].items():
            require(counts[(model, split)] == expected, 'Per-model eligible total changed')
            require(source_counts[(model, split)] == {'primary': 1075, 'secondary': 10020}[split],
                    'Source question coverage differs')
    # Exactly one normal answer for each model/split is first, for a six-request
    # acceptance segment. These same immutable tasks are reused by full resume.
    ordered = sorted(tasks, key=lambda t: (t['split'], t['case_id'], t['question_id'], MODELS.index(t['answering_model'])))
    lead = [next(t for t in ordered if t['split'] == split and t['answering_model'] == model)
            for split in ('primary', 'secondary') for model in MODELS]
    lead_keys = {key(t) for t in lead}
    ordered = lead + [t for t in ordered if key(t) not in lead_keys]
    output.mkdir(parents=True, exist_ok=True)
    require(not output.is_symlink(), 'Output must not be a symlink')
    files = {}
    for name, content in [('legacy_scoring_bundle.py', bundle_bytes), ('excluded_cases.json', excluded_bytes),
                          ('source_release_audit.json', audit_bytes)]:
        files[name] = freeze_chunks(output / name, [content])
    for name, records in [('answer_provenance.jsonl', provenance), ('excluded_answers.jsonl', exclusions)]:
        files[name] = freeze_chunks(output / name,
            ((canonical(r) + '\n').encode('utf-8') for r in sorted(records, key=key)))
    upstream_hash = files['answer_provenance.jsonl']['sha256']
    def chunks():
        top = dict(CONFIG, bundle_sha256=BUNDLE_HASH, upstream_answers_sha256=upstream_hash)
        yield (canonical(top)[:-1] + ',"tasks":[').encode('utf-8')
        for i, task in enumerate(ordered):
            yield ((',' if i else '') + canonical(task)).encode('utf-8')
        yield b']}\n'
    files['input_scoring.json'] = freeze_chunks(output / 'input_scoring.json', chunks())
    manifest = dict(run_id=CONFIG['run_id'], stage='existing_three_model_answers_ds_scoring_only',
                    state='prepared_not_started', schema='V18-four-key', source_answer_count=33285,
                    network_tasks=33047, excluded_answer_count=238, excluded_status_counts=dict(Counter(r['answer_status'] for r in exclusions)),
                    tasks_by_model_split=EXPECTED, source_answers_by_model_split={m: {'primary': 1075, 'secondary': 10020} for m in MODELS},
                    cases_by_split={'primary': 215, 'secondary': 2004}, questions=11095, cases=2219,
                    permanently_excluded_cases=18, bundle_sha256=BUNDLE_HASH, upstream_answers_sha256=upstream_hash,
                    input_scoring_sha256=files['input_scoring.json']['sha256'], runtime_parameters=CONFIG,
                    legacy_adapter_hashes=adapter_hashes, key_fields=list(KEY_FIELDS),
                    acceptance_first_six=[{n: t[n] for n in KEY_FIELDS} for t in lead], files=files,
                    answer_regeneration=False, self_scoring=False, scoring_protocol_changed=False,
                    score_zero_imputation=False, paid_xhub_requests=False,
                    source_audit_sha256=sha(audit_bytes), source_answer_fingerprint=audit['eligible_provenance_fingerprint'])
    freeze_chunks(output / 'manifest.json', [json_bytes(manifest)])
    print(json.dumps(dict(output=str(output), eligible=len(tasks), excluded=len(exclusions),
                          input_bytes=files['input_scoring.json']['bytes'], input_sha256=manifest['input_scoring_sha256'],
                          by_model_split=EXPECTED), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUT)
    args = parser.parse_args()
    prepare(args.output.resolve())
