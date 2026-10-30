#!/usr/bin/env python3
"""One independently logged capacity variant inside allocation 407198.

Never edits the original deployment, starts an allocation, or stops its
supervisor. The outer controller must also track/verify the descendant tree:
process-group cleanup alone does not prove all vLLM workers have exited.
Importing this module performs no filesystem, network, or process operations.
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import threading
import time
import urllib.request
import uuid

ORIGINAL_ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20261002')
TUNE_ROOT = ORIGINAL_ROOT.with_name('dvjustice_v4flash_autotune_20261002')
JOB = '407198'
UID = 1083
PORT = 18044
CONCURRENCIES = (32, 64, 128, 192, 256, 384, 512)
DEPLOYMENT_FILES = {'launch_service.py', 'allocation_supervisor.py', 'service.sbatch', 'validate_cli.py'}


def require(condition, message):
    if not condition:
        raise RuntimeError(message)


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def owned_path(path, boundary, uid=None):
    """Reject redirected paths and foreign-owned personal subdirectories."""
    path, boundary = Path(path), Path(boundary)
    uid = os.getuid() if uid is None else uid
    require(path.is_absolute() and '..' not in path.parts, 'Path must be canonical and absolute')
    require(path == boundary or boundary in path.parents, 'Path outside allowed directory')
    require(path.resolve() == path, 'Symlink or redirected path')
    for part in (path, *path.parents):
        if part == boundary or boundary in part.parents:
            try:
                info = part.lstat()
            except FileNotFoundError:
                continue
            require(not stat.S_ISLNK(info.st_mode) and info.st_uid == uid,
                    'Unexpected path owner/type')
    return path


def owned_regular(path, boundary, uid=None):
    path = owned_path(path, boundary, uid)
    require(stat.S_ISREG(path.lstat().st_mode), 'Expected regular file')
    return path


def validate_context(environ, uid):
    require(uid == UID and environ.get('SLURM_JOB_ID') == JOB and
            environ.get('SLURM_JOB_NUM_NODES') == '1', 'Wrong account or allocation')


def read_original(name):
    path = owned_regular(ORIGINAL_ROOT / name, ORIGINAL_ROOT.parent)
    return json.loads(path.read_text(encoding='utf-8'))


def verify_original():
    """Verify frozen code before import; then metadata/runtime/shard inventory."""
    owned_path(ORIGINAL_ROOT, ORIGINAL_ROOT.parent)
    manifest = read_original('preflight.json')
    require(set(manifest['deployment_files']) == DEPLOYMENT_FILES, 'Unexpected deployment manifest')
    for name, expected in manifest['deployment_files'].items():
        path = owned_regular(ORIGINAL_ROOT / name, ORIGINAL_ROOT.parent)
        require(sha(path) == expected, 'Frozen deployment mismatch: ' + name)
    spec = importlib.util.spec_from_file_location('dvj_frozen_launcher', ORIGINAL_ROOT / 'launch_service.py')
    original = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(original)
    require(original.ROOT == ORIGINAL_ROOT and original.PORT == PORT,
            'Original service identity mismatch')
    for name, expected in manifest['model_files'].items():
        require(Path(name).name == name, 'Invalid model metadata path')
        require(sha(original.MODEL / name) == expected, 'Model metadata mismatch')
    for name, expected in manifest['runtime_files'].items():
        require(original.RUNTIME in Path(name).parents, 'Runtime path outside frozen environment')
        require(sha(Path(name)) == expected, 'Runtime source mismatch')
    for item in manifest['shards']:
        require(Path(item['file']).name == item['file'], 'Invalid shard path')
        require((original.MODEL / item['file']).stat().st_size == item['bytes'], 'Model shard size mismatch')
    return original, manifest


def variant_args(original_args, concurrency):
    require(type(concurrency) is int and concurrency in CONCURRENCIES, 'Unsupported concurrency')
    require(isinstance(original_args, list) and all(isinstance(v, str) for v in original_args),
            'Invalid frozen argv')
    require(original_args.count('--max-num-seqs') == 1, 'Ambiguous capacity flag')
    index = original_args.index('--max-num-seqs') + 1
    require(index < len(original_args) and original_args[index] == '128', 'Unexpected frozen capacity')
    result = original_args.copy()
    result[index] = str(concurrency)
    return result


def verify_original_idle(state):
    require(state.get('job_id') == JOB and state.get('allocation_state') == 'waiting_for_repair'
            and state.get('waiting_for_repair') is True and state.get('service_pid') is None
            and state.get('cleanup_unknown') is False and state.get('cleanup_survivors') == [],
            'Original service has not been verifiably stopped')
    require(state.get('process_inspection') == 'verified', 'Original process inspection is unverified')


def acquire_original_service_lock():
    """Open an existing original lock; never create, truncate, or write it."""
    path = owned_regular(ORIGINAL_ROOT / 'service.lock', ORIGINAL_ROOT.parent)
    fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == os.getuid(), 'Invalid service lock')
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return fd
    except BaseException:
        os.close(fd)
        raise


def create_run_dir(path):
    path = owned_path(Path(path), TUNE_ROOT)
    require(path != TUNE_ROOT, 'Run must be a new child directory')
    require(not path.exists(), 'Refusing to overwrite an existing run')
    personal = ORIGINAL_ROOT.parent
    owned_path(TUNE_ROOT, personal)
    for parent in reversed((path.parent, *path.parent.parents)):
        if parent == TUNE_ROOT or TUNE_ROOT in parent.parents:
            owned_path(parent, personal)
            parent.mkdir(mode=0o700, exist_ok=True)
            require(parent.is_dir(), 'Parent is not a directory')
    path.mkdir(mode=0o700, exist_ok=False)
    return path


def create_temp_dir(run):
    """Use a new short owned path, following the original verified IPC setup."""
    personal = ORIGINAL_ROOT.parent
    base = owned_path(personal / 't', personal)
    base.mkdir(mode=0o700, exist_ok=True)
    token = hashlib.sha256(str(run).encode()).hexdigest()[:16]
    path = owned_path(base / ('dvjt' + JOB + token), personal)
    require(not path.exists(), 'Refusing to reuse an existing temporary path')
    path.mkdir(mode=0o700, exist_ok=False)
    return path


def write_json(run, name, value):
    require(Path(name).name == name, 'Invalid output name')
    path = owned_path(run / name, run)
    temp = run / ('.' + name + '.' + uuid.uuid4().hex)
    fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def process_identity(pid):
    path = Path('/proc') / str(pid) / 'stat'
    require(path.stat().st_uid == os.getuid(), 'Unexpected process owner')
    raw = path.read_text(errors='replace')
    fields = raw[raw.rfind(')') + 2:].split()
    require(len(fields) >= 20 and fields[0] != 'Z', 'Missing live process identity')
    return {'pid': int(pid), 'started': fields[19]}


def gpu_inventory(raw):
    rows = [line.strip() for line in raw.splitlines() if line.strip()]
    require(len(rows) == 8, 'Expected eight GPUs')
    for row in rows:
        name, memory = row.rsplit(',', 1)
        require('H800' in name and int(memory.strip()) >= 79000, 'Expected H800 80GB GPUs')
    return rows


def build_environment(inherited, run, runtime, key, temp_path):
    env = dict(inherited)
    env.pop('PYTHONPATH', None)
    for name in list(env):
        if name.startswith(('VLLM_', 'SGLANG_')):
            env.pop(name)
    paths = {'XDG_CACHE_HOME': 'cache',
             'XDG_CONFIG_HOME': 'config', 'HF_HOME': 'cache/huggingface',
             'HF_HUB_CACHE': 'cache/huggingface/hub', 'HF_MODULES_CACHE': 'cache/huggingface/modules',
             'TORCH_HOME': 'cache/torch', 'TORCHINDUCTOR_CACHE_DIR': 'cache/inductor',
             'TRITON_CACHE_DIR': 'cache/triton', 'NUMBA_CACHE_DIR': 'cache/numba',
             'VLLM_CACHE_ROOT': 'cache/vllm', 'VLLM_CONFIG_ROOT': 'config/vllm',
             'CUDA_CACHE_PATH': 'cache/cuda', 'FLASHINFER_WORKSPACE_BASE': 'cache/flashinfer',
             'TORCH_EXTENSIONS_DIR': 'cache/torch_extensions', 'TVM_FFI_CACHE_DIR': 'cache/tvm-ffi'}
    for name, relative in paths.items():
        path = owned_path(run / relative, run)
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
        env[name] = str(path)
    temp_path = owned_path(temp_path, ORIGINAL_ROOT.parent / 't')
    require(temp_path.is_dir(), 'Missing owned temporary directory')
    env.update({name: str(temp_path) for name in ('TMPDIR', 'TMP', 'TEMP')})
    env.update({'PATH': str(runtime / 'bin') + ':/cm/shared/apps/cuda-12.8/bin:/usr/bin:/bin',
                'CUDA_HOME': '/cm/shared/apps/cuda-12.8', 'CUDA_PATH': '/cm/shared/apps/cuda-12.8',
                'LD_LIBRARY_PATH': '/cm/shared/apps/cuda-12.8/lib64',
                'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
                'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
                'VLLM_NO_USAGE_STATS': '1', 'VLLM_DO_NOT_TRACK': '1', 'DO_NOT_TRACK': '1',
                'VLLM_API_KEY': key, 'VLLM_ENGINE_READY_TIMEOUT_S': '1800',
                'VLLM_RPC_TIMEOUT': '1800000', 'VLLM_ENGINE_ITERATION_TIMEOUT_S': '1800',
                'FLASHINFER_NO_DOWNLOAD': '1', 'OMP_NUM_THREADS': '4'})
    return env


def terminate_group(child, grace=30):
    """Bounded own-process-group cleanup; controller checks escaped descendants."""
    if child.poll() is not None:
        return
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        child.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        child.wait(timeout=10)


def wait_ready(child, stopped, opener, key, model, run, timeout=1800):
    deadline = time.monotonic() + timeout
    headers = {'Authorization': 'Bearer ' + key}
    while child.poll() is None and not stopped.is_set() and time.monotonic() < deadline:
        try:
            def request(path, data=None):
                remaining = deadline - time.monotonic()
                require(remaining > 0, 'Startup deadline exceeded')
                req = urllib.request.Request(f'http://127.0.0.1:{PORT}' + path, data=data,
                    headers={**headers, **({'Content-Type': 'application/json'} if data else {})})
                return opener.open(req, timeout=min(180 if data else 5, remaining))
            with request('/health') as response:
                require(response.status == 200, 'Unhealthy service')
            with request('/v1/models') as response:
                models = json.load(response)
            require(model in {m['id'] for m in models['data']}, 'Wrong served model')
        except (OSError, ValueError, KeyError, TypeError, RuntimeError):
            stopped.wait(min(10, max(0, deadline - time.monotonic())))
            continue
        if stopped.is_set():
            return None
        payload = {'model': model, 'messages': [{'role': 'user', 'content': '只输出数字：1+1等于多少？'}],
                   'temperature': 0, 'max_tokens': 32, 'chat_template_kwargs': {'thinking': False}}
        # One diagnostic only; no endless probe retries or production-case calls.
        with request('/v1/chat/completions', json.dumps(payload).encode()) as response:
            probe = json.load(response)
        write_json(run, 'smoke_response.json', probe)
        require((probe['choices'][0]['message'].get('content') or '').strip() == '2',
                'Synthetic readiness probe failed')
        require(child.poll() is None and not stopped.is_set(), 'Service stopped during readiness probe')
        return probe
    return None


def run_variant(concurrency, run_dir):
    os.umask(0o077)
    validate_context(os.environ, os.getuid())
    original, manifest = verify_original()
    args = variant_args(original.service_args(), concurrency)
    verify_original_idle(read_original('allocation_status.json'))
    require(not (ORIGINAL_ROOT / 'restart_request.json').exists(), 'Pending original restart request')
    lock = acquire_original_service_lock()
    try:
        verify_original_idle(read_original('allocation_status.json'))
        with socket.socket() as sock:
            sock.bind(('0.0.0.0', PORT))
        gpus = gpu_inventory(subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total',
            '--format=csv,noheader,nounits'], check=True, capture_output=True, text=True, timeout=20).stdout)
        keyfile = owned_regular(ORIGINAL_ROOT / 'api_key', ORIGINAL_ROOT.parent)
        require(not keyfile.stat().st_mode & 0o077, 'Credential permissions are not private')
        key = keyfile.read_text().strip()
        require(len(key) >= 32, 'Missing service key')
        run = create_run_dir(run_dir)
        temp_path = create_temp_dir(run)
        env = build_environment(os.environ, run, original.RUNTIME, key, temp_path)
        identity = process_identity(os.getpid())
        state = {'job_id': JOB, 'attempt': run.name, 'run_dir': str(run), 'status': 'starting',
                 'launcher_pid': identity['pid'], 'launcher_started': identity['started'],
                 'launcher_identity': identity, 'server_pid': None, 'server_started': None,
                 'server_identity': None, 'max_num_seqs': concurrency, 'max_model_len': 32768,
                 'base_url': f'http://{socket.gethostname()}:{PORT}/v1', 'model': original.ALIAS,
                 'hostname': socket.gethostname(), 'gpu_count': 8, 'gpus': gpus,
                 'model_revision': manifest['model_revision'], 'created_at': now(),
                 'command_without_secrets': args, 'temp_path': str(temp_path),
                 'original_preflight_sha256': sha(ORIGINAL_ROOT / 'preflight.json'),
                 'variant_source_sha256': sha(Path(__file__)), 'experiment_started': False}
        write_json(run, 'endpoint.json', state)
        stopped, child = threading.Event(), None
        old_handlers = {}
        def stop(signum, frame):
            stopped.set()
        for signum in (signal.SIGTERM, signal.SIGINT):
            old_handlers[signum] = signal.signal(signum, stop)
        ready, failure, returncode = False, None, 1
        try:
            with (run / 'server.log').open('x', encoding='utf-8') as log:
                require(not stopped.is_set(), 'Stopped before service launch')
                child = subprocess.Popen(args, env=env, cwd=run, stdout=log,
                                         stderr=subprocess.STDOUT, start_new_session=True)
                server = process_identity(child.pid)
                state.update(server_pid=server['pid'], server_started=server['started'],
                             server_identity=server, server_process_group=child.pid)
                write_json(run, 'endpoint.json', state)
                probe = wait_ready(child, stopped, urllib.request.build_opener(
                    urllib.request.ProxyHandler({})), key, original.ALIAS, run)
                require(probe is not None, 'Service failed to become ready')
                ready = True
                state.update(status='ready', ready_at=now(), smoke_usage=probe.get('usage'))
                write_json(run, 'endpoint.json', state)
                print(json.dumps({k: state[k] for k in ('status', 'job_id', 'max_num_seqs', 'run_dir')}), flush=True)
                while child.poll() is None and not stopped.wait(1):
                    pass
                returncode = 0 if stopped.is_set() else (child.returncode or 1)
        except Exception as exc:
            failure = type(exc).__name__  # Never echo exception text that may contain credentials.
        finally:
            try:
                if child is not None:
                    terminate_group(child)
            except Exception as exc:
                failure, returncode = type(exc).__name__, 1
            for signum, previous in old_handlers.items():
                signal.signal(signum, previous)
            state.update(status='stopped' if ready and stopped.is_set() and failure is None else
                         ('failed_startup' if not ready else 'failed'), ended_at=now(),
                         exit_code=returncode, server_exit_code=child.poll() if child else None,
                         error_type=failure, descendant_cleanup_requires_controller=True)
            write_json(run, 'endpoint.json', state)
            write_json(run, 'exit.json', {k: state[k] for k in ('job_id', 'run_dir', 'status',
                'launcher_identity', 'server_identity', 'exit_code', 'server_exit_code', 'ended_at',
                'error_type', 'descendant_cleanup_requires_controller')})
        return returncode
    finally:
        os.close(lock)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--concurrency', required=True, type=int, choices=CONCURRENCIES)
    parser.add_argument('--run-dir', required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        return run_variant(args.concurrency, args.run_dir)
    except Exception as exc:
        print(json.dumps({'status': 'launch_rejected', 'error_type': type(exc).__name__}), flush=True)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
