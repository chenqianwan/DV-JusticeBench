#!/usr/bin/env python3
"""Read-only precise cluster export plus offline final-result audit.

Hashing/compression execute inside the existing allocation; no inference calls,
resource changes, original-file writes, or per-request evidence claims.
"""
import argparse
from collections import Counter, defaultdict
import datetime as dt
import hashlib
import io
import json
import math
from pathlib import Path
import shlex
import subprocess
import tarfile

import run_three_model_scoring as score

PROJECT = Path(__file__).resolve().parents[3]
SOURCE = PROJECT / 'data/pkulaw_three_model_scoring_20260930'
DESTINATION = SOURCE / 'final_export_20261001'
REMOTE_ROOT = '/aifs4su/hansirui_3rd/chenlong/dvjustice_three_model_scoring_20260930'
SCORES = ['results/scores_' + model + '_' + split + '.jsonl'
          for model in score.ANSWER_MODELS for split in ('primary', 'secondary')]
FILES = SCORES + ['results/summary.json', 'results/run_state.json', 'results/run_identity.json',
                  'dispatches/attempt-002.exit.json', 'dispatches/attempt-002.execution.json',
                  'dispatches/attempt-002.wrapper.json', 'manifest.json']

REMOTE = r'''
import datetime, gzip, hashlib, io, json, os, pathlib, socket, stat, sys, tarfile
root = pathlib.Path(ROOT)
assert os.getuid() == 1083 and os.environ.get('SLURM_JOB_ID') == '402816'
assert socket.gethostname().split('.')[0] == 'dgx-040'
assert root.resolve() == root and not root.is_symlink()
exit_record = json.loads((root / 'dispatches/attempt-002.exit.json').read_bytes())
execution = json.loads((root / 'dispatches/attempt-002.execution.json').read_bytes())
assert exit_record['exit_code'] == 0 and exit_record['runner_identity'] == execution['runner_identity']
summary = json.loads((root / 'results/summary.json').read_bytes())
assert summary['completed'] == 33047 and summary['pending'] == summary['inflight'] == 0
assert summary['needs_reconcile'] == summary['unmatched_intents'] == 0
assert summary['counts'] == {'ok': 32872, 'parse_incomplete': 173, 'truncated': 2}
files = {}
for name in FILES:
    p = root / name
    st = p.lstat()
    assert stat.S_ISREG(st.st_mode) and not p.is_symlink() and st.st_size < 1500000000
    h = hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda: f.read(1048576), b''):
            h.update(block)
    files[name] = {'sha256': h.hexdigest(), 'bytes': st.st_size}
metadata = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(),
            'remote_root': str(root), 'allocation': '402816', 'node': socket.gethostname(),
            'files': files, 'actual_exit_code': exit_record['exit_code'],
            'scope': 'Six final score exports and run metadata; not all per-request receipts'}
with gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', compresslevel=3, mtime=0) as zipped, tarfile.open(fileobj=zipped, mode='w|') as archive:
    raw = (json.dumps(metadata, ensure_ascii=False, indent=2) + '\n').encode()
    info = tarfile.TarInfo('REMOTE_EXPORT_MANIFEST.json')
    info.size, info.mode = len(raw), 0o600
    archive.addfile(info, io.BytesIO(raw))
    for name in FILES:
        p = root / name
        info = archive.gettarinfo(str(p), arcname=name)
        assert info.isfile() and info.size == files[name]['bytes']
        with p.open('rb') as f:
            archive.addfile(info, f)
'''


def require(value, message):
    if not value:
        raise ValueError(message)


def read(path):
    require(path.is_file() and not path.is_symlink(), 'Missing or redirected evidence: ' + str(path))
    return json.loads(path.read_bytes())


def sha(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1048576), b''):
            h.update(block)
    return h.hexdigest()


def write_once(path, value):
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')


