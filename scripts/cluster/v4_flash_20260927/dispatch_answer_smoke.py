#!/usr/bin/env python3
"""Stage and start exactly one answer-only pilot inside existing job 402816."""
import argparse
import base64
import datetime
import json
from pathlib import Path
import shlex
import subprocess

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[2]
LOCAL = REPO / 'data/pkulaw_v4_answer_smoke_20260927'
REMOTE = r'''
import base64,datetime,fcntl,hashlib,json,os,pathlib,subprocess,sys
r=json.load(sys.stdin)
assert type(r.get('wait900',False)) is bool
wait900=r.get('wait900',False)
root=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_answering_smoke_20260927'+('_wait900' if wait900 else ''))
service=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20260927')
assert os.getuid()==1083 and root.resolve()==root and root.parent.stat().st_uid==1083
os.umask(0o077)
env={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1',
     'SLURM_CONF':'/cm/shared/apps/slurm/var/etc/slurm/slurm.conf'}
B='/cm/shared/apps/slurm/current/bin/'
def now(): return datetime.datetime.now().astimezone().isoformat()
def digest(b): return hashlib.sha256(b).hexdigest()
def safe(path):
    if path.resolve()!=path or root not in (path,*path.parents): raise RuntimeError('Unsafe run path')
    if path.exists() and path.stat().st_uid!=1083: raise RuntimeError('Unexpected owner')
    return path
def save(path,value):
    safe(path)
    temp=safe(path.with_suffix('.tmp'))
    with temp.open('w') as f:
        json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
    os.replace(temp,path)
def read(name): return json.loads(safe(root/name).read_text())
if r['mode']=='stage':
    expected_names={'run_answer_smoke.py','input_tasks.json','input_manifest.json'}
    if wait900:expected_names.add('run_answer_smoke_wait900.py')
    assert set(r['files'])==expected_names
    root.mkdir(mode=0o700,exist_ok=True)
    expected={}
    for name,data in r['files'].items():
        payload=base64.b64decode(data,validate=True);expected[name]=digest(payload)
        p=safe(root/name)
        if p.exists():
            if p.read_bytes()!=payload: raise RuntimeError('Refusing to overwrite frozen '+name)
        else:
            with p.open('xb') as f:f.write(payload)
    inputs=read('input_tasks.json')
    assert inputs['service_job_id']=='402816' and len(inputs['tasks'])==10
    assert inputs['base_url']=='http://dgx-040:18044/v1'
    assert inputs['timeout_s']==(900 if wait900 else 180)
    assert read('input_manifest.json')['input_tasks_sha256']==expected['input_tasks.json']
    save(root/'stage_manifest.json',{'at':now(),'files':expected,'experiment_scope':'answer-only, 10 tasks'})
    print(json.dumps({'status':'staged','root':str(root),'files':expected}))
elif r['mode']=='start':
    fd=os.open(safe(root/'dispatch.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        receipt=safe(root/'dispatch.json')
        if receipt.exists():
            record=read('dispatch.json')
            print(json.dumps({'existing_dispatch':True,'receipt':record,'action':'No duplicate client started'}))
        else:
            if wait900:
                prior=root.parent/'dvjustice_answering_smoke_20260927'/'results'
                with (prior/'.lock').open('r+') as old_lock:
                    fcntl.flock(old_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                    previous=json.loads((prior/'summary.json').read_text())
                    assert previous['success']==0 and previous['counts']=={'pending':10}
                    assert previous['uncertain_request_intents']==0
            for name,value in read('stage_manifest.json')['files'].items():
                assert digest(safe(root/name).read_bytes())==value
            job=subprocess.run([B+'scontrol','-a','show','job','402816','-o'],env=env,capture_output=True,text=True,check=True,timeout=15).stdout
            assert 'JobState=RUNNING' in job and 'NodeList=dgx-040 ' in job
            endpoint=json.loads((service/'endpoint.json').read_text())
            assert endpoint['job_id']=='402816' and endpoint['status']=='ready'
            assert endpoint['base_url']=='http://dgx-040:18044/v1'
            command=[B+'srun','--jobid=402816','--overlap','--exact','--nodes=1','--ntasks=1',
                '--cpus-per-task=1','--gres=none','--unbuffered','--export=ALL',
                '/usr/bin/python3','-B',str(root/('run_answer_smoke_wait900.py' if wait900 else 'run_answer_smoke.py')),
                '--input',str(root/'input_tasks.json'),'--output-dir',str(root/'results')]
            record={'status':'starting','at':now(),'allocation_job_id':'402816','new_gpu_allocation':False,
                    'command':command,'input_sha256':read('stage_manifest.json')['files']['input_tasks.json']}
            save(receipt,record)
            try:
                with safe(root/'client.log').open('xb') as log:
                    p=subprocess.Popen(command,env=env,cwd=str(root),stdin=subprocess.DEVNULL,
                        stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                record.update(status='dispatched',client_launcher_pid=p.pid)
                save(receipt,record)
            except Exception as exc:
                record.update(status='needs_reconciliation',error_type=type(exc).__name__)
                save(receipt,record);raise
            print(json.dumps(record))
elif r['mode']=='status':
    result={'at':now()}
    for name in ['dispatch.json','results/summary.json','results/run_state.json']:
        if (root/name).is_file():result[name]=read(name)
    path=safe(root/'client.log')
    if path.exists():
        with path.open('rb') as f:
            f.seek(max(0,path.stat().st_size-6000));result['client_log_tail']=f.read().decode(errors='replace')
    result['result_files']=[str(p.relative_to(root)) for p in sorted((root/'results').rglob('*')) if p.is_file()]
    print(json.dumps(result))
elif r['mode']=='fetch':
    files={}
    for p in sorted((root/'results').rglob('*')):
        if p.is_file() and p.suffix in {'.json','.jsonl'}:
            safe(p);files[str(p.relative_to(root))]=base64.b64encode(p.read_bytes()).decode()
    if not files: raise RuntimeError('No results to fetch')
    print(json.dumps({'files':files}))
else:raise RuntimeError('Unknown mode')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['stage', 'start', 'status', 'fetch'])
    parser.add_argument('--wait900', action='store_true', help='Separate, audited 900-second recovery run')
    args = parser.parse_args()
    local = LOCAL.with_name(LOCAL.name+'_wait900') if args.wait900 else LOCAL
    payload = {'mode': args.mode, 'wait900': args.wait900}
    if args.mode == 'stage':
        files = {'run_answer_smoke.py': HERE/'run_answer_smoke.py',
                 'input_tasks.json': local/'input_tasks.json',
                 'input_manifest.json': local/'input_manifest.json'}
        if args.wait900:
            files['run_answer_smoke_wait900.py'] = HERE/'run_answer_smoke_wait900.py'
        payload['files'] = {name: base64.b64encode(path.read_bytes()).decode() for name,path in files.items()}
    p = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=12',
                        '-o','StrictHostKeyChecking=yes','Group3_slogin',
                        'python3 -B -c '+shlex.quote(REMOTE)],input=json.dumps(payload),
                       text=True,capture_output=True,timeout=90)
    record = {'at': datetime.datetime.now().astimezone().isoformat(), 'mode': args.mode,
              'exit_code': p.returncode, 'stderr': p.stderr}
    try:
        result = json.loads(p.stdout)
    except json.JSONDecodeError:
        result = {'stdout': p.stdout}
    if args.mode == 'fetch' and p.returncode == 0:
        fetched = []
        for name,encoded in result['files'].items():
            rel = Path(name)
            if rel.is_absolute() or '..' in rel.parts or rel.parts[0] != 'results':
                raise RuntimeError('Unsafe result path')
            path = local/rel
            if path.resolve()!=path: raise RuntimeError('Redirected local result path')
            data = base64.b64decode(encoded,validate=True)
            path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists():
                if path.read_bytes()!=data: raise RuntimeError('Existing result differs; preserve prior snapshot')
            else:
                with path.open('xb') as stream:stream.write(data)
            fetched.append(name)
        result={'fetched_files':fetched,'count':len(fetched)}
    record['result']=result
    records=local/'dispatch_records';records.mkdir(parents=True,exist_ok=True)
    stamp=datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')
    with (records/(args.mode+'_'+stamp+'.json')).open('x') as f:
        json.dump(record,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(record,ensure_ascii=False,indent=2))
    raise SystemExit(p.returncode)


if __name__ == '__main__':
    main()
