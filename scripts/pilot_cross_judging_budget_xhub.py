#!/usr/bin/env python3
"""Recheck frozen judges on 15 historical answers, logging every HTTP attempt.

No answer/question/masking generation. The legacy evaluation code is imported
unchanged. A separate transport guard accounts for truncation and empty retries.
Without --execute this only freezes the pilot manifest (no paid requests).
"""
from __future__ import annotations

import argparse
import hashlib
import inspect
import json
import sys
import threading
import time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from urllib.parse import urlsplit

import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import run_experiment1_smoke_xhub as legacy_transport
from scripts.run_cross_family_judging_sample20_xhub import (
    JUDGES, DIMENSIONS, atomic_write_json, file_sha256, load_json, now_iso,
    result_is_valid, run_new_task, task_id,
)
from utils.evaluator import AnswerEvaluator

SOURCE = ROOT / 'data/experiment1_cross_judge_sample40_xhub_20260822/combined_results.json'
DEFAULT_OUT = ROOT / 'data/cross_judge_budget_pilot_20260908'


class BudgetStop(RuntimeError):
    pass


def payload_for(answer):
    class Capture:
        provider = 'deepseek'
        def analyze_case(self, case_text, question=None, use_thinking=False):
            self.messages = legacy_transport.analysis_messages(case_text, question)
            return {'answer': '', 'thinking': ''}
    cap = Capture()
    AnswerEvaluator(api=cap, flag_parser_version='legacy')._call_evaluation_api(
        answer['answer'], answer['masked_judgment'], answer['question'], answer['masked_case_text'])
    return cap.messages


class GuardedTransport:
    """Reserve a conservative cost before EACH physical legacy HTTP attempt."""
    def __init__(self, base, key, out, prices, quota_unit, budget):
        parsed = urlsplit(base)
        self.origin = f'{parsed.scheme}://{parsed.netloc}'
        self.base, self.key, self.out = base, key, out
        self.headers = {'Authorization': 'Bearer ' + key}
        self.prices, self.unit, self.budget = prices, quota_unit, budget
        self.lock = threading.Lock()
        self.path = out / 'budget_ledger.json'
        if self.path.exists():
            self.state = load_json(self.path)
            if self.state['budget_usd'] != budget:
                raise BudgetStop('Budget differs from frozen run')
            if self.state['reservations']:
                raise BudgetStop('Unresolved previous HTTP requests; reconcile before resuming')
        else:
            self.state = {'started_at': now_iso(), 'initial_used_quota': self.used_quota(),
                          'budget_usd': budget, 'reservations': {}, 'next_id': 1,
                          'estimated_all_http_usd': 0.0}
            atomic_write_json(self.path, self.state)

    def __getattr__(self, name):
        return getattr(requests, name)

    def used_quota(self):
        response = requests.get(self.origin + '/api/usage/token/', headers=self.headers, timeout=20)
        response.raise_for_status()
        data = response.json()['data']
        if 'total_used' not in data:
            raise BudgetStop('Missing quota counter')
        return int(data['total_used'])

    def post(self, url, **kwargs):
        payload = kwargs['json']
        price = self.prices[payload['model']]
        # UTF-8 bytes upper-bound ordinary text token counts. The 2x margin also
        # allows for hidden reasoning billing; settled quota is checked each time.
        input_upper = len(json.dumps(payload['messages'], ensure_ascii=False).encode()) + 1024
        reserve = 2 * (input_upper * price[0] + payload['max_tokens'] * price[1]) / 1e6
        with self.lock:
            # Metering endpoints themselves rate-limit. Query account totals at
            # batch boundaries only; reserve 2x list cost for every paid attempt.
            accounted = max(self.state.get('observed_debit_usd', 0),
                            2 * self.state['estimated_all_http_usd'])
            pending = sum(self.state['reservations'].values())
            if accounted + pending + reserve > self.budget:
                raise BudgetStop('Pilot budget guard: insufficient unreserved budget')
            call_id = f"http_{self.state['next_id']:04d}"
            self.state['next_id'] += 1
            self.state['reservations'][call_id] = reserve
            atomic_write_json(self.path, self.state)
        started = time.monotonic()
        receipt = {'call_id': call_id, 'started_at': now_iso(), 'request': payload,
                   'reserved_usd': reserve, 'response': None, 'usage': {}}
        completed = False
        try:
            response = requests.post(url, **kwargs)
            receipt['http_status'] = response.status_code
            try:
                body = response.json()
                receipt['response'] = body
                receipt['usage'] = body.get('usage') or {}
            except ValueError:
                receipt['response_text'] = response.text[:1000].replace(self.key, '[REDACTED]')
            completed = True
            return response
        except Exception as exc:
            receipt['error'] = (type(exc).__name__ + ': ' + str(exc)).replace(self.key, '[REDACTED]')
            raise
        finally:
            receipt['latency_seconds'] = round(time.monotonic() - started, 3)
            usage = receipt['usage']
            cost = (usage.get('prompt_tokens', 0) * price[0] + usage.get('completion_tokens', 0) * price[1]) / 1e6
            receipt['list_estimated_usd'] = cost
            atomic_write_json(self.out / 'http_receipts' / f'{call_id}.json', receipt)
            with self.lock:
                self.state['estimated_all_http_usd'] += cost
                # Unknown completion/cost keeps the reservation; it is never free.
                if completed and (usage or receipt.get('http_status', 0) >= 400):
                    self.state['reservations'].pop(call_id, None)
                atomic_write_json(self.path, self.state)


