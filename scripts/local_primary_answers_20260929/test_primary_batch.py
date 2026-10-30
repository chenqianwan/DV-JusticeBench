"""Offline release checks. No paid API, SSH, or worker launch."""
import copy
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import run_primary_batch as batch


class PrimaryBatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory()
        cls.out = Path(cls.temp.name)
        market = batch.read(batch.pilot.OUT / 'market_20260929T100048911238Z.json')
        market['checked_at'] = batch.now()
        cls.market = cls.out / 'input_market.json'
        batch.write(cls.market, market)
        with patch.object(batch.pilot, 'credentials', side_effect=AssertionError('prepare accessed credentials')):
            batch.prepare(cls.out, cls.market)
        cls.tasks = batch.read(cls.out / 'input_tasks.json')['tasks']

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def test_real_scope_and_reuse(self):
        tasks, identity = batch.load(self.out)
        self.assertEqual(len(tasks), 3225)
        self.assertTrue(identity['full_run_authorized'])
        self.assertFalse(batch.read(batch.pilot.OUT / 'manifest.json')['full_run_authorized'])
        counts, _, done, unresolved = batch.scan(tasks, self.out / 'results', True)
        self.assertEqual(dict(counts), {'ok': 15})
        self.assertEqual(len(done), 15)
        self.assertFalse(unresolved)
        self.assertFalse((self.out / 'results/budget_ledger.json').exists())
        self.assertTrue((self.out / 'reused_pilot_audit/budget_ledger.json').exists())
        self.assertEqual(identity['prepaid_usd'], .128728)
        self.assertEqual(len(identity['code_sha256']), 3)

    def test_scope_secondary_and_exclusions_and_size(self):
        exclusions = {x['case_id'] for x in batch.read(batch.pilot.SOURCE / 'excluded_cases.json')}
        for change in ({'split':'secondary'}, {'case_id':next(iter(exclusions))}, {'model':'other'}):
            tasks = list(self.tasks)
            tasks[0] = dict(tasks[0], **change)
            with self.assertRaises(ValueError):
                batch.validate_scope(tasks, exclusions)
        with self.assertRaises(ValueError):
            batch.validate_scope(self.tasks[:-1], exclusions)

    def test_immutable_code_identity(self):
        with patch.object(batch, 'code_identity', return_value={}):
            with self.assertRaisesRegex(ValueError, 'runner or helper'):
                batch.load(self.out)

    def test_existing_success_has_no_transport(self):
        task = batch.pilot.load_pilot(batch.pilot.OUT)[0]
        class Never:
            def post(self, *args, **kwargs):
                raise AssertionError('Successful reused answer was rerun')
        runner = batch.pilot.Runner([task], self.out / 'results', Never(), 'https://example.invalid/v1', 'fake')
        result = runner.task(task)
        self.assertEqual(result['status'], 'ok')

    def test_unknown_or_nonterminal_blocks(self):
        task = self.tasks[0]
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / batch.pilot.task_id(task)
            batch.write(directory / 'attempt_001.request.json', {})
            with self.assertRaisesRegex(ValueError, 'requires reconciliation'):
                batch.scan([task], Path(td), True)

    def test_unknown_terminal_blocks(self):
        task = self.tasks[0]
        with tempfile.TemporaryDirectory() as td:
            directory = Path(td) / batch.pilot.task_id(task)
            batch.write(directory / 'terminal.json', dict(task_sha256=batch.sha(batch.canonical(task)),status='needs_reconcile'))
            with self.assertRaises(ValueError):
                batch.scan([task], Path(td), True)

    def test_no_exit_blocks_resume(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            batch.write(out / 'sessions/001.intent.json', {})
            with self.assertRaisesRegex(ValueError, 'lacks exit receipt'):
                batch.previous_guard(out, True)
            batch.write(out / 'sessions/001.exit.json', {'exit_code':2})
            batch.previous_guard(out, True)
            with self.assertRaises(ValueError):
                batch.previous_guard(out, False)

    def test_live_pid_blocks_even_with_exit(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            batch.write(out / 'sessions/001.intent.json', {})
            batch.write(out / 'sessions/001.launch.json', batch.identity_pid(__import__('os').getpid()))
            batch.write(out / 'sessions/001.exit.json', {'exit_code':0})
            with self.assertRaisesRegex(ValueError, 'still alive'):
                batch.previous_guard(out, True)

    def test_duplicate_internal_worker_cannot_forge_exit(self):
        with tempfile.TemporaryDirectory() as td:
            out = Path(td)
            (out / 'results').mkdir()
            batch.write(out / 'sessions/001.start.json', {'owner':'old'})
            with patch.object(batch.pilot, 'credentials', side_effect=AssertionError('No credentials allowed')):
                with self.assertRaisesRegex(ValueError, 'already has an owner'):
                    batch.worker(out, Path('unused.env'), '001')
            self.assertFalse((out / 'sessions/001.exit.json').exists())
            self.assertEqual(batch.read(out / 'sessions/001.start.json'), {'owner':'old'})
            self.assertEqual(len(list((out / 'sessions').glob('001.startup_failure.*.json'))),1)

    def test_bounded_per_model_schedule_skips_done(self):
        tasks = self.tasks[:90]
        done = {batch.pilot.task_id(t) for t in tasks[:15]}
        active, peak, called = Counter(), Counter(), []
        lock = threading.Lock()
        class FakeRunner:
            stop = threading.Event()
            def task(self, task):
                with lock:
                    active[task['model']] += 1
                    peak[task['model']] = max(peak[task['model']], active[task['model']])
                    called.append(batch.pilot.task_id(task))
                time.sleep(.005)
                with lock:
                    active[task['model']] -= 1
                return {'model':task['model'],'status':'ok'}
        batch.bounded_schedule(tasks, FakeRunner(), done, lambda *_: None, workers=3)
        self.assertEqual(len(called),75)
        self.assertFalse(set(called) & done)
        self.assertTrue(all(value <= 3 for value in peak.values()))

    def test_stop_does_not_submit_backlog(self):
        tasks = self.tasks[:300]
        calls = []
        class FakeRunner:
            stop = threading.Event()
            def task(self, task):
                calls.append(task)
                self.stop.set()
                return None
        batch.bounded_schedule(tasks, FakeRunner(), set(), lambda *_: None)
        self.assertLessEqual(len(calls),24)

    def test_budget_denial_has_not_sent_and_no_terminal(self):
        class Deny:
            def post(self,*args,**kwargs):
                raise batch.pilot.BudgetStop('offline test budget')
        with tempfile.TemporaryDirectory() as td:
            runner = batch.pilot.Runner([self.tasks[0]], Path(td), Deny(), 'https://example.invalid/v1','fake')
            self.assertIsNone(runner.task(self.tasks[0]))
            receipt = next(Path(td).glob('task_*/attempt_001.response.json'))
            self.assertTrue(batch.read(receipt)['not_sent'])
            self.assertTrue(runner.stop.is_set())
            self.assertFalse(list(Path(td).glob('task_*/terminal.json')))


if __name__ == '__main__':
    unittest.main()
