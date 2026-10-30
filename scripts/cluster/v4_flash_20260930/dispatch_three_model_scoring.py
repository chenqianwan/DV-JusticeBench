#!/usr/bin/env python3
"""Deploy frozen three-model answers for DS scoring on allocation 402816 only.

Stage uses a framed gzip stream, not a large base64 JSON object. Hashing and
release parsing run on the existing compute allocation. Start with six tasks,
inspect their evidence, and explicitly resume; no answering or XHub calls.
"""
import argparse
import ast
import contextlib
import datetime
import gzip
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import tempfile

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / 'v4_flash_20260927'
LOCAL = HERE.parents[2] / 'data/pkulaw_three_model_scoring_20260930'
ROOT_NAME = 'dvjustice_three_model_scoring_20260930'
DATA_FILES = ('input_scoring.json', 'manifest.json', 'legacy_scoring_bundle.py',
              'excluded_cases.json', 'answer_provenance.jsonl', 'excluded_answers.jsonl',
              'source_release_audit.json')
SCRIPT_FILES = ('run_three_model_scoring.py', 'run_selfscore_batch128_v2.py',
                'run_selfscore_smoke.py', 'run_answer_batch128.py',
                'run_answer_smoke.py', 'benchmark_concurrency.py')
WRAPPER_NAME = 'three_model_scoring_dispatch_wrapper.py'
MAX_FILE_BYTES = 1024 * 1024 * 1024
CHUNK_BYTES = 1024 * 1024

# Reuse the audited dispatch machinery without editing its source or invoking
# its main. Exact adaptation boundaries fail closed if that machinery changes.
_spec = importlib.util.spec_from_file_location(
    'three_model_original_dispatch', OLD / 'dispatch_selfscore_batch128_v2.py')
original = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(original)


def replace_once(text, before, after):
    if text.count(before) != 1:
        raise ValueError('Audited dispatcher adaptation boundary changed')
    return text.replace(before, after, 1)


def stream_digest(stream):
    checksum, size = hashlib.sha256(), 0
    for chunk in iter(lambda: stream.read(CHUNK_BYTES), b''):
        checksum.update(chunk)
        size += len(chunk)
        if size > MAX_FILE_BYTES:
            raise ValueError('Source exceeds 1 GiB per-file bound')
    return size, checksum.hexdigest()


def compress_source(source, destination):
    """Bounded-memory gzip plus fingerprints; source is a binary stream."""
    checksum, size = hashlib.sha256(), 0
    with destination.open('xb') as output:
        with gzip.GzipFile(filename='', mode='wb', fileobj=output,
                           compresslevel=1, mtime=0) as zipped:
            for chunk in iter(lambda: source.read(CHUNK_BYTES), b''):
                size += len(chunk)
                if size > MAX_FILE_BYTES:
                    raise ValueError('Source exceeds 1 GiB per-file bound')
                checksum.update(chunk)
                zipped.write(chunk)
    with destination.open('rb') as stream:
        # Compressed incompressible inputs can be slightly larger than MAX.
        zipped_hash = hashlib.sha256()
        for chunk in iter(lambda: stream.read(CHUNK_BYTES), b''):
            zipped_hash.update(chunk)
    return dict(size=size, sha256=checksum.hexdigest(),
                gzip_size=destination.stat().st_size, gzip_sha256=zipped_hash.hexdigest())


@contextlib.contextmanager
def stage_stream(local=LOCAL):
    """Spool compressed files locally, then stream one bounded framed upload."""
    with tempfile.TemporaryDirectory(prefix='three-model-scoring-stage-') as temporary:
        directory = Path(temporary)
        paths = {name: local / name for name in DATA_FILES}
        paths.update({name: (HERE if name == 'run_three_model_scoring.py' else OLD) / name
                      for name in SCRIPT_FILES})
        files, compressed = {}, {}
        for name in (*DATA_FILES, *SCRIPT_FILES, WRAPPER_NAME):
            if name == WRAPPER_NAME:
                source = io.BytesIO(WRAPPER.encode())
            else:
                path = paths[name]
                if path.is_symlink() or not path.is_file():
                    raise ValueError('Unsafe/missing source file: ' + name)
                if path.stat().st_size > MAX_FILE_BYTES:
                    raise ValueError('Source exceeds 1 GiB per-file bound')
                if name.endswith('.py'):
                    ast.parse(path.read_text())
                source = path.open('rb')
            destination = directory / (name + '.gz')
            with source:
                files[name] = compress_source(source, destination)
            compressed[name] = destination
        # Temporary binary spool keeps subprocess stdin streaming and lets SSH
        # stdout/stderr be drained by subprocess.run without pipe deadlocks.
        with tempfile.TemporaryFile(mode='w+b', dir=directory) as upload:
            upload.write((json.dumps({'mode': 'stage', 'files': files},
                                     separators=(',', ':')) + '\n').encode())
            for name in files:
                with compressed[name].open('rb') as source:
                    shutil.copyfileobj(source, upload, CHUNK_BYTES)
            upload.seek(0)
            yield upload


