#!/usr/bin/env python3
"""Read-only, resumable evidence download; never calls a model or changes Slurm.

Inventory hashing streams 1 MiB at a time. Each separate SSH transfer contains
at most 16 MiB of original bytes, including slices of large aggregate JSONL.
Completed parts, receipts, and final files are immutable and SHA256 verified.
"""
import argparse
import base64
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path
import shlex
import subprocess
import tempfile
import uuid
import zlib

LOCAL=Path(__file__).resolve().parents[3]/'data/pkulaw_v4_answer_batch128_20260928/evidence_download'
PIECE=8*1024*1024
BATCH=16*1024*1024
REMOTE=r'''
import base64,contextlib,fcntl,gzip,hashlib,json,os,pathlib,stat,sys
r=json.load(sys.stdin)
R=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_answer_batch128_20260928')
ROOT_FILES={'stage_manifest.json','input_manifest.json','excluded_cases.json','reused_answers.jsonl',
            'run_answer_batch128.py','run_answer_smoke.py','benchmark_concurrency.py','answer_dispatch_wrapper.py'}
assert os.getuid()==1083 and R.resolve()==R and R.stat().st_uid==1083
def safe(name):
    rel=pathlib.PurePosixPath(name)
    assert not rel.is_absolute() and '..' not in rel.parts
    assert (len(rel.parts)==1 and name in ROOT_FILES) or (len(rel.parts)>1 and rel.parts[0] in {'results','dispatches'})
    p=R/name
    assert p.resolve()==p and p.stat().st_uid==1083 and stat.S_ISREG(p.lstat().st_mode)
    return p
def stream_hash(p):
    h=hashlib.sha256()
    with p.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()
def metadata(p):
    s=p.stat();return {'size':s.st_size,'mtime_ns':s.st_mtime_ns}
def assert_finished():
    p=safe('results/summary.json');s=json.loads(p.read_text())
    assert s['completed']==11085 and s['success']==11085 and s['failed']==0 and s['pending']==0
    exits=sorted((R/'dispatches').glob('attempt-*.exit.json'));assert exits
    last=safe(str(exits[-1].relative_to(R)));assert json.loads(last.read_text())['exit_code']==0
    return {'summary_sha256':stream_hash(p),'exit_path':str(last.relative_to(R)),'exit_sha256':stream_hash(last),
            'completed':11085,'success':11085}
if r['mode']=='inventory':
    assert set(r)=={'mode'}
    lock=R/'results'/'.lock';assert lock.resolve()==lock and lock.stat().st_uid==1083
    with lock.open('r+') as f:
        fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
        completed=assert_finished();paths=[]
        for directory in ['results','dispatches']:
            for p in (R/directory).rglob('*'):
                if p.is_file() and p.suffix in {'.json','.jsonl','.log'}:paths.append(str(p.relative_to(R)))
        paths += [n for n in ROOT_FILES if (R/n).exists()]
        files=[]
        for name in sorted(set(paths)):
            p=safe(name);before=metadata(p);digest=stream_hash(p);assert metadata(p)==before
            files.append(dict(path=name,sha256=digest,**before))
    print(json.dumps({'schema':1,'remote_root':str(R),'completed_receipt':completed,'files':files,
                      'file_count':len(files),'total_bytes':sum(x['size'] for x in files),
                      'excluded_from_download':['input_tasks.json (already frozen locally)','lock files']}))
elif r['mode']=='chunks':
    assert set(r)=={'mode','pieces'} and 0<len(r['pieces'])<=128
    assert sum(x['length'] for x in r['pieces'])<=16*1024*1024
    output=[]
    for item in r['pieces']:
        assert set(item)=={'path','size','mtime_ns','sha256','offset','length'}
        assert all(type(item[k]) is int for k in ['size','mtime_ns','offset','length'])
        assert 0<=item['offset']<=item['size'] and 0<=item['length']<=8*1024*1024
        assert item['offset']+item['length']<=item['size']
        p=safe(item['path']);expected={'size':item['size'],'mtime_ns':item['mtime_ns']}
        assert metadata(p)==expected,'Evidence changed since inventory'
        with p.open('rb') as f:f.seek(item['offset']);data=f.read(item['length'])
        assert len(data)==item['length'] and metadata(p)==expected
        compressed=gzip.compress(data,compresslevel=3,mtime=0)
        output.append({'path':item['path'],'offset':item['offset'],'length':len(data),
                       'sha256':hashlib.sha256(data).hexdigest(),'gzip_sha256':hashlib.sha256(compressed).hexdigest(),
                       'gzip_size':len(compressed),'data':base64.b64encode(compressed).decode()})
    print(json.dumps({'pieces':output}))
else:raise RuntimeError('Only read-only inventory/chunks are supported')
'''


def sha(data):return hashlib.sha256(data).hexdigest()


def hash_file(path):
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for data in iter(lambda:stream.read(1024*1024),b''):h.update(data)
    return h.hexdigest()


