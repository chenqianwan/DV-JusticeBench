"""Offline contract tests; synthetic references are NOT verified legal sources.

The validator checks structure and curator-supplied attestations, not whether a
law is real, the supplied hash matches a download, or the legal judgment is true.
No fixture or passing test constitutes external legal verification.
"""
from copy import deepcopy
import inspect
import json
import unittest
from unittest.mock import patch

import judge_contract as contract


def reference_pack():
    return {'evidence': [], 'checklists': [], 'coverage': {}}


def authority(verified=True, temporal=False, ident='law'):
    # Deliberately synthetic; metadata shape is not source authentication.
    return {'evidence_id': ident, 'kind': 'external_authority',
            'text': '用于离线结构测试的假设条文，不作为真实法律。',
            'verified': verified, 'source_url': 'https://example.invalid/law',
            'source_sha256': 'a' * 64, 'version_id': 'fixture-v1',
            'locator': 'article-1', 'temporal_applicability_verified': temporal}


def checklist(code='L1.4.2', verified=True, ident='required_fact'):
    return {'checklist_id': ident, 'code': code, 'text': '本题需要核查的事实。',
            'verified': verified, 'basis_evidence_ids': ['input_case'],
            'available_to_answerer': True}


class ContractTests(unittest.TestCase):
    def setUp(self):
        self.packet = {'case_text': '甲主张遭受暴力，乙否认；案情未说明已经协议离婚。',
                       'question': '应如何评价双方争议？',
                       'judge_decision': '裁判理由仅用于独立参考。',
                       'answer': '前😀甲方已经协议离婚。e\u0301',
                       'reference_pack': reference_pack()}
        self.result = {'schema_version': 'dvj-hallucination-L1-v1',
                       'answer_disposition': 'substantive',
                       'subtype_results': [
                           {'code': code, 'state': 'uncertain', 'reason': '证据尚不足。',
                            'evidence_ids': [], 'checklist_ids': []}
                           for code in contract.CODES],
                       'findings': [], 'review_notes': []}

    def row(self, code):
        return next(x for x in self.result['subtype_results'] if x['code'] == code)

    def pack(self):
        return self.packet['reference_pack']

    def add_authority(self, verified=True, temporal=False):
        self.pack()['evidence'].append(authority(verified, temporal))

    def set_state(self, code, state, evidence=None, checklists=None):
        self.row(code).update(state=state, evidence_ids=evidence or [],
                              checklist_ids=checklists or [])

    def add_error(self, code='L1.4.1', evidence=None, checklists=None, quote='已经协议离婚'):
        evidence = ['input_case'] if evidence is None else evidence
        checklists = [] if checklists is None else checklists
        self.set_state(code, 'error', evidence, checklists)
        omission = code in contract.OMISSION_CODES
        start = None if omission else self.packet['answer'].index(quote)
        event = {'code': code, 'same_error_id': 'E1', 'quote': None if omission else quote,
                 'start': start, 'end': None if omission else start + len(quote),
                 'explanation': '仅用于结构测试的错误说明。',
                 'evidence_ids': list(evidence), 'checklist_ids': list(checklists)}
        self.result['findings'].append(event)
        return event

    def validate(self):
        return contract.validate_result(self.result, self.packet)

    def rejects(self, pattern=None):
        if pattern is None:
            return self.assertRaises(ValueError)
        return self.assertRaisesRegex(ValueError, pattern)

    def test_exact_twenty_unique_codes(self):
        self.assertEqual(len(contract.CODES), 20)
        self.assertEqual(len(set(contract.CODES)), 20)
        self.assertEqual([sum(code.startswith(f'L1.{i}.') for code in contract.CODES)
                          for i in range(1, 5)], [5, 6, 5, 4])

    def test_legal_uncertain_result(self):
        self.assertIs(self.validate(), self.result)

    def test_all_four_answer_dispositions_are_explicit(self):
        self.assertEqual(contract.ANSWER_DISPOSITIONS,
                         {'substantive', 'full_refusal', 'partial_refusal', 'uninterpretable'})
        for disposition in contract.ANSWER_DISPOSITIONS:
            with self.subTest(disposition=disposition):
                self.result['answer_disposition'] = disposition
                self.validate()

    def test_missing_or_invalid_answer_disposition_rejected(self):
        for value in ['ok', '', None, []]:
            with self.subTest(value=value):
                self.result['answer_disposition'] = value
                with self.rejects('disposition'):
                    self.validate()
        del self.result['answer_disposition']
        with self.rejects('result fields'):
            self.validate()

    def test_full_refusal_and_uninterpretable_cannot_be_counted_no_error_or_error(self):
        for disposition in ['full_refusal', 'uninterpretable']:
            for state in ['no_error', 'error']:
                with self.subTest(disposition=disposition, state=state):
                    self.result['answer_disposition'] = disposition
                    self.set_state('L1.4.1', state, ['input_case'])
                    with self.rejects('Non-substantive'):
                        self.validate()

    def test_full_refusal_and_uninterpretable_cannot_have_findings(self):
        self.add_error()
        for disposition in ['full_refusal', 'uninterpretable']:
            with self.subTest(disposition=disposition):
                self.result['answer_disposition'] = disposition
                with self.rejects('cannot have findings'):
                    self.validate()

    def test_full_refusal_and_uninterpretable_allow_na_and_uncertain(self):
        self.set_state('L1.4.1', 'not_applicable')
        for disposition in ['full_refusal', 'uninterpretable']:
            with self.subTest(disposition=disposition):
                self.result['answer_disposition'] = disposition
                self.validate()

    def test_partial_refusal_can_still_have_substantive_error(self):
        self.result['answer_disposition'] = 'partial_refusal'
        self.add_error()
        self.validate()

    def test_missing_category_rejected(self):
        self.result['subtype_results'].pop()
        with self.rejects('twenty'):
            self.validate()

    def test_duplicate_category_with_twenty_rows_rejected(self):
        self.result['subtype_results'][-1] = deepcopy(self.result['subtype_results'][0])
        with self.rejects('duplicate category'):
            self.validate()

    def test_unknown_category_rejected(self):
        self.result['subtype_results'][-1]['code'] = 'L1.5.1'
        with self.rejects():
            self.validate()

    def test_non_string_code_and_state_fail_closed(self):
        for field in ['code', 'state']:
            with self.subTest(field=field):
                prior = self.result['subtype_results'][0][field]
                self.result['subtype_results'][0][field] = []
                with self.rejects():
                    self.validate()
                self.result['subtype_results'][0][field] = prior

    def test_invalid_schema_and_extra_result_field_rejected(self):
        self.result['schema_version'] = 'other'
        with self.rejects('schema'):
            self.validate()
        self.result['schema_version'] = 'dvj-hallucination-L1-v1'
        self.result['previous_score'] = 20
        with self.rejects('result fields'):
            self.validate()

    def test_negated_error_words_do_not_generate_errors(self):
        self.packet['answer'] = '不能虚构事实。本案并未协议离婚，不能因引用旧法就认定违法。'
        self.set_state('L1.4.1', 'no_error', ['input_case'])
        self.row('L1.4.1')['reason'] = '没有编造或虚构事实；“错误”一词不是错误标签。'
        self.validate()
        self.assertEqual(self.result['findings'], [])
        self.assertEqual(self.row('L1.4.1')['state'], 'no_error')

    def test_legal_fact_error(self):
        self.add_error()
        self.validate()

    def test_error_requires_evidence(self):
        self.add_error(evidence=[])
        with self.rejects('requires evidence'):
            self.validate()

    def test_every_error_requires_event(self):
        self.set_state('L1.4.1', 'error', ['input_case'])
        with self.rejects('traceable event'):
            self.validate()

    def test_event_must_correspond_to_error_state(self):
        self.add_error()
        self.set_state('L1.4.1', 'no_error', ['input_case'])
        with self.rejects('positive category'):
            self.validate()

    def test_event_cannot_relabel_itself(self):
        self.add_error()['code'] = 'L1.4.4'
        with self.rejects('positive category'):
            self.validate()

    def test_invented_evidence_or_checklist_id_rejected(self):
        for field in ['evidence_ids', 'checklist_ids']:
            with self.subTest(field=field):
                self.row('L1.4.1')[field] = ['invented']
                with self.rejects('Invented'):
                    self.validate()
                self.row('L1.4.1')[field] = []

    def test_duplicate_evidence_reference_rejected(self):
        self.row('L1.4.1')['evidence_ids'] = ['input_case', 'input_case']
        with self.rejects('Duplicate evidence'):
            self.validate()

    def test_unverified_evidence_allowed_only_for_uncertain(self):
        self.add_authority(verified=False)
        self.set_state('L1.1.1', 'uncertain', ['law'])
        self.validate()
        self.add_error('L1.1.1', ['law'])
        with self.rejects('Unverified'):
            self.validate()

    def test_external_gate_for_all_four_required_categories(self):
        for code in contract.EXTERNAL_REQUIRED:
            with self.subTest(code=code):
                self.set_state(code, 'no_error', ['judicial_reference'])
                self.pack()['coverage'][code] = True
                with self.rejects('external evidence'):
                    self.validate()
                self.set_state(code, 'uncertain')

    def test_hierarchy_keeps_authorized_auto_judge_scope(self):
        self.add_error('L1.1.4', ['judicial_reference'])
        self.validate()

    def test_legal_authority_error_with_external_evidence(self):
        self.add_authority()
        self.add_error('L1.1.1', ['law'])
        self.validate()

    def test_authority_event_cannot_borrow_unused_row_external_evidence(self):
        self.add_authority()
        event = self.add_error('L1.1.1', ['input_case', 'law'])
        event['evidence_ids'] = ['input_case']
        with self.rejects('external evidence'):
            self.validate()

    def test_authority_no_error_requires_coverage(self):
        self.add_authority()
        for coverage in [None, False]:
            with self.subTest(coverage=coverage):
                self.set_state('L1.1.2', 'no_error', ['law'])
                self.pack()['coverage'] = {} if coverage is None else {'L1.1.2': coverage}
                with self.rejects('coverage'):
                    self.validate()

    def test_legal_authority_no_error(self):
        self.add_authority()
        self.set_state('L1.1.5', 'no_error', ['law'])
        self.pack()['coverage']['L1.1.5'] = True
        self.validate()

    def test_coverage_alone_cannot_substitute_for_evidence(self):
        self.set_state('L1.1.1', 'no_error')
        self.pack()['coverage']['L1.1.1'] = True
        with self.rejects('external evidence'):
            self.validate()

    def test_temporal_error_requires_temporal_applicability(self):
        self.add_authority()
        self.add_error('L1.1.3', ['law'])
        with self.rejects('Temporal applicability'):
            self.validate()

    def test_temporal_marker_on_non_external_evidence_is_insufficient(self):
        self.add_authority()
        self.pack()['evidence'].append({'evidence_id': 'other', 'kind': 'curated_reference',
                                       'text': '其他材料', 'verified': True,
                                       'temporal_applicability_verified': True})
        self.add_error('L1.1.3', ['law', 'other'])
        with self.rejects('Temporal applicability'):
            self.validate()

    def test_temporal_event_requires_its_own_temporal_authority(self):
        self.add_authority(temporal=True)
        self.pack()['evidence'].append(authority(ident='other_law'))
        event = self.add_error('L1.1.3', ['law', 'other_law'])
        event['evidence_ids'] = ['other_law']
        with self.rejects('Temporal applicability'):
            self.validate()

    def test_legal_temporal_error(self):
        self.add_authority(temporal=True)
        self.add_error('L1.1.3', ['law'])
        self.validate()

    def test_omission_needs_verified_checklist(self):
        self.add_error('L1.4.2')
        with self.rejects('checklist'):
            self.validate()
        self.pack()['checklists'].append(checklist(verified=False))
        self.row('L1.4.2')['checklist_ids'] = ['required_fact']
        with self.rejects('Unverified'):
            self.validate()

    def test_wrong_omission_checklist_category_rejected(self):
        self.pack()['checklists'].append(checklist(code='L1.2.3'))
        self.add_error('L1.4.2', checklists=['required_fact'])
        with self.rejects('matching checklist'):
            self.validate()

    def test_legal_visible_fact_omission(self):
        self.pack()['checklists'].append(checklist())
        self.add_error('L1.4.2', checklists=['required_fact'])
        self.validate()

    def test_hidden_fact_cannot_be_required(self):
        item = checklist()
        item['available_to_answerer'] = False
        self.pack()['checklists'].append(item)
        with self.rejects('Hidden fact'):
            self.validate()

    def test_manual_visibility_flag_cannot_replace_input_grounding(self):
        item = checklist()
        item['basis_evidence_ids'] = ['judicial_reference']
        self.pack()['checklists'].append(item)
        with self.rejects('answerer-visible'):
            self.validate()

    def test_verified_checklist_cannot_use_unverified_basis(self):
        self.add_authority(verified=False)
        item = checklist(code='L1.2.3')
        item['basis_evidence_ids'] = ['law']
        self.pack()['checklists'].append(item)
        with self.rejects('unverified basis'):
            self.validate()

    def test_verified_checklist_needs_known_nonempty_basis(self):
        for basis in [[], ['unknown']]:
            with self.subTest(basis=basis):
                item = checklist(code='L1.2.3')
                item['basis_evidence_ids'] = basis
                self.pack()['checklists'] = [item]
                with self.rejects():
                    self.validate()

    def test_legal_exception_omission(self):
        self.add_authority()
        item = checklist(code='L1.2.3', ident='exception')
        item['basis_evidence_ids'] = ['law']
        self.pack()['checklists'].append(item)
        self.add_error('L1.2.3', ['law'], ['exception'])
        self.validate()

    def test_omission_no_error_requires_coverage(self):
        self.pack()['checklists'].append(checklist())
        self.set_state('L1.4.2', 'no_error', ['input_case'], ['required_fact'])
        with self.rejects('coverage'):
            self.validate()
        self.pack()['coverage']['L1.4.2'] = True
        self.validate()

    def test_omission_cannot_fabricate_quote(self):
        self.pack()['checklists'].append(checklist())
        event = self.add_error('L1.4.2', checklists=['required_fact'])
        event.update(quote='甲方', start=2, end=4)
        with self.rejects('Omission must cite checklist'):
            self.validate()

    def test_unicode_code_point_half_open_span_accepted(self):
        self.add_error(quote='😀甲方')
        self.validate()

    def test_utf8_byte_offsets_rejected(self):
        event = self.add_error()
        answer = self.packet['answer']
        event['start'] = len(answer[:event['start']].encode('utf-8'))
        event['end'] = event['start'] + len(event['quote'].encode('utf-8'))
        with self.rejects():
            self.validate()

    def test_unicode_normalization_not_silently_applied(self):
        event = self.add_error(quote='e\u0301')
        event['quote'] = '\u00e9'
        with self.rejects('not identical'):
            self.validate()

    def test_empty_out_of_bounds_and_boolean_spans_rejected(self):
        for start, end in [(0, 0), (-1, 1), (0, 999), (True, 3)]:
            with self.subTest(start=start, end=end):
                self.result['findings'] = []
                event = self.add_error()
                event.update(start=start, end=end)
                with self.rejects('Invalid span'):
                    self.validate()

    def test_modified_quote_rejected(self):
        self.add_error()['quote'] = '已办理协议离婚'
        with self.rejects('not identical'):
            self.validate()

    def test_event_evidence_must_be_reflected_in_category(self):
        self.add_error()['evidence_ids'] = ['judicial_reference']
        with self.rejects('not reflected'):
            self.validate()

    def test_exact_duplicate_event_rejected(self):
        event = self.add_error()
        self.result['findings'].append(deepcopy(event))
        with self.rejects('Duplicate event'):
            self.validate()

    def test_changing_error_identity_does_not_evade_duplicate_span(self):
        duplicate = deepcopy(self.add_error())
        duplicate['same_error_id'] = 'E2'
        self.result['findings'].append(duplicate)
        with self.rejects('Duplicate event'):
            self.validate()

    def test_duplicate_omission_checklist_rejected(self):
        self.pack()['checklists'].append(checklist())
        duplicate = deepcopy(self.add_error('L1.4.2', checklists=['required_fact']))
        duplicate['same_error_id'] = 'E2'
        self.result['findings'].append(duplicate)
        with self.rejects('Duplicate event'):
            self.validate()

    def test_same_span_can_support_nonexclusive_different_codes(self):
        self.add_error('L1.4.1')
        self.add_error('L1.4.4')
        self.validate()

    def test_exact_same_span_cannot_use_mutually_exclusive_codes(self):
        self.add_error('L1.2.1')
        self.add_error('L1.2.2')
        with self.rejects('mutually exclusive'):
            self.validate()

    def test_same_root_cause_can_recur_at_different_spans(self):
        self.packet['answer'] += '又称已经协议离婚。'
        first = self.add_error()
        second = deepcopy(first)
        second['start'] = self.packet['answer'].rindex(second['quote'])
        second['end'] = second['start'] + len(second['quote'])
        self.result['findings'].append(second)
        self.validate()

    def test_reference_pack_does_not_accept_empty_invalid_pack(self):
        with self.rejects('reference-pack fields'):
            contract.build_messages('案情', '问题', '裁判', '回答', {})

    def test_external_metadata_required_and_well_shaped(self):
        for field, value in [('source_url', 'https://'), ('source_url', 'http://example.invalid'),
                             ('source_url', 'https://user:password@example.invalid'),
                             ('source_sha256', 'x' * 64), ('source_sha256', 'a' * 63),
                             ('version_id', []), ('locator', ''),
                             ('temporal_applicability_verified', 'true')]:
            with self.subTest(field=field, value=value):
                item = authority()
                item[field] = value
                self.pack()['evidence'] = [item]
                with self.rejects():
                    self.validate()

    def test_duplicate_and_reserved_evidence_ids_rejected(self):
        self.pack()['evidence'] = [authority(), authority()]
        with self.rejects('duplicate evidence ID'):
            self.validate()
        self.pack()['evidence'] = [authority(ident='input_case')]
        with self.rejects('Reserved ID'):
            self.validate()

    def test_reference_pack_cannot_inject_identity_or_history_fields(self):
        for location, field in [('evidence', 'model'), ('checklists', 'previous_score')]:
            with self.subTest(location=location):
                item = authority() if location == 'evidence' else checklist()
                item[field] = 'hidden identity or old score'
                self.packet['reference_pack'] = reference_pack()
                self.pack()[location] = [item]
                with self.rejects('Unexpected'):
                    self.validate()

    def test_packet_cannot_add_identity_history_or_reasoning_fields(self):
        for key in ['model', 'answer_condition', 'previous_score', 'reasoning_content']:
            with self.subTest(key=key):
                self.packet[key] = 'forbidden metadata'
                with self.rejects('blind packet'):
                    self.validate()
                del self.packet[key]

    def test_builder_signature_and_serialized_packet_are_blind(self):
        self.assertEqual(set(inspect.signature(contract.build_messages).parameters),
                         {'case_text', 'question', 'judge_decision', 'answer', 'reference_pack'})
        messages, packet = contract.build_messages(**self.packet)
        self.assertEqual(set(packet), contract.PACKET_FIELDS)
        self.assertEqual(json.loads(messages[1]['content']), self.packet)
        self.assertEqual([m['role'] for m in messages], ['system', 'user'])
        for name in ['model', 'previous_score', 'reasoning_content']:
            with self.assertRaises(TypeError):
                contract.build_messages(**self.packet, **{name: 'forbidden'})

    def test_contract_is_offline_and_does_not_authenticate_synthetic_reference(self):
        # This deliberately passes STRUCTURAL checks for a non-existent source.
        # Therefore callers must never equate passing with legal verification.
        self.add_authority()
        self.set_state('L1.1.1', 'no_error', ['law'])
        self.pack()['coverage']['L1.1.1'] = True
        with patch('socket.socket', side_effect=AssertionError('Network forbidden')):
            contract.build_messages(**self.packet)
            self.validate()


if __name__ == '__main__':
    unittest.main()