WRAPPER = original.WRAPPER.replace('dvjustice_selfscore_batch128_v2_20260928', ROOT_NAME)
WRAPPER = WRAPPER.replace('fixed answer-only batch', 'frozen three-model scoring batch')
WRAPPER = WRAPPER.replace("R/'run_selfscore_batch128_v2.py'", "R/'run_three_model_scoring.py'")
WRAPPER = WRAPPER.replace('import argparse,datetime,', 'import argparse,contextlib,fcntl,datetime,')
WRAPPER = replace_once(WRAPPER, '    try:\n        child=subprocess.Popen', '''    try:
        # Hold the old experiment locks throughout this run. Existing runners
        # use the same flock files, closing the preflight-to-launch race.
        locks=contextlib.ExitStack()
        for name in ['dvjustice_answer_batch128_20260928',
                     'dvjustice_selfscore_batch128_v2_20260928',
                     'dvjustice_thinking_pipeline_20260929']:
            p=R.parent/name/'results'/'.lock'
            assert p.resolve()==p and p.stat().st_uid==1083
            f=locks.enter_context(p.open('r+'))
            fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        child=subprocess.Popen''')
WRAPPER = replace_once(WRAPPER, '        code=child.wait()', '        code=child.wait()\n        locks.close()')

NODE = r'''
import contextlib,fcntl,hashlib,json,os,pathlib,re,socket,stat,sys,urllib.request
r=json.load(sys.stdin)
R=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_three_model_scoring_20260930')
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
if r['action']=='sources':
    manifest=read(R/'stage_manifest.json')
    for name,item in manifest['files'].items():
        assert pathlib.PurePosixPath(name).name==name
        p=R/name
        assert p.resolve()==p and p.stat().st_uid==1083 and stat.S_ISREG(p.lstat().st_mode)
        h=hashlib.sha256();size=0
        with p.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk);size+=len(chunk)
        assert size==item['size'] and h.hexdigest()==item['sha256'],'Staged source changed: '+name
    result=manifest
elif r['action']=='preflight':
    assert not any(result['known_processes'].values()),'Previous dispatch still running'
    prior=['dvjustice_answer_batch128_20260928','dvjustice_selfscore_batch128_v2_20260928',
           'dvjustice_thinking_pipeline_20260929']
    result['previous_queues']={}
    for name in prior:
        previous=R.parent/name
        intents=sorted((previous/'dispatches').glob('attempt-*.intent.json'))
        assert intents,'Missing previous queue dispatch evidence: '+name
        ident=read(intents[-1])['dispatch_id']
        assert re.fullmatch(r'attempt-[0-9]{3}',ident)
        exit_record=read(previous/'dispatches'/(ident+'.exit.json'))
        execution=read(previous/'dispatches'/(ident+'.execution.json'))
        wrapper=read(previous/'dispatches'/(ident+'.wrapper.json'))
        assert exit_record['exit_code']==0,'Previous queue did not exit cleanly: '+name
        assert not alive(execution.get('runner_identity')),'Previous queue runner alive: '+name
        assert not alive(wrapper.get('wrapper_identity')),'Previous queue wrapper alive: '+name
        summary=read(previous/'results'/'summary.json')
        assert summary['pending']==summary['inflight']==summary['needs_reconcile']==summary['unmatched_intents']==0,'Previous queue unresolved: '+name
        result['previous_queues'][name]={'exit_code':0,'pending':0,'processes_alive':False}
    endpoint=read(S/'endpoint.json')
    assert endpoint['job_id']=='402816' and endpoint['status']=='ready' and endpoint['max_num_seqs']>=128
    assert endpoint['base_url']=='http://dgx-040:18044/v1' and endpoint['model']=='deepseek_v4_flash'
    roots=[R/'results']+[R.parent/name/'results' for name in prior]
    roots += [R.parent/name/'results' for name in ['dvjustice_answering_smoke_20260927',
        'dvjustice_answering_smoke_20260927_wait900','dvjustice_selfscore_batch128_20260928',
        'dvjustice_selfscore_smoke_20260927']]
    roots += [R.parent/'dvjustice_concurrency_benchmark_20260927'/'results'/('c%03d'%c) for c in [16,32,64,128]]
    with contextlib.ExitStack() as stack:
        for directory in roots:
            p=directory/'.lock'
            if p.exists():
                assert p.resolve()==p and p.stat().st_uid==1083 and stat.S_ISREG(p.lstat().st_mode)
                f=stack.enter_context(p.open('r+'));fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        keypath=S/'api_key'
        assert keypath.resolve()==keypath and keypath.stat().st_uid==1083 and stat.S_ISREG(keypath.lstat().st_mode) and not keypath.stat().st_mode&0o077
        key=keypath.read_text().strip();assert len(key)>=32
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        request=urllib.request.Request('http://127.0.0.1:18044/health',headers={'Authorization':'Bearer '+key})
        with opener.open(request,timeout=5) as response:assert response.status==200
        request=urllib.request.Request('http://127.0.0.1:18044/v1/models',headers={'Authorization':'Bearer '+key})
        with opener.open(request,timeout=5) as response:models=json.load(response)
        assert any(m.get('id')=='deepseek_v4_flash' for m in models.get('data',[])),'Scoring model is unavailable'
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

STAGE_HELPERS = r'''
def stream_hash(p):
    p=safe(p);assert stat.S_ISREG(p.lstat().st_mode)
    h=hashlib.sha256();size=0
    with p.open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk);size+=len(chunk)
    return size,h.hexdigest()
