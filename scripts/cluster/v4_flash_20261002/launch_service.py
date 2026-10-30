#!/usr/bin/env python3
"""Start one Slurm-owned, authenticated V4 Flash service; no case experiments."""
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request

ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20261002')
PERSONAL = ROOT.parent
RUNTIME = Path('/aifs4su/hansirui/vllm-dsv4-venv')
MODEL = Path('/aifs4su/models/DeepSeek-V4-Flash')
PORT = 18044
ALIAS = 'deepseek_v4_flash'


def checked_dir(path, create=False):
    if path.resolve() != path or PERSONAL not in (path, *path.parents):
        raise RuntimeError('Output path escaped personal directory: ' + str(path))
    for p in (path, *path.parents):
        if p == PERSONAL or PERSONAL in p.parents:
            if p.exists() and (not p.is_dir() or p.stat().st_uid != os.getuid()):
                raise RuntimeError('Unexpected directory owner/type: ' + str(p))
    if create:
        path.mkdir(parents=True, exist_ok=True, mode=0o700)
    return path


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path, obj):
    tmp = path.with_suffix('.tmp')
    with tmp.open('w') as f:
        json.dump(obj, f, indent=2)
        f.write('\n')
    os.replace(tmp, path)


def service_args():
    return [str(RUNTIME / 'bin/python3'), '-B', '-m', 'vllm.entrypoints.openai.api_server',
            '--model', str(MODEL), '--served-model-name', ALIAS,
            '--host', '0.0.0.0', '--port', str(PORT), '--dtype', 'auto',
            '--tensor-parallel-size', '8', '--max-model-len', '32768',
            '--max-num-seqs', '128', '--max-num-batched-tokens', '8192',
            '--gpu-memory-utilization', '0.90', '--kv-cache-dtype', 'fp8',
            '--block-size', '256', '--tokenizer-mode', 'deepseek_v4',
            '--reasoning-parser', 'deepseek_v4', '--trust-remote-code',
            '--enforce-eager', '--no-enable-log-requests']


