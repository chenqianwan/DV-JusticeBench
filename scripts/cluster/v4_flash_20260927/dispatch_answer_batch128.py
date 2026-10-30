#!/usr/bin/env python3
"""Dispatch frozen 11085-answer/128-concurrency run inside existing job 402816.

No scoring, new allocation, model restart, or implicit retry. Use `resume` only
after reviewing an exited dispatch; uncertain dispatches require reconciliation.
Large inputs travel compressed over SSH stdin, never in argv or status logs.
"""
import argparse
import ast
import base64
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import zlib

HERE=Path(__file__).resolve().parent
LOCAL=HERE.parents[2]/'data/pkulaw_v4_answer_batch128_20260928'
DATA_FILES=('input_tasks.json','input_manifest.json','reused_answers.jsonl','excluded_cases.json')
SCRIPT_FILES=('run_answer_batch128.py','run_answer_smoke.py','benchmark_concurrency.py')
MAX_FILE_BYTES=256*1024*1024


def digest(data):return hashlib.sha256(data).hexdigest()


def pack(data):
    if len(data)>MAX_FILE_BYTES:raise ValueError('Source exceeds per-file size limit')
    zipped=gzip.compress(data,compresslevel=6,mtime=0)
    return {'size':len(data),'sha256':digest(data),'gzip_size':len(zipped),
            'gzip_sha256':digest(zipped),'data':base64.b64encode(zipped).decode()}


def unpack(item):
    if type(item['size']) is not int or not 0<=item['size']<=MAX_FILE_BYTES:raise ValueError('Invalid size')
    zipped=base64.b64decode(item['data'],validate=True)
    if len(zipped)!=item['gzip_size'] or digest(zipped)!=item['gzip_sha256']:raise ValueError('Compressed hash/size mismatch')
    obj=zlib.decompressobj(16+zlib.MAX_WBITS)
    data=obj.decompress(zipped,item['size']+1)
    if not obj.eof or obj.unused_data or obj.unconsumed_tail or len(data)!=item['size'] or digest(data)!=item['sha256']:
        raise ValueError('Uncompressed hash/size mismatch')
    return data


WRAPPER=r'''#!/usr/bin/env python3
"""Persistent execution receipt for the fixed answer-only batch; no API access."""
import argparse,datetime,json,os,pathlib,re,socket,subprocess,sys,uuid
R=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_answer_batch128_20260928')
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def identity(pid):
    p=pathlib.Path('/proc')/str(pid)/'stat';assert p.stat().st_uid==1083
    raw=p.read_text();fields=raw[raw.rfind(')')+2:].split();assert fields[0]!='Z'
    return {'pid':pid,'started':fields[19]}
def write(p,value):
    assert p.resolve()==p and R in p.parents
    temporary=p.parent/('.'+p.name+'.'+uuid.uuid4().hex)
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as f:json.dump(value,f);f.write('\n');f.flush();os.fsync(f.fileno())
    os.link(temporary,p);os.unlink(temporary)
    fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser();parser.add_argument('--dispatch-id',required=True);args=parser.parse_args()
    assert re.fullmatch(r'attempt-[0-9]{3}',args.dispatch_id)
    assert os.getuid()==1083 and os.environ.get('SLURM_JOB_ID')=='402816'
    assert socket.gethostname().split('.')[0]=='dgx-040' and R.resolve()==R and R.stat().st_uid==1083
    p=R/'dispatches'/(args.dispatch_id+'.intent.json');assert p.resolve()==p and p.stat().st_uid==1083
    intent=json.loads(p.read_text());assert intent['dispatch_id']==args.dispatch_id
    command=['/usr/bin/python3','-B',str(R/'run_answer_batch128.py'),'--input',str(R/'input_tasks.json'),
             '--output-dir',str(R/'results')]
    if intent['resume']:command.append('--resume')
    execution={'dispatch_id':args.dispatch_id,'job_id':'402816','node':'dgx-040',
               'wrapper_identity':identity(os.getpid()),'started_at':now(),'resume':intent['resume']}
    write(R/'dispatches'/(args.dispatch_id+'.wrapper.json'),execution)
    try:
        child=subprocess.Popen(command,cwd=R,stdin=subprocess.DEVNULL)
        execution.update(runner_identity=identity(child.pid))
        write(R/'dispatches'/(args.dispatch_id+'.execution.json'),execution)
        code=child.wait()
    except Exception as exc:
        write(R/'dispatches'/(args.dispatch_id+'.exit.json'),{'dispatch_id':args.dispatch_id,
              'ended_at':now(),'exit_code':None,'error_type':type(exc).__name__})
        raise
    write(R/'dispatches'/(args.dispatch_id+'.exit.json'),{'dispatch_id':args.dispatch_id,
          'ended_at':now(),'exit_code':code,'runner_identity':execution['runner_identity']})
    return code
if __name__=='__main__':sys.exit(main())
'''


