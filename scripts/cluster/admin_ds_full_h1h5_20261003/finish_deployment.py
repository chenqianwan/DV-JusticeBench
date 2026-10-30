"""One-shot continuation of the current upload; not a recurring monitor.

Wait for a specifically identified transfer process to exit, require a genuine
successful finalizer receipt, then ask the guarded dispatcher to start once.
The recurring two-hour monitor is provided by the app automation, separately.
"""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import select
import subprocess
import sys

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LOCAL = REPO / 'data/pkulaw_admin_ds_full_h1h5_20261003'
STATE = LOCAL / 'deployment_continuation'
EXPECTED_START = 'Sun Oct  4 00:31:27 2026'
TRANSFER_PID = 40544
MIN_RECEIPT = '2026-10-03T16:31:27'


def now():
    return datetime.now(timezone.utc).isoformat()


def write(path, value):
    with path.open('x') as f:
        json.dump(value, f, ensure_ascii=False, indent=2)
        f.write('\n')
        f.flush()
        os.fsync(f.fileno())


def transfer_identity():
    result = subprocess.run(['ps', '-p', str(TRANSFER_PID), '-o', 'lstart=,command='],
                            capture_output=True, text=True, timeout=5)
    if result.returncode == 1 and not result.stdout.strip():
        return None
    result.check_returncode()
    value = result.stdout.strip()
    if not (value.startswith(EXPECTED_START) and
            'python3 -B scripts/cluster/admin_ds_full_h1h5_20261003/compact_stage.py' in value):
        raise RuntimeError('Transfer PID identity changed; do not wait or launch automatically')
    return value


def accepted_finalizer():
    paths = sorted((LOCAL / 'checks').glob('*_compact_finalize.json'))
    if not paths:
        raise RuntimeError('No real compact finalizer receipt')
    value = json.loads(paths[-1].read_text())
    result = value.get('result') or {}
    if not (value.get('exit_code') == 0 and value.get('at', '') >= MIN_RECEIPT and
            result.get('status') in ('staged', 'already_staged')):
        raise RuntimeError('Finalizer did not confirm verified staging')
    return {'path': str(paths[-1]), 'receipt': value}


def continue_once():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(STATE / 'guard.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (STATE / 'attempt.json').exists():
            raise RuntimeError('One-shot deployment continuation already attempted')
        source = transfer_identity()
        write(STATE / 'attempt.json', {'at': now(), 'pid': os.getpid(), 'transfer_pid': TRANSFER_PID,
              'transfer_identity': source, 'one_shot': True, 'timeout_s': 5400})
        if source:
            queue = select.kqueue()
            try:
                event = select.kevent(TRANSFER_PID, filter=select.KQ_FILTER_PROC,
                    flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT, fflags=select.KQ_NOTE_EXIT)
                try:
                    queue.control([event], 0, 0)
                except ProcessLookupError:
                    if transfer_identity() is not None:
                        raise
                else:
                    events = queue.control(None, 1, 5400)
                    if not events:
                        raise TimeoutError('Transfer wait expired; no launch; recurring monitor will inspect')
            finally:
                queue.close()
        if transfer_identity() is not None:
            raise RuntimeError('Transfer still exists; no premature launch')
        verified = accepted_finalizer()
        write(STATE / 'finalizer_verified.json', {'at': now(), **verified})
        # Exactly one invocation. The remote dispatcher has launch identity/lock
        # guards and never duplicates an ambiguous or live prior client.
        process = subprocess.run([sys.executable, '-B', str(HERE / 'dispatch.py'), 'start'],
                                 cwd=REPO, capture_output=True, text=True, timeout=180)
        write(STATE / 'dispatch_result.json', {'at': now(), 'exit_code': process.returncode,
              'stdout': process.stdout, 'stderr': process.stderr,
              'start_call_made': True, 'recurring_monitor_must_verify_real_client': True})
        if process.returncode:
            raise RuntimeError('Guarded start did not succeed; inspect receipts, never repeat blindly')
        write(STATE / 'exit.json', {'at': now(), 'completed': True, 'dispatch_invoked_once': True,
              'inference_completion_not_asserted': True})
    except BaseException as error:
        if not (STATE / 'exit.json').exists():
            write(STATE / 'exit.json', {'at': now(), 'completed': False,
                  'error_type': type(error).__name__, 'reason': str(error)[:500]})
        raise
    finally:
        os.close(fd)


def detach():
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Creating a single intent before spawning prevents multiple observers.
    write(STATE / 'launch_intent.json', {'at': now(), 'transfer_pid': TRANSFER_PID,
          'expected_transfer_start': EXPECTED_START})
    with (STATE / 'observer.log').open('x') as out:
        child = subprocess.Popen([sys.executable, '-B', str(Path(__file__).resolve()), 'observe'],
            cwd=REPO, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT,
            start_new_session=True, close_fds=True)
    identity = subprocess.run(['ps', '-p', str(child.pid), '-o', 'lstart=,command='],
                              capture_output=True, text=True, check=True).stdout.strip()
    result = {'at': now(), 'pid': child.pid, 'identity': identity, 'one_shot': True}
    write(STATE / 'launch_receipt.json', result)
    print(json.dumps(result, ensure_ascii=False))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=('detach', 'observe'))
    args = parser.parse_args()
    detach() if args.mode == 'detach' else continue_once()
