"""Isolated, at-most-three retries of exact original H1-H5 failures.

Frozen original prompts/parameters/parser; one administrator endpoint at 128.
No successful-original reruns, service mutations, scoring or paid requests.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import ExitStack
from datetime import datetime, timezone
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

HERE = Path(__file__).resolve().parent
LOCAL = Path('/Users/chenlong/WorkSpace/huangyidan')
OLD_LOCAL = LOCAL/'data/pkulaw_admin_ds_full_h1h5_20261003'
LOCAL_ROOT = LOCAL/'data/pkulaw_admin_ds_h1h5_retry3_20261006'
ROOT = Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_h1h5_retry3_20261006')
OLD = ROOT.parent/'dvjustice_admin_ds_full_h1h5_20261003'
HOST = '10.33.4.152'
URL = 'http://10.33.4.152:18044/v1'
MAX_RETRIES = 3
EXPECTED = 9556
PARAMETERS = {'model':'deepseek_v4_flash','temperature':0.1,'max_tokens':3000,
              'chat_template_kwargs':{'thinking':False}}
FLAGS = {'provisional':True,'reference_ready':False,'legal_correctness_verified':False,
         'formal_hallucination_statistics_allowed':False,'none_is_verified_negative':False}
RETRYABLE = {'complete_invalid_format','truncated'}
REPORT_DIR = 'reports/collection_v1_20261004T002123021435Z'

def now():return datetime.now(timezone.utc).isoformat()
def stamp():return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,allow_nan=False,separators=(',',':')).encode()
def digest(raw):return hashlib.sha256(raw).hexdigest()
def file_sha(p):
    h=hashlib.sha256()
    with Path(p).open('rb') as f:
        for raw in iter(lambda:f.read(1024*1024),b''):h.update(raw)
    return h.hexdigest()
def read(p):return json.loads(Path(p).read_bytes())
def new_bytes(p,raw):
    p=Path(p);p.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
    if p.exists():
        if p.read_bytes()!=raw:raise RuntimeError('Refuse overwriting evidence: '+str(p))
        return
    with p.open('xb') as f:f.write(raw);f.flush();os.fsync(f.fileno())
def save(p,v):new_bytes(p,canonical(v)+b'\n')
def rows(p):
    with Path(p).open('rb') as f:
        for raw in f:
            if not raw.strip():raise ValueError('Blank frozen row')
            yield raw,json.loads(raw)
def load_base():
    p=HERE/'base_runner.py'
    if not p.exists():p=HERE.parent/'admin_ds_full_h1h5_20261003/runner.py'
    spec=importlib.util.spec_from_file_location('original_full_h1h5',p)
    m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
    assert m.PARAMETERS==PARAMETERS and m.CONCURRENCY==128 and m.pilot.DEADLINE==1800
    return m
def selected_failure(r):
    return (r.get('audit_state') in ('invalid_format','truncated')
      and r.get('terminal_status') in RETRYABLE
      and r.get('observed_raw_parse',{}).get('status')==r['terminal_status']
      and r['observed_raw_parse'].get('http_status')==200
      and r['observed_raw_parse'].get('valid_format') is False)
def can_retry(t,attempt):
    return (attempt<MAX_RETRIES and t.get('status') in RETRYABLE
      and t.get('http_status')==200 and t.get('valid_format') is False
      and t.get('sent_intent') is True and not t.get('service_error') and not t.get('timeout'))
def valid_success(t):
    return t.get('status')=='complete' and t.get('complete') is True and t.get('valid_format') is True

def prepare():
    """Local selection only. Never edits original files or invokes an LLM."""
    base=load_base();manifest=read(OLD_LOCAL/'prepared/manifest.json')
    report=OLD_LOCAL/REPORT_DIR/'provisional_results.jsonl'
    summary=read(OLD_LOCAL/REPORT_DIR/'summary.json')
    assert file_sha(report)==summary['output_files']['provisional_results.jsonl']['sha256']
    selected=[];all_ids=set();states=Counter()
    for _,r in rows(report):
        assert r['request_id'] not in all_ids;all_ids.add(r['request_id']);states[r['audit_state']]+=1
        if not selected_failure(r):continue
        evidence=r['source_evidence']
        # The local full replay already authenticated these files; verify the
        # exact known-failed complete HTTP receipt again, including pilot reuse.
        if r['source']=='pilot_reuse':
            directory=Path(evidence['source_directory']);files=evidence['source_file_sha256']
        else:directory=Path(evidence['directory']);files=evidence['file_sha256']
        for name,h in files.items():assert file_sha(directory/name)==h,(directory,name)
        http=read(directory/'response.http.json')
        assert http['status']==200 and http['body_complete'] is True
        assert file_sha(directory/'response.raw.txt')==r['response_raw_sha256']
        selected.append({k:r[k] for k in ('request_id','global_index','split','case_id','question_id',
            'answer_condition','host','answer_sha256','packet_sha256','audit_state','terminal_status','response_raw_sha256')})
    assert len(all_ids)==55236 and len(selected)==EXPECTED
    assert Counter(r['audit_state'] for r in selected)=={'invalid_format':9331,'truncated':225}
    by_id={r['request_id']:r for r in selected};seen=set();h=hashlib.sha256();selected_bytes=0
    for host,meta in manifest['shards'].items():
        path=OLD_LOCAL/'prepared'/meta['path'];source_hash=hashlib.sha256()
        for raw,r in rows(path):
            source_hash.update(raw)
            if r['request_id'] not in by_id:continue
            item=by_id[r['request_id']];assert r['request_id'] not in seen
            assert item['host']==host
            for f in ('global_index','answer_sha256','packet_sha256'):assert r[f]==item[f]
            base.validate_row(r)
            item['input_row_sha256']=digest(raw);seen.add(r['request_id']);h.update(raw);selected_bytes+=len(raw)
        assert source_hash.hexdigest()==meta['sha256']
    assert seen==set(by_id)
    bundle=LOCAL_ROOT/'bundle';codes={'retry.py':Path(__file__).resolve(),
      **{('base_runner.py' if name=='runner.py' else name):p for name,p in base.code_paths().items()}}
    code_hashes={name:file_sha(p) for name,p in codes.items()}
    for old_name,expected in manifest['code_sha256'].items():
        name='base_runner.py' if old_name=='runner.py' else old_name
        assert code_hashes[name]==expected
    plan={'protocol':'h1h5-known-failures-retry3-20261006','root':str(ROOT),'old_root':str(OLD),
      'endpoint':URL,'count':EXPECTED,'max_new_attempts_per_original_failure':MAX_RETRIES,'concurrency':128,
      'parameters':PARAMETERS,'timeout_s':1800,'selection':selected,
      'input_sha256':h.hexdigest(),'input_bytes':selected_bytes,'input_order':'old manifest shard order; original raw lines unchanged',
      'old_manifest_sha256':file_sha(OLD_LOCAL/'prepared/manifest.json'),'old_shards':manifest['shards'],
      'old_report_sha256':file_sha(report),'original_successes_preserved':45680,'code_sha256':code_hashes,
      'original_failure_counts':{'invalid_format':9331,'truncated':225},
      'selection_rule':'Only original confirmed complete HTTP200 format failures or length truncations; first valid retry wins; no best-of-N',
      'new_nonthinking_rerun_included':False,**FLAGS}
    for name,p in codes.items():new_bytes(bundle/'code'/name,p.read_bytes())
    save(bundle/'plan.json',plan)
    print(json.dumps({'bundle':str(bundle),'count':EXPECTED,'input_bytes':selected_bytes,'plan_sha256':file_sha(bundle/'plan.json')}))

def safe_root():
    assert ROOT.resolve()==ROOT and ROOT.parent.is_dir() and os.getuid()==1083
    for p in (ROOT,ROOT.parent):assert p.stat().st_uid==os.getuid()

def verify_code(plan):
    assert plan['root']==str(ROOT) and plan['old_root']==str(OLD)
    assert plan['endpoint']==URL and plan['count']==EXPECTED and plan['concurrency']==128
    assert plan['max_new_attempts_per_original_failure']==3 and plan['parameters']==PARAMETERS and plan['timeout_s']==1800
    assert plan['original_successes_preserved']==45680 and plan['new_nonthinking_rerun_included'] is False
    for name,h in plan['code_sha256'].items():
        assert '/' not in name and file_sha(HERE/name)==h
    for f,v in FLAGS.items():assert plan[f]==v

def verify_selection(plan):
    selected=plan['selection'];ids={r['request_id'] for r in selected}
    assert len(ids)==len(selected)==EXPECTED
    assert len({r['global_index'] for r in selected})==EXPECTED
    assert len({tuple(r[f] for f in ('split','case_id','question_id','answer_condition')) for r in selected})==EXPECTED
    assert Counter(r['audit_state'] for r in selected)=={'invalid_format':9331,'truncated':225}
    assert all(r['terminal_status'] in RETRYABLE for r in selected)
    return {r['request_id']:r for r in selected}

def old_finished(base):
    latest=read(OLD/'latest_session.json')['session']
    assert latest=='20261003T180722387344Z'
    start=read(OLD/'sessions'/latest/'start.json');end=read(OLD/'sessions'/latest/'exit.json')
    assert not base.is_live(start['identity'])
    assert all(end[k] is True for k in ('corpus_complete','all_inputs_accounted','drained','local_futures_finished'))
    return {'session':latest,'start_sha256':file_sha(OLD/'sessions'/latest/'start.json'),
            'exit_sha256':file_sha(OLD/'sessions'/latest/'exit.json'),'original_client_alive':False}

def stage():
    safe_root();plan=read(ROOT/'plan.json');verify_code(plan);selected=verify_selection(plan)
    base=load_base()
    with base.coordinator(ROOT),base.coordinator(OLD):
        evidence=old_finished(base)
        assert file_sha(OLD/'prepared/manifest.json')==plan['old_manifest_sha256']
        receipt=ROOT/'stage_receipt.json'
        if receipt.exists():
            assert read(receipt)['plan_sha256']==file_sha(ROOT/'plan.json')
            assert file_sha(ROOT/'prepared/requests.jsonl')==plan['input_sha256']
            print(json.dumps({'stage':'already_verified'}));return
        assert not (ROOT/'launch_intent.json').exists() and not (ROOT/'sessions').exists()
        dest=ROOT/'prepared/requests.jsonl';dest.parent.mkdir(mode=0o700,exist_ok=True)
        # Partial stage files are never overwritten or assumed complete.
        assert not dest.exists()
        total_hash=hashlib.sha256();count=0
        with dest.open('xb') as out:
            for host,meta in plan['old_shards'].items():
                source_hash=hashlib.sha256()
                for raw,r in rows(OLD/'prepared'/meta['path']):
                    source_hash.update(raw)
                    if r['request_id'] not in selected:continue
                    wanted=selected[r['request_id']]
                    assert wanted['host']==host and digest(raw)==wanted['input_row_sha256']
                    base.validate_row(r);out.write(raw);total_hash.update(raw);count+=1
                assert source_hash.hexdigest()==meta['sha256']
            out.flush();os.fsync(out.fileno())
        assert count==EXPECTED and total_hash.hexdigest()==plan['input_sha256'] and dest.stat().st_size==plan['input_bytes']
        save(receipt,{'at':now(),'plan_sha256':file_sha(ROOT/'plan.json'),'input_sha256':total_hash.hexdigest(),'count':count,'previous_finished':evidence})
        print(json.dumps({'stage':'verified','count':count,'plan_sha256':file_sha(ROOT/'plan.json')}))

def verify_input(plan,base):
    assert read(ROOT/'stage_receipt.json')['plan_sha256']==file_sha(ROOT/'plan.json')
    selection=verify_selection(plan);seen=set();h=hashlib.sha256()
    for raw,r in rows(ROOT/'prepared/requests.jsonl'):
        h.update(raw);rid=r['request_id'];assert rid in selection and rid not in seen
        assert digest(raw)==selection[rid]['input_row_sha256'];base.validate_row(r);seen.add(rid)
    assert h.hexdigest()==plan['input_sha256'] and len(seen)==EXPECTED

def attempt_dir(phase,row):
    return phase/'requests'/f"{row['global_index']:06d}_{digest(row['request_id'].encode())[:16]}"

def replay_attempt(base,row,directory):
    """Reparse exact HTTP bytes with the frozen contract before deciding retry."""
    terminal=read(directory/'terminal.json');intent=read(directory/'intent.json')
    assert terminal['request_id']==intent['request_id']==row['request_id']
    payload={**PARAMETERS,'messages':row['messages']}
    assert intent['payload']==payload and intent['payload_sha256']==digest(base.canonical(payload))
    assert intent['answer_sha256']==row['answer_sha256'] and intent['packet_sha256']==row['packet_sha256']
    assert terminal.get('sent_intent') is True
    if 'reconcile' in terminal.get('status',''):return terminal
    http=read(directory/'response.http.json');raw=(directory/'response.raw.txt').read_bytes()
    assert http['body_complete'] is True and digest(raw)==http['raw_sha256']
    def parse(text,answer):return base.contract.parse_output(text,answer,row['sentences'])
    observed=base.ext.bench.inspect_response(raw.decode('utf8',errors='replace'),row['answer'],status=http['status'],parser=parse)
    for f in ('status','complete','valid_format','http_status','finish_reason','response_model'):
        assert terminal.get(f)==observed.get(f),(row['request_id'],f)
    if valid_success(terminal):
        content=json.loads(raw)['choices'][0]['message']['content']
        native=parse(content,row['answer']);hydrated=base.contract.hydrate_output(native,row['answer'],row['sentences'])
        assert native==read(directory/'parsed.native.json') and hydrated==read(directory/'hydrated.json')
    return terminal

def write_subset(plan,ids,number):
    if number==1:
        assert len(ids)==EXPECTED
        return 'requests.jsonl',plan['input_sha256']
    relative=f'round_{number}_requests.jsonl';dest=ROOT/'prepared'/relative
    h=hashlib.sha256();count=0
    with dest.open('xb') as out:
        for raw,r in rows(ROOT/'prepared/requests.jsonl'):
            if r['request_id'] in ids:out.write(raw);h.update(raw);count+=1
        out.flush();os.fsync(out.fileno())
    assert count==len(ids)
    return relative,h.hexdigest()

def run(session):
    safe_root();base=load_base();plan=read(ROOT/'plan.json');verify_code(plan)
    assert session.isalnum() and 10<=len(session)<=40
    # Keep raw records truthful about the bounded retry wrapper; HTTP itself
    # still never retries automatically. No original frozen code is edited.
    flags={**base.FLAGS,'bounded_recovery':True,'max_additional_attempts':3}
    base.FLAGS=flags;base.pilot.FLAGS=flags;base.ext.FLAGS=flags
    with base.coordinator(ROOT),base.coordinator(OLD):
        previous=old_finished(base);verify_input(plan,base)
        d=ROOT/'sessions'/session;d.mkdir(parents=True,mode=0o700)
        identity=base.pilot.read_identity(os.getpid());assert identity
        save(d/'start.json',{'at':now(),'identity':identity,'session':session,'plan_sha256':file_sha(ROOT/'plan.json'),'previous_finished':previous,**FLAGS})
        pending={r['request_id'] for r in plan['selection']};best={};history={};gates=[];handlers={};phase_results=[];stop_requested=False
        def halt(number,frame):
            nonlocal stop_requested
            stop_requested=True
            for gate in gates:gate.halt('controller_signal:'+str(number))
        for sig in (signal.SIGTERM,signal.SIGINT):handlers[sig]=signal.signal(sig,halt)
        try:
            for number in range(1,MAX_RETRIES+1):
                if not pending or stop_requested:break
                relative,h=write_subset(plan,pending,number)
                phase=d/f'round_{number}';phase.mkdir(mode=0o700)
                phase_manifest={'shards':{HOST:{'path':relative,'sha256':h,'count':len(pending)}}}
                save(phase/'input_manifest.json',{'round':number,'request_count':len(pending),'input_sha256':h,'input_path':relative,'retry_only_statuses':sorted(RETRYABLE)})
                gate=base.ext.Gate();gates.append(gate)
                if stop_requested:gate.halt('controller_signal_before_round')
                base.write_json(d/'progress.json',{'at':now(),'state':'running','active_round':number,'round_planned':len(pending),'original_failures':EXPECTED,'resolved':len(best),'identity':identity,**FLAGS},replace=True)
                result=base.run_endpoint(ROOT,phase,HOST,phase_manifest,{},gate);phase_results.append(result)
                next_ids=set()
                for _,row in rows(ROOT/'prepared'/relative):
                    rid=row['request_id'];where=attempt_dir(phase,row)
                    if not where.exists():continue
                    if not (where/'terminal.json').exists():
                        terminal={'status':'missing_terminal_needs_reconcile','request_id':rid,'valid_format':False}
                    else:terminal=replay_attempt(base,row,where)
                    history.setdefault(rid,[]).append({'round':number,'directory':str(where),
                      'terminal_sha256':file_sha(where/'terminal.json') if (where/'terminal.json').exists() else None,'terminal':terminal})
                    if valid_success(terminal):
                        assert rid not in best;best[rid]=history[rid][-1]
                    elif can_retry(terminal,number):next_ids.add(rid)
                save(phase/'replay_audit.json',{'at':now(),'round':number,'resolved_so_far':len(best),'eligible_next':len(next_ids),'original_failed':EXPECTED,'raw_replayed':len(history),**FLAGS})
                if (not result['drained'] or not result['local_futures_finished'] or result['in_flight']
                    or result['untouched'] or result['worker_errors'] or result['failure'] or gate.reasons):break
                pending=next_ids
                if pending and number<MAX_RETRIES:time.sleep(10)
            index=[]
            for item in plan['selection']:
                rid=item['request_id'];attempts=history.get(rid,[]);chosen=best.get(rid) or (attempts[-1] if attempts else None)
                terminal=chosen['terminal'] if chosen else {'status':'not_started','valid_format':False}
                index.append({**item,'retry_attempts':len(attempts),'selected_retry_round':chosen['round'] if chosen else None,
                    'selected_directory':chosen['directory'] if chosen else None,'retry_status':terminal['status'],
                    'retry_valid_format':valid_success(terminal),'retry_format_kind':terminal.get('format_kind'),
                    'attempts':[{'round':a['round'],'directory':a['directory'],'terminal_sha256':a['terminal_sha256'],'status':a['terminal']['status']} for a in attempts],**FLAGS})
            new_bytes(d/'retry_index.jsonl',b''.join(canonical(r)+b'\n' for r in index))
            statuses=Counter(r['retry_status'] for r in index)
            all_finished=all(r['retry_valid_format'] or (r['retry_attempts']==3 and r['retry_status'] in RETRYABLE)
                or r['retry_status'] in ('refusal','non_stop') for r in index)
            drained=bool(phase_results) and all(r['drained'] for r in phase_results)
            finished=bool(phase_results) and all(r['local_futures_finished'] for r in phase_results)
            final={'at':now(),'identity':identity,'state':'completed' if all_finished and drained and finished else 'stopped_needs_review',
              'original_failures':EXPECTED,'resolved':len(best),'remaining_exceptions':EXPECTED-len(best),'statuses':dict(statuses),
              'new_requests':sum(r['retry_attempts'] for r in index),'rounds':len(phase_results),'max_new_attempts':3,
              'drained':drained,'local_futures_finished':finished,'all_inputs_have_auditable_terminal':all_finished,
              'merged_structural_candidates':45680+len(best),'retry_index_sha256':file_sha(d/'retry_index.jsonl'),**FLAGS}
            save(d/'exit.json',final);base.write_json(d/'progress.json',final,replace=True)
            print(json.dumps(final,ensure_ascii=False))
        except BaseException as error:
            for gate in gates:gate.halt('controller_exception:'+type(error).__name__)
            save(d/'controller_error.json',{'at':now(),'error_type':type(error).__name__,'message':str(error)[:500],'requires_readonly_reconciliation':True})
            # Never fabricate a drained/success receipt after an unexpected exit.
            raise
        finally:
            for sig,handler in handlers.items():signal.signal(sig,handler)

def live(base,identity):
    return bool(identity and base.is_live(identity))

def start():
    safe_root();base=load_base();plan=read(ROOT/'plan.json');verify_code(plan)
    with base.coordinator(ROOT),base.coordinator(OLD):
        old_finished(base)
        assert read(ROOT/'stage_receipt.json')['plan_sha256']==file_sha(ROOT/'plan.json')
        assert not (ROOT/'launch_intent.json').exists() and not (ROOT/'sessions').exists()
        preflight=ROOT/'launch_preflight';preflight.mkdir(mode=0o700);(preflight/'telemetry').mkdir(mode=0o700)
        gate=base.ext.Gate();sampler=base.CompactSampler(URL,preflight,gate)
        models=base.ext.read_models(URL,preflight,gate);sample=sampler.take()
        assert base.ext.server_idle(sample) and not gate.stop.is_set()
        session=stamp();save(ROOT/'launch_intent.json',{'at':now(),'session':session,'endpoint':URL,'concurrency':128,'max_new_attempts':3,'plan_sha256':file_sha(ROOT/'plan.json'),'preflight':sample})
        log=(ROOT/'controller.log').open('xb')
        child=subprocess.Popen([sys.executable,'-B',str(HERE/'retry.py'),'run','--session',session],
            stdin=subprocess.DEVNULL,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,cwd=ROOT,close_fds=True)
        identity=base.pilot.read_identity(child.pid)
        save(ROOT/'launch_receipt.json',{'at':now(),'pid':child.pid,'identity':identity,'session':session,'dispatch_only':True})
        log.close();print(json.dumps({'launched_pid':child.pid,'identity':identity,'session':session}))

def status():
    safe_root();base=load_base();out={'at':now(),'root':str(ROOT),'endpoint':URL}
    intent=read(ROOT/'launch_intent.json') if (ROOT/'launch_intent.json').exists() else None
    receipt=read(ROOT/'launch_receipt.json') if (ROOT/'launch_receipt.json').exists() else None
    out.update(launch_intent=intent,launch_receipt=receipt)
    out['client_alive']=live(base,receipt.get('identity')) if receipt else False
    if intent:
        d=ROOT/'sessions'/intent['session']
        for name in ('start','progress','exit','controller_error'):
            if (d/(name+'.json')).exists():out[name]=read(d/(name+'.json'))
        out['rounds']={}
        for phase in sorted(d.glob('round_*')):
            if phase.is_dir():out['rounds'][phase.name]={name:read(phase/(name+'.json')) for name in ('progress','exit','replay_audit') if (phase/(name+'.json')).exists()}
    # A missing start may mean input hash validation, not a failed dispatch.
    for path in ('/health','/metrics'):
        status_code,headers,raw=base.ext.http_once(URL[:-3]+path,timeout_s=5)
        out[path]={'http_status':status_code}
        if path=='/metrics':out[path]['metrics']=base.ext.bench.parse_metrics(raw.decode())
    print(json.dumps(out,ensure_ascii=False))

def main():
    os.umask(0o077)
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('mode',choices=['prepare','stage','start','run','status']);parser.add_argument('--session')
    args=parser.parse_args()
    if args.mode=='run':run(args.session)
    else:globals()[args.mode]()

if __name__=='__main__':main()