REMOTE=r"""
import ast,base64,contextlib,datetime,fcntl,gzip,hashlib,json,os,pathlib,re,socket,stat,subprocess,sys,uuid,zlib
r=json.load(sys.stdin);mode=r['mode']
assert mode in {'stage','start','resume','status','fetch'}
R=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_answer_batch128_20260928')
S=R.parent/'dvjustice_v4flash_20260927'
B='/cm/shared/apps/slurm/current/bin/'
E={'PATH':'/usr/bin:/bin','LANG':'C.UTF-8','PYTHONDONTWRITEBYTECODE':'1','PYTHONNOUSERSITE':'1',
   'SLURM_CONF':'/cm/shared/apps/slurm/var/etc/slurm/slurm.conf'}
MAX=256*1024*1024
assert os.getuid()==1083 and R.resolve()==R and R.parent.stat().st_uid==1083
os.umask(0o077)
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(b):return hashlib.sha256(b).hexdigest()
def safe(p):
    p=pathlib.Path(p)
    assert p.resolve()==p and R in (p,*p.parents)
    for q in (p,*p.parents):
        if q==R or R in q.parents:
            if q.exists():assert q.stat().st_uid==1083
    return p
def read(p):
    p=safe(p);assert stat.S_ISREG(p.lstat().st_mode)
    return json.loads(p.read_text())
def encoded(x):return (json.dumps(x,ensure_ascii=False,indent=2,sort_keys=True)+'\n').encode()
def write_once(p,b):
    p=safe(p)
    if p.exists():
        if p.read_bytes()!=b:raise RuntimeError('Frozen file differs: '+p.name)
        return
    temporary=safe(p.parent/('.'+p.name+'.'+uuid.uuid4().hex))
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as f:f.write(b);f.flush();os.fsync(f.fileno())
    try:os.link(temporary,p)
    finally:os.unlink(temporary)
    fd=os.open(p.parent,os.O_RDONLY|os.O_DIRECTORY)
    try:os.fsync(fd)
    finally:os.close(fd)
def unbundle(x):
    assert type(x['size']) is int and 0<=x['size']<=MAX
    zipped=base64.b64decode(x['data'],validate=True)
    assert len(zipped)==x['gzip_size'] and sha(zipped)==x['gzip_sha256']
    obj=zlib.decompressobj(16+zlib.MAX_WBITS);data=obj.decompress(zipped,x['size']+1)
    assert obj.eof and not obj.unused_data and not obj.unconsumed_tail
    assert len(data)==x['size'] and sha(data)==x['sha256']
    return data
def bundle(b):
    assert len(b)<=MAX
    zipped=gzip.compress(b,compresslevel=6,mtime=0)
    return {'size':len(b),'sha256':sha(b),'gzip_size':len(zipped),'gzip_sha256':sha(zipped),'data':base64.b64encode(zipped).decode()}
def checked_sources():
    manifest=read(R/'stage_manifest.json')
    for name,item in manifest['files'].items():
        assert pathlib.PurePosixPath(name).name==name
        b=safe(R/name).read_bytes()
        assert len(b)==item['size'] and sha(b)==item['sha256'],'Staged source changed: '+name
    return manifest
def job():
    out=subprocess.run([B+'scontrol','-a','show','job','402816','-o'],env=E,capture_output=True,text=True,check=True,timeout=20).stdout
    fields={k:m.group(1) for k in ['JobId','JobState','NodeList','TimeLimit','EndTime','AllocTRES']
        if (m:=re.search(r'(?:^|\s)'+k+r'=(\S+)',out))}
    assert fields.get('JobState')=='RUNNING' and fields.get('NodeList')=='dgx-040'
    assert fields.get('TimeLimit')=='5-00:00:00'
    return fields
def proc_alive(identity):
    if not identity:return False
    p=pathlib.Path('/proc')/str(identity['pid'])/'stat'
    if not p.exists():return False
    if p.stat().st_uid!=1083:return False
    raw=p.read_text();fields=raw[raw.rfind(')')+2:].split()
    return fields[0]!='Z' and fields[19]==identity['started']
def identity(pid):
    p=pathlib.Path('/proc')/str(pid)/'stat';assert p.stat().st_uid==1083
    raw=p.read_text();fields=raw[raw.rfind(')')+2:].split()
    return {'pid':pid,'started':fields[19]}
def latest():
    paths=sorted((R/'dispatches').glob('attempt-*.intent.json'))
    for p in paths:assert re.fullmatch(r'attempt-[0-9]{3}\.intent\.json',p.name)
    return paths[-1] if paths else None
NODE=r'''
import contextlib,fcntl,json,os,pathlib,socket,stat,sys,urllib.request,re
r=json.load(sys.stdin)
R=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_answer_batch128_20260928')
S=R.parent/'dvjustice_v4flash_20260927'
assert os.getuid()==1083 and os.environ.get('SLURM_JOB_ID')=='402816' and socket.gethostname().split('.')[0]=='dgx-040'
def read(p):
    assert p.resolve()==p and p.stat().st_uid==1083 and stat.S_ISREG(p.lstat().st_mode)
    return json.loads(p.read_text())
def alive(i):
    if not i:return False
    p=pathlib.Path('/proc')/str(i['pid'])/'stat'
    if not p.exists() or p.stat().st_uid!=1083:return False
    raw=p.read_text();v=raw[raw.rfind(')')+2:].split()
    return v[0]!='Z' and v[19]==i['started']
result={'node':'dgx-040','known_processes':{}}
for label,ident in r.get('identities',{}).items():result['known_processes'][label]=alive(ident)
if r['action']=='preflight':
    assert not any(result['known_processes'].values()),'Previous dispatch still running'
    endpoint=read(S/'endpoint.json')
    assert endpoint['job_id']=='402816' and endpoint['status']=='ready' and endpoint['max_num_seqs']>=128
    assert endpoint['base_url']=='http://dgx-040:18044/v1' and endpoint['model']=='deepseek_v4_flash'
    roots=[R.parent/'dvjustice_answering_smoke_20260927'/'results',R.parent/'dvjustice_answering_smoke_20260927_wait900'/'results']
    roots += [R.parent/'dvjustice_concurrency_benchmark_20260927'/'results'/('c%03d'%c) for c in [16,32,64,128]]
    roots += [R/'results']
    with contextlib.ExitStack() as stack:
        for directory in roots:
            p=directory/'.lock'
            if p.exists():
                assert p.resolve()==p and p.stat().st_uid==1083 and stat.S_ISREG(p.lstat().st_mode)
                f=stack.enter_context(p.open('r+'));fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        keypath=S/'api_key';assert keypath.resolve()==keypath and keypath.stat().st_uid==1083 and not keypath.stat().st_mode&0o077
        key=keypath.read_text().strip();assert len(key)>=32
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request=urllib.request.Request('http://127.0.0.1:18044/health',headers={'Authorization':'Bearer '+key})
        with opener.open(request,timeout=5) as response:assert response.status==200
        request=urllib.request.Request('http://127.0.0.1:18044/metrics',headers={'Authorization':'Bearer '+key})
        with opener.open(request,timeout=5) as response:metrics=response.read().decode()
        result['metrics']={}
        for suffix in ['running','waiting']:
            values=re.findall(r'^vllm:num_requests_'+suffix+r'(?:\{[^\n]*\})?\s+([0-9.eE+-]+)\s*$',metrics,re.M)
            assert values,'Missing idle metric'
            result['metrics'][suffix]=sum(float(v) for v in values)
            assert result['metrics'][suffix]==0,'Service is not idle'
    result['service_attempt']=endpoint['attempt']
elif r['action']!='status':raise RuntimeError('Unexpected node action')
print(json.dumps(result))
'''
def node(action,identities):
    command=[B+'srun','--jobid=402816','--overlap','--exact','--nodes=1','--ntasks=1','--cpus-per-task=1',
             '--gres=none','--unbuffered','--export=ALL','/usr/bin/python3','-B','-c',NODE]
    out=subprocess.run(command,input=json.dumps({'action':action,'identities':identities}),env=E,capture_output=True,text=True,timeout=40)
    if out.returncode:raise RuntimeError('Node check failed: '+out.stderr[-1600:])
    return json.loads(out.stdout)
def dispatch_info(path):
    intent=read(path);ident=intent['dispatch_id'];out={'intent':intent}
    identities={}
    for kind in ['launcher','wrapper','execution','exit']:
        p=R/'dispatches'/(ident+'.'+kind+'.json')
        if p.exists():
            value=read(p);out[kind]=value
            for key in ['wrapper_identity','runner_identity']:
                if key in value:identities[key]=value[key]
    if 'launcher' in out:out['launcher_alive']=proc_alive(out['launcher']['launcher_identity'])
    out['node_identities']=identities
    return out
if mode=='stage':
    names={'run_answer_batch128.py','run_answer_smoke.py','benchmark_concurrency.py','answer_dispatch_wrapper.py',
           'input_tasks.json','input_manifest.json','reused_answers.jsonl','excluded_cases.json'}
    assert set(r)=={'mode','files'} and set(r['files'])==names
    safe(R).mkdir(mode=0o700,exist_ok=True)
    assert not R.stat().st_mode&0o077,'Run directory permissions too broad'
    fd=os.open(safe(R/'staging.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        files={}
        for name,item in r['files'].items():
            b=unbundle(item)
            if name.endswith('.py'):ast.parse(b.decode())
            write_once(R/name,b);files[name]={k:item[k] for k in ['size','sha256']}
        inputs=read(R/'input_tasks.json');manifest=read(R/'input_manifest.json')
        assert inputs['run_id']=='pkulaw_v4_answer_batch128_20260928' and inputs['service_job_id']=='402816'
        assert inputs['base_url']=='http://dgx-040:18044/v1' and inputs['model']=='deepseek_v4_flash'
        assert len(inputs['tasks'])==11085 and inputs['workers']==128 and inputs['thinking'] is False
        assert inputs['temperature']==0.3 and inputs['initial_max_tokens']==3000 and inputs['timeout_s']==1800
        assert manifest['input_tasks_sha256']==files['input_tasks.json']['sha256']
        for name in ['input_tasks.json','reused_answers.jsonl','excluded_cases.json']:
            assert manifest['files'][name]['sha256']==files[name]['sha256']
            assert manifest['files'][name]['bytes']==files[name]['size']
        reused=[json.loads(line) for line in (R/'reused_answers.jsonl').read_text().splitlines() if line.strip()]
        excluded=read(R/'excluded_cases.json')
        assert len(reused)==10 and len(excluded)==18
        excludeids={x['case_id'] for x in excluded}
        assert len(excludeids)==18 and not any(t['case_id'] in excludeids for t in inputs['tasks'])
        assert not any(t['case_id'] in excludeids for t in reused)
        audit={'files':files,'new_network_questions':11085,'reused_answers':10,'permanently_excluded_cases':18,
               'stage_scope':'Answer-only; no task_mapping/judgment data; existing allocation only'}
        write_once(R/'stage_manifest.json',encoded(audit));checked_sources()
        print(json.dumps({'status':'staged','root':str(R),'file_count':len(files),
                          'uncompressed_bytes':sum(x['size'] for x in files.values()),'input_sha256':files['input_tasks.json']['sha256']}))
elif mode in {'start','resume'}:
    assert set(r)=={'mode'}
    checked_sources();live=job();safe(R/'dispatches').mkdir(mode=0o700,exist_ok=True)
    fd=os.open(safe(R/'dispatch.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        previous=latest();info=dispatch_info(previous) if previous else None
        if previous and mode=='start':
            print(json.dumps({'status':'existing_dispatch_no_duplicate','dispatch_id':info['intent']['dispatch_id'],
                              'action':'Use status; resume is explicit only after confirmed exit.'}));sys.exit(0)
        if mode=='resume':
            assert previous,'No existing dispatch to resume'
            if info.get('launcher_alive'):raise RuntimeError('Previous login launcher still active')
            if 'exit' not in info or info['exit'].get('exit_code') is None:
                raise RuntimeError('Missing/ambiguous runner exit receipt; reconcile before resume')
            assert (R/'results'/'run_identity.json').exists(),'Runner never initialized; reconcile before resume'
            summary=read(R/'results'/'summary.json') if (R/'results'/'summary.json').exists() else {}
            if summary.get('pending')==0:
                print(json.dumps({'status':'no_pending_tasks','action':'No runner launched; terminal failures need separate review.'}));sys.exit(0)
        elif (R/'results'/'run_identity.json').exists():raise RuntimeError('Pre-existing run without dispatch; reconcile')
        preflight=node('preflight',info['node_identities'] if info else {})
        number=int(info['intent']['dispatch_id'].split('-')[1])+1 if info else 1
        assert number<1000
        ident='attempt-%03d'%number
        intent={'dispatch_id':ident,'requested_at':now(),'resume':mode=='resume','job_id':'402816','node':'dgx-040',
                'new_gpu_allocation':False,'workers':128,'new_network_questions':11085,'scoring_enabled':False,
                'input_sha256':read(R/'stage_manifest.json')['files']['input_tasks.json']['sha256'],
                'service_attempt':preflight['service_attempt'],'job_end_time':live['EndTime']}
        write_once(R/'dispatches'/(ident+'.intent.json'),encoded(intent))
        command=[B+'srun','--jobid=402816','--overlap','--exact','--nodes=1','--ntasks=1','--cpus-per-task=8',
                 '--gres=gpu:8','--unbuffered','--export=ALL','/usr/bin/python3','-B',str(R/'answer_dispatch_wrapper.py'),
                 '--dispatch-id',ident]
        with safe(R/'dispatches'/(ident+'.log')).open('xb') as log:
            child=subprocess.Popen(command,env=E,cwd=R,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        launcher={'dispatch_id':ident,'dispatched_at':now(),'launcher_identity':identity(child.pid)}
        write_once(R/'dispatches'/(ident+'.launcher.json'),encoded(launcher))
        print(json.dumps({'status':'dispatched','dispatch_id':ident,'resume':mode=='resume',
                          'launcher_pid':child.pid,'workers':128,'questions':11085,'new_gpu_allocation':False}))
elif mode=='status':
    result={'at':now(),'job':job()}
    if not R.exists():result['status']='not_staged'
    else:
        previous=latest()
        if previous:
            info=dispatch_info(previous);result['dispatch_id']=info['intent']['dispatch_id']
            result['login_launcher_alive']=info.get('launcher_alive')
            if 'exit' in info:result['exit']=info['exit']
            result['node_processes']=node('status',info['node_identities'])['known_processes']
        fields={'status','running','completed','success','failed','needs_reconcile','pending','inflight','stop_reasons',
                'max_inflight','updated_at','finished_at','started_at','completed_at','elapsed_s','current_session_walltime_s',
                'counts','network_tasks','reused_existing_answers','combined_expected_answers','usage_total','usage_totals',
                'new_completed','actual_attempts','recorded_responses','unmatched_intents','length_retries',
                'walltime_s','sampler_count','workers','scoring_performed'}
        for name in ['run_state.json','summary.json']:
            p=R/'results'/name
            if p.exists():result[name]={k:v for k,v in read(p).items() if k in fields}
        count=max((result.get(n,{}).get('sampler_count',0) for n in ['run_state.json','summary.json']),default=0)
        if type(count) is int and count>0:
            p=R/'results'/'samples'/('sample_%06d.json'%(count-1))
            if p.exists():
                sample=read(p)
                result['latest_sample']={k:sample.get(k) for k in ['at','healthy','health_status','metrics',
                                         'gpu_returncode','gpu_anomalies','probe_error']}
                result['latest_sample']['gpu_count']=len(sample.get('gpus') or [])
    print(json.dumps(result))
elif mode=='fetch':
    assert set(r)=={'mode','raw'} and type(r['raw']) is bool
    files={};total=0
    for directory in ['results','dispatches']:
        for p in sorted((R/directory).rglob('*')):
            if not p.is_file() or p.suffix not in {'.json','.jsonl'}:continue
            relative=p.relative_to(R)
            if directory=='results' and len(relative.parts)>2 and p.name!='terminal.json' and not r['raw']:continue
            b=safe(p).read_bytes();total+=len(b)
            if total>1024*1024*1024:raise RuntimeError('Fetch exceeds 1 GiB; use smaller result batches')
            files[str(relative)]=bundle(b)
    if not files:raise RuntimeError('No result evidence yet')
    print(json.dumps({'files':files,'bytes':total,'raw_attempts_included':r['raw']}))
"""