def fetch():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    archive = DESTINATION / 'final_exports.tar.gz'
    require(not archive.exists(), 'Existing export archive will not be overwritten')
    code = 'ROOT = ' + repr(REMOTE_ROOT) + '\nFILES = ' + repr(FILES) + '\n' + REMOTE
    command = ['env', 'SLURM_CONF=/cm/shared/apps/slurm/var/etc/slurm/slurm.conf',
               '/cm/shared/apps/slurm/current/bin/srun', '--jobid=402816', '--overlap', '--exact',
               '--nodes=1', '--ntasks=1', '--cpus-per-task=2', '--gres=none', '--unbuffered',
               '--export=ALL', '/usr/bin/python3', '-B', '-']
    partial = DESTINATION / ('transfer_' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S') + '.partial')
    with partial.open('xb') as output:
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                                 '-o', 'ConnectTimeout=20', 'Group3_slogin', shlex.join(command)],
                                input=code.encode(), stdout=output, stderr=subprocess.PIPE, timeout=1200)
    require(result.returncode == 0, 'Read-only export failed: ' + result.stderr.decode(errors='replace')[-2000:])
    partial.rename(archive)
    with tarfile.open(archive, 'r:gz') as bundle:
        members = bundle.getmembers()
        require({m.name for m in members} == set(FILES) | {'REMOTE_EXPORT_MANIFEST.json'}
                and len(members) == len(FILES) + 1, 'Unexpected archive scope')
        metadata = json.load(bundle.extractfile('REMOTE_EXPORT_MANIFEST.json'))
        for member in members:
            require(member.isfile() and not member.name.startswith('/') and '..' not in Path(member.name).parts,
                    'Unsafe archive member')
            target = DESTINATION / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            h = hashlib.sha256()
            size = 0
            with bundle.extractfile(member) as source, target.open('xb') as output:
                for block in iter(lambda: source.read(1048576), b''):
                    output.write(block)
                    h.update(block)
                    size += len(block)
            if member.name in metadata['files']:
                require(metadata['files'][member.name] == {'sha256': h.hexdigest(), 'bytes': size},
                        'Transfer checksum differs: ' + member.name)
    write_once(DESTINATION / 'transfer_receipt.json', dict(
        at=dt.datetime.now(dt.timezone.utc).isoformat(), archive_sha256=sha(archive),
        archive_bytes=archive.stat().st_size, files_verified=len(FILES), model_requests=0,
        command=command, ssh_exit_code=result.returncode))
    print(json.dumps({'download_verified': True, 'archive_bytes': archive.stat().st_size,
                      'files': metadata['files']}, ensure_ascii=False), flush=True)


def audit():
    export = read(DESTINATION / 'REMOTE_EXPORT_MANIFEST.json')
    for name, metadata in export['files'].items():
        p = DESTINATION / name
        require(p.stat().st_size == metadata['bytes'] and sha(p) == metadata['sha256'], 'Export altered: ' + name)
    require(sha(DESTINATION / 'manifest.json') == sha(SOURCE / 'manifest.json'), 'Remote/local manifest differs')
    raw = (SOURCE / 'input_scoring.json').read_bytes()
    data = json.loads(raw)
    bundle, _ = score.load_bundle(SOURCE)
    identity = score.verify_release(SOURCE, raw, bundle)
    tasks = {score.key(task): task for task in data['tasks']}
    provenance = {score.key(row): row for row in
                  (json.loads(line) for line in (SOURCE / 'answer_provenance.jsonl').open())}
    require(set(tasks) == set(provenance) and len(tasks) == 33047, 'Task provenance coverage differs')
    exit_record = read(DESTINATION / 'dispatches/attempt-002.exit.json')
    execution = read(DESTINATION / 'dispatches/attempt-002.execution.json')
    wrapper = read(DESTINATION / 'dispatches/attempt-002.wrapper.json')
    require(exit_record['exit_code'] == 0 and exit_record['runner_identity'] == execution['runner_identity']
            and execution['wrapper_identity'] == wrapper['wrapper_identity'], 'Dispatch identity/exit mismatch')
    summary = read(DESTINATION / 'results/summary.json')
    require(summary == read(DESTINATION / 'results/run_state.json'), 'Final state differs from summary')
    run_identity = read(DESTINATION / 'results/run_identity.json')
    counts, by_group, seen, usage = Counter(), defaultdict(Counter), set(), Counter()
    attempts = length_retries = missing_raw = 0
    for name in SCORES:
        with (DESTINATION / name).open() as stream:
            for line in stream:
                row = json.loads(line)
                key = score.key(row)
                require(key in tasks and key not in seen, 'Unknown/duplicate result key')
                require(name == 'results/scores_' + key[0] + '_' + key[1] + '.jsonl', 'Wrong split/model export')
                seen.add(key)
                task = tasks[key]
                for field, value in task.items():
                    if field not in score.OMIT_TERMINAL:
                        require(row.get(field) == value, 'Result/input differs: ' + field + ' ' + str(key))
                require(row['answer_hash'] == provenance[key]['answer_hash']
                        and row['scoring_prompt_hash'] == provenance[key]['scoring_prompt_hash'], 'Provenance changed')
                inner, messages = score.messages_for(bundle, task)
                hashes = {str(limit): score.base.digest(score.base.canonical(dict(
                    model=score.CONFIG['model'], messages=messages, temperature=.3,
                    max_tokens=limit, chat_template_kwargs={'thinking': True}))) for limit in (3000, 6000)}
                require(row['request_sha256_by_token_limit'] == hashes, 'Recorded payload hash changed')
                require(row['requested_model'] == row['response_model'] == 'deepseek_v4_flash'
                        and row['temperature'] == .3 and row['thinking'] is True,
                        'Scoring model/temperature/thinking mismatch')
                require(row['self_scoring'] is False and row['answers_regenerated'] is False
                        and row['score_zero_imputed'] is False and row['evaluation_parser'] == 'legacy',
                        'Unexpected regeneration/parser/imputation')
                require(row['status'] in {'ok', 'parse_incomplete', 'truncated'}, 'Unexpected scoring terminal')
                require(isinstance(row['raw_evaluation'], str) and isinstance(row['reasoning_content'], str),
                        'Raw text not preserved')
                if row['status'] != 'truncated':
                    capture = score.legacy.Capture({'answer': row['raw_evaluation'],
                                                    'thinking': row['reasoning_content']}, expected=inner)
                    evaluator, replayed = score.legacy.evaluate(bundle, capture, task, task['answer'])
                    require(replayed == row['legacy_evaluation'], 'Legacy parser replay differs')
                    missing = score.complete_format(row['raw_evaluation'], evaluator.scoring_criteria)
                    require(missing == row['missing_or_invalid_dimensions'], 'Validity guard differs')
                    require((not missing) == (row['status'] == 'ok'), 'Validity/status mismatch')
                if row['status'] == 'ok':
                    evaluation = row['evaluation']
                    require(evaluation == row['legacy_evaluation'], 'Score was replaced')
                    dimensions = evaluation['各维度得分']
                    require(len(dimensions) == 5 and all(isinstance(v, (int, float)) and not isinstance(v, bool)
                            and math.isfinite(v) and 0 <= v <= 4 for v in dimensions.values()), 'Bad dimension score')
                    require(math.isclose(evaluation['总分'], round(sum(dimensions.values()), 2))
                            and math.isclose(evaluation['百分制'], round(sum(dimensions.values()) * 5, 2)),
                            'Total does not match dimension sum')
                else:
                    require(row['evaluation'] is None, 'Exception incorrectly has a valid score')
                counts[row['status']] += 1
                by_group[(key[0], key[1])][row['status']] += 1
                usage.update(row['usage'])
                attempts += row['attempts']
                length_retries += row['length_retried']
                missing_raw += not bool(row['raw_evaluation'])
    require(seen == set(tasks), 'Incomplete output coverage')
    require(dict(counts) == summary['counts'] and summary['completed'] == len(seen), 'Summary status counts differ')
    require(summary['pending'] == summary['inflight'] == summary['needs_reconcile'] == summary['unmatched_intents'] == 0,
            'Nonterminal or unknown scoring remains')
    require(dict(usage) == summary['usage_totals'] and attempts == summary['actual_attempts']
            and length_retries == summary['length_retries'], 'Usage/attempt accounting differs')
    for (model, split), c in by_group.items():
        require(sum(c.values()) == summary['completed_by_model_split'][model][split]
                and c['ok'] == summary['success_by_model_split'][model][split], 'Group summary differs')
    result = dict(audit_passed=True, at=dt.datetime.now(dt.timezone.utc).isoformat(),
                  scope='Six final score exports and run metadata; raw evaluation text replayed for every non-truncated score. '
                        'Full per-attempt API receipts were not downloaded or re-audited.',
                  completed=len(seen), counts=dict(counts), by_model_split={m: {s: dict(by_group[m, s])
                  for s in ('primary', 'secondary')} for m in score.ANSWER_MODELS},
                  release_identity=identity, input_scoring_sha256=hashlib.sha256(raw).hexdigest(),
                  run_identity_sha256=sha(DESTINATION / 'results/run_identity.json'),
                  legacy_bundle_sha256=score.BUNDLE_SHA256, actual_exit_code=exit_record['exit_code'],
                  scoring_ended_at=exit_record['ended_at'], usage_totals=dict(usage), actual_attempts=attempts,
                  length_retries=length_retries, empty_raw_evaluation=missing_raw, model_requests_by_audit=0,
                  legacy_scores_modified=False, files=export['files'])
    write_once(DESTINATION / 'FINAL_EXPORT_AUDIT.json', result)
    print(json.dumps({k: v for k, v in result.items() if k != 'files'}, ensure_ascii=False), flush=True)


