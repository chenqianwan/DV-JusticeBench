"""Read-only interim paired comparison; never changes runs or scores."""
import collections
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shlex
import statistics
import subprocess

ROOT = Path('/Users/chenlong/WorkSpace/huangyidan')
BASE = ROOT / 'data/pkulaw_admin_ds_nt_rerun_20261004'
FINAL = ROOT / 'data/pkulaw_three_model_scoring_20260930/final_export_20261001'
DIMS = ['规范依据相关性', '涵摄链条对齐度', '价值衡量与同理心对齐度', '关键事实与争点覆盖度', '裁判结论与救济配置一致性']
REMOTE = r'''
import json,hashlib,pathlib,datetime
r=pathlib.Path('/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_nt_rerun_20261004')
start=datetime.datetime.now(datetime.timezone.utc).isoformat()
paths=[(ep,p) for ep in ('ep146','ep152') for p in sorted((r/ep/'results').glob('task_*/terminal.json'))]
rows=[]
fields=['split','case_id','question_id','status','answer_status','answer_hash','question_hash','masked_case_text_hash','judge_decision_hash','thinking','scoring_thinking','completed_at','evaluation','answer_finish_reason','finish_reason','pipeline_attempt']
for ep,p in paths:
 raw=p.read_bytes();v=json.loads(raw)
 row={k:v.get(k) for k in fields}
 ev=row.get('evaluation')
 row['evaluation']={k:ev[k] for k in ('总分','各维度得分')} if ev is not None else None
 row.update(endpoint=ep,terminal_path=str(p),terminal_sha256=hashlib.sha256(raw).hexdigest(),answer_sha_verified=hashlib.sha256(v.get('answer','').encode()).hexdigest()==v.get('answer_hash'),answer_reasoning_empty=not v.get('answer_reasoning_content','').strip())
 rows.append(row)
print(json.dumps({'started_at':start,'finished_at':datetime.datetime.now(datetime.timezone.utc).isoformat(),'rows':rows},ensure_ascii=False,separators=(',',':')))
'''

def key(r):
    return tuple(r[f] for f in ('split', 'case_id', 'question_id'))

def metrics(values):
    return {'n':len(values),'mean':statistics.mean(values), 'sd':statistics.stdev(values) if len(values)>1 else None}

def main():
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = BASE / 'checks' / ('score_comparison_' + stamp)
    output.mkdir(parents=True,exist_ok=False)
    process = subprocess.run(['ssh','-o','BatchMode=yes','-o','ConnectTimeout=20','Group3_slogin',
        'python3 -B -c '+shlex.quote(REMOTE)],capture_output=True,timeout=180)
    if process.returncode:
        raise RuntimeError(process.stderr.decode()[-2000:])
    snapshot = json.loads(process.stdout)
    (output/'current_compact_snapshot.json').write_bytes(process.stdout)
    new = {}
    for row in snapshot['rows']:
        assert key(row) not in new
        assert row['thinking'] is False and row['scoring_thinking'] is True
        assert row['answer_sha_verified'] and row['answer_reasoning_empty']
        new[key(row)] = row
    models = {'old_nonthinking':{}, 'old_thinking':{}, 'new_nonthinking':new}
    sources = {}
    paths = [(name, FINAL/directory/'results'/filename)
             for name,directory,prefix in [('old_nonthinking','ds_nonthinking','scores_deepseek_v4_flash'),('old_thinking','ds_thinking','thinking_pipeline')]
             for filename in [prefix+'_primary.jsonl',prefix+'_secondary.jsonl']]
    paths.append(('old_nonthinking',ROOT/'data/pkulaw_v4_selfscore_batch128_v2_20260928/reused_scores.jsonl'))
    for name,path in paths:
        h=hashlib.sha256();count=0
        with path.open('rb') as stream:
            for raw in stream:
                h.update(raw)
                if not raw.strip():continue
                row=json.loads(raw);k=key(row);count+=1
                assert k not in models[name]
                models[name][k] = {f:row.get(f) for f in ('split','case_id','question_id','status','answer_hash','question_hash','masked_case_text_hash','judge_decision_hash','evaluation')}
        sources[str(path.relative_to(ROOT))]={'rows':count,'sha256':h.hexdigest()}
    assert len(models['old_nonthinking']) == len(models['old_thinking']) == 11095
    for k,row in new.items():
        for name in ('old_nonthinking','old_thinking'):
            assert k in models[name]
            for field in ('question_hash','masked_case_text_hash','judge_decision_hash'):
                assert row[field] == models[name][k][field],(k,field)
    for name,rows in models.items():
        for row in rows.values():
            ev=row['evaluation']
            if row['status'] != 'ok':
                assert ev is None
                continue
            assert all(math.isfinite(ev['各维度得分'][d]) and 0<=ev['各维度得分'][d]<=4 for d in DIMS)
            assert math.isfinite(ev['总分']) and abs(sum(ev['各维度得分'][d] for d in DIMS)-ev['总分'])<1e-7
    result={'snapshot_started_at':snapshot['started_at'],'snapshot_finished_at':snapshot['finished_at'],
      'completed_snapshot':len(new),'expected':11095,'status_counts':dict(collections.Counter(r['status'] for r in new.values())),
      'provisional_running_snapshot':True,'comparison':'三条件同题且评分均有效的交集；现行legacy评分原值，不修复/替换/填零',
      'sources':sources,'current_snapshot_sha256':hashlib.sha256(process.stdout).hexdigest(),'by_split':{}}
    for split in ('primary','secondary'):
        valid={name:{k for k,r in rows.items() if k[0]==split and r['status']=='ok'} for name,rows in models.items()}
        common=set.intersection(*valid.values())
        assert common
        ordered=sorted(common)
        totals={name:[rows[k]['evaluation']['总分'] for k in ordered] for name,rows in models.items()}
        record={'paired_n':len(ordered),'new_completed':sum(k[0]==split for k in new),
          'new_status_counts':dict(collections.Counter(r['status'] for k,r in new.items() if k[0]==split)),
          'all_available_valid':{name:metrics([models[name][k]['evaluation']['总分'] for k in keys]) for name,keys in valid.items()},
          'paired_scores':{name:dict(**metrics(values),dim_means=[statistics.mean(models[name][k]['evaluation']['各维度得分'][d] for k in ordered) for d in DIMS]) for name,values in totals.items()},
          'paired_comparisons':{}}
        for comparator in ('old_nonthinking','old_thinking'):
            a,b=totals['new_nonthinking'],totals[comparator]
            diffs=[x-y for x,y in zip(a,b)]
            record['paired_comparisons']['new_minus_'+comparator]={
              'mean_difference':statistics.mean(diffs),'mean_absolute_difference':statistics.mean(map(abs,diffs)),
              'identical_scores':sum(abs(d)<1e-9 for d in diffs), 'higher':sum(d>1e-9 for d in diffs),'lower':sum(d<-1e-9 for d in diffs),
              'identical_answers':sum(new[k]['answer_hash']==models[comparator][k]['answer_hash'] for k in ordered),
              'correlation':statistics.correlation(a,b)}
        result['by_split'][split]=record
    result['output_directory']=str(output)
    (output/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2))
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
