"""Stream the frozen full answer corpus into disjoint sentence-ID audit shards.

No network/model calls. Candidate labels remain provisional until independent
legal-reference verification. Original corpus, answers and diagnostic receipts
are never modified. A pilot answer is reused from its preassigned endpoint,
including exceptional outcomes, never selected by score or parsing success.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))
from scripts.hallucination_h1_h5_sentence_ids_20261003 import contract

DATA = REPO / 'data/pkulaw_hallucination_audit_20261002'
OUT = REPO / 'data/pkulaw_admin_ds_full_h1h5_20261003/prepared'
PILOT = REPO / 'data/pkulaw_admin_ds_sentence_retry_20261003/fetched/20261003T153202489346Z'
SOURCE_HASH = 'f8907a506663fcf5204c7b1a588fca4bad16dc2c942cc62f129cd01252c97aa2'
EXCLUDED_HASH = '325950f643d38aa9fc81497aa8ad7ca5e2619c49d9efdd55b488cfdefd563686'
DIRTY_PATH = REPO / 'data/pkulaw_v4_answer_batch128_20260928/excluded_cases.json'
DIRTY_HASH = 'f936878f02a61aa99fcf10eb94efc23155066bba2c42d0ab532a772cdd4ced8a'
HOSTS = ('10.33.4.146', '10.33.4.152')
KEYS = ('split', 'case_id', 'question_id', 'answer_condition')
TEXT_FIELDS = {'case_text': ('case_text_masked', 'masked_case_text_hash'),
               'question': ('question_text', 'question_hash'),
               'judge_decision': ('judicial_reference', 'judge_decision_hash'),
               'answer': ('answer', 'answer_sha256')}
PARAMETERS = {'model': 'deepseek_v4_flash', 'temperature': 0.1, 'max_tokens': 3000,
              'chat_template_kwargs': {'thinking': False}}
PILOT_INPUT_HASH = '14d558d672aa8e4e7267a7d2b43416badc748c06159fdf5040c18fc108e7f6f2'
CODE = {
    'runner.py': REPO / 'scripts/cluster/admin_ds_full_h1h5_20261003/runner.py',
    'pilot_runner.py': REPO / 'scripts/cluster/admin_ds_sentence_retry_20261003/runner.py',
    'bench_external.py': REPO / 'scripts/cluster/admin_ds_benchmark_20261003/bench_external.py',
    'bench.py': REPO / 'scripts/cluster/v4_flash_20261002_autotune/bench.py',
    'contract.py': REPO / 'scripts/hallucination_h1_h5_20261002/contract.py',
    'sentence_contract.py': Path(contract.__file__),
}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def canonical(value):
    return contract.canonical(value)


def identity(row):
    key = tuple(row.get(name) for name in KEYS)
    require(all(isinstance(x, str) and x for x in key), 'Missing complete identity')
    return key


def request_id(key):
    # Same identity function as the preceding pilots, not a model-facing label.
    return 'perf_' + sha(canonical(list(key)))


def compact(row, index):
    key = identity(row)
    require(row.get('status') == 'complete' and row.get('answer_status') == 'ok'
            and row.get('main_denominator_eligible') is True, 'Ineligible source answer')
    packet = {'reference_pack': {'evidence': [], 'checklists': [], 'coverage': {}}}
    metadata = dict(zip(KEYS, key))
    metadata.update(request_id=request_id(key), global_index=index, source_line=index + 1)
    for target, (source, digest_field) in TEXT_FIELDS.items():
        value = row.get(source)
        require(isinstance(value, str) and value.strip(), 'Missing source text: ' + source)
        require(sha(value.encode()) == row.get(digest_field), 'Source text/hash mismatch: ' + source)
        packet[target] = value
        metadata[digest_field] = row[digest_field]
    contract.validate_packet(packet)
    return packet, metadata


def assign_shards(metadata):
    """Balanced salted ranks per split/condition; neither outcomes nor prose rank."""
    groups = defaultdict(list)
    seen = set()
    for item in metadata:
        rid = item['request_id']
        require(rid not in seen, 'Duplicate identity in shard plan')
        seen.add(rid)
        groups[item['split'], item['answer_condition']].append(rid)
    assignments, counts = {}, []
    next_extra = 0
    for group, ids in sorted(groups.items()):
        ranked = sorted(ids, key=lambda rid: (sha(('full-h1h5-two-endpoint-v1:' + rid).encode()), rid))
        n146 = len(ranked) // 2
        if len(ranked) % 2:
            n146 += next_extra == 0
            next_extra = 1 - next_extra
        for index, rid in enumerate(ranked):
            assignments[rid] = HOSTS[0] if index < n146 else HOSTS[1]
        counts.append({'split': group[0], 'answer_condition': group[1], 'count': len(ranked),
                       HOSTS[0]: n146, HOSTS[1]: len(ranked) - n146})
    totals = Counter(assignments.values())
    require(abs(totals[HOSTS[0]] - totals[HOSTS[1]]) <= 1, 'Unbalanced endpoint plan')
    return assignments, counts


def source_plan(active):
    require(active['input_sha256'] == SOURCE_HASH and active['excluded_sha256'] == EXCLUDED_HASH,
            'Unexpected frozen corpus version')
    require(active['complete_answers'] == 55236 and active['excluded_slots'] == 239,
            'Unexpected authorized scope')
    require(file_sha(DIRTY_PATH) == DIRTY_HASH, 'Permanent-exclusion source changed')
    dirty = {(item['split'], item['case_id']) for item in read(DIRTY_PATH)
             if item.get('permanently_excluded') is True}
    require(len(dirty) == 18, 'Missing permanent 18-case exclusion')
    groups = {(split, condition) for split in ('primary', 'secondary') for condition in active['answer_conditions']}
    require(len(groups) == 10, 'Expected ten split/condition groups')
    metadata, seen, digest, cases = [], set(), hashlib.sha256(), set()
    with (REPO / active['input_path']).open('rb') as source:
        for index, line in enumerate(source):
            digest.update(line)
            require(bool(line.strip()), 'Blank corpus line')
            row = json.loads(line)
            _, meta = compact(row, index)
            key = identity(meta)
            require(key not in seen, 'Duplicate complete source key')
            require((key[0], key[1]) not in dirty, 'Permanent dirty case entered corpus')
            require((key[0], key[3]) in groups, 'Unexpected source split/condition')
            metadata.append(meta)
            seen.add(key)
            cases.add(key[:2])
    require(digest.hexdigest() == SOURCE_HASH, 'Frozen corpus bytes changed')
    require(len(metadata) == 55236 and len(cases) == 2219, 'Full corpus coverage mismatch')
    excluded, digest, statuses = set(), hashlib.sha256(), Counter()
    with (REPO / active['excluded_path']).open('rb') as source:
        for line in source:
            digest.update(line)
            row = json.loads(line)
            key = identity(row)
            require(key not in seen and key not in excluded, 'Excluded slot overlaps or duplicates complete corpus')
            require(key[:2] not in dirty, 'Dirty case in admitted/excluded slot scope')
            excluded.add(key)
            statuses[row['status']] += 1
    require(digest.hexdigest() == EXCLUDED_HASH and len(excluded) == 239, 'Excluded slot corpus changed')
    require(statuses == Counter({'content_filter': 230, 'needs_reconcile': 8, 'truncated': 1}), 'Excluded status changed')
    coverage = Counter((k[0], k[1], k[3]) for k in seen | excluded)
    require(len(coverage) == 2219 * 5 and set(coverage.values()) == {5}, 'Five-question coverage mismatch')
    assignments, counts = assign_shards(metadata)
    require(Counter(assignments.values()) == Counter({h: 27618 for h in HOSTS}), 'Full endpoint shard size mismatch')
    return metadata, assignments, counts, statuses


def pilot_sources(snapshot=PILOT):
    snapshot = Path(snapshot)
    manifest, stage = read(snapshot / 'prepared/manifest.json'), read(snapshot / 'stage_manifest.json')
    require(file_sha(snapshot / 'prepared/requests.jsonl') == PILOT_INPUT_HASH == manifest['input_sha256'],
            'Pilot frozen requests changed')
    require(file_sha(snapshot / 'code/sentence_contract.py') == file_sha(Path(contract.__file__)),
            'Current sentence contract differs from pilot')
    for path, digest in stage['files'].items():
        source = snapshot / path
        require(source.resolve().is_relative_to(snapshot.resolve()), 'Unsafe pilot receipt path')
        require(file_sha(source) == digest, 'Staged pilot evidence changed: ' + path)
    require(file_sha(snapshot / 'baseline_paired.json') == stage['baseline_sha256'], 'Pilot paired baseline changed')
    exit_receipt = read(snapshot / 'results/exit.json')
    require(exit_receipt.get('completed') is True and exit_receipt.get('drained') is True, 'Pilot not completed/drained')
    rows = {}
    with (snapshot / 'prepared/requests.jsonl').open() as source:
        for line in source:
            item = json.loads(line)
            require(item['request_id'] not in rows, 'Duplicate pilot input')
            rows[item['request_id']] = item
    require(len(rows) == 128, 'Pilot is not 128 unique answers')
    terminals = {}
    for host in HOSTS:
        receipt = read(snapshot / 'results' / host / 'exit.json')
        require(receipt.get('completed') is True and receipt.get('drained') is True, 'Pilot endpoint not drained')
        terminals[host] = {}
        for path in (snapshot / 'results' / host / 'requests').glob('*/terminal.json'):
            term = read(path)
            require(term['request_id'] not in terminals[host], 'Duplicate endpoint pilot terminal')
            terminals[host][term['request_id']] = path.parent
        require(set(terminals[host]) == set(rows), 'Pilot endpoint coverage mismatch')
    return rows, terminals


def reused_record(row, host, previous, directory):
    # Check the request before looking at the outcome; host choice is preassigned.
    for key in ('request_id', 'messages', 'answer', 'sentences', 'packet_sha256', 'answer_sha256'):
        require(row[key] == previous[key], 'Pilot/full exact input differs: ' + key)
    directory = Path(directory)
    intent, terminal = read(directory / 'intent.json'), read(directory / 'terminal.json')
    require(intent['request_id'] == row['request_id'] == terminal['request_id'], 'Pilot receipt identity mismatch')
    require(intent['answer_sha256'] == row['answer_sha256'] and intent['packet_sha256'] == row['packet_sha256'],
            'Pilot intent source hashes differ')
    require(intent['payload'] == dict(PARAMETERS, messages=row['messages']), 'Pilot request payload differs')
    http = read(directory / 'response.http.json')
    raw_bytes = (directory / 'response.raw.txt').read_bytes()
    require(http.get('status') == 200 and http.get('body_complete') is True
            and http['raw_sha256'] == sha(raw_bytes), 'Unverified raw pilot response')
    response = json.loads(raw_bytes)
    require(response.get('model') == PARAMETERS['model'] and len(response.get('choices', [])) == 1,
            'Unexpected pilot model response')
    choice = response['choices'][0]
    native = hydrated = None
    if terminal.get('complete'):
        require(choice.get('finish_reason') == 'stop' and not choice['message'].get('refusal'),
                'Complete pilot receipt/response mismatch')
        try:
            native = contract.parse_output(choice['message']['content'], row['answer'], row['sentences'])
        except (ValueError, TypeError, KeyError):
            require(terminal.get('valid_format') is False, 'Pilot valid output failed replay')
        else:
            require(terminal.get('valid_format') is True, 'Pilot invalid output unexpectedly passes replay')
            hydrated = contract.hydrate_output(native, row['answer'], row['sentences'])
            require(read(directory / 'parsed.native.json') == native and read(directory / 'hydrated.json') == hydrated,
                    'Pilot cached output differs from replay')
    else:
        require(terminal.get('status') == 'truncated' and choice.get('finish_reason') == 'length',
                'Only complete or truncated pilot receipts may be reused')
    files = {p.name: file_sha(p) for p in directory.iterdir() if p.is_file()}
    return {'request_id': row['request_id'], 'global_index': row['global_index'], 'host': host,
            'answer_sha256': row['answer_sha256'], 'packet_sha256': row['packet_sha256'],
            'terminal': terminal, 'native': native, 'hydrated': hydrated,
            'response_raw': raw_bytes.decode('utf-8'), 'http': http,
            'provenance': {'source_directory': str(directory), 'source_file_sha256': files,
                'selection': 'Preassigned endpoint only, regardless of outcome; other endpoint excluded',
                'reuse_scope': 'provisional candidate output only', 'reference_ready': False,
                'legal_truth_verified': False, 'formal_hallucination_statistics_allowed': False}}


def prepare(output=OUT):
    output = Path(output).resolve()
    require(not output.exists(), 'Refuse to overwrite frozen or interrupted prepared directory')
    active = read(DATA / 'ACTIVE_PROTOCOL.json')
    require(active['protocol_id'] == 'dvj-hallucination-H1-H5-v1' and active['categories'] == list(contract.CODES),
            'Unexpected active category protocol')
    require(file_sha(contract.TAXONOMY) == active['prompt_sha256']['taxonomy_user.txt'], 'Original taxonomy changed')
    metadata, assignments, groups, excluded_statuses = source_plan(active)
    pilot, pilot_dirs = pilot_sources()
    require(set(pilot) <= set(assignments), 'Pilot outside full corpus')
    output.mkdir(parents=True, exist_ok=False)
    names = {host: 'requests_' + host.rsplit('.', 1)[-1] + '.jsonl' for host in HOSTS}
    digests = {name: hashlib.sha256() for name in (*names.values(), 'private_mapping.jsonl', 'reused.jsonl')}
    counts, reused_counts, reused_statuses, spans = Counter(), Counter(), Counter(), 0
    current_source_hash = hashlib.sha256()
    prompt_digest = None
    def emit(handles, name, value):
        line = canonical(value) + b'\n'
        handles[name].write(line)
        digests[name].update(line)
    with ExitStack() as stack:
        handles = {name: stack.enter_context((output / name).open('xb')) for name in digests}
        source = stack.enter_context((REPO / active['input_path']).open('rb'))
        count = 0
        for index, line in enumerate(source):
            current_source_hash.update(line)
            packet, meta = compact(json.loads(line), index)
            require(index < len(metadata) and meta == metadata[index], 'Source changed between streaming passes')
            messages, delivered = contract.build_messages_from_packet(packet)
            table = delivered['sentence_table']
            contract.validate_sentences(packet['answer'], table)
            require(''.join(x['text'] for x in table['sentences']) == packet['answer'], 'Nonlossless segmentation')
            for field in ('case_text', 'question', 'judge_decision', 'reference_pack'):
                require(delivered[field] == packet[field], 'Scientific input changed: ' + field)
            digest = sha(messages[0]['content'].encode())
            require(prompt_digest is None or prompt_digest == digest, 'Prompt changed during preparation')
            prompt_digest = digest
            rid = meta['request_id']
            host = assignments[rid]
            row = {'request_id': rid, 'global_index': index, 'messages': messages,
                   'answer': packet['answer'], 'sentences': table,
                   'packet_sha256': sha(messages[1]['content'].encode()), 'answer_sha256': meta['answer_sha256']}
            emit(handles, names[host], row)
            emit(handles, 'private_mapping.jsonl', dict(meta, host=host, packet_sha256=row['packet_sha256']))
            if rid in pilot:
                reused = reused_record(row, host, pilot[rid], pilot_dirs[host][rid])
                emit(handles, 'reused.jsonl', reused)
                reused_counts[host] += 1
                reused_statuses['valid' if reused['terminal'].get('valid_format') else reused['terminal']['status']] += 1
            spans += len(table['sentences'])
            counts[host] += 1
            count += 1
            if count % 10000 == 0:
                print(json.dumps({'prepared_rows': count, 'sentence_spans': spans}), flush=True)
    require(count == 55236 and current_source_hash.hexdigest() == SOURCE_HASH, 'Source coverage/hash changed')
    require(sum(reused_counts.values()) == 128, 'Pilot reuse coverage mismatch')
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(),
        'protocol_id': 'dvj-H1-H5-full-sentence-id-provisional-v1',
        'state': 'prepared_not_frozen', 'code_sha256': {},
        'request_count': count, 'full_corpus_count': count, 'cases': 2219,
        'key_fields': list(KEYS), 'excluded_slots': 239, 'excluded_statuses': dict(excluded_statuses),
        'permanently_excluded_cases': 18, 'input_path': active['input_path'], 'input_sha256': SOURCE_HASH,
        'excluded_path': active['excluded_path'], 'excluded_sha256': EXCLUDED_HASH,
        'dirty_cases_path': str(DIRTY_PATH.relative_to(REPO)), 'dirty_cases_sha256': DIRTY_HASH,
        'active_protocol_sha256': file_sha(DATA / 'ACTIVE_PROTOCOL.json'),
        'sentence_contract_sha256': file_sha(Path(contract.__file__)),
        'taxonomy_sha256': file_sha(contract.TAXONOMY), 'prompt_sha256': prompt_digest,
        'packet_sha256_semantics': 'SHA256 of exact user message UTF-8 content',
        'shards': {h: {'path': names[h], 'sha256': digests[names[h]].hexdigest(), 'count': counts[h]} for h in HOSTS},
        'private_mapping': {'path': 'private_mapping.jsonl', 'sha256': digests['private_mapping.jsonl'].hexdigest(), 'count': count},
        'reused': {'path': 'reused.jsonl', 'sha256': digests['reused.jsonl'].hexdigest(), 'count': 128,
                   'by_host': dict(reused_counts), 'outcomes': dict(reused_statuses)},
        'pilot_snapshot': str(PILOT), 'pilot_requests_sha256': PILOT_INPUT_HASH,
        'new_network_requests': count - 128,
        'selection': 'Full corpus; salted identity rank within split/condition, balanced disjoint endpoint halves',
        'reuse_selection': 'Exactly assigned endpoint for each pilot identity, all outcomes retained; no best-of-two',
        'group_counts': groups, 'sentence_spans_validated': spans,
        'endpoints': ['http://' + h + ':18044/v1' for h in HOSTS], 'concurrency_per_endpoint': 128,
        'parameters': PARAMETERS, 'retries': 0, 'length_retry': False, 'request_deadline_s': 1800,
        'provisional': True, 'reference_ready': False, 'legal_truth_verified': False,
        'formal_hallucination_statistics_allowed': False, 'original_answers_unchanged': True,
        'notes': ['No external legal-reference package is present. NONE is not verified absence of hallucination.',
                  'Original H1-H5 taxonomy, sentence prompt, case text, question, judicial reference and final answer are unchanged.',
                  'Full source and all sentence spans validated with bounded-memory streaming; no model/network operations.']}
    with (output / 'manifest.json').open('x') as destination:
        json.dump(manifest, destination, ensure_ascii=False, indent=2, allow_nan=False)
        destination.write('\n')
    return manifest


def freeze_code(output=OUT, code=None):
    """Freeze only this newly prepared artifact after runner tests are complete.

    Refuse code changes once frozen and refuse any modification after staging.
    Hash every emitted artifact anew; source inputs and sentence prompt unchanged.
    """
    output = Path(output).resolve()
    code = CODE if code is None else code
    require(set(code) == set(CODE), 'Expected exact six runtime dependencies')
    require(not (output.parent / 'stage_manifest.json').exists()
            and not (output.parent / 'launch_intent.json').exists(), 'Already staged/launched; manifest immutable')
    manifest = read(output / 'manifest.json')
    require(manifest.get('protocol_id') == 'dvj-H1-H5-full-sentence-id-provisional-v1'
            and manifest.get('provisional') is True
            and manifest.get('reference_ready') is False
            and manifest.get('legal_truth_verified') is False
            and manifest.get('formal_hallucination_statistics_allowed') is False, 'Wrong provisional protocol flags')
    artifacts = list(manifest['shards'].values()) + [manifest['private_mapping'], manifest['reused']]
    for item in artifacts:
        path = output / item['path']
        require(path.parent == output and path.name == item['path'], 'Unsafe prepared artifact path')
        require(file_sha(path) == item['sha256'], 'Prepared artifact changed: ' + item['path'])
        with path.open('rb') as source:
            count = sum(1 for line in source if line.strip())
        require(count == item['count'], 'Prepared artifact line count changed: ' + item['path'])
    hashes = {name: file_sha(path) for name, path in code.items()}
    require(hashes['sentence_contract.py'] == manifest['sentence_contract_sha256'], 'Sentence contract changed')
    if manifest['code_sha256']:
        require(manifest['state'] == 'frozen_not_sent' and manifest['code_sha256'] == hashes,
                'Existing frozen runtime code changed')
        return manifest
    require(manifest['state'] == 'prepared_not_frozen', 'Unexpected freeze transition')
    manifest['code_sha256'] = hashes
    manifest['state'] = 'frozen_not_sent'
    manifest['code_frozen_at'] = datetime.now(timezone.utc).isoformat()
    temporary = output / 'manifest.freeze.tmp'
    with temporary.open('x') as destination:
        json.dump(manifest, destination, ensure_ascii=False, indent=2, allow_nan=False)
        destination.write('\n')
    temporary.replace(output / 'manifest.json')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=OUT)
    parser.add_argument('--freeze-code', action='store_true', help='Verify artifacts and freeze finalized runtime dependencies')
    args = parser.parse_args()
    result = freeze_code(args.output) if args.freeze_code else prepare(args.output)
    print(json.dumps(result, ensure_ascii=False, indent=2))
