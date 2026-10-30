"""Prepare 128 paired, blinded sentence-anchor diagnostics; no network calls."""
from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
OLD = REPO / 'data/pkulaw_v4_autotune_20261002/prepared'
OUT = REPO / 'data/pkulaw_admin_ds_sentence_retry_20261003/prepared'
OLD_HASH = '5b7ce82cd05936b70605a946b495d0bc6023b7b8eab3ce776f38c20a4cd17506'
OLD_MAP_HASH = '4c78e94b1e3b9cc6377ed9f320e9240542a318adb14a27453ff24981cf3ff626'
OLD_REMOTE = '/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_benchmark_20261003_c128'
CODE = {
    'runner.py': REPO / 'scripts/cluster/admin_ds_sentence_retry_20261003/runner.py',
    'bench_external.py': REPO / 'scripts/cluster/admin_ds_benchmark_20261003/bench_external.py',
    'bench.py': REPO / 'scripts/cluster/v4_flash_20261002_autotune/bench.py',
    'contract.py': REPO / 'scripts/hallucination_h1_h5_20261002/contract.py',
    'sentence_contract.py': REPO / 'scripts/hallucination_h1_h5_sentence_ids_20261003/contract.py',
}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def select(mapping):
    groups = defaultdict(list)
    for row in mapping:
        groups[row['split'], row['answer_condition']].append(row)
    assert len(groups) == 10
    chosen, counts = [], []
    for i, (group, rows) in enumerate(sorted(groups.items())):
        count = 13 if i < 8 else 12
        subset = sorted(rows, key=lambda r: sha(('sentence-id-pilot-v1:' + r['request_id']).encode()))[:count]
        assert len(subset) == count
        chosen.extend(subset)
        counts.append({'split': group[0], 'answer_condition': group[1], 'selected': count})
    chosen.sort(key=lambda r: sha(('sentence-id-pilot-order-v1:' + r['request_id']).encode()))
    return chosen, counts


def prepare():
    assert sha((OLD / 'requests.jsonl').read_bytes()) == OLD_HASH
    assert sha((OLD / 'private_mapping.jsonl').read_bytes()) == OLD_MAP_HASH
    original = {r['request_id']: r for r in map(json.loads, (OLD / 'requests.jsonl').read_text().splitlines())}
    mapping = list(map(json.loads, (OLD / 'private_mapping.jsonl').read_text().splitlines()))
    assert len(original) == len(mapping) == 1024
    assert set(original) == {r['request_id'] for r in mapping}
    selected, counts = select(mapping)
    spec = importlib.util.spec_from_file_location('sentence_contract_prepare', CODE['sentence_contract.py'])
    contract = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(contract)
    requests, provenance = [], []
    for mapped in selected:
        old = original[mapped['request_id']]
        packet = json.loads(old['messages'][1]['content'])
        assert packet['answer'] == old['answer']
        assert sha(canonical(packet)) == old['packet_sha256'] == mapped['packet_sha256']
        assert sha(old['answer'].encode()) == mapped['answer_sha256']
        messages, anchored_packet = contract.build_messages_from_packet(packet)
        assert json.loads(messages[1]['content']) == anchored_packet
        for key in ('case_text', 'question', 'judge_decision', 'reference_pack'):
            assert anchored_packet[key] == packet[key]
        table = contract.build_sentences(old['answer'])
        contract.validate_sentences(old['answer'], table)
        requests.append({'request_id': old['request_id'], 'messages': messages, 'answer': old['answer'],
                         'sentences': table, 'packet_sha256': sha(messages[1]['content'].encode()),
                         'answer_sha256': mapped['answer_sha256']})
        provenance.append({**mapped, 'legacy_packet_sha256': old['packet_sha256'],
                           'sentence_packet_sha256': requests[-1]['packet_sha256']})
    assert len(requests) == len({r['request_id'] for r in requests}) == 128
    raw = b''.join(canonical(r) + b'\n' for r in requests)
    manifest = {'created_at': datetime.now(timezone.utc).isoformat(), 'protocol_id': 'dvj-H1-H5-sentence-id-pilot-v2',
                'request_count': 128, 'requests_per_endpoint': 128, 'input_sha256': sha(raw),
                'packet_sha256_semantics': 'SHA256 of exact user message UTF-8 content',
                'source_requests_sha256': OLD_HASH, 'source_mapping_sha256': OLD_MAP_HASH,
                'code_sha256': {name: sha(path.read_bytes()) for name, path in CODE.items()},
                'previous_run_root': OLD_REMOTE, 'group_counts': counts,
                'selection': 'Fixed salted SHA256 rank within 10 split/source strata; independent of old outcome',
                'endpoints': ['http://10.33.4.146:18044/v1', 'http://10.33.4.152:18044/v1'],
                'concurrency_per_endpoint': 128, 'parameters': {'model': 'deepseek_v4_flash', 'temperature': 0.1,
                    'max_tokens': 3000, 'chat_template_kwargs': {'thinking': False}},
                'retries': 0, 'request_deadline_s': 1800, 'reference_ready': False,
                'formal_hallucination_statistics_allowed': False, 'original_answers_unchanged': True,
                'taxonomy_sha256': sha((REPO / 'prompts/hallucination_h1_h5_20261002/taxonomy_user.txt').read_bytes()),
                'prompt_sha256': sha(messages[0]['content'].encode()),
                'paired_comparison': 'Same 128 request IDs on each endpoint; compare to corresponding old receipts only'}
    if OUT.exists():
        prior = json.loads((OUT / 'manifest.json').read_text())
        assert prior['input_sha256'] == manifest['input_sha256'] and prior['code_sha256'] == manifest['code_sha256'], 'Existing prepared batch is frozen'
        assert sha((OUT / 'requests.jsonl').read_bytes()) == prior['input_sha256']
        print(json.dumps({'status': 'already_prepared', 'manifest': prior}, ensure_ascii=False, indent=2))
        return
    OUT.mkdir(parents=True, exist_ok=False)
    with (OUT / 'requests.jsonl').open('xb') as f:
        f.write(raw)
    with (OUT / 'private_mapping.jsonl').open('xb') as f:
        f.write(b''.join(canonical(r) + b'\n' for r in provenance))
    with (OUT / 'manifest.json').open('x') as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps({'status': 'prepared_not_sent', 'manifest': manifest}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    prepare()
