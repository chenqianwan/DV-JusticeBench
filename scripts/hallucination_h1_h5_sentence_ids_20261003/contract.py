"""Sentence-ID H1-H5 transport contract; anchors never certify legal truth.

The original answer is never normalized. Offsets count Python Unicode code points
(not UTF-8 bytes or JavaScript UTF-16 units). Every nonempty answer is represented
losslessly by deterministic contiguous spans; model output selects IDs only.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / 'prompts/hallucination_h1_h5_sentence_ids_20261003'
TAXONOMY = ROOT / 'prompts/hallucination_h1_h5_20261002/taxonomy_user.txt'
PROTOCOL_VERSION = 'h1-h5-sentence-ids-20261003-v1'
SENTENCE_SCHEMA = 'lossless-unicode-spans-v1'
CODES = ('H1', 'H2', 'H3', 'H4', 'H5')
FIELDS = {'sentence_ids', 'category', 'reason', 'correct_content', 'confidence'}
LEGACY_FIELDS = {'quote', 'category', 'reason', 'correct_content', 'confidence'}
PACKET_FIELDS = {'case_text', 'question', 'judge_decision', 'answer', 'reference_pack'}
TABLE_FIELDS = {'schema', 'answer_sha256', 'offset_unit', 'sentences'}
SPAN_FIELDS = {'id', 'start', 'end', 'text'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')


def answer_hash(answer):
    require(isinstance(answer, str) and answer.strip(), 'Missing original answer')
    return hashlib.sha256(answer.encode('utf-8')).hexdigest()


def packet_hash(packet):
    return hashlib.sha256(canonical(packet)).hexdigest()


def _period_is_internal(answer, index):
    """Do not split decimal numbers, dotted list markers, or URL/domain tokens."""
    before = answer[index - 1] if index else ''
    after = answer[index + 1] if index + 1 < len(answer) else ''
    if before.isdigit() and after.isdigit():
        return True
    line_start = max(answer.rfind('\n', 0, index), answer.rfind('\r', 0, index)) + 1
    prefix = answer[line_start:index]
    if re.fullmatch(r'\s*(?:[-*+]\s+)?\d+(?:\.\d+)*', prefix) and (not after or after.isspace()):
        return True
    # URL periods are structural, but a period after a URL can terminate a sentence.
    token_start = index
    while token_start and not answer[token_start - 1].isspace():
        token_start -= 1
    token_prefix = answer[token_start:index].lower()
    if after and not after.isspace() and ('://' in token_prefix or token_prefix.startswith('www.') or '@' in token_prefix):
        return True
    return False


def build_sentences(answer):
    """Return a hash-bound, lossless table. Sentence IDs are canonical S000001... .

    Split at 。！？.!? and CR/LF, except decimal/list/URL periods; attach repeated
    punctuation and closing quotes/brackets/Markdown delimiters to the same span.
    Whitespace-only spans are folded into a neighbour. IDs are local to one answer.
    """
    digest = answer_hash(answer)
    ranges = []
    start = index = 0
    size = len(answer)
    terminal = '。！？.!?'
    closers = '\"\'”’」』）)]】》〉»*_`'
    while index < size:
        char = answer[index]
        is_newline = char in '\r\n'
        is_terminal = char in terminal and not (char == '.' and _period_is_internal(answer, index))
        if not (is_newline or is_terminal):
            index += 1
            continue
        end = index + 1
        if is_newline:
            if char == '\r' and end < size and answer[end] == '\n':
                end += 1
        else:
            while end < size and (answer[end] in terminal or answer[end] in closers):
                end += 1
        if answer[start:end].strip():
            ranges.append([start, end])
            start = end
        elif ranges:
            ranges[-1][1] = end
            start = end
        # Leading whitespace remains part of the first subsequent substantive span.
        index = end
    if start < size:
        if answer[start:].strip() or not ranges:
            ranges.append([start, size])
        else:
            ranges[-1][1] = size
    sentences = [dict(id=f'S{number:06d}', start=begin, end=end,
                      text=answer[begin:end])
                 for number, (begin, end) in enumerate(ranges, 1)]
    return dict(schema=SENTENCE_SCHEMA, answer_sha256=digest,
                offset_unit='unicode_code_point', sentences=sentences)


def validate_sentences(answer, table):
    """Require current-answer hash, exact offsets/text, and deterministic reproduction."""
    digest = answer_hash(answer)
    require(isinstance(table, dict) and set(table) == TABLE_FIELDS, 'Unexpected sentence table fields')
    require(table['schema'] == SENTENCE_SCHEMA, 'Unknown sentence table schema')
    require(table['offset_unit'] == 'unicode_code_point', 'Wrong sentence offset unit')
    require(table['answer_sha256'] == digest, 'Sentence table belongs to another answer')
    spans = table['sentences']
    require(isinstance(spans, list) and spans, 'Missing sentence spans')
    previous_end = 0
    for number, span in enumerate(spans, 1):
        require(isinstance(span, dict) and set(span) == SPAN_FIELDS, 'Unexpected sentence span fields')
        require(span['id'] == f'S{number:06d}', 'Noncanonical or reordered sentence ID')
        require(type(span['start']) is int and type(span['end']) is int, 'Offsets must be integers')
        require(span['start'] == previous_end and span['start'] < span['end'] <= len(answer),
                'Sentence spans must cover the answer contiguously')
        require(isinstance(span['text'], str) and span['text'].strip(), 'Empty sentence text')
        require(span['text'] == answer[span['start']:span['end']], 'Sentence text differs from original answer')
        previous_end = span['end']
    require(previous_end == len(answer), 'Sentence spans do not cover complete answer')
    require(table == build_sentences(answer), 'Sentence segmentation is not reproducible')
    return table


def validate_packet(packet):
    """Retain the previous blind source-packet and evidence restrictions unchanged."""
    require(isinstance(packet, dict) and set(packet) == PACKET_FIELDS, 'Unexpected blind packet fields')
    for name in PACKET_FIELDS - {'reference_pack'}:
        require(isinstance(packet[name], str) and packet[name].strip(), 'Missing ' + name)
    pack = packet['reference_pack']
    require(isinstance(pack, dict) and set(pack) == {'evidence', 'checklists', 'coverage'},
            'Unexpected reference pack fields')
    require(isinstance(pack['evidence'], list), 'Evidence must be a list')
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


def build_messages_from_packet(packet):
    """Return (messages, exact_delivered_packet); original answer remains caller-owned.

    The delivered blind packet replaces free-form answer with its lossless table,
    avoiding duplicate answer tokens. Joining sentence text reproduces the original.
    """
    validate_packet(packet)
    table = build_sentences(packet['answer'])
    anchored = {name: packet[name] for name in ('case_text', 'question', 'judge_decision', 'reference_pack')}
    anchored['sentence_table'] = table
    # Deep-copy so later mutation of the caller's evidence cannot alter provenance.
    anchored = json.loads(canonical(anchored))
    system = (PROMPTS / 'system_zh.txt').read_text(encoding='utf-8')
    system += '\n\n' + TAXONOMY.read_text(encoding='utf-8')
    messages = [{'role': 'system', 'content': system},
                {'role': 'user', 'content': json.dumps(anchored, ensure_ascii=False, allow_nan=False)}]
    return messages, anchored


def build_messages(case_text, question, judge_decision, answer, reference_pack=None):
    refs = {'evidence': [], 'checklists': [], 'coverage': {}} if reference_pack is None else reference_pack
    return build_messages_from_packet(dict(case_text=case_text, question=question,
        judge_decision=judge_decision, answer=answer, reference_pack=refs))


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, 'Duplicate JSON key')
        result[key] = value
    return result


def invalid_constant(value):
    raise ValueError('Non-finite JSON constant: ' + value)


def validate_output(result, answer, table=None):
    """Validate selection structure only, not semantic relevance or legal correctness."""
    table = validate_sentences(answer, build_sentences(answer) if table is None else table)
    if result == 'NONE':
        return result
    require(isinstance(result, list) and bool(result), 'Expected nonempty event array or NONE')
    indices = {span['id']: number for number, span in enumerate(table['sentences'])}
    events = set()
    for event in result:
        require(isinstance(event, dict) and set(event) == FIELDS, 'Exactly five event fields required')
        ids = event['sentence_ids']
        require(isinstance(ids, list) and ids, 'sentence_ids must be a nonempty array')
        require(all(isinstance(item, str) and item in indices for item in ids), 'Unknown or noncanonical sentence ID')
        positions = [indices[item] for item in ids]
        require(positions == list(range(positions[0], positions[0] + len(positions))),
                'Sentence IDs must be unique, ascending, and contiguous')
        require(isinstance(event['category'], str) and event['category'] in CODES,
                'Each event must have exactly one H1-H5 category')
        for field in ('reason', 'correct_content'):
            require(isinstance(event[field], str) and event[field].strip(), 'Missing event ' + field)
        confidence = event['confidence']
        require(type(confidence) in (float, int) and math.isfinite(confidence) and 0 <= confidence <= 1,
                'Confidence must be a finite number in [0,1]')
        # Distinct claims within one sentence may share the same exact anchor.
        identity = canonical(event)
        require(identity not in events, 'Exact duplicate event')
        events.add(identity)
    return result


def parse_output(text, answer, table=None):
    require(isinstance(text, str), 'Output must be text')
    value = text.strip()
    if value == 'NONE':
        return validate_output('NONE', answer, table)
    result = json.loads(value, object_pairs_hook=unique_keys, parse_constant=invalid_constant)
    require(result != 'NONE', 'NONE must be bare, not a JSON string')
    return validate_output(result, answer, table)


def hydrate_output(parsed, answer, table=None):
    """Create legacy quotations exclusively from source offsets; preserve ID provenance.

    Exact anchoring does not mean the model selected the right sentence or identified
    a real hallucination. Automatic statistics still require independent evidence.
    """
    table = validate_sentences(answer, build_sentences(answer) if table is None else table)
    parsed = validate_output(parsed, answer, table)
    if parsed == 'NONE':
        return 'NONE'
    spans = {item['id']: item for item in table['sentences']}
    events, anchors = [], []
    for index, event in enumerate(parsed):
        ids = event['sentence_ids']
        start, end = spans[ids[0]]['start'], spans[ids[-1]]['end']
        quote = answer[start:end]
        legacy = {name: event[name] for name in ('category', 'reason', 'correct_content', 'confidence')}
        legacy['quote'] = quote
        events.append(legacy)
        anchors.append(dict(event_index=index, sentence_ids=list(ids), start=start, end=end,
            offset_unit='unicode_code_point', answer_sha256=table['answer_sha256'],
            quote_sha256=hashlib.sha256(quote.encode('utf-8')).hexdigest(),
            quote_source='original_answer_exact_span', semantic_alignment_verified=False,
            legal_truth_verified=False))
    return dict(events=events, anchors=anchors)


def prompt_hashes():
    return {'system_zh.txt': hashlib.sha256((PROMPTS / 'system_zh.txt').read_bytes()).hexdigest(),
            'taxonomy_user.txt': hashlib.sha256(TAXONOMY.read_bytes()).hexdigest()}


def protocol_hashes():
    return dict(protocol_version=PROTOCOL_VERSION, sentence_schema=SENTENCE_SCHEMA,
                prompts=prompt_hashes(), contract_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
