"""Offline paired format/anchor report; never estimates legal accuracy."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[3]
DEFAULT_MAPPING = REPO / 'data/pkulaw_v4_autotune_20261002/prepared/private_mapping.jsonl'
FIELDS = ('requested', 'complete', 'valid', 'truncated', 'json_errors', 'quote_errors',
          'id_errors', 'other_contract_errors', 'refusal', 'interface_or_unknown',
          'none', 'events', 'quote_events', 'quote_mismatches')


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def read(path):
    return json.loads(Path(path).read_bytes())


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def classify(response, terminal, row, contract, *, new):
    """Replay frozen parser rather than trusting cached validity labels."""
    value = dict.fromkeys(FIELDS, 0)
    value['requested'] = 1
    value['request_id'] = row['request_id']
    value['parsed'] = None
    value['hydrated'] = None
    value['error'] = None
    status = terminal.get('status')
    if status == 'truncated':
        value['truncated'] = 1
        return value
    if status == 'refusal':
        value['refusal'] = 1
        return value
    if not terminal.get('complete'):
        value['interface_or_unknown'] = 1
        return value
    require(isinstance(response, dict) and response.get('model') == 'deepseek_v4_flash', 'Wrong/missing response model')
    require(isinstance(response.get('choices'), list) and len(response['choices']) == 1, 'Wrong response choices')
    choice = response['choices'][0]
    require(choice.get('finish_reason') == 'stop' and not choice['message'].get('refusal'), 'Terminal response mismatch')
    text = choice['message'].get('content')
    require(isinstance(text, str) and text.strip(), 'Missing complete response text')
    value['complete'] = 1
    try:
        parsed = contract.parse_output(text, row['answer'], row['sentences']) if new else contract.parse_output(text, row['answer'])
        value['valid'] = 1
        value['parsed'] = parsed
        if parsed == 'NONE':
            value['none'] = 1
        else:
            value['events'] = len(parsed)
            if new:
                hydrated = contract.hydrate_output(parsed, row['answer'], row['sentences'])
                value['hydrated'] = hydrated
                require(len(hydrated['events']) == len(hydrated['anchors']) == len(parsed), 'Hydration event count mismatch')
                for event, anchor in zip(hydrated['events'], hydrated['anchors']):
                    exact = row['answer'][anchor['start']:anchor['end']]
                    require(exact == event['quote'] and sha(exact.encode()) == anchor['quote_sha256'], 'Hydrated quote mismatch')
                value['quote_events'] = len(parsed)
    except (ValueError, TypeError, KeyError) as error:
        require(not value['valid'], 'Valid parse failed source hydration: ' + str(error))
        value['error'] = str(error)
        if isinstance(error, json.JSONDecodeError):
            value['json_errors'] = 1
        elif not new and 'Quote is not verbatim' in str(error):
            value['quote_errors'] = 1
        elif new and ('sentence ID' in str(error) or 'Sentence IDs' in str(error) or 'sentence_ids' in str(error)):
            value['id_errors'] = 1
        else:
            value['other_contract_errors'] = 1
    require(bool(terminal.get('valid_format')) == bool(value['valid']), 'Stored terminal validity differs from replay')
    return value


def total(records):
    result = {field: sum(item[field] for item in records) for field in FIELDS}
    result['valid_rate_all_requests'] = result['valid'] / result['requested'] if result['requested'] else None
    result['valid_rate_complete_responses'] = result['valid'] / result['complete'] if result['complete'] else None
    result['error_reasons'] = dict(Counter(item['error'] for item in records if item['error']))
    return result


def select_review(events):
    """Up to two events per source condition per endpoint, deterministic ranking."""
    groups = defaultdict(list)
    for event in events:
        groups[event['endpoint'], event['source_condition']].append(event)
    rank = lambda e: sha(json.dumps(e, sort_keys=True, ensure_ascii=False).encode())
    selected = []
    for endpoint in sorted({event['endpoint'] for event in events}):
        available = [item for (host, _), group in groups.items() if host == endpoint for item in group]
        chosen = []
        for (host, source), group in sorted(groups.items()):
            if host == endpoint:
                chosen.extend(sorted(group, key=rank)[:2])
        chosen_keys = {rank(item) for item in chosen}
        if len(chosen) < 10:
            chosen.extend(sorted((item for item in available if rank(item) not in chosen_keys), key=rank)[:10-len(chosen)])
        selected.extend(sorted(chosen, key=rank)[:10])
    return selected


def audit(snapshot, mapping_path):
    snapshot = Path(snapshot).resolve()
    manifest, stage = read(snapshot / 'prepared/manifest.json'), read(snapshot / 'stage_manifest.json')
    for name, digest in stage['files'].items():
        path = snapshot / name
        require(path.resolve().is_relative_to(snapshot), 'Unsafe staged path')
        require(sha(path.read_bytes()) == digest, 'Staged file hash changed: ' + name)
    require(sha((snapshot / 'baseline_paired.json').read_bytes()) == stage['baseline_sha256'], 'Paired baseline hash changed')
    require(sha((snapshot / 'prepared/requests.jsonl').read_bytes()) == manifest['input_sha256'], 'Input changed')
    for name, digest in manifest['code_sha256'].items():
        require(sha((snapshot / 'code' / name).read_bytes()) == digest, 'Frozen code changed')
    require(manifest['request_count'] == 128 and manifest['reference_ready'] is False and manifest['formal_hallucination_statistics_allowed'] is False,
            'Not the authorized nonformal 128-request pilot')
    rows = [json.loads(line) for line in (snapshot / 'prepared/requests.jsonl').read_text().splitlines()]
    records = {row['request_id']: row for row in rows}
    require(len(rows) == len(records) == 128, 'Wrong unique request count')
    require(sha(Path(mapping_path).read_bytes()) == manifest['source_mapping_sha256'], 'Source mapping changed')
    mapping = {item['request_id']: item for item in map(json.loads, Path(mapping_path).read_text().splitlines())}
    require(set(records) <= set(mapping), 'Source mappings missing')
    new_contract = load_module(snapshot / 'code/sentence_contract.py', '_report_sentence_contract')
    old_contract = load_module(snapshot / 'code/contract.py', '_report_old_contract')
    for row in rows:
        require(sha(row['answer'].encode()) == row['answer_sha256'] == mapping[row['request_id']]['answer_sha256'], 'Original answer changed')
        require(sha(row['messages'][1]['content'].encode()) == row['packet_sha256'], 'Delivered packet changed')
        packet = json.loads(row['messages'][1]['content'])
        require(packet['sentence_table'] == row['sentences'], 'Delivered sentence table mismatch')
        new_contract.validate_sentences(row['answer'], row['sentences'])
    baseline = read(snapshot / 'baseline_paired.json')
    for receipt in (baseline['exit'], read(snapshot / 'results/exit.json')):
        require(receipt.get('completed') is True and receipt.get('drained') is True, 'Run is not completed and drained')
    hosts = [urlsplit(url).hostname for url in manifest['endpoints']]
    require(set(hosts) == {'10.33.4.146', '10.33.4.152'}, 'Unexpected endpoints')
    output = {'snapshot': str(snapshot), 'generated_at': datetime.now(timezone.utc).isoformat(),
              'reference_ready': False, 'formal_hallucination_statistics_allowed': False,
              'legal_accuracy_evaluated': False, 'comparison': 'Same 128 source answers per endpoint, 256 paired calls; no comparison to old 1024-all rates',
              'endpoints': {}, 'paired_changes': {}, 'input_sha256': manifest['input_sha256'],
              'baseline_sha256': stage['baseline_sha256']}
    combined = {'old': [], 'new': []}
    reviews = []
    for host in hosts:
        receipt = read(snapshot / 'results' / host / 'exit.json')
        require(receipt.get('completed') is True and receipt.get('drained') is True, 'Endpoint not completed and drained')
        require(set(baseline['endpoints'][host]) == set(records), 'Old paired coverage mismatch')
        terminals = {}
        for path in (snapshot / 'results' / host / 'requests').glob('*/terminal.json'):
            terminal = read(path)
            require(terminal['request_id'] not in terminals, 'Duplicate new terminal')
            terminals[terminal['request_id']] = (path.parent, terminal)
        require(set(terminals) == set(records), 'New paired coverage mismatch')
        pairs = []
        for rid, row in records.items():
            old = baseline['endpoints'][host][rid]
            require(old['terminal']['request_id'] == rid, 'Old request identity mismatch')
            previous = classify(old.get('response'), old['terminal'], row, old_contract, new=False)
            directory, terminal = terminals[rid]
            response_path = directory / 'response.raw.txt'
            response = read(response_path) if response_path.exists() else None
            if response_path.exists():
                http = read(directory / 'response.http.json')
                require(http['raw_sha256'] == sha(response_path.read_bytes()), 'Raw response receipt hash mismatch')
            current = classify(response, terminal, row, new_contract, new=True)
            if current['valid']:
                require(read(directory / 'parsed.native.json') == current['parsed'], 'Cached native result mismatch')
                expected = 'NONE' if current['parsed'] == 'NONE' else current['hydrated']
                require(read(directory / 'hydrated.json') == expected, 'Cached hydrated result mismatch')
                if current['parsed'] != 'NONE':
                    for event, native in zip(current['hydrated']['events'], current['parsed']):
                        reviews.append({'request_id': rid, 'endpoint': host,
                            'source_condition': mapping[rid]['answer_condition'], 'selected_ids': native['sentence_ids'],
                            'quote': event['quote'], 'reason': event['reason'], 'category': event['category'],
                            'correct_content': event['correct_content'], 'original_answer': row['answer']})
            combined['old'].append(previous)
            combined['new'].append(current)
            pairs.append((previous, current))
        output['endpoints'][host] = {version: total([pair[i] for pair in pairs]) for i, version in enumerate(('old', 'new'))}
        output['paired_changes'][host] = dict(Counter(('valid' if old['valid'] else 'invalid') + ' -> ' +
               ('valid' if new['valid'] else 'invalid') for old, new in pairs))
    output['overall'] = {key: total(value) for key, value in combined.items()}
    output['semantic_review_status'] = 'not_reviewed; source anchoring is not semantic or legal correctness'
    samples = select_review(reviews)
    output['semantic_review_sample_count'] = len(samples)
    return output, samples


def markdown(report):
    lines = ['# 句子编号定位：配对重试结果', '',
             '同一批 128 份回答 × 两个 DS 服务；只比较对应的 256 对请求。以下为工程格式和引文定位指标，不是法律判断准确率或幻觉率。', '',
             '| 范围 | 版本 | 请求 | 完整回包 | 严格通过 | 通过率（全部请求） | 通过率（完整回包） | JSON 错误 | 引文/编号错误 | 其他结构错误 | 截断 | NONE |',
             '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|']
    for scope, versions in list(report['endpoints'].items()) + [('合计', report['overall'])]:
        for version in ('old', 'new'):
            x = versions[version]
            pct = lambda value: '—' if value is None else f'{value:.1%}'
            lines.append(f"| {scope} | {'旧引文' if version == 'old' else '新编号'} | {x['requested']} | {x['complete']} | {x['valid']} | {pct(x['valid_rate_all_requests'])} | {pct(x['valid_rate_complete_responses'])} | {x['json_errors']} | {x['quote_errors'] + x['id_errors']} | {x['other_contract_errors']} | {x['truncated']} | {x['none']} |")
    new = report['overall']['new']
    lines += ['', f"新结果中 {new['quote_events']} 个事件的引文由原回答位置回填；逐字不匹配 {new['quote_mismatches']} 个。NONE 不计入有引文事件。",
              '', f"已导出 {report['semantic_review_sample_count']} 个事件用于人工核对“句子是否选对”。法源核验尚未就绪，本结果不进入正式幻觉统计。", '']
    return '\n'.join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--mapping', type=Path, default=DEFAULT_MAPPING)
    args = parser.parse_args()
    report, samples = audit(args.snapshot, args.mapping)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    destination = args.snapshot.resolve().parent.parent / 'reports' / stamp
    destination.mkdir(parents=True, exist_ok=False)
    for name, value in [('paired_report.json', report), ('semantic_review_samples.json', samples)]:
        with (destination / name).open('x') as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write('\n')
    with (destination / 'RESULTS.md').open('x') as handle:
        handle.write(markdown(report))
    print(json.dumps({'report_directory': str(destination), 'overall': report['overall']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
