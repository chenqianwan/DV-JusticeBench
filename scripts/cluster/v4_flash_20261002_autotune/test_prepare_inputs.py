"""Offline unit tests for deterministic, bounded-memory performance sampling."""
import copy
import hashlib
import itertools
import json
import random
import unittest

from scripts.cluster.v4_flash_20261002_autotune.prepare_inputs import (
    DiverseTopN, PARAMETERS, SLOTS, compact_row, group_targets, identity, identity_hash,
)
from scripts.hallucination_h1_h5_20261002.contract import build_messages, packet_hash


class PreparationTests(unittest.TestCase):
    def test_balanced_targets(self):
        groups = group_targets(['ds_nonthinking', 'ds_thinking', 'gpt-4o', 'gemini-2.5-flash', 'qwen-max'])
        self.assertEqual(len(groups), 10)
        self.assertEqual(sum(groups.values()), SLOTS)
        self.assertEqual(sorted(groups.values()), [102] * 6 + [103] * 4)

    def test_topn_one_case_and_order_invariant(self):
        rows = [(90, 'a'), (30, 'a'), (11, 'b'), (22, 'c'), (8, 'a'), (15, 'd'), (5, 'e')]
        expected = [(5, 'e'), (8, 'a'), (11, 'b')]
        for seed in range(100):
            shuffled = list(rows)
            random.Random(seed).shuffle(shuffled)
            selector = DiverseTopN(3)
            for rank, case in shuffled:
                selector.add(rank, case, (rank, case))
            self.assertEqual(selector.selected(), expected)
            self.assertLessEqual(len(selector.rows), 3)
            self.assertLessEqual(len(selector.heap), 6)

    def test_heap_remains_bounded_when_same_cases_replaced(self):
        selector = DiverseTopN(4)
        for rank in range(10000, 0, -1):
            selector.add(rank, str(rank % 4), rank)
            self.assertLessEqual(len(selector.heap), 8)
            self.assertLessEqual(len(selector.rows), 4)
        self.assertEqual(selector.selected(), [1, 2, 3, 4])

    def test_matches_global_best_per_case(self):
        rows = [(int(identity_hash(('primary', str(c), str(q), 'gpt-4o')), 16), str(c))
                for c, q in itertools.product(range(100), range(5))]
        best = {}
        for rank, case in rows:
            best[case] = min(rank, best.get(case, rank))
        expected = sorted((rank, case) for case, rank in best.items())[:13]
        selector = DiverseTopN(13)
        random.Random(15).shuffle(rows)
        for rank, case in rows:
            selector.add(rank, case, (rank, case))
        self.assertEqual(selector.selected(), expected)

    def row(self):
        row = dict(split='primary', case_id='case1', question_id='q1',
                   answer_condition='gpt-4o', main_denominator_eligible=True,
                   case_text_masked='材料', question_text='问题', judicial_reference='理由', answer='回答')
        for value, digest in [('case_text_masked', 'masked_case_text_hash'),
                              ('question_text', 'question_hash'),
                              ('judicial_reference', 'judge_decision_hash'), ('answer', 'answer_sha256')]:
            row[digest] = hashlib.sha256(row[value].encode()).hexdigest()
        return row

    def test_source_text_preserved_and_prompt_exact(self):
        row = self.row()
        selected = compact_row(row, 8)
        messages, packet = build_messages(**{field: selected[field] for field in
            ('case_text', 'question', 'judge_decision', 'answer')})
        self.assertEqual(packet['answer'], row['answer'])
        self.assertEqual(packet['case_text'], row['case_text_masked'])
        self.assertEqual(packet['reference_pack'], {'evidence': [], 'checklists': [], 'coverage': {}})
        self.assertEqual(packet_hash(packet), packet_hash(json.loads(messages[1]['content'])))
        self.assertNotIn('answer_condition', json.loads(messages[1]['content']))
        self.assertEqual(selected['identity'], identity(row))

    def test_corrupted_or_ineligible_source_fails_closed(self):
        for field, value in [('answer_sha256', 'bad'), ('main_denominator_eligible', False),
                             ('case_text_masked', ''), ('answer_condition', '')]:
            row = copy.deepcopy(self.row())
            row[field] = value
            with self.assertRaises(ValueError):
                compact_row(row, 1)

    def test_engineering_parameters_no_retry_no_length_shortcut(self):
        self.assertEqual(PARAMETERS['temperature'], 0.1)
        self.assertIs(PARAMETERS['thinking'], False)
        self.assertEqual(PARAMETERS['max_tokens'], 3000)
        self.assertEqual(PARAMETERS['retries'], 0)
        self.assertIs(PARAMETERS['length_retry'], False)


if __name__ == '__main__':
    unittest.main()
