#!/usr/bin/env python3
"""Read-only audit of completed benchmark evidence; write one concise report."""
import argparse
from collections import Counter
import json
import math
from pathlib import Path

import benchmark_concurrency as benchmark
import run_answer_smoke as base

LOCAL = Path(__file__).resolve().parents[3] / 'data/pkulaw_concurrency_benchmark_20260927'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def number_equal(actual, expected, label):
    if expected is None:
        require(actual is None, label + ': expected unavailable value')
    else:
        require(isinstance(actual, (int, float)) and math.isclose(actual, expected, rel_tol=1e-8, abs_tol=1e-8),
                label + ': numeric mismatch')


def read(path):
    return json.loads(path.read_bytes())


def observation_issues(samples):
    """Missing telemetry is not evidence of stability."""
    issues = set()
    if not samples:
        return ['无采样']
    critical = ('running', 'waiting', 'kv_cache_usage', 'preemptions_total')
    gpu_fields = ('utilization_pct', 'memory_used_mib', 'memory_total_mib', 'power_w', 'temperature_c')
    for sample in samples:
        if sample.get('healthy') is not True or sample.get('health_status') != 200:
            issues.add('健康检查缺失或失败')
        metrics = sample.get('metrics') or {}
        if sample.get('metrics_status') != 200 or sample.get('metrics_error') or any(
                not isinstance(metrics.get(k), (int, float)) or not math.isfinite(metrics[k]) for k in critical):
            issues.add('关键metrics不可得')
        gpus = sample.get('gpus') or []
        observed = len(gpus) == 8 and len({g.get('index') for g in gpus}) == 8
        for gpu in gpus:
            try:
                observed = observed and bool(gpu.get('name')) and all(math.isfinite(float(gpu[k])) for k in gpu_fields)
            except (KeyError, TypeError, ValueError):
                observed = False
        if (not observed or sample.get('gpu_returncode') != 0 or sample.get('gpu_error')
                or sample.get('gpu_anomalies')):
            issues.add('8卡GPU观测不完整或存在错误')
    return sorted(issues)


