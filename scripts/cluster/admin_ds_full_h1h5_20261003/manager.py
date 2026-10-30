"""Remote staging/launch guard for the full provisional sentence-ID audit.

No service changes or resource management. An ambiguous launch never relaunches.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_full_h1h5_20261003')
PILOT = ROOT.parent / 'dvjustice_admin_ds_sentence_retry_20261003'
OWNER = 1083


def now():
    return datetime.now(timezone.utc).isoformat()


def digest_file(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def safe(path, root=ROOT):
    path = Path(path)
    if path.resolve() != path or not (path == root or root in path.parents):
        raise RuntimeError('Unexpected or symlinked path')
    for part in (path, *path.parents):
        if (part == root or root in part.parents) and part.exists() and part.stat().st_uid != os.getuid():
            raise RuntimeError('Unexpected owner')
    return path


def read(path):
    return json.loads(Path(path).read_text()) if Path(path).exists() else None


def write_new(path, value):
    path = safe(path)
    with path.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())


@contextmanager
def exclusive(path):
    fd = os.open(safe(path), os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def identity(pid):
    if type(pid) is not int or pid <= 0:
        raise RuntimeError('Invalid process identity')
    try:
        directory = Path('/proc') / str(pid)
        stat = (directory / 'stat').read_text()
        fields = stat[stat.rfind(')') + 2:].split()
        args = (directory / 'cmdline').read_bytes().split(b'\0')
        return {'pid': pid, 'start_ticks': fields[19], 'state': fields[0],
                'runner_match': os.fsencode(ROOT / 'code/runner.py') in args}
    except (FileNotFoundError, ProcessLookupError):
        return None


def live_same(record, current):
    return bool(record and current and current['state'] != 'Z' and
                record['pid'] == current['pid'] and
                str(record['start_ticks']) == str(current['start_ticks']))


def verify_stage():
    stage = read(safe(ROOT / 'stage_manifest.json'))
    if not stage:
        raise RuntimeError('No completed stage')
    for name, expected in stage['files'].items():
        if digest_file(safe(ROOT / name)) != expected:
            raise RuntimeError('Frozen staged artifact mismatch: ' + name)
    manifest = read(ROOT / 'prepared/manifest.json')
    if not manifest.get('code_sha256'):
        raise RuntimeError('Code not frozen')
    for name, expected in manifest['code_sha256'].items():
        if digest_file(safe(ROOT / 'code' / name)) != expected:
            raise RuntimeError('Frozen code mismatch: ' + name)
    return manifest


def finalize(incoming_name):
    if not incoming_name.startswith('bundle_') or any(c not in 'abcdefghijklmnopqrstuvwxyz0123456789_' for c in incoming_name):
        raise RuntimeError('Invalid staging name')
    incoming = safe(ROOT / 'incoming' / incoming_name)
    stage = read(safe(incoming / 'bundle_manifest.json'))
    if not stage or not stage.get('files'):
        raise RuntimeError('No bundle manifest')
    expected_names = {'code/' + x for x in ('runner.py', 'pilot_runner.py', 'bench_external.py',
        'bench.py', 'contract.py', 'sentence_contract.py', 'manager.py')}
    expected_names.update('prepared/' + x for x in ('requests_146.jsonl', 'requests_152.jsonl',
        'private_mapping.jsonl', 'reused.jsonl', 'manifest.json'))
    if set(stage['files']) != expected_names:
        raise RuntimeError('Unexpected staged file set')
    with exclusive(ROOT / 'stage.lock'):
        if (ROOT / 'stage_manifest.json').exists():
            verify_stage()
            if read(ROOT / 'stage_manifest.json')['files'] != {k: v['sha256'] for k, v in stage['files'].items()}:
                raise RuntimeError('Refuse replacing frozen stage')
            return {'status': 'already_staged'}
        if (ROOT / 'launches').exists() or (ROOT / 'sessions').exists():
            raise RuntimeError('Cannot stage over launched work')
        for name, meta in stage['files'].items():
            source = safe(incoming / meta['transport_path'])
            if digest_file(source) != meta['transport_sha256']:
                raise RuntimeError('Transport hash mismatch: ' + name)
            target = safe(ROOT / name)
            target.parent.mkdir(mode=0o700, exist_ok=True)
            if target.exists():
                if digest_file(target) != meta['sha256']:
                    raise RuntimeError('Partial stage target mismatch; needs inspection: ' + name)
                continue
            temporary = safe(target.with_name(target.name + '.staging'))
            if temporary.exists():
                # Do not overwrite unknown interrupted copies. Manual hash-based reconciliation only.
                if digest_file(temporary) != meta['sha256']:
                    raise RuntimeError('Interrupted decompression requires reconciliation: ' + name)
            else:
                opener = gzip.open if meta['gzip'] else open
                with opener(source, 'rb') as src, temporary.open('xb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
            if digest_file(temporary) != meta['sha256']:
                raise RuntimeError('Decompressed hash mismatch: ' + name)
            # Under stage lock, destination known absent; retain source transport evidence.
            os.rename(temporary, target)
        write_new(ROOT / 'stage_manifest.json', {'at': now(), 'files': {
            k: v['sha256'] for k, v in stage['files'].items()},
            'bundle_manifest_sha256': digest_file(incoming / 'bundle_manifest.json')})
        verify_stage()
    return {'status': 'staged', 'root': str(ROOT)}


def runner_module():
    spec = importlib.util.spec_from_file_location('full_audit_runner', ROOT / 'code/runner.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@contextmanager
def finished_pilot():
    if PILOT.resolve() != PILOT:
        raise RuntimeError('Unexpected pilot root')
    receipt = read(PILOT / 'launch_receipt.json')
    if not receipt or not receipt.get('identity'):
        raise RuntimeError('Pilot launch identity absent')
    old = receipt['identity']
    if old.get('pid') != receipt['pid'] or not old.get('correct_runner'):
        raise RuntimeError('Pilot process identity mismatch')
    if live_same(old, identity(receipt['pid'])):
        raise RuntimeError('Pilot still running')
    for path in [PILOT / 'results/exit.json'] + [PILOT / 'results' / h / 'exit.json'
                for h in ('10.33.4.146', '10.33.4.152')]:
        value = read(path)
        if not value or value.get('completed') is not True or value.get('drained') is not True:
            raise RuntimeError('Pilot not completed/drained')
    fd = os.open(PILOT / 'code/global.lock', os.O_RDWR | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def launch_records():
    result = []
    directory = ROOT / 'launches'
    if not directory.exists():
        return result
    for launch in sorted(directory.iterdir()):
        safe(launch)
        intent = read(safe(launch / 'intent.json'))
        receipt = read(safe(launch / 'receipt.json'))
        if not intent or not receipt or not receipt.get('identity'):
            result.append({'session': launch.name, 'ambiguous_launch': True,
                           'intent': intent, 'receipt': receipt})
            continue
        if (intent.get('session') != launch.name or receipt.get('session') != launch.name or
                receipt['identity'].get('pid') != receipt['pid']):
            raise RuntimeError('Launch receipt identity/session mismatch')
        live = identity(receipt['pid'])
        result.append({'session': launch.name, 'receipt': receipt, 'live_identity': live,
                       'same_live_process': live_same(receipt['identity'], live),
                       'runner_match': bool(live and live['runner_match']),
                       'exit': read(safe(ROOT / 'sessions' / launch.name / 'exit.json'))})
    return result


def status():
    # The completed stage hash audit is performed on launch. Avoid hashing GBs on every poll.
    stage = read(safe(ROOT / 'stage_manifest.json'))
    if not stage:
        return {'at': now(), 'state': 'not_staged', 'root_exists': ROOT.exists()}
    for name in ('manager.py', 'runner.py', 'pilot_runner.py', 'bench_external.py', 'bench.py',
                 'contract.py', 'sentence_contract.py'):
        if digest_file(safe(ROOT / 'code' / name)) != stage['files']['code/' + name]:
            raise RuntimeError('Status code changed')
    records = launch_records()
    return {'at': now(), 'root': str(ROOT), 'launches': records,
            'runner': runner_module().status(ROOT)}


def launch(mode):
    verify_stage()
    with exclusive(ROOT / 'dispatch.lock'):
        records = launch_records()
        for record in records:
            if record.get('ambiguous_launch'):
                raise RuntimeError('Ambiguous prior launch; inspect without relaunching')
            if record['same_live_process']:
                if not record['runner_match']:
                    raise RuntimeError('Live process identity mismatch')
                return {'action': 'already_running_no_duplicate', 'session': record['session']}
        if records and mode == 'start':
            raise RuntimeError('Prior launch exists; explicit resume required')
        if not records and mode == 'resume':
            raise RuntimeError('No prior launch; use explicit start')
        for record in records:
            exit_record = record.get('exit')
            if not exit_record or exit_record.get('drained') is not True or exit_record.get('local_futures_finished') is not True:
                raise RuntimeError('Prior exit/drain receipt incomplete; inspect actual exit cause')
        if records and records[-1]['exit'].get('corpus_complete') is True:
            return {'action': 'already_complete_no_new_client', 'session': records[-1]['session']}
        # Hold runner lock until complete child PID receipt is durably recorded.
        with finished_pilot(), exclusive(ROOT / 'coordinator.lock'):
            session = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
            directory = safe(ROOT / 'launches' / session)
            directory.mkdir(mode=0o700, parents=True, exist_ok=False)
            command = ['/usr/bin/python3', '-B', str(ROOT / 'code/runner.py'), 'run',
                       '--root', str(ROOT), '--session', session]
            write_new(directory / 'intent.json', {'at': now(), 'session': session, 'command': command,
                'mode': mode, 'provisional': True, 'reference_ready': False,
                'formal_hallucination_statistics_allowed': False})
            with (directory / 'runner.log').open('x') as log:
                process = subprocess.Popen(command, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log,
                    stderr=subprocess.STDOUT, start_new_session=True, close_fds=True,
                    env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1', PYTHONNOUSERSITE='1'))
            who = identity(process.pid)
            write_new(directory / 'receipt.json', {'at': now(), 'pid': process.pid, 'identity': who,
                'session': session, 'dispatch_does_not_prove_requests_started': True})
            return {'action': 'dispatched_verify_status', 'session': session, 'pid': process.pid,
                    'identity': who}


def main():
    os.umask(0o077)
    if os.getuid() != OWNER or ROOT.resolve() != ROOT or not ROOT.is_dir():
        raise RuntimeError('Unexpected account/root')
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('finalize', 'start', 'resume', 'status'))
    parser.add_argument('--bundle')
    args = parser.parse_args()
    result = finalize(args.bundle) if args.mode == 'finalize' else status() if args.mode == 'status' else launch(args.mode)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))


if __name__ == '__main__':
    main()
