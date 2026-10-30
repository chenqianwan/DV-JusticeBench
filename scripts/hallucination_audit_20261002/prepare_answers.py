#!/usr/bin/env python3
"""Prepare immutable, answer-level hallucination-audit inputs, entirely offline.

Only frozen primary/secondary sources are used. No original-108 input, network,
new inference, score corrections, or hallucination classification is performed.
"""
import argparse
from collections import Counter, defaultdict
import datetime as dt
import hashlib
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/pkulaw_hallucination_audit_20261002'
REPORT = ROOT / 'outputs/primary_secondary_full_results_20261001/support/statistics.json'
BASE = ROOT / 'data/pkulaw_three_model_scoring_20260930'
FINAL = BASE / 'final_export_20261001'
NT = ROOT / 'data/pkulaw_v4_answer_batch128_20260928'
CONDITIONS = ('ds_nonthinking', 'ds_thinking', 'gpt-4o', 'gemini-2.5-flash', 'qwen-max')
SPLITS = ('primary', 'secondary')


def require(value, message):
    if not value:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def digest(value):
    return hashlib.sha256(value.encode() if isinstance(value, str) else value).hexdigest()


def file_hash(path):
    require(path.is_file() and not path.is_symlink(), 'Missing/redirected source: ' + str(path))
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def question_key(row):
    return row['split'], row['case_id'], row['question_id']


def record_key(row):
    return (*question_key(row), row['answer_condition'])


def insert_unique(mapping, key, value):
    require(key not in mapping, 'Duplicate/conflicting key: ' + str(key))
    mapping[key] = value


class Sources:
    def __init__(self, root, old_report_sources):
        self.root, self.expected, self.entries = root, old_report_sources, {}

    def register(self, path, expected=None):
        path = Path(path)
        name = str(path.relative_to(self.root))
        if name not in self.entries:
            actual = file_hash(path)
            inherited = self.expected.get(name, {}).get('sha256')
            require(inherited is None or actual == inherited, 'Old-report source changed: ' + name)
            self.entries[name] = dict(sha256=actual, bytes=path.stat().st_size,
                                      matched_old_report_hash=inherited is not None)
        require(expected is None or self.entries[name]['sha256'] == expected, 'Frozen source hash changed: ' + name)
        return name, self.entries[name]['sha256']

    def pointer(self, path, row, **position):
        name, hashed = self.register(path)
        return dict(path=name, file_sha256=hashed, record_sha256=digest(canonical(row)),
                    record_hash_encoding='canonical-json-utf8', **position)

    def rows(self, path):
        name, _ = self.register(path)
        count = 0
        with Path(path).open('rb') as stream:
            for line in stream:
                require(line.endswith(b'\n') and line.strip(), 'Partial/blank JSONL row: ' + name)
                count += 1
                row = json.loads(line)
                require(isinstance(row, dict), 'Non-object JSONL row')
                yield row, self.pointer(path, row, line_number=count)
        self.entries[name]['rows'] = count
        expected_count = self.expected.get(name, {}).get('rows')
        require(expected_count is None or count == expected_count, 'Old-report row count changed: ' + name)

    def read(self, path, expected=None):
        self.register(path, expected)
        return json.loads(Path(path).read_bytes())


class JsonStream:
    """Small standard-library JSON tokenizer, retaining at most one task object."""
    def __init__(self, stream, chunk_size=1048576):
        self.stream, self.chunk_size = stream, chunk_size
        self.buffer, self.pos, self.eof = '', 0, False
        self.decoder = json.JSONDecoder()

    def fill(self):
        require(not self.eof, 'Unexpected end of streamed JSON')
        self.buffer = self.buffer[self.pos:]
        self.pos = 0
        block = self.stream.read(self.chunk_size)
        self.eof = not bool(block)
        self.buffer += block

    def peek(self):
        while True:
            while self.pos < len(self.buffer) and self.buffer[self.pos].isspace():
                self.pos += 1
            if self.pos < len(self.buffer):
                return self.buffer[self.pos]
            if self.eof:
                return None
            self.fill()

    def expect(self, symbol):
        require(self.peek() == symbol, 'Unexpected JSON structure; expected ' + symbol)
        self.pos += 1

    def value(self):
        self.peek()
        while True:
            try:
                value, end = self.decoder.raw_decode(self.buffer, self.pos)
                if not self.eof and (end == len(self.buffer) or
                        (isinstance(value, (int, float)) and self.buffer[end] not in ',]} \r\n\t')):
                    self.fill()
                    continue
                self.pos = end
                return value
            except json.JSONDecodeError:
                if self.eof:
                    raise
                self.fill()