def audit_round(directory, data, input_hash):
    summary, identity = read(directory / 'summary.json'), read(directory / 'run_identity.json')
    concurrency = int(directory.name[1:])
    require(concurrency in benchmark.CONCURRENCIES, 'Unexpected round directory')
    require(identity['input_sha256'] == input_hash, 'Frozen input hash mismatch')
    require(identity['script_sha256'] == base.digest(Path(benchmark.__file__).read_bytes()), 'Runner source changed')
    require(identity['helper_sha256'] == base.digest(Path(base.__file__).read_bytes()), 'Helper source changed')
    require(identity['parameters'] == benchmark.CONFIG, 'Runner parameters differ')
    require(identity['concurrency'] == concurrency and identity['tasks'] == 2 * concurrency, 'Round identity mismatch')
    require(summary['run_id'] == data['run_id'] and summary['concurrency'] == concurrency, 'Summary identity mismatch')
    require(summary['formal_experiment'] is False and summary['output_cap'] == 256 and summary['automatic_retries'] == 0,
            'Benchmark/formal experiment boundary mismatch')
    tasks = data['tasks'][:2 * concurrency]
    expected_directories = {'task_' + base.digest(t['task_id'].encode())[:24] for t in tasks}
    require(all(p.name in expected_directories for p in directory.glob('task_*')), 'Unexpected task directory')
    rows, usage, valid_tokens, intents, receipts, unfinished = [], Counter(), 0, 0, 0, 0
    for task in tasks:
        task_dir = directory / ('task_' + base.digest(task['task_id'].encode())[:24])
        requests = list(task_dir.glob('attempt_*.request.json'))
        responses = list(task_dir.glob('attempt_*.response.json'))
        require(len(requests) <= 1 and len(responses) <= 1, 'Benchmark request unexpectedly retried')
        terminal_path = task_dir / 'terminal.json'
        if not requests:
            require(not responses and not terminal_path.exists(), 'Orphan response/terminal')
            continue
        intents += 1
        require(requests[0].name == 'attempt_001.request.json', 'Unexpected attempt number')
        request = read(requests[0])
        expected_payload = dict(model=benchmark.CONFIG['model'], messages=task['messages'], max_tokens=256,
                                temperature=.3, chat_template_kwargs={'thinking': False}, stream=False)
        require(request['payload'] == expected_payload, 'Request prompt/parameters modified')
        if not responses or not terminal_path.exists():
            unfinished += 1
            receipts += len(responses)
            require(summary.get('unfinished_request_intents', 0) > 0, 'Completed round lacks per-request evidence')
            continue
        receipts += 1
        response, terminal = read(responses[0]), read(terminal_path)
        require(responses[0].name == 'attempt_001.response.json', 'Unexpected response attempt')
        body = response.get('raw_response')
        if body is not None:
            require(json.loads(response['raw_body']) == body, 'Parsed/raw response mismatch')
        body = body if isinstance(body, dict) else {}
        require(response.get('usage') == body.get('usage'), 'Receipt usage differs from raw response')
        status, content, finish = base.classify(response)
        if status == 'truncated' and content.strip():
            status = 'ok_length'
        elif status == 'ok':
            status = 'ok_stop'
        if status in ('ok_stop', 'ok_length') and body.get('model') != benchmark.CONFIG['model']:
            status = 'error'
        require(terminal['status'] == status, 'Terminal status differs from raw response')
        require(terminal['finish_reason'] == finish, 'Finish reason mismatch')
        require(terminal['http_status'] == response.get('http_status'), 'HTTP status mismatch')
        require(terminal['response_id'] == body.get('id') and terminal['response_model'] == body.get('model'),
                'Response identity mismatch')
        require(terminal['request_id'] == response.get('headers', {}).get('x-request-id'), 'Request ID mismatch')
        require(all(terminal[k] == task[k] for k in ('task_id', 'case_id', 'question_id', 'prompt_hash')),
                'Terminal task provenance mismatch')
        require(terminal['formal_experiment_answer'] is False and terminal['maximum_output_tokens'] == 256
                and terminal['automatic_retries'] == 0, 'Terminal experiment boundary mismatch')
        require(terminal['output_chars'] == len(content), 'Output character count mismatch')
        require(terminal['usage'] == (response.get('usage') or {}), 'Terminal usage mismatch')
        number_equal(terminal['latency_s'], response.get('elapsed_s'), 'Request latency')
        for key in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            value = terminal['usage'].get(key, 0)
            require(isinstance(value, (int, float)) and value >= 0, 'Invalid token count')
            usage[key] += value
        if status in ('ok_stop', 'ok_length'):
            valid_tokens += terminal['usage'].get('completion_tokens', 0) or 0
        rows.append(terminal)
    require(summary.get('unfinished_request_intents', 0) == unfinished, 'Unfinished request count mismatch')
    counts = Counter(row['status'] for row in rows)
    successful = counts['ok_stop'] + counts['ok_length']
    require(summary['counts'] == dict(counts), 'Status counts mismatch')
    expected_counts = {'intended_tasks': 2 * concurrency, 'completed': len(rows), 'successful': successful,
                       'failed': len(rows) - successful, 'not_dispatched': 2 * concurrency - len(rows),
                       'request_intents': intents, 'recorded_responses': receipts, 'valid_completion_tokens': valid_tokens}
    for key, value in expected_counts.items():
        require(summary[key] == value, 'Summary count mismatch: ' + key)
    require(dict(usage) == summary['usage_totals'], 'Aggregate token usage mismatch')
    number_equal(summary['success_rate'], successful / len(rows) if rows else None, 'Success rate')
    number_equal(summary['intended_completion_rate'], len(rows) / (2 * concurrency), 'Completion rate')
    elapsed = summary['elapsed_s']
    require(isinstance(elapsed, (int, float)) and elapsed >= 0, 'Invalid wall time')
    number_equal(summary['completion_tokens_per_s'], valid_tokens / elapsed if elapsed else None, 'Output throughput')
    number_equal(summary['successful_requests_per_s'], successful / elapsed if elapsed else None, 'Successful RPS')
    number_equal(summary['completed_requests_per_s'], len(rows) / elapsed if elapsed else None, 'Completed RPS')
    latencies = [r['latency_s'] for r in rows if isinstance(r['latency_s'], (int, float))]
    number_equal(summary['p50_latency_s'], benchmark.percentile(latencies, .5), 'Latency P50')
    number_equal(summary['p95_latency_s'], benchmark.percentile(latencies, .95), 'Latency P95')
    require(0 <= summary['max_inflight'] <= concurrency, 'Impossible client concurrency')
    samples = [read(path) for path in sorted(directory.glob('sample_*.json'))]
    require(len(samples) == summary['samples'], 'Sample count mismatch')
    for sample in samples:
        if 'metrics_raw' in sample:
            require(benchmark.parse_metrics(sample['metrics_raw']) == sample['metrics'], 'Metrics parse mismatch')
    metrics = [sample.get('metrics', {}) for sample in samples]
    for source, target in [('kv_cache_usage', 'kv_cache_peak'), ('running', 'server_running_peak'),
                           ('waiting', 'server_waiting_peak')]:
        values = [m[source] for m in metrics if m.get(source) is not None]
        number_equal(summary[target], max(values) if values else None, target)
    for source, target in [('preemptions_total', 'preemption_delta'), ('prefix_cache_queries', 'prefix_cache_queries_delta'),
                           ('prefix_cache_hits', 'prefix_cache_hits_delta')]:
        values = [m[source] for m in metrics if m.get(source) is not None]
        number_equal(summary[target], values[-1] - values[0] if len(values) >= 2 else None, target)
    require(summary['gpu_anomalies'] == sorted({v for sample in samples for v in sample.get('gpu_anomalies', [])}),
            'GPU anomaly summary mismatch')
    temperatures, powers = [], []
    for sample in samples:
        gpu_powers = []
        for gpu in sample.get('gpus', []):
            try:
                temperatures.append(float(gpu['temperature_c']))
                gpu_powers.append(float(gpu['power_w']))
            except (ValueError, KeyError):
                continue
        if len(gpu_powers) == 8:
            powers.append(sum(gpu_powers))
    return dict(summary, audited=True, gpu_peak_total_power_w=max(powers) if powers else None,
                gpu_peak_temperature_c=max(temperatures) if temperatures else None,
                observation_issues=observation_issues(samples))


