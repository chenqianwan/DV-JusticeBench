"""Single-launch guards for independent administrator-endpoint rerun clients."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import time
import urllib.request

ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_nt_rerun_20261004')
ENDPOINTS = {'ep146':'http://10.33.4.146:18044/v1', 'ep152':'http://10.33.4.152:18044/v1'}


def now():return datetime.now(timezone.utc).isoformat()
def stamp():return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
def read(p):return json.loads(p.read_bytes()) if p.exists() else None
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()


def safe(p):
    if p.resolve()!=p or not (p==ROOT or ROOT in p.parents):raise ValueError('Unsafe target')
    for x in (p,*p.parents):
        if (x==ROOT or ROOT in x.parents) and x.exists() and x.stat().st_uid!=os.getuid():
            raise ValueError('Unexpected owner')
    return p


def write(p,value):
    with safe(p).open('x') as f:
        json.dump(value,f,ensure_ascii=False,sort_keys=True,allow_nan=False)
        f.write('\n');f.flush();os.fsync(f.fileno())


@contextmanager
def lock(p):
    fd=os.open(safe(p),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    try:
        fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally:os.close(fd)


def identity(pid):
    if type(pid) is not int or pid<=0:return None
    try:
        proc=Path('/proc')/str(pid)
        raw=(proc/'stat').read_text();fields=raw[raw.rfind(')')+2:].split()
        args=(proc/'cmdline').read_bytes().split(b'\0')
        return {'pid':pid,'start_ticks':fields[19],'state':fields[0],
                'runner_match':os.fsencode(ROOT/'code/runner.py') in args,
                'ep146':b'ep146' in args,'ep152':b'ep152' in args}
    except (FileNotFoundError,ProcessLookupError):return None


def live(old,current):
    return bool(old and current and current['state']!='Z' and old['pid']==current['pid']
                and str(old['start_ticks'])==str(current['start_ticks']))


def verify(full=False):
    m=read(ROOT/'prepared/manifest.json')
    if not m or m['run_id']!='pkulaw_admin_ds_nt_rerun_20261004':raise ValueError('Missing frozen manifest')
    expected=read(ROOT/'stage_manifest.json')
    if not expected or sha(ROOT/'prepared/manifest.json')!=expected['manifest_sha256']:
        raise ValueError('Stage/manifest identity mismatch')
    for name,digest in m['code_sha256'].items():
        if sha(safe(ROOT/'code'/name))!=digest:raise ValueError('Frozen code differs: '+name)
    if full:
        for name,digest in m['artifacts'].items():
            if sha(safe(ROOT/name))!=digest:raise ValueError('Frozen input differs: '+name)
    return m


def probe(ep):
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    result={'at':now(),'base_url':ENDPOINTS[ep]}
    for route in ('/health','/v1/models','/version','/metrics'):
        try:
            with opener.open(ENDPOINTS[ep][:-3]+route,timeout=8) as response:
                raw=response.read().decode();item={'http_status':response.status}
            if route=='/metrics':
                wanted=('num_requests_running','num_requests_waiting','kv_cache_usage_perc','num_preemptions_total','generation_tokens_total')
                item['lines']=[];item['values']={}
                for k in wanted:
                    pattern=re.compile(r'^vllm:'+re.escape(k)+r'(?:\{[^}]*\})?\s+(\S+)(?:\s+\S+)?$')
                    matches=[(line,pattern.match(line)) for line in raw.splitlines()]
                    observed=[(line,float(match.group(1))) for line,match in matches if match]
                    item['lines'].extend(line for line,value in observed)
                    values=[value for line,value in observed]
                    item['values'][k]=(sum(values) if values and all(math.isfinite(v) for v in values) else None)
            elif raw:item['body']=json.loads(raw)
            result[route]=item
        except Exception as e:result[route]={'error':type(e).__name__}
    return result


def records(ep):
    result=[]
    for d in sorted((ROOT/'launches'/ep).glob('*')):
        intent,receipt=read(d/'intent.json'),read(d/'receipt.json')
        who=identity(receipt['pid']) if receipt else None
        result.append({'session':d.name,'intent':intent,'receipt':receipt,
                       'actual_identity':who,'same_live_process':live((receipt or {}).get('identity'),who),
                       'exit':read(ROOT/ep/'sessions'/d.name/'exit.json'),
                       'ambiguous_launch':not bool(intent and receipt and receipt.get('identity'))})
    return result


def start(ep,mode):
    verify(full=True)
    (ROOT/'launches'/ep).mkdir(mode=0o700,parents=True,exist_ok=True)
    (ROOT/ep).mkdir(mode=0o700,exist_ok=True)
    with lock(ROOT/ep/'dispatch.lock'):
        old=records(ep)
        if any(r['ambiguous_launch'] for r in old):raise RuntimeError('Ambiguous launch; do not relaunch')
        for r in old:
            if r['same_live_process']:
                if not r['actual_identity']['runner_match'] or not r['actual_identity'][ep]:raise RuntimeError('PID identity conflict')
                return {'action':'already_running_no_duplicate','endpoint':ep,'session':r['session']}
        if mode=='start' and old:raise RuntimeError('Prior launch exists; explicit resume required')
        if mode=='resume' and not old:raise RuntimeError('No prior launch')
        for r in old:
            ex=r['exit']
            if not ex or ex.get('local_futures_finished') is not True or ex.get('drained') is not True:
                raise RuntimeError('Missing true exit/drain; inspect before resuming')
        state=read(ROOT/ep/'results/summary.json')
        if state and state.get('pending')==0:return {'action':'already_complete','endpoint':ep}
        health=probe(ep)
        if health['/health'].get('http_status')!=200 or health['/v1/models'].get('http_status')!=200:
            raise RuntimeError('Endpoint not healthy')
        if 'deepseek_v4_flash' not in {m['id'] for m in health['/v1/models']['body']['data']}:
            raise RuntimeError('Expected model missing')
        vals=health['/metrics'].get('values',{})
        if not vals or vals.get('num_requests_running')!=0 or vals.get('num_requests_waiting')!=0:
            raise RuntimeError('Endpoint not proven idle; no overlapping launch')
        (ROOT/ep/'results').mkdir(mode=0o700,exist_ok=True)
        with lock(ROOT/ep/'results/.lock'):
            session=stamp();d=ROOT/'launches'/ep/session;d.mkdir(mode=0o700)
            command=['/usr/bin/python3','-B',str(ROOT/'code/runner.py'),'--root',str(ROOT),
                     '--endpoint-id',ep,'--session',session]
            if mode=='resume':command.append('--resume')
            write(d/'preflight.json',health)
            write(d/'intent.json',{'at':now(),'endpoint':ep,'session':session,'mode':mode,'command':command,
                                  'workers':128,'answer_thinking':False,'scoring_thinking':True})
            with (d/'runner.log').open('x') as log:
                child=subprocess.Popen(command,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                                       start_new_session=True,close_fds=True,
                                       env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1'))
            who=None
            for _ in range(20):
                who=identity(child.pid)
                if who and who['runner_match'] and who[ep]:break
                time.sleep(.02)
            write(d/'receipt.json',{'at':now(),'session':session,'pid':child.pid,'identity':who,
                                    'dispatch_is_not_inference_confirmation':True})
            return {'action':'dispatched_verify_status','endpoint':ep,'session':session,'identity':who}


def status():
    verify()
    return {'at':now(),'root':str(ROOT),'endpoints':{ep:{'launches':records(ep),
            'summary':read(ROOT/ep/'results/summary.json'),'health':probe(ep)} for ep in ENDPOINTS}}


def main():
    os.umask(0o077)
    if os.getuid()!=1083 or ROOT.resolve()!=ROOT:raise RuntimeError('Wrong execution identity')
    p=argparse.ArgumentParser();p.add_argument('mode',choices=('start','resume','status'))
    p.add_argument('--endpoint-id',choices=tuple(ENDPOINTS)+('all',),default='all');a=p.parse_args()
    result=status() if a.mode=='status' else [start(ep,a.mode) for ep in (ENDPOINTS if a.endpoint_id=='all' else [a.endpoint_id])]
    print(json.dumps(result,ensure_ascii=False,allow_nan=False))


if __name__=='__main__':main()
