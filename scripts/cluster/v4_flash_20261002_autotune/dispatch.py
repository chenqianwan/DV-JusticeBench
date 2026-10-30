"""Append-only stage/check/start of one autotune step in existing job 407198."""
from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

BASE = Path(__file__).resolve().parent
REPO = BASE.parents[2]
LOCAL = REPO / 'data/pkulaw_v4_autotune_20261002'

REMOTE = r'''
import ast,base64,datetime,fcntl,hashlib,json,os,pathlib,re,subprocess,sys,urllib.request
request=json.load(sys.stdin)
mode=request['mode']
root=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_autotune_20261002')
original=root.parent/'dvjustice_v4flash_20261002'
job='407198'
bin='/cm/shared/apps/slurm/current/bin/'
env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','SLURM_CONF':'/cm/shared/apps/slurm/var/etc/slurm/slurm.conf'}
os.umask(0o077)
if os.getuid()!=1083: raise RuntimeError('Wrong account')
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(data): return hashlib.sha256(data).hexdigest()
def checked(path):
    if path.resolve()!=path or root.parent not in path.parents: raise RuntimeError('Unsafe path')
    for p in (path,*path.parents):
        if p==root.parent or root.parent in p.parents:
            if p.exists() and p.stat().st_uid!=os.getuid(): raise RuntimeError('Unexpected owner')
    return path
def read(path):
    checked(path)
    return json.loads(path.read_text()) if path.exists() else None
def save_new(path,value):
    with checked(path).open('x') as f:
        json.dump(value,f,indent=2);f.flush();os.fsync(f.fileno())
def status():
    p=subprocess.run([bin+'scontrol','-a','show','job',job,'-o'],env=env,capture_output=True,text=True,timeout=20)
    if p.returncode: return {'lookup_failed':True,'returncode':p.returncode}
    fields={k:m.group(1) for k in ['JobId','JobName','UserId','JobState','Reason','NodeList','NumNodes','AllocTRES','StartTime','EndTime','TimeLimit','Command'] if (m:=re.search(r'(?:^|\s)'+k+r'=(\S+)',p.stdout))}
    if fields.get('JobId')!=job or fields.get('Command')!=str(original/'service.sbatch') or fields.get('UserId')!='hansirui_3rd(1083)': raise RuntimeError('Unexpected job identity')
    return fields
def verify():
    manifest=read(root/'stage_manifest.json')
    if not manifest or manifest.get('job_id')!=job: raise RuntimeError('No frozen stage')
    for name,digest in manifest['files'].items():
        path=checked(root/name)
        if root not in path.parents or sha(path.read_bytes())!=digest: raise RuntimeError('Frozen stage changed')
    if sha((original/'preflight.json').read_bytes())!=manifest['original_preflight_sha256']: raise RuntimeError('Original preflight changed')
    return manifest
if mode=='stage':
    if root.exists():
        manifest=verify()
        proposed={name:sha(base64.b64decode(data,validate=True)) for name,data in request['files'].items()}
        if proposed!=manifest['files']: raise RuntimeError('Refuse to overwrite different staged revision')
        print(json.dumps({'status':'already_staged','manifest':manifest}));sys.exit(0)
    checked(root).mkdir(mode=0o700)
    files={name:base64.b64decode(data,validate=True) for name,data in request['files'].items()}
    if set(files)!=set(request['expected_files']): raise RuntimeError('Unexpected staged files')
    for name,data in files.items():
        path=checked(root/name)
        if root not in path.parents: raise RuntimeError('Outside stage')
        if name.endswith('.py'): ast.parse(data.decode())
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with path.open('xb') as f:f.write(data)
    manifest={'job_id':job,'created_at':now(),'benchmark_only':True,'formal_hallucination_statistics_allowed':False,
              'files':{n:sha(d) for n,d in files.items()},'original_preflight_sha256':sha((original/'preflight.json').read_bytes())}
    old=read(original/'preflight.json')
    if manifest['files']['code/allocation_supervisor_frozen.py']!=old['deployment_files']['allocation_supervisor.py']: raise RuntimeError('Supervisor source not frozen original')
    save_new(root/'stage_manifest.json',manifest)
    print(json.dumps({'status':'staged','manifest':manifest}));sys.exit(0)
snapshot={'checked_at':now(),'job':status(),'stage_present':(root/'stage_manifest.json').exists(),
          'original_allocation':read(original/'allocation_status.json'),'original_endpoint':read(original/'endpoint.json'),
          'controller':read(root/'controller_state.json'),'selection':read(root/'selection.json'),
          'service_control':read(root/'service_control.json'),'launch_intent':read(root/'launch_intent.json'),
          'launch_receipt':read(root/'launch_receipt.json')}
endpoint=snapshot['original_endpoint']
control=snapshot['service_control'] or {}
if control.get('endpoint_file'):
    path=checked(pathlib.Path(control['endpoint_file']))
    if root not in path.parents and path!=original/'endpoint.json': raise RuntimeError('Unexpected endpoint path')
    endpoint=read(path)
snapshot['active_endpoint']=endpoint
if snapshot['job'].get('JobState')=='RUNNING' and endpoint and endpoint.get('status')=='ready':
    health={'at':now()}
    try:
        node=snapshot['job']['NodeList']
        if endpoint.get('base_url')!='http://'+node+':18044/v1': raise RuntimeError('Unexpected endpoint node')
        key=(original/'api_key').read_text().strip()
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        for suffix in ['health','metrics']:
            req=urllib.request.Request('http://'+node+':18044/'+suffix,headers={'Authorization':'Bearer '+key})
            with opener.open(req,timeout=10) as response:
                raw=response.read().decode()
                health[suffix+'_status']=response.status
            if suffix=='metrics':
                allowed={'vllm:num_requests_running','vllm:num_requests_waiting','vllm:kv_cache_usage_perc',
                         'vllm:gpu_cache_usage_perc','vllm:num_preemptions_total','vllm:generation_tokens_total'}
                values={}
                for line in raw.splitlines():
                    if line.startswith('#') or not line: continue
                    name=line.split('{',1)[0].split(' ',1)[0]
                    if name in allowed: values[name]=values.get(name,0)+float(line.rsplit(' ',1)[1])
                health['metrics']=values
    except Exception as error: health['error_type']=type(error).__name__
    snapshot['service_health']=health
if mode=='start-if-ready':
    verify()
    fd=os.open(checked(root/'dispatch.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
    if (root/'launch_intent.json').exists():
        snapshot['action']='existing_launch_intent_no_duplicate'
    elif snapshot['job'].get('JobState')!='RUNNING':
        snapshot['action']='waiting_for_allocation'
    elif not snapshot['original_endpoint'] or snapshot['original_endpoint'].get('status')!='ready':
        snapshot['action']='waiting_for_service'
    elif snapshot.get('service_health',{}).get('health_status')!=200:
        snapshot['action']='waiting_for_healthy_service'
    else:
        state=snapshot['original_allocation'] or {}
        endpoint=snapshot['original_endpoint']
        if str(state.get('job_id'))!=job or str(endpoint.get('job_id'))!=job or state.get('cleanup_unknown') is not False or state.get('cleanup_survivors')!=[] or state.get('allocation_state')!='service_running' or endpoint.get('max_num_seqs')!=128: raise RuntimeError('Service ownership/state unavailable')
        command=[bin+'srun','--overlap','--exact','--jobid='+job,'--nodes=1','--ntasks=1','--cpus-per-task=64','--gres=gpu:8',
                 '/aifs4su/hansirui/vllm-dsv4-venv/bin/python3','-B',str(root/'code/controller.py')]
        save_new(root/'launch_intent.json',{'at':now(),'job_id':job,'command':command,'scope':'one existing allocation step, no sbatch'})
        with (root/'controller.log').open('x') as log:
            process=subprocess.Popen(command,env=env,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,close_fds=True,cwd=root)
        raw=pathlib.Path('/proc',str(process.pid),'stat').read_text()
        identity={'pid':process.pid,'started':raw[raw.rfind(')')+2:].split()[19]}
        receipt={'at':now(),'job_id':job,'srun_identity':identity,'controller_started_confirmed':False}
        save_new(root/'launch_receipt.json',receipt)
        snapshot['action']='step_dispatched'
        snapshot['launch_receipt']=receipt
    os.close(fd)
print(json.dumps(snapshot))
'''


