#!/usr/bin/env python3
"""Read-only audit of saved pilot evidence; writes only new audit/report files.

No credentials, HTTP calls, model calls, scoring, or mutation of pilot_results.
"""
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import statistics

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / 'data/pkulaw_primary_three_answers_20260929'
RESULTS = OUT / 'pilot_results'
MODELS = ('gpt-4o', 'gemini-2.5-flash', 'qwen-max')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()


def sha(value):
    return hashlib.sha256(value if isinstance(value, bytes) else value.encode()).hexdigest()


def read(path):
    return json.loads(path.read_text())


def unchanged_write(path, value):
    data = value.encode() if isinstance(value, str) else (json.dumps(value, ensure_ascii=False, indent=2) + '\n').encode()
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('Refusing to overwrite a different audit/report: ' + str(path))
    else:
        with path.open('xb') as f:
            f.write(data)


def main():
    checks = []
    def check(condition, description):
        if not condition:
            raise AssertionError(description)
        checks.append(description)
    before = {str(p.relative_to(RESULTS)): sha(p.read_bytes()) for p in RESULTS.rglob('*') if p.is_file()}
    identity, manifest, ledger = (read(p) for p in (RESULTS / 'run_identity.json', OUT / 'manifest.json', RESULTS / 'budget_ledger.json'))
    pilot_bytes = (OUT / 'pilot_tasks.json').read_bytes()
    check(sha(pilot_bytes) == identity['pilot_input_sha256'] == manifest['pilot_tasks_sha256'], 'pilot immutable identity')
    check(sha((ROOT / 'scripts/local_primary_answers_20260929/run.py').read_bytes()) == identity['runner_sha256'], 'runner unchanged since run')
    check(sha((ROOT / 'scripts/cross_judge_budget_guard.py').read_bytes()) == identity['budget_guard_sha256'], 'budget guard unchanged since run')
    tasks = json.loads(pilot_bytes)['tasks']
    check(len(tasks) == 15 and Counter(t['model'] for t in tasks) == {m: 5 for m in MODELS}, 'fifteen tasks, five per model')
    case_ids = {t['case_id'] for t in tasks}
    check(len(case_ids) == 1, 'one primary case')
    source_path = ROOT / 'data/pkulaw_v4_answer_batch128_20260928/task_mapping.jsonl'
    check(sha(source_path.read_bytes()) == manifest['source_mapping_sha256'], 'original task mapping unchanged')
    source = {}
    with source_path.open() as f:
        for line in f:
            row = json.loads(line)
            if row['case_id'] in case_ids:
                source[row['question_id']] = row
    check(len(source) == 5, 'source contains exact same five questions')
    guard_records = [read(p) for p in sorted((RESULTS / 'http_receipts').glob('*.json'))]
    by_payload = defaultdict(list)
    for record in guard_records:
        by_payload[sha(canonical(record['request']))].append(record)
    check(len(guard_records) == 15, 'fifteen persisted physical request receipts')
    counts, usage_warnings, audit_rows = defaultdict(list), [], []
    for task in tasks:
        tid = 'task_' + sha(canonical([task[k] for k in ('split', 'case_id', 'question_id', 'model')]))[:24]
        directory = RESULTS / tid
        frozen, terminal = read(directory / 'frozen_input.json'), read(directory / 'terminal.json')
        reqs = sorted(directory.glob('attempt_*.request.json'))
        reps = sorted(directory.glob('attempt_*.response.json'))
        check(len(reqs) == len(reps) == 1, tid + ' one matched request/response')
        req, rep = read(reqs[0]), read(reps[0])
        expected_payload = dict(model=task['model'], messages=task['messages'], temperature=.3, max_tokens=3000)
        check(frozen == task and terminal['task_sha256'] == sha(canonical(task)), tid + ' frozen task identity')
        check(task['messages'] == source[task['question_id']]['answer_messages'], tid + ' exact original answer prompt')
        check(source[task['question_id']]['judge_decision_masked'] not in task['messages'][1]['content'], tid + ' judgment absent from answer input')
        check(req['payload'] == expected_payload and req['payload_sha256'] == sha(canonical(expected_payload)), tid + ' exact model/temperature/token payload; no thinking parameter')
        check(terminal['status'] == 'ok' and terminal['finish_reason'] == 'stop', tid + ' complete successful terminal')
        check(terminal['attempts'] == 1 and terminal['length_retried'] is False, tid + ' exact successful evidence count')
        check(terminal['scoring_performed'] is False and terminal['thinking_parameter_omitted'] is True, tid + ' answer-only scientific condition')
        check(rep['http_status'] == 200 and sha(rep['raw_body']) == rep['raw_body_sha256'], tid + ' raw body intact')
        body = json.loads(rep['raw_body'])
        check(body == rep['raw_response'], tid + ' parsed response matches complete raw body')
        check(body['choices'][0]['message']['content'] == terminal['answer'] and bool(terminal['answer'].strip()), tid + ' terminal answer equals response')
        check(body['model'] == terminal['response_model'] and body['id'] == terminal['response_id'], tid + ' response model/id provenance')
        usage = body['usage']
        check(rep['usage'] == usage, tid + ' response usage identical')
        numeric = {k: v for k, v in usage.items() if isinstance(v, (int, float)) and not isinstance(v, bool)}
        check(terminal['usage'] == numeric, tid + ' terminal numeric usage identical')
        guards = by_payload[sha(canonical(expected_payload))]
        check(len(guards) == 1, tid + ' unique budget receipt match')
        guard = guards[0]
        check(guard['response'] == body and guard['usage'] == usage and guard['http_status'] == 200 and guard['settled'], tid + ' complete guard response/usage settlement')
        pin, pout = ledger['prices_usd_per_million'][task['model']]
        cost = (usage['prompt_tokens'] * pin + usage['completion_tokens'] * pout) / 1e6
        check(math.isclose(cost, guard['list_estimated_usd'], rel_tol=1e-12) and guard['unknown_upper_usd'] == 0, tid + ' exact cost estimate and no unknown charge')
        reason_details = usage.get('completion_tokens_details') or {}
        explicit_reasoning = usage.get('reasoning_tokens', reason_details.get('reasoning_tokens'))
        discrepancy = usage['total_tokens'] - usage['prompt_tokens'] - usage['completion_tokens']
        if discrepancy:
            usage_warnings.append(dict(model=task['model'], question_id=task['question_id'],
                    prompt_tokens=usage['prompt_tokens'], completion_tokens=usage['completion_tokens'],
                    total_tokens_reported=usage['total_tokens'], total_minus_input_output=discrepancy))
        record = dict(task_id=tid, question_id=task['question_id'], model=task['model'], response_model=body['model'],
                      prompt_tokens=usage['prompt_tokens'], completion_tokens=usage['completion_tokens'],
                      total_tokens_reported=usage['total_tokens'], reasoning_tokens_explicit=explicit_reasoning,
                      estimated_usd=cost, latency_seconds=guard['latency_seconds'],
                      response_id=body['id'], raw_response_sha256=rep['raw_body_sha256'],
                      request_sha256=req['payload_sha256'], budget_receipt=guard['call_id'], status='ok')
        counts[task['model']].append(record)
        audit_rows.append(record)
    summary = {}
    for model in MODELS:
        records = counts[model]
        reasoning = [r['reasoning_tokens_explicit'] for r in records]
        summary[model] = dict(answers=len(records), valid=len(records),
                             prompt_tokens=sum(r['prompt_tokens'] for r in records),
                             completion_tokens=sum(r['completion_tokens'] for r in records),
                             total_tokens_reported=sum(r['total_tokens_reported'] for r in records),
                             reasoning_tokens_explicit_sum=sum(x for x in reasoning if x is not None),
                             reasoning_tokens_reported_answers=sum(x is not None for x in reasoning),
                             estimated_usd=sum(r['estimated_usd'] for r in records),
                             mean_latency_seconds=statistics.mean(r['latency_seconds'] for r in records),
                             min_latency_seconds=min(r['latency_seconds'] for r in records),
                             max_latency_seconds=max(r['latency_seconds'] for r in records),
                             response_models=dict(Counter(r['response_model'] for r in records)))
    total = sum(row['estimated_usd'] for row in audit_rows)
    check(math.isclose(total, ledger['estimated_all_http_usd'], rel_tol=1e-12), 'aggregate task costs equal ledger')
    check(ledger['completed_http_attempts'] == 15 and ledger['unknown_http_attempts'] == 0 and not ledger['reservations'], 'budget has fifteen settled receipts and zero unknown/inflight')
    starts = sorted(RESULTS.glob('session_*.start.json'))
    exits = sorted(RESULTS.glob('session_*.exit.json'))
    check(len(starts) == len(exits) == 1 and read(exits[0])['exit_code'] == 0, 'clean process exit')
    start, end = read(starts[0])['started_at'], read(exits[0])['completed_at']
    wall = (datetime.fromisoformat(end) - datetime.fromisoformat(start)).total_seconds()
    after = {str(p.relative_to(RESULTS)): sha(p.read_bytes()) for p in RESULTS.rglob('*') if p.is_file()}
    check(before == after, 'audit left all results unchanged')
    report = dict(audit_pass=True, checks_passed=len(checks), checks=checks,
                  case_id=next(iter(case_ids)), stage='answering_only', total_answers=15,
                  completed_answers=15, scoring_performed=False, started_at=start, completed_at=end,
                  walltime_seconds=wall, by_model=summary, estimated_usd=total,
                  budget_guard_accounted_usd=ledger['accounted_including_prepaid_usd'],
                  budget_usd=ledger['budget_usd'], billing_reconciled=False,
                  billing_note='Costs use provider input/output usage and frozen public price metadata; no final balance poll occurred within the 38-second run. 1.1x guard amount is a safety estimate, not charged cost.',
                  usage_warnings=usage_warnings, rows=audit_rows, evidence_sha256=before)
    unchanged_write(OUT / 'smoke_audit.json', report)
    names = {'gpt-4o': 'GPT-4o', 'gemini-2.5-flash': 'Gemini 2.5 Flash', 'qwen-max': 'Qwen-Max'}
    lines = ['# Primary 三模型回答冒烟结果', '',
             '范围：同一 primary 案件的 5 个问题，3 个模型共 15 份回答；只作答，未评分。', '',
             '| 模型 | 完整回答 | 输入 token | 输出 token | 显式 reasoning token | 估计费用（美元） | 单条平均耗时 |',
             '|---|---:|---:|---:|---|---:|---:|']
    for model in MODELS:
        row = summary[model]
        reason = str(row['reasoning_tokens_explicit_sum']) if row['reasoning_tokens_reported_answers'] == 5 else '未返回'
        lines.append(f"| {names[model]} | {row['answers']}/5 | {row['prompt_tokens']:,} | {row['completion_tokens']:,} | {reason} | ${row['estimated_usd']:.6f} | {row['mean_latency_seconds']:.1f} 秒 |")
    lines.extend(['', f'合计：**15/15 完整回答，估计费用 ${total:.6f}，总耗时 {wall:.1f} 秒**。全量未启动。', '',
                  '输入与历史作答 prompt 一致；温度 0.3、输出上限 3,000，未传 thinking 参数；输入未包含裁判理由。完整问答及原始回执均已保留。', '',
                  '注意：GPT-4o 的接口回执为 `gpt-4o`（2 条）、`gpt-4o-2024-05-13`（2 条）、`gpt-4o-2024-11-20`（1 条）；请求 ID 均为 `gpt-4o`，供应商后台并未返回统一的具体版本。', '',
                  '费用按本次 XHub 价格与接口输入/输出 token 估算，尚非账单对账金额。Gemini 有 2 条回执的 total_tokens 各比输入加输出少 1，保留原值，费用不使用该 total 字段。Qwen 未返回 reasoning token 字段，不将缺失记为 0。', ''])
    unchanged_write(OUT / 'SMOKE_RESULT.md', '\n'.join(lines))
    print(json.dumps({k: report[k] for k in ('audit_pass', 'checks_passed', 'total_answers', 'walltime_seconds', 'estimated_usd', 'by_model', 'usage_warnings')}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
