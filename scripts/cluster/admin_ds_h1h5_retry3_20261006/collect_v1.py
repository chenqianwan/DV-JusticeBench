"""Read-only completed retry evidence collection; no inference or service calls."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile

ROOT = Path('/Users/chenlong/WorkSpace/huangyidan/data/pkulaw_admin_ds_h1h5_retry3_20261006/collection_v1')
SESSION = '20261006T041135265808Z'
EXPECTED_INDEX = '15dd0686149cad320065cf63613d51dcaf090edc9914835c44d89be6d8422bdb'
MANIFEST = '__collection_manifest__.json'

REMOTE = r'''
from pathlib import Path
from datetime import datetime,timezone
import fcntl,gzip,hashlib,io,json,os,stat,sys,tarfile
os.nice(10)
root=Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_h1h5_retry3_20261006')
session='20261006T041135265808Z'
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
 return h.hexdigest()
def read(p):return json.loads(p.read_bytes())
def live(identity):
 p=Path('/proc')/str(identity['pid'])/'stat'
 try:r=p.read_text().rsplit(') ',1)[1].split()
 except FileNotFoundError:return False
 return r[19]==identity['start_ticks']
lock=(root/'coordinator.lock').open('rb')
fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
finish_path=root/'sessions'/session/'exit.json'
finish=read(finish_path)
assert finish['state']=='completed' and not live(finish['identity'])
assert all(finish[k] is True for k in ('drained','local_futures_finished','all_inputs_have_auditable_terminal'))
assert finish['original_failures']==9556 and finish['new_requests']==13720
assert finish['resolved']==8846 and finish['remaining_exceptions']==710
plan=read(root/'plan.json')
assert sha(root/'plan.json')=='030843d2f8fb37b9bc783a0a7f24f97886a783766f36ab760d6f919d6046e3bb'
assert sha(root/'prepared/requests.jsonl')==plan['input_sha256']
assert (root/'prepared/requests.jsonl').stat().st_size==plan['input_bytes']
index=root/'sessions'/session/'retry_index.jsonl'
assert sha(index)==finish['retry_index_sha256']=='15dd0686149cad320065cf63613d51dcaf090edc9914835c44d89be6d8422bdb'
items=[]
for child in sorted(root.iterdir()):
 if child.name=='prepared':continue
 for p in ([child] if not child.is_dir() else sorted(child.rglob('*'))):
  st=p.lstat()
  assert not stat.S_ISLNK(st.st_mode),p
  if stat.S_ISDIR(st.st_mode):continue
  assert stat.S_ISREG(st.st_mode),p
  items.append(p)
class DigestReader:
 def __init__(self,f):self.f=f;self.h=hashlib.sha256()
 def read(self,n=-1):
  b=self.f.read(n);self.h.update(b);return b
meta={'protocol':'retry3-readonly-collection-v1','root':str(root),'session':session,
 'collected_at':datetime.now(timezone.utc).isoformat(),'exit':finish,
 'omitted_prepared':{'path':'prepared/requests.jsonl','sha256':plan['input_sha256'],'bytes':plan['input_bytes']},
 'files':[]}
with gzip.GzipFile(fileobj=sys.stdout.buffer,mode='wb',compresslevel=1,mtime=0) as gz:
 with tarfile.open(fileobj=gz,mode='w|',format=tarfile.PAX_FORMAT) as archive:
  for p in items:
   before=p.stat(); name=p.relative_to(root).as_posix()
   member=tarfile.TarInfo(name);member.size=before.st_size;member.mode=0o600;member.mtime=before.st_mtime
   with p.open('rb') as f:
    wrapped=DigestReader(f);archive.addfile(member,wrapped)
   after=p.stat();assert (before.st_size,before.st_mtime_ns,before.st_ino)==(after.st_size,after.st_mtime_ns,after.st_ino),name
   meta['files'].append({'path':name,'bytes':before.st_size,'sha256':wrapped.h.hexdigest()})
  assert read(finish_path)==finish and not live(finish['identity'])
  raw=json.dumps(meta,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()+b'\n'
  member=tarfile.TarInfo('__collection_manifest__.json');member.size=len(raw);member.mode=0o600
  archive.addfile(member,io.BytesIO(raw))
'''

def now(): return datetime.now(timezone.utc).isoformat()
def sha(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for raw in iter(lambda:f.read(1024*1024),b''):h.update(raw)
    return h.hexdigest()
def save(path,data):
    with path.open('x') as f:json.dump(data,f,ensure_ascii=False,sort_keys=True,indent=2);f.write('\n')
def fetch():
    ROOT.mkdir(parents=True,exist_ok=True,mode=0o700)
    assert not (ROOT/'download_intent.json').exists(), 'Collection already dispatched; inspect before any recovery'
    assert not (ROOT/'retry_evidence.tar.gz').exists()
    save(ROOT/'download_intent.json',{'at':now(),'collector_pid':os.getpid(),'session':SESSION,'readonly':True})
    part=ROOT/'retry_evidence.tar.gz.part'
    with part.open('xb') as out, (ROOT/'ssh.stderr.txt').open('xb') as err:
        p=subprocess.Popen(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=20','-o','ServerAliveInterval=20',
                            '-o','ServerAliveCountMax=3','Group3_slogin','python3 -B -'],stdin=subprocess.PIPE,stdout=out,stderr=err)
        save(ROOT/'ssh_launch.json',{'at':now(),'ssh_pid':p.pid,'collector_pid':os.getpid()})
        p.communicate(REMOTE.encode())
        out.flush();os.fsync(out.fileno())
    record={'at':now(),'ssh_returncode':p.returncode,'bytes':part.stat().st_size,'sha256':sha(part),'ssh_finished':True}
    save(ROOT/'download_exit.json',record)
    assert p.returncode==0, 'SSH did not complete successfully; retain partial evidence'
    part.rename(ROOT/'retry_evidence.tar.gz')
    print(json.dumps(record))
def valid_member(member):
    p=PurePosixPath(member.name)
    assert member.isfile() and not p.is_absolute() and '..' not in p.parts and p.parts,member.name
    assert p.as_posix()==member.name and '\\' not in member.name and '\x00' not in member.name,member.name
    return p
def audit():
    result=ROOT/'collection_audit.json'
    assert not result.exists(), 'Already audited; do not overwrite evidence'
    receipt=json.loads((ROOT/'download_exit.json').read_bytes());archive=ROOT/'retry_evidence.tar.gz'
    assert receipt['ssh_finished'] and receipt['ssh_returncode']==0
    assert archive.stat().st_size==receipt['bytes'] and sha(archive)==receipt['sha256']
    observed={};meta=None
    with tarfile.open(archive,'r|gz') as tf:
        for m in tf:
            valid_member(m);assert m.name not in observed,m.name
            source=tf.extractfile(m);h=hashlib.sha256();size=0
            if m.name==MANIFEST:
                assert meta is None
                raw=source.read();meta=json.loads(raw);size=len(raw);h.update(raw)
            else:
                for raw in iter(lambda:source.read(1024*1024),b''):size+=len(raw);h.update(raw)
            assert size==m.size
            observed[m.name]={'path':m.name,'bytes':size,'sha256':h.hexdigest()}
    assert meta is not None and meta['session']==SESSION
    expected={r['path']:r for r in meta['files']};assert len(expected)==len(meta['files'])
    assert set(observed)==set(expected)|{MANIFEST}
    assert all(observed[k]==v for k,v in expected.items())
    assert expected[f'sessions/{SESSION}/retry_index.jsonl']['sha256']==EXPECTED_INDEX
    assert meta['exit']['state']=='completed' and meta['exit']['resolved']==8846 and meta['exit']['remaining_exceptions']==710
    snapshot=ROOT/'snapshot';building=ROOT/'snapshot.building'
    assert not snapshot.exists() and not building.exists(), 'Existing extraction must be diagnosed; no overwrite'
    building.mkdir(mode=0o700)
    with tarfile.open(archive,'r|gz') as tf:
        for m in tf:
            parts=valid_member(m);target=building.joinpath(*parts.parts)
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            with target.open('xb') as f:shutil.copyfileobj(tf.extractfile(m),f,1024*1024)
            target.chmod(0o600)
    for name,e in observed.items():
        p=building/name;assert p.stat().st_size==e['bytes'] and sha(p)==e['sha256']
    building.rename(snapshot)
    report={'at':now(),'archive_sha256':receipt['sha256'],'archive_bytes':receipt['bytes'],
            'snapshot':str(snapshot),'file_count':len(expected),'total_raw_bytes':sum(r['bytes'] for r in expected.values()),
            'all_member_hashes_verified':True,'retry_index_sha256':EXPECTED_INDEX,'exit':meta['exit'],
            'omitted_prepared':meta['omitted_prepared'],'no_inference_or_service_changes':True}
    save(result,report);print(json.dumps(report,ensure_ascii=False))
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('mode',choices=['fetch','audit']);a=p.parse_args()
    (fetch if a.mode=='fetch' else audit)()