def staged_files():
    mapping = {f'code/{name}': BASE / name for name in
               ['controller.py', 'variant_launcher.py', 'policy.py', 'bench.py']}
    mapping.update({
        'code/contract.py': REPO / 'scripts/hallucination_h1_h5_20261002/contract.py',
        'code/allocation_supervisor_frozen.py': REPO / 'scripts/cluster/v4_flash_20261002/allocation_supervisor.py',
        'prepared/requests.jsonl': LOCAL / 'prepared/requests.jsonl',
        'prepared/manifest.json': LOCAL / 'prepared/manifest.json',
    })
    return {name: base64.b64encode(path.read_bytes()).decode() for name, path in mapping.items()}


def run(mode):
    request = {'mode': mode}
    if mode == 'stage':
        request['files'] = staged_files()
        request['expected_files'] = sorted(request['files'])
    result = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15',
        'Group3_slogin', 'python3 -B -c ' + shlex.quote(REMOTE)],
        input=json.dumps(request), text=True, capture_output=True, timeout=180)
    record = {'at': dt.datetime.now(dt.timezone.utc).isoformat(), 'mode': mode,
              'returncode': result.returncode}
    if result.returncode:
        # No credentials in any script/command, but keep remote diagnostics local.
        record['error'] = result.stderr[-3000:]
    else:
        record['result'] = json.loads(result.stdout)
    records = LOCAL / 'checks'
    records.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    with (records / f'{stamp}_{mode}.json').open('x') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
    print(json.dumps(record, ensure_ascii=False, indent=2))
    return result.returncode


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['stage', 'status', 'start-if-ready'])
    raise SystemExit(run(parser.parse_args().mode))
