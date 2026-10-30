"""Freeze balanced H1-H5 performance inputs, without making any model calls.

These packets deliberately contain no independently verified external evidence.
Their outputs are performance diagnostics, never formal hallucination statistics.
Only selected rows are retained while streaming the full corpus.
"""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import heapq
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.hallucination_h1_h5_20261002.contract import (
    build_messages, canonical, packet_hash, prompt_hashes, validate_packet,
)

DATA = ROOT / 'data/pkulaw_hallucination_audit_20261002'
OUTPUT = ROOT / 'data/pkulaw_v4_autotune_20261002/prepared'
SLOTS = 1024
PROTOCOL = 'dvj-hallucination-H1-H5-v1'
PARAMETERS = {
    'model': 'deepseek_v4_flash', 'temperature': 0.1,
    'thinking': False, 'max_tokens': 3000, 'retries': 0,
    'length_retry': False, 'request_timeout_seconds': 1800,
}
IDENTITY_FIELDS = ('split', 'case_id', 'question_id', 'answer_condition')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def file_sha(path):
    h = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def identity(row):
    value = tuple(row.get(field) for field in IDENTITY_FIELDS)
    require(all(isinstance(x, str) and x for x in value), 'Missing source identity')
    return value


def identity_hash(key):
    return hashlib.sha256(canonical(list(key))).hexdigest()


def group_targets(conditions, slots=SLOTS):
    groups = sorted((split, model) for split in ('primary', 'secondary') for model in conditions)
    require(len(groups) == len(set(groups)) and bool(groups), 'Duplicate or absent groups')
    base, extra = divmod(slots, len(groups))
    require(base > 0, 'Not enough slots for balanced groups')
    return {group: base + (i < extra) for i, group in enumerate(groups)}


class DiverseTopN:
    """Lowest identity hashes subject to one row per case; bounded selected payloads.

    Each case's best row competes for the group's top N. Lazy heap entries hold
    only a rank and case ID, not discarded payloads, and are regularly compacted.
    """

    def __init__(self, size):
        require(type(size) is int and size > 0, 'Invalid sample size')
        self.size = size
        self.rows = {}
        self.heap = []

    def _trim(self):
        while self.heap:
            neg, case_id = self.heap[0]
            current = self.rows.get(case_id)
            if current is not None and current[0] == -neg:
                break
            heapq.heappop(self.heap)
        if len(self.heap) > 2 * self.size:
            self.heap = [(-rank, case_id) for case_id, (rank, _) in self.rows.items()]
            heapq.heapify(self.heap)

    def add(self, rank, case_id, row):
        self._trim()
        current = self.rows.get(case_id)
        if current is not None:
            if rank >= current[0]:
                return
        elif len(self.rows) >= self.size:
            worst_rank, worst_case = self.heap[0]
            if rank >= -worst_rank:
                return
            heapq.heappop(self.heap)
            del self.rows[worst_case]
        self.rows[case_id] = (rank, row)
        heapq.heappush(self.heap, (-rank, case_id))
        self._trim()

    def selected(self):
        return [row for _, row in sorted(self.rows.values(), key=lambda item: item[0])]


def compact_row(row, line_number):
    key = identity(row)
    require(row.get('main_denominator_eligible') is True, 'Ineligible answer in complete corpus')
    result = {'identity': key, 'source_line': line_number}
    field_map = {
        'case_text': 'case_text_masked', 'question': 'question_text',
        'judge_decision': 'judicial_reference', 'answer': 'answer',
    }
    for target, source in field_map.items():
        value = row.get(source)
        require(isinstance(value, str) and value.strip(), 'Missing substantive ' + source)
        result[target] = value
    hashes = {
        'case_text': 'masked_case_text_hash', 'question': 'question_hash',
        'judge_decision': 'judge_decision_hash', 'answer': 'answer_sha256',
    }
    for target, source in hashes.items():
        digest = hashlib.sha256(result[target].encode('utf-8')).hexdigest()
        require(row.get(source) == digest, 'Source field hash mismatch: ' + source)
        result[source] = digest
    return result


def active_configuration():
    path = DATA / 'ACTIVE_PROTOCOL.json'
    active = json.loads(path.read_text(encoding='utf-8'))
    require(active['protocol_id'] == PROTOCOL, 'Unexpected active scientific protocol')
    require(active['categories'] == ['H1', 'H2', 'H3', 'H4', 'H5'], 'Unexpected categories')
    require(active['complete_answers'] == 55236, 'Unexpected full corpus count')
    require(active['old_20_subtype_packets_allowed_for_new_runs'] is False, 'Old prompt not disabled')
    require(prompt_hashes() == active['prompt_sha256'], 'Frozen prompt hash mismatch')
    require((ROOT / active['input_path']).resolve() == (DATA / 'answers.jsonl').resolve(),
            'Unexpected input source')
    require((ROOT / active['excluded_path']).resolve() == (DATA / 'excluded_answers.jsonl').resolve(),
            'Unexpected excluded source')
    return active