def receive_file(name,item):
    assert pathlib.PurePosixPath(name).name==name
    assert set(item)=={'size','sha256','gzip_size','gzip_sha256'}
    assert type(item['size']) is int and 0<=item['size']<=MAX
    assert type(item['gzip_size']) is int and 0<item['gzip_size']<=MAX+2*1024*1024
    assert all(re.fullmatch('[0-9a-f]{64}',item[k]) for k in ['sha256','gzip_sha256'])
    p=safe(R/name);temporary=safe(R/('.'+name+'.'+uuid.uuid4().hex))
    fd=os.open(temporary,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600)
    obj=zlib.decompressobj(16+zlib.MAX_WBITS);rawhash=hashlib.sha256();ziphash=hashlib.sha256();size=0
    try:
        with os.fdopen(fd,'wb') as f:
            remaining=item['gzip_size']
            while remaining:
                chunk=sys.stdin.buffer.read(min(1024*1024,remaining))
                assert chunk,'Truncated gzip upload'
                remaining-=len(chunk);ziphash.update(chunk)
                while chunk:
                    data=obj.decompress(chunk,1024*1024)
                    chunk=obj.unconsumed_tail
                    assert not obj.unused_data,'Trailing gzip data'
                    size+=len(data);assert size<=item['size'],'Expanded upload exceeds declared size'
                    rawhash.update(data);f.write(data)
            assert obj.eof and not obj.unused_data and not obj.unconsumed_tail,'Incomplete gzip upload'
            assert size==item['size'] and rawhash.hexdigest()==item['sha256'],'Uncompressed hash/size mismatch'
            assert ziphash.hexdigest()==item['gzip_sha256'],'Compressed hash mismatch'
            f.flush();os.fsync(f.fileno())
        if name.endswith('.py'):ast.parse(temporary.read_text())
        if p.exists():
            assert stream_hash(p)==(size,item['sha256']),'Frozen file differs: '+name
        else:os.link(temporary,p)
        fd=os.open(R,os.O_RDONLY|os.O_DIRECTORY)
        try:os.fsync(fd)
        finally:os.close(fd)
    finally:
        if temporary.exists():os.unlink(temporary)
    return {'size':size,'sha256':item['sha256']}
