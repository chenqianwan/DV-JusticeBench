#!/usr/bin/env python3
"""Audited max-num-seqs 16 -> 128 change inside existing Slurm job 402816.

No submission, allocation cancellation, shared-runtime writes, or automatic KILL.
The only callable operations are the five fixed modes listed by --help.
"""
import argparse
import ast
import base64
import datetime
import hashlib
import json
from pathlib import Path
import shlex
import subprocess

HERE = Path(__file__).resolve().parent
CHANGE = 'benchmark128_20260927'


def transform_files(launcher, validator):
    replacements = {
        "'--max-num-seqs', '16'": "'--max-num-seqs', '128'",
        "'max_num_seqs': 16": "'max_num_seqs': 128",
    }
    original = launcher.decode()
    for before, after in replacements.items():
        if original.count(before) != 1:
            raise ValueError('Launcher is not the reviewed max-num-seqs=16 source')
        original = original.replace(before, after, 1)
    check = validator.decode()
    anchor = '    assert args.max_model_len == 32768\n'
    if check.count(anchor) != 1 or 'assert args.max_num_seqs' in check:
        raise ValueError('Validator is not the reviewed source')
    check = check.replace(anchor, anchor + '    assert args.max_num_seqs == 128\n', 1)
    files = {'launch_service.py': original.encode(), 'validate_cli.py': check.encode()}
    for content in files.values():
        ast.parse(content.decode())
    return files


