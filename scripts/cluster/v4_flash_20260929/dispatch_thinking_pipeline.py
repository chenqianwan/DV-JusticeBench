#!/usr/bin/env python3
"""Isolated thinking pipeline dispatch inside the existing allocation; no restart."""
import argparse
import ast
import datetime
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys

HERE = Path(__file__).resolve().parent
OLD = HERE.parent / 'v4_flash_20260927'
sys.path.insert(0, str(OLD))
import dispatch_answer_batch128 as original

LOCAL = HERE.parents[2] / 'data/pkulaw_v4_thinking_pipeline_20260929'
DATA_FILES = ('input_tasks.json', 'input_manifest.json', 'task_mapping.jsonl',
              'excluded_cases.json', 'legacy_scoring_bundle.py')
OLD_SCRIPTS = ('run_answer_batch128.py', 'run_answer_smoke.py', 'benchmark_concurrency.py',
               'run_selfscore_smoke.py', 'run_selfscore_batch128_v2.py')
NEW_ROOT_NAME = 'dvjustice_thinking_pipeline_20260929'


def replace_once(text, old, new):
    if text.count(old) != 1:
        raise ValueError('Audited dispatcher source changed at adaptation boundary')
    return text.replace(old, new, 1)


WRAPPER = original.WRAPPER.replace('dvjustice_answer_batch128_20260928', NEW_ROOT_NAME)
WRAPPER = WRAPPER.replace("R/'run_answer_batch128.py'", "R/'run_thinking_pipeline.py'")
WRAPPER = replace_once(WRAPPER, "    if intent['resume']:command.append('--resume')", "    if intent['resume']:command.append('--resume')\n    if intent['max_new_tasks'] is not None:command.extend(['--max-new-tasks',str(intent['max_new_tasks'])])")

STAGE = r'''
if mode=='stage':
    names={'run_thinking_pipeline.py','run_answer_batch128.py','run_answer_smoke.py',
           'benchmark_concurrency.py','run_selfscore_smoke.py','run_selfscore_batch128_v2.py',
           'answer_dispatch_wrapper.py','input_tasks.json','input_manifest.json',
           'task_mapping.jsonl','excluded_cases.json','legacy_scoring_bundle.py'}
    assert set(r)=={'mode','files'} and set(r['files'])==names
    safe(R).mkdir(mode=0o700,exist_ok=True)
    assert not R.stat().st_mode&0o077
    fd=os.open(safe(R/'staging.lock'),os.O_CREAT|os.O_RDWR|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'r+') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        files={}
        for name,item in r['files'].items():
            b=unbundle(item)
            if name.endswith('.py'):ast.parse(b.decode())
            write_once(R/name,b);files[name]={k:item[k] for k in ['size','sha256']}
        inputs=read(R/'input_tasks.json');manifest=read(R/'input_manifest.json')
        assert inputs['run_id']=='pkulaw_v4_thinking_pipeline_20260929' and inputs['service_job_id']=='402816'
        assert inputs['base_url']=='http://dgx-040:18044/v1' and inputs['model']=='deepseek_v4_flash'
        assert len(inputs['tasks'])==11095 and inputs['workers']==128 and inputs['thinking'] is True
        assert inputs['temperature']==.3 and inputs['initial_max_tokens']==3000 and inputs['timeout_s']==1800
        assert manifest['retry_contract']=='legacy108_coupled' and manifest['reused_answers']==0
        assert manifest['input_tasks_sha256']==files['input_tasks.json']['sha256']
        assert manifest['task_mapping_sha256']==files['task_mapping.jsonl']['sha256']
        assert manifest['bundle_sha256']==files['legacy_scoring_bundle.py']['sha256']=='6af07139a06994b3192e450e5fa79f23e69851b889e999a99187efaabc81d9ad'
        for name in ['input_tasks.json','task_mapping.jsonl','excluded_cases.json','legacy_scoring_bundle.py']:
            assert manifest['files'][name]['sha256']==files[name]['sha256']
            assert manifest['files'][name]['bytes']==files[name]['size']
        excluded=read(R/'excluded_cases.json');excludeids={x['case_id'] for x in excluded}
        assert len(excluded)==len(excludeids)==18
        keys=set();cases={'primary':set(),'secondary':set()}
        fields={'split','case_id','question_id','messages','prompt_hash','source_case_text_hash','masked_case_text_hash','question_hash','question_text'}
        for t in inputs['tasks']:
            assert set(t)==fields and t['case_id'] not in excludeids
            key=(t['split'],t['case_id'],t['question_id']);assert key not in keys;keys.add(key)
            cases[t['split']].add(t['case_id'])
        assert len(cases['primary'])==215 and len(cases['secondary'])==2004
        audit={'files':files,'new_network_questions':11095,'reused_answers':0,'permanently_excluded_cases':18,
               'stage_scope':'Thinking answer + legacy self-score; existing allocation only'}
        write_once(R/'stage_manifest.json',encoded(audit));checked_sources()
        print(json.dumps({'status':'staged','root':str(R),'file_count':len(files),
                          'uncompressed_bytes':sum(x['size'] for x in files.values()),'input_sha256':files['input_tasks.json']['sha256']}))
'''