def streamed_tasks(stream, chunk_size=1048576):
    parser, seen = JsonStream(stream, chunk_size), set()
    parser.expect('{')
    while parser.peek() != '}':
        name = parser.value()
        require(isinstance(name, str) and name not in seen, 'Duplicate/non-string root key')
        seen.add(name)
        parser.expect(':')
        if name == 'tasks':
            parser.expect('[')
            while parser.peek() != ']':
                row = parser.value()
                require(isinstance(row, dict), 'Non-object task')
                yield row
                if parser.peek() != ']':
                    parser.expect(',')
                    require(parser.peek() != ']', 'Trailing task comma')
            parser.expect(']')
        else:
            parser.value()
        if parser.peek() != '}':
            parser.expect(',')
            require(parser.peek() != '}', 'Trailing object comma')
    parser.expect('}')
    require('tasks' in seen and parser.peek() is None, 'Missing task array or trailing data')


def classify_answer(status, answer):
    require(isinstance(answer, str), 'Answer body must be a string')
    require(status in ('ok', 'truncated', 'content_filter', 'needs_reconcile'), 'Unexpected answer status: ' + status)
    if status == 'ok':
        require(bool(answer.strip()), 'Complete answer has no body; reasoning is not a substitute')
        return 'complete', True
    return status, False


def verify_text_hashes(row, mapping, condition):
    require(question_key(row) == question_key(mapping), 'Question key mismatch')
    for field in ('question_hash', 'masked_case_text_hash', 'source_case_text_hash'):
        require(row.get(field) == mapping[field], condition + ' differs: ' + field)
    require(row.get('question_text') == mapping['question_text'], 'Question body differs')
    if 'judge_decision_hash' in row:
        require(row['judge_decision_hash'] == mapping['judge_decision_hash'], 'Judicial-reference hash differs')
    prompt = row.get('answer_prompt_hash', row.get('prompt_hash'))
    if prompt is not None:
        require(prompt == mapping['answer_prompt_hash'], 'Actual-answer prompt hash differs')


