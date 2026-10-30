#!/usr/bin/env python3
"""Offline hard gate for the first two formal DeepSeek self-scores.

Reads frozen scoring inputs/bundle and a fetched --raw evidence snapshot.
Never modifies real inputs/results, accesses credentials, or calls a model.
"""
import argparse
from collections import Counter
import json
import math
from pathlib import Path
import tempfile
import threading

import run_selfscore_batch128 as score

HERE=Path(__file__).resolve().parent
SOURCE=HERE.parents[2]/'data/pkulaw_v4_selfscore_batch128_20260928'
KEYS=('split','case_id','question_id')


def require(value,message):
    if not value:raise ValueError(message)


def read(path):return json.loads(path.read_bytes())


def digest(path):return score.base.digest(path.read_bytes())


def task_dir(task):return 'task_'+score.base.digest(score.base.canonical([task[k] for k in KEYS]))[:24]


def numbers_equal(a,b,label):
    require(isinstance(a,(int,float)) and not isinstance(a,bool) and math.isfinite(a)
            and math.isclose(a,b,rel_tol=1e-9,abs_tol=1e-9),label+' mismatch')


def audit(source,snapshot,*,expected_tasks=11095):
    raw=(source/'input_scoring.json').read_bytes();data=json.loads(raw);manifest=read(source/'input_manifest.json')
    score.validate(data)
    require(score.base.digest(raw)==manifest['input_scoring_sha256'],'Input manifest hash mismatch')
    require(len(data['tasks'])==expected_tasks,'Wrong scoring population')
    bundle,bundle_hash=score.load_bundle(source)
    require(bundle_hash==data['bundle_sha256']==score.BUNDLE_SHA256,'Not the fixed historical scoring bundle')
    if expected_tasks==11095:
        require(data['upstream_answers_sha256']=='4b6c30e35f1520db153e88b01c0c3b2673b0e7571c3a1ff0a76a34d77f2beac5',
                'Wrong final answer release')
        require(manifest['cases']==2219 and manifest['questions']==11095,'Manifest population differs')
    tasks=data['tasks'][:2]
    require([t['split'] for t in tasks]==['primary','secondary'],'First two tasks must cover both splits')
    require(len({tuple(t[k] for k in KEYS) for t in tasks})==2,'Repeated pilot key')
    results=snapshot/'results';dispatch=snapshot/'dispatches'
    identity=read(results/'run_identity.json')
    expected_identity={'input_sha256':score.base.digest(raw),'script_sha256':digest(Path(score.__file__)),
       'helper_sha256':digest(Path(score.base.__file__)),'queue_sha256':digest(Path(score.answering.__file__)),
       'legacy_adapter_sha256':digest(Path(score.legacy.__file__)),'monitor_sha256':digest(Path(score.benchmark.__file__)),
       'bundle_sha256':bundle_hash,'upstream_answers_sha256':data['upstream_answers_sha256'],
       'parameters':score.CONFIG,'tasks':expected_tasks,'retry_policy':score.answering.RETRY_POLICY,
       'schema_version':'V18','evaluation_parser':'legacy'}
    require(identity==expected_identity,'Execution identity/source hashes differ')
    expected_dirs={task_dir(t) for t in tasks}
    require({p.name for p in results.glob('task_*')}==expected_dirs,'Snapshot does not contain exactly first two tasks')
    intents=sorted(dispatch.glob('attempt-*.intent.json'))
    require([p.name for p in intents]==['attempt-001.intent.json'],'Not the untouched first pilot dispatch')
    intent=read(intents[0]);exit_record=read(dispatch/'attempt-001.exit.json')
    require(intent['max_new_tasks']==2 and intent['resume'] is False and intent['scoring_enabled'] is True
            and intent['answers_regenerated'] is False and intent['new_gpu_allocation'] is False,
            'Pilot dispatch scope differs')
    require(intent['job_id']=='402816' and intent['node']=='dgx-040'
            and intent['input_sha256']==score.base.digest(raw),'Dispatch input/job mismatch')
    require(exit_record['exit_code']==0 and exit_record['dispatch_id']=='attempt-001','Pilot did not exit cleanly')
    execution=read(dispatch/'attempt-001.execution.json');wrapper=read(dispatch/'attempt-001.wrapper.json')
    require(execution['runner_identity']==exit_record['runner_identity'],'Exit belongs to another runner')
    require(execution['wrapper_identity']==wrapper['wrapper_identity'] and execution['max_new_tasks']==wrapper['max_new_tasks']==2,
            'Wrapper/runner pilot identity differs')
    rows=[];usage=Counter();attempts=0;length_retries=0;terminals=[]
    for task in tasks:
        directory=results/task_dir(task);terminal=read(directory/'terminal.json');terminals.append(terminal)
        inner,messages=score.messages_for(bundle,task)
        payloads={str(limit):dict(model='deepseek_v4_flash',messages=messages,temperature=.3,max_tokens=limit,
                                 chat_template_kwargs={'thinking':True}) for limit in (3000,6000)}
        payload_hashes={k:score.base.digest(score.base.canonical(v)) for k,v in payloads.items()}
        frozen={k:v for k,v in task.items() if k not in score.OMIT_TERMINAL}
        frozen.update(messages=messages,inner_prompt_sha256=score.base.digest(inner.encode()),
                      request_sha256_by_token_limit=payload_hashes)
        require(read(directory/'frozen_input.json')==frozen,'Frozen per-task scoring request changed')
        requests=sorted(directory.glob('attempt_*.request.json'));responses=sorted(directory.glob('attempt_*.response.json'))
        require(1<=len(requests)<=score.base.MAX_HTTP and len(requests)==len(responses),'Missing/extra/unknown request receipt')
        previous_status=None;maximum=3000;expanded=False;last_response=None
        for index,(req_path,res_path) in enumerate(zip(requests,responses),1):
            require(req_path.name=='attempt_%03d.request.json'%index and res_path.name=='attempt_%03d.response.json'%index,
                    'Nonconsecutive attempt numbering')
            request,response=read(req_path),read(res_path)
            require(request['attempt']==index,'Request attempt field differs')
            limit=request['payload'].get('max_tokens')
            require(limit in (3000,6000),'Unapproved token budget')
            if limit!=maximum:
                require(maximum==3000 and limit==6000 and not expanded and previous_status=='truncated',
                        'Token budget did not follow the one allowed truncation extension')
                maximum=6000;expanded=True
            require(request['payload']==payloads[str(limit)],'Prompt/model/thinking/temperature/payload differs')
            require(not str(response.get('error','')).startswith('transport_') and response.get('http_status') not in (408,504),
                    'Uncertain execution cannot pass pilot gate')
            require('raw_response' in response and json.loads(response['raw_body'])==response['raw_response'],
                    'Raw/parsed response differs or response is not JSON')
            body=response['raw_response'];require(isinstance(body,dict),'Invalid response body')
            require(response.get('usage')==body.get('usage'),'Receipt/raw token usage differs')
            status,content,finish=score.base.classify(response)
            require(response['classification']==status and response['finish_reason']==finish,'Receipt classification differs')
            require(status not in ('refusal','content_filter'),'Pilot contains a refusal/filter response')
            if status in ('ok','empty','truncated'):require(body.get('model')=='deepseek_v4_flash','Wrong response model')
            if index<len(requests):
                require(status in ('error','empty','truncated'),'Already successful request was scored again')
                if status=='truncated':require(limit==3000,'A 6000-token truncation cannot be retried')
            else:
                require(status=='ok' and finish=='stop' and content.strip(),'Final score is empty/incomplete/failed')
                last_response=response
            previous_status=status
        require(last_response is not None,'Missing successful final receipt')
        body=last_response['raw_response'];message=body['choices'][0]['message']
        content=message['content'];reasoning=message.get('reasoning_content') or message.get('reasoning') or ''
        reasoning=reasoning if isinstance(reasoning,str) else ''
        for k,v in task.items():
            if k not in score.OMIT_TERMINAL:require(terminal.get(k)==v,'Terminal task provenance differs: '+k)
        for k,v in {'status':'ok','requested_model':'deepseek_v4_flash','response_model':'deepseek_v4_flash',
                    'finish_reason':'stop','temperature':.3,'thinking':True,'self_scoring':True,
                    'answers_regenerated':False,'evaluation_parser':'legacy','score_zero_imputed':False}.items():
            require(terminal.get(k)==v,'Terminal final condition differs: '+k)
        require(terminal['raw_evaluation']==content and terminal['reasoning_content']==reasoning,'Terminal/raw evaluation differs')
        require(terminal['response_id']==body.get('id'),'Response identity differs')
        headers=last_response.get('headers',{})
        require(terminal['request_id']==(headers.get('x-request-id') or headers.get('request-id')),'Request identity differs')
        replay=score.legacy.Capture({'answer':content,'thinking':reasoning},expected=inner)
        evaluator,evaluation=score.legacy.evaluate(bundle,replay,task,task['answer'])
        require(not score.legacy.complete_format(content,evaluator.scoring_criteria),'Five score dimensions are incomplete')
        require(terminal['missing_or_invalid_dimensions']==[] and terminal['parse_error'] is None,'Parser guard failed')
        require(terminal['evaluation']==terminal['legacy_evaluation']==evaluation,'Legacy replay differs or score was imputed')
        dimensions=evaluation['各维度得分']
        require(set(dimensions)==set(evaluator.scoring_criteria) and all(isinstance(v,(int,float)) and 0<=v<=4 for v in dimensions.values()),
                'Dimension values invalid')
        numbers_equal(evaluation['总分'],round(sum(dimensions.values()),2),'Total score')
        numbers_equal(evaluation['百分制'],round(sum(dimensions.values())*5,2),'Percentage')
        receipts,task_usage,num=score.legacy.stats(directory)
        require(terminal['usage']==task_usage and terminal['final_response_usage']==last_response.get('usage'),'Terminal usage differs')
        require(terminal['attempts']==terminal['request_intents']==terminal['recorded_responses']==num==len(receipts),
                'Terminal attempt accounting differs')
        require(terminal['max_tokens']==maximum and terminal['length_retried'] is expanded
                and terminal['request_sha256_by_token_limit']==payload_hashes,'Token/hash terminal accounting differs')
        numbers_equal(terminal['elapsed_s'],round(sum(r.get('elapsed_s',0) for r in receipts),3),'Cumulative request latency')
        attempts+=num;length_retries+=expanded;usage.update(task_usage)
        rows.append({**{k:task[k] for k in KEYS},'score_20':evaluation['总分'],'score_100':evaluation['百分制'],
                     'dimensions':dimensions,'error_flags':evaluation['错误详情'],'attempts':num,'length_retried':expanded,
                     'final_max_tokens':maximum,'usage':task_usage,'answer_hash':task['answer_hash'],
                     'scoring_prompt_hash':task['scoring_prompt_hash']})
    summary=read(results/'summary.json');state=read(results/'run_state.json')
    require(state==summary,'Final state and summary differ')
    required={'status':'segment_completed','tasks':expected_tasks,'network_tasks':expected_tasks,'completed':2,
              'new_completed':2,'success':2,'failed':0,'needs_reconcile':0,'pending':expected_tasks-2,
              'counts':{'ok':2},'inflight':0,'actual_attempts':attempts,'recorded_responses':attempts,
              'unmatched_intents':0,'length_retries':length_retries,'stop_reasons':[],'thinking':True,
              'temperature':.3,'answers_regenerated':False,'evaluation_parser':'legacy','zero_imputation':False,
              'session_max_new_tasks':2,'formal_scoring':True,'self_scoring':True}
    for k,v in required.items():require(summary.get(k)==v,'Pilot summary mismatch: '+k)
    require(summary['usage_totals']==dict(usage),'Aggregate token totals differ')
    require(1<=summary['max_inflight']<=2,'Pilot exceeded two concurrent formal tasks')
    sessions=sorted((results/'sessions').glob('session_*.json'))
    require(len(sessions)==1 and read(sessions[0])==summary,'Pilot session receipt missing or differs')
    for split in ('primary','secondary'):
        lines=(results/('scores_deepseek_v4_flash_'+split+'.jsonl')).read_text().splitlines()
        expected=[r for r in terminals if r['split']==split]
        require([json.loads(line) for line in lines]==expected,'Split score export differs')
    require((results/'failures.jsonl').read_bytes()==b'','Pilot failures export is not empty')
    samples=sorted((results/'samples').glob('sample_*.json'))
    require(len(samples)==summary['sampler_count'] and len(samples)>0,'Monitoring evidence missing')
    for path in samples:
        sample=read(path)
        require(sample.get('healthy') is True and not sample.get('gpu_anomalies') and not sample.get('gpu_error'),
                'Monitoring reported an unhealthy pilot')
        require(sample.get('gpu_returncode')==0 and len(sample.get('gpus') or [])==8,'Missing eight-GPU observation')
    return {'audit_passed':True,'scope':'First two formal scores only; not full scoring completion',
            'input_scoring_sha256':score.base.digest(raw),'bundle_sha256':bundle_hash,'snapshot':str(snapshot),
            'population_tasks':expected_tasks,'valid_pilot_scores':2,'remaining_tasks':expected_tasks-2,
            'cases':[r for r in rows],'usage_totals':dict(usage),'actual_attempts':attempts,'length_retries':length_retries,
            'pilot_walltime_s':summary['walltime_s'],'self_scoring':True,'answers_regenerated':False,
            'zero_imputation':False,'full_resume_gate':'passed','model_requests_by_this_audit':0}