def sample_source(active):
    targets = group_targets(active['answer_conditions'])
    require(len(targets) == 10 and sum(targets.values()) == SLOTS, 'Unexpected stratum allocation')
    selectors = {group: DiverseTopN(size) for group, size in targets.items()}
    counts = Counter()
    seen = set()
    digest = hashlib.sha256()
    source_count = 0
    with (ROOT / active['input_path']).open('rb') as source:
        for line_number, line in enumerate(source, 1):
            digest.update(line)
            require(bool(line.strip()), 'Blank source line')
            row = json.loads(line)
            key = identity(row)
            require(key not in seen, 'Duplicate complete-corpus identity')
            seen.add(key)
            group = key[0], key[3]
            require(group in selectors, 'Unexpected split/model group')
            counts[group] += 1
            source_count += 1
            # Retain only the selected raw text, not provenance trees or scores.
            compact = compact_row(row, line_number)
            selectors[group].add(int(identity_hash(key), 16), key[1], compact)
    require(source_count == active['complete_answers'], 'Full corpus count mismatch')
    require(digest.hexdigest() == active['input_sha256'], 'Frozen complete corpus changed')
    require(file_sha(ROOT / active['excluded_path']) == active['excluded_sha256'],
            'Frozen exclusions changed; never restore excluded rows')
    selected = []
    for group, selector in selectors.items():
        require(len(selector.rows) == targets[group], 'Not enough distinct cases in ' + str(group))
        selected.extend(selector.selected())
    selected.sort(key=lambda row: identity_hash(row['identity']))
    return selected, counts, targets, source_count


def verify_existing(output, active):
    manifest = json.loads((output / 'manifest.json').read_text(encoding='utf-8'))
    require(manifest['benchmark_only'] is True and manifest['reference_ready'] is False
            and manifest['formal_hallucination_statistics_allowed'] is False,
            'Existing artifact is not an evidence-unready performance diagnostic')
    require(manifest['parameters'] == PARAMETERS and manifest['slots'] == SLOTS,
            'Existing benchmark configuration differs')
    require(manifest['input_sha256'] == active['input_sha256']
            and manifest['prompt_sha256'] == prompt_hashes()
            and manifest['full_corpus_count'] == active['complete_answers'], 'Existing provenance differs')
    require(file_sha(ROOT / active['input_path']) == active['input_sha256'], 'Frozen input changed')
    require(file_sha(ROOT / active['excluded_path']) == active['excluded_sha256'], 'Frozen exclusions changed')
    for name, expected in manifest['output_sha256'].items():
        require(name in ('requests.jsonl', 'private_mapping.jsonl'), 'Unexpected artifact filename')
        require(file_sha(output / name) == expected, 'Prepared artifact hash mismatch: ' + name)
    require(set(manifest['output_sha256']) == {'requests.jsonl', 'private_mapping.jsonl'},
            'Incomplete output hash inventory')
    mapping = {}
    with (output / 'private_mapping.jsonl').open(encoding='utf-8') as source:
        for line in source:
            row = json.loads(line)
            request_id = row['request_id']
            require(request_id not in mapping, 'Duplicate private mapping')
            require(request_id == 'perf_' + identity_hash(identity(row)), 'Private identity mismatch')
            mapping[request_id] = row
    request_ids = set()
    group_counts = Counter()
    cases = set()
    with (output / 'requests.jsonl').open(encoding='utf-8') as source:
        for line in source:
            row = json.loads(line)
            require(set(row) == {'request_id', 'messages', 'answer', 'packet_sha256'},
                    'Unexpected public request fields')
            request_id = row['request_id']
            require(request_id in mapping and request_id not in request_ids, 'Request/mapping mismatch')
            request_ids.add(request_id)
            require(len(row['messages']) == 2 and row['messages'][1]['role'] == 'user', 'Bad messages')
            packet = validate_packet(json.loads(row['messages'][1]['content']))
            expected_messages, _ = build_messages(**packet)
            require(row['messages'] == expected_messages, 'Exact prompt/payload mismatch')
            require(packet['reference_pack'] == {'evidence': [], 'checklists': [], 'coverage': {}},
                    'Unexpected independent reference evidence')
            require(row['packet_sha256'] == packet_hash(packet) and row['answer'] == packet['answer'],
                    'Packet/answer mismatch')
            meta = mapping[request_id]
            require(hashlib.sha256(row['answer'].encode('utf-8')).hexdigest() == meta['answer_sha256'],
                    'Private answer hash mismatch')
            group = meta['split'], meta['answer_condition']
            group_counts[group] += 1
            case_key = group + (meta['case_id'],)
            require(case_key not in cases, 'Repeated case within a stratum')
            cases.add(case_key)
    require(len(request_ids) == SLOTS and request_ids == set(mapping), 'Prepared count/coverage mismatch')
    require(dict(group_counts) == group_targets(active['answer_conditions']), 'Prepared group imbalance')
    return manifest