def dump(value):return (json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()


def safe_local(root,relative):
    rel=Path(relative)
    if rel.is_absolute() or '..' in rel.parts:raise ValueError('Unsafe local path')
    p=root/rel
    if p.resolve()!=p:raise ValueError('Redirected local path')
    return p


def immutable(path,data):
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if path.exists():
        if path.stat().st_size!=len(data) or hash_file(path)!=sha(data):raise ValueError('Immutable local artifact differs: '+str(path))
        return
    temporary=path.parent/('.'+path.name+'.'+uuid.uuid4().hex)
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'wb') as stream:stream.write(data);stream.flush();os.fsync(stream.fileno())
    try:os.link(temporary,path)
    finally:os.unlink(temporary)


def ssh(payload,timeout):
    command=['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes',
             'Group3_slogin','python3 -B -c '+shlex.quote(REMOTE)]
    result=subprocess.run(command,input=json.dumps(payload),text=True,capture_output=True,timeout=timeout)
    if result.returncode:
        raise RuntimeError('Read-only SSH failed (%d): %s'%(result.returncode,result.stderr[-1600:]))
    try:return json.loads(result.stdout)
    except json.JSONDecodeError:raise RuntimeError('Invalid SSH JSON; no receipt committed') from None


def validate_inventory(inventory):
    if inventory['schema']!=1 or inventory['remote_root']!='/aifs4su/hansirui_3rd/chenlong/dvjustice_answer_batch128_20260928':
        raise ValueError('Wrong frozen inventory')
    names=set()
    for item in inventory['files']:
        rel=Path(item['path'])
        if rel.is_absolute() or '..' in rel.parts or item['path'] in names:raise ValueError('Unsafe/duplicate inventory path')
        if type(item['size']) is not int or item['size']<0:raise ValueError('Invalid size')
        if len(item['sha256'])!=64:raise ValueError('Invalid hash')
        names.add(item['path'])
    if len(names)!=inventory['file_count'] or sum(x['size'] for x in inventory['files'])!=inventory['total_bytes']:
        raise ValueError('Inventory totals differ')


def pieces_for(item):
    for offset in range(0,max(1,item['size']),PIECE):
        yield dict(item,offset=offset,length=min(PIECE,item['size']-offset))


def batches(inventory):
    batch=[];size=0
    for item in inventory['files']:
        for piece in pieces_for(item):
            if batch and (size+piece['length']>BATCH or len(batch)>=128):
                yield batch;batch=[];size=0
            batch.append(piece);size+=piece['length']
    if batch:yield batch


def part_path(root,piece):
    return safe_local(root,'.parts/'+sha(piece['path'].encode())+'/%012d.bin'%piece['offset'])


def decode_piece(piece):
    if type(piece['length']) is not int or not 0<=piece['length']<=PIECE:raise ValueError('Invalid piece length')
    compressed=base64.b64decode(piece['data'],validate=True)
    if len(compressed)!=piece['gzip_size'] or sha(compressed)!=piece['gzip_sha256']:raise ValueError('Compressed piece corrupted')
    obj=zlib.decompressobj(16+zlib.MAX_WBITS);data=obj.decompress(compressed,piece['length']+1)
    if not obj.eof or obj.unused_data or obj.unconsumed_tail or len(data)!=piece['length'] or sha(data)!=piece['sha256']:
        raise ValueError('Decompressed piece corrupted')
    return data


def verify_batch(root,batch,receipt,inventory_hash):
    if receipt['inventory_sha256']!=inventory_hash or receipt['requests']!=batch:raise ValueError('Batch receipt identity differs')
    if len(receipt['pieces'])!=len(batch):raise ValueError('Incomplete batch receipt')
    for request,piece in zip(batch,receipt['pieces']):
        if any(piece[k]!=request[k] for k in ['path','offset','length']):raise ValueError('Wrong piece receipt')
        p=part_path(root,request)
        if p.stat().st_size!=piece['length'] or hash_file(p)!=piece['sha256']:raise ValueError('Saved piece corrupted')


def assemble(root,item):
    path=safe_local(root,'files/'+item['path'])
    if path.exists():
        if path.stat().st_size!=item['size'] or hash_file(path)!=item['sha256']:raise ValueError('Completed file differs')
        return
    path.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    temporary=path.parent/('.'+path.name+'.'+uuid.uuid4().hex)
    fd=os.open(temporary,os.O_CREAT|os.O_EXCL|os.O_WRONLY|os.O_NOFOLLOW,0o600)
    digest=hashlib.sha256();size=0
    try:
        with os.fdopen(fd,'wb') as stream:
            for piece in pieces_for(item):
                with part_path(root,piece).open('rb') as source:
                    for data in iter(lambda:source.read(1024*1024),b''):
                        stream.write(data);digest.update(data);size+=len(data)
            stream.flush();os.fsync(stream.fileno())
        if size!=item['size'] or digest.hexdigest()!=item['sha256']:raise ValueError('Full-file hash mismatch: '+item['path'])
        os.link(temporary,path)
    finally:os.unlink(temporary)


