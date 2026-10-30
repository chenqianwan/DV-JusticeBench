#!/usr/bin/env python3
"""Classify the original 108 cases via XHub DS v3.2; never touches the cluster."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import sys
import threading
import time
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts.local_primary_answers_20260929 import run as shared
from scripts.cross_judge_budget_guard import GuardedTransport, BudgetStop
from scripts.prepare_original108_dv_classification import prepare

MODEL = 'deepseek-v3.2'
INPUT = ROOT / 'data/original108_dv_classification_20261002'
OUT = ROOT / 'data/original108_dv_classification_xhub_20261002'
LABELS = ('primary', 'secondary', 'irrelevant')
DISPUTES = {'是否构成家暴', '人身安全保护令', '离婚事由', '子女抚养影响',
            '精神/物质损害赔偿', '刑事量刑', '其他'}
shared.MODELS = (MODEL,)  # Reuse price validator for this exact singleton route.
read, write, freeze, sha, canonical, now = shared.read, shared.write, shared.freeze, shared.sha, shared.canonical, shared.now


def load_tasks():
    manifest = read(INPUT / 'manifest.json')
    content = (INPUT / 'input_cases.jsonl').read_bytes()
    prompt = (ROOT / 'prompts/dv_relevance_screening_zh.txt').read_bytes()
    source = (ROOT / 'data/cases/cases.json').read_bytes()
    if (sha(content) != manifest['tasks_sha256'] or sha(prompt) != manifest['prompt_sha256']
            or sha(source) != manifest['source_sha256']):
        raise ValueError('Frozen input or original source/prompt changed')
    tasks = [json.loads(line) for line in content.splitlines() if line]
    if tasks != prepare(source, prompt.decode()) or len(tasks) != 108:
        raise ValueError('Prepared 108-case messages differ from exact original substitution')
    return sorted(tasks, key=lambda t: t['case_id']), manifest


def parse_label(content):
    value = content.strip()
    fenced = re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```', value, re.I)
    if fenced:
        value = fenced.group(1)
    obj = json.loads(value)
    if not isinstance(obj, dict) or set(obj) != {'label', 'court_addressed_dv', 'dispute_types', 'confidence', 'reason'}:
        raise ValueError('Classification schema differs')
    if not isinstance(obj['label'], str) or obj['label'] not in LABELS:
        raise ValueError('Unknown label')
    if type(obj['court_addressed_dv']) is not bool:
        raise ValueError('court_addressed_dv must be a JSON boolean')
    if not isinstance(obj['dispute_types'], list) or not all(isinstance(x, str) for x in obj['dispute_types']):
        raise ValueError('dispute_types must be a string list')
    if type(obj['confidence']) not in (int, float) or not 0 <= obj['confidence'] <= 1:
        raise ValueError('Invalid confidence')
    if not isinstance(obj['reason'], str) or not obj['reason'].strip():
        raise ValueError('Missing reason')
    # Keep raw model judgments. Formatting/semantic flags are never silently repaired.
    flags = []
    if len(obj['reason']) > 20:
        flags.append('reason_over_20_characters')
    if any(x not in DISPUTES for x in obj['dispute_types']):
        flags.append('dispute_type_outside_literal_enum')
    if len(obj['dispute_types']) != len(set(obj['dispute_types'])):
        flags.append('duplicate_dispute_type')
    if (obj['label'] == 'primary') != obj['court_addressed_dv']:
        flags.append('raw_label_court_flag_inconsistent_with_strict_prompt')
    return obj, flags


def task_dir(out, task):
    return out / 'results' / ('case_' + sha(task['case_id'])[:24])


def summarize(out):
    tasks, source = load_tasks()
    results = []
    statuses, labels, groups, flags, response_models = Counter(), Counter(), Counter(), Counter(), Counter()
    for task in tasks:
        path = task_dir(out, task) / 'terminal.json'
        if not path.exists():
            statuses['not_started'] += 1
            continue
        result = read(path)
        if result['case_id'] != task['case_id'] or result['messages_sha256'] != task['messages_sha256']:
            raise ValueError('Existing result identity changed')
        results.append(result)
        statuses[result['status']] += 1
        if result['status'] == 'ok':
            label = result['classification']['label']
            labels[label] += 1
            groups[result['engineering_group']] += 1
            flags.update(result['flags'])
            response_models[result.get('response_model')] += 1
    valid = sum(labels.values())
    ledger = read(out / 'budget/budget_ledger.json') if (out / 'budget/budget_ledger.json').exists() else {}
    summary = {'at': now(), 'cases': 108, 'valid_classifications': valid, 'statuses': dict(statuses),
               'raw_label_counts': {k: labels[k] for k in LABELS},
               'raw_label_percent_all_108': {k: labels[k] / 108 * 100 for k in LABELS},
               'engineering_group_counts_separate_only': {k: groups[k] for k in LABELS},
               'flags': dict(flags), 'requested_model': MODEL, 'response_models': dict(response_models),
               'temperature': 0.1, 'thinking': 'disabled', 'initial_max_tokens': 512,
               'length_retry_max_tokens': 1024, 'source_sha256': source['source_sha256'],
               'prompt_sha256': source['prompt_sha256'], 'classification_per_case_not_question': True,
               'cost_list_estimated_usd': ledger.get('estimated_all_http_usd'),
               'cost_conservative_accounted_usd': ledger.get('accounted_including_prepaid_usd'),
               'http_attempts': ledger.get('completed_http_attempts'),
               'human_verified': False}
    write(out / 'summary.json', summary)
    write(out / 'classifications.json', results)
    lines = ['# 原始108案：家暴相关性分类', '',
             f'XHub `{MODEL}`，非思考，temperature=0.1；逐字复用原筛选prompt。完成 {valid}/108 案。', '',
             '| 分类（原始label） | 案件数 | 占全部108案 |', '| --- | ---: | ---: |']
    for label in LABELS:
        lines.append(f'| {label} | {labels[label]} | {labels[label]/108:.2%} |')
    if valid != 108:
        lines.append(f'| 未取得有效分类 | {108-valid} | {(108-valid)/108:.2%} |')
    lines += ['| 合计 | 108 | 100.00% |', '',
              '主表严格采用模型原始label，不按旧工程规则擅自将secondary升级为primary。原prompt辅助字段、置信度、原文回执和异常均保留；该结果是自动分类，未经专家复核。', '',
              '## 逐案结果', '', '| case_id | 标题 | label | 法院实质分析家暴 | 依据 |', '| --- | --- | --- | --- | --- |']
    by_id = {r['case_id']: r for r in results}
    def cell(v):
        return str(v).replace('|', '\\|').replace('\n', ' ')
    for task in tasks:
        result = by_id.get(task['case_id'], {})
        c = result.get('classification') or {}
        lines.append('| ' + ' | '.join(cell(x) for x in [task['case_id'], task['title'],
                     c.get('label', result.get('status', 'not_started')),
                     c.get('court_addressed_dv', ''), c.get('reason', '')]) + ' |')
    lines += ['', '## 可追溯信息', '', f'- 分类schema／辅助字段标记：`{dict(flags)}`。',
              f'- 请求模型：`{MODEL}`；回执model分布：`{dict(response_models)}`。',
              f'- 原材料hash：`{source["source_sha256"]}`；原prompt hash：`{source["prompt_sha256"]}`。',
              '- 旧工程分组另存在summary.json：primary或secondary且court_addressed_dv=true归primary；不改主表raw label。',
              '- 原始材料中含摘要、保护令、裁定等，分类基于项目既有文本，不声称全部为完整判决原件。',
              f'- Token标价估计费用：{summary["cost_list_estimated_usd"]} USD（非最终扣费账单）。', '']
    # This is a generated report, not an edit to a pre-existing manuscript.
    (out / '原始108案_primary_secondary_irrelevant统计.md').write_text('\n'.join(lines), encoding='utf-8')
    return summary


def execute(args):
    tasks, source = load_tasks()
    market = read(args.market)
    base, key = shared.credentials(shared.ENV)
    host = urlsplit(base).hostname
    no_proxy = [x for x in os.environ.get('NO_PROXY', os.environ.get('no_proxy', '')).split(',') if x]
    os.environ['NO_PROXY'] = os.environ['no_proxy'] = ','.join(dict.fromkeys(no_proxy + [host]))
    shared.validate_market(market, base)
    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    with (out / 'run.lock').open('a+') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        identity = {'model': MODEL, 'temperature': .1, 'thinking': {'type': 'disabled'},
                    'initial_max_tokens': 512, 'length_retry_max_tokens': 1024,
                    'max_attempts': 3, 'timeout_s': 300, 'workers': 8, 'budget_usd': 5.0,
                    'budget_note': 'Internal conservative stop, not a claim of user-specified budget.',
                    'input_sha256': source['tasks_sha256'], 'prompt_sha256': source['prompt_sha256'],
                    'runner_sha256': sha(Path(__file__).read_bytes()),
                    'market_sha256': sha(args.market.read_bytes())}
        if (out / 'identity.json').exists() != (args.command == 'resume'):
            raise ValueError('New queue requires run; existing queue requires explicit resume')
        freeze(out / 'identity.json', identity)
        starts = sorted(out.glob('session_*.start.json'))
        if starts and not starts[-1].with_name(starts[-1].name.replace('.start.', '.exit.')).exists():
            raise ValueError('Previous process lacks actual exit receipt; reconcile first')
        pending = []
        for task in tasks:
            directory = task_dir(out, task)
            terminal = directory / 'terminal.json'
            if terminal.exists():
                existing = read(terminal)
                if existing['messages_sha256'] != task['messages_sha256']:
                    raise ValueError('Terminal input differs')
                continue  # Every terminal, including refusal/unknown, stays preserved.
            if list(directory.glob('attempt_*.request.json')):
                raise ValueError('Unresolved sent intent; never blindly resend')
            pending.append(task)
        pending = pending[:args.limit] if args.limit else pending
        session = 'session_' + shared.datetime.now(shared.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        write(out / (session + '.start.json'), dict(at=now(), **shared.process_identity()), True)
        stop = threading.Event()
        for sig in [signal.SIGINT, signal.SIGTERM]:
            signal.signal(sig, lambda *_: stop.set())
        code = 1
        try:
            transport = GuardedTransport(base, key, out / 'budget', market['prices_usd_per_million'],
                                         market['quota_per_unit'], 5.0, prepaid_usd=0)

            def process(task):
                if stop.is_set():
                    return None
                directory = task_dir(out, task)
                freeze(directory / 'input.json', task)
                maximum, length_retry = 512, False
                status, classification, flags = 'interface_error', None, []
                last = {}
                attempts = 0
                for attempt in range(1, 4):
                    if stop.is_set():
                        break
                    attempts = attempt
                    payload = {'model': MODEL, 'messages': task['messages'], 'temperature': .1,
                               'max_tokens': maximum, 'thinking': {'type': 'disabled'}}
                    write(directory / f'attempt_{attempt:03d}.request.json',
                          dict(at=now(), payload=payload, payload_sha256=sha(canonical(payload))), True)
                    start = time.monotonic()
                    try:
                        response = transport.post(base + '/chat/completions', json=payload,
                                    headers={'Authorization': 'Bearer ' + key}, timeout=300)
                        raw = response.text.replace(key, '[REDACTED]')
                        try:
                            parsed = json.loads(raw)
                        except ValueError:
                            parsed = None
                        last = dict(http_status=response.status_code, raw_body=raw, raw_response=parsed,
                                    headers={k.lower():v for k,v in response.headers.items()
                                             if k.lower() in ('retry-after','x-request-id','request-id')})
                    except BudgetStop:
                        last = dict(not_sent=True, error='budget_guard_stop')
                        stop.set()
                    except Exception as exc:
                        last = dict(transport_error=type(exc).__name__, error=str(exc).replace(key, '[REDACTED]'))
                        stop.set()
                    last.update(at=now(), elapsed_s=time.monotonic()-start)
                    write(directory / f'attempt_{attempt:03d}.response.json', last, True)
                    if last.get('not_sent'):
                        status = 'not_sent_budget'; break
                    response_status, content, finish = shared.classify(last)
                    status = response_status
                    if status == 'needs_reconcile':
                        stop.set(); break
                    if status == 'truncated' and not length_retry and attempt < 3:
                        maximum, length_retry = 1024, True
                        continue
                    if status == 'ok':
                        try:
                            classification, flags = parse_label(content)
                            break
                        except (ValueError, TypeError) as exc:
                            status = 'parse_error'
                            write(directory / f'attempt_{attempt:03d}.parse_error.json',
                                  {'type': type(exc).__name__, 'error': str(exc)}, True)
                    if status in ('refusal','content_filter','truncated'):
                        break
                    if last.get('http_status') in (401, 403):
                        stop.set(); break
                    if attempt < 3:
                        time.sleep(5 if last.get('http_status') == 429 else 2)
                if not attempts:
                    return None
                body = last.get('raw_response') or {}
                group = None
                if classification:
                    group = 'primary' if classification['label'] == 'primary' or (
                        classification['label'] == 'secondary' and classification['court_addressed_dv']) else classification['label']
                result = {'case_id': task['case_id'], 'messages_sha256': task['messages_sha256'],
                          'status': status, 'classification': classification, 'engineering_group': group,
                          'flags': flags, 'requested_model': MODEL,
                          'response_model': body.get('model') if isinstance(body, dict) else None,
                          'response_id': body.get('id') if isinstance(body, dict) else None,
                          'attempts': attempts, 'length_retry_used': length_retry, 'at': now()}
                write(directory / 'terminal.json', result, True)
                return result

            iterator = iter(pending)
            with ThreadPoolExecutor(max_workers=8) as pool:
                active = {pool.submit(process, t) for t in list(next(iterator, None) for _ in range(min(8, len(pending)))) if t}
                while active:
                    done, active = wait(active, return_when=FIRST_COMPLETED)
                    for future in done:
                        result = future.result()
                        if result:
                            print(json.dumps({'case_id': result['case_id'], 'status':result['status'],
                                              'label':(result['classification'] or {}).get('label')},ensure_ascii=False),flush=True)
                        if not stop.is_set():
                            task = next(iterator, None)
                            if task:
                                active.add(pool.submit(process, task))
            code = 2 if stop.is_set() else 0
        finally:
            write(out / (session + '.exit.json'), {'at':now(), 'exit_code':code, 'pid':os.getpid()}, True)
        return summarize(out)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run','resume','status'])
    parser.add_argument('--out',type=Path,default=OUT)
    parser.add_argument('--market',type=Path)
    parser.add_argument('--limit',type=int)
    args = parser.parse_args()
    if args.command != 'status' and args.market is None:
        parser.error('--market is required for paid requests')
    print(json.dumps(summarize(args.out) if args.command=='status' else execute(args),ensure_ascii=False,indent=2))
