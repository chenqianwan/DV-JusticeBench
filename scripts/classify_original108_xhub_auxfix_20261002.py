#!/usr/bin/env python3
"""Preserve valid three-way labels despite invalid auxiliary confidence; no prompt edits."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import classify_original108_xhub_20261002 as base

PILOT = base.OUT
OUT = ROOT / 'data/original108_dv_classification_xhub_auxfix_20261002'
STRICT_PARSE = base.parse_label
ORIGINAL_FREEZE = base.freeze
ORIGINAL_TRANSPORT = base.GuardedTransport
ORIGINAL_SUMMARIZE = base.summarize


def parse_label(content):
    value = content.strip()
    match = re.fullmatch(r'```(?:json)?\s*([\s\S]*?)\s*```', value, re.I)
    obj = json.loads(match.group(1) if match else value)
    if not isinstance(obj, dict) or 'confidence' not in obj:
        return STRICT_PARSE(content)
    confidence = obj['confidence']
    valid = type(confidence) in (int, float) and 0 <= confidence <= 1
    validation_copy = dict(obj, confidence=confidence if valid else 0.0)
    _, flags = STRICT_PARSE(json.dumps(validation_copy, ensure_ascii=False))
    if not valid:
        flags.append('auxiliary_confidence_invalid_raw_value_preserved')
    # Validation-only placeholder above is never persisted as a model value.
    return obj, flags


def pilot_info():
    identity = base.read(PILOT / 'identity.json')
    if identity['runner_sha256'] != base.sha(Path(base.__file__).read_bytes()):
        raise ValueError('Frozen pilot implementation changed')
    sessions = sorted(PILOT.glob('session_*.start.json'))
    if not sessions or not sessions[-1].with_name(sessions[-1].name.replace('.start.', '.exit.')).exists():
        raise ValueError('Pilot has no real exit receipt')
    ledger = base.read(PILOT / 'budget/budget_ledger.json')
    return ledger


def reuse_pilot(out):
    import fcntl
    out.mkdir(parents=True, exist_ok=True)
    with (PILOT / 'run.lock').open('a+') as source_lock, (out / 'run.lock').open('a+') as target_lock:
        fcntl.flock(source_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(target_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        pilot_info()
        tasks, _ = base.load_tasks()
        for task in tasks:
            old_dir = base.task_dir(PILOT, task)
            if not (old_dir / 'terminal.json').exists():
                continue
            old = base.read(old_dir / 'terminal.json')
            if old['messages_sha256'] != task['messages_sha256']:
                raise ValueError('Pilot input mismatch')
            selected = None
            for response_path in sorted(old_dir.glob('attempt_*.response.json')):
                raw = base.read(response_path)
                status, content, _ = base.shared.classify(raw)
                if status != 'ok':
                    continue
                try:
                    classification, flags = parse_label(content)
                except (ValueError, TypeError):
                    continue
                selected = (response_path, raw, classification, flags)
                break  # First complete schema with a valid label; never select by label outcome.
            if selected is None:
                raise ValueError('Pilot has no reusable classification; needs independent recovery')
            path, raw, classification, flags = selected
            result = dict(old, status='ok', classification=classification, flags=flags,
                response_model=raw['raw_response'].get('model'), response_id=raw['raw_response'].get('id'),
                engineering_group='primary' if classification['label']=='primary' or (
                    classification['label']=='secondary' and classification['court_addressed_dv']) else classification['label'],
                recovery_kind='offline_reparse_auxiliary_confidence_only',
                source_response=str(path), source_response_sha256=base.sha(path.read_bytes()),
                source_terminal=str(old_dir / 'terminal.json'),
                source_terminal_sha256=base.sha((old_dir / 'terminal.json').read_bytes()))
            directory = base.task_dir(out, task)
            ORIGINAL_FREEZE(directory / 'input.json', task)
            ORIGINAL_FREEZE(directory / 'terminal.json', result)


def install_adapter():
    pilot = pilot_info()
    def freeze(path, value):
        if path.name == 'identity.json':
            value = dict(value, auxiliary_adapter_sha256=base.sha(Path(__file__).read_bytes()),
                auxiliary_policy='Preserve raw invalid confidence with a flag; raw label unchanged.',
                inherited_pilot_accounted_usd=pilot['accounted_including_prepaid_usd'],
                inherited_pilot_estimated_usd=pilot['estimated_all_http_usd'])
        return ORIGINAL_FREEZE(path, value)

    def transport(*args, **kwargs):
        if kwargs.get('prepaid_usd') != 0:
            raise ValueError('Unexpected prepaid value')
        kwargs['prepaid_usd'] = pilot['accounted_including_prepaid_usd']
        return ORIGINAL_TRANSPORT(*args, **kwargs)

    def summarize(out):
        summary = ORIGINAL_SUMMARIZE(out)
        summary['cost_list_estimated_usd_new_calls_only'] = summary['cost_list_estimated_usd']
        summary['cost_list_estimated_usd'] = (summary['cost_list_estimated_usd'] or 0) + pilot['estimated_all_http_usd']
        summary['http_attempts_new_calls_only'] = summary['http_attempts']
        summary['http_attempts'] = (summary['http_attempts'] or 0) + pilot['completed_http_attempts']
        summary['pilot_source'] = str(PILOT)
        summary['confidence_policy'] = 'Invalid confidence excluded from confidence analyses, not from raw label counts.'
        base.write(out / 'summary.json', summary)
        report = out / '原始108案_primary_secondary_irrelevant统计.md'
        text = report.read_text()
        text = re.sub(r'- Token标价估计费用：[^\n]*',
            f'- Token标价估计费用（含首案冒烟）：{summary["cost_list_estimated_usd"]:.6f} USD（非最终扣费账单）。', text)
        text += '\n辅助字段中的非法置信度保留原值并标记，不用于置信度分析，不改变三类原始标签统计。首案复用首个可解析的原始回执，没有重新付费调用。\n'
        report.write_text(text)
        return summary

    base.freeze, base.GuardedTransport, base.parse_label, base.summarize = freeze, transport, parse_label, summarize


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['run', 'resume', 'status'])
    parser.add_argument('--out', type=Path, default=OUT)
    parser.add_argument('--market', type=Path)
    parser.add_argument('--limit', type=int)
    args = parser.parse_args()
    if args.command != 'status' and args.market is None:
        parser.error('--market required')
    if args.command == 'run':
        reuse_pilot(args.out)
    install_adapter()
    result = base.summarize(args.out) if args.command == 'status' else base.execute(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