def audit_identity():
    identity = read(DESTINATION / 'results/run_identity.json')
    modules = {'script_sha256': score, 'helper_sha256': score.base,
               'queue_sha256': score.answering, 'legacy_adapter_sha256': score.legacy,
               'monitor_sha256': score.benchmark, 'guard_sha256': score.guard_module}
    for field, module in modules.items():
        require(identity[field] == sha(Path(module.__file__)), 'Frozen runtime code identity differs: ' + field)
    final = read(DESTINATION / 'FINAL_EXPORT_AUDIT.json')
    require(identity['input_sha256'] == final['input_scoring_sha256']
            and identity['release_identity'] == final['release_identity']
            and identity['bundle_sha256'] == final['legacy_bundle_sha256']
            and identity['parameters'] == score.CONFIG
            and identity['retry_policy'] == score.answering.RETRY_POLICY
            and identity['tasks'] == final['completed'], 'Runtime/input/release identity mismatch')
    result = dict(audit_passed=True, scope='Frozen runtime code hashes, parameters, retry policy, and release identity',
                  fields=list(modules), model_requests=0)
    write_once(DESTINATION / 'RUNTIME_IDENTITY_AUDIT.json', result)
    print(json.dumps(result))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['fetch', 'audit', 'both', 'identity'])
    args = parser.parse_args()
    if args.mode in ('fetch', 'both'):
        fetch()
    if args.mode in ('audit', 'both'):
        audit()
    if args.mode in ('audit', 'both', 'identity'):
        audit_identity()
