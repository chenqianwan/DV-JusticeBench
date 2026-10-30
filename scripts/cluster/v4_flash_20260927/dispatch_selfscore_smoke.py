#!/usr/bin/env python3
"""Stage one scoring-only dependent client inside existing allocation 402816."""
import argparse
import base64
import datetime
import json
from pathlib import Path
import shlex
import subprocess

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parents[2] / 'data/pkulaw_v4_selfscore_smoke_20260927'
REMOTE = r'''
import base64,datetime,fcntl,hashlib,json,os,pathlib,subprocess,sys
r=json.load(sys.stdin)
root=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_selfscore_smoke_20260927')
service=root.parent/'dvjustice_v4flash_20260927'
assert os.getuid()==1083 and root.resolve()==root and root.parent.stat().st_uid==1083
os.umask(0o077)
env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1',
     'SLURM_CONF':'/cm/shared/apps/slurm/var/etc/slurm/slurm.conf'}
B='/cm/shared/apps/slurm/current/bin/'
def now():return datetime.datetime.now().astimezone().isoformat()
def digest(b):return hashlib.sha256(b).hexdigest()
def safe(p):
    if p.resolve()!=p or root not in (p,*p.parents):raise RuntimeError('Unsafe path')
    if p.exists() and p.stat().st_uid!=1083:raise RuntimeError('Unexpected owner')
    return p
def save(p,v):
    safe(p);temporary=safe(p.with_suffix('.tmp'))
    with temporary.open('w') as f:
        json.dump(v,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(temporary,p)
def read(name):return json.loads(safe(root/name).read_text())
if r['mode']=='stage':
    assert set(r['files'])=={'run_selfscore_smoke.py','run_answer_smoke.py','legacy_scoring_bundle.py','input_scoring.json','input_manifest.json'}
    root.mkdir(mode=0o700,exist_ok=True)
    expected={}
    for name,data in r['files'].items():
        payload=base64.b64decode(data,validate=True);expected[name]=digest(payload)
        p=safe(root/name)
        if p.exists():
            if p.read_bytes()!=payload:raise RuntimeError('Refusing to overwrite frozen '+name)
        else:
            with p.open('xb') as f:f.write(payload)
    inputs=read('input_scoring.json');manifest=read('input_manifest.json')
    assert inputs['service_job_id']=='402816' and len(inputs['tasks'])==10
    assert inputs['thinking'] is True and inputs['timeout_s']==900 and inputs['workers']==2
    assert inputs['base_url']=='http://dgx-040:18044/v1' and inputs['model']=='deepseek_v4_flash'
    assert manifest['input_scoring_sha256']==expected['input_scoring.json']
    assert manifest['bundle_sha256']==expected['legacy_scoring_bundle.py']
    save(root/'stage_manifest.json',{'at':now(),'files':expected,'scope':'10 self-scores after answer completion'})
    print(json.dumps({'status':'staged','root':str(root),'files':expected}))
elif r['mode']=='start':
    fd=os.open(safe(root/'dispatch.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if (root/'dispatch.json').exists():
            print(json.dumps({'existing_dispatch':True,'receipt':read('dispatch.json'),'action':'No duplicate client started'}))
        else:
            for name,value in read('stage_manifest.json')['files'].items():
                assert digest(safe(root/name).read_bytes())==value
            job=subprocess.run([B+'scontrol','-a','show','job','402816','-o'],env=env,capture_output=True,text=True,check=True,timeout=15).stdout
            assert 'JobState=RUNNING' in job and 'NodeList=dgx-040 ' in job
            endpoint=json.loads((service/'endpoint.json').read_text())
            assert endpoint['job_id']=='402816' and endpoint['status']=='ready'
            assert endpoint['base_url']=='http://dgx-040:18044/v1'
            command=[B+'srun','--jobid=402816','--overlap','--exact','--nodes=1','--ntasks=1',
                '--cpus-per-task=1','--gres=none','--unbuffered','--export=ALL',
                '/usr/bin/python3','-B',str(root/'run_selfscore_smoke.py'),
                '--input',str(root/'input_scoring.json'),'--output-dir',str(root/'results')]
            record={'status':'starting','at':now(),'allocation_job_id':'402816','new_gpu_allocation':False,
                    'command':command,'input_sha256':read('stage_manifest.json')['files']['input_scoring.json'],
                    'dependency':'Wait for all ten answers to reach terminal state before any scoring request.'}
            save(root/'dispatch.json',record)
            try:
                with safe(root/'client.log').open('xb') as log:
                    p=subprocess.Popen(command,env=env,cwd=str(root),stdin=subprocess.DEVNULL,
                        stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                record.update(status='dispatched',client_launcher_pid=p.pid)
                save(root/'dispatch.json',record)
            except Exception as exc:
                record.update(status='needs_reconciliation',error_type=type(exc).__name__)
                save(root/'dispatch.json',record);raise
            print(json.dumps(record))
elif r['mode']=='status':
    result={'at':now()}
    for name in ['dispatch.json','results/summary.json','results/run_state.json']:
        if (root/name).is_file():result[name]=read(name)
    log=safe(root/'client.log')
    if log.exists():
        with log.open('rb') as f:
            f.seek(max(0,log.stat().st_size-4000));result['client_log_tail']=f.read().decode(errors='replace')
    result['result_files']=[str(p.relative_to(root)) for p in sorted((root/'results').rglob('*')) if p.is_file()]
    print(json.dumps(result))
elif r['mode']=='fetch':
    files={}
    for p in sorted((root/'results').rglob('*')):
        if p.is_file() and p.suffix in {'.json','.jsonl'}:
            safe(p);files[str(p.relative_to(root))]=base64.b64encode(p.read_bytes()).decode()
    if not files:raise RuntimeError('No results to fetch')
    print(json.dumps({'files':files}))
else:raise RuntimeError('Unknown mode')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['stage', 'start', 'status', 'fetch'])
    args = parser.parse_args()
    payload = {'mode': args.mode}
    if args.mode == 'stage':
        files = {name: HERE/name for name in ('run_selfscore_smoke.py', 'run_answer_smoke.py')}
        files.update({name: LOCAL/name for name in ('legacy_scoring_bundle.py', 'input_scoring.json', 'input_manifest.json')})
        payload['files'] = {name:base64.b64encode(p.read_bytes()).decode() for name,p in files.items()}
    p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=12',
                        '-o', 'StrictHostKeyChecking=yes', 'Group3_slogin',
                        'python3 -B -c '+shlex.quote(REMOTE)], input=json.dumps(payload),
                       text=True, capture_output=True, timeout=90)
    record = {'at':datetime.datetime.now().astimezone().isoformat(), 'mode':args.mode,
              'exit_code':p.returncode, 'stderr':p.stderr}
    try:
        result = json.loads(p.stdout)
    except json.JSONDecodeError:
        result = {'stdout':p.stdout}
    if args.mode == 'fetch' and p.returncode == 0:
        fetched = []
        for name, encoded in result['files'].items():
            rel = Path(name)
            if rel.is_absolute() or '..' in rel.parts or rel.parts[0] != 'results':
                raise RuntimeError('Unsafe result path')
            path = LOCAL/rel
            if path.resolve() != path:
                raise RuntimeError('Redirected local result path')
            data = base64.b64decode(encoded, validate=True)
            path.parent.mkdir(parents=True, exist_ok=True)
            if path.exists():
                if path.read_bytes() != data:
                    raise RuntimeError('Existing result differs; preserve previous snapshot')
            else:
                with path.open('xb') as f:
                    f.write(data)
            fetched.append(name)
        result = {'fetched_files':fetched, 'count':len(fetched)}
    record['result'] = result
    directory = LOCAL/'dispatch_records'
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')
    with (directory/(args.mode+'_'+stamp+'.json')).open('x') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps(record, ensure_ascii=False, indent=2))
    raise SystemExit(p.returncode)


if __name__ == '__main__':
    main()