def freeze_manifest(source, out, market):
    old = load_json(source)
    pool = old['answers']
    groups = defaultdict(list)
    for a in pool:
        groups[(a['case_id'], a['question_number'])].append(a)
    ranked = sorted(groups, key=lambda k: (sum(len(json.dumps(payload_for(a), ensure_ascii=False)) for a in groups[k]), k))
    selected, seen = [], set()
    for fraction in (0.2, 0.5, 0.9):
        pos = round(fraction * (len(ranked) - 1))
        key = next(k for k in ranked[pos:] + ranked[:pos] if k[0] not in seen)
        selected.extend(groups[key]); seen.add(key[0])
    tasks = []
    for a in selected:
        for label, judge in JUDGES.items():
            if judge['family'] == a['answer_family']:
                continue
            tid = task_id(a['answer_id'], label)
            tasks.append({'task_id': tid, 'answer_id': a['answer_id'], 'case_id': a['case_id'],
                          'question_number': a['question_number'], 'answer_condition': a['answer_condition'],
                          'answer_family': a['answer_family'], 'judge_label': label,
                          'judge_family': judge['family'], 'judge_model_id': judge['model_id'],
                          'judge_thinking': judge['thinking'], 'source': 'new_api_pilot',
                          **{k: a[k] for k in ['input_hash','question_sha256','answer_sha256','masked_case_sha256','masked_judgment_sha256']},
                          'messages_sha256': legacy_transport.sha256_text(json.dumps(payload_for(a), ensure_ascii=False, sort_keys=True)),
                          'historical_rating_path': old['rating_files'][tid]})
    hashes = {
        'evaluation_prompt_source_sha256': legacy_transport.sha256_text(inspect.getsource(AnswerEvaluator._call_evaluation_api)),
        'legacy_flag_parser_source_sha256': legacy_transport.sha256_text(inspect.getsource(AnswerEvaluator._detect_flags_legacy)),
        'legacy_penalty_source_sha256': legacy_transport.sha256_text(inspect.getsource(AnswerEvaluator._apply_penalty_for_flags)),
    }
    previous = load_json(ROOT / 'data/experiment1_cross_judge_sample20_xhub_20260822/manifest.json')
    assert all(previous[k] == v for k, v in hashes.items()), 'Historical scoring code hash changed'
    manifest = {'created_at': now_iso(), 'source_sha256': file_sha256(source),
                'population_cases': 108, 'prior_cross_judged_cases': 40, 'pilot_cases': 3,
                'pilot_answers': 15, 'pilot_ratings': 45,
                'selection': 'one case-question group at prompt-character-length P20, P50, P90, five answer conditions each; distinct cases; no selection by score',
                'parameters': {'temperature': 0.3, 'max_tokens': 3000, 'parser': 'legacy', 'legacy_retries': 'unchanged; budget guard on every physical HTTP attempt'},
                'hashes': hashes, 'tasks': tasks}
    target = out / 'manifest.json'
    if target.exists():
        stored = load_json(target)
        assert stored['tasks'] == tasks and stored['hashes'] == hashes and stored['source_sha256'] == manifest['source_sha256']
        manifest = stored
    else:
        atomic_write_json(target, manifest)
        atomic_write_json(out / 'source_answers.json', selected)
        atomic_write_json(out / 'market_snapshot.json', market)
    return manifest, selected


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument('--execute', action='store_true')
    ap.add_argument('--xhub-env', type=Path, default=ROOT.parent / 'AI_Council/.env')
    ap.add_argument('--output-dir', type=Path, default=DEFAULT_OUT)
    ap.add_argument('--pilot-budget-usd', type=float, default=5.0)
    ap.add_argument('--workers', type=int, default=3)
    args = ap.parse_args()
    if not 0 < args.pilot_budget_usd <= 5:
        ap.error('Pilot cap must be positive and at most $5; overall study budget is $100')
    cfg = dotenv_values(args.xhub_env)
    base = cfg['UNIFIED_API_BASE_URL'].rstrip('/'); key = cfg['UNIFIED_API_KEY']
    p = urlsplit(base); origin = f'{p.scheme}://{p.netloc}'
    def get(path, auth=False):
        r = requests.get(origin + path, headers={'Authorization': 'Bearer '+key} if auth else {}, timeout=25)
        r.raise_for_status(); return r.json()
    catalog = get('/v1/models', True)
    pricing = get('/api/pricing')
    status = get('/api/status')['data']
    ids = {x['id'] for x in catalog['data']}
    required = {v['model_id'] for v in JUDGES.values()}
    assert required <= ids, f'Missing: {required - ids}'
    relevant = {d['model_name']: d for d in pricing['data'] if d['model_name'] in required}
    prices = {}
    for name in sorted(required):
        spec = relevant[name]
        assert spec['quota_type'] == 0
        rate = spec['model_ratio'] * 1e6 / status['quota_per_unit']
        prices[name] = (rate, rate * spec['completion_ratio'])
    market = {'checked_at': now_iso(), 'endpoint': base, 'catalog_present': sorted(required),
              'quota_per_unit': status['quota_per_unit'], 'public_default_group_prices_usd_per_million': prices,
              'pricing_entries': relevant, 'group_ratio': pricing.get('group_ratio')}
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest, answers = freeze_manifest(SOURCE, args.output_dir, market)
    print(json.dumps({'phase':'frozen', 'cases':3, 'answers':15, 'ratings':45, 'models':sorted(required), 'prices':prices}, ensure_ascii=False), flush=True)
    if not args.execute:
        return 0
    frozen_market = load_json(args.output_dir / 'market_snapshot.json')
    assert frozen_market['public_default_group_prices_usd_per_million'] == {k: list(v) for k,v in prices.items()}, 'Prices changed; reconcile frozen estimate before paid requests'
    transport = GuardedTransport(base, key, args.output_dir, prices, status['quota_per_unit'], args.pilot_budget_usd)
    legacy_transport.requests = transport
    client = legacy_transport.XHubClient(key, base, timeout=180)
    lookup = {a['answer_id']: a for a in answers}
    pending = [t for t in manifest['tasks'] if not result_is_valid(args.output_dir / 'ratings' / f"{t['task_id']}.json", t)]
    def worker(task):
        result = run_new_task(task, lookup[task['answer_id']], client)
        historical = load_json(Path(task['historical_rating_path']))
        result['historical_score'] = historical['evaluation']['总分']
        result['historical_response_model'] = historical.get('scoring', {}).get('response_model')
        # Preserve failed attempts from previous interrupted/limited runs.
        target = args.output_dir / 'ratings' / f"{task['task_id']}.json"
        if target.exists():
            old_result = load_json(target)
            archive = args.output_dir / 'prior_attempts' / f"{task['task_id']}_{time.time_ns()}.json"
            atomic_write_json(archive, old_result)
        atomic_write_json(args.output_dir / 'ratings' / f"{task['task_id']}.json", result)
        return result
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(worker,t) for t in pending]
        for count, future in enumerate(as_completed(jobs), 1):
            result = future.result()
            print(json.dumps({'done_this_run':count, 'pending_this_run':len(pending), 'judge':result['judge_label'],
                              'status':result['status'], 'tokens':(result.get('scoring') or {}).get('usage'),
                              'score':result.get('evaluation',{}).get('总分')},ensure_ascii=False), flush=True)
    summary = {'finished_at': now_iso(), 'pilot_budget_usd': args.pilot_budget_usd,
               'initial_used_quota': transport.state['initial_used_quota'],
               'quota_per_unit': transport.unit}
    try:
        summary['final_used_quota'] = transport.used_quota()
        summary['observed_key_debit_usd'] = (summary['final_used_quota'] - summary['initial_used_quota']) / transport.unit
    except requests.RequestException as exc:
        summary['observed_key_debit_usd'] = None
        summary['metering_error'] = type(exc).__name__
    summary['all_http_list_estimate_usd'] = transport.state['estimated_all_http_usd']
    summary['ratings_success'] = sum(result_is_valid(args.output_dir / 'ratings' / f"{t['task_id']}.json",t) for t in manifest['tasks'])
    atomic_write_json(args.output_dir / 'summary.json', summary)
    print(json.dumps(summary,ensure_ascii=False),flush=True)
    return 0 if summary['ratings_success'] == 45 else 2


if __name__ == '__main__':
    raise SystemExit(main())
