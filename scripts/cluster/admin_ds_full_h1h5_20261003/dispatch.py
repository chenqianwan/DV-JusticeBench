"""Transfer and safely dispatch the versioned full provisional audit client."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LOCAL = REPO / 'data/pkulaw_admin_ds_full_h1h5_20261003'
REMOTE = '/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_full_h1h5_20261003'
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=25', 'Group3_slogin']
CODE = {
    'runner.py': HERE / 'runner.py',
    'pilot_runner.py': HERE.parent / 'admin_ds_sentence_retry_20261003/runner.py',
    'bench_external.py': HERE.parent / 'admin_ds_benchmark_20261003/bench_external.py',
    'bench.py': HERE.parent / 'v4_flash_20261002_autotune/bench.py',
    'contract.py': REPO / 'scripts/hallucination_h1_h5_20261002/contract.py',
    'sentence_contract.py': REPO / 'scripts/hallucination_h1_h5_sentence_ids_20261003/contract.py',
}


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def stamp():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def save_check(mode, result):
    checks = LOCAL / 'checks'
    checks.mkdir(parents=True, exist_ok=True)
    record = {'at': datetime.now(timezone.utc).isoformat(), 'mode': mode, **result}
    with (checks / (stamp() + '_' + mode + '.json')).open('x') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
        f.write('\n')
    return record


def remote(command, mode, timeout=150):
    try:
        p = subprocess.run(SSH + [command], capture_output=True, text=True, timeout=timeout)
        result = {'exit_code': p.returncode, 'stderr': p.stderr[-5000:]}
        try:
            result['result'] = json.loads(p.stdout)
        except ValueError:
            result['stdout'] = p.stdout[-5000:]
    except subprocess.TimeoutExpired:
        result = {'exit_code': 124, 'error': 'SSH timeout; inspect launch/exit receipts before any repeat'}
    return save_check(mode, result)


def build_bundle():
    prepared = LOCAL / 'prepared'
    manifest = json.loads((prepared / 'manifest.json').read_text())
    if set(manifest.get('code_sha256', {})) != set(CODE):
        raise RuntimeError('Preparation code hashes not frozen')
    for name, path in CODE.items():
        if sha(path) != manifest['code_sha256'][name]:
            raise RuntimeError('Frozen dependency changed: ' + name)
    identifier = hashlib.sha256((sha(prepared / 'manifest.json') + sha(HERE / 'manager.py')).encode()).hexdigest()[:24]
    bundle = LOCAL / 'bundles' / ('bundle_' + identifier)
    files = {'code/' + name: path for name, path in CODE.items()}
    files['code/manager.py'] = HERE / 'manager.py'
    files.update({'prepared/' + name: prepared / name for name in (
        'requests_146.jsonl', 'requests_152.jsonl', 'private_mapping.jsonl', 'reused.jsonl', 'manifest.json')})
    if (bundle / 'bundle_manifest.json').exists():
        prior = json.loads((bundle / 'bundle_manifest.json').read_text())
        if set(prior['files']) != set(files):
            raise RuntimeError('Existing bundle file set mismatch')
        for name, source in files.items():
            meta = prior['files'][name]
            if sha(source) != meta['sha256'] or sha(bundle / meta['transport_path']) != meta['transport_sha256']:
                raise RuntimeError('Existing frozen bundle mismatch')
        return bundle
    bundle.mkdir(parents=True, exist_ok=True, mode=0o700)
    meta = {}
    for name, source in files.items():
        compressed = name.endswith('.jsonl')
        transport_name = name + ('.gz' if compressed else '')
        destination = bundle / transport_name
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        source_hash = sha(source)
        if not destination.exists():
            temporary = destination.with_name(destination.name + '.building')
            # Unknown interrupted compressors are retained, never overwritten.
            with source.open('rb') as src, temporary.open('xb') as dst:
                if compressed:
                    with gzip.GzipFile(fileobj=dst, mode='wb', mtime=0, compresslevel=6) as zipper:
                        shutil.copyfileobj(src, zipper, 1024 * 1024)
                else:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
            os.rename(temporary, destination)
        # Validate transport unpacking against source even on an interrupted bundle build.
        h = hashlib.sha256()
        opener = gzip.open if compressed else open
        with opener(destination, 'rb') as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b''):
                h.update(block)
        if h.hexdigest() != source_hash:
            raise RuntimeError('Existing transport differs from prepared artifact')
        meta[name] = {'sha256': source_hash, 'transport_path': transport_name,
                      'transport_sha256': sha(destination), 'gzip': compressed,
                      'source_bytes': source.stat().st_size, 'transport_bytes': destination.stat().st_size}
    with (bundle / 'bundle_manifest.json').open('x') as f:
        json.dump({'at': datetime.now(timezone.utc).isoformat(), 'files': meta}, f, indent=2)
        f.write('\n')
    return bundle


def stage():
    bundle = build_bundle()
    target = REMOTE + '/incoming/' + bundle.name
    bootstrap = (
        'import os,pathlib,json;os.umask(0o077);'
        f'r=pathlib.Path({REMOTE!r});p=pathlib.Path({target!r});'
        'assert os.getuid()==1083 and r.resolve()==r and p.resolve()==p;'
        'assert r.parent.is_dir() and r.parent.stat().st_uid==os.getuid();'
        'assert all(not x.exists() or x.stat().st_uid==os.getuid() for x in (r,r/"incoming",p));'
        'p.mkdir(mode=0o700,parents=True,exist_ok=True);print(json.dumps({"incoming":str(p)}))'
    )
    result = remote('python3 -B -c ' + shlex.quote(bootstrap), 'stage_bootstrap', 40)
    if result['exit_code']:
        return result
    p = subprocess.run(['rsync', '-az', '--partial', '--partial-dir=.rsync-partial', '-e',
                        'ssh -o BatchMode=yes -o ConnectTimeout=25', str(bundle) + '/',
                        'Group3_slogin:' + target + '/'])
    record = save_check('stage_upload', {'exit_code': p.returncode, 'bundle': str(bundle), 'remote_incoming': target})
    if p.returncode:
        return record
    # Bootstrap verifies the manager file before executing it.
    incoming_manager = target + '/code/manager.py'
    call = ('import hashlib,pathlib,runpy,sys;'
            f'p=pathlib.Path({incoming_manager!r});assert p.resolve()==p;'
            f'assert hashlib.sha256(p.read_bytes()).hexdigest()=={sha(HERE / "manager.py")!r};'
            f'sys.argv=[str(p),"finalize","--bundle",{bundle.name!r}];runpy.run_path(str(p),run_name="__main__")')
    return remote('python3 -B -c ' + shlex.quote(call), 'stage_finalize', 180)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('bundle', 'stage', 'start', 'resume', 'status', 'fetch'))
    args = parser.parse_args()
    if args.mode == 'bundle':
        result = {'exit_code': 0, 'bundle': str(build_bundle())}
    elif args.mode == 'stage':
        result = stage()
    elif args.mode == 'fetch':
        destination = LOCAL / 'fetched' / stamp()
        destination.mkdir(parents=True, exist_ok=False)
        p = subprocess.run(['rsync', '-az', '--partial', '-e', 'ssh -o BatchMode=yes -o ConnectTimeout=25',
                            '--exclude=incoming', 'Group3_slogin:' + REMOTE + '/', str(destination) + '/'])
        result = save_check('fetch', {'exit_code': p.returncode, 'destination': str(destination)})
    else:
        result = remote('python3 -B ' + shlex.quote(REMOTE + '/code/manager.py') + ' ' + args.mode, args.mode)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(result['exit_code'])


if __name__ == '__main__':
    main()
