"""Offline prompt assembly and structural evidence gates; never calls an LLM.

Passing these checks is not substantive verification or expert endorsement.
Reference packs must be curated independently; an LLM cannot mark its own
external evidence or mandatory-fact/exception checklist as verified.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
PROMPTS = ROOT / 'prompts/hallucination_layer1_20261002'
CODES = tuple(f'L1.{major}.{minor}' for major, count in ((1, 5), (2, 6), (3, 5), (4, 4))
              for minor in range(1, count + 1))
STATES = {'error', 'no_error', 'not_applicable', 'uncertain'}
ANSWER_DISPOSITIONS = {'substantive', 'full_refusal', 'partial_refusal', 'uninterpretable'}
EXTERNAL_REQUIRED = {'L1.1.1', 'L1.1.2', 'L1.1.3', 'L1.1.5'}
OMISSION_CODES = {'L1.2.3', 'L1.4.2'}
PACKET_FIELDS = {'case_text', 'question', 'judge_decision', 'answer', 'reference_pack'}
EVIDENCE_FIELDS = {'evidence_id', 'kind', 'text', 'verified', 'source_url',
                   'source_sha256', 'version_id', 'locator',
                   'temporal_applicability_verified'}
CHECKLIST_FIELDS = {'checklist_id', 'code', 'text', 'verified', 'basis_evidence_ids',
                    'available_to_answerer'}


def require(condition, reason):
    if not condition:
        raise ValueError(reason)


def build_messages(case_text, question, judge_decision, answer, reference_pack=None):
    """Deliberately accepts no model identity, previous score or reasoning trace."""
    for name, value in [('case_text', case_text), ('question', question),
                        ('judge_decision', judge_decision), ('answer', answer)]:
        require(isinstance(value, str) and value.strip(), 'Missing text: ' + name)
    refs = ({'evidence': [], 'checklists': [], 'coverage': {}}
            if reference_pack is None else reference_pack)
    validate_reference_pack(refs)
    system = (PROMPTS / 'judge_system_zh.txt').read_text(encoding='utf-8')
    system += '\n\n分类表（例子不作为已核验的个案金标准）：\n'
    system += (PROMPTS / 'taxonomy_user.md').read_text(encoding='utf-8')
    packet = {'case_text': case_text, 'question': question, 'judge_decision': judge_decision,
              'answer': answer, 'reference_pack': refs}
    return [{'role': 'system', 'content': system},
            {'role': 'user', 'content': json.dumps(packet, ensure_ascii=False)}], packet


def validate_reference_pack(pack):
    """Structural checks only, not authentication of a source or verification flag.

    The caller must independently curate and verify the frozen reference pack.
    A plausible URL/hash and a hand-set ``verified`` flag do not establish legal
    truth, source authenticity, or temporal applicability. Extra model/score
    metadata is excluded from the packet; source-provenance manifests stay out
    of the judge-facing schema.
    """
    require(isinstance(pack, dict), 'Invalid reference pack')
    require(set(pack) == {'evidence', 'checklists', 'coverage'}, 'Invalid reference-pack fields')
    require(isinstance(pack['evidence'], list) and isinstance(pack['checklists'], list), 'Invalid lists')
    evidence_ids = set()
    verified_ids = {'input_case', 'input_question', 'judicial_reference'}
    for item in pack['evidence']:
        require(isinstance(item, dict), 'Invalid evidence item')
        require(set(item).issubset(EVIDENCE_FIELDS), 'Unexpected evidence fields')
        ident = item.get('evidence_id')
        require(isinstance(ident, str) and ident and ident not in evidence_ids,
                'Missing/duplicate evidence ID')
        require(ident not in {'input_case', 'input_question', 'judicial_reference'}, 'Reserved ID')
        require(isinstance(item.get('text'), str) and item['text'].strip(), 'Empty evidence text')
        require(isinstance(item.get('kind'), str) and item['kind'].strip(), 'Missing evidence kind')
        require(type(item.get('verified')) is bool, 'Evidence verification must be explicit')
        if 'temporal_applicability_verified' in item:
            require(type(item['temporal_applicability_verified']) is bool,
                    'Temporal verification must be explicit')
        if item.get('kind') == 'external_authority' and item['verified']:
            url = item.get('source_url')
            require(isinstance(url, str), 'External authority missing actual source URL')
            try:
                parsed = urlsplit(url)
            except ValueError as exc:
                raise ValueError('Malformed source URL') from exc
            require(parsed.scheme == 'https' and parsed.hostname and
                    parsed.username is None and parsed.password is None,
                    'External authority missing actual source URL')
            require(isinstance(item.get('source_sha256'), str) and
                    len(item['source_sha256']) == 64 and
                    all(c in '0123456789abcdef' for c in item['source_sha256']), 'Missing snapshot hash')
            require(all(isinstance(item.get(key), str) and item[key].strip()
                        for key in ('version_id', 'locator')), 'Missing version or source locator')
        evidence_ids.add(ident)
        if item['verified']:
            verified_ids.add(ident)
    checklist_ids = set()
    for item in pack['checklists']:
        require(isinstance(item, dict), 'Invalid checklist item')
        require(set(item).issubset(CHECKLIST_FIELDS), 'Unexpected checklist fields')
        ident = item.get('checklist_id')
        require(isinstance(ident, str) and ident and ident not in checklist_ids, 'Duplicate checklist ID')
        require(isinstance(item.get('code'), str) and item['code'] in OMISSION_CODES,
                'Checklist category not supported')
        require(type(item.get('verified')) is bool and isinstance(item.get('text'), str) and
                item['text'].strip(), 'Invalid checklist verification/text')
        require(isinstance(item.get('basis_evidence_ids'), list) and
                all(isinstance(x, str) and
                    x in evidence_ids | {'input_case', 'input_question', 'judicial_reference'}
                    for x in item['basis_evidence_ids']), 'Unknown checklist basis')
        require(len(item['basis_evidence_ids']) == len(set(item['basis_evidence_ids'])),
                'Duplicate checklist basis')
        if item['verified']:
            require(bool(item['basis_evidence_ids']), 'Verified checklist needs evidence')
            require(all(x in verified_ids for x in item['basis_evidence_ids']),
                    'Verified checklist has unverified basis')
        if item['code'] == 'L1.4.2' and item['verified']:
            require(item.get('available_to_answerer') is True, 'Hidden fact cannot be required')
            require(bool(set(item['basis_evidence_ids']) & {'input_case', 'input_question'}),
                    'Mandatory fact must be grounded in answerer-visible input')
        checklist_ids.add(ident)
    require(isinstance(pack['coverage'], dict) and set(pack['coverage']).issubset(CODES) and
            all(type(v) is bool for v in pack['coverage'].values()), 'Invalid evidence coverage')


def validate_result(result, packet):
    """Reject missing categories, invented evidence and unsupported certainty."""
    require(isinstance(packet, dict) and set(packet) == PACKET_FIELDS, 'Invalid blind packet fields')
    require(all(isinstance(packet[x], str) and packet[x].strip()
                for x in PACKET_FIELDS - {'reference_pack'}), 'Missing packet text')
    require(isinstance(result, dict) and set(result) == {
        'schema_version', 'answer_disposition', 'subtype_results', 'findings', 'review_notes'},
        'Invalid result fields')
    require(result['schema_version'] == 'dvj-hallucination-L1-v1', 'Unknown schema')
    require(isinstance(result['answer_disposition'], str) and
            result['answer_disposition'] in ANSWER_DISPOSITIONS, 'Invalid answer disposition')
    no_substantive_answer = result['answer_disposition'] in {'full_refusal', 'uninterpretable'}
    require(isinstance(result['subtype_results'], list) and len(result['subtype_results']) == 20,
            'All twenty categories required')
    require(isinstance(result['findings'], list), 'Findings must be a list')
    if no_substantive_answer:
        require(not result['findings'], 'Non-substantive answer cannot have findings')
    require(isinstance(result['review_notes'], list) and
            all(isinstance(x, str) for x in result['review_notes']), 'Invalid review notes')
    pack = packet['reference_pack']
    validate_reference_pack(pack)
    evidence = {x['evidence_id']: x for x in pack['evidence']}
    evidence.update({x: {'verified': True, 'kind': 'input'}
                     for x in ('input_case', 'input_question', 'judicial_reference')})
    checklists = {x['checklist_id']: x for x in pack['checklists']}

    def check_refs(row, certain=False):
        for field, registry in [('evidence_ids', evidence), ('checklist_ids', checklists)]:
            require(isinstance(row.get(field), list) and all(isinstance(x, str) for x in row[field]),
                    'Invalid evidence/checklist references')
            require(len(row[field]) == len(set(row[field])), 'Duplicate evidence references')
            require(all(x in registry for x in row[field]), 'Invented evidence/checklist ID')
            if certain:
                require(all(registry[x]['verified'] for x in row[field]), 'Unverified evidence used as certain')

    def check_external(row):
        external = [evidence[x] for x in row['evidence_ids']
                    if evidence[x].get('kind') == 'external_authority']
        require(bool(external), 'Authority conclusion requires external evidence')
        if row['code'] == 'L1.1.3':
            require(any(x.get('temporal_applicability_verified') is True for x in external),
                    'Temporal applicability not verified on external authority')

    rows = {}
    for row in result['subtype_results']:
        require(isinstance(row, dict) and set(row) == {
            'code', 'state', 'reason', 'evidence_ids', 'checklist_ids'}, 'Invalid category result')
        code, state = row['code'], row['state']
        require(isinstance(code, str) and code in CODES and code not in rows, 'Invalid/duplicate category')
        require(isinstance(state, str) and state in STATES and
                isinstance(row['reason'], str) and row['reason'].strip(),
                'Invalid category state/reason')
        if no_substantive_answer:
            require(state in {'not_applicable', 'uncertain'},
                    'Non-substantive answer cannot be counted as error or no_error')
        certain = state in {'error', 'no_error'}
        check_refs(row, certain)
        if code in EXTERNAL_REQUIRED and certain:
            check_external(row)
            if state == 'no_error':
                require(pack['coverage'].get(code) is True, 'Cannot clear unverified authority coverage')
        if code in OMISSION_CODES and certain:
            require(row['checklist_ids'] and all(checklists[x]['code'] == code
                    for x in row['checklist_ids']), 'Omission conclusion needs verified matching checklist')
            if state == 'no_error':
                require(pack['coverage'].get(code) is True, 'Checklist coverage incomplete')
        if state == 'error':
            require(bool(row['evidence_ids']), 'Error requires evidence')
        rows[code] = row
    seen = set()
    event_codes = set()
    for event in result['findings']:
        require(isinstance(event, dict) and set(event) == {
            'code', 'same_error_id', 'quote', 'start', 'end', 'explanation', 'evidence_ids', 'checklist_ids'},
            'Invalid finding fields')
        code = event['code']
        require(isinstance(code, str) and code in rows and rows[code]['state'] == 'error',
                'Finding without positive category')
        require(isinstance(event['same_error_id'], str) and event['same_error_id'], 'Missing error identity')
        require(isinstance(event['explanation'], str) and event['explanation'].strip(), 'Missing explanation')
        check_refs(event, True)
        require(event['evidence_ids'] and set(event['evidence_ids']).issubset(rows[code]['evidence_ids']),
                'Finding evidence not reflected in category')
        require(set(event['checklist_ids']).issubset(rows[code]['checklist_ids']), 'Checklist mismatch')
        if code in EXTERNAL_REQUIRED:
            check_external(event)
        if code in OMISSION_CODES:
            require(event['checklist_ids'] and event['quote'] is None and event['start'] is None and
                    event['end'] is None, 'Omission must cite checklist, not fabricate a quotation')
        else:
            a, b = event['start'], event['end']
            require(type(a) is int and type(b) is int and 0 <= a < b <= len(packet['answer']), 'Invalid span')
            require(isinstance(event['quote'], str) and packet['answer'][a:b] == event['quote'],
                    'Quote/span not identical to original answer')
        fingerprint = ((code, tuple(sorted(event['checklist_ids']))) if code in OMISSION_CODES
                       else (code, event['start'], event['end']))
        require(fingerprint not in seen, 'Duplicate event')
        if code in {'L1.2.1', 'L1.2.2', 'L1.4.3'}:
            require(not any((other, event['start'], event['end']) in seen
                            for other in {'L1.2.1', 'L1.2.2', 'L1.4.3'} - {code}),
                    'Same span cannot be assigned mutually exclusive concept/element/application errors')
        seen.add(fingerprint)
        event_codes.add(code)
    require(event_codes == {code for code, row in rows.items() if row['state'] == 'error'},
            'Every error category must have a traceable event')
    # Substantive correctness, scope and whether categories genuinely overlap
    # still require source review; this is not a keyword-based error detector.
    return result


def prompt_hashes():
    return {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
            for p in (PROMPTS / 'judge_system_zh.txt', PROMPTS / 'taxonomy_user.md')}