def prepare(output=OUTPUT):
    output = Path(output).resolve()
    active = active_configuration()
    if output.exists():
        return verify_existing(output, active)
    selected, counts, targets, count = sample_source(active)
    require(prompt_hashes() == active['prompt_sha256'], 'Prompt changed during preparation')
    # Creation is exclusive. An interrupted directory is not overwritten on retry.
    output.mkdir(parents=True, exist_ok=False)
    with (output / 'requests.jsonl').open('x', encoding='utf-8') as requests, \
            (output / 'private_mapping.jsonl').open('x', encoding='utf-8') as private:
        for row in selected:
            key = row['identity']
            request_id = 'perf_' + identity_hash(key)
            messages, packet = build_messages(**{name: row[name] for name in
                ('case_text', 'question', 'judge_decision', 'answer')})
            request = {'request_id': request_id, 'messages': messages,
                       'answer': row['answer'], 'packet_sha256': packet_hash(packet)}
            mapping = dict(zip(IDENTITY_FIELDS, key))
            mapping.update(request_id=request_id, source_line=row['source_line'],
                           packet_sha256=request['packet_sha256'])
            mapping.update({field: row[field] for field in (
                'masked_case_text_hash', 'question_hash', 'judge_decision_hash', 'answer_sha256')})
            requests.write(json.dumps(request, ensure_ascii=False, separators=(',', ':')) + '\n')
            private.write(json.dumps(mapping, ensure_ascii=False, sort_keys=True) + '\n')
    require(prompt_hashes() == active['prompt_sha256'], 'Prompt changed while building requests')
    manifest = {
        'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'protocol_id': PROTOCOL, 'benchmark_protocol_id': 'dvj-v4-autotune-h1-h5-performance-v1',
        'state': 'offline_prepared_not_sent', 'model_calls': 0, 'slots': SLOTS,
        'full_corpus_count': count, 'input_path': active['input_path'],
        'input_sha256': active['input_sha256'], 'excluded_path': active['excluded_path'],
        'excluded_sha256': active['excluded_sha256'],
        'active_protocol_sha256': file_sha(DATA / 'ACTIVE_PROTOCOL.json'),
        'prompt_sha256': prompt_hashes(), 'benchmark_only': True,
        'reference_ready': False, 'formal_hallucination_statistics_allowed': False,
        'parameters': PARAMETERS,
        'parameters_are_engineering_benchmark_not_formal_legal_protocol': True,
        'selection': 'Lowest SHA256 of [split,case_id,question_id,answer_condition], '
                     'retaining at most one question per case in each of ten strata; '
                     '102 slots per stratum plus one for the first four lexicographic strata.',
        'identical_requests_required_across_concurrency_stages': True,
        'selected_unique_cases': len({(r['identity'][0], r['identity'][1]) for r in selected}),
        'group_counts': [{'split': g[0], 'answer_condition': g[1],
                          'source_count': counts[g], 'selected_count': targets[g],
                          'selected_unique_cases': targets[g]} for g in sorted(targets)],
        'output_sha256': {name: file_sha(output / name) for name in
                          ('requests.jsonl', 'private_mapping.jsonl')},
        'notes': [
            'Do not merge diagnostic output, NONE, or event arrays into formal hallucination statistics.',
            'No external law/reference pack has been verified; structural parse success is not legal truth.',
            'No source answers, scoring data, exclusion data or scientific prompts were modified.',
            'No model, network, or remote operations are performed by this preparation script.',
            'Each concurrency stage reuses these frozen payloads; no retries or length-budget escalation.',
        ],
    }
    with (output / 'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    return verify_existing(output, active)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUTPUT)
    result = prepare(parser.parse_args().output)
    print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
