#!/usr/bin/env python3
"""Read-only remote SHA inventory and local completed-rsync snapshot audit."""
import argparse
from collections import Counter
import datetime
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess

LOCAL=Path(__file__).resolve().parents[3]/'data/pkulaw_v4_answer_batch128_20260928'
REMOTE=r'''
import hashlib,json,os,pathlib,socket,stat
R=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_answer_batch128_20260928')
assert os.getuid()==1083 and os.environ.get('SLURM_JOB_ID')=='402816'
assert socket.gethostname().split('.')[0]=='dgx-040' and R.resolve()==R and R.stat().st_uid==1083
def checked(p):
    assert p.resolve()==p and p.stat().st_uid==1083 and stat.S_ISREG(p.lstat().st_mode)
    return p
def digest(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for block in iter(lambda:f.read(1024*1024),b''):h.update(block)
    return h.hexdigest()
summary=json.loads(checked(R/'results'/'summary.json').read_text())
assert summary['success']==11085 and summary['failed']==0 and summary['pending']==0
exits=sorted((R/'dispatches').glob('attempt-*.exit.json'));assert exits
assert json.loads(checked(exits[-1]).read_text())['exit_code']==0
paths=[R/'stage_manifest.json']
for name in ['results','dispatches']:
    paths.extend(p for p in (R/name).rglob('*') if p.is_file())
files=[]
for p in sorted(paths):
    checked(p);before=p.stat();h=digest(p);after=p.stat()
    assert (before.st_size,before.st_mtime_ns)==(after.st_size,after.st_mtime_ns),'Evidence changed while hashing'
    files.append({'path':str(p.relative_to(R)),'size':before.st_size,'sha256':h})
print(json.dumps({'schema':1,'remote_root':str(R),'scope':['results/','dispatches/','stage_manifest.json'],
                  'file_count':len(files),'total_bytes':sum(f['size'] for f in files),'files':files,
                  'success':11085,'failed':0,'last_exit_path':str(exits[-1].relative_to(R)),'last_exit_code':0}))
'''


def digest(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):h.update(block)
    return h.hexdigest()


def save(path,value):
    data=(json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()
    with path.open('xb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())


def audit(inventory,root):
    assert inventory['schema']==1 and inventory['remote_root']=='/aifs4su/hansirui_3rd/chenlong/dvjustice_answer_batch128_20260928'
    expected=set();missing=[];mismatched=[];counts=Counter();matched=0;matched_bytes=0
    for item in inventory['files']:
        rel=Path(item['path'])
        assert not rel.is_absolute() and '..' not in rel.parts and item['path'] not in expected
        assert item['path']=='stage_manifest.json' or (len(rel.parts)>1 and rel.parts[0] in {'results','dispatches'})
        expected.add(item['path']);path=root/rel
        assert path.resolve()==path,'Redirected local snapshot path'
        if item['path'].endswith('.request.json'):kind='raw_requests'
        elif item['path'].endswith('.response.json'):kind='raw_responses'
        elif path.name=='terminal.json':kind='terminals'
        elif '/samples/' in item['path']:kind='monitor_samples'
        elif item['path'].startswith('dispatches/'):kind='dispatch_evidence'
        else:kind='other_metadata_or_aggregate'
        counts[kind]+=1
        if not path.is_file():missing.append(item['path']);continue
        if path.stat().st_size!=item['size'] or digest(path)!=item['sha256']:
            mismatched.append(item['path']);continue
        matched+=1;matched_bytes+=item['size']
    assert len(expected)==inventory['file_count'] and sum(x['size'] for x in inventory['files'])==inventory['total_bytes']
    actual={'stage_manifest.json'} if (root/'stage_manifest.json').is_file() else set()
    for directory in ['results','dispatches']:
        actual.update(str(p.relative_to(root)) for p in (root/directory).rglob('*') if p.is_file())
    extras=sorted(actual-expected)
    return {'integrity_passed':not(missing or mismatched or extras),'expected_files':inventory['file_count'],
            'verified_files':matched,'expected_bytes':inventory['total_bytes'],'verified_bytes':matched_bytes,
            'counts_by_kind':dict(counts),'missing':missing,'mismatched':mismatched,'extra':extras,
            'snapshot_root':str(root),'model_requests':0,'remote_writes':False,'new_allocations':False}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path,default=LOCAL/'fetched/completed_attempt001_rsync')
    parser.add_argument('--inventory',type=Path,help='Reuse a previously saved inventory without any SSH')
    parser.add_argument('--timeout',type=int,default=650)
    parser.add_argument('--retry-once',action='store_true',help='At most one further SSH attempt, only on exit255')
    args=parser.parse_args();os.umask(0o077)
    root=args.snapshot.resolve();assert root.is_dir()
    monitoring=LOCAL/'monitoring';monitoring.mkdir(parents=True,exist_ok=True,mode=0o700)
    stamp=datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    attempts=[]
    if args.inventory:
        inventory_path=args.inventory.resolve();inventory=json.loads(inventory_path.read_text())
    else:
        command=['env','SLURM_CONF=/cm/shared/apps/slurm/var/etc/slurm/slurm.conf','PATH=/usr/bin:/bin',
                 '/cm/shared/apps/slurm/current/bin/srun','--jobid=402816','--overlap','--exact','--nodes=1','--ntasks=1',
                 '--cpus-per-task=2','--gres=none','--unbuffered','--export=ALL','/usr/bin/python3','-B','-c',REMOTE]
        ssh=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes',
             'Group3_slogin',shlex.join(command)]
        for index in range(2 if args.retry_once else 1):
            try:result=subprocess.run(ssh,capture_output=True,text=True,timeout=args.timeout)
            except subprocess.TimeoutExpired:
                attempts.append({'attempt':index+1,'status':'timeout','timeout_seconds':args.timeout});break
            attempts.append({'attempt':index+1,'exit_code':result.returncode,'stderr':result.stderr[-1600:]})
            if result.returncode==0:
                inventory=json.loads(result.stdout);break
            if result.returncode!=255:break
        else:result=None
        if not attempts or attempts[-1].get('exit_code')!=0:
            receipt={'remote_inventory_unavailable':True,'attempts':attempts,'local_snapshot_unchanged':True}
            path=monitoring/('remote_inventory_unavailable_'+stamp+'.json');save(path,receipt)
            print(json.dumps({'status':'remote_inventory_unavailable','receipt':str(path),'attempts':attempts}));raise SystemExit(1)
        inventory_path=monitoring/('remote_evidence_inventory_'+stamp+'.json');save(inventory_path,inventory)
        print(json.dumps({'event':'remote_inventory_saved','files':inventory['file_count'],'bytes':inventory['total_bytes'],
                          'manifest':str(inventory_path)}),flush=True)
    comparison=audit(inventory,root)
    comparison.update(inventory_path=str(inventory_path),inventory_sha256=digest(inventory_path),ssh_attempts=attempts)
    report=monitoring/('snapshot_sha_audit_'+stamp+'.json');save(report,comparison)
    brief={k:v for k,v in comparison.items() if k not in {'missing','mismatched','extra','ssh_attempts'}}
    brief.update(report=str(report),missing_count=len(comparison['missing']),mismatched_count=len(comparison['mismatched']),
                 extra_count=len(comparison['extra']))
    print(json.dumps(brief,ensure_ascii=False,indent=2),flush=True)
    if not comparison['integrity_passed']:raise SystemExit(2)


if __name__=='__main__':main()