def self_test():
    # Real runner with an entirely synthetic local transport; no API/SSH.
    from test_selfscore_batch128 import fixture,reply,BUNDLE,BUNDLE_HASH
    from test_answer_batch128 import Sampler
    data=fixture();tasks=data['tasks'];data['tasks']=[tasks[0],tasks[5]]+tasks[1:5]+tasks[6:]
    raw=score.base.canonical(data)
    with tempfile.TemporaryDirectory() as directory:
        root=Path(directory).resolve();source=root/'source';snapshot=root/'snapshot'
        source.mkdir();(snapshot/'dispatches').mkdir(parents=True)
        (source/'input_scoring.json').write_bytes(raw)
        (source/'legacy_scoring_bundle.py').write_bytes((SOURCE/'legacy_scoring_bundle.py').read_bytes())
        (source/'input_manifest.json').write_text(json.dumps({'bundle_sha256':BUNDLE_HASH,'input_scoring_sha256':score.base.digest(raw)}))
        calls=[];lock=threading.Lock()
        def fake(payload):
            with lock:calls.append(payload)
            return reply()
        endpoint=dict(status='ready',job_id='402816',model='deepseek_v4_flash',hostname='dgx-040',max_num_seqs=128)
        runner=score.Runner(raw,snapshot/'results',fake,Sampler(),endpoint,BUNDLE,BUNDLE_HASH,max_new_tasks=2,sleep=lambda _:None)
        runner.run()
        common={'dispatch_id':'attempt-001','max_new_tasks':2,'resume':False,'scoring_enabled':True,'answers_regenerated':False,
                'new_gpu_allocation':False,'job_id':'402816','node':'dgx-040','input_sha256':score.base.digest(raw)}
        ids={'wrapper_identity':{'pid':1,'started':'1'},'runner_identity':{'pid':2,'started':'2'},'max_new_tasks':2}
        for suffix,value in [('intent',common),('wrapper',ids),('execution',ids),
                             ('exit',{'exit_code':0,'dispatch_id':'attempt-001','runner_identity':ids['runner_identity']})]:
            (snapshot/'dispatches'/('attempt-001.'+suffix+'.json')).write_text(json.dumps(value))
        report=audit(source,snapshot,expected_tasks=10);require(report['audit_passed'] and len(calls)==2,'Valid fixture failed')
        terminal=next((snapshot/'results').glob('task_*/terminal.json'));old=read(terminal);bad=dict(old,status='truncated',evaluation=None)
        terminal.write_text(json.dumps(bad))
        try:audit(source,snapshot,expected_tasks=10)
        except ValueError:pass
        else:raise AssertionError('Invalid terminal accepted')
    print(json.dumps({'offline_self_test':'passed','real_api_calls':0,'real_data_modified':False}))


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot',type=Path)
    parser.add_argument('--source',type=Path,default=SOURCE)
    parser.add_argument('--self-test',action='store_true')
    args=parser.parse_args()
    if args.self_test:self_test();return
    if not args.snapshot:parser.error('--snapshot is required')
    report=audit(args.source.resolve(),args.snapshot.resolve())
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
