"""Rebuild the same 100 offline pilot slots with the active H1-H5 prompt; no API."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from scripts.hallucination_h1_h5_20261002.contract import build_messages, prompt_hashes

DATA = ROOT / 'data/pkulaw_hallucination_audit_20261002'
OLD = DATA / 'smoke_prepared_v1'


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def convert(old_request):
    messages = old_request['messages']
    if len(messages) != 2 or messages[1]['role'] != 'user':
        raise ValueError('Unexpected old packet')
    packet = json.loads(messages[1]['content'])
    if set(packet) != {'case_text', 'question', 'judge_decision', 'answer', 'reference_pack'}:
        raise ValueError('Unexpected packet fields')
    new_messages, new_packet = build_messages(**packet)
    if packet != new_packet:
        raise ValueError('Answer/evidence input was changed')
    return {'audit_id': old_request['audit_id'], 'protocol_id': 'dvj-hallucination-H1-H5-v1',
            'messages': new_messages}


def prepare(output):
    if output.exists():
        raise ValueError('Refuse to overwrite any prepared pilot')
    active = json.loads((DATA / 'ACTIVE_PROTOCOL.json').read_text())
    if active['protocol_id'] != 'dvj-hallucination-H1-H5-v1' or prompt_hashes() != active['prompt_sha256']:
        raise ValueError('Active prompt does not match frozen H1-H5 configuration')
    old_manifest = json.loads((OLD / 'manifest.json').read_text())
    for name, digest in old_manifest['output_sha256'].items():
        if sha(OLD / name) != digest:
            raise ValueError('Frozen pilot source changed: ' + name)
    for name, digest in [('answers.jsonl', active['input_sha256']),
                         ('excluded_answers.jsonl', active['excluded_sha256'])]:
        if sha(DATA / name) != digest:
            raise ValueError('Frozen full corpus changed: ' + name)
    old_rows = [json.loads(line) for line in (OLD / 'requests.jsonl').read_text().splitlines() if line]
    requests = [convert(row) for row in old_rows]
    if len(requests) != 100 or len({r['audit_id'] for r in requests}) != 100:
        raise ValueError('Expected exactly 100 distinct offline requests')
    output.mkdir(parents=True)
    with (output / 'requests.jsonl').open('x', encoding='utf-8') as stream:
        for row in requests:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
    with (output / 'private_mapping.jsonl').open('xb') as stream:
        stream.write((OLD / 'private_mapping.jsonl').read_bytes())
    manifest = {
        'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'protocol_id': active['protocol_id'], 'state': 'offline_prepared_not_sent',
        'full_scope_complete_answers': active['complete_answers'],
        'slots': 100, 'model_calls': 0, 'selected_cases': old_manifest['selected_cases'],
        'selection': 'Same ID-only deterministic 100-slot pilot; does not replace the full audit.',
        'source_manifest_sha256': sha(OLD / 'manifest.json'),
        'active_protocol_sha256': sha(DATA / 'ACTIVE_PROTOCOL.json'),
        'input_sha256': old_manifest['input_sha256'],
        'prompt_sha256': prompt_hashes(),
        'output_sha256': {n: sha(output / n) for n in ['requests.jsonl', 'private_mapping.jsonl']},
        'reference_ready': False, 'ready_for_model_calls': False,
        'blocking': ['Independent reference verification', 'Healthy cluster service', 'H1-H5 smoke acceptance'],
        'old_20_category_prompt_sent': False,
    }
    with (output / 'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DATA / 'smoke_prepared_h1_h5_v1')
    print(json.dumps(prepare(parser.parse_args().output.resolve()), ensure_ascii=False, indent=2))
