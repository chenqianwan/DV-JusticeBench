#!/usr/bin/env python3
"""Dispatch isolated benchmark rounds; never submit a new allocation."""
import argparse
import base64
import datetime
import json
from pathlib import Path
import shlex
import subprocess

HERE = Path(__file__).resolve().parent
LOCAL = HERE.parents[2] / 'data/pkulaw_concurrency_benchmark_20260927'
REMOTE = r'''
import base64,datetime,fcntl,hashlib,json,os,pathlib,subprocess,sys
r=json.load(sys.stdin)
root=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_concurrency_benchmark_20260927')
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
    assert set(r['files'])=={'benchmark_concurrency.py','run_answer_smoke.py','input_benchmark.json','input_manifest.json'}
    root.mkdir(mode=0o700,exist_ok=True)
    expected={}
    for name,data in r['files'].items():
        payload=base64.b64decode(data,validate=True);expected[name]=digest(payload)
        p=safe(root/name)
        if p.exists():
            if p.read_bytes()!=payload:raise RuntimeError('Refusing to overwrite frozen '+name)
        else:
            with p.open('xb') as f:f.write(payload)
    inputs=read('input_benchmark.json');manifest=read('input_manifest.json')
    assert inputs['service_job_id']=='402816' and len(inputs['tasks'])==256
    assert inputs['thinking'] is False and inputs['max_tokens']==256
    assert inputs['base_url']=='http://dgx-040:18044/v1' and inputs['model']=='deepseek_v4_flash'
    assert manifest['input_sha256']==expected['input_benchmark.json']
    save(root/'stage_manifest.json',{'at':now(),'files':expected,'scope':'Isolated non-formal concurrency benchmark'})
    print(json.dumps({'status':'staged','root':str(root),'files':expected}))
elif r['mode']=='start':
    concurrency=r['concurrency'];assert type(concurrency) is int and concurrency in [16,32,64,128]
    label='c%03d'%concurrency
    fd=os.open(safe(root/'dispatch.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        receipt=root/('dispatch_'+label+'.json')
        if receipt.exists():
            print(json.dumps({'existing_dispatch':True,'receipt':read(receipt.name),'action':'No duplicate client started'}))
        else:
            for existing in root.glob('dispatch_c*.json'):
                previous=existing.stem.removeprefix('dispatch_')
                if not (root/'results'/previous/'summary.json').is_file():
                    raise RuntimeError('Previous benchmark must be reconciled before next round: '+previous)
            for name,value in read('stage_manifest.json')['files'].items():
                assert digest(safe(root/name).read_bytes())==value
            upstream=root.parent/'dvjustice_answering_smoke_20260927_wait900/results'
            with (upstream/'.lock').open('r+') as old_lock:
                fcntl.flock(old_lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                summary=json.loads((upstream/'summary.json').read_text())
                assert summary['success']==10 and summary['counts']=={'ok':10}
            job=subprocess.run([B+'scontrol','-a','show','job','402816','-o'],env=env,capture_output=True,text=True,check=True,timeout=15).stdout
            assert 'JobState=RUNNING' in job and 'NodeList=dgx-040 ' in job
            endpoint=json.loads((service/'endpoint.json').read_text())
            assert endpoint['job_id']=='402816' and endpoint['status']=='ready' and endpoint['max_num_seqs']>=concurrency
            assert endpoint['base_url']=='http://dgx-040:18044/v1'
            command=[B+'srun','--jobid=402816','--overlap','--exact','--nodes=1','--ntasks=1',
                '--cpus-per-task=2','--gres=gpu:8','--unbuffered','--export=ALL',
                '/usr/bin/python3','-B',str(root/'benchmark_concurrency.py'),
                '--input',str(root/'input_benchmark.json'),'--output-dir',str(root/'results'),
                '--concurrency',str(concurrency)]
            record={'status':'starting','at':now(),'allocation_job_id':'402816','new_gpu_allocation':False,
                    'command':command,'concurrency':concurrency,'service_attempt':endpoint['attempt'],
                    'input_sha256':read('stage_manifest.json')['files']['input_benchmark.json'],
                    'performance_only':True}
            save(receipt,record)
            try:
                with safe(root/('client_'+label+'.log')).open('xb') as log:
                    p=subprocess.Popen(command,env=env,cwd=str(root),stdin=subprocess.DEVNULL,
                        stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                record.update(status='dispatched',client_launcher_pid=p.pid)
                save(receipt,record)
            except Exception as exc:
                record.update(status='needs_reconciliation',error_type=type(exc).__name__)
                save(receipt,record);raise
            print(json.dumps(record))
elif r['mode']=='status':
    result={'at':now(),'rounds':{}}
    for concurrency in [16,32,64,128]:
        label='c%03d'%concurrency;state={}
        for name in ['dispatch_'+label+'.json','results/'+label+'/summary.json','results/'+label+'/run_state.json']:
            if (root/name).is_file():state[name]=read(name)
        samples=sorted((root/'results'/label).glob('sample_*.json'))
        if samples:
            sample=read(str(samples[-1].relative_to(root)))
            state['latest_sample']={k:sample.get(k) for k in ('at','healthy','health_status','metrics','gpus','gpu_anomalies','gpu_error')}
        log=safe(root/('client_'+label+'.log'))
        if log.exists():
            with log.open('rb') as f:
                f.seek(max(0,log.stat().st_size-1500));state['log_tail']=f.read().decode(errors='replace')
        if state:result['rounds'][label]=state
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
    parser.add_argument('--concurrency', type=int, choices=[16,32,64,128])
    args = parser.parse_args()
    if args.mode == 'start' and args.concurrency is None:
        parser.error('start requires --concurrency')
    payload = {'mode':args.mode,'concurrency':args.concurrency}
    if args.mode == 'stage':
        files = {'benchmark_concurrency.py':HERE/'benchmark_concurrency.py','run_answer_smoke.py':HERE/'run_answer_smoke.py',
                 'input_benchmark.json':LOCAL/'input_benchmark.json','input_manifest.json':LOCAL/'input_manifest.json'}
        payload['files'] = {name:base64.b64encode(p.read_bytes()).decode() for name,p in files.items()}
    p = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=12','-o','StrictHostKeyChecking=yes',
                        'Group3_slogin','python3 -B -c '+shlex.quote(REMOTE)],input=json.dumps(payload),
                       text=True,capture_output=True,timeout=90)
    record={'at':datetime.datetime.now().astimezone().isoformat(),'mode':args.mode,
            'exit_code':p.returncode,'stderr':p.stderr}
    try:
        result=json.loads(p.stdout)
    except json.JSONDecodeError:
        result={'stdout':p.stdout}
    if args.mode=='fetch' and p.returncode==0:
        count=0
        for name,encoded in result['files'].items():
            rel=Path(name)
            if rel.is_absolute() or '..' in rel.parts or rel.parts[0]!='results':raise RuntimeError('Unsafe path')
            path=LOCAL/rel
            if path.resolve()!=path:raise RuntimeError('Redirected result path')
            data=base64.b64decode(encoded,validate=True);path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists():
                if path.read_bytes()!=data:raise RuntimeError('Existing snapshot differs; preserve history')
            else:
                with path.open('xb') as stream:stream.write(data)
            count+=1
        result={'fetched_file_count':count}
    record['result']=result
    directory=LOCAL/'dispatch_records';directory.mkdir(parents=True,exist_ok=True)
    stamp=datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')
    with (directory/(args.mode+'_'+stamp+'.json')).open('x') as f:
        json.dump(record,f,ensure_ascii=False,indent=2);f.write('\n')
    print(json.dumps(record,ensure_ascii=False,indent=2))
    raise SystemExit(p.returncode)


if __name__ == '__main__':
    main()
