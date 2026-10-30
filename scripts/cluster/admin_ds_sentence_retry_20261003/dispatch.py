"""Single-use deployment for the independent 128-pair sentence-ID pilot."""
from __future__ import annotations
import argparse
import base64
import gzip
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
import subprocess
import importlib.util

BASE = Path(__file__).resolve().parent
REPO = BASE.parents[2]
LOCAL = REPO / 'data/pkulaw_admin_ds_sentence_retry_20261003'
REMOTE_ROOT = '/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_sentence_retry_20261003'
REMOTE = r'''
import ast,base64,datetime,fcntl,gzip,hashlib,json,os,pathlib,subprocess,sys
r=json.load(sys.stdin);os.umask(0o077)
root=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_sentence_retry_20261003')
old=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_benchmark_20261003_c128')
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
 m=read(root/'prepared/manifest.json');stage=read(root/'stage_manifest.json')
 if not m or not stage or sha(root/'prepared/requests.jsonl')!=m['input_sha256']:raise RuntimeError('Staged input changed')
 if sha(check(root/'baseline_paired.json'))!=stage['baseline_sha256']:raise RuntimeError('Paired baseline changed')
 for name,digest in stage['files'].items():
  if sha(check(root/name))!=digest:raise RuntimeError('Staged file changed')
 for name,digest in m['code_sha256'].items():
  if sha(check(root/'code'/name))!=digest:raise RuntimeError('Code changed')
 return m
def identity(pid):
 try:
  p=pathlib.Path('/proc')/str(pid);raw=(p/'stat').read_text();fields=raw[raw.rfind(')')+2:].split()
  args=(p/'cmdline').read_bytes().split(b'\0')
  return {'pid':pid,'start_ticks':fields[19],'state':fields[0], 'correct_runner':os.fsencode(root/'code/runner.py') in args}
 except (OSError,IndexError):return None
if r['mode']=='stage':
 files={name:gzip.decompress(base64.b64decode(data,validate=True)) for name,data in r['files_gzip'].items()}
 expected={'code/runner.py','code/bench_external.py','code/bench.py','code/contract.py','code/sentence_contract.py','prepared/requests.jsonl','prepared/manifest.json'}
 if set(files)!=expected:raise RuntimeError('Unexpected file set')
 for name,data in files.items():
  if name.endswith('.py'):ast.parse(data.decode())
 m=json.loads(files['prepared/manifest.json'])
 if m['request_count']!=128 or m['previous_run_root']!=str(old):raise RuntimeError('Unexpected manifest')
 hashes={name:hashlib.sha256(data).hexdigest() for name,data in files.items()}
 if hashes['prepared/requests.jsonl']!=m['input_sha256']:raise RuntimeError('Input hash mismatch')
 for name,digest in m['code_sha256'].items():
  if hashes['code/'+name]!=digest:raise RuntimeError('Code hash mismatch')
 if root.exists():
  verify()
  if read(root/'stage_manifest.json')['files']!=hashes:raise RuntimeError('Do not overwrite a frozen stage')
  print(json.dumps({'status':'already_staged','root':str(root)}));sys.exit(0)
 # Snapshot paired old receipts without changing them. Never reuse nonterminal work.
 old_exit=read(old/'results/exit.json')
 if not old_exit or not old_exit.get('completed') or not old_exit.get('drained'):raise RuntimeError('Old benchmark not yet drained')
 ids={json.loads(line)['request_id'] for line in files['prepared/requests.jsonl'].decode().splitlines()}
 if len(ids)!=128:raise RuntimeError('Wrong pair count')
 old_source=old.parent/'dvjustice_v4flash_autotune_20261002/prepared/requests.jsonl'
 if sha(check(old_source))!=m['source_requests_sha256']:raise RuntimeError('Old paired input changed')
 positions={row['request_id']:i for i,row in enumerate(map(json.loads,old_source.read_text().splitlines()))}
 if not ids<=set(positions):raise RuntimeError('Missing paired source')
 baseline={'captured_at':now(),'old_root':str(old),'exit':old_exit,'round':read(old/'results/round.json'),'endpoints':{}}
 for host in ('10.33.4.146','10.33.4.152'):
  found={}
  for rid in sorted(ids):
   path=old/'results'/host/'requests'/f"{positions[rid]:06d}_{hashlib.sha256(rid.encode()).hexdigest()[:16]}"/'terminal.json'
   terminal=read(path)
   if not terminal or terminal['request_id']!=rid:raise RuntimeError('Missing/mismatched old receipt')
   raw=path.parent/'response.raw.txt'
   found[rid]={'terminal':terminal,'terminal_sha256':sha(path), 'response':read(raw) if raw.exists() else None,
               'response_sha256':sha(raw) if raw.exists() else None,'remote_terminal_path':str(path)}
  if set(found)!=ids:raise RuntimeError('Incomplete old paired receipts')
  baseline['endpoints'][host]=found
 check(root).mkdir(mode=0o700);check(root/'code').mkdir(mode=0o700);check(root/'prepared').mkdir(mode=0o700)
 for name,data in files.items():
  with check(root/name).open('xb') as f:f.write(data);f.flush();os.fsync(f.fileno())
 write_new(root/'baseline_paired.json',baseline)
 write_new(root/'stage_manifest.json',{'at':now(),'files':hashes,'baseline_sha256':sha(root/'baseline_paired.json')})
 print(json.dumps({'status':'staged','root':str(root),'input_sha256':m['input_sha256'],'paired_per_endpoint':128}));sys.exit(0)
if r['mode']=='start':
 verify()
 fd=os.open(check(root/'dispatch.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600);fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
 try:
  if (root/'launch_intent.json').exists():
   print(json.dumps({'action':'existing_intent_no_duplicate','receipt':read(root/'launch_receipt.json')}));sys.exit(0)
  if (root/'results').exists():raise RuntimeError('Output exists; no blind resume')
  command=['/usr/bin/python3','-B',str(root/'code/runner.py'),'run','--requests',str(root/'prepared/requests.jsonl'),
           '--manifest',str(root/'prepared/manifest.json'),'--output',str(root/'results')]
  write_new(root/'launch_intent.json',{'at':now(),'command':command,'no_automatic_retries':True})
  with check(root/'runner.log').open('x') as log:
   p=subprocess.Popen(command,cwd=root,stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,
       close_fds=True,env=dict(os.environ,PYTHONDONTWRITEBYTECODE='1',PYTHONNOUSERSITE='1'))
  receipt={'at':now(),'pid':p.pid,'identity':identity(p.pid)};write_new(root/'launch_receipt.json',receipt)
  print(json.dumps({'action':'dispatched_not_yet_verified','receipt':receipt}));sys.exit(0)
 finally:os.close(fd)
if r['mode']=='status':
 verify();receipt=read(root/'launch_receipt.json');live=identity(receipt['pid']) if receipt else None
 matches=bool(receipt and receipt.get('identity') and live and live.get('correct_runner') and live['state']!='Z' and
              live['start_ticks']==receipt['identity']['start_ticks'])
 result={'at':now(),'receipt':receipt,'live_identity':live,'identity_matches':matches}
 if (root/'results').exists():
  p=subprocess.run(['/usr/bin/python3','-B',str(root/'code/runner.py'),'status','--output',str(root/'results')],capture_output=True,text=True,timeout=25)
  result['status_exit_code']=p.returncode
  try:result['benchmark']=json.loads(p.stdout)
  except ValueError:result['stdout_tail']=p.stdout[-1000:];result['stderr_tail']=p.stderr[-1000:]
 if not matches and (root/'runner.log').exists():
  with (root/'runner.log').open('rb') as f:f.seek(max(0,os.fstat(f.fileno()).st_size-2500));result['log_tail']=f.read().decode(errors='replace')
 print(json.dumps(result));sys.exit(0)
raise RuntimeError('Unsupported mode')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['stage', 'start', 'status', 'fetch'])
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    LOCAL.mkdir(exist_ok=True, parents=True)
    if args.mode == 'fetch':
        destination = LOCAL / 'fetched' / stamp
        destination.mkdir(parents=True, exist_ok=False)
        p = subprocess.run(['rsync', '-az', '--partial', '-e', 'ssh -o BatchMode=yes -o ConnectTimeout=25',
                            'Group3_slogin:' + REMOTE_ROOT + '/', str(destination) + '/'])
        print(json.dumps({'exit_code': p.returncode, 'output': str(destination)}))
        raise SystemExit(p.returncode)
    payload = {'mode': args.mode}
    if args.mode == 'stage':
        spec = importlib.util.spec_from_file_location('prepare_sentence_pilot', BASE / 'prepare.py')
        preparation = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(preparation)
        files = {'code/' + name: path for name, path in preparation.CODE.items()}
        files.update({'prepared/' + name: LOCAL / 'prepared' / name for name in ('requests.jsonl', 'manifest.json')})
        payload['files_gzip'] = {name: base64.b64encode(gzip.compress(path.read_bytes(), mtime=0)).decode() for name, path in files.items()}
    try:
        p = subprocess.run(['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=25', 'Group3_slogin',
                            'python3 -B -c ' + shlex.quote(REMOTE)], input=json.dumps(payload), capture_output=True, text=True, timeout=150)
        record = {'checked_at': datetime.now(timezone.utc).isoformat(), 'mode': args.mode, 'exit_code': p.returncode, 'stderr': p.stderr}
        try:record['result'] = json.loads(p.stdout)
        except ValueError:record['stdout'] = p.stdout
    except subprocess.TimeoutExpired:
        record = {'checked_at': datetime.now(timezone.utc).isoformat(), 'mode': args.mode, 'exit_code': 124,
                  'error': 'SSH command timeout; outcome unknown; inspect remote receipts before any retry'}
    checks = LOCAL / 'checks'
    checks.mkdir(exist_ok=True)
    with (checks / (stamp + '_' + args.mode + '.json')).open('x') as f:
        json.dump(record, f, ensure_ascii=False, indent=2)
        f.write('\n')
    print(json.dumps(record, ensure_ascii=False, indent=2))
    raise SystemExit(record['exit_code'])


if __name__ == '__main__':
    main()
