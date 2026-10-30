"""Lossless deduplicated transport; restores byte-identical frozen runtime inputs.

This is a deployment transport, not a new inference protocol or runner version.
"""
from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import tempfile

HERE = Path(__file__).resolve().parent
spec = importlib.util.spec_from_file_location('full_dispatch_transport', HERE / 'dispatch.py')
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
PACKED = base.LOCAL / 'compact_transport_v1'

FINALIZER = r'''
import fcntl,hashlib,importlib.util,json,lzma,os,pathlib,shutil,subprocess,sys
os.umask(0o077)
incoming=pathlib.Path(sys.argv[1]);root=incoming.parent.parent
assert str(root)=='/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_full_h1h5_20261003'
assert os.getuid()==1083 and root.resolve()==root and incoming.resolve()==incoming
def sha(p):
 h=hashlib.sha256()
 with p.open('rb') as f:
  for chunk in iter(lambda:f.read(1048576),b''):h.update(chunk)
 return h.hexdigest()
def safe(p):
 assert p.resolve()==p and root in p.parents
 assert all(not x.exists() or x.stat().st_uid==os.getuid() for x in (p,*[q for q in p.parents if q==root or root in q.parents]))
 return p
def read(p):return json.loads(safe(p).read_bytes())
assert sha(safe(incoming/'bundle_manifest.json'))==sys.argv[2]
bundle=read(incoming/'bundle_manifest.json')
expected={'code/'+n for n in ('runner.py','pilot_runner.py','bench_external.py','bench.py','contract.py','sentence_contract.py','manager.py')}
expected|={'prepared/'+n for n in ('requests_146.jsonl','requests_152.jsonl','manifest.json','private_mapping.jsonl','reused.jsonl')}
assert set(bundle['original_files'])==expected
assert bundle['helper_sha256']==sha(safe(incoming/'transport_compact.py'))
for name,meta in bundle['transport_files'].items():
 assert sha(safe(incoming/name))==meta['transport_sha256']
 assert meta['target'] in (expected-{'prepared/requests_146.jsonl','prepared/requests_152.jsonl'})|{'packed/'+n for n in ('manifest.json','system.txt','contexts.jsonl','compact_146.jsonl','compact_152.jsonl')}
targets=[meta['target'] for meta in bundle['transport_files'].values()]
assert len(targets)==len(set(targets)) and set(targets)==(expected-{'prepared/requests_146.jsonl','prepared/requests_152.jsonl'})|{'packed/'+n for n in ('manifest.json','system.txt','contexts.jsonl','compact_146.jsonl','compact_152.jsonl')}
fd=os.open(safe(root/'stage.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
try:
 if (root/'stage_manifest.json').exists():
  old=read(root/'stage_manifest.json');assert old['files']==bundle['original_files']
  assert all(sha(safe(root/name))==value for name,value in old['files'].items())
  print(json.dumps({'status':'already_staged'}));sys.exit(0)
 assert not (root/'launches').exists() and not (root/'sessions').exists()
 for name,meta in bundle['transport_files'].items():
  target=safe((incoming/'expanded'/meta['target']) if meta['target'].startswith('packed/') else (root/meta['target']))
  target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
  if target.exists():assert sha(target)==meta['raw_sha256'];continue
  temp=safe(target.with_name(target.name+'.staging'))
  if not temp.exists():
   opener=lzma.open if meta['xz'] else open
   with opener(safe(incoming/name),'rb') as src,temp.open('xb') as dst:
    shutil.copyfileobj(src,dst,1048576);dst.flush();os.fsync(dst.fileno())
  assert sha(temp)==meta['raw_sha256'];os.rename(temp,target)
 for name in expected-{'prepared/requests_146.jsonl','prepared/requests_152.jsonl'}:
  assert sha(safe(root/name))==bundle['original_files'][name]
 packed_manifest=read(incoming/'expanded/packed/manifest.json')
 assert packed_manifest['frozen_manifest_sha256']==bundle['original_files']['prepared/manifest.json']
 frozen_manifest=read(root/'prepared/manifest.json')
 assert packed_manifest['request_count']==frozen_manifest['request_count']==55236
 assert set(packed_manifest['shards'])=={'requests_146.jsonl','requests_152.jsonl'}
 for host,meta in frozen_manifest['shards'].items():
  packed_meta=packed_manifest['shards'][meta['path']]
  assert packed_meta['sha256']==meta['sha256'] and packed_meta['count']==meta['count'] and packed_meta['host']==host
 decoded=safe(incoming/'decoded_requests');decoded.mkdir(mode=0o700,exist_ok=True)
 request_names=('requests_146.jsonl','requests_152.jsonl')
 if not all((decoded/(n+'.staging')).exists() and sha(decoded/(n+'.staging'))==bundle['original_files']['prepared/'+n] for n in request_names):
  # An incomplete decoder output is not overwritten; failures require inspection.
  subprocess.run([sys.executable,'-B',str(incoming/'transport_compact.py'),'unpack','--packed',str(incoming/'expanded/packed'),
    '--output',str(decoded),'--suffix','.staging','--contract',str(root/'code/sentence_contract.py')],check=True,stdout=sys.stderr)
 for name in request_names:
  source=safe(decoded/(name+'.staging'));target=safe(root/'prepared'/name)
  assert sha(source)==bundle['original_files']['prepared/'+name]
  if target.exists():assert sha(target)==sha(source)
  else:os.link(source,target)
 assert all(sha(safe(root/name))==value for name,value in bundle['original_files'].items())
 value={'at':__import__('datetime').datetime.now(__import__('datetime').timezone.utc).isoformat(),'files':bundle['original_files'],
  'transport':'lossless_compact_xz','bundle_manifest_sha256':sha(incoming/'bundle_manifest.json'),'helper_sha256':bundle['helper_sha256']}
 with safe(root/'stage_manifest.json').open('x') as f:json.dump(value,f,indent=2);f.write('\n');f.flush();os.fsync(f.fileno())
 print(json.dumps({'status':'staged','root':str(root),'transport':'lossless_compact_xz'}))
finally:os.close(fd)
'''


