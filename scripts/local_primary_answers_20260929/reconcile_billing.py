#!/usr/bin/env python3
"""Read-only XHub billing reconciliation for the frozen fifteen-call pilot."""
from collections import defaultdict
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run as pilot


def epoch(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def main():
    base, key = pilot.credentials(pilot.ENV)
    parsed = urlsplit(base)
    origin = parsed.scheme + '://' + parsed.netloc

    def get(path, auth=False):
        response = requests.get(origin + path,
            headers={'Authorization': 'Bearer ' + key} if auth else {},
            timeout=25, allow_redirects=False)
        if response.status_code != 200:
            raise ValueError(f'Billing GET failed: {path} HTTP {response.status_code}')
        body = response.json()
        if not isinstance(body, dict) or body.get('success') is False:
            raise ValueError('Billing GET unsuccessful: ' + path)
        return body['data']

    unit = Decimal(str(get('/api/status')['quota_per_unit']))
    assert unit > 0
    logs = get('/api/log/token', True)
    assert isinstance(logs, list)
    balance = get('/api/usage/token/', True)
    root = pilot.OUT / 'pilot_results'
    ledger = pilot.read(root / 'budget_ledger.json')
    assert unit == Decimal(str(ledger['quota_per_unit']))
    tasks = pilot.load_pilot(pilot.OUT)
    local = []
    hashes = {}
    for task in tasks:
        directory = root / pilot.task_id(task)
        paths = sorted(directory.glob('attempt_*.response.json'))
        assert len(paths) == 1 and pilot.read(directory / 'terminal.json')['status'] == 'ok'
        p = paths[0]
        req_path = p.with_name(p.name.replace('.response.', '.request.'))
        request, response = pilot.read(req_path), pilot.read(p)
        assert response['http_status'] == 200
        usage = response['usage']
        local.append(dict(model=task['model'], question_id=task['question_id'],
                          prompt_tokens=usage['prompt_tokens'], completion_tokens=usage['completion_tokens'],
                          started_at=request['created_at'], completed_at=response['completed_at']))
        for path in (req_path, p):
            hashes[str(path.relative_to(pilot.OUT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    start, end = min(epoch(r['started_at']) for r in local), max(epoch(r['completed_at']) for r in local)
    # This endpoint may ignore date filters; filter locally. Never save unrelated logs.
    window = [r for r in logs if r.get('type') == 2 and start - 3 <= r.get('created_at', 0) <= end + 3]
    matches, matched_ids = [], set()
    numeric_fields = ('model_ratio', 'completion_ratio', 'group_ratio', 'cache_tokens',
                      'cache_ratio', 'cache_creation_tokens', 'cache_creation_ratio')
    for row in local:
        found = [r for r in window if r.get('model_name') == row['model']
                 and r.get('prompt_tokens') == row['prompt_tokens']
                 and r.get('completion_tokens') == row['completion_tokens']
                 and epoch(row['started_at']) - 3 <= r.get('created_at', 0) <= epoch(row['completed_at']) + 3]
        assert len(found) == 1, 'Billing row must uniquely match model, tokens and time'
        bill = found[0]
        rid = bill['request_id']
        assert rid not in matched_ids
        matched_ids.add(rid)
        other = bill.get('other') or {}
        if isinstance(other, str):
            other = json.loads(other)
        details = {k: v for k, v in other.items() if k in numeric_fields and isinstance(v, (int, float))}
        quota = bill['quota']
        assert isinstance(quota, int) and quota >= 0
        matches.append(dict(**row, billing_request_id=rid, billed_at=bill['created_at'],
                            quota=quota, actual_usd=str(Decimal(quota) / unit), billing_details=details))
    assert len(matches) == len(window) == 15, 'Unexpected additional/missing calls in pilot window'
    grouped = defaultdict(lambda: {'calls': 0, 'quota': 0, 'prompt_tokens': 0, 'completion_tokens': 0})
    for row in matches:
        for field, value in [('calls', 1), ('quota', row['quota']),
                             ('prompt_tokens', row['prompt_tokens']), ('completion_tokens', row['completion_tokens'])]:
            grouped[row['model']][field] += value
    for row in grouped.values():
        row['actual_usd'] = str(Decimal(row['quota']) / unit)
    used = balance['total_used']
    debit_quota = used - ledger['initial_used_quota']
    quota = sum(row['quota'] for row in matches)
    report = dict(checked_at=datetime.now(timezone.utc).isoformat(),
        sources=['/api/status', '/api/usage/token/', '/api/log/token'],
        matching_method='Unique model + exact input/output token counts + per-request time window (3-second tolerance)',
        quota_per_unit=str(unit), matched_calls=len(matches), by_model=dict(grouped), total_quota=quota,
        actual_usd=str(Decimal(quota) / unit), estimated_usd=ledger['estimated_all_http_usd'],
        initial_used_quota=ledger['initial_used_quota'], current_used_quota=used,
        observed_key_debit_usd=str(Decimal(debit_quota) / unit),
        logs_equal_key_debit=quota == debit_quota,
        available_quota=balance['total_available'], available_usd=str(Decimal(balance['total_available']) / unit),
        local_receipt_hashes=hashes, matched_billing_rows=matches, model_generation_calls=0)
    output = pilot.OUT / ('billing_reconciliation_' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ') + '.json')
    pilot.write(output, report, exclusive=True)
    print(json.dumps({**{k: report[k] for k in ('checked_at', 'matched_calls', 'by_model', 'actual_usd',
                      'estimated_usd', 'observed_key_debit_usd', 'logs_equal_key_debit', 'available_usd')},
                      'report': str(output)}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try:
        main()
    except Exception as error:
        print(json.dumps({'failed': True, 'error_type': type(error).__name__}))
        raise SystemExit(1)
