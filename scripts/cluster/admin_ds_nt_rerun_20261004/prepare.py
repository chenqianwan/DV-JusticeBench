"""Build an independent paired-input replication from verified frozen sources.

Runs locally and remotely using the same bytes; performs no model calls.
"""
import argparse
from collections import Counter
import hashlib
import importlib.util
import json
import os
from pathlib import Path

RUN_ID = 'pkulaw_admin_ds_nt_rerun_20261004'
ENDPOINTS = {'ep146': 'http://10.33.4.146:18044/v1', 'ep152': 'http://10.33.4.152:18044/v1'}
SOURCE_HASHES = {
    'input_tasks.json': '046f6f374a2d6f64dca1df6ee7044a3c532e6a4b95e01474ee47d797a8a6f5e9',
    'task_mapping.jsonl': 'ac70b5739bd2fa471441ff8c970709c15643df16030b831ed8a19d9136e969e2',
    'excluded_cases.json': 'f936878f02a61aa99fcf10eb94efc23155066bba2c42d0ab532a772cdd4ced8a',
    'legacy_scoring_bundle.py': '6af07139a06994b3192e450e5fa79f23e69851b889e999a99187efaabc81d9ad',
}
CODE_NAMES = ('runner.py', 'manager.py', 'prepare.py', 'run_answer_smoke.py',
              'run_answer_batch128.py', 'benchmark_concurrency.py',
              'run_selfscore_smoke.py', 'run_selfscore_batch128_v2.py')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def key(row):
    return tuple(row[n] for n in ('split', 'case_id', 'question_id'))


def write_once(path, raw):
    if path.is_symlink() or path.resolve() != path.absolute():
        raise ValueError('Symlinked artifact path')
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if path.exists():
        if path.read_bytes() != raw:
            raise ValueError('Refusing to overwrite frozen artifact: ' + str(path))
        return
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'wb') as f:
        f.write(raw)
        f.flush()
        os.fsync(f.fileno())


