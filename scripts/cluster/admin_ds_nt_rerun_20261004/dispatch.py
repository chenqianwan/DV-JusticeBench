"""Freeze, stage and launch a new nonthinking replication, never the old queues."""
import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
LOCAL=REPO/'data/pkulaw_admin_ds_nt_rerun_20261004'
REMOTE='/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_nt_rerun_20261004'
SOURCE=REPO/'data/pkulaw_v4_thinking_pipeline_20260929'
REMOTE_SOURCE='/aifs4su/hansirui_3rd/chenlong/dvjustice_thinking_pipeline_20260929'
OLD=HERE.parent/'v4_flash_20260927'
SSH=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=20','Group3_slogin']
sys.path.insert(0,str(HERE))
import prepare


def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def stamp():return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')


def record(mode,result):
    d=LOCAL/'checks';d.mkdir(mode=0o700,parents=True,exist_ok=True)
    value={'at':datetime.now(timezone.utc).isoformat(),'mode':mode,**result}
    prepare.write_once(d/(stamp()+'_'+mode+'.json'),prepare.canonical(value)+b'\n')
    return value


def remote(command,mode,timeout=90):
    try:
        p=subprocess.run(SSH+[command],capture_output=True,text=True,timeout=timeout)
        result={'exit_code':p.returncode,'stderr':p.stderr[-6000:]}
        try:result['result']=json.loads(p.stdout)
        except ValueError:result['stdout']=p.stdout[-8000:]
    except subprocess.TimeoutExpired:
        result={'exit_code':124,'error':'SSH timeout. Inspect real launch/exit receipts before any repeat.'}
    return record(mode,result)


def build():
    codes={name:(HERE/name if name in ('runner.py','manager.py','prepare.py') else OLD/name)
           for name in prepare.CODE_NAMES}
    manifest=prepare.build(SOURCE,LOCAL,codes)
    manifest_path=LOCAL/'prepared/manifest.json'
    identifier='bundle_'+sha(manifest_path)[:24]
    bundle=LOCAL/'bundles'/identifier
    files={'code/'+n:p.read_bytes() for n,p in codes.items()}
    files['expected_manifest.json']=manifest_path.read_bytes()
    for name,raw in files.items():prepare.write_once(bundle/name,raw)
    transfer={'files':{name:hashlib.sha256(raw).hexdigest() for name,raw in files.items()},
              'source_root':REMOTE_SOURCE,'root':REMOTE,'manifest_sha256':sha(manifest_path)}
    prepare.write_once(bundle/'transfer_manifest.json',prepare.canonical(transfer)+b'\n')
    return bundle


def stage():
    bundle=build();incoming=REMOTE+'/incoming/'+bundle.name
    bootstrap=('import pathlib,os,json;os.umask(0o077);'
       f'r=pathlib.Path({REMOTE!r});p=pathlib.Path({incoming!r});'
       'assert os.getuid()==1083 and r.resolve()==r and p.resolve()==p;'
       'assert r.parent.is_dir() and r.parent.stat().st_uid==os.getuid();'
       'assert all(not x.exists() or x.stat().st_uid==os.getuid() for x in (r,r/"incoming",p));'
       'p.mkdir(mode=0o700,parents=True,exist_ok=True);print(json.dumps({"incoming":str(p)}))')
    result=remote('python3 -B -c '+shlex.quote(bootstrap),'stage_bootstrap',40)
    if result['exit_code']:return result
    p=subprocess.run(['rsync','-az','--checksum','--partial','-e','ssh -o BatchMode=yes -o ConnectTimeout=20',
                      str(bundle)+'/', 'Group3_slogin:'+incoming+'/'])
    result=record('stage_upload',{'exit_code':p.returncode,'bundle':str(bundle),'incoming':incoming})
    if p.returncode:return result
    finalize=r'''
import pathlib,hashlib,json,os,sys,fcntl,importlib.util
from datetime import datetime,timezone
os.umask(0o077)
r=pathlib.Path(ROOT);b=pathlib.Path(INCOMING)
assert r.resolve()==r and b.resolve()==b and os.getuid()==1083
fd=os.open(r/'stage.lock',os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
assert hashlib.sha256((b/'transfer_manifest.json').read_bytes()).hexdigest()==TRANSFER_SHA
m=json.loads((b/'transfer_manifest.json').read_bytes())
assert m['root']==ROOT and m['source_root']==SOURCE
assert not (r/'launches').exists()
for name,digest in m['files'].items():
    p=b/name
    assert p.resolve()==p and b in p.parents and p.stat().st_uid==os.getuid()
    assert hashlib.sha256(p.read_bytes()).hexdigest()==digest
for name,digest in m['files'].items():
    if not name.startswith('code/'):continue
    p=r/name;assert p.resolve()==p and r in p.parents
    p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    raw=(b/name).read_bytes()
    if p.exists():assert p.read_bytes()==raw
    else:
        with p.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
spec=importlib.util.spec_from_file_location('replicate_prepare',r/'code/prepare.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
manifest=module.build(pathlib.Path(SOURCE),r,{name:r/'code'/name for name in module.CODE_NAMES},b/'expected_manifest.json')
assert hashlib.sha256((r/'prepared/manifest.json').read_bytes()).hexdigest()==m['manifest_sha256']
stage={'manifest_sha256':m['manifest_sha256'],'source_sha256':manifest['source_sha256'],
       'transfer_sha256':TRANSFER_SHA,'staged_questions':11095}
module.write_once(r/'stage_manifest.json',module.canonical(stage)+b'\n')
print(json.dumps({'status':'staged','questions':11095,'manifest_sha256':m['manifest_sha256'],'shards':manifest['shards']}))
'''
    constants=f'ROOT={REMOTE!r}\nINCOMING={incoming!r}\nSOURCE={REMOTE_SOURCE!r}\nTRANSFER_SHA={sha(bundle/"transfer_manifest.json")!r}\n'
    return remote('python3 -B -c '+shlex.quote(constants+finalize),'stage_finalize',180)


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('mode',choices=('build','stage','start','resume','status','fetch'))
    p.add_argument('--endpoint-id',choices=('all','ep146','ep152'),default='all');a=p.parse_args()
    if a.mode=='build':result=record('build',{'exit_code':0,'bundle':str(build())})
    elif a.mode=='stage':result=stage()
    elif a.mode=='fetch':
        dest=LOCAL/'fetched'/stamp();dest.mkdir(mode=0o700,parents=True)
        call=subprocess.run(['rsync','-az','--partial','--exclude=incoming','-e','ssh -o BatchMode=yes -o ConnectTimeout=20',
                             'Group3_slogin:'+REMOTE+'/',str(dest)+'/'])
        result=record('fetch',{'exit_code':call.returncode,'destination':str(dest)})
    else:
        command='python3 -B '+shlex.quote(REMOTE+'/code/manager.py')+' '+a.mode+' --endpoint-id '+a.endpoint_id
        result=remote(command,a.mode,120)
    print(json.dumps(result,ensure_ascii=False,indent=2))
    raise SystemExit(result['exit_code'])


if __name__=='__main__':main()