def fmt(value, digits=1):
    return '—' if value is None else f'{value:.{digits}f}'


def render(rows, manifest):
    by_c = {row['concurrency']: row for row in rows}
    baseline = by_c.get(16, {}).get('completion_tokens_per_s')
    lines = ['# V4 Flash 并发短输出压测', '',
             '固定条件：单节点 8 张 H800；原回答 prompt；非思考；temperature=0.3；每请求最多输出 256 token。以下均已核验逐请求回执，不属于正式实验答案。', '',
             '| 并发 | 请求成功/计划 | 输出 token/s | 相对16倍率 | P50/P95秒 | KV峰值 | 前缀缓存命中 | 抢占增量 | 服务运行峰值 | 8卡峰值功耗W/最高℃ |',
             '|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for concurrency in benchmark.CONCURRENCIES:
        row = by_c.get(concurrency)
        if row is None:
            lines.append(f'| {concurrency} | 未测或尚未完成 | — | — | — | — | — | — | — | — |')
            continue
        rate = row['completion_tokens_per_s']
        ratio = rate / baseline if rate is not None and baseline else None
        kv = fmt(row['kv_cache_peak'] * 100) + '%' if row['kv_cache_peak'] is not None else '—'
        hits, queries = row.get('prefix_cache_hits_delta'), row.get('prefix_cache_queries_delta')
        cache_ratio = (hits / queries if isinstance(hits, (int, float)) and isinstance(queries, (int, float))
                       and queries > 0 and 0 <= hits <= queries else None)
        cache = fmt(cache_ratio * 100) + '%' if cache_ratio is not None else '—'
        lines.append(f"| {concurrency} | {row['successful']}/{row['intended_tasks']} | {fmt(rate)} | {fmt(ratio, 2)} | "
                     f"{fmt(row['p50_latency_s'])}/{fmt(row['p95_latency_s'])} | {kv} | {cache} | {fmt(row['preemption_delta'], 0)} | "
                     f"{fmt(row['server_running_peak'], 0)} | {fmt(row['gpu_peak_total_power_w'], 0)}/{fmt(row['gpu_peak_temperature_c'], 0)} |")
    lines.extend(['', '## 并发建议', ''])
    candidates = [r for r in rows if r['successful'] == r['intended_tasks'] and not r['stop_reasons']
                  and not r['gpu_anomalies'] and r['preemption_delta'] == 0 and r['completion_tokens_per_s'] is not None
                  and r.get('observation_issues') == [] and r['kv_cache_peak'] is not None and r['kv_cache_peak'] < .65]
    if candidates:
        best = max(candidates, key=lambda r: (r['completion_tokens_per_s'], -r['concurrency']))
        lines.append(f"已测档位中，**并发 {best['concurrency']}** 在全部成功、无停止/抢占/GPU异常、每次健康与关键metrics及8卡GPU观测完整、KV峰值<65%的条件下短测吞吐最高（{fmt(best['completion_tokens_per_s'])} token/s），建议作为下一步完整长度验证的候选；不自动切换正式实验。")
    elif rows:
        lines.append('现有轮次尚无同时满足“全部成功、无停止/抢占/GPU异常、每次健康与关键metrics及8卡GPU观测完整、KV峰值<65%”的可核实候选；缺少观测不能认定稳定，不建议据此提高正式并发。')
    else:
        lines.append('尚无完成轮次；暂不作并发或吞吐结论。')
    issues = [f"并发{r['concurrency']}：{', '.join(r['stop_reasons']) or '无停止'}；失败{r['failed']}；GPU异常{', '.join(r['gpu_anomalies']) or '无已记录异常'}；观测{', '.join(r['observation_issues']) or '完整'}"
              for r in rows if r['failed'] or r['stop_reasons'] or r['gpu_anomalies'] or r['observation_issues']]
    if issues:
        lines.extend([''] + ['- ' + issue for issue in issues])
    lengths = []
    for row in rows:
        record = manifest.get('rounds', {}).get(str(row['concurrency']), {})
        if 'mean_fact_chars' in record and 'max_fact_chars' in record:
            lengths.append(f"{row['concurrency']}并发={record['mean_fact_chars']}/{record['max_fact_chars']}")
    lines.extend(['', '## 解释边界', '',
                  '- 每档请求数为 2×并发，输入长度分布并非完全相同；跨档复用前缀，后续轮次可能受暖缓存影响，倍率不是严格等负载加速比。',
                  '- 前缀缓存命中比例=本轮hits增量/queries增量；计数缺失、重置或queries为0时记为“—”。',
                  '- `length/max_tokens` 是本次256-token压测的正常终止，不代表回答完整；这些文本不得混入论文正式结果。',
                  '- 本次只测非思考短输出，不能直接代表完整回答、thinking评分吞吐或长期稳定性；评分耗时尚无本次实测依据。',
                  '- 服务运行峰值来自约5秒一次采样，可能漏掉瞬时峰值；功耗为8卡总功耗采样峰值，温度为单卡最高采样值；“—”表示不可得。'])
    if lengths:
        lines.append('- 各档案情平均/最长字符数：' + '；'.join(lengths) + '。')
    return '\n'.join(lines) + '\n'


def generate(root):
    raw = (root / 'input_benchmark.json').read_bytes()
    data, manifest = json.loads(raw), read(root / 'input_manifest.json')
    benchmark.validate(data)
    input_hash = base.digest(raw)
    require(manifest['input_sha256'] == input_hash, 'Input manifest hash mismatch')
    rows = []
    for concurrency in benchmark.CONCURRENCIES:
        directory = root / 'results' / ('c%03d' % concurrency)
        if (directory / 'summary.json').exists():
            rows.append(audit_round(directory, data, input_hash))
    return render(rows, manifest), rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, default=LOCAL)
    args = parser.parse_args()
    report, rows = generate(args.root)
    path = args.root / 'BENCHMARK_REPORT.md'
    base.atomic(path, report, raw=True)
    print(json.dumps({'report': str(path), 'audited_rounds': [r['concurrency'] for r in rows]}, ensure_ascii=False))


if __name__ == '__main__':
    main()