def make_bundle():
    manifest = json.loads((base.LOCAL / 'prepared/manifest.json').read_text())
    for name, source in base.CODE.items():
        if base.sha(source) != manifest['code_sha256'][name]:
            raise RuntimeError('Frozen runtime changed')
    original = {'code/' + name: source for name, source in base.CODE.items()}
    original['code/manager.py'] = HERE / 'manager.py'
    original.update({'prepared/' + name: base.LOCAL / 'prepared' / name for name in (
        'requests_146.jsonl', 'requests_152.jsonl', 'private_mapping.jsonl', 'reused.jsonl', 'manifest.json')})
    originals = {name: base.sha(source) for name, source in original.items()}
    helper = HERE / 'transport_compact.py'
    marker = hashlib.sha256((base.sha(PACKED / 'manifest.json') + base.sha(helper) +
                             json.dumps(originals, sort_keys=True)).encode()).hexdigest()[:24]
    bundle = base.LOCAL / 'bundles' / ('compact_' + marker)
    if (bundle / 'bundle_manifest.json').exists():
        prior = json.loads((bundle / 'bundle_manifest.json').read_text())
        if prior['original_files'] != originals or prior['helper_sha256'] != base.sha(helper):
            raise RuntimeError('Frozen compact bundle changed')
        for name, meta in prior['transport_files'].items():
            if base.sha(bundle / name) != meta['transport_sha256']:
                raise RuntimeError('Existing compact transport mismatch')
        return bundle
    bundle.mkdir(parents=True, exist_ok=True, mode=0o700)
    sources = {name: source for name, source in original.items() if name not in (
        'prepared/requests_146.jsonl', 'prepared/requests_152.jsonl')}
    sources.update({'packed/' + name: PACKED / name for name in (
        'manifest.json', 'system.txt', 'contexts.jsonl', 'compact_146.jsonl', 'compact_152.jsonl')})
    transport = {}
    packed_compression = json.loads((PACKED / 'compression_manifest.json').read_text())
    for name, source in sources.items():
        compressed = source.stat().st_size > 100000 and not name.endswith('.py')
        destination_name = name + ('.xz' if compressed else '')
        destination = bundle / destination_name
        destination.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if not destination.exists():
            temp = destination.with_name(destination.name + '.building')
            with temp.open('xb') as output:
                if compressed:
                    precompressed = packed_compression.get(source.name) if source.parent == PACKED else None
                    if precompressed:
                        existing = PACKED / precompressed['transport_path']
                        if base.sha(source) != precompressed['sha256'] or base.sha(existing) != precompressed['transport_sha256']:
                            raise RuntimeError('Precompressed artifact mismatch')
                        with existing.open('rb') as inp:
                            shutil.copyfileobj(inp, output, 1024 * 1024)
                    else:
                        subprocess.run(['/opt/homebrew/bin/xz', '-6', '-c', str(source)], stdout=output, check=True)
                else:
                    with source.open('rb') as inp:
                        shutil.copyfileobj(inp, output, 1024 * 1024)
                output.flush()
                os.fsync(output.fileno())
            os.rename(temp, destination)
        transport[destination_name] = {'target': name, 'xz': compressed,
            'raw_sha256': base.sha(source), 'transport_sha256': base.sha(destination),
            'transport_bytes': destination.stat().st_size}
    for source, target in [(helper, bundle / 'transport_compact.py')]:
        if target.exists():
            if base.sha(target) != base.sha(source):
                raise RuntimeError('Existing helper mismatch')
        else:
            with source.open('rb') as inp, target.open('xb') as output:
                shutil.copyfileobj(inp, output)
    with (bundle / 'bundle_manifest.json').open('x') as out:
        json.dump({'original_files': originals, 'transport_files': transport,
                   'helper_sha256': base.sha(helper), 'created_at': datetime.now(timezone.utc).isoformat()}, out, indent=2)
        out.write('\n')
    return bundle