def prepare(output=OUT):
    require(not any((output / name).exists() for name in ('answers.jsonl', 'excluded_answers.jsonl', 'manifest.json')),
            'Canonical output exists; refusing to overwrite')
    report = json.loads(REPORT.read_bytes())
    sources = Sources(ROOT, report['sources'])
    sources.register(REPORT)
    sources.register(REPORT.parent / 'prepare_report.py')
    excluded_case_path = NT / 'excluded_cases.json'
    dirty = {row['case_id'] for row in sources.read(excluded_case_path)}
    require(len(dirty) == 18, 'Permanent dirty-case list changed')
    mapping, mapping_sources = {}, {}
    for row, source in sources.rows(NT / 'task_mapping.jsonl'):
        key = question_key(row)
        require(row['case_id'] not in dirty, 'Permanently excluded case re-entered')
        for field, hashed in [('case_text_masked', 'masked_case_text_hash'),
                              ('judge_decision_masked', 'judge_decision_hash'), ('question_text', 'question_hash')]:
            require(digest(row[field]) == row[hashed], 'Frozen mapping text hash mismatch')
        require(digest(canonical(row['answer_messages'])) == row['answer_prompt_hash'], 'Frozen messages hash differs')
        require(row['case_text_masked'] in row['answer_messages'][1]['content']
                and row['question_text'] in row['answer_messages'][1]['content'], 'Actual model input omitted full facts/question')
        require(row['judge_decision_masked'] not in row['answer_messages'][1]['content'], 'Full judicial reference leaked')
        insert_unique(mapping, key, row)
        mapping_sources[key] = source
    require(len(mapping) == 11095 and Counter(k[0] for k in mapping) == {'primary':1075,'secondary':10020}, 'Question coverage changed')
    cases = Counter(k[:2] for k in mapping)
    require(len(cases) == 2219 and set(cases.values()) == {5}, 'Case/five-question coverage changed')
    metadata = {}
    for split in SPLITS:
        path = ROOT / f'data/pkulaw_clean_release_20260925/{split}/masked_cases_fix4.jsonl'
        for row, source in sources.rows(path):
            key = split, row['case_id']
            if key not in cases:
                require(row['case_id'] in dirty, 'Unexpected case outside frozen cohort')
                continue
            first = next(mapping[k] for k in mapping if k[:2] == key)
            require(row['case_text_masked'] == first['case_text_masked']
                    and row['judge_decision_masked'] == first['judge_decision_masked'], 'Metadata input differs from actual frozen prompt')
            insert_unique(metadata, key, (row, source))
    require(set(metadata) == set(cases), 'Incomplete title metadata')

    scores = {}
    score_paths = [('ds_nonthinking', FINAL / f'ds_nonthinking/results/scores_deepseek_v4_flash_{s}.jsonl') for s in SPLITS]
    score_paths += [('ds_nonthinking', ROOT / 'data/pkulaw_v4_selfscore_batch128_v2_20260928/reused_scores.jsonl')]
    score_paths += [(m, FINAL / f'results/scores_{m}_{s}.jsonl') for m in CONDITIONS[2:] for s in SPLITS]
    for condition, path in score_paths:
        for row, source in sources.rows(path):
            key = question_key(row)
            require(key in mapping, 'Score outside canonical cohort')
            verify_text_hashes(row, mapping[key], condition)
            insert_unique(scores, (*key, condition), dict(status=row['status'], answer_hash=row['answer_hash'],
                          answer_status=row.get('answer_status', 'ok'), source=source))
    manifest = sources.read(BASE / 'manifest.json')
    provenance = {}
    sources.register(BASE / 'answer_provenance.jsonl', manifest['upstream_answers_sha256'])
    for row, source in sources.rows(BASE / 'answer_provenance.jsonl'):
        insert_unique(provenance, (*question_key(row), row['answering_model']), (row, source))
    require(len(provenance) == 33047, 'Three-model normal-answer provenance count changed')

    output.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%f')
    temporary = {name: output / (name + '.' + stamp + '.partial') for name in ('answers.jsonl', 'excluded_answers.jsonl')}
    streams = {name: path.open('xb') for name, path in temporary.items()}
    seen, grouped = set(), defaultdict(lambda: {'answer_status': Counter(), 'score_status': Counter(), 'status': Counter()})
    output_hashes, output_counts = {n:hashlib.sha256() for n in streams}, Counter()

    def emit(condition, row, answer, answer_status, score_status, source, extra_sources=None,
             answer_completed_at=None, record_completed_at=None, response_model=None):
        key = question_key(row)
        fullkey = (*key, condition)
        require(key in mapping and fullkey not in seen, 'Duplicate/unknown canonical record')
        seen.add(fullkey)
        base = mapping[key]
        status, eligible = classify_answer(answer_status, answer)
        case, case_source = metadata[key[:2]]
        item = dict(split=key[0], case_id=key[1], question_id=key[2], answer_condition=condition,
                    title=case['title_masked'], title_is_masked=True, question_text=base['question_text'],
                    case_text_masked=base['case_text_masked'], judicial_reference=base['judge_decision_masked'],
                    judicial_reference_was_answer_input=False, answer=answer, answer_sha256=digest(answer),
                    answer_prompt_hash=base['answer_prompt_hash'], question_hash=base['question_hash'],
                    masked_case_text_hash=base['masked_case_text_hash'], judge_decision_hash=base['judge_decision_hash'],
                    status=status, answer_status=answer_status, scoring_status=score_status,
                    main_denominator_eligible=eligible, judgment_date=case.get('judgment_date'),
                    answer_completed_at=answer_completed_at, record_completed_at=record_completed_at,
                    response_model=response_model, sources=dict(answer_record=source,
                    frozen_input=mapping_sources[key], case_metadata=case_source, **(extra_sources or {})))
        name = 'answers.jsonl' if eligible else 'excluded_answers.jsonl'
        raw = canonical(item) + b'\n'
        streams[name].write(raw); output_hashes[name].update(raw); output_counts[name] += 1
        group = grouped[condition, key[0]]
        group['answer_status'][answer_status] += 1
        group['score_status'][score_status] += 1
        group['status'][status] += 1

    try:
        for row, source in sources.rows(NT / 'final/answers_deepseek_v4_flash_all.jsonl'):
            key = question_key(row); fullkey = (*key, 'ds_nonthinking')
            require(key in mapping and row['thinking'] is False, 'Nonthinking answer condition mismatch')
            verify_text_hashes(row, mapping[key], 'ds_nonthinking')
            scoring = scores[fullkey]
            require(digest(row['answer']) == scoring['answer_hash'] and row['status'] == scoring['answer_status'], 'Nonthinking final answer/score mismatch')
            evidence = row.get('audit_evidence') or row.get('reuse_source')
            require(isinstance(evidence, dict) and evidence['answer_sha256'] == digest(row['answer']), 'Nonthinking preserved answer audit differs')
            emit('ds_nonthinking', row, row['answer'], row['status'], scoring['status'], source,
                 dict(scoring_record=scoring['source'], preserved_original_answer_audit=evidence),
                 answer_completed_at=row.get('completed_at'), record_completed_at=row.get('completed_at'),
                 response_model=row.get('response_model'))
        for split in SPLITS:
            path = FINAL / f'ds_thinking/results/thinking_pipeline_{split}.jsonl'
            for row, source in sources.rows(path):
                key = question_key(row)
                require(key in mapping and row['thinking'] is True and row['answering_model'] == 'deepseek_v4_flash',
                        'Thinking answer condition mismatch')
                verify_text_hashes(row, mapping[key], 'ds_thinking')
                generation = row['generation']
                require(row['answer'] == generation['content'] and row['answer_status'] == generation['status'],
                        'Thinking final answer differs from final generation body/status')
                require(row['answer_finish_reason'] == generation['finish_reason'], 'Thinking finish reason mismatch')
                emit('ds_thinking', row, row['answer'], row['answer_status'], row['status'], source,
                     dict(scoring_record=source, final_generation=dict(response_id=generation.get('response_id'),
                     last_response_file=generation.get('last_response_file'),
                     verification='Frozen final pipeline export; original per-attempt receipts not re-downloaded')),
                     record_completed_at=row.get('completed_at'), response_model=generation.get('response_model'))
        path = BASE / 'input_scoring.json'
        sources.register(path, manifest['input_scoring_sha256'])
        n = 0
        with path.open() as stream:
            for n, row in enumerate(streamed_tasks(stream), 1):
                condition = row['answering_model']; key = question_key(row); fullkey = (*key, condition)
                require(condition in CONDITIONS[2:] and key in mapping, 'Unexpected external model/slot')
                verify_text_hashes(row, mapping[key], condition)
                require(row['case_text_masked'] == mapping[key]['case_text_masked']
                        and row['judge_decision_masked'] == mapping[key]['judge_decision_masked'], 'Scoring input/reference differs')
                scoring = scores[fullkey]; original, original_source = provenance[fullkey]
                require(row['answer_status'] == original['answer_status'] == scoring['answer_status'] == 'ok', 'Normal-answer status conflict')
                require(digest(row['answer']) == row['answer_hash'] == original['answer_hash'] == scoring['answer_hash'], 'External-model final answer hash conflict')
                require(row['answer_terminal_sha256'] == original['answer_terminal_sha256'], 'Original answer receipt identity mismatch')
                emit(condition, row, row['answer'], 'ok', scoring['status'], sources.pointer(path,row,array_index=n-1),
                     dict(scoring_record=scoring['source'], answer_provenance=original_source,
                     source_terminal=dict(path=original['source_terminal_path'], file_sha256=original['answer_terminal_sha256'],
                     verification='Hash inherited from frozen answer provenance; not rescanned'),
                     final_answer_response=dict(path=original['final_answer_response_path'],
                     file_sha256=original['final_answer_response_sha256'],
                     verification='Hash inherited from frozen answer provenance; not rescanned')),
                     response_model=original.get('response_model'))
        require(n == 33047, 'External-model complete-answer count changed')
        sources.entries[str(path.relative_to(ROOT))]['tasks'] = n
        for row, source in sources.rows(BASE / 'excluded_answers.jsonl'):
            condition = row['answering_model']; key = question_key(row)
            require(condition in CONDITIONS[2:] and key in mapping, 'Excluded answer outside cohort')
            terminal_path = ROOT / row['source_terminal_path']
            terminal = sources.read(terminal_path, row['answer_terminal_sha256'])
            require(question_key(terminal) == key and terminal['status'] == row['answer_status'], 'Excluded terminal mismatch')
            verify_text_hashes(terminal, mapping[key], condition)
            emit(condition, row, terminal.get('answer') or '', row['answer_status'], 'not_scored', source,
                 dict(source_terminal=sources.pointer(terminal_path,terminal)),
                 answer_completed_at=terminal.get('completed_at'), record_completed_at=terminal.get('completed_at'),
                 response_model=terminal.get('response_model'))
        expected_keys = {(*key, condition) for key in mapping for condition in CONDITIONS}
        require(seen == expected_keys and len(seen) == 55475, 'Canonical slot coverage/uniqueness differs')
        require(output_counts == {'answers.jsonl':55236,'excluded_answers.jsonl':239}, 'Unexpected complete/excluded answer totals')
        for row in report['stats']:
            g = grouped[row['model'],row['split']]
            require(dict(g['answer_status']) == row['answer_status'] and dict(g['score_status']) == row['status'],
                    'Answer/score status differs from old full report: '+row['model']+'/'+row['split'])
        require(set(report['sources']) <= set(sources.entries), 'Not all old-report sources checked')
        results = dict(schema='hallucination_answer_corpus_v1', created_at=dt.datetime.now(dt.timezone.utc).isoformat(),
                       mode='offline_input_preparation_only', cases=2219, questions=11095, answer_slots=55475,
                       complete_answers=55236, excluded_answers=239, conditions=list(CONDITIONS),
                       key_fields=['split','case_id','question_id','answer_condition'], permanently_excluded_cases=18,
                       counts={m:{s:{kind:dict(v) for kind,v in grouped[m,s].items()} for s in SPLITS} for m in CONDITIONS},
                       scoring_failures_do_not_exclude_complete_answers=True, reasoning_content_used_as_answer=False,
                       hallucination_labels_generated=False, zero_imputation=False, original_results_modified=False,
                       network_requests=0, prior_report_valid_score_denominator=54966,
                       date_policy='Only explicit source date fields are retained. No judgment date is inferred from title, case number, or model text. '
                       'Pipeline completed_at is not mislabelled as answer completion. External complete-answer generation dates are absent from the frozen scoring/provenance source and remain null.',
                       reference_policy='case_text_masked is the complete fact text in the frozen original answering messages; '
                       'judicial_reference is separate and was not an extra answering input; title is masked metadata.',
                       verification_scope='Every frozen aggregate source used by the old full report has been rehashed and each slot joined to the same input/answer/score provenance. '
                       'Normal-answer API request/response files were not individually rescanned or downloaded; their existing audit hashes are preserved as inherited evidence. '
                       'The 238 excluded-answer terminal files were rehashed/read. No assertion is made that all original per-attempt API receipts were newly re-audited.',
                       sources=sources.entries, files={name:dict(rows=output_counts[name],sha256=output_hashes[name].hexdigest()) for name in streams})
    finally:
        for stream in streams.values():
            stream.flush(); os.fsync(stream.fileno()); stream.close()
    for name, path in temporary.items():
        results['files'][name]['bytes'] = path.stat().st_size
        os.link(path, output/name)
        path.unlink()
    with (output/'manifest.json').open('x',encoding='utf-8') as stream:
        json.dump(results,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps({k:v for k,v in results.items() if k not in ['sources']},ensure_ascii=False,indent=2))
    return results


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUT)
    args = parser.parse_args()
    prepare(args.output)
