"""Recoverable evidence collection; never dispatches inference or edits frozen inputs.

prepare starts one guarded remote archive worker. status is read-only. fetch resumes
the same download. audit validates/extracts the archive, reuses local frozen prepared
bytes, and invokes the existing offline provisional report. Ambiguous attempts and
partial builds are retained for diagnosis, never silently overwritten or restarted.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import gzip
import hashlib
import importlib.util
import json
import os
from pathlib import Path, PurePosixPath
import shlex
import shutil
import stat
import subprocess
import sys
import tarfile

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parents[2] / 'data/pkulaw_admin_ds_full_h1h5_20261003'
REMOTE = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_full_h1h5_20261003')
EXPORT = REMOTE / 'exports/collection_v1'
COLLECTION = LOCAL / 'collection_v1'
HOSTS = ('10.33.4.146', '10.33.4.152')
PREPARED = ('requests_146.jsonl', 'requests_152.jsonl', 'private_mapping.jsonl', 'reused.jsonl', 'manifest.json')
ROOTS = ('code', 'launches', 'sessions', 'stage_manifest.json', 'latest_session.json',
         'dispatch.lock', 'coordinator.lock', 'stage.lock')
SSH = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=25', 'Group3_slogin']


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
    return value.hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def write_new(path, value):
    with Path(path).open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())


def safe(path, root):
    path, root = Path(path), Path(root)
    require(path == root or root in path.parents, 'Path outside collection root')
    require(path.resolve() == path, 'Symlink or noncanonical path')
    return path


@contextmanager
def lock(path, *, create=True):
    flags = os.O_RDWR | os.O_NOFOLLOW | (os.O_CREAT if create else 0)
    fd = os.open(path, flags, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(fd)


def identity(pid):
    require(type(pid) is int and pid > 0, 'Invalid PID')
    try:
        raw = (Path('/proc') / str(pid) / 'stat').read_text()
        parts = raw[raw.rfind(')') + 2:].split()
        return {'pid': pid, 'start_ticks': parts[19], 'state': parts[0]}
    except (FileNotFoundError, ProcessLookupError):
        return None


def same_live(record, current):
    return bool(record and current and current['state'] != 'Z'
                and record['pid'] == current['pid']
                and str(record['start_ticks']) == str(current['start_ticks']))


def require_finished(root):
    """Check original real receipts and client identity; callers hold runner locks."""
    latest = read(safe(root / 'latest_session.json', root))['session']
    sessions = sorted(safe(root / 'sessions', root).iterdir())
    require(sessions and any(p.name == latest for p in sessions), 'Missing latest session')
    launches = sorted(safe(root / 'launches', root).iterdir())
    require(launches and {p.name for p in launches} == {p.name for p in sessions}, 'Launch/session mismatch')
    for directory in launches:
        safe(directory, root)
        intent = read(safe(directory / 'intent.json', root))
        receipt = read(safe(directory / 'receipt.json', root))
        require(intent['session'] == receipt['session'] == directory.name, 'Ambiguous launch')
        require(not same_live(receipt['identity'], identity(receipt['pid'])), 'Inference client still live')
    for directory in sessions:
        safe(directory, root)
        for endpoint in (directory, *(directory / host for host in HOSTS)):
            value = read(safe(endpoint / 'exit.json', root))
            require(value.get('local_futures_finished') is True and value.get('drained') is True,
                    'Missing real finished/drained exit')
        if directory.name == latest:
            final = read(directory / 'exit.json')
            require(final.get('corpus_complete') is True and final.get('all_inputs_accounted') is True,
                    'Corpus not complete/accounted')
    return latest


def source_files(root):
    result = []
    for name in ROOTS:
        path = safe(root / name, root)
        if not path.exists():
            require(name.endswith('.lock'), 'Missing archive root: ' + name)
            continue
        paths = [path, *sorted(path.rglob('*'))] if path.is_dir() else [path]
        for item in paths:
            # Every traversed entry is checked with lstat; directory symlinks
            # are rejected rather than followed. Avoid resolving each ancestor
            # repeatedly across hundreds of thousands of small evidence files.
            mode = item.lstat().st_mode
            require(stat.S_ISREG(mode) or stat.S_ISDIR(mode), 'Nonregular source evidence')
            if stat.S_ISREG(mode):
                result.append(item)
    return sorted(result)


def build_archive(root, output):
    """Single attempt: all targets are exclusive creations; no source mutation."""
    archive, members = output / 'evidence.tar.gz', output / 'members.jsonl'
    temporary = output / 'evidence.tar.gz.building'
    member_tmp = output / 'members.jsonl.building'
    require(not any(p.exists() for p in (archive, members, temporary, member_tmp, output / 'receipt.json')),
            'Archive/build already exists; inspect without overwriting')
    paths = source_files(root)
    total = 0
    with temporary.open('xb') as raw, member_tmp.open('xb') as inventory:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', compresslevel=1, mtime=0) as zipped:
            with tarfile.open(fileobj=zipped, mode='w|', format=tarfile.PAX_FORMAT, dereference=True) as tar:
                for path in paths:
                    before = path.stat()
                    name = path.relative_to(root).as_posix()
                    info = tar.gettarinfo(str(path), arcname=name)
                    require(info.isfile(), 'Only regular evidence files are archived')
                    hasher = hashlib.sha256()
                    with path.open('rb') as source:
                        class HashingReader:
                            def read(self, size=-1):
                                block = source.read(size)
                                hasher.update(block)
                                return block
                        tar.addfile(info, HashingReader())
                    after = path.stat()
                    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'Evidence changed while archiving')
                    value = {'path': name, 'size': before.st_size, 'sha256': hasher.hexdigest()}
                    inventory.write(json.dumps(value, sort_keys=True).encode() + b'\n')
                    total += before.st_size
        raw.flush()
        os.fsync(raw.fileno())
        inventory.flush()
        os.fsync(inventory.fileno())
    # Source clients are exited and both launch/coordinator locks are held.
    # Final report independently checks full request/session coverage.
    os.rename(temporary, archive)
    os.rename(member_tmp, members)
    result = {'at': now(), 'archive': archive.name, 'sha256': digest(archive), 'bytes': archive.stat().st_size,
              'members': members.name, 'members_sha256': digest(members), 'member_count': len(paths),
              'source_bytes': total, 'excluded': ['prepared', 'incoming', 'exports'],
              'provisional': True, 'reference_ready': False, 'legal_correctness_verified': False,
              'formal_hallucination_statistics_allowed': False}
    write_new(output / 'receipt.json', result)
    return result


def pack_worker():
    os.umask(0o077)
    require(os.getuid() == 1083 and REMOTE.resolve() == REMOTE and EXPORT.resolve() == EXPORT, 'Wrong remote owner/root')
    os.nice(10)
    # The launch guard creates this unique worker once. No retry after any attempt.
    with lock(EXPORT / 'collection.lock'):
        write_new(EXPORT / 'attempt.json', {'at': now(), 'identity': identity(os.getpid())})
        success = False
        error = None
        try:
            with lock(REMOTE / 'dispatch.lock', create=False), lock(REMOTE / 'coordinator.lock', create=False):
                session = require_finished(REMOTE)
                receipt = build_archive(REMOTE, EXPORT)
                success = True
                return {'session': session, **receipt}
        except BaseException as exc:
            error = type(exc).__name__ + ': ' + str(exc)
            raise
        finally:
            write_new(EXPORT / 'exit.json', {'at': now(), 'identity': identity(os.getpid()),
                      'success': success, 'error': error, 'worker_finished': True})


def remote_status():
    if not EXPORT.exists():
        return {'state': 'not_prepared'}
    safe(EXPORT, REMOTE)
    result = {'state': 'existing', 'export': str(EXPORT)}
    for name in ('launch_intent', 'launch_receipt', 'attempt', 'receipt', 'exit'):
        path = EXPORT / (name + '.json')
        result[name] = read(safe(path, REMOTE)) if path.exists() else None
    launch = result['launch_receipt']
    result['worker_identity'] = identity(launch['identity']['pid']) if launch else None
    result['worker_alive'] = bool(launch and same_live(launch['identity'], result['worker_identity']))
    result['ready'] = bool(result['receipt'] and result['exit'] and result['exit'].get('success') is True
                           and result['exit'].get('worker_finished') is True and not result['worker_alive'])
    return result


def remote_prepare(source, source_sha):
    os.umask(0o077)
    require(os.getuid() == 1083 and REMOTE.resolve() == REMOTE, 'Wrong remote root/owner')
    safe(EXPORT, REMOTE).mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(EXPORT / 'launch.lock'):
        if (EXPORT / 'launch_intent.json').exists():
            return remote_status()
        require(not any((EXPORT / n).exists() for n in ('attempt.json', 'exit.json', 'receipt.json',
                    'evidence.tar.gz', 'evidence.tar.gz.building', 'members.jsonl.building')),
                'Existing export without launch intent; inspect first')
        helper = EXPORT / 'fetch_archive_v1.py'
        require(hashlib.sha256(source).hexdigest() == source_sha, 'Helper hash mismatch')
        if helper.exists():
            require(digest(safe(helper, REMOTE)) == source_sha, 'Existing helper differs')
        else:
            with helper.open('xb') as stream:
                stream.write(source)
                stream.flush()
                os.fsync(stream.fileno())
        command = ['/usr/bin/python3', '-B', str(helper), '_pack-worker']
        write_new(EXPORT / 'launch_intent.json', {'at': now(), 'command': command, 'helper_sha256': source_sha})
        with (EXPORT / 'worker.log').open('x') as log:
            child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     cwd=EXPORT, start_new_session=True, close_fds=True)
        who = identity(child.pid)
        require(who is not None, 'Worker launch identity unavailable; do not relaunch')
        write_new(EXPORT / 'launch_receipt.json', {'at': now(), 'identity': who, 'helper_sha256': source_sha})
    return remote_status()


def remote_call(mode):
    source = Path(__file__).read_bytes()
    encoded = base64.b64encode(source).decode()
    code = ('import base64;source=base64.b64decode(' + repr(encoded) + ');'
            'scope={"__name__":"archive_rpc","__file__":' + repr(str(EXPORT / 'fetch_archive_v1.py')) + '};'
            'exec(compile(source,"fetch_archive_v1.py","exec"),scope);'
            'result=scope[' + repr('remote_prepare' if mode == 'prepare' else 'remote_status') + ']('
            + ('source,' + repr(hashlib.sha256(source).hexdigest()) if mode == 'prepare' else '')
            + ');print(scope["json"].dumps(result))')
    process = subprocess.run(SSH + ['python3 -B -c ' + shlex.quote(code)], capture_output=True, text=True, timeout=60)
    require(process.returncode == 0, 'SSH failed; inspect before repeat: ' + process.stderr[-3000:])
    return json.loads(process.stdout)


def validate_download(directory):
    receipt, exit_receipt = read(directory / 'receipt.json'), read(directory / 'exit.json')
    require(exit_receipt.get('success') is True and exit_receipt.get('worker_finished') is True, 'Export lacks successful exit')
    require(receipt.get('archive') == 'evidence.tar.gz' and receipt.get('members') == 'members.jsonl', 'Unexpected export filenames')
    for name in ('evidence.tar.gz', 'members.jsonl', 'receipt.json', 'exit.json'):
        safe(directory / name, directory)
    require((directory / 'evidence.tar.gz').stat().st_size == receipt['bytes']
            and digest(directory / 'evidence.tar.gz') == receipt['sha256'], 'Downloaded archive hash/size mismatch')
    require(digest(directory / 'members.jsonl') == receipt['members_sha256'], 'Downloaded memberlist mismatch')
    return receipt


def fetch():
    status = remote_call('status')
    require(status.get('ready') is True, 'Archive is not complete with real worker exit; use status')
    COLLECTION.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(COLLECTION / 'local.lock'):
        target = safe(COLLECTION / 'dl', COLLECTION)
        target.mkdir(exist_ok=True, mode=0o700)
        process = subprocess.run(['rsync', '-az', '--partial', '--partial-dir=.rsync-partial',
            '--exclude=*.building', '--exclude=*.lock', '-e', 'ssh -o BatchMode=yes -o ConnectTimeout=25',
            'Group3_slogin:' + str(EXPORT) + '/', str(target) + '/'])
        require(process.returncode == 0, 'Download interrupted; rerun fetch to resume the same target')
        receipt = validate_download(target)
        require(receipt == status['receipt'], 'Downloaded receipt differs from remote completed export')
        return {'download': str(target), 'receipt': receipt}


def member_name(name):
    path = PurePosixPath(name)
    require(isinstance(name, str) and name and not path.is_absolute() and '..' not in path.parts
            and str(path) == name and path.parts[0] in ROOTS, 'Unsafe/unexpected archive member')
    return path


def extract_verified(directory, destination, receipt):
    """Resumable per-file exclusive extraction; validates all files on every pass."""
    expected = {}
    with (directory / 'members.jsonl').open('rb') as stream:
        for line in stream:
            item = json.loads(line)
            name = item['path']
            member_name(name)
            require(name not in expected and type(item['size']) is int and item['size'] >= 0, 'Duplicate/invalid inventory member')
            expected[name] = item
    require(len(expected) == receipt['member_count'], 'Inventory member count mismatch')
    destination.mkdir(exist_ok=True, mode=0o700)
    seen = set()
    with tarfile.open(directory / 'evidence.tar.gz', 'r|gz') as archive:
        for member in archive:
            name = member.name
            member_name(name)
            require(member.isfile() and not member.issparse() and name not in seen, 'Nonregular/duplicate archive member')
            require(name in expected and member.size == expected[name]['size'], 'Archive/inventory mismatch')
            path = safe(destination / name, destination)
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            temporary = path.with_name(path.name + '.extracting')
            # No overwrite, including interrupted partial extraction. Complete partial
            # bytes may be safely promoted; incomplete bytes require manual diagnosis.
            existing = path if path.exists() else temporary if temporary.exists() else None
            if existing:
                safe(existing, destination)
                require(existing.is_file() and existing.stat().st_size == member.size
                        and digest(existing) == expected[name]['sha256'], 'Existing extraction differs; preserved for diagnosis')
            else:
                with archive.extractfile(member) as src, temporary.open('xb') as dst:
                    shutil.copyfileobj(src, dst, 1024 * 1024)
                    dst.flush()
                    os.fsync(dst.fileno())
                require(digest(temporary) == expected[name]['sha256'], 'Extracted evidence digest mismatch')
            if not path.exists():
                os.rename(temporary, path)
            seen.add(name)
    require(seen == set(expected), 'Missing archive evidence')
    return expected


def reuse_prepared(source, snapshot):
    stage = read(snapshot / 'stage_manifest.json')
    destination = snapshot / 'prepared'
    safe(destination, snapshot).mkdir(exist_ok=True, mode=0o700)
    for name in PREPARED:
        original = safe(source / name, source)
        expected = stage['files']['prepared/' + name]
        require(original.is_file() and digest(original) == expected, 'Local frozen prepared mismatch: ' + name)
        path = safe(destination / name, snapshot)
        temporary = safe(path.with_name(path.name + '.copying'), snapshot)
        if path.exists():
            require(path.is_file() and digest(path) == expected, 'Existing prepared copy mismatch')
            continue
        if temporary.exists():
            require(digest(temporary) == expected, 'Incomplete prepared copy retained; inspect first')
        else:
            with original.open('rb') as src, temporary.open('xb') as dst:
                shutil.copyfileobj(src, dst, 1024 * 1024)
                dst.flush()
                os.fsync(dst.fileno())
        require(digest(temporary) == expected, 'Prepared copy digest mismatch')
        os.rename(temporary, path)


def audit():
    COLLECTION.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock(COLLECTION / 'local.lock'):
        directory, snapshot = COLLECTION / 'dl', COLLECTION / 'snapshot'
        safe(directory, COLLECTION)
        safe(snapshot, COLLECTION)
        receipt = validate_download(directory)
        expected = extract_verified(directory, snapshot, receipt)
        reuse_prepared(LOCAL / 'prepared', snapshot)
        actual = {p.relative_to(snapshot).as_posix() for p in snapshot.rglob('*') if p.is_file()}
        require(actual == set(expected) | {'prepared/' + name for name in PREPARED}, 'Unexpected snapshot files')
        report_path = HERE / 'report.py'
        spec = importlib.util.spec_from_file_location('collection_provisional_report', report_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        output = LOCAL / 'reports' / ('collection_v1_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        result = module.audit(snapshot, output)
        record = {'at': now(), 'snapshot': str(snapshot), 'output': str(output), 'archive_sha256': receipt['sha256'],
                  'report_state': result['report_state'], 'overall': result['overall'],
                  'provisional': True, 'reference_ready': False, 'legal_correctness_verified': False,
                  'formal_hallucination_statistics_allowed': False}
        write_new(COLLECTION / ('audit_' + output.name + '.json'), record)
        return record


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('prepare', 'status', 'fetch', 'audit', '_pack-worker'))
    mode = parser.parse_args().mode
    if mode == '_pack-worker':
        result = pack_worker()
    elif mode in ('prepare', 'status'):
        result = remote_call(mode)
    else:
        result = fetch() if mode == 'fetch' else audit()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
