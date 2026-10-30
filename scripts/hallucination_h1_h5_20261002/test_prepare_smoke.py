import hashlib
import json
from pathlib import Path
import unittest

from scripts.hallucination_h1_h5_20261002.prepare_smoke import DATA, ROOT, convert


class H1H5PreparationTests(unittest.TestCase):
    def packet(self):
        return {'case_text':'原案情', 'question':'原问题', 'judge_decision':'独立裁判参考',
                'answer':'原回答，不重新生成。',
                'reference_pack':{'evidence':[], 'checklists':[], 'coverage':{}}}

    def test_active_configuration_hashes_and_full_scope(self):
        active = json.loads((DATA / 'ACTIVE_PROTOCOL.json').read_text())
        self.assertEqual(active['complete_answers'], 55236)
        self.assertEqual(active['categories'], ['H1','H2','H3','H4','H5'])
        self.assertFalse(active['old_20_subtype_packets_allowed_for_new_runs'])
        for field in ['taxonomy_path', 'system_path']:
            path = ROOT / active[field]
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                             active['prompt_sha256'][path.name])

    def test_convert_changes_only_prompt_version_not_answer_or_evidence(self):
        packet = self.packet()
        request = {'audit_id':'blind-id', 'messages':[
            {'role':'system', 'content':'OLD TWENTY CATEGORIES SENTINEL'},
            {'role':'user', 'content':json.dumps(packet, ensure_ascii=False)}]}
        new = convert(request)
        self.assertEqual(new['audit_id'], 'blind-id')
        self.assertEqual(new['protocol_id'], 'dvj-hallucination-H1-H5-v1')
        self.assertEqual(json.loads(new['messages'][1]['content']), packet)
        self.assertNotIn('OLD TWENTY CATEGORIES SENTINEL', new['messages'][0]['content'])
        for code in ['H1.', 'H2.', 'H3.', 'H4.', 'H5.']:
            self.assertIn(code, new['messages'][0]['content'])
        self.assertNotIn('L1.1.1', new['messages'][0]['content'])

    def test_unexpected_metadata_rejected(self):
        packet = dict(self.packet(), model='hidden-model-name')
        request = {'audit_id':'id', 'messages':[
            {'role':'system', 'content':'old'}, {'role':'user','content':json.dumps(packet)}]}
        with self.assertRaises(ValueError):
            convert(request)


if __name__ == '__main__':
    unittest.main()