REMOTE = original.REMOTE.replace('dvjustice_answer_batch128_20260928', NEW_ROOT_NAME)
stage_start = REMOTE.index("if mode=='stage':")
stage_end = REMOTE.index("elif mode in {'start','resume'}:", stage_start)
REMOTE = REMOTE[:stage_start] + STAGE.lstrip() + REMOTE[stage_end:]
REMOTE = replace_once(REMOTE, "    roots += [R/'results']", "    roots += [R/'results']\n    roots += [R.parent/n/'results' for n in ['dvjustice_answer_batch128_20260928','dvjustice_selfscore_batch128_20260928','dvjustice_selfscore_batch128_v2_20260928']]")
REMOTE = replace_once(REMOTE, "    endpoint=read(S/'endpoint.json')", """    result['previous_queues']={}
    for name in ['dvjustice_answer_batch128_20260928','dvjustice_selfscore_batch128_v2_20260928']:
        previous=R.parent/name
        intents=sorted((previous/'dispatches').glob('attempt-*.intent.json'))
        assert intents,'Missing previous queue dispatch evidence'
        ident=read(intents[-1])['dispatch_id']
        exit_record=read(previous/'dispatches'/(ident+'.exit.json'))
        execution=read(previous/'dispatches'/(ident+'.execution.json'))
        assert exit_record['exit_code']==0,'Previous queue did not exit cleanly'
        assert not alive(execution.get('runner_identity')),'Previous queue runner alive'
        wrapper=read(previous/'dispatches'/(ident+'.wrapper.json'))
        assert not alive(wrapper.get('wrapper_identity')),'Previous queue wrapper alive'
        summary=read(previous/'results'/'summary.json')
        assert summary['pending']==summary['inflight']==summary['needs_reconcile']==summary['unmatched_intents']==0
        result['previous_queues'][name]={'exit_code':0,'pending':0,'processes_alive':False}
    endpoint=read(S/'endpoint.json')""")
REMOTE = replace_once(REMOTE, "    assert set(r)=={'mode'}", "    assert set(r)=={'mode','max_new_tasks'}\n    assert r['max_new_tasks'] is None or type(r['max_new_tasks']) is int and 1<=r['max_new_tasks']<=10\n    if mode=='start':assert r['max_new_tasks']==10")
REMOTE = replace_once(REMOTE, "'new_gpu_allocation':False,'workers':128,'new_network_questions':11085,'scoring_enabled':False,", "'new_gpu_allocation':False,'workers':128,'new_network_questions':11095,'scoring_enabled':True,'max_new_tasks':r['max_new_tasks'],")
REMOTE = replace_once(REMOTE, "'launcher_pid':child.pid,'workers':128,'questions':11085,'new_gpu_allocation':False", "'launcher_pid':child.pid,'workers':128,'questions':11095,'max_new_tasks':r['max_new_tasks'],'new_gpu_allocation':False")
REMOTE = replace_once(REMOTE, "'walltime_s','sampler_count','workers','scoring_performed'}", "'walltime_s','sampler_count','workers','scoring_performed','answer_counts','scoring_counts','successful_answers','valid_scores','stage_counts','retry_contract','session_max_new_tasks'}")


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('mode', choices=['stage','start','resume','status','fetch'])
    parser.add_argument('--raw', action='store_true')
    limits = parser.add_mutually_exclusive_group()
    limits.add_argument('--max-new-tasks', type=int, default=10)
    limits.add_argument('--all-remaining', action='store_true')
    args = parser.parse_args()
    if args.raw and args.mode != 'fetch':parser.error('--raw requires fetch')
    if args.all_remaining and args.mode != 'resume':parser.error('Full continuation requires explicit resume after pilot audit')
    payload = {'mode':args.mode}
    if args.mode == 'stage':
        sources = {name:(LOCAL/name).read_bytes() for name in DATA_FILES}
        sources.update({name:(OLD/name).read_bytes() for name in OLD_SCRIPTS})
        sources['run_thinking_pipeline.py'] = (HERE/'run_thinking_pipeline.py').read_bytes()
        sources['answer_dispatch_wrapper.py'] = WRAPPER.encode()
        for name, raw in sources.items():
            if name.endswith('.py'):ast.parse(raw.decode())
        payload['files'] = {name:original.pack(raw) for name,raw in sources.items()}
    elif args.mode in ('start','resume'):
        payload['max_new_tasks'] = None if args.all_remaining else args.max_new_tasks
    elif args.mode == 'fetch':
        payload['raw'] = args.raw
    ast.parse(REMOTE);ast.parse(WRAPPER)
    command = ['ssh','-o','BatchMode=yes','-o','ConnectTimeout=15','-o','StrictHostKeyChecking=yes',
               'Group3_slogin','python3 -B -c '+shlex.quote(REMOTE)]
    completed = subprocess.run(command,input=json.dumps(payload),text=True,capture_output=True,
                               timeout=300 if args.mode in ('stage','fetch') else 100)
    record = {'at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'mode':args.mode,
              'exit_code':completed.returncode,'stderr':completed.stderr}
    try:result=json.loads(completed.stdout)
    except json.JSONDecodeError:result={'output_parse_failed':True,'stdout_bytes':len(completed.stdout.encode())}
    if args.mode=='fetch' and completed.returncode==0:result=original.save_fetch(result,LOCAL)
    record['result']=result
    directory=LOCAL/'dispatch_records';directory.mkdir(parents=True,mode=0o700,exist_ok=True)
    stamp=datetime.datetime.now().strftime('%Y%m%dT%H%M%S%f')
    with (directory/(args.mode+'_'+stamp+'.json')).open('x') as stream:
        json.dump(record,stream,ensure_ascii=False,indent=2);stream.write('\n')
    print(json.dumps(record,ensure_ascii=False,indent=2))
    raise SystemExit(completed.returncode)


if __name__ == '__main__':main()
