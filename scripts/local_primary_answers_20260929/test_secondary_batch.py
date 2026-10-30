"""Offline secondary release tests. No paid API, SSH, or background launch."""
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import run_secondary_batch as batch


class SecondaryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.tmp.name)
        market = batch.read(batch.pilot.OUT / 'market_20260929T100048911238Z.json')
        market['checked_at'] = batch.now()
        cls.market = cls.out / 'new_market.json'
        batch.write(cls.market, market)
        with patch.object(batch.pilot, 'credentials', side_effect=AssertionError('No credentials during prepare')):
            batch.prepare(cls.out, cls.market)
        cls.tasks = batch.read(cls.out / 'input_tasks.json')['tasks']
        cls.excluded = {r['case_id'] for r in batch.read(cls.out / 'excluded_cases.json')}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_scope_no_paid_calls_or_primary(self):
        tasks, identity = batch.load(self.out)
        self.assertEqual(len(tasks), 30060)
        self.assertEqual(identity['cases'], 2004)
        self.assertEqual(identity['prepaid_usd'], 0)
        self.assertEqual(identity['scoring_calls'], 0)
        self.assertEqual(len(identity['code_sha256']), 4)
        self.assertFalse((self.out / 'results').exists())

    def test_wrong_scope_and_duplicate_rejected(self):
        for change in [{'split': 'primary'}, {'case_id': next(iter(self.excluded))}, {'model': 'new-model'}]:
            tasks = list(self.tasks)
            tasks[0] = dict(tasks[0], **change)
            with self.assertRaises(ValueError):
                batch.validate_scope(tasks, self.excluded)
        with self.assertRaises(ValueError):
            batch.validate_scope(self.tasks[:-1], self.excluded)
        tasks = list(self.tasks)
        tasks[0] = tasks[1]
        with self.assertRaises(ValueError):
            batch.validate_scope(tasks, self.excluded)

    def test_scientific_payload_unchanged(self):
        for t in self.tasks[:15]:
            for maximum in [3000, 6000]:
                self.assertEqual(batch.pilot.payload(t, maximum), {
                    'model': t['model'], 'messages': t['messages'], 'temperature': .3, 'max_tokens': maximum})
        self.assertEqual(batch.WORKERS, batch.previous.WORKERS)
        self.assertEqual(batch.TIMEOUT, batch.previous.TIMEOUT)

    def test_frozen_code_check(self):
        with patch.object(batch, 'code_identity', return_value={}):
            with self.assertRaisesRegex(ValueError, 'helper changed'):
                batch.load(self.out)

    def test_local_stop_isolates_unknown_and_honors_global_stop(self):
        shared = threading.Event()
        a, b = batch.LocalStop(shared), batch.LocalStop(shared)
        a.set()
        self.assertTrue(a.is_set())
        self.assertFalse(b.is_set())
        shared.set()
        self.assertTrue(b.is_set())

    def test_unknown_isolated_but_three_consecutive_stop(self):
        with tempfile.TemporaryDirectory() as td:
            stop = threading.Event()
            class Transport:
                stopped = threading.Event()
            runner = batch.IsolatedRunner([], Path(td), Transport(), 'https://invalid.example', 'fake', stop)
            with patch.object(batch.pilot.Runner, 'task', return_value={'status': 'needs_reconcile'}):
                runner.task(self.tasks[0])
                self.assertFalse(stop.is_set())
                runner.task(self.tasks[0])
                self.assertFalse(stop.is_set())
                runner.task(self.tasks[0])
                self.assertTrue(stop.is_set())
                runner.task(self.tasks[0])
                self.assertTrue((Path(td) / 'circuit_breaker.json').exists())

    def test_existing_answer_not_sent(self):
        task = self.tasks[0]
        with tempfile.TemporaryDirectory() as td:
            class Transport:
                stopped = threading.Event()
                def post(self, *args, **kwargs):
                    raise AssertionError('Successful answer resent')
            result = dict(task, status='ok', task_sha256=batch.sha(batch.canonical(task)), answer='unchanged')
            batch.write(Path(td) / batch.pilot.task_id(task) / 'terminal.json', result, True)
            runner = batch.IsolatedRunner([task], Path(td), Transport(), 'https://invalid.example', 'fake', threading.Event())
            self.assertEqual(runner.task(task)['answer'], 'unchanged')

    def test_budget_stop_propagates_without_terminal(self):
        with tempfile.TemporaryDirectory() as td:
            stop = threading.Event()
            class Transport:
                stopped = threading.Event()
                def post(self, *args, **kwargs):
                    self.stopped.set()
                    raise batch.pilot.BudgetStop('offline')
            runner = batch.IsolatedRunner([], Path(td), Transport(), 'https://invalid.example', 'fake', stop)
            self.assertIsNone(runner.task(self.tasks[0]))
            self.assertTrue(stop.is_set())
            self.assertFalse(list(Path(td).glob('task_*/terminal.json')))
            self.assertTrue(batch.read(next(Path(td).glob('task_*/attempt_001.response.json')))['not_sent'])

    def test_unknown_blocks_resume(self):
        with tempfile.TemporaryDirectory() as td:
            task = self.tasks[0]
            batch.write(Path(td) / batch.pilot.task_id(task) / 'terminal.json', {
                'task_sha256': batch.sha(batch.canonical(task)), 'status': 'needs_reconcile'})
            with self.assertRaises(ValueError):
                batch.previous.scan([task], Path(td), reject_unknown=True)

    def test_previous_without_exit_blocks(self):
        with tempfile.TemporaryDirectory() as td:
            batch.write(Path(td) / 'sessions/001.intent.json', {})
            with self.assertRaisesRegex(ValueError, 'exit receipt'):
                batch.previous.previous_guard(Path(td), True)


if __name__ == '__main__':
    unittest.main()