def stage_connected(bundle, socket_path):
    target = base.REMOTE + '/incoming/' + bundle.name
    boot = ('import os,pathlib,json;os.umask(0o077);'
            f'r=pathlib.Path({base.REMOTE!r});p=pathlib.Path({target!r});'
            'assert os.getuid()==1083 and r.resolve()==r and p.resolve()==p;'
            'assert all(not x.exists() or x.stat().st_uid==os.getuid() for x in (r,r/"incoming",p));'
            'p.mkdir(mode=0o700,parents=True,exist_ok=True);print(json.dumps({"incoming":str(p)}))')
    result = base.remote('python3 -B -c ' + shlex.quote(boot), 'compact_bootstrap', 40)
    if result['exit_code']:
        return result
    common = ['rsync', '-a', '--partial', '--partial-dir=.rsync-partial', '-e',
              'ssh -S ' + shlex.quote(str(socket_path)) + ' -o BatchMode=yes -o ConnectTimeout=25']
    # Three bounded file-transfer streams, disjoint destinations, no inference parallelism change.
    commands = [common + ['--exclude=compact_146.jsonl.xz', '--exclude=compact_152.jsonl.xz',
                         str(bundle) + '/', 'Group3_slogin:' + target + '/']]
    mkdir = base.remote('mkdir -p ' + shlex.quote(target + '/packed'), 'compact_packed_dir', 40)
    if mkdir['exit_code']:
        return mkdir
    commands.extend(common + [str(bundle / 'packed' / name), 'Group3_slogin:' + target + '/packed/']
                    for name in ('compact_146.jsonl.xz', 'compact_152.jsonl.xz'))
    with ThreadPoolExecutor(max_workers=3) as pool:
        returncodes = list(pool.map(lambda command: subprocess.run(command).returncode, commands))
    result = base.save_check('compact_upload', {'exit_code': next((x for x in returncodes if x), 0),
                            'stream_exit_codes': returncodes, 'bundle': str(bundle), 'remote': target})
    if result['exit_code']:
        return result
    return base.remote('python3 -B -c ' + shlex.quote(FINALIZER) + ' ' + shlex.quote(target) + ' ' +
                       shlex.quote(base.sha(bundle / 'bundle_manifest.json')),
                       'compact_finalize', 240)


def stage(bundle):
    # A private per-deployment SSH multiplex socket avoids simultaneous login
    # handshakes. It changes no global SSH/VPN configuration and is closed last.
    control_dir = Path(tempfile.mkdtemp(prefix='dvjssh_', dir='/tmp'))
    socket_path = control_dir / 'control'
    command = ['ssh', '-M', '-S', str(socket_path), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=25',
               '-o', 'ControlPersist=120', '-N', '-f', 'Group3_slogin']
    try:
        process = subprocess.run(command, capture_output=True, text=True, timeout=45)
    except subprocess.TimeoutExpired:
        return base.save_check('compact_connection', {'exit_code': 124, 'control_socket': str(socket_path),
                               'error': 'Connection establishment timeout; no upload or inference launched'})
    if process.returncode:
        return base.save_check('compact_connection', {'exit_code': process.returncode,
                               'stderr': process.stderr, 'control_socket': str(socket_path)})
    old_ssh = base.SSH
    base.SSH = ['ssh', '-S', str(socket_path), '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=25', 'Group3_slogin']
    base.save_check('compact_connection', {'exit_code': 0, 'control_socket': str(socket_path),
                    'mode': 'single_authenticated_connection_with_three_disjoint_transfer_channels'})
    try:
        return stage_connected(bundle, socket_path)
    finally:
        base.SSH = old_ssh
        subprocess.run(['ssh', '-S', str(socket_path), '-O', 'exit', 'Group3_slogin'],
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)


if __name__ == '__main__':
    bundle = make_bundle()
    meta = json.loads((bundle / 'bundle_manifest.json').read_text())
    print(json.dumps({'bundle': str(bundle), 'bytes': sum(x['transport_bytes'] for x in meta['transport_files'].values())}), flush=True)
    if '--bundle-only' not in sys.argv:
        result = stage(bundle)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        raise SystemExit(result['exit_code'])
