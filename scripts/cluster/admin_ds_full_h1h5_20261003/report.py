"""Independent, offline, streaming audit of full provisional H1-H5 outputs.

Reads a completed downloaded snapshot; never invokes inference or changes the
frozen run. --allow-partial labels unresolved and untouched slots explicitly.
Counts are candidate-format/evidence-location counts, not verified hallucination
rates. NONE is never promoted to a verified negative.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import re

TOTAL, REUSE_COUNT = 55236, 128
HOSTS = ('10.33.4.146', '10.33.4.152')
SOURCE_SHA = 'f8907a506663fcf5204c7b1a588fca4bad16dc2c942cc62f129cd01252c97aa2'
EXCLUDED_SHA = '325950f643d38aa9fc81497aa8ad7ca5e2619c49d9efdd55b488cfdefd563686'
DIRTY_SHA = 'f936878f02a61aa99fcf10eb94efc23155066bba2c42d0ab532a772cdd4ced8a'
KEYS = ('split', 'case_id', 'question_id', 'answer_condition')
CODE = ('runner.py', 'pilot_runner.py', 'bench_external.py', 'bench.py', 'contract.py', 'sentence_contract.py')
FILES = {'code/' + name for name in (*CODE, 'manager.py')} | {
    'prepared/' + name for name in ('requests_146.jsonl', 'requests_152.jsonl', 'private_mapping.jsonl', 'reused.jsonl', 'manifest.json')}
FLAGS = {'provisional': True, 'reference_ready': False, 'legal_truth_verified': False,
         'formal_hallucination_statistics_allowed': False, 'none_is_verified_negative': False,
         'semantic_alignment_verified': False}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    value = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return value.hexdigest()


def safe(root, relative):
    require(isinstance(relative, str) and relative and not Path(relative).is_absolute(), 'Expected relative evidence path')
    result = root / relative
    require(result.resolve() == result and result.is_relative_to(root) and '..' not in Path(relative).parts,
            'Evidence path escapes snapshot or traverses symlinks')
    return result


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def parse_json(raw):
    def invalid(value):
        raise ValueError('Nonfinite JSON constant: ' + value)
    return json.loads(raw, object_pairs_hook=unique_keys, parse_constant=invalid)


def read(path):
    return parse_json(Path(path).read_bytes())


def rows(path):
    with Path(path).open('rb') as source:
        for line in source:
            require(bool(line.strip()), 'Blank JSONL line')
            row = parse_json(line)
            require(isinstance(row, dict), 'JSONL row must be object')
            yield row


def load_module(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def authenticate(snapshot):
    """Verify all frozen staged bytes before importing the frozen parser."""
    manifest = read(safe(snapshot, 'prepared/manifest.json'))
    stage = read(safe(snapshot, 'stage_manifest.json'))
    require(set(stage.get('files', {})) == FILES, 'Unexpected staged file inventory')
    for name, digest in stage['files'].items():
        require(file_sha(safe(snapshot, name)) == digest, 'Staged artifact changed: ' + name)
    require(manifest.get('state') == 'frozen_not_sent', 'Manifest was not frozen before staging')
    require(manifest.get('protocol_id') == 'dvj-H1-H5-full-sentence-id-provisional-v1'
            and manifest.get('input_sha256') == SOURCE_SHA
            and manifest.get('excluded_sha256') == EXCLUDED_SHA
            and manifest.get('dirty_cases_sha256') == DIRTY_SHA
            and manifest.get('excluded_slots') == 239 and manifest.get('permanently_excluded_cases') == 18,
            'Manifest source/exclusion protocol differs from authorized corpus')
    require(manifest.get('request_count') == TOTAL and set(manifest.get('shards', {})) == set(HOSTS), 'Wrong corpus scope')
    require(manifest.get('provisional') is True and manifest.get('reference_ready') is False
            and manifest.get('legal_truth_verified') is False
            and manifest.get('formal_hallucination_statistics_allowed') is False, 'Unsafe protocol readiness flags')
    require(set(manifest.get('code_sha256', {})) == set(CODE), 'Wrong frozen dependency set')
    for name, digest in manifest['code_sha256'].items():
        require(stage['files']['code/' + name] == digest, 'Manifest/stage code digest mismatch')
    for item in list(manifest['shards'].values()) + [manifest['private_mapping'], manifest['reused']]:
        require(stage['files'].get('prepared/' + item['path']) == item['sha256'], 'Manifest/stage input digest mismatch')
    require(manifest['sentence_contract_sha256'] == manifest['code_sha256']['sentence_contract.py'], 'Sentence contract digest mismatch')
    require(manifest['reused']['count'] == REUSE_COUNT, 'Unexpected reuse count')
    require(all(manifest['shards'][h]['count'] == TOTAL // 2 for h in HOSTS), 'Unbalanced shard size')
    require(manifest.get('parameters') == {'model': 'deepseek_v4_flash', 'temperature': 0.1,
                'max_tokens': 3000, 'chat_template_kwargs': {'thinking': False}}
            and manifest.get('concurrency_per_endpoint') == 128 and manifest.get('retries') == 0
            and manifest.get('length_retry') is False and manifest.get('request_deadline_s') == 1800,
            'Scientific or dispatch parameters differ from the frozen protocol')
    return manifest, stage


def mappings(snapshot, manifest):
    result, indexes, identities = {}, set(), set()
    for row in rows(safe(snapshot, 'prepared/' + manifest['private_mapping']['path'])):
        key = tuple(row.get(name) for name in KEYS)
        require(all(isinstance(x, str) and x for x in key), 'Incomplete private identity')
        rid, index = row.get('request_id'), row.get('global_index')
        require(rid == 'perf_' + sha(canonical(list(key))), 'Private identity/hash mismatch')
        require(type(index) is int and 0 <= index < TOTAL and row.get('source_line') == index + 1, 'Invalid global index')
        require(rid not in result and index not in indexes and key not in identities, 'Duplicate full-corpus identity')
        require(row['host'] in HOSTS and key[0] in ('primary', 'secondary'), 'Unknown shard/split')
        result[rid] = row
        indexes.add(index)
        identities.add(key)
    require(len(result) == manifest['private_mapping']['count'] == TOTAL and indexes == set(range(TOTAL)), 'Incomplete private coverage')
    return result


def reuse_rows(snapshot, manifest, mapping):
    result = {}
    for item in rows(safe(snapshot, 'prepared/' + manifest['reused']['path'])):
        rid = item['request_id']
        require(rid not in result and rid in mapping, 'Duplicate/unknown pilot reuse')
        for field in ('host', 'global_index', 'answer_sha256', 'packet_sha256'):
            require(item[field] == mapping[rid][field], 'Pilot reuse is not the assigned endpoint/input')
        require(item['terminal']['request_id'] == rid and item['terminal'].get('sent_intent') is True, 'Pilot terminal identity mismatch')
        result[rid] = item
    require(len(result) == REUSE_COUNT, 'Pilot reuse coverage differs')
    return result


def session_evidence(snapshot, mapping, reused, allow_partial):
    """Index only paths, not response bodies; never infer that a sent gap is safe."""
    by_index = {row['global_index']: rid for rid, row in mapping.items()}
    history, summaries, all_drained = {}, [], True
    root = safe(snapshot, 'sessions')
    directories = sorted(root.iterdir()) if root.exists() else []
    for directory in directories:
        safe(snapshot, str(directory.relative_to(snapshot)))
        require(directory.is_dir(), 'Unexpected session artifact')
        start = read(directory / 'start.json')
        require(start.get('session') == directory.name
                and start.get('manifest_sha256') == file_sha(snapshot / 'prepared/manifest.json'), 'Session start manifest mismatch')
        receipt_path, round_path = directory / 'exit.json', directory / 'round.json'
        receipt = read(receipt_path) if receipt_path.exists() else None
        complete_exit = bool(receipt and receipt.get('drained') is True and receipt.get('local_futures_finished') is True)
        all_drained &= complete_exit
        if not allow_partial:
            require(complete_exit, 'Final report requires real finished/drained session exit')
        if receipt and 'round_sha256' in receipt:
            require(round_path.exists() and sha(canonical(read(round_path))) == receipt['round_sha256'], 'Session round/exit hash mismatch')
        for host in HOSTS:
            hostroot = directory / host
            if not hostroot.exists():
                require(allow_partial, 'Missing final endpoint session')
                all_drained = False
                continue
            er = read(hostroot / 'exit.json') if (hostroot / 'exit.json').exists() else None
            host_drained = bool(er and er.get('drained') is True and er.get('local_futures_finished') is True)
            all_drained &= host_drained
            if not allow_partial:
                require(host_drained, 'Final report requires drained endpoint exit')
            if er and 'round_sha256' in er:
                require(sha(canonical(read(hostroot / 'round.json'))) == er['round_sha256'], 'Endpoint round/exit hash mismatch')
            requests = hostroot / 'requests'
            for path in requests.iterdir() if requests.exists() else []:
                safe(snapshot, str(path.relative_to(snapshot)))
                match = re.fullmatch(r'(\d{6})_([0-9a-f]{16})', path.name)
                require(match is not None and path.is_dir(), 'Malformed request evidence directory')
                rid = by_index.get(int(match[1]))
                require(rid in mapping and sha(rid.encode())[:16] == match[2] and mapping[rid]['host'] == host,
                        'Request directory does not match assigned input')
                require(rid not in reused and rid not in history, 'Duplicate attempt or rerun of reused input')
                history[rid] = path
        summaries.append({'session': directory.name, 'exit': receipt, 'real_drained_exit': complete_exit})
    latest_path = snapshot / 'latest_session.json'
    latest = read(latest_path)['session'] if latest_path.exists() else None
    require(latest is None or latest in {x.name for x in directories}, 'Latest session is absent from snapshot')
    latest_exit = next((row['exit'] for row in summaries if row['session'] == latest), None)
    final_receipt = bool(all_drained and latest_exit and latest_exit.get('corpus_complete') is True
                         and latest_exit.get('all_inputs_accounted') is True)
    if not allow_partial:
        require(final_receipt, 'Final report requires real corpus-complete, fully accounted exit')
    return history, summaries, final_receipt


def validate_input(row, meta, manifest, contract):
    for field in ('request_id', 'global_index', 'answer_sha256', 'packet_sha256'):
        require(row[field] == meta[field], 'Shard/private identity mismatch')
    require(sha(row['answer'].encode()) == row['answer_sha256'], 'Original answer digest mismatch')
    contract.validate_sentences(row['answer'], row['sentences'])
    messages = row['messages']
    require(len(messages) == 2 and all(set(m) == {'role', 'content'} for m in messages)
            and [m['role'] for m in messages] == ['system', 'user'], 'Wrong blind messages')
    require(sha(messages[0]['content'].encode()) == manifest['prompt_sha256'], 'Changed sentence prompt')
    require(sha(messages[1]['content'].encode()) == row['packet_sha256'], 'Changed delivered packet')
    packet = parse_json(messages[1]['content'])
    require(set(packet) == {'case_text', 'question', 'judge_decision', 'reference_pack', 'sentence_table'}, 'Nonblind packet fields')
    require(packet['sentence_table'] == row['sentences'], 'Delivered sentence map differs')
    require(packet['reference_pack'] == {'evidence': [], 'checklists': [], 'coverage': {}}, 'Reference readiness changed')
    for field, key in [('case_text', 'masked_case_text_hash'), ('question', 'question_hash'), ('judge_decision', 'judge_decision_hash')]:
        require(sha(packet[field].encode()) == meta[key], 'Original scientific input changed: ' + field)


def raw_files(directory):
    terminal = read(directory / 'terminal.json') if (directory / 'terminal.json').exists() else None
    intent = read(directory / 'intent.json') if (directory / 'intent.json').exists() else None
    raw = directory / 'response.raw.txt'
    if not raw.exists():
        raw = directory / 'response.partial.raw.txt'
    raw_bytes = raw.read_bytes() if raw.exists() else None
    http = read(directory / 'response.http.json') if (directory / 'response.http.json').exists() else None
    native = read(directory / 'parsed.native.json') if (directory / 'parsed.native.json').exists() else None
    hydrated = read(directory / 'hydrated.json') if (directory / 'hydrated.json').exists() else None
    return terminal, intent, raw_bytes, http, native, hydrated


def inspect(row, meta, manifest, contract, bench, *, reuse=None, directory=None):
    output = {name: meta[name] for name in (*KEYS, 'request_id', 'global_index', 'host', 'answer_sha256', 'packet_sha256')}
    output.update(FLAGS)
    output.update(source='pilot_reuse' if reuse else ('full_run' if directory else 'unstarted'),
                  audit_state='unstarted', terminal_status='not_started', event_count=0,
                  native=None, hydrated=None, literal_quotes_exact=None, observed_raw_parse=None)
    if not reuse and directory is None:
        return output
    if reuse:
        terminal, intent = reuse['terminal'], None
        raw, http, native, hydrated = reuse['response_raw'].encode(), reuse['http'], reuse['native'], reuse['hydrated']
        provenance = reuse['provenance']
        require(provenance.get('reference_ready') is False and provenance.get('legal_truth_verified') is False,
                'Unsafe pilot reuse readiness')
        output['source_evidence'] = provenance
    else:
        terminal, intent, raw, http, native, hydrated = raw_files(directory)
        output['source_evidence'] = {'directory': str(directory), 'file_sha256': {
            p.name: file_sha(p) for p in directory.iterdir() if p.is_file()}}
        if intent:
            require(intent.get('request_id') == row['request_id']
                    and intent.get('answer_sha256') == row['answer_sha256']
                    and intent.get('packet_sha256') == row['packet_sha256'], 'Intent/input hash mismatch')
            require(intent['payload'] == dict(manifest['parameters'], messages=row['messages'])
                    and intent['payload_sha256'] == sha(canonical(intent['payload']))
                    and intent['sentences_sha256'] == sha(canonical(row['sentences'])), 'Actual payload differs from frozen input')
        require(terminal is None or intent is not None, 'Terminal lacks original request intent')
    if terminal:
        require(terminal.get('request_id') == row['request_id'] and terminal.get('sent_intent') is True, 'Terminal identity mismatch')
        output['terminal_status'] = terminal['status']
        output['terminal_sha256'] = sha(canonical(terminal))
    else:
        output['terminal_status'] = 'missing_terminal_needs_reconcile'
    output['audit_state'] = 'needs_reconcile'
    if raw is None or http is None:
        require(not terminal or not terminal.get('complete'), 'Complete terminal lacks raw receipt')
        output['reconciliation_reason'] = 'No complete, hash-bound response receipt; may be in flight or interrupted'
        return output
    require(http.get('raw_sha256') == sha(raw), 'Raw response receipt digest mismatch')
    output['response_raw_sha256'] = sha(raw)
    if http.get('body_complete') is not True:
        require(not terminal or not terminal.get('complete'), 'Complete terminal has partial HTTP body')
        output['reconciliation_reason'] = 'Partial HTTP body; outcome unresolved'
        return output
    parsed = {}
    def parser(text, answer):
        parsed['native'] = contract.parse_output(text, answer, row['sentences'])
        parsed['hydrated'] = contract.hydrate_output(parsed['native'], answer, row['sentences'])
        return parsed['native']
    replay = bench.inspect_response(raw.decode('utf-8', errors='replace'), row['answer'], status=http['status'], parser=parser)
    output['observed_raw_parse'] = replay
    if not terminal or 'reconcile' in terminal['status']:
        # A response that reached disk but lacks a matching terminal can be
        # recovered later; this aggregator does not invent that missing receipt.
        output['reconciliation_reason'] = 'Raw receipt exists, but final terminal requires reconciliation'
        return output
    for key in ('status', 'complete', 'valid_format', 'http_status', 'finish_reason', 'response_model', 'output_tokens'):
        if key in replay:
            require(terminal.get(key) == replay[key], 'Stored terminal differs from raw replay: ' + key)
    if replay['valid_format']:
        require(native == parsed['native'] and hydrated == parsed['hydrated'], 'Cached parsed/hydrated output differs from replay')
        output['native'], output['hydrated'] = parsed['native'], parsed['hydrated']
        if parsed['native'] == 'NONE':
            output['audit_state'] = 'none_candidate'
            require(terminal.get('event_count') == 0, 'NONE terminal has hallucination events')
        else:
            output['audit_state'] = 'events_candidate'
            output['event_count'] = len(parsed['native'])
            require(terminal.get('event_count') == output['event_count'], 'Terminal event count mismatch')
            for event, anchor in zip(parsed['hydrated']['events'], parsed['hydrated']['anchors']):
                quote = row['answer'][anchor['start']:anchor['end']]
                require(quote == event['quote'] and sha(quote.encode()) == anchor['quote_sha256']
                        and anchor['answer_sha256'] == row['answer_sha256'], 'Hydrated literal quote/answer digest mismatch')
            output['literal_quotes_exact'] = True
    else:
        output['audit_state'] = {'complete_invalid_format': 'invalid_format', 'truncated': 'truncated',
            'refusal': 'refusal', 'http_error': 'interface_failure', 'protocol_error': 'protocol_failure',
            'non_stop': 'non_stop'}.get(replay['status'], 'needs_reconcile')
        require(native is None and hydrated is None, 'Invalid terminal has accepted cached output')
    return output


def count_record(summary, record):
    summary['slots'] += 1
    summary['statuses'][record['audit_state']] += 1
    summary['sources'][record['source']] += 1
    summary['candidate_events'] += record['event_count']
    if record['audit_state'] == 'events_candidate':
        summary['candidate_categories'].update(event['category'] for event in record['native'])


def empty_summary():
    return {'slots': 0, 'statuses': Counter(), 'sources': Counter(), 'candidate_events': 0, 'candidate_categories': Counter()}


def audit(snapshot, destination, *, allow_partial=False):
    snapshot, destination = Path(snapshot).resolve(), Path(destination).resolve()
    require(snapshot.is_dir(), 'Missing snapshot')
    require(not destination.exists(), 'Refuse overwriting an existing report')
    require(not destination.is_relative_to(snapshot), 'Report output must not mutate frozen snapshot')
    manifest, stage = authenticate(snapshot)
    mapping = mappings(snapshot, manifest)
    reused = reuse_rows(snapshot, manifest, mapping)
    history, sessions, final_receipt = session_evidence(snapshot, mapping, reused, allow_partial)
    contract = load_module(snapshot / 'code/sentence_contract.py', '_full_report_sentence_contract')
    bench = load_module(snapshot / 'code/bench.py', '_full_report_frozen_bench')
    destination.mkdir(parents=True, exist_ok=False)
    names = ('provisional_results.jsonl', 'needs_reconcile.jsonl', 'unstarted.jsonl')
    handles = {name: (destination / name).open('xb') for name in names}
    digests = {name: hashlib.sha256() for name in names}
    written = Counter()
    def emit(name, record):
        raw = canonical(record) + b'\n'
        handles[name].write(raw)
        digests[name].update(raw)
        written[name] += 1
    overall, by_group, seen = empty_summary(), defaultdict(empty_summary), set()
    try:
        for host in HOSTS:
            count = 0
            for row in rows(snapshot / 'prepared' / manifest['shards'][host]['path']):
                rid = row['request_id']
                require(rid in mapping and rid not in seen and mapping[rid]['host'] == host, 'Unknown/duplicate/cross-shard request')
                validate_input(row, mapping[rid], manifest, contract)
                record = inspect(row, mapping[rid], manifest, contract, bench, reuse=reused.get(rid), directory=history.get(rid))
                emit('provisional_results.jsonl', record)
                if record['audit_state'] == 'needs_reconcile':
                    emit('needs_reconcile.jsonl', record)
                if record['audit_state'] == 'unstarted':
                    emit('unstarted.jsonl', record)
                count_record(overall, record)
                count_record(by_group[record['split'], record['answer_condition']], record)
                seen.add(rid)
                count += 1
            require(count == manifest['shards'][host]['count'], 'Actual shard count differs')
        require(len(seen) == TOTAL and seen == set(mapping), 'Final unique coverage mismatch')
        if not allow_partial:
            require(overall['statuses']['needs_reconcile'] == overall['statuses']['unstarted'] == 0,
                    'Cannot emit final report while unknown/unstarted slots remain')
    except BaseException as error:
        # Preserve diagnostic partial exports, but never let them resemble an
        # accepted final report. A later retry gets a fresh output directory.
        with (destination / 'AUDIT_FAILED.json').open('x') as failed:
            json.dump({'at': datetime.now(timezone.utc).isoformat(), 'accepted_final_report': False,
                       'error_type': type(error).__name__, 'reason': str(error),
                       'rows_written': dict(written), **FLAGS}, failed, ensure_ascii=False, indent=2)
            failed.write('\n')
        raise
    finally:
        for stream in handles.values():
            stream.close()
    result = {'generated_at': datetime.now(timezone.utc).isoformat(), 'snapshot': str(snapshot),
        'report_state': 'partial_provisional_snapshot' if allow_partial else 'completed_provisional_audit',
        'real_final_complete_drained_receipt': final_receipt, **FLAGS,
        'overall': overall, 'by_split_condition': [dict(split=g[0], answer_condition=g[1], **v) for g, v in sorted(by_group.items())],
        'sessions': sessions, 'manifest_sha256': file_sha(snapshot / 'prepared/manifest.json'),
        'stage_manifest_sha256': file_sha(snapshot / 'stage_manifest.json'),
        'output_files': {name: {'sha256': digests[name].hexdigest(), 'rows': written[name]} for name in names},
        'excluded_original_slots': manifest.get('excluded_slots'),
        'excluded_dirty_cases': manifest.get('permanently_excluded_cases'),
        'notes': ['All event quotations re-extracted and checked against original-answer character offsets.',
                  'Source-model labels come from private mapping, never judge messages.',
                  'No verified hallucination rate or verified negative count is calculated.',
                  'Partial missing terminals may still be in flight; unknown does not imply safe retry.']}
    with (destination / 'summary.json').open('x') as stream:
        json.dump(result, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')
    lines = ['# 全量 H1–H5 候选标注结果', '',
        '以下为候选标注与接口状态统计，不是已核验幻觉率；NONE 不代表已确认无幻觉。', '',
        '| 分组 | 回答模型 | 槽位 | 候选事件回答 | NONE 候选 | 格式异常 | 截断 | 其他异常 | 未知/待核对 | 未开始 |',
        '|---|---|---:|---:|---:|---:|---:|---:|---:|---:|']
    for row in result['by_split_condition']:
        s = row['statuses']
        other = sum(v for k, v in s.items() if k not in {'events_candidate', 'none_candidate', 'invalid_format', 'truncated', 'needs_reconcile', 'unstarted'})
        lines.append(f"| {row['split']} | {row['answer_condition']} | {row['slots']} | {s['events_candidate']} | {s['none_candidate']} | {s['invalid_format']} | {s['truncated']} | {other} | {s['needs_reconcile']} | {s['unstarted']} |")
    lines += ['', '报告状态：' + result['report_state'], '', '所有原始回执和异常均保留，未重新调用模型。', '']
    with (destination / 'RESULTS.md').open('x') as stream:
        stream.write('\n'.join(lines))
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--allow-partial', action='store_true')
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.output or args.snapshot.resolve().parent.parent / 'reports' / stamp
    report = audit(args.snapshot, output, allow_partial=args.allow_partial)
    print(json.dumps({'output': str(output), 'report_state': report['report_state'], 'overall': report['overall']}, ensure_ascii=False))
