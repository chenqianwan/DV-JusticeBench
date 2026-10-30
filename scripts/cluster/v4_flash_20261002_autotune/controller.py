"""One finite, allocation-owned concurrency experiment; never a corpus runner.

The original allocation supervisor is NEVER stopped. The original deployment
is immutable. A variant is permitted only after the original supervisor has
attested complete cleanup. Unknown processes/requests stop the experiment.
"""
from __future__ import annotations

import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request

HERE = Path(__file__).resolve().parent
ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_autotune_20261002')
ORIGINAL = ROOT.parent / 'dvjustice_v4flash_20261002'
RUNTIME = Path('/aifs4su/hansirui/vllm-dsv4-venv/bin/python3')
JOB = '407198'


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def atomic(path, value):
    temporary = path.with_name('.' + path.name + '.' + str(os.getpid()))
    with temporary.open('x') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def import_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def cleanup_attested(state):
    return (str(state.get('job_id')) == JOB
            and state.get('allocation_state') == 'waiting_for_repair'
            and state.get('waiting_for_repair') is True
            and state.get('cleanup_unknown') is False
            and state.get('cleanup_survivors') == []
            and state.get('service_pid') is None
            and state.get('service_identity') is None
            and state.get('process_inspection') == 'verified')


def original_ready(state, endpoint):
    return (str(state.get('job_id')) == JOB and str(endpoint.get('job_id')) == JOB
            and state.get('allocation_state') == 'service_running'
            and state.get('cleanup_unknown') is False
            and state.get('cleanup_survivors') == []
            and state.get('process_inspection') == 'verified'
            and endpoint.get('status') == 'ready'
            and endpoint.get('max_num_seqs') == 128
            and isinstance(state.get('service_identity'), dict)
            and state.get('service_pid') == state['service_identity'].get('pid'))


def initialize_rounds(root):
    if root.resolve() != root or root.stat().st_uid != os.getuid():
        raise RuntimeError('Untrusted benchmark output root')
    path = root / 'rounds'
    path.mkdir(mode=0o700, exist_ok=False)
    return path


def validate_stage():
    if os.getuid() != 1083 or os.environ.get('SLURM_JOB_ID') != JOB:
        raise RuntimeError('Wrong account or allocation')
    if os.environ.get('SLURM_JOB_NUM_NODES') != '1':
        raise RuntimeError('Expected exactly one node')
    for path in [ROOT.parent, ROOT, ORIGINAL, HERE]:
        if path.resolve() != path or path.stat().st_uid != os.getuid():
            raise RuntimeError('Untrusted path')
    stage = json.loads((ROOT / 'stage_manifest.json').read_text())
    if stage['job_id'] != JOB or stage.get('benchmark_only') is not True:
        raise RuntimeError('Wrong staged scope')
    for name, digest in stage['files'].items():
        path = ROOT / name
        if path.resolve() != path or ROOT not in path.parents or sha(path) != digest:
            raise RuntimeError('Frozen benchmark file changed')
    prepared = json.loads((ROOT / 'prepared/manifest.json').read_text())
    if (prepared.get('slots') != 1024 or prepared.get('benchmark_only') is not True
            or prepared.get('reference_ready') is not False
            or prepared.get('formal_hallucination_statistics_allowed') is not False
            or prepared['output_sha256']['requests.jsonl'] != stage['files']['prepared/requests.jsonl']):
        raise RuntimeError('Prepared workload does not match frozen benchmark scope')
    original = json.loads((ORIGINAL / 'preflight.json').read_text())
    if sha(ORIGINAL / 'preflight.json') != stage['original_preflight_sha256']:
        raise RuntimeError('Original preflight changed')
    for name, digest in original['deployment_files'].items():
        if sha(ORIGINAL / name) != digest:
            raise RuntimeError('Original deployment changed')
    return stage


