#!/usr/bin/env python3
"""Freeze performance-only real prompts, excluding flagged reference leakage."""
import hashlib
import json
from pathlib import Path
import random
from collections import Counter

from prepare_answer_smoke import write_once

REPO = Path(__file__).resolve().parents[3]
SOURCE = REPO / 'data/pkulaw_v4_full_pipeline_20260927'
OUT = REPO / 'data/pkulaw_concurrency_benchmark_20260927'


def sha(data):
    return hashlib.sha256(data).hexdigest()


def main():
    data = (SOURCE/'input_tasks.jsonl').read_bytes()
    rows = [json.loads(line) for line in data.splitlines()]
    assert len(rows) == 11185
    cases = {}
    excluded = set()
    for row in rows:
        cid = row['case_id']
        if row['judge_decision_masked'] and row['judge_decision_masked'] in row['case_text_masked']:
            excluded.add(cid)
        cases.setdefault(cid, []).append(row)
    assert len(cases) == 2237 and len(excluded) == 18
    rng = random.Random(20260927)
    selected = {}
    for split, count in [('primary', 24), ('secondary', 232)]:
        group = sorted((questions for cid,questions in cases.items()
                        if cid not in excluded and questions[0]['split'] == split),
                       key=lambda qs: (len(qs[0]['case_text_masked']), qs[0]['case_id']))
        picked = [group[round(i*(len(group)-1)/(count-1))] for i in range(count)]
        assert len({qs[0]['case_id'] for qs in picked}) == count
        selected[split] = [rng.choice(qs) for qs in picked]
        rng.shuffle(selected[split])
    ordered = []
    for block in range(8):
        group = selected['primary'][block*3:(block+1)*3] + selected['secondary'][block*29:(block+1)*29]
        rng.shuffle(group)
        ordered.extend(group)
    tasks = [dict(task_id=f'bench_{i:03d}', case_id=r['case_id'], question_id=r['question_id'],
                  messages=r['answer_messages'], prompt_hash=r['answer_prompt_hash'])
             for i,r in enumerate(ordered, 1)]
    assert len(tasks) == len({r['case_id'] for r in tasks}) == 256
    inputs = dict(run_id=OUT.name, model='deepseek_v4_flash', base_url='http://dgx-040:18044/v1',
                  service_job_id='402816', temperature=0.3, max_tokens=256, thinking=False,
                  timeout_s=1800, tasks=tasks)
    OUT.mkdir(exist_ok=True)
    write_once(OUT/'input_benchmark.json', inputs)
    prefixes = {}
    for concurrency in [16,32,64,128]:
        chosen = ordered[:2*concurrency]
        lengths = sorted(len(r['case_text_masked']) for r in chosen)
        prefixes[str(concurrency)] = dict(requests=len(chosen), splits=dict(Counter(r['split'] for r in chosen)),
                                         mean_fact_chars=round(sum(lengths)/len(lengths),1),
                                         max_fact_chars=max(lengths))
    manifest = dict(purpose='Performance diagnostic only; never formal experiment answers or scores.',
                    source_task_file_sha256=sha(data), input_sha256=sha((OUT/'input_benchmark.json').read_bytes()),
                    cases=256, questions=256, seed=20260927, flagged_cases_excluded_from_benchmark=sorted(excluded),
                    selection='Length-stratified distinct cases; 3 primary + 29 secondary per 32 requests; original prompt unchanged.',
                    workload='Answering, non-thinking, 256 output token cap; scoring throughput not measured by this test.',
                    formal_output_token_limit_unchanged='Formal answers still 3000->6000; benchmark responses excluded.',
                    cache_caveat='Prompts repeat across levels, so later levels may benefit from warm prefix cache. Report cache/latency and do not assume linear full-run speedup.',
                    rounds=prefixes, api_calls=0)
    write_once(OUT/'input_manifest.json', manifest)
    print(json.dumps({'directory':str(OUT),'input_sha256':manifest['input_sha256'],'rounds':prefixes},ensure_ascii=False))


if __name__ == '__main__':
    main()