def run(args):
    root=args.output.resolve();root.mkdir(parents=True,exist_ok=True,mode=0o700)
    manifest=root/'inventory.json'
    if not manifest.exists():
        if args.mode=='verify':raise ValueError('Inventory is missing')
        inventory=ssh({'mode':'inventory'},args.inventory_timeout);validate_inventory(inventory)
        immutable(manifest,dump(inventory))
    inventory=json.loads(manifest.read_text());validate_inventory(inventory)
    inventory_hash=hash_file(manifest)
    print(json.dumps({'event':'inventory_ready','files':inventory['file_count'],'bytes':inventory['total_bytes'],
                      'inventory_sha256':inventory_hash,'local':str(root)}),flush=True)
    if args.mode=='inventory':return
    if args.mode=='verify':
        for item in inventory['files']:
            path=safe_local(root,'files/'+item['path'])
            if path.stat().st_size!=item['size'] or hash_file(path)!=item['sha256']:raise ValueError('Final-file verification failed')
        print(json.dumps({'event':'verified','files':inventory['file_count'],'bytes':inventory['total_bytes']}),flush=True);return
    new=0
    for index,batch in enumerate(batches(inventory)):
        receipt_path=root/'receipts'/('batch_%06d.json'%index)
        if receipt_path.exists():
            verify_batch(root,batch,json.loads(receipt_path.read_text()),inventory_hash);continue
        if args.max_batches is not None and new>=args.max_batches:
            print(json.dumps({'event':'batch_limit_reached','new_batches':new,'next_batch':index}),flush=True);return
        response=ssh({'mode':'chunks','pieces':batch},args.batch_timeout)
        if len(response['pieces'])!=len(batch):raise ValueError('Response piece count differs')
        records=[]
        for request,piece in zip(batch,response['pieces']):
            if any(piece[k]!=request[k] for k in ['path','offset','length']):raise ValueError('Unexpected response piece')
            data=decode_piece(piece);immutable(part_path(root,request),data)
            records.append({k:piece[k] for k in ['path','offset','length','sha256','gzip_size','gzip_sha256']})
        receipt={'inventory_sha256':inventory_hash,'requests':batch,'pieces':records}
        immutable(receipt_path,dump(receipt));verify_batch(root,batch,receipt,inventory_hash)
        new+=1
        print(json.dumps({'event':'batch_verified','batch':index,'pieces':len(batch),'bytes':sum(p['length'] for p in batch)}),flush=True)
    for item in inventory['files']:assemble(root,item)
    complete={'inventory_sha256':inventory_hash,'files':inventory['file_count'],'bytes':inventory['total_bytes'],
              'whole_file_sha256_verified':True,'remote_writes':False,'model_requests':0}
    immutable(root/'COMPLETE.json',dump(complete))
    print(json.dumps({'event':'download_complete',**complete,'local':str(root/'files')}),flush=True)


def self_test():
    import ast
    compile(REMOTE,'remote','exec')
    original=b'abc'*10000;compressed=gzip.compress(original,mtime=0)
    piece={'path':'results/test.json','offset':0,'length':len(original),'sha256':sha(original),
           'gzip_size':len(compressed),'gzip_sha256':sha(compressed),'data':base64.b64encode(compressed).decode()}
    assert decode_piece(piece)==original
    bad=dict(piece,sha256='0'*64)
    try:decode_piece(bad)
    except ValueError:pass
    else:raise AssertionError('Corruption not rejected')
    item={'path':'results/test.json','size':len(original),'mtime_ns':1,'sha256':sha(original)}
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory).resolve();request=next(pieces_for(item))
        immutable(part_path(root,request),original);assemble(root,item);assemble(root,item)
        assert (root/'files/results/test.json').read_bytes()==original
        try:safe_local(root,'../escape')
        except ValueError:pass
        else:raise AssertionError('Traversal not rejected')
    huge=dict(item,size=3*BATCH)
    plan=list(batches({'files':[huge]}))
    assert len(plan)==3 and all(sum(x['length'] for x in b)<=BATCH for b in plan)
    assert "api_key" not in REMOTE and "'sbatch'" not in REMOTE and 'scancel' not in REMOTE
    print('Offline self-tests passed; no SSH/API calls.')


def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode',choices=['inventory','fetch','verify','self-test'])
    parser.add_argument('--output',type=Path,default=LOCAL)
    parser.add_argument('--inventory-timeout',type=int,default=300)
    parser.add_argument('--batch-timeout',type=int,default=90)
    parser.add_argument('--max-batches',type=int)
    args=parser.parse_args()
    if args.max_batches is not None and args.max_batches<1:parser.error('--max-batches must be positive')
    if args.mode=='self-test':self_test()
    else:run(args)


if __name__=='__main__':main()
