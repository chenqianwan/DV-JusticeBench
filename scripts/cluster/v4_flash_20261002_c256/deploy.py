#!/usr/bin/env python3
"""Stage/submit/status a single reviewed 8-GPU service, with duplicate guards."""
import argparse
import base64
import datetime
import json
from pathlib import Path
import subprocess
import shlex

BASE = Path(__file__).resolve().parent
REMOTE = r'''
import ast, base64, datetime, fcntl, hashlib, importlib.metadata, json, os, pathlib, re, secrets, shlex, subprocess, sys
request = json.load(sys.stdin)
mode = request['mode']
root = pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20261002_c256')
personal = root.parent
model = pathlib.Path('/aifs4su/models/DeepSeek-V4-Flash')
runtime = pathlib.Path('/aifs4su/hansirui/vllm-dsv4-venv')
site = runtime / 'lib/python3.11/site-packages'
B = '/cm/shared/apps/slurm/current/bin/'
E = {'PATH':'/usr/bin:/bin', 'LANG':'C.UTF-8', 'SLURM_CONF':'/cm/shared/apps/slurm/var/etc/slurm/slurm.conf'}
if os.getuid() != 1083:
    raise RuntimeError('Unexpected Unix account')
os.umask(0o077)

def now(): return datetime.datetime.now().astimezone().isoformat()
def sha(data): return hashlib.sha256(data).hexdigest()
def checked(path):
    if path.resolve() != path or personal not in (path,*path.parents):
        raise RuntimeError('Redirected or out-of-scope path: '+str(path))
    for p in (path,*path.parents):
        if p == personal or personal in p.parents:
            if p.exists() and p.stat().st_uid != os.getuid():
                raise RuntimeError('Unexpected owner: '+str(p))
    return path
def save(path,data):
    checked(path)
    temp=checked(path.with_suffix('.tmp'))
    with temp.open('w') as f:
        json.dump(data,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
def run(args,timeout=30):
    p=subprocess.run(args,env=E,capture_output=True,text=True,timeout=timeout)
    if p.returncode: raise RuntimeError('Command failed: '+repr(args)+' '+p.stderr[:2000]+p.stdout[:2000])
    return p.stdout
def job_status(job):
    p=subprocess.run([B+'scontrol','-a','show','job',job,'-o'],env=E,capture_output=True,text=True,timeout=20)
    keys=['JobId','JobName','UserId','Account','Partition','QOS','Nice','JobState','Reason','NodeList','NumNodes','NumCPUs','ReqTRES','AllocTRES','TimeLimit','StartTime','EndTime','Command','Comment']
    return {'returncode':p.returncode,'stderr':p.stderr,'fields':{k:m.group(1) for k in keys if (m:=re.search(r'(?:^|\s)'+k+r'=(\S+)',p.stdout))}}

if mode == 'stage':
    files={name:base64.b64decode(data,validate=True) for name,data in request['files'].items()}
    if set(files) != {'service.sbatch','launch_service.py','allocation_supervisor.py','validate_cli.py'}: raise RuntimeError('Unexpected deployment file')
    for name,data in files.items():
        if name.endswith('.py'): ast.parse(data.decode())
    p=subprocess.run(['/bin/bash','-n'],input=files['service.sbatch'],env=E,capture_output=True,timeout=10)
    if p.returncode: raise RuntimeError('Invalid batch script: '+p.stderr.decode())
    directives={}
    for line in files['service.sbatch'].decode().splitlines():
        if line.startswith('#SBATCH '):
            args=shlex.split(line[8:]);key,value=(args[0].split('=',1) if '=' in args[0] else (args[0],True))
            if len(args)!=1 or key in directives: raise RuntimeError('Ambiguous batch directive')
            directives[key]=value
    expected={'--job-name':'chenlong_dvj_v4flash','--partition':'llm','--qos':'llm','--account':'hansirui',
        '--nice':'0','--nodes':'1','--ntasks':'1','--cpus-per-task':'64','--gres':'gpu:8',
        '--mem':'512G','--time':'5-00:00:00','--no-requeue':True,'--export':'NONE',
        '--chdir':str(root),'--output':str(root/'logs/slurm-%j.out'),'--error':str(root/'logs/slurm-%j.err')}
    if directives != expected: raise RuntimeError('Batch allocation differs from approved configuration')
    if (root/'submission.json').exists(): raise RuntimeError('Already has submission record; do not restage')
    model_files={name:sha((model/name).read_bytes()) for name in ['config.json','generation_config.json','model.safetensors.index.json','tokenizer.json','tokenizer_config.json']}
    if model_files['config.json']!='b628e63398a645abc711d92207f8737dd8140f7a4ef1e0a5b3616019e0ddd818': raise RuntimeError('Unexpected checkpoint configuration')
    if model_files['model.safetensors.index.json']!='7e975ba3bef8947a94e7da0abd60888375b232b4dfad883d59653e65c6ba522a': raise RuntimeError('Unexpected checkpoint index')
    # Read shared checkpoint provenance without changing Git's ownership settings.
    gitdir=model/'.git'
    revision=(gitdir/'HEAD').read_text().strip()
    if revision.startswith('ref: '):
        ref=revision[5:]
        if not ref.startswith('refs/') or '..' in pathlib.PurePosixPath(ref).parts:
            raise RuntimeError('Unexpected checkpoint Git reference')
        if (gitdir/ref).is_file():
            revision=(gitdir/ref).read_text().strip()
        else:
            refs={parts[1]:parts[0] for line in (gitdir/'packed-refs').read_text().splitlines()
                  if len(parts:=line.split())==2 and not line.startswith(('#','^'))}
            revision=refs.get(ref,'')
    if revision != '60d8d70770c6776ff598c94bb586a859a38244f1': raise RuntimeError('Model revision changed')
    index=json.loads((model/'model.safetensors.index.json').read_bytes())
    shards=[]
    for name in sorted(set(index['weight_map'].values())):
        rel=pathlib.PurePosixPath(name)
        if rel.is_absolute() or '..' in rel.parts: raise RuntimeError('Unsafe shard path')
        path=model/name
        if not path.is_file() or not os.access(path,os.R_OK) or path.stat().st_size<1000: raise RuntimeError('Invalid/unreadable shard: '+name)
        shards.append({'file':name,'bytes':path.stat().st_size})
    if len(shards)!=46 or sum(s['bytes'] for s in shards)!=159617149040: raise RuntimeError('Shard inventory changed')
    relative_sources=['vllm/model_executor/models/registry.py','vllm/model_executor/models/deepseek_v4.py',
        'vllm/model_executor/layers/fused_moe/oracle/mxfp4.py','vllm/model_executor/layers/fused_moe/fused_marlin_moe.py',
        'vllm/tokenizers/registry.py','vllm/reasoning/__init__.py','vllm/entrypoints/openai/api_server.py',
        'vllm/entrypoints/openai/cli_args.py','vllm/utils/argparse_utils.py','vllm/envs.py']
    runtime_files={str(site/path):sha((site/path).read_bytes()) for path in relative_sources}
    packages={}
    for d in importlib.metadata.distributions(path=[str(site)]):
        if d.metadata['Name'].lower() in ['vllm','torch','transformers','flashinfer-python','triton']:
            packages[d.metadata['Name'].lower()]=d.version
    if packages.get('vllm') != '0.20.0+cu129': raise RuntimeError('Unexpected runtime version')
    for tool in [runtime/'bin/python3',runtime/'bin/ninja',pathlib.Path('/cm/shared/apps/cuda-12.8/bin/nvcc'),pathlib.Path('/usr/bin/gcc'),pathlib.Path('/usr/bin/g++')]:
        if not os.access(tool,os.X_OK): raise RuntimeError('Missing tool: '+str(tool))
    checked(root).mkdir(mode=0o700,exist_ok=True)
    checked(root/'logs').mkdir(mode=0o700,exist_ok=True)
    for name,data in files.items():
        p=checked(root/name)
        if p.exists():
            if p.read_bytes()!=data: raise RuntimeError('Refusing to overwrite staged file: '+name)
        else:
            with p.open('xb') as f: f.write(data)
    key=checked(root/'api_key')
    if not key.exists():
        fd=os.open(key,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
        with os.fdopen(fd,'w') as f:f.write(secrets.token_urlsafe(48)+'\n')
    if key.stat().st_mode & 0o077: raise RuntimeError('API key permissions too broad')
    manifest={'created_at':now(),'model_path':str(model),'model_resolved_path':str(model.resolve()),
        'model_revision':revision,'model_files':model_files,'shards':shards,'shard_validation':'existence/readability/size; full tensor SHA256 not recalculated',
        'runtime_path':str(runtime),'runtime_packages':packages,'runtime_files':runtime_files,
        'deployment_files':{name:sha(data) for name,data in files.items()},'resources':expected,
        'checkpoint_variant':'DeepSeek-V4-Flash preview; not V4-Flash-0731/V4.1',
        'experiment_started':False,'api_key_file':str(key),
        'parent_deployment_job':'407198','failure_policy':'Keep Slurm allocation for repair until scheduler deadline or termination; no automatic retry loop'}
    save(root/'preflight.json',manifest)
    print(json.dumps({'status':'staged','root':str(root),'preflight':manifest}))
elif mode == 'cli-check':
    checked(root)
    manifest=json.loads((root/'preflight.json').read_text())
    for name,digest in manifest['deployment_files'].items():
        if sha(checked(root/name).read_bytes())!=digest: raise RuntimeError('Staged file changed')
    work=checked(root/'cli_preflight')
    work.mkdir(mode=0o700,exist_ok=True)
    env=dict(E)
    for key,name in {'TMPDIR':'tmp','XDG_CACHE_HOME':'cache','HF_HOME':'cache/hf',
        'HF_MODULES_CACHE':'cache/hf/modules','TORCH_HOME':'cache/torch','TRITON_CACHE_DIR':'cache/triton',
        'VLLM_CACHE_ROOT':'cache/vllm','VLLM_CONFIG_ROOT':'config/vllm',
        'CUDA_CACHE_PATH':'cache/cuda','TORCH_EXTENSIONS_DIR':'cache/torch_extensions',
        'FLASHINFER_WORKSPACE_BASE':'cache/flashinfer','TVM_FFI_CACHE_DIR':'cache/tvm'}.items():
        path=checked(work/name);path.mkdir(parents=True,exist_ok=True,mode=0o700);env[key]=str(path)
    env.update({'PATH':str(runtime/'bin')+':/cm/shared/apps/cuda-12.8/bin:/usr/bin:/bin',
        'CUDA_HOME':'/cm/shared/apps/cuda-12.8','CUDA_VISIBLE_DEVICES':'',
        'PYTHONNOUSERSITE':'1','PYTHONDONTWRITEBYTECODE':'1','HF_HUB_OFFLINE':'1',
        'TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1','VLLM_NO_USAGE_STATS':'1',
        'VLLM_DO_NOT_TRACK':'1','FLASHINFER_NO_DOWNLOAD':'1','OMP_NUM_THREADS':'1'})
    p=subprocess.run([str(runtime/'bin/python3'),'-B',str(root/'validate_cli.py')],
        env=env,capture_output=True,text=True,timeout=180,cwd=str(root))
    result={'at':now(),'exit_code':p.returncode,'stdout':p.stdout[-16000:],'stderr':p.stderr[-16000:],
        'deployment_files':manifest['deployment_files'],'runtime_files':manifest['runtime_files'],
        'gpu_inference_started':False}
    save(root/'cli_validation.json',result)
    print(json.dumps(result))
    if p.returncode: raise SystemExit(p.returncode)
elif mode == 'submit':
    checked(root)
    manifest=json.loads((root/'preflight.json').read_text())
    for name,digest in manifest['deployment_files'].items():
        if sha(checked(root/name).read_bytes())!=digest: raise RuntimeError('Staged file changed')
    validation=json.loads(checked(root/'cli_validation.json').read_text())
    if validation['exit_code']!=0 or validation['deployment_files']!=manifest['deployment_files']:
        raise RuntimeError('Full CLI validation has not passed for this exact deployment')
    for path,digest in validation['runtime_files'].items():
        if sha(pathlib.Path(path).read_bytes())!=digest: raise RuntimeError('Runtime changed since validation')
    fd=os.open(checked(root/'submission.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        receipt_path=checked(root/'submission.json')
        if receipt_path.exists():
            receipt=json.loads(receipt_path.read_text())
            if receipt.get('status')!='submitted' or not str(receipt.get('job_id','')).isdigit():
                raise RuntimeError('Existing ambiguous intent: reconcile before retry')
        else:
            live=run([B+'squeue','-a','-h','-u','hansirui_3rd','-n','chenlong_dvj_v4flash','-o','%i|%j|%T'])
            if live.strip(): raise RuntimeError('Matching job already exists: '+live)
            comment='chenlong-dvjustice-v4flash-20261002-c256'
            receipt={'status':'submitting','requested_at':now(),'comment':comment,'script':str(root/'service.sbatch'),
                     'script_sha256':manifest['deployment_files']['service.sbatch'],'resources':manifest['resources']}
            save(receipt_path,receipt)
            try:
                p=subprocess.run([B+'sbatch','--parsable','--nice=0','--comment='+comment,str(root/'service.sbatch')],
                    env=E,capture_output=True,text=True,timeout=40)
            except Exception as exc:
                receipt.update(status='needs_reconciliation',error=str(exc));save(receipt_path,receipt);raise
            receipt.update(sbatch_exit_code=p.returncode,sbatch_stdout=p.stdout,sbatch_stderr=p.stderr)
            match=re.fullmatch(r'(\d+)(?:;[^\s;]+)?',p.stdout.strip())
            if p.returncode or not match:
                receipt['status']='needs_reconciliation';save(receipt_path,receipt)
                raise RuntimeError('Ambiguous/failed submission; inspect receipt, do not resubmit automatically')
            receipt.update(status='submitted',job_id=match.group(1));save(receipt_path,receipt)
        verification=job_status(receipt['job_id'])
        receipt['verification']=verification;save(receipt_path,receipt)
        print(json.dumps(receipt))
elif mode == 'status':
    receipt=json.loads(checked(root/'submission.json').read_text())
    api_key=checked(root/'api_key').read_text().strip()
    result={'at':now(),'receipt':receipt,'live_job':job_status(receipt['job_id'])}
    for name in ['endpoint.json','allocation_status.json']:
        if (root/name).exists():result[name]=json.loads((root/name).read_text())
    attempt=str(result.get('endpoint.json',{}).get('attempt','001'))
    if not attempt.isdigit(): raise RuntimeError('Invalid service attempt')
    for path in [root/'logs'/('slurm-'+receipt['job_id']+'.out'),root/'logs'/('slurm-'+receipt['job_id']+'.err'),root/'runs'/receipt['job_id']/('attempt-'+attempt)/'server.log']:
        if path.exists():
            with path.open('rb') as f:
                f.seek(max(0,path.stat().st_size-16000));tail=f.read().decode(errors='replace')
            result[str(path)]=tail.replace(api_key,'[REDACTED]') if api_key else tail
    print(json.dumps(result))
else: raise RuntimeError('Unsupported mode')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['stage', 'cli-check', 'submit', 'status'])
    args = parser.parse_args()
    payload = {'mode': args.mode}
    if args.mode == 'stage':
        payload['files'] = {p: base64.b64encode((BASE / p).read_bytes()).decode()
                            for p in ['service.sbatch', 'launch_service.py', 'allocation_supervisor.py', 'validate_cli.py']}
    # Argument parsing needs a CUDA-capable host even without allocating a CUDA
    # context. This mode hides all GPUs and never starts an engine or loads weights.
    host = 'Group3_dgx-127' if args.mode == 'cli-check' else 'Group3_slogin'
    response = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
                               '-o', 'StrictHostKeyChecking=yes', host,
                               'python3 -B -c ' + shlex.quote(REMOTE)],
                              input=json.dumps(payload), capture_output=True, text=True, timeout=240)
    record = {'at': datetime.datetime.now().astimezone().isoformat(), 'mode': args.mode,
              'host': host, 'exit_code': response.returncode, 'stderr': response.stderr}
    try:
        record['result'] = json.loads(response.stdout)
    except json.JSONDecodeError:
        record['stdout'] = response.stdout
    records = BASE / 'records'
    records.mkdir(exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')
    with (records / (args.mode + '_' + stamp + '.json')).open('x') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps(record, ensure_ascii=False, indent=2))
    raise SystemExit(response.returncode)


if __name__ == '__main__':
    main()