def main():
    os.umask(0o077)
    job = os.environ.get('SLURM_JOB_ID', '')
    if os.getuid() != 1083 or not job.isdigit() or os.environ.get('SLURM_JOB_NUM_NODES') != '1':
        raise RuntimeError('Expected authorized Unix account and one-node Slurm allocation')
    checked_dir(ROOT)
    manifest = json.loads((ROOT / 'preflight.json').read_text())
    for name, expected in manifest['deployment_files'].items():
        if sha(ROOT / name) != expected:
            raise RuntimeError('Deployment file changed after staging: ' + name)
    for name, expected in manifest['model_files'].items():
        if sha(MODEL / name) != expected:
            raise RuntimeError('Model metadata changed: ' + name)
    for path, expected in manifest['runtime_files'].items():
        if sha(Path(path)) != expected:
            raise RuntimeError('Shared runtime source changed since preflight: ' + path)
    for item in manifest['shards']:
        if (MODEL / item['file']).stat().st_size != item['bytes']:
            raise RuntimeError('Model shard size changed: ' + item['file'])
    lock = os.open(ROOT / 'service.lock', os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with socket.socket() as sock:
        sock.bind(('0.0.0.0', PORT))
    gpu = subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total', '--format=csv,noheader,nounits'],
                         check=True, capture_output=True, text=True, timeout=20).stdout.splitlines()
    if len(gpu) != 8 or any('H800' not in row or int(row.rsplit(',', 1)[1].strip()) < 79000 for row in gpu):
        raise RuntimeError('Allocation is not eight H800 80GB GPUs: ' + repr(gpu))
    attempt = os.environ.get('DVJ_ATTEMPT', '001')
    if not attempt.isdigit() or not 1 <= int(attempt) <= 999:
        raise RuntimeError('Invalid service attempt')
    run = checked_dir(ROOT / 'runs' / job, True) / ('attempt-' + attempt)
    if run.exists():
        raise RuntimeError('Refusing to overwrite an existing attempt')
    checked_dir(run, True)
    short = checked_dir(PERSONAL / 't', True) / ('dvj' + job + 'a' + attempt)
    if short.exists():
        raise RuntimeError('Temporary path already exists')
    checked_dir(short, True)
    env = dict(os.environ)
    env.pop('PYTHONPATH', None)
    for k in list(env):
        if k.startswith(('VLLM_', 'SGLANG_')):
            env.pop(k)
    paths = {
        'TMPDIR': short, 'TMP': short, 'TEMP': short,
        'XDG_CACHE_HOME': run / 'cache', 'XDG_CONFIG_HOME': run / 'config',
        'HF_HOME': run / 'cache/huggingface', 'HF_HUB_CACHE': run / 'cache/huggingface/hub',
        'HF_MODULES_CACHE': run / 'cache/huggingface/modules',
        'TORCH_HOME': run / 'cache/torch', 'TORCHINDUCTOR_CACHE_DIR': run / 'cache/inductor',
        'TRITON_CACHE_DIR': run / 'cache/triton', 'NUMBA_CACHE_DIR': run / 'cache/numba',
        'VLLM_CACHE_ROOT': run / 'cache/vllm', 'VLLM_CONFIG_ROOT': run / 'config/vllm',
        'CUDA_CACHE_PATH': run / 'cache/cuda', 'FLASHINFER_WORKSPACE_BASE': run / 'cache/flashinfer',
        'TORCH_EXTENSIONS_DIR': run / 'cache/torch_extensions', 'TVM_FFI_CACHE_DIR': run / 'cache/tvm-ffi',
    }
    for k, p in paths.items():
        checked_dir(p, True)
        env[k] = str(p)
    api_key = (ROOT / 'api_key').read_text().strip()
    if len(api_key) < 32:
        raise RuntimeError('Missing service authentication key')
    env.update({
        'PATH': str(RUNTIME / 'bin') + ':/cm/shared/apps/cuda-12.8/bin:/usr/bin:/bin',
        'CUDA_HOME': '/cm/shared/apps/cuda-12.8', 'CUDA_PATH': '/cm/shared/apps/cuda-12.8',
        'LD_LIBRARY_PATH': '/cm/shared/apps/cuda-12.8/lib64',
        'PYTHONDONTWRITEBYTECODE': '1', 'PYTHONNOUSERSITE': '1',
        'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_HUB_DISABLE_TELEMETRY': '1',
        'VLLM_NO_USAGE_STATS': '1', 'VLLM_DO_NOT_TRACK': '1', 'DO_NOT_TRACK': '1',
        'VLLM_API_KEY': api_key, 'VLLM_ENGINE_READY_TIMEOUT_S': '1800',
        'VLLM_RPC_TIMEOUT': '1800000', 'VLLM_ENGINE_ITERATION_TIMEOUT_S': '1800',
        'FLASHINFER_NO_DOWNLOAD': '1', 'OMP_NUM_THREADS': '4',
    })
    # Slurm's CUDA_VISIBLE_DEVICES is preserved. No modifications to shared runtime/weights.
    args = service_args()
    state = {'job_id': job, 'attempt': attempt, 'status': 'starting', 'hostname': socket.gethostname(),
             'base_url': f'http://{socket.gethostname()}:{PORT}/v1', 'model': ALIAS,
             'checkpoint': 'deepseek-ai/DeepSeek-V4-Flash (preview, not 0731 or V4.1)',
             'model_revision': manifest['model_revision'], 'api_key_file': str(ROOT / 'api_key'),
             'gpu_count': 8, 'gpus': gpu, 'max_model_len': 32768, 'max_num_seqs': 128,
             'created_at': datetime.datetime.now().astimezone().isoformat(),
             'command_without_secrets': args, 'experiment_started': False}
    write_json(run / 'status.json', state)
    write_json(ROOT / 'endpoint.json', state)
    print(json.dumps(state), flush=True)
    with (run / 'server.log').open('x') as log:
        child = subprocess.Popen(args, env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)

        state['server_pid'] = child.pid
        state['server_process_group'] = child.pid
        write_json(run / 'status.json', state)
        write_json(ROOT / 'endpoint.json', state)
        stopping = False

        def stop(signum, frame):
            nonlocal stopping
            stopping = True
            if child.poll() is None:
                os.killpg(child.pid, signal.SIGTERM)

        signal.signal(signal.SIGTERM, stop)
        signal.signal(signal.SIGINT, stop)
        deadline = time.monotonic() + 1800
        ready = False
        probe_client = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        while child.poll() is None and not stopping and time.monotonic() < deadline:
            try:
                request = urllib.request.Request(f'http://127.0.0.1:{PORT}/v1/models',
                    headers={'Authorization': 'Bearer ' + api_key})
                with probe_client.open(request, timeout=5) as response:
                    models = json.load(response)
                if ALIAS in {m['id'] for m in models['data']}:
                    ready = True
                    break
            except (OSError, ValueError, KeyError):
                pass
            time.sleep(10)
        if not ready and child.poll() is None:
            os.killpg(child.pid, signal.SIGTERM)
            try:
                child.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(child.pid, signal.SIGKILL)
        if ready:
            # One tiny synthetic diagnostic, never a case or experiment prompt.
            payload = {'model': ALIAS, 'messages': [{'role': 'user', 'content': '只输出数字：1+1等于多少？'}],
                       'temperature': 0, 'max_tokens': 32,
                       'chat_template_kwargs': {'thinking': False}}
            request = urllib.request.Request(f'http://127.0.0.1:{PORT}/v1/chat/completions',
                data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + api_key,
                                                            'Content-Type': 'application/json'})
            try:
                with probe_client.open(request, timeout=180) as response:
                    probe = json.load(response)
                write_json(run / 'smoke_response.json', probe)
                content = probe['choices'][0]['message'].get('content') or ''
                state['status'] = 'ready' if content.strip() == '2' else 'needs_smoke_review'
                state['smoke_content'] = content
                state['smoke_usage'] = probe.get('usage')
            except Exception as e:
                state['status'] = 'needs_smoke_review'
                state['smoke_error'] = str(e).replace(api_key, '[REDACTED]')
            state['ready_at'] = datetime.datetime.now().astimezone().isoformat()
            write_json(run / 'status.json', state)
            write_json(ROOT / 'endpoint.json', state)
            print(json.dumps(state), flush=True)
        code = child.wait()
    state.update(status='stopped' if ready else 'failed_startup', exit_code=code,
                 stopped_at=datetime.datetime.now().astimezone().isoformat())
    write_json(run / 'status.json', state)
    write_json(ROOT / 'endpoint.json', state)
    return code if code else (0 if ready else 1)


if __name__ == '__main__':
    sys.exit(main())
