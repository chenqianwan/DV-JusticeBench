"""Stage and launch one finite HTTP-only 128+128 benchmark; no Slurm mutation."""
from __future__ import annotations

import argparse
import base64
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shlex
import subprocess

BASE = Path(__file__).resolve().parent
REPO = BASE.parents[2]
LOCAL = REPO / 'data/pkulaw_admin_ds_benchmark_20261003_c128'
REMOTE_ROOT = '/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_benchmark_20261003_c128'
REMOTE = r'''
import ast,base64,datetime,fcntl,hashlib,json,os,pathlib,subprocess,sys
r=json.load(sys.stdin)
root=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_benchmark_20261003_c128')
source=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_autotune_20261002/prepared/requests.jsonl')
expected_input='5b7ce82cd05936b70605a946b495d0bc6023b7b8eab3ce776f38c20a4cd17506'
os.umask(0o077)
if os.getuid()!=1083:raise RuntimeError('Unexpected account')
def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def check(p):
 if p.resolve()!=p or root.parent not in p.parents:raise RuntimeError('Unexpected path')
 for q in (p,*p.parents):
  if (q==root.parent or root.parent in q.parents) and q.exists() and q.stat().st_uid!=os.getuid():raise RuntimeError('Unexpected owner')
 return p
def write_new(p,value):
 with check(p).open('x') as f:json.dump(value,f,ensure_ascii=False,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
def read(p):return json.loads(check(p).read_text()) if p.exists() else None
def verify():
 m=read(root/'stage_manifest.json')
 if not m or m['input_sha256']!=expected_input or sha(source)!=expected_input:raise RuntimeError('Input/stage mismatch')
 for name,digest in m['files'].items():
  if sha(check(root/'code'/name))!=digest:raise RuntimeError('Staged code changed')
 return m
def identity(pid):
 try:
  p=pathlib.Path('/proc')/str(pid)
  raw=(p/'stat').read_text();fields=raw[raw.rfind(')')+2:].split()
  args=(p/'cmdline').read_bytes().split(b'\0')
  return {'pid':pid,'start_ticks':fields[19],'state':fields[0],
          'correct_runner':os.fsencode(root/'code/bench_external.py') in args}
 except (OSError,IndexError):return None
if r['mode']=='stage':
 proposed={name:base64.b64decode(data,validate=True) for name,data in r['files'].items()}
 if set(proposed)!={'bench_external.py','bench.py','contract.py'}:raise RuntimeError('Unexpected file set')
 for name,data in proposed.items():ast.parse(data.decode())
 hashes={name:hashlib.sha256(data).hexdigest() for name,data in proposed.items()}
 if root.exists():
  m=verify()
  if m['files']!=hashes:raise RuntimeError('Refusing to change existing stage')
  print(json.dumps({'status':'already_staged','manifest':m}));sys.exit(0)
 if source.resolve()!=source or sha(source)!=expected_input:raise RuntimeError('Frozen input changed')
 check(root).mkdir(mode=0o700);check(root/'code').mkdir(mode=0o700)
 for name,data in proposed.items():
  with check(root/'code'/name).open('xb') as f:f.write(data)
 m={'created_at':now(),'files':hashes,'input_path':str(source),'input_sha256':expected_input,
    'client_concurrency_per_endpoint':128,'requests_per_endpoint':1024,
    'endpoints':['http://10.33.4.146:18044/v1','http://10.33.4.152:18044/v1'],
    'benchmark_only':True,'formal_hallucination_statistics_allowed':False,
    'server_configuration_changed':False,'gpu_telemetry_available':False,'model_revision_verified':False}
 write_new(root/'stage_manifest.json',m)
 print(json.dumps({'status':'staged','manifest':m}));sys.exit(0)
if r['mode']=='start':
 verify()
 fd=os.open(check(root/'dispatch.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
 fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:
  if (root/'launch_intent.json').exists():
   print(json.dumps({'action':'existing_intent_no_duplicate','receipt':read(root/'launch_receipt.json')}));sys.exit(0)
  if (root/'results').exists():raise RuntimeError('Output already exists; do not resume blindly')
  command=['/usr/bin/python3','-B',str(root/'code/bench_external.py'),'run','--requests',str(source),'--output',str(root/'results')]
  write_new(root/'launch_intent.json',{'at':now(),'command':command,'automatic_retry_allowed':False})
  with check(root/'runner.log').open('x') as log:
   p=subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,
                      start_new_session=True,close_fds=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1'))
  receipt={'at':now(),'identity':identity(p.pid),'pid':p.pid,'dispatched_not_yet_completed':True}
  write_new(root/'launch_receipt.json',receipt)
  print(json.dumps({'action':'dispatched','receipt':receipt}));sys.exit(0)
 finally:os.close(fd)
if r['mode']=='status':
 verify()
 receipt=read(root/'launch_receipt.json');live=identity(receipt['pid']) if receipt else None
 matches=bool(receipt and receipt.get('identity') and live and live.get('correct_runner') and live['state']!='Z' and
              live['start_ticks']==receipt['identity']['start_ticks'])
 result={'at':now(),'receipt':receipt,'current_identity':live,'identity_matches':matches,'output_present':(root/'results').exists()}
 if result['output_present']:
  p=subprocess.run(['/usr/bin/python3','-B',str(root/'code/bench_external.py'),'status','--output',str(root/'results')],
                   capture_output=True,text=True,timeout=20)
  result['status_exit_code']=p.returncode
  try:result['benchmark']=json.loads(p.stdout)
  except ValueError:result['status_output']=p.stdout[-2000:];result['status_stderr']=p.stderr[-1000:]
 if not matches and (root/'runner.log').exists():
  with (root/'runner.log').open('rb') as f:
   f.seek(max(0,os.fstat(f.fileno()).st_size-3000));result['runner_log_tail']=f.read().decode(errors='replace')
 print(json.dumps(result));sys.exit(0)
raise RuntimeError('Unsupported mode')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['stage', 'start', 'status', 'fetch'])
    args = parser.parse_args()
    LOCAL.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    ssh_options = ['-o', 'BatchMode=yes', '-o', 'ConnectTimeout=25']
    control_path = os.environ.get('DVJUSTICE_BENCH_CONTROL_PATH')
    if control_path:
        ssh_options += ['-S', control_path]
    if args.mode == 'fetch':
        output = LOCAL / 'fetched' / stamp
        output.mkdir(parents=True, exist_ok=False)
        p = subprocess.run(['rsync', '-a', '--partial', '-e', shlex.join(['ssh', *ssh_options]),
                            'Group3_slogin:' + REMOTE_ROOT + '/', str(output) + '/'])
        print(json.dumps({'exit_code': p.returncode, 'output': str(output)}))
        raise SystemExit(p.returncode)
    payload = {'mode': args.mode}
    if args.mode == 'stage':
        mapping = {'bench_external.py': BASE / 'bench_external.py',
                   'bench.py': BASE.parent / 'v4_flash_20261002_autotune/bench.py',
                   'contract.py': REPO / 'scripts/hallucination_h1_h5_20261002/contract.py'}
        payload['files'] = {name: base64.b64encode(path.read_bytes()).decode() for name, path in mapping.items()}
    p = subprocess.run(['ssh', *ssh_options, 'Group3_slogin',
                        'python3 -B -c ' + shlex.quote(REMOTE)], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=100)
    record = {'checked_at': datetime.now(timezone.utc).isoformat(), 'mode': args.mode,
              'exit_code': p.returncode, 'stderr': p.stderr}
    try:
        record['result'] = json.loads(p.stdout)
    except ValueError:
        record['stdout'] = p.stdout
    checks = LOCAL / 'checks'
    checks.mkdir(exist_ok=True)
    with (checks / (stamp + '_' + args.mode + '.json')).open('x') as handle:
        json.dump(record, handle, ensure_ascii=False, indent=2)
        handle.write('\n')
    print(json.dumps(record, ensure_ascii=False, indent=2))
    raise SystemExit(p.returncode)


if __name__ == '__main__':
    main()