class Services:
    def __init__(self, supervisor, bench):
        self.sup = supervisor
        self.bench = bench
        self.process = None
        self.tree = None
        self.endpoint = ORIGINAL / 'endpoint.json'
        self.concurrency = 128
        self.iteration = 0
        self.scan_stop = threading.Event()
        self.scan_thread = None
        self.scan_error = None

    def supervisor_state(self):
        row = self.sup.read_json(ORIGINAL, ORIGINAL / 'allocation_status.json')
        pid = row.get('supervisor_pid')
        identity = self.sup.read_process_identity(pid) if type(pid) is int else None
        if identity is None or str(row.get('job_id')) != JOB:
            raise RuntimeError('Original supervisor unavailable')
        command = Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        if str(ORIGINAL / 'allocation_supervisor.py').encode() not in command:
            raise RuntimeError('Original supervisor identity mismatch')
        return row

    def metrics(self):
        key = (ORIGINAL / 'api_key').read_text().strip()
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        req = urllib.request.Request('http://127.0.0.1:18044/metrics',
                                     headers={'Authorization': 'Bearer ' + key})
        with opener.open(req, timeout=10) as response:
            raw = response.read().decode()
        # Fail closed if either counter is absent. Never treat missing as zero.
        counts = {}
        for line in raw.splitlines():
            if not line or line.startswith('#'):
                continue
            name = line.split('{', 1)[0].split(' ', 1)[0]
            if name in ('vllm:num_requests_running', 'vllm:num_requests_waiting'):
                counts[name] = counts.get(name, 0) + float(line.rsplit(' ', 1)[1])
        if len(counts) != 2:
            raise RuntimeError('Missing request telemetry')
        return counts

    def require_idle(self):
        for _ in range(3):
            if any(self.metrics().values()):
                raise RuntimeError('Requests still executing; do not switch services')
            time.sleep(2)

    def require_lock_and_port_free(self):
        fd = os.open(ORIGINAL / 'service.lock', os.O_RDWR | os.O_NOFOLLOW)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with socket.socket() as sock:
                sock.bind(('0.0.0.0', 18044))
        finally:
            os.close(fd)

    def stop_original(self):
        self.require_idle()
        row = self.supervisor_state()
        endpoint = self.sup.read_json(ORIGINAL, ORIGINAL / 'endpoint.json')
        if not original_ready(row, endpoint):
            raise RuntimeError('Original service not safely stoppable')
        if (ORIGINAL / 'restart_request.json').exists():
            raise RuntimeError('Concurrent service management request')
        identity = row['service_identity']
        actual = self.sup.read_process_identity(identity['pid'])
        if actual is None or actual['started'] != identity['started']:
            raise RuntimeError('Original launcher identity changed')
        command = Path(f"/proc/{identity['pid']}/cmdline").read_bytes().split(b'\0')
        if str(ORIGINAL / 'launch_service.py').encode() not in command:
            raise RuntimeError('Not the original launcher')
        # Only this proven launcher. The allocation supervisor remains alive.
        self.sup.signal_owned(identity, signal.SIGTERM)
        deadline = time.monotonic() + 180
        while time.monotonic() < deadline:
            if cleanup_attested(self.supervisor_state()):
                self.require_lock_and_port_free()
                return
            time.sleep(1)
        raise RuntimeError('Original service cleanup not attested')

    def begin_tracking(self):
        self.scan_stop.clear()
        self.scan_error = None
        self.tree = self.sup.ProcessTree(self.process.pid)
        if self.tree.discovery_gap:
            raise RuntimeError('Initial variant identity unavailable')

        def scan():
            while not self.scan_stop.wait(0.5):
                try:
                    self.tree.refresh()
                except self.sup.ProcessSnapshotError as exc:
                    self.scan_error = exc.reason
        self.scan_thread = threading.Thread(target=scan, daemon=True)
        self.scan_thread.start()

    def stop_variant(self):
        if self.process is None:
            raise RuntimeError('No owned variant handle')
        self.scan_stop.set()
        if self.scan_thread:
            self.scan_thread.join(timeout=5)
        # stop_tree performs a fresh complete inventory and pidfd-pinned signals.
        survivors = self.sup.stop_tree(self.process, self.tree, grace=60, kill_timeout=15)
        if survivors:
            raise RuntimeError('Variant cleanup incomplete')
        self.process.wait(timeout=10)
        self.process = None
        self.tree = None
        if not cleanup_attested(self.supervisor_state()):
            raise RuntimeError('Original supervisor state changed during test')
        self.require_lock_and_port_free()

    def start_variant(self, concurrency):
        if not cleanup_attested(self.supervisor_state()):
            raise RuntimeError('Original supervisor has not attested cleanup')
        self.require_lock_and_port_free()
        self.iteration += 1
        path = ROOT / 'services' / f'{self.iteration:03d}-c{concurrency}'
        path.parent.mkdir(exist_ok=True, mode=0o700)
        logs = ROOT / 'launcher_logs'
        logs.mkdir(exist_ok=True, mode=0o700)
        with (logs / (path.name + '.log')).open('x') as log:
            self.process = subprocess.Popen([str(RUNTIME), '-B', str(HERE / 'variant_launcher.py'),
                '--concurrency', str(concurrency), '--run-dir', str(path)],
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
        self.begin_tracking()
        self.endpoint = path / 'endpoint.json'
        self.concurrency = concurrency
        atomic(ROOT / 'service_control.json', {'status': 'starting', 'concurrency': concurrency,
            'variant_identity': self.tree.owned[self.process.pid], 'endpoint_file': str(self.endpoint),
            'job_id': JOB, 'updated_at': now()})
        deadline = time.monotonic() + 2100
        while time.monotonic() < deadline:
            if self.process.poll() is not None:
                raise RuntimeError('Variant exited during startup')
            if self.scan_error:
                raise RuntimeError('Variant process observation failed')
            if self.endpoint.exists():
                state = json.loads(self.endpoint.read_text())
                if state.get('status') == 'ready' and state.get('max_num_seqs') == concurrency:
                    self.require_idle()
                    atomic(ROOT / 'service_control.json', {'status': 'ready', 'concurrency': concurrency,
                        'variant_identity': self.tree.owned[self.process.pid], 'endpoint_file': str(self.endpoint),
                        'job_id': JOB, 'updated_at': now()})
                    return
            time.sleep(2)
        raise RuntimeError('Variant startup deadline exceeded')

    def restore_original(self):
        if not cleanup_attested(self.supervisor_state()):
            raise RuntimeError('Unsafe original restart')
        self.require_lock_and_port_free()
        path = ORIGINAL / 'restart_request.json'
        request = {'job_id': JOB, 'request_id': 'autotune-restore-' + str(time.time_ns())}
        # O_EXCL: never overwrite a request from another operator.
        with path.open('x') as stream:
            json.dump(request, stream)
            stream.flush()
            os.fsync(stream.fileno())
        deadline = time.monotonic() + 2100
        while time.monotonic() < deadline:
            row = self.supervisor_state()
            if row.get('last_restart_request_id') == request['request_id']:
                endpoint = self.sup.read_json(ORIGINAL, ORIGINAL / 'endpoint.json')
                if original_ready(row, endpoint):
                    self.endpoint = ORIGINAL / 'endpoint.json'
                    self.concurrency = 128
                    self.require_idle()
                    atomic(ROOT / 'service_control.json', {'status': 'ready', 'concurrency': 128,
                        'variant_identity': None, 'endpoint_file': str(self.endpoint),
                        'job_id': JOB, 'updated_at': now(), 'owner': 'original_supervisor'})
                    return
            time.sleep(2)
        raise RuntimeError('Original restore did not become ready')

    def switch(self, concurrency):
        if concurrency == self.concurrency:
            return
        self.require_idle()
        if self.process is None:
            self.stop_original()
        else:
            self.stop_variant()
        if concurrency == 128:
            self.restore_original()
        else:
            self.start_variant(concurrency)


def main():
    os.umask(0o077)
    validate_stage()
    lock = os.open(ROOT / 'controller.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    state_path = ROOT / 'controller_state.json'
    if state_path.exists():
        raise RuntimeError('Controller has prior intent/state; inspect, never auto-repeat')
    sup = import_file('autotune_supervisor', HERE / 'allocation_supervisor_frozen.py')
    bench = import_file('bench', HERE / 'bench.py')
    policy = import_file('policy', HERE / 'policy.py')
    services = Services(sup, bench)
    state = {'job_id': JOB, 'status': 'starting', 'created_at': now(),
             'controller_identity': sup.read_process_identity(os.getpid()),
             'node': socket.gethostname(), 'benchmark_only': True,
             'formal_hallucination_statistics_allowed': False, 'rounds': []}
    atomic(state_path, state)
    drained = False
    try:
        initialize_rounds(ROOT)
        row = services.supervisor_state()
        endpoint = sup.read_json(ORIGINAL, ORIGINAL / 'endpoint.json')
        if not original_ready(row, endpoint):
            raise RuntimeError('Original service not ready for baseline')
        services.require_idle()
        drained = True
        while True:
            decision = policy.next_action(state['rounds'])
            state.update(decision=decision, updated_at=now())
            atomic(state_path, state)
            if decision['action'] == 'blocked':
                raise RuntimeError('Policy blocked: ' + decision['reason'])
            if decision['action'] == 'select':
                state['status'] = 'activating_selection'
                atomic(state_path, state)
                services.switch(decision['selected_concurrency'])
                selection = {'job_id': JOB, 'selected_concurrency': services.concurrency,
                    'endpoint_file': str(services.endpoint), 'decision': decision, 'completed_at': now(),
                    'benchmark_only': True, 'formal_hallucination_statistics_allowed': False}
                atomic(ROOT / 'selection.json', selection)
                state.update(status='selected', selection=selection, updated_at=now())
                atomic(state_path, state)
                # A variant needs this owner's lifetime, not an unmanaged child.
                # Hold it until allocation expiry; the heartbeat stops on selection.
                if services.process is not None:
                    while services.process.poll() is None:
                        time.sleep(10)
                    state.update(status='selected_service_exited', exit_code=services.process.returncode,
                                 updated_at=now(), retained_variant_owner=True)
                    atomic(state_path, state)
                    # Do not turn an uncertain descendant/request state into a
                    # successful step exit. A subsequent operator must reconcile.
                    while True:
                        time.sleep(30)
                return 0
            concurrency = decision['concurrency']
            state.update(status='switching_service', next_concurrency=concurrency)
            atomic(state_path, state)
            services.switch(concurrency)
            services.require_idle()
            state.update(status='benchmarking', active_concurrency=concurrency, updated_at=now())
            atomic(state_path, state)
            validate_stage()  # Verify again BEFORE sending, not after consuming the round.
            drained = False
            result = bench.run_round(concurrency=concurrency, requests_path=ROOT / 'prepared/requests.jsonl',
                output_dir=ROOT / 'rounds' / f'c{concurrency}', base_url='http://127.0.0.1:18044/v1',
                api_key_file=ORIGINAL / 'api_key', timeout_s=1800, sample_interval=5)
            state['rounds'].append(result)
            drained = result.get('drained') is True
            atomic(state_path, state)
            if not drained:
                raise RuntimeError('Unreconciled benchmark requests; service retained, no retry')
            if services.scan_error:
                raise RuntimeError('Variant process observation requires reconciliation')
    except Exception as exc:
        # Exception text can contain remote responses; do not persist it or keys.
        failed_phase = state.get('status')
        state.update(status='blocked', failed_phase=failed_phase, error_type=type(exc).__name__, updated_at=now(),
                     requests_drained=drained, endpoint_file=str(services.endpoint))
        atomic(state_path, state)
        # Only already-drained, observed variant processes may be replaced.
        if drained and services.process is not None and not services.scan_error:
            try:
                if failed_phase not in ('switching_service', 'activating_selection'):
                    services.require_idle()
                services.stop_variant()
                services.restore_original()
                state.update(fallback='original_128_restored', endpoint_file=str(services.endpoint))
            except Exception as recovery:
                state['fallback_error_type'] = type(recovery).__name__
            atomic(state_path, state)
        # Ending the Slurm step can terminate its descendants. Preserve the
        # owner when cleanup or requests are uncertain; a later heartbeat must
        # inspect, not launch a second controller or kill unknown requests.
        if services.process is not None:
            state['retained_variant_owner'] = True
            atomic(state_path, state)
            while True:
                time.sleep(30)
        return 1
    finally:
        os.close(lock)


if __name__ == '__main__':
    sys.exit(main())