REMOTE = r"""
import ast,base64,datetime,fcntl,hashlib,json,os,pathlib,re,signal,socket,stat,subprocess,sys,uuid
r=json.load(sys.stdin)
mode=r['mode']
if mode not in {'stage','cli-check','stop','restart','status'}: raise RuntimeError('Unsupported mode')
ROOT=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20260927')
A=ROOT/'changes'/'benchmark128_20260927'
B='/cm/shared/apps/slurm/current/bin/'
E={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1',
   'SLURM_CONF':'/cm/shared/apps/slurm/var/etc/slurm/slurm.conf'}
assert os.getuid()==1083 and ROOT.resolve()==ROOT and ROOT.stat().st_uid==1083
os.umask(0o077)
def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(b): return hashlib.sha256(b).hexdigest()
def safe(p):
    p=pathlib.Path(p)
    if p.resolve()!=p or ROOT not in (p,*p.parents): raise RuntimeError('Unsafe path')
    for q in (p,*p.parents):
        if q==ROOT or ROOT in q.parents:
            if q.exists() and q.stat().st_uid!=1083: raise RuntimeError('Unexpected path owner')
    return p
def read(p):
    p=safe(p)
    if not stat.S_ISREG(p.lstat().st_mode): raise RuntimeError('Not regular file')
    return json.loads(p.read_text())
def exclusive(p,b):
    p=safe(p)
    if p.exists():
        if p.read_bytes()!=b: raise RuntimeError('Existing immutable audit differs: '+p.name)
        return
    fd=os.open(p,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f: f.write(b);f.flush();os.fsync(f.fileno())
def encoded(x): return (json.dumps(x,indent=2,sort_keys=True)+'\n').encode()
def atomic(p,b):
    p=safe(p);tmp=safe(p.parent/('.'+p.name+'.'+uuid.uuid4().hex))
    exclusive(tmp,b);os.replace(tmp,p)
    fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
def publish_request(p,b):
    p=safe(p);tmp=safe(p.parent/('.restart_request.'+uuid.uuid4().hex))
    exclusive(tmp,b)
    try:os.link(tmp,p)  # Publish complete bytes without replacing an existing request.
    finally:os.unlink(tmp)
    fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
def receipt(kind,x): exclusive(A/(kind+'_'+datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')+'.json'),encoded(x))
def job():
    out=subprocess.run([B+'scontrol','-a','show','job','402816','-o'],env=E,capture_output=True,text=True,check=True,timeout=20).stdout
    fields={k:m.group(1) for k in ['JobId','JobState','NodeList','TimeLimit','StartTime','EndTime','NumNodes','AllocTRES']
            if (m:=re.search(r'(?:^|\s)'+k+r'=(\S+)',out))}
    if fields.get('JobState')!='RUNNING' or fields.get('NodeList')!='dgx-040' or fields.get('TimeLimit')!='5-00:00:00':
        raise RuntimeError('Original five-day dgx-040 allocation not RUNNING')
    return fields
def current_files():
    manifest=read(ROOT/'preflight.json')
    for name,digest in manifest['deployment_files'].items():
        if pathlib.PurePosixPath(name).name!=name or sha(safe(ROOT/name).read_bytes())!=digest:
            raise RuntimeError('Deployment hash mismatch: '+name)
    return manifest
def verified_change():
    plan=read(A/'plan.json');manifest=current_files()
    if sha(safe(ROOT/'preflight.json').read_bytes())!=plan['new_preflight_sha256']:
        raise RuntimeError('Preflight does not match reviewed change')
    for name,digest in plan['new_files'].items():
        if manifest['deployment_files'][name]!=digest: raise RuntimeError('Wrong change hash')
    return plan
NODE = r'''
import datetime,fcntl,hashlib,json,os,pathlib,re,signal,socket,stat,subprocess,sys,urllib.request
p=json.load(sys.stdin)
R=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20260927')
A=R/'changes'/'benchmark128_20260927'
assert os.getuid()==1083 and os.environ.get('SLURM_JOB_ID')=='402816' and socket.gethostname().split('.')[0]=='dgx-040'
def read(path):
    assert path.resolve()==path and path.stat().st_uid==1083 and stat.S_ISREG(path.lstat().st_mode)
    return json.loads(path.read_text())
def ident(pid):
    q=pathlib.Path('/proc')/str(pid)/'stat'
    assert q.stat().st_uid==1083
    raw=q.read_text();f=raw[raw.rfind(')')+2:].split()
    assert f[0]!='Z'
    return {'pid':pid,'started':f[19]}
s=read(R/'allocation_status.json')
assert s['job_id']=='402816'
supervisor=ident(s['supervisor_pid'])
if p.get('supervisor_identity') and supervisor!=p['supervisor_identity']:raise RuntimeError('Supervisor identity changed')
manifest=read(R/'preflight.json')
for name,h in manifest['deployment_files'].items():
    path=R/name;assert path.resolve()==path and path.stat().st_uid==1083
    assert hashlib.sha256(path.read_bytes()).hexdigest()==h
if p['mode']=='cli-check':
    runtime=pathlib.Path('/aifs4su/hansirui/vllm-dsv4-venv')
    work=A/'cli_preflight';work.mkdir(mode=0o700,exist_ok=True)
    env={'PATH':str(runtime/'bin')+':/cm/shared/apps/cuda-12.8/bin:/usr/bin:/bin','LANG':'C.UTF-8'}
    for key,name in {'TMPDIR':'tmp','XDG_CACHE_HOME':'cache','HF_HOME':'cache/hf','HF_MODULES_CACHE':'cache/hf/modules',
            'TORCH_HOME':'cache/torch','TRITON_CACHE_DIR':'cache/triton','VLLM_CACHE_ROOT':'cache/vllm',
            'VLLM_CONFIG_ROOT':'config/vllm','CUDA_CACHE_PATH':'cache/cuda','TORCH_EXTENSIONS_DIR':'cache/torch_extensions',
            'FLASHINFER_WORKSPACE_BASE':'cache/flashinfer','TVM_FFI_CACHE_DIR':'cache/tvm'}.items():
        q=work/name;assert q.resolve()==q;q.mkdir(parents=True,exist_ok=True,mode=0o700);env[key]=str(q)
    env.update({'CUDA_HOME':'/cm/shared/apps/cuda-12.8','CUDA_VISIBLE_DEVICES':'','PYTHONNOUSERSITE':'1',
        'PYTHONDONTWRITEBYTECODE':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1','HF_HUB_DISABLE_TELEMETRY':'1',
        'VLLM_NO_USAGE_STATS':'1','VLLM_DO_NOT_TRACK':'1','FLASHINFER_NO_DOWNLOAD':'1','OMP_NUM_THREADS':'1'})
    out=subprocess.run([str(runtime/'bin/python3'),'-B',str(R/'validate_cli.py')],env=env,cwd=R,
        capture_output=True,text=True,timeout=150)
    result={'exit_code':out.returncode,'stdout':out.stdout[-20000:],'stderr':out.stderr[-12000:],
        'supervisor_identity':supervisor,'gpu_inference_started':False,'deployment_files':manifest['deployment_files']}
elif p['mode']=='check':
    assert s['waiting_for_repair'] and s['service_pid'] is None and not s['cleanup_survivors']
    with socket.socket() as probe:probe.bind(('0.0.0.0',18044))
    result={'supervisor_identity':supervisor,'state':s}
elif p['mode']=='stop':
    assert s['allocation_state']=='service_running' and not s['waiting_for_repair']
    launcher=ident(s['service_pid']);old=s['service_identity']
    assert launcher=={'pid':old['pid'],'started':old['started']} and launcher['pid']!=supervisor['pid']
    smoke=R.parent/'dvjustice_answering_smoke_20260927_wait900'/'results'
    q=smoke/'.lock';assert q.resolve()==q and q.stat().st_uid==1083
    with q.open('r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        summary=read(smoke/'summary.json')
        assert summary['tasks']==10 and summary['success']==10 and summary['failed']==0 and summary['counts']=={'ok':10}
        keypath=R/'api_key';assert keypath.resolve()==keypath and keypath.stat().st_uid==1083 and not keypath.stat().st_mode&0o077
        key=keypath.read_text().strip()
        req=urllib.request.Request('http://127.0.0.1:18044/metrics',headers={'Authorization':'Bearer '+key})
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(req,timeout=10) as response:metrics=response.read().decode()
        values={}
        for suffix in ('running','waiting'):
            matches=re.findall(r'^vllm:num_requests_'+suffix+r'(?:\{[^\n]*\})?\s+([0-9.eE+-]+)\s*$',metrics,re.M)
            assert matches,'Missing '+suffix+' metric'
            values[suffix]=sum(float(v) for v in matches)
            assert values[suffix]==0,'Service still has '+suffix+' requests'
        assert ident(launcher['pid'])==launcher and ident(supervisor['pid'])==supervisor
        os.kill(launcher['pid'],signal.SIGTERM)
    result={'signal':'SIGTERM','target':'launcher_only','launcher_identity':launcher,
        'supervisor_identity':supervisor,'idle_metrics':values,'smoke_success':10,'automatic_kill':False}
else:raise RuntimeError('Invalid internal action')
print(json.dumps(result))
'''
def node(action,supervisor=None):
    payload={'mode':action}
    if supervisor:payload['supervisor_identity']=supervisor
    # vLLM imports query NVML even with CUDA_VISIBLE_DEVICES=''. Reuse this
    # allocation's GPU device visibility for CLI detection, never load an engine.
    gres='--gres=gpu:8' if action=='cli-check' else '--gres=none'
    command=[B+'srun','--jobid=402816','--overlap','--exact','--nodes=1','--ntasks=1','--cpus-per-task=1',
             gres,'--unbuffered','--export=ALL','/usr/bin/python3','-B','-c',NODE]
    out=subprocess.run(command,input=json.dumps(payload),env=E,capture_output=True,text=True,timeout=175 if action=='cli-check' else 45)
    if out.returncode:raise RuntimeError('CPU-step failed: '+out.stderr[-3000:])
    return json.loads(out.stdout)
if mode=='status':
    result={'at':now(),'job':job(),'allocation':read(ROOT/'allocation_status.json'),'endpoint':read(ROOT/'endpoint.json')}
    for name in ['plan.json','cli_result.json']:
        if (A/name).exists():
            data=read(A/name)
            result[name]={k:v for k,v in data.items() if k not in {'stdout','stderr'}}
    attempt=result['endpoint']['attempt']
    assert str(attempt).isdigit()
    log=safe(ROOT/'runs'/'402816'/('attempt-'+str(attempt))/'server.log')
    if log.exists():
        with log.open('rb') as f:f.seek(max(0,log.stat().st_size-8000));tail=f.read().decode(errors='replace')
        key=safe(ROOT/'api_key').read_text().strip();result['server_log_tail']=tail.replace(key,'[REDACTED]')
    print(json.dumps(result));sys.exit(0)
fd=os.open(safe(ROOT/'configuration.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
with os.fdopen(fd,'r+') as lock:
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    live=job()
    if mode=='stage':
        assert set(r)=={'mode','files','old_files'} and set(r['files'])==set(r['old_files'])=={'launch_service.py','validate_cli.py'}
        files={n:base64.b64decode(b,validate=True) for n,b in r['files'].items()}
        for b in files.values():ast.parse(b.decode())
        safe(A.parent).mkdir(mode=0o700,exist_ok=True);safe(A).mkdir(mode=0o700,exist_ok=True)
        if not (A/'plan.json').exists():
            original=current_files()
            for n,h in r['old_files'].items():
                assert sha(safe(ROOT/n).read_bytes())==h,'Source differs from reviewed local source'
            for n in ['launch_service.py','validate_cli.py','preflight.json','cli_validation.json']:
                exclusive(A/('original_'+n),safe(ROOT/n).read_bytes())
            updated=json.loads(json.dumps(original))
            for n,b in files.items():updated['deployment_files'][n]=sha(b)
            updated['configuration_revision']='benchmark128_20260927'
            preflight=encoded(updated)
            exclusive(A/'new_preflight.json',preflight)
            for n,b in files.items():exclusive(A/('new_'+n),b)
            plan={'change':'benchmark128_20260927','job_id':'402816','at':now(),'original_job':live,
                  'old_files':r['old_files'],'new_files':{n:sha(b) for n,b in files.items()},
                  'old_preflight_sha256':sha((A/'original_preflight.json').read_bytes()),
                  'new_preflight_sha256':sha(preflight),'only_parameter_change':'max_num_seqs 16 -> 128'}
            exclusive(A/'plan.json',encoded(plan))
        plan=read(A/'plan.json')
        assert plan['old_files']==r['old_files'] and plan['new_files']=={n:sha(b) for n,b in files.items()}
        for n,b in files.items():
            assert sha(safe(ROOT/n).read_bytes()) in {plan['old_files'][n],plan['new_files'][n]}
        assert sha(safe(ROOT/'preflight.json').read_bytes()) in {plan['old_preflight_sha256'],plan['new_preflight_sha256']}
        for n,b in files.items():atomic(ROOT/n,b)
        atomic(ROOT/'preflight.json',safe(A/'new_preflight.json').read_bytes())
        verified_change();result={'status':'staged','plan':plan,'allocation_unchanged':True};receipt('stage',result)
    elif mode=='cli-check':
        plan=verified_change()
        result=node('cli-check');result['at']=now()
        receipt('cli_check',result)
        if result['exit_code']!=0:raise RuntimeError('Full CLI validation failed; service left running')
        verified_change()
        atomic(A/'cli_result.json',encoded(result))
    else:
        plan=verified_change();validation=read(A/'cli_result.json')
        assert validation['exit_code']==0 and validation['deployment_files']==current_files()['deployment_files']
        assert validation['gpu_inference_started'] is False
        if mode=='stop':
            state=read(ROOT/'allocation_status.json')
            if state['waiting_for_repair'] and not state['service_pid']:
                result={'status':'already_waiting_for_repair','state':state,'signal_sent':False}
            else:
                endpoint=read(ROOT/'endpoint.json')
                assert endpoint['attempt']=='001','Do not stop a replacement service'
                receipt('stop_intent',{'at':now(),'service_identity':state['service_identity'],'job_id':'402816'})
                result=node('stop',validation['supervisor_identity']);receipt('stop_result',result)
        else:
            snapshot=node('check',validation['supervisor_identity']);state=snapshot['state']
            assert state['waiting_for_repair'] and state['allocation_state']=='waiting_for_repair'
            assert state['service_pid'] is None and not state['cleanup_survivors'] and state['attempt']=='001'
            assert 'bench128-20260927' not in state['handled_request_ids']
            req=safe(ROOT/'restart_request.json')
            if req.exists():raise RuntimeError('Pending restart request exists; do not overwrite')
            receipt('restart_intent',{'at':now(),'job_id':'402816','request_id':'bench128-20260927'})
            publish_request(req,encoded({'job_id':'402816','request_id':'bench128-20260927'}))
            result={'status':'restart_requested','request_id':'bench128-20260927','allocation_unchanged':True}
            receipt('restart_result',result)
    print(json.dumps(result))
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['stage', 'cli-check', 'stop', 'restart', 'status'])
    args = parser.parse_args()
    payload = {'mode': args.mode}
    if args.mode == 'stage':
        originals = {name: (HERE/name).read_bytes() for name in ['launch_service.py', 'validate_cli.py']}
        files = transform_files(originals['launch_service.py'], originals['validate_cli.py'])
        payload.update(files={name: base64.b64encode(b).decode() for name,b in files.items()},
                       old_files={name: hashlib.sha256(b).hexdigest() for name,b in originals.items()})
    command = ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=12','-o','StrictHostKeyChecking=yes',
               'Group3_slogin','python3 -B -c '+shlex.quote(REMOTE)]
    completed = subprocess.run(command,input=json.dumps(payload),capture_output=True,text=True,
                               timeout=210 if args.mode=='cli-check' else 85)
    record = {'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'mode':args.mode,
              'exit_code':completed.returncode,'stderr':completed.stderr}
    try:record['result']=json.loads(completed.stdout)
    except json.JSONDecodeError:record['stdout']=completed.stdout
    directory=HERE/'records';directory.mkdir(exist_ok=True)
    stamp=datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')
    with (directory/('benchmark128_'+args.mode+'_'+stamp+'.json')).open('x') as stream:
        json.dump(record,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(record,ensure_ascii=False,indent=2))
    raise SystemExit(completed.returncode)


if __name__=='__main__':main()
