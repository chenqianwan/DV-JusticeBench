"""Offline H1-H5 contract; structural checks do not establish legal truth.

The executor, not the model, supplies readiness after checking the necessary
reference material. Automatic audit results remain distinct from expert review.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / 'prompts/hallucination_h1_h5_20261002'
CODES = ('H1', 'H2', 'H3', 'H4', 'H5')
FIELDS = {'quote', 'category', 'reason', 'correct_content', 'confidence'}
PACKET_FIELDS = {'case_text', 'question', 'judge_decision', 'answer', 'reference_pack'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def packet_hash(packet):
    return hashlib.sha256(canonical(packet)).hexdigest()


def validate_packet(packet):
    require(isinstance(packet, dict) and set(packet) == PACKET_FIELDS,
            'Unexpected blind packet fields')
    for name in PACKET_FIELDS - {'reference_pack'}:
        require(isinstance(packet[name], str) and packet[name].strip(), 'Missing ' + name)
    pack = packet['reference_pack']
    require(isinstance(pack, dict) and set(pack) == {'evidence', 'checklists', 'coverage'},
            'Unexpected reference pack fields')
    require(isinstance(pack['evidence'], list), 'Evidence must be a list')
    # Old omission checklists are not a sixth hallucination category.
    require(pack['checklists'] == [], 'Do not reuse old twenty-subtype checklists')
    require(isinstance(pack['coverage'], dict) and set(pack['coverage']).issubset(CODES)
            and all(type(v) is bool for v in pack['coverage'].values()), 'Invalid H1-H5 coverage')
    allowed = {'evidence_id', 'kind', 'text', 'verified', 'source_url', 'source_sha256',
               'version_id', 'locator', 'temporal_applicability_verified'}
    ids = set()
    for item in pack['evidence']:
        require(isinstance(item, dict) and set(item).issubset(allowed), 'Unexpected evidence fields')
        for name in ['evidence_id', 'kind', 'text']:
            require(isinstance(item.get(name), str) and item[name].strip(), 'Missing evidence ' + name)
        require(item['evidence_id'] not in ids, 'Duplicate evidence ID')
        ids.add(item['evidence_id'])
        require(type(item.get('verified')) is bool, 'Evidence verification must be explicit')
    return packet


def build_messages(case_text, question, judge_decision, answer, reference_pack=None):
    refs = {'evidence': [], 'checklists': [], 'coverage': {}} if reference_pack is None else reference_pack
    packet = validate_packet(dict(case_text=case_text, question=question,
        judge_decision=judge_decision, answer=answer, reference_pack=refs))
    system = (PROMPTS / 'system_zh.txt').read_text(encoding='utf-8')
    system += '\n\n' + (PROMPTS / 'taxonomy_user.txt').read_text(encoding='utf-8')
    return [{'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(packet, ensure_ascii=False, allow_nan=False)}], packet


def unique_keys(pairs):
    obj = {}
    for key, value in pairs:
        require(key not in obj, 'Duplicate JSON key')
        obj[key] = value
    return obj


def invalid_constant(value):
    raise ValueError('Non-finite JSON constant: ' + value)


def validate_output(result, answer):
    """Check format and literal quotations only; never certify H5 or a law source."""
    require(isinstance(answer, str) and answer.strip(), 'Missing original answer')
    if result == 'NONE':
        return result
    require(isinstance(result, list) and bool(result), 'Expected nonempty event array or NONE')
    quotes = set()
    for event in result:
        require(isinstance(event, dict) and set(event) == FIELDS, 'Exactly five event fields required')
        require(isinstance(event['category'], str) and event['category'] in CODES,
                'Each event must have exactly one H1-H5 category')
        for field in ['quote', 'reason', 'correct_content']:
            require(isinstance(event[field], str) and event[field].strip(), 'Missing event ' + field)
        require(event['quote'] in answer, 'Quote is not verbatim in original answer')
        require(event['quote'] not in quotes, 'Duplicate quote/event; split independent events precisely')
        quotes.add(event['quote'])
        c = event['confidence']
        require(type(c) in (float, int) and math.isfinite(c) and 0 <= c <= 1,
                'Confidence must be a finite number in [0,1]')
    return result


def parse_output(text, answer):
    require(isinstance(text, str), 'Output must be text')
    value = text.strip()
    if value == 'NONE':
        return validate_output(value, answer)
    parsed = json.loads(value, object_pairs_hook=unique_keys, parse_constant=invalid_constant)
    require(parsed != 'NONE', 'NONE must be bare, not a JSON string')
    return validate_output(parsed, answer)


def accept_output(text, packet, readiness=None):
    """Executor-only gate for automatic statistics, not an expert certification.

    A handwritten readiness/verified flag is not proof of real source review.
    The calling executor must actually verify provenance and required coverage.
    No per-output expert-review requirement is added to the user's experiment.
    """
    validate_packet(packet)
    require(isinstance(readiness, dict), 'Independent executor readiness required')
    require(readiness.get('packet_sha256') == packet_hash(packet), 'Readiness belongs to another input')
    for field in ['audit_complete', 'evidence_ready', 'substantive_answer']:
        require(readiness.get(field) is True, 'Not ready for automatic statistics: ' + field)
    result = parse_output(text, packet['answer'])
    return {'status': 'auto_audited', 'result': result, 'human_verified': False}


def prompt_hashes():
    return {name: hashlib.sha256((PROMPTS / name).read_bytes()).hexdigest()
            for name in ['system_zh.txt', 'taxonomy_user.txt']}
