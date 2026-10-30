import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('full_prepare', Path(__file__).with_name('prepare.py'))
p = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(p)


class PrepareTests(unittest.TestCase):
    def row(self, i=0):
        row = {'split': 'primary', 'case_id': 'case1', 'question_id': f'q{i}',
               'answer_condition': 'gpt-4o', 'status': 'complete', 'answer_status': 'ok',
               'main_denominator_eligible': True}
        for target, (source, digest) in p.TEXT_FIELDS.items():
            row[source] = {'case_text': '事实。\n', 'question': '问题？', 'judge_decision': '裁判参考。',
                           'answer': '原回答。金额1.25元。\n'}[target]
            row[digest] = p.sha(row[source].encode())
        return row

    def test_compact_preserves_exact_text_and_blinds(self):
        row = self.row()
        packet, meta = p.compact(row, 9)
        self.assertEqual(meta['global_index'], 9)
        self.assertEqual(meta['source_line'], 10)
        for target, (source, _) in p.TEXT_FIELDS.items():
            self.assertEqual(packet[target], row[source])
        self.assertNotIn('answer_condition', packet)
        self.assertEqual(packet['reference_pack'], {'evidence': [], 'checklists': [], 'coverage': {}})

    def test_hash_mismatch_rejected(self):
        row = self.row()
        row['answer'] += '改写'
        with self.assertRaisesRegex(ValueError, 'hash mismatch'):
            p.compact(row, 0)

    def test_exception_not_promoted(self):
        for field, value in [('status', 'truncated'), ('answer_status', 'content_filter'), ('main_denominator_eligible', False)]:
            row = self.row()
            row[field] = value
            with self.assertRaisesRegex(ValueError, 'Ineligible'):
                p.compact(row, 0)

    def test_identity_compatible_with_old_pilot(self):
        old = p.REPO / 'data/pkulaw_v4_autotune_20261002/prepared/private_mapping.jsonl'
        with old.open() as stream:
            row = json.loads(next(stream))
        self.assertEqual(row['request_id'], p.request_id(p.identity(row)))

    def test_balanced_shards_reproducible_independent_of_input_order(self):
        records = []
        for split in ['primary', 'secondary']:
            for condition in ['a', 'b', 'c', 'd', 'e']:
                for i in range(11 if condition != 'b' else 8):
                    records.append({'split': split, 'answer_condition': condition,
                                    'request_id': split + condition + str(i)})
        assignment, groups = p.assign_shards(records)
        reverse, groups_reverse = p.assign_shards(list(reversed(records)))
        self.assertEqual(assignment, reverse)
        self.assertEqual(groups, groups_reverse)
        self.assertEqual(len(assignment), len(records))
        totals = p.Counter(assignment.values())
        self.assertEqual(totals[p.HOSTS[0]], totals[p.HOSTS[1]])
        self.assertTrue(all(abs(g[p.HOSTS[0]] - g[p.HOSTS[1]]) <= 1 for g in groups))

    def test_duplicate_identity_rejected(self):
        row = {'split': 'primary', 'answer_condition': 'a', 'request_id': 'same'}
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            p.assign_shards([row, row])

    def test_no_overwrite_prepared_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'Refuse to overwrite'):
                p.prepare(Path(directory))

    def test_reuse_checks_input_before_outcome(self):
        row = {'request_id': 'same', 'messages': ['current']}
        previous = {'request_id': 'same', 'messages': ['different']}
        with self.assertRaisesRegex(ValueError, 'exact input differs'):
            p.reused_record(row, p.HOSTS[0], previous, '/not/accessed')

    def test_freeze_code_refuses_staged_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'stage_manifest.json').touch()
            with self.assertRaisesRegex(ValueError, 'Already staged'):
                p.freeze_code(root / 'prepared')

    def test_freeze_requires_six_exact_dependencies(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ValueError, 'six runtime'):
                p.freeze_code(Path(directory), {})


if __name__ == '__main__':
    unittest.main()
