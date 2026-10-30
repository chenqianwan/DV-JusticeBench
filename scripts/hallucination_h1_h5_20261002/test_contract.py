import json
import math
import unittest

from scripts.hallucination_h1_h5_20261002.contract import (
    PROMPTS, accept_output, build_messages, packet_hash, parse_output, validate_output)


class FiveCategoryTests(unittest.TestCase):
    def setUp(self):
        self.answer = '被申请人曾被判刑。保护令须达到刑事证明标准。'
        self.event = {'quote':'被申请人曾被判刑', 'category':'H1', 'reason':'材料没有该事实',
                      'correct_content':'材料未载既往刑事定罪', 'confidence':0.9}
        self.messages, self.packet = build_messages('案件原文', '问题', '裁判参考', self.answer)

    def encoded(self, event=None):
        return json.dumps([self.event if event is None else event], ensure_ascii=False)

    def ready(self):
        return dict(packet_sha256=packet_hash(self.packet), audit_complete=True,
                    evidence_ready=True, substantive_answer=True)

    def test_original_prompt_included_unedited(self):
        system = (PROMPTS/'system_zh.txt').read_text() + '\n\n' + (PROMPTS/'taxonomy_user.txt').read_text()
        self.assertEqual(self.messages[0]['content'], system)
        self.assertEqual(json.loads(self.messages[1]['content']), self.packet)
        self.assertNotIn('subtype_results', system)

    def test_event_and_none(self):
        self.assertEqual(parse_output(self.encoded(), self.answer), [self.event])
        self.assertEqual(parse_output(' NONE\n', self.answer), 'NONE')

    def test_empty_array_fences_explanations_and_old_schema_rejected(self):
        for text in ['[]', '{}', '"NONE"', 'none', 'NONE because no error',
                     '```json\n'+self.encoded()+'\n```', '解释'+self.encoded(),
                     '{"subtype_results":[]}']:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_output(text, self.answer)

    def test_confidence_valid_endpoints(self):
        for confidence in [0, 1, .5]:
            self.assertEqual(parse_output(self.encoded(dict(self.event, confidence=confidence)), self.answer)[0]['confidence'], confidence)

    def test_invalid_confidence(self):
        for confidence in [True, False, '0.9', -1, 1.1, None, float('nan'), float('inf')]:
            with self.subTest(confidence=confidence), self.assertRaises(ValueError):
                parse_output(self.encoded(dict(self.event, confidence=confidence)), self.answer)

    def test_unique_valid_category(self):
        for code in ['H1','H2','H3','H4','H5']:
            self.assertEqual(parse_output(self.encoded(dict(self.event, category=code)), self.answer)[0]['category'], code)
        for code in [['H1','H5'], 'H1,H5', 'L1.4.1', 'H6', None]:
            with self.subTest(code=code), self.assertRaises(ValueError):
                parse_output(self.encoded(dict(self.event, category=code)), self.answer)

    def test_quote_and_exact_fields(self):
        for event in [dict(self.event, quote='并无这段话'), dict(self.event, reason=''),
                      dict(self.event, correct_content=''), dict(self.event, verified=True)]:
            with self.assertRaises(ValueError):
                parse_output(self.encoded(event), self.answer)

    def test_duplicate_quote_and_json_keys(self):
        with self.assertRaises(ValueError):
            parse_output(json.dumps([self.event, dict(self.event, category='H5')]), self.answer)
        with self.assertRaises(ValueError):
            parse_output(self.encoded().replace('"category": "H1"', '"category":"H1","category":"H5"'), self.answer)

    def test_distinct_events_allowed(self):
        second = dict(self.event, quote='保护令须达到刑事证明标准', category='H4')
        self.assertEqual(len(parse_output(json.dumps([self.event, second]), self.answer)), 2)

    def test_formal_gate_defaults_closed_even_for_none(self):
        for text in ['NONE', self.encoded()]:
            with self.assertRaises(ValueError):
                accept_output(text, self.packet)
            for flag in ['audit_complete', 'evidence_ready', 'substantive_answer']:
                ready = self.ready(); ready[flag] = False
                with self.assertRaises(ValueError):
                    accept_output(text, self.packet, ready)

    def test_readiness_cannot_be_reused_for_other_answer(self):
        ready = self.ready()
        self.packet['answer'] += '另一句'
        with self.assertRaises(ValueError):
            accept_output('NONE', self.packet, ready)

    def test_automatic_result_not_expert_certification(self):
        result = accept_output(self.encoded(), self.packet, self.ready())
        self.assertEqual(result['status'], 'auto_audited')
        self.assertFalse(result['human_verified'])

    def test_structural_validation_does_not_prove_h5_prerequisites(self):
        # The format permits H5 but cannot establish facts/rules are correct.
        questionable = dict(self.event, category='H5')
        self.assertEqual(validate_output([questionable], self.answer), [questionable])

    def test_blind_packet_rejects_extra_metadata_and_old_coverage(self):
        for pack in [dict(self.packet['reference_pack'], model='gpt-4o'),
                     dict(self.packet['reference_pack'], coverage={'L1.1.1':True}),
                     dict(self.packet['reference_pack'], checklists=[{'code':'L1.4.2'}])]:
            with self.assertRaises(ValueError):
                build_messages('案情','问题','参考',self.answer,pack)


if __name__ == '__main__':
    unittest.main()
