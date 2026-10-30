"""Prepare a deterministic, model-blind 100-slot pilot; never send requests."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from collections import Counter
from pathlib import Path

from judge_contract import build_messages, prompt_hashes, require

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / 'data/pkulaw_hallucination_audit_20261002'
SEED = 'dvj-hallucination-smoke-20261002-v1'


def rows(path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def key(row):
    return (row['split'], row['case_id'], row['question_id'], row['answer_condition'])


def select_cases(case_keys):
    selected = []
    for split in ['primary', 'secondary']:
        candidates = [pair for pair in case_keys if pair[0] == split]
        require(len(candidates) >= 2, 'Not enough cases in ' + split)
        candidates.sort(key=lambda pair: (hashlib.sha256(
            (SEED + '\n' + split + '\n' + pair[1]).encode()).hexdigest(), pair[1]))
        selected.extend(candidates[:2])
    return set(selected)


def prepare(output):
    require(not output.exists(), 'Refuse to overwrite pilot directory')
    paths = [DATA / 'answers.jsonl', DATA / 'excluded_answers.jsonl']
    cases = set()
    # Select using IDs only, without scores, answer text or model success rates.
    for path in paths:
        for row in rows(path):
            cases.add((row['split'], row['case_id']))
    require(Counter(split for split, _ in cases) == {'primary': 215, 'secondary': 2004},
            'Unexpected corpus split/case counts')
    selected = select_cases(cases)
    sample = []
    for path in paths:
        for row in rows(path):
            if (row['split'], row['case_id']) in selected:
                sample.append(row)
    require(len(sample) == 100 and len({key(r) for r in sample}) == 100,
            'Pilot must cover exactly 100 distinct slots')
    output.mkdir(parents=True)
    counts = Counter()
    with (output / 'requests.jsonl').open('x', encoding='utf-8') as requests, \
         (output / 'private_mapping.jsonl').open('x', encoding='utf-8') as mapping:
        for row in sorted(sample, key=key):
            ident = hashlib.sha256((SEED + '\n' + json.dumps(key(row))).encode()).hexdigest()[:24]
            m = {'audit_id': ident, 'key': list(key(row)), 'status': row['status'],
                 'answer_sha256': row.get('answer_sha256'), 'sources': row.get('sources'),
                 'main_denominator_eligible': row['main_denominator_eligible']}
            mapping.write(json.dumps(m, ensure_ascii=False) + '\n')
            if not row['main_denominator_eligible']:
                counts['excluded_slots'] += 1
                continue
            messages, _ = build_messages(row['case_text_masked'], row['question_text'],
                                         row['judicial_reference'], row['answer'])
            requests.write(json.dumps({'audit_id': ident, 'messages': messages}, ensure_ascii=False) + '\n')
            counts['candidate_requests'] += 1
    manifest = {
        'created_at': dt.datetime.now(dt.timezone.utc).isoformat(),
        'state': 'offline_prepared_not_sent_missing_external_evidence',
        'seed': SEED, 'selection': 'two cases per split by seeded SHA256 ordering, independent of answers/scores',
        'selected_cases': [list(pair) for pair in sorted(selected)],
        'slots': 100, 'counts': dict(counts), 'model_calls': 0,
        'scope': 'Only the newly requested hallucination audit; not original108 classification',
        'input_sha256': {str(p.relative_to(ROOT)): sha(p) for p in paths},
        'prompt_sha256': prompt_hashes(),
        'output_sha256': {name: sha(output / name) for name in ['requests.jsonl', 'private_mapping.jsonl']},
        'reference_pack': 'Empty. No external evidence/checklists have been verified for this pilot.',
        'cannot_claim': 'No hallucination decisions, no complete 20-category verdicts, no human validation.',
        'blinding': 'Model identity and historical scores excluded from judge metadata. Answer text stays verbatim; any self-identification in it remains.'
    }
    with (output / 'manifest.json').open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return manifest


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=DATA / 'smoke_prepared_v1')
    print(json.dumps(prepare(parser.parse_args().output.resolve()), ensure_ascii=False, indent=2))
