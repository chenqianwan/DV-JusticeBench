import unittest

from prepare_smoke import key, select_cases


class SmokePreparationTests(unittest.TestCase):
    def test_selection_reproducible_without_answers_or_scores(self):
        pairs = [(s, 'case_' + str(i)) for s in ['primary', 'secondary'] for i in range(20)]
        selected = select_cases(pairs)
        self.assertEqual(selected, select_cases(list(reversed(pairs))))
        self.assertEqual(len(selected), 4)
        self.assertEqual(sum(s == 'primary' for s, _ in selected), 2)
        self.assertEqual(sum(s == 'secondary' for s, _ in selected), 2)

    def test_insufficient_group_fails_closed(self):
        with self.assertRaises(ValueError):
            select_cases([('primary', 'one'), ('secondary', 'two')])

    def test_key_keeps_thinking_conditions_separate(self):
        base = {'split': 'primary', 'case_id': 'c', 'question_id': 'q'}
        self.assertNotEqual(key(dict(base, answer_condition='ds_thinking')),
                            key(dict(base, answer_condition='ds_nonthinking')))


if __name__ == '__main__':
    unittest.main()