'''

STAGE = r'''
if mode=='stage':
    assert os.environ.get('SLURM_JOB_ID')=='402816' and socket.gethostname().split('.')[0]=='dgx-040','Stage only on existing compute allocation'
    names={'input_scoring.json','manifest.json','legacy_scoring_bundle.py','excluded_cases.json',
           'answer_provenance.jsonl','excluded_answers.jsonl','source_release_audit.json',
           'run_three_model_scoring.py','run_selfscore_batch128_v2.py','run_selfscore_smoke.py',
           'run_answer_batch128.py','run_answer_smoke.py','benchmark_concurrency.py',
           'three_model_scoring_dispatch_wrapper.py'}
    assert set(r)=={'mode','files'} and set(r['files'])==names
    safe(R).mkdir(mode=0o700,exist_ok=True)
    assert not R.stat().st_mode&0o077,'Run directory permissions too broad'
    fd=os.open(safe(R/'staging.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        files={name:receive_file(name,item) for name,item in r['files'].items()}
        assert not sys.stdin.buffer.read(1),'Unexpected trailing upload bytes'
        manifest=read(R/'manifest.json')
        assert manifest['network_tasks']==33047 and manifest['excluded_answer_count']==238 and manifest['source_answer_count']==33285
        for name,item in manifest['files'].items():
            assert name in files and item['sha256']==files[name]['sha256'] and item['bytes']==files[name]['size']
        sys.path.insert(0,str(R))
        import run_three_model_scoring as scoring
        scoring_bundle,_=scoring.load_bundle(R)
        release_validation=scoring.verify_release(R,(R/'input_scoring.json').read_bytes(),scoring_bundle)
        assert release_validation['network_tasks']==33047 and release_validation['excluded_answer_count']==238
        audit={'files':files,'new_network_questions':33047,'reused_scores':0,'combined_scores':33047,
               'offline_release_validation':release_validation,'permanently_excluded_cases':18,
               'stage_scope':'Score existing three-model successful answers only; unchanged legacy rubric; existing allocation only'}
        write_once(R/'stage_manifest.json',encoded(audit));checked_sources()
        print(json.dumps({'status':'staged','root':str(R),'file_count':len(files),
                          'uncompressed_bytes':sum(x['size'] for x in files.values()),'input_sha256':files['input_scoring.json']['sha256']}))
'''

REMOTE = original.REMOTE.replace('dvjustice_selfscore_batch128_v2_20260928', ROOT_NAME)
REMOTE = replace_once(REMOTE, "r=json.load(sys.stdin);mode=r['mode']",
                      "header=sys.stdin.buffer.readline(65537)\nassert len(header)<=65536 and header.endswith(b'\\n'),'Invalid request header'\nr=json.loads(header);mode=r['mode']")
REMOTE = replace_once(REMOTE, 'MAX=256*1024*1024', 'MAX=1024*1024*1024')
node_start = REMOTE.index("NODE=r'''")
node_end = REMOTE.index("\ndef node(action,identities):", node_start)
REMOTE = REMOTE[:node_start] + 'NODE=' + repr(NODE) + '\n' + REMOTE[node_end:]
check_start = REMOTE.index('def checked_sources():')
check_end = REMOTE.index('\ndef job():', check_start)
REMOTE = REMOTE[:check_start] + '''def checked_sources():
    if socket.gethostname().split('.')[0]!='dgx-040':return node('sources',{})
    assert os.environ.get('SLURM_JOB_ID')=='402816'
    manifest=read(R/'stage_manifest.json')
    for name,item in manifest['files'].items():
        assert pathlib.PurePosixPath(name).name==name
        assert stream_hash(R/name)==(item['size'],item['sha256']),'Staged source changed: '+name
    return manifest
''' + REMOTE[check_end:]
stage_start = REMOTE.index("if mode=='stage':")
stage_end = REMOTE.index("elif mode=='preflight':", stage_start)
REMOTE = REMOTE[:stage_start] + STAGE_HELPERS + '\n' + STAGE.lstrip() + REMOTE[stage_end:]
REMOTE = replace_once(REMOTE, "    if mode=='start':assert maximum is None,'v2 starts pending scores after the two original receipts are audited and reused'",
                      "    if mode=='start':assert maximum==6,'Initial start requires the six-answer acceptance segment'")
REMOTE = REMOTE.replace('maximum<=11095', 'maximum<=33047')
REMOTE = REMOTE.replace("'new_network_questions':11093,'reused_scores':2", "'new_network_questions':33047,'reused_scores':0")
REMOTE = REMOTE.replace("'questions':11093,'reused_scores':2", "'questions':33047,'reused_scores':0")
REMOTE = REMOTE.replace("R/'selfscore_v2_dispatch_wrapper.py'", "R/'three_model_scoring_dispatch_wrapper.py'")
REMOTE = REMOTE.replace('timeout=40)', "timeout=180 if action=='sources' else 60)")
REMOTE = replace_once(REMOTE, "'reused_existing_scores','combined_expected_scores','combined_valid_scores','combined_success','combined_completed'}",
                      "'reused_existing_scores','combined_expected_scores','combined_valid_scores','combined_success','combined_completed',\n                'tasks_by_model_split','completed_by_model_split','success_by_model_split','source_answer_count','excluded_answer_count','session_max_new_tasks'}")


def remote_command(mode):
    if mode in {'stage', 'fetch'}:
        return shlex.join(['env', 'SLURM_CONF=/cm/shared/apps/slurm/var/etc/slurm/slurm.conf',
            'PATH=/usr/bin:/bin', '/cm/shared/apps/slurm/current/bin/srun', '--jobid=402816',
            '--overlap', '--exact', '--nodes=1', '--ntasks=1', '--cpus-per-task=2', '--gres=none',
            '--unbuffered', '--export=ALL', '/usr/bin/python3', '-B', '-c', REMOTE])
    return 'python3 -B -c ' + shlex.quote(REMOTE)


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['stage', 'start', 'resume', 'status', 'fetch', 'preflight'])
    parser.add_argument('--max-new-tasks', type=int, help='Start requires 6; resume defaults to all remaining tasks')
    parser.add_argument('--raw', action='store_true', help='Include raw attempt receipts in fetch')
    args = parser.parse_args()
    if args.raw and args.mode != 'fetch':parser.error('--raw is only valid for fetch')
    if args.max_new_tasks is not None and args.mode not in {'start', 'resume'}:
        parser.error('--max-new-tasks is only valid for start/resume')
    if args.max_new_tasks is not None and not 1 <= args.max_new_tasks <= 33047:
        parser.error('--max-new-tasks must be between 1 and 33047')
    if args.mode == 'start' and args.max_new_tasks != 6:
        parser.error('Initial start requires --max-new-tasks 6 for acceptance')
    for source in (REMOTE, NODE, WRAPPER):ast.parse(source)
    payload = {'mode': args.mode}
    if args.mode in {'start', 'resume'}:payload['max_new_tasks'] = args.max_new_tasks
    if args.mode == 'fetch':payload['raw'] = args.raw
    command = ['ssh', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=15', '-o', 'StrictHostKeyChecking=yes',
               'Group3_slogin', remote_command(args.mode)]
    if args.mode == 'stage':
        with stage_stream() as upload:
            completed = subprocess.run(command, stdin=upload, capture_output=True, timeout=1200)
    else:
        completed = subprocess.run(command, input=(json.dumps(payload) + '\n').encode(),
                                   capture_output=True, timeout=600 if args.mode == 'fetch' else 300)
    record = {'at': datetime.datetime.now(datetime.timezone.utc).isoformat(), 'mode': args.mode,
              'exit_code': completed.returncode, 'stderr': completed.stderr.decode(errors='replace')}
    try:result = json.loads(completed.stdout)
    except json.JSONDecodeError:
        result = {'output_parse_failed': True, 'stdout_bytes': len(completed.stdout)}
    if args.mode == 'fetch' and completed.returncode == 0:
        result = original.save_fetch(result, LOCAL)
    record['result'] = result
    records = LOCAL / 'dispatch_records'
    records.mkdir(parents=True, exist_ok=True, mode=0o700)
    stamp = datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')
    with (records / (args.mode + '_' + stamp + '.json')).open('x') as stream:
        json.dump(record, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print(json.dumps(record, ensure_ascii=False, indent=2))
    raise SystemExit(completed.returncode)


if __name__ == '__main__':main()