def save_fetch(result,local=LOCAL):
    stamp=datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')
    root=local/'fetched'/stamp
    root.mkdir(parents=True,mode=0o700)
    total=0
    for name,item in result['files'].items():
        rel=Path(name)
        if rel.is_absolute() or '..' in rel.parts or rel.parts[0] not in {'results','dispatches'}:
            raise ValueError('Unsafe fetched result path')
        path=root/rel
        if path.resolve()!=path:raise ValueError('Redirected output path')
        data=unpack(item);total+=len(data)
        path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
        with path.open('xb') as stream:stream.write(data)
    return {'snapshot':str(root),'file_count':len(result['files']),'uncompressed_bytes':total,
            'raw_attempts_included':result['raw_attempts_included']}


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['stage','start','resume','status','fetch'])
    parser.add_argument('--raw',action='store_true',help='Fetch raw request/response receipts as well as terminals')
    args=parser.parse_args()
    if args.raw and args.mode!='fetch':parser.error('--raw is only valid for fetch')
    payload={'mode':args.mode}
    if args.mode=='stage':
        sources={name:(LOCAL/name).read_bytes() for name in DATA_FILES}
        sources.update({name:(HERE/name).read_bytes() for name in SCRIPT_FILES})
        sources['answer_dispatch_wrapper.py']=WRAPPER.encode()
        for name,data in sources.items():
            if name.endswith('.py'):ast.parse(data.decode())
        payload['files']={name:pack(data) for name,data in sources.items()}
    elif args.mode=='fetch':payload['raw']=args.raw
    command=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes',
             'Group3_slogin','python3 -B -c '+shlex.quote(REMOTE)]
    completed=subprocess.run(command,input=json.dumps(payload),text=True,capture_output=True,
                             timeout=300 if args.mode in {'stage','fetch'} else 100)
    record={'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'mode':args.mode,
            'exit_code':completed.returncode,'stderr':completed.stderr}
    try:result=json.loads(completed.stdout)
    except json.JSONDecodeError:result={'output_parse_failed':True,'stdout_bytes':len(completed.stdout.encode())}
    if args.mode=='fetch' and completed.returncode==0:result=save_fetch(result)
    record['result']=result
    records=LOCAL/'dispatch_records';records.mkdir(parents=True,exist_ok=True,mode=0o700)
    stamp=datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')
    with (records/(args.mode+'_'+stamp+'.json')).open('x') as stream:
        json.dump(record,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(record,ensure_ascii=False,indent=2))
    raise SystemExit(completed.returncode)


if __name__=='__main__':main()
