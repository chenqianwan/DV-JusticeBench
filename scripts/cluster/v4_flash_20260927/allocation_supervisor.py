#!/usr/bin/env python3
"""Keep one authorized allocation after a service failure; restart only on request.

No credentials are read here. A running launcher is not a declaration of readiness.
Only the launcher/service can determine whether its API is ready.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid

ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20260927')
PERSONAL = ROOT.parent
REQUEST_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,95}\Z')


def timestamp():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def safe_path(root, path):
    path = Path(path)
    if path != root and root not in path.parents:
        raise RuntimeError('Path outside deployment root')
    if path.resolve() != path:
        raise RuntimeError('Symlink or redirected deployment path')
    return path


def atomic_json(root, path, value):
    path = safe_path(root, path)
    temporary = safe_path(root, path.parent / ('.' + path.name + '.' + uuid.uuid4().hex))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    directory_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def read_json(root, path):
    safe_path(root, path)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd) as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Unexpected JSON file type/owner')
        if info.st_size > 1024 * 1024:
            raise RuntimeError('Control JSON exceeds size limit')
        return json.load(stream)


def process_snapshot():
    """Linux PID/start-time metadata only; never read commands or environments."""
    result = {}
    for path in Path('/proc').glob('[0-9]*/stat'):
        try:
            if path.stat().st_uid != os.getuid():
                continue
            raw = path.read_text()
            fields = raw[raw.rfind(')') + 2:].split()
            pid = int(path.parent.name)
            if fields[0] == 'Z':
                continue
            result[pid] = {'pid': pid, 'ppid': int(fields[1]),
                           'started': fields[19]}
        except (OSError, ValueError, IndexError):
            continue
    return result


class ProcessTree:
    """Track descendants across their separate process groups and reparenting."""
    def __init__(self, pid, snapshot=process_snapshot):
        self.snapshot = snapshot
        self.owned = {}
        first = snapshot()
        if pid in first:
            self.owned[pid] = first[pid]
        self.refresh()

    def refresh(self):
        current = self.snapshot()
        alive = {pid: row for pid, row in current.items()
                 if pid in self.owned and row['started'] == self.owned[pid]['started']}
        changed = True
        while changed:
            changed = False
            for pid, row in current.items():
                if pid not in alive and row['ppid'] in alive:
                    alive[pid] = row
                    self.owned[pid] = row
                    changed = True
        return alive


def stop_tree(process, tree, grace=15.0, kill_timeout=5.0,
              clock=time.monotonic, sleep=time.sleep, send=os.kill):
    """Bounded cleanup, including vLLM descendants in their own sessions.

    PID start times prevent signalling an unrelated process after PID reuse.
    Return survivors rather than claiming cleanup succeeded unconditionally.
    """
    terminated = set()
    deadline = clock() + grace
    while True:
        alive = tree.refresh()
        for pid in sorted(alive, reverse=True):
            identity = (pid, alive[pid]['started'])
            if identity not in terminated:
                try:
                    send(pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                terminated.add(identity)
        process.poll()  # Reap the launcher if it has already exited.
        if not alive or clock() >= deadline:
            break
        sleep(min(0.2, max(0.0, deadline - clock())))
    deadline = clock() + kill_timeout
    while True:
        alive = tree.refresh()
        for pid in sorted(alive, reverse=True):
            try:
                send(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.poll()
        if not alive or clock() >= deadline:
            return list(alive.values())
        sleep(min(0.2, max(0.0, deadline - clock())))


class AllocationSupervisor:
    """One iteration is testable without Slurm or a real service process."""
    def __init__(self, root, job_id, start, stop, refresh=lambda: None,
                 survivors_alive=lambda rows: [], now=timestamp):
        self.root = Path(root)
        self.job_id = str(job_id)
        self.start_process = start
        self.stop_process = stop
        self.refresh_processes = refresh
        self.survivors_alive = survivors_alive
        self.now = now
        self.path = self.root / 'allocation_status.json'
        self.audit = safe_path(self.root, self.root / 'restart_requests')
        self.audit.mkdir(mode=0o700, exist_ok=True)
        self.process = None
        self.state = {'job_id': self.job_id, 'attempt': '000', 'service_exit_code': None,
                      'waiting_for_repair': True, 'allocation_state': 'initializing',
                      'service_pid': None, 'cleanup_survivors': [],
                      'handled_request_ids': [], 'supervisor_pid': os.getpid()}
        self.fresh = not self.path.exists()
        if not self.fresh:
            old = read_json(self.root, self.path)
            if old.get('job_id') != self.job_id:
                raise RuntimeError('Status belongs to another Slurm allocation')
            self.state.update(old)
            # Never infer permission to relaunch from a supervisor restart.
            self.state.update(allocation_state='waiting_for_repair', waiting_for_repair=True,
                              supervisor_pid=os.getpid(), recovery_required=True)
            if old.get('service_identity'):
                self.state['cleanup_survivors'].append(old['service_identity'])
        # Claimed request files survive a crash before updating allocation_status.
        seen = set(self.state['handled_request_ids'])
        for path in self.audit.glob('*.request.json'):
            try:
                request = read_json(self.root, path)
                if isinstance(request, dict) and str(request.get('job_id', '')) == self.job_id:
                    ident = request.get('request_id')
                    if isinstance(ident, str) and REQUEST_ID.fullmatch(ident):
                        seen.add(ident)
            except (OSError, ValueError, RuntimeError):
                continue
        self.state['handled_request_ids'] = sorted(seen)
        self.persist()

    def persist(self):
        self.state['updated_at'] = self.now()
        atomic_json(self.root, self.path, self.state)

    def launch(self, request_id=None):
        self.state.update(attempt=f"{int(self.state['attempt']) + 1:03d}",
                          allocation_state='launching', waiting_for_repair=False,
                          service_exit_code=None, service_pid=None, service_identity=None,
                          last_restart_request_id=request_id)
        self.persist()  # Intent before starting a process; never auto-repeat it.
        try:
            self.process, identity = self.start_process(self.state['attempt'])
        except Exception as exc:
            # Exception text can contain argv/environment; record only its class.
            self.state.update(allocation_state='waiting_for_repair', waiting_for_repair=True,
                              launch_error_type=type(exc).__name__)
            self.persist()
            return
        self.state.update(allocation_state='service_running', service_pid=self.process.pid,
                          service_identity=identity, recovery_required=False)
        self.persist()

    def handle_request(self):
        source = self.root / 'restart_request.json'
        if not source.exists() and not source.is_symlink():
            return
        # Claim once before parsing; only process an owned, regular, non-symlink file.
        safe_path(self.root, source)
        info = source.lstat()
        if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
            raise RuntimeError('Restart request is not an owned regular file')
        token = uuid.uuid4().hex
        archive = safe_path(self.root, self.audit / (token + '.request.json'))
        os.rename(source, archive)
        for directory in (self.root, self.audit):
            directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        decision = {'job_id': self.job_id, 'received_at': self.now(),
                    'request_file': archive.name, 'accepted': False}
        try:
            request = read_json(self.root, archive)
            if not isinstance(request, dict) or set(request) != {'job_id', 'request_id'}:
                raise ValueError('invalid_schema')
            ident = request['request_id']
            if not isinstance(ident, str) or not REQUEST_ID.fullmatch(ident):
                raise ValueError('invalid_request_id')
            decision['request_id'] = ident
            if str(request['job_id']) != self.job_id:
                raise ValueError('wrong_job_id')
            if ident in self.state['handled_request_ids']:
                raise ValueError('duplicate_request_id')
            self.state['handled_request_ids'].append(ident)
            self.persist()
            if self.process is not None:
                raise ValueError('service_still_running')
            if self.state['cleanup_survivors']:
                raise ValueError('previous_processes_still_present')
            decision['accepted'] = True
        except (OSError, ValueError, TypeError, RuntimeError) as exc:
            # Do not save arbitrary request content or exception text.
            reason = str(exc) if isinstance(exc, ValueError) and str(exc) in {
                'invalid_schema', 'invalid_request_id', 'wrong_job_id',
                'duplicate_request_id', 'service_still_running',
                'previous_processes_still_present'} else type(exc).__name__
            decision['reason'] = reason
        atomic_json(self.root, self.audit / (token + '.decision.json'), decision)
        self.state['last_request_decision'] = decision
        self.persist()
        if decision['accepted']:
            self.launch(decision['request_id'])

    def step(self):
        if self.fresh:
            self.fresh = False
            self.launch()
        if self.process is not None:
            self.refresh_processes()
            code = self.process.poll()
            if code is not None:
                survivors = self.stop_process(self.process)
                self.process = None
                self.state.update(service_exit_code=code, service_pid=None,
                                  service_identity=None, cleanup_survivors=survivors,
                                  allocation_state='waiting_for_repair', waiting_for_repair=True)
                self.persist()
        survivors = self.survivors_alive(self.state['cleanup_survivors'])
        if survivors != self.state['cleanup_survivors']:
            self.state['cleanup_survivors'] = survivors
            self.persist()
        try:
            self.handle_request()
        except (OSError, ValueError, RuntimeError) as exc:
            self.state['request_error_type'] = type(exc).__name__
            self.persist()  # A bad control file must not surrender the allocation.

    def shutdown(self, reason):
        self.state.update(allocation_state='stopping', stop_reason=reason)
        self.persist()
        if self.process is not None:
            self.state['cleanup_survivors'] = self.stop_process(self.process)
            self.state['service_exit_code'] = self.process.poll()
            self.process = None
        self.state.update(allocation_state='stopped', waiting_for_repair=False, service_pid=None)
        self.persist()


def main():
    os.umask(0o077)
    job = os.environ.get('SLURM_JOB_ID', '')
    if os.getuid() != 1083 or not job.isdigit() or os.environ.get('SLURM_JOB_NUM_NODES') != '1':
        raise RuntimeError('Expected uid 1083 and a one-node Slurm allocation')
    for path in (PERSONAL, ROOT):
        if path.resolve() != path or not path.is_dir() or path.stat().st_uid != os.getuid():
            raise RuntimeError('Unexpected deployment directory')
    launcher = safe_path(ROOT, ROOT / 'launch_service.py')
    if not launcher.is_file() or launcher.stat().st_uid != os.getuid():
        raise RuntimeError('Invalid launcher')
    lock_fd = os.open(safe_path(ROOT, ROOT / 'allocation.lock'),
                      os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stopping = threading.Event()
    reason = {'value': 'slurm_signal'}

    def signal_stop(signum, frame):
        reason['value'] = signal.Signals(signum).name
        stopping.set()

    signal.signal(signal.SIGTERM, signal_stop)
    signal.signal(signal.SIGINT, signal_stop)
    active = {'tree': None}

    def start(attempt):
        env = dict(os.environ, DVJ_ATTEMPT=attempt, PYTHONDONTWRITEBYTECODE='1')
        process = subprocess.Popen([sys.executable, '-B', str(launcher)], cwd=ROOT,
                                   env=env, start_new_session=True)
        active['tree'] = ProcessTree(process.pid)
        return process, active['tree'].owned.get(process.pid)

    def stop(process):
        tree = active['tree']
        return stop_tree(process, tree) if tree is not None else []

    def refresh():
        if active['tree'] is not None:
            active['tree'].refresh()

    def alive(rows):
        if not rows:
            return []
        snapshot = process_snapshot()
        return [row for row in rows if snapshot.get(row['pid'], {}).get('started') == row['started']]

    supervisor = AllocationSupervisor(ROOT, job, start, stop, refresh, alive)
    end = os.environ.get('SLURM_JOB_END_TIME', '')
    deadline = int(end) if end.isdigit() and int(end) > 0 else None
    try:
        while not stopping.is_set():
            if deadline is not None and time.time() >= deadline:
                reason['value'] = 'slurm_deadline'
                break
            supervisor.step()
            stopping.wait(0.5)
    finally:
        supervisor.shutdown(reason['value'])
        os.close(lock_fd)
    return 0


if __name__ == '__main__':
    sys.exit(main())