def build(source, root, code_paths, expected_manifest=None):
    source, root = Path(source).absolute(), Path(root).absolute()
    if root.resolve() != root or source.resolve() != source:
        raise ValueError('Symlinked source/output root')
    if (root / 'launches').exists():
        raise ValueError('Preparation cannot run over launched work')
    inputs = {}
    for name, expected in SOURCE_HASHES.items():
        raw = (source / name).read_bytes()
        if sha(raw) != expected:
            raise ValueError('Source changed: ' + name)
        inputs[name] = raw
    original = json.loads(inputs['input_tasks.json'])
    tasks = original['tasks']
    mapping = [json.loads(l) for l in inputs['task_mapping.jsonl'].splitlines() if l.strip()]
    by_key = {key(r): r for r in mapping}
    if len(tasks) != 11095 or len(by_key) != 11095 or set(by_key) != {key(t) for t in tasks}:
        raise ValueError('Expected complete 11095-question corpus, including ten original pilot questions')
    if Counter(t['split'] for t in tasks) != {'primary': 1075, 'secondary': 10020}:
        raise ValueError('Split coverage differs')
    groups = Counter(key(t)[:2] for t in tasks)
    if set(groups.values()) != {5}:
        raise ValueError('Expected five questions per case')
    excluded = json.loads(inputs['excluded_cases.json'])
    dirty = {r['case_id'] for r in excluded if r['permanently_excluded'] is True}
    if len(dirty) != 18 or any(t['case_id'] in dirty for t in tasks):
        raise ValueError('Permanent exclusion violation')
    spec = importlib.util.spec_from_file_location('original_scoring_bundle', source / 'legacy_scoring_bundle.py')
    bundle = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(bundle)
    for t in tasks:
        m = by_key[key(t)]
        if t['messages'] != m['answer_messages'] or t['messages'] != bundle.analysis_messages(m['case_text_masked'], m['question_text']):
            raise ValueError('Answer prompt is not original frozen prompt')
        if sha(canonical(t['messages'])) != t['prompt_hash']:
            raise ValueError('Prompt hash mismatch')
        for name, text in [('masked_case_text_hash', m['case_text_masked']), ('judge_decision_hash', m['judge_decision_masked']), ('question_hash', m['question_text'])]:
            if sha(text.encode()) != m[name]:
                raise ValueError('Reference text hash mismatch: ' + name)
    assigned = {}
    for split in ('primary', 'secondary'):
        cases = sorted((c for s, c in groups if s == split),
                       key=lambda c: sha(('independent-nt-rerun-v1:' + split + ':' + c).encode()))
        for i, case in enumerate(cases):
            assigned[split, case] = ('ep146', 'ep152')[i % 2]
    artifact_raw = {'prepared/excluded_cases.json': inputs['excluded_cases.json'],
                    'legacy_scoring_bundle.py': inputs['legacy_scoring_bundle.py']}
    shards = {}
    for ep, url in ENDPOINTS.items():
        selected = sorted((t for t in tasks if assigned[key(t)[:2]] == ep), key=key)
        config = dict(run_id=RUN_ID, service_job_id='admin_managed', base_url=url,
                      model='deepseek_v4_flash', thinking=False, scoring_thinking=True,
                      temperature=.3, initial_max_tokens=3000, timeout_s=1800, workers=128)
        value = dict(config, tasks=selected, source_manifest_sha256=SOURCE_HASHES['input_tasks.json'])
        input_name = f'prepared/{ep}/input_tasks.json'
        mapping_name = f'prepared/{ep}/task_mapping.jsonl'
        artifact_raw[input_name] = canonical(value) + b'\n'
        artifact_raw[mapping_name] = b''.join(canonical(by_key[key(t)]) + b'\n' for t in selected)
        shards[ep] = dict(base_url=url, input_path=input_name, mapping_path=mapping_name,
                          input_sha256=sha(artifact_raw[input_name]), mapping_sha256=sha(artifact_raw[mapping_name]),
                          count=len(selected), split_counts=dict(Counter(t['split'] for t in selected)))
    if sorted(s['count'] for s in shards.values()) != [5545, 5550]:
        raise ValueError('Endpoint partition mismatch')
    code_hashes = {name: sha(Path(code_paths[name]).read_bytes()) for name in CODE_NAMES}
    manifest = dict(run_id=RUN_ID, total_questions=11095, case_counts={'primary':215, 'secondary':2004},
                    bundle_sha256=SOURCE_HASHES['legacy_scoring_bundle.py'], code_sha256=code_hashes,
                    excluded_cases={'path':'prepared/excluded_cases.json', 'sha256':SOURCE_HASHES['excluded_cases.json']},
                    shards=shards, source_sha256=SOURCE_HASHES,
                    source_role='Same frozen cases/questions as prior two DS answer conditions; no old answers reused',
                    trial_role='Independent replication; retain both old and new outcomes; no result selection',
                    retry_contract='legacy108_coupled', reused_answers=0, answer_thinking=False,
                    scoring_thinking=True, per_request_seed=None, temperature=.3,
                    initial_max_tokens=3000, length_retry_max_tokens=6000,
                    endpoint_model_revision_verified=False,
                    model_deployment_note='Administrator-managed services, not original dgx-040 deployment; retain endpoint metadata',
                    artifacts={n:sha(raw) for n,raw in artifact_raw.items()})
    manifest_raw = canonical(manifest) + b'\n'
    if expected_manifest and Path(expected_manifest).read_bytes() != manifest_raw:
        raise ValueError('Remote preparation differs from locally frozen manifest')
    for name, raw in artifact_raw.items():
        write_once(root / name, raw)
    write_once(root / 'prepared/manifest.json', manifest_raw)
    return manifest


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--source-root', type=Path, required=True)
    p.add_argument('--root', type=Path, required=True)
    p.add_argument('--code-root', type=Path, required=True)
    p.add_argument('--expected-manifest', type=Path)
    a=p.parse_args()
    os.umask(0o077)
    manifest=build(a.source_root,a.root,{n:a.code_root/n for n in CODE_NAMES},a.expected_manifest)
    print(json.dumps({'run_id':manifest['run_id'],'questions':manifest['total_questions'],'shards':manifest['shards']}))


if __name__=='__main__':main()
