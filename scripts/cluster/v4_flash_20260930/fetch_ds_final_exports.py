#!/usr/bin/env python3
"""Read-only exact DS baseline export; uses only the existing allocation."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import tarfile

from fetch_final_exports import DESTINATION, require, sha, write_once

SCOPES = {
    'ds_nonthinking': {
        'root': '/aifs4su/hansirui_3rd/chenlong/dvjustice_selfscore_batch128_v2_20260928',
        'score_files': ['results/scores_deepseek_v4_flash_primary.jsonl',
                        'results/scores_deepseek_v4_flash_secondary.jsonl'],
        'attempt': 'attempt-001'},
    'ds_thinking': {
        'root': '/aifs4su/hansirui_3rd/chenlong/dvjustice_thinking_pipeline_20260929',
        'score_files': ['results/thinking_pipeline_primary.jsonl',
                        'results/thinking_pipeline_secondary.jsonl'],
        'attempt': 'attempt-002'}
}

REMOTE = r'''
import datetime, gzip, hashlib, io, json, os, pathlib, socket, stat, sys, tarfile
assert os.getuid() == 1083 and os.environ.get('SLURM_JOB_ID') == '402816'
assert socket.gethostname().split('.')[0] == 'dgx-040'
metadata, paths = {}, {}
for tag, scope in SCOPES.items():
    root = pathlib.Path(scope['root'])
    assert root.resolve() == root and not root.is_symlink()
    attempt = scope['attempt']
    exit_record = json.loads((root / ('dispatches/' + attempt + '.exit.json')).read_bytes())
    execution = json.loads((root / ('dispatches/' + attempt + '.execution.json')).read_bytes())
    assert exit_record['exit_code'] == 0 and exit_record['runner_identity'] == execution['runner_identity']
    names = scope['score_files'] + ['results/summary.json', 'results/run_identity.json',
                                   'dispatches/' + attempt + '.exit.json',
                                   'dispatches/' + attempt + '.execution.json']
    for name in names:
        p = root / name
        st = p.lstat()
        assert stat.S_ISREG(st.st_mode) and not p.is_symlink() and st.st_size < 1500000000
        h = hashlib.sha256()
        with p.open('rb') as stream:
            for block in iter(lambda: stream.read(1048576), b''):
                h.update(block)
        target = tag + '/' + name
        metadata[target] = {'sha256': h.hexdigest(), 'bytes': st.st_size, 'remote_path': str(p)}
        paths[target] = p
manifest = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'allocation': '402816',
            'node': socket.gethostname(), 'files': metadata,
            'scope': 'Four final DS exports and run metadata; no full per-attempt receipts or inference calls'}
with gzip.GzipFile(fileobj=sys.stdout.buffer, mode='wb', compresslevel=3, mtime=0) as zipped, tarfile.open(fileobj=zipped, mode='w|') as archive:
    raw = (json.dumps(manifest, ensure_ascii=False, indent=2) + '\n').encode()
    info = tarfile.TarInfo('DS_REMOTE_EXPORT_MANIFEST.json')
    info.size, info.mode = len(raw), 0o600
    archive.addfile(info, io.BytesIO(raw))
    for name, p in paths.items():
        info = archive.gettarinfo(str(p), arcname=name)
        assert info.isfile() and info.size == metadata[name]['bytes']
        with p.open('rb') as stream:
            archive.addfile(info, stream)
'''


def main():
    DESTINATION.mkdir(parents=True, exist_ok=True)
    archive = DESTINATION / 'ds_final_exports.tar.gz'
    require(not archive.exists(), 'Existing DS archive will not be overwritten')
    command = ['env', 'SLURM_CONF=/cm/shared/apps/slurm/var/etc/slurm/slurm.conf',
               '/cm/shared/apps/slurm/current/bin/srun', '--jobid=402816', '--overlap', '--exact',
               '--nodes=1', '--ntasks=1', '--cpus-per-task=2', '--gres=none', '--unbuffered',
               '--export=ALL', '/usr/bin/python3', '-B', '-']
    code = 'SCOPES = ' + repr(SCOPES) + '\n' + REMOTE
    partial = DESTINATION / ('ds_transfer_' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S') + '.partial')
    with partial.open('xb') as output:
        result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'StrictHostKeyChecking=yes',
                                 '-o', 'ConnectTimeout=20', 'Group3_slogin', shlex.join(command)],
                                input=code.encode(), stdout=output, stderr=subprocess.PIPE, timeout=1200)
    require(result.returncode == 0, 'DS read-only export failed: ' + result.stderr.decode(errors='replace')[-2000:])
    partial.rename(archive)
    with tarfile.open(archive, 'r:gz') as bundle:
        metadata = json.load(bundle.extractfile('DS_REMOTE_EXPORT_MANIFEST.json'))
        members = bundle.getmembers()
        require({m.name for m in members} == set(metadata['files']) | {'DS_REMOTE_EXPORT_MANIFEST.json'}
                and len(members) == len(metadata['files']) + 1, 'Unexpected DS archive scope')
        for member in members:
            require(member.isfile() and not member.name.startswith('/') and '..' not in Path(member.name).parts,
                    'Unsafe DS archive member')
            target = DESTINATION / member.name
            target.parent.mkdir(parents=True, exist_ok=True)
            h, size = hashlib.sha256(), 0
            with bundle.extractfile(member) as stream, target.open('xb') as output:
                for block in iter(lambda: stream.read(1048576), b''):
                    output.write(block)
                    h.update(block)
                    size += len(block)
            if member.name in metadata['files']:
                require(metadata['files'][member.name]['sha256'] == h.hexdigest()
                        and metadata['files'][member.name]['bytes'] == size, 'DS transfer checksum mismatch')
    rows = {}
    for tag, scope in SCOPES.items():
        for name in scope['score_files']:
            path = DESTINATION / tag / name
            statuses = {}
            count = 0
            with path.open() as stream:
                for line in stream:
                    row = json.loads(line)
                    count += 1
                    status = row.get('status', '<no top-level status>')
                    statuses[status] = statuses.get(status, 0) + 1
            rows[tag + '/' + name] = {'rows': count, 'status_counts': statuses}
    receipt = dict(at=dt.datetime.now(dt.timezone.utc).isoformat(), files_verified=len(metadata['files']),
                   archive_bytes=archive.stat().st_size, archive_sha256=sha(archive),
                   model_requests=0, actual_ssh_exit_code=0, scope='Transport hashes and JSON row counts only; '
                   'not a complete DS scoring-protocol audit', exports=rows, files=metadata['files'])
    write_once(DESTINATION / 'DS_TRANSFER_AUDIT.json', receipt)
    print(json.dumps({k: v for k, v in receipt.items() if k != 'files'}, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
