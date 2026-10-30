"""Offline continuation audit: real inputs read-only; all writes are temporary.

The production artifacts are never changed and every HTTP request is blocked.
"""
from collections import Counter
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import run_secondary_continuation_20260930 as batch


class ContinuationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = patch('requests.sessions.Session.request',
                         side_effect=AssertionError('HTTP forbidden in offline tests')).start()
        cls.addClassCleanup(patch.stopall)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.out = cls.root / 'prepared'
        # This validates real source hashes, identities and physical receipts.
        with patch.object(batch.pilot, 'credentials',
                          side_effect=AssertionError('No credentials in prepare')), \
                patch.object(batch.pilot, 'validate_market'):
            cls.prepared = batch.prepare(cls.out, batch.SOURCES[1] / 'market_snapshot.json')
        cls.tasks, cls.ident = batch.load(cls.out)
        cls.audit = batch.read(cls.out / 'source_audit.json')
        cls.original = {batch.pilot.task_id(t): t for t in
                        batch.read(batch.SOURCES[0] / 'input_tasks.json')['tasks']}

    def setUp(self):
        self.tmp_case = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_case.cleanup)
        self.case_root = Path(self.tmp_case.name)

    @classmethod
    def tearDownClass(cls):
        cls.http.assert_not_called()

    def runner(self):
        class Transport:
            def __init__(self):
                self.stopped = threading.Event()
        return batch.ContinuationRunner([], self.case_root / 'results', Transport(),
                                        'https://invalid.example/v1', 'offline', threading.Event())

    def test_actual_partition_excludes_decided_and_all_unknown(self):
        selected = {batch.pilot.task_id(t) for t in self.tasks}
        previous = self.audit['prior_terminal']
        self.assertEqual(len(self.tasks), 16072)
        self.assertEqual(len(selected), 16072)
        self.assertFalse(selected & set(previous))
        self.assertEqual(len(previous), 13988)
        self.assertEqual(sum(r['status'] == 'needs_reconcile' for r in previous.values()), 69)
        self.assertEqual(sum(r['status'] != 'needs_reconcile' for r in previous.values()), 13919)
        self.assertEqual(selected | set(previous), set(self.original))
        self.assertEqual(Counter(t['model'] for t in self.tasks),
                         {'gpt-4o': 5881, 'gemini-2.5-flash': 2986, 'qwen-max': 7205})

    def test_every_selected_task_has_no_prior_physical_attempt(self):
        for task in self.tasks:
            tid = batch.pilot.task_id(task)
            for source in batch.SOURCES:
                directory = source / 'results' / tid
                self.assertFalse(list(directory.glob('attempt_*.request.json')), tid)
                self.assertFalse(list(directory.glob('attempt_*.response.json')), tid)
                self.assertFalse((directory / 'terminal.json').exists(), tid)

    def test_scientific_payload_is_exact_original_for_both_token_limits(self):
        for task in self.tasks:
            original = self.original[batch.pilot.task_id(task)]
            self.assertEqual(task, original)
            self.assertEqual(task['split'], 'secondary')
            for maximum in (3000, 6000):
                payload = batch.pilot.payload(task, maximum)
                self.assertEqual(payload, batch.pilot.payload(original, maximum))
                self.assertEqual(payload, {'model': task['model'], 'messages': task['messages'],
                                           'temperature': .3, 'max_tokens': maximum})
                self.assertNotIn('thinking', payload)

    def test_budget_is_cumulative_not_new_allowance(self):
        ledger = batch.read(batch.SOURCES[1] / 'results/budget_ledger.json')
        self.assertEqual(self.ident['prepaid_usd'], 148.8391970689192)
        self.assertEqual(self.audit['prepaid_usd'], self.ident['prepaid_usd'])
        self.assertEqual(self.ident['prepaid_usd'], ledger['accounted_including_prepaid_usd'])
        self.assertEqual(self.ident['budget_usd'], 350.0)
        self.assertAlmostEqual(350 - self.ident['prepaid_usd'], 201.1608029310808)
        self.assertFalse(ledger['reservations'])
        self.assertFalse(self.ident['scoring_performed'])

    def test_history_explicitly_preserves_three_completed_and_fourth_rejection(self):
        self.assertEqual(self.audit['prior_network_recoveries_completed'], 3)
        self.assertEqual(self.audit['prior_pause_attempts'], [1, 2, 3, 4])
        self.assertTrue(self.audit['prior_exhaustion_preserved'])
        self.assertEqual(self.ident['inherited_network_recoveries'], 3)
        self.assertEqual(self.ident['lifetime_network_recovery_limit'], 4)
        self.assertIn('3 -> 4 lifetime', self.ident['recovery_amendment'])

    def test_capacity_never_promotes_above_four(self):
        runner = self.runner()
        for model in batch.pilot.MODELS:
            self.assertEqual(runner.capacity(model), 4)
            runner.successes[model] = 10000
            runner.limits[model] = 16
            self.assertEqual(runner.capacity(model), 4)
            runner.limits[model] = 2
            self.assertEqual(runner.capacity(model), 2)

    def test_only_one_additional_network_recovery(self):
        runner = self.runner()
        self.assertEqual(runner.recovery_cycles, 3)
        runner.pause.set()
        with patch.object(runner.stop, 'wait', return_value=False), \
                patch.object(batch.base, 'health', return_value={'ok': True}) as probe:
            self.assertTrue(runner.recover_connection())
            self.assertEqual(probe.call_count, 2)
        self.assertEqual(runner.recovery_cycles, 4)
        self.assertFalse(runner.pause.is_set())
        runner.pause.set()
        with patch.object(batch.base, 'health') as probe:
            self.assertFalse(runner.recover_connection())
            probe.assert_not_called()
        self.assertTrue(runner.stop.is_set())

    def test_network_recovery_count_survives_new_runner_instance(self):
        runner = self.runner()
        with patch.object(runner.stop, 'wait', return_value=False), \
                patch.object(batch.base, 'health', return_value={'ok': True}):
            self.assertTrue(runner.recover_connection())
        resumed = self.runner()
        self.assertEqual(resumed.recovery_cycles, 4)
        with patch.object(batch.base, 'health') as probe:
            self.assertFalse(resumed.recover_connection())
            probe.assert_not_called()

    def test_failed_probe_cycle_is_bounded_and_persisted(self):
        runner = self.runner()
        with patch.object(runner.stop, 'wait', return_value=False) as wait, \
                patch.object(batch.base, 'health', return_value={'ok': False}) as probe:
            self.assertFalse(runner.recover_connection())
        self.assertEqual(probe.call_count, 5)
        self.assertEqual([x.args[0] for x in wait.call_args_list], [30, 60, 120, 300, 300])
        self.assertTrue(runner.stop.is_set())
        self.assertEqual(self.runner().recovery_cycles, 4)

    def test_probe_requires_two_successes_and_never_posts(self):
        runner = self.runner()
        with patch.object(runner.stop, 'wait', return_value=False), \
                patch.object(batch.base, 'health', side_effect=[
                    {'ok': True}, {'ok': False}, {'ok': True}, {'ok': True}]) as probe:
            self.assertTrue(runner.recover_connection())
        self.assertEqual(probe.call_count, 4)
        self.assertTrue(all(runner.capacity(m) <= 4 for m in batch.pilot.MODELS))

    def test_permanent_stop_does_not_probe_or_resume(self):
        runner = self.runner()
        runner.stop.set()
        with patch.object(batch.base, 'health') as probe:
            self.assertFalse(runner.recover_connection())
            probe.assert_not_called()
        self.assertTrue(runner.stop.is_set())

    def test_missing_exit_receipt_blocks_launch(self):
        target = self.case_root / 'queue'
        batch.write(target / 'sessions/001.intent.json', {})
        with patch.object(batch, 'source_locks', return_value=[]), \
                patch.object(batch, 'load', return_value=(self.tasks[:1], self.ident)), \
                patch.object(batch.pilot, 'credentials') as credentials:
            with self.assertRaisesRegex(ValueError, 'exit receipt'):
                batch.launch(target, self.case_root / 'no-env', resume=True)
            credentials.assert_not_called()

    def test_live_identity_blocks_launch_even_with_exit_receipt(self):
        target = self.case_root / 'queue'
        batch.write(target / 'sessions/001.intent.json', {})
        batch.write(target / 'sessions/001.launch.json', {'pid': 999, 'process_start': 'same identity'})
        batch.write(target / 'sessions/001.exit.json', {'exit_code': 2})
        with patch.object(batch, 'source_locks', return_value=[]), \
                patch.object(batch, 'load', return_value=(self.tasks[:1], self.ident)), \
                patch.object(batch.previous, 'is_alive', return_value=True), \
                patch.object(batch.pilot, 'credentials') as credentials:
            with self.assertRaisesRegex(ValueError, 'still alive'):
                batch.launch(target, self.case_root / 'no-env', resume=True)
            credentials.assert_not_called()

    def test_held_source_lock_blocks_before_loading_or_network(self):
        target = self.case_root / 'queue'
        lock = self.case_root / 'source/results/run.lock'
        with batch.previous.exclusive(lock), \
                patch.object(batch, 'source_locks', return_value=[lock]), \
                patch.object(batch, 'load') as load:
            with self.assertRaisesRegex(ValueError, 'holds lock'):
                batch.launch(target, self.case_root / 'no-env')
            load.assert_not_called()

    def test_unknown_terminal_blocks_explicit_resume(self):
        target = self.case_root / 'queue'
        task = self.tasks[0]
        batch.write(target / 'sessions/001.intent.json', {})
        batch.write(target / 'sessions/001.exit.json', {'exit_code': 2})
        batch.write(target / 'results' / batch.pilot.task_id(task) / 'terminal.json',
                    {'task_sha256': batch.sha(batch.canonical(task)), 'status': 'needs_reconcile'})
        with patch.object(batch, 'source_locks', return_value=[]), \
                patch.object(batch, 'load', return_value=([task], self.ident)), \
                patch.object(batch.pilot, 'credentials') as credentials:
            with self.assertRaisesRegex(ValueError, 'requires reconciliation'):
                batch.launch(target, self.case_root / 'no-env', resume=True)
            credentials.assert_not_called()

    def test_missing_request_response_blocks_explicit_resume(self):
        target = self.case_root / 'queue'
        task = self.tasks[0]
        batch.write(target / 'sessions/001.intent.json', {})
        batch.write(target / 'sessions/001.exit.json', {'exit_code': 2})
        batch.write(target / 'results' / batch.pilot.task_id(task) / 'attempt_001.request.json', {})
        with patch.object(batch, 'source_locks', return_value=[]), \
                patch.object(batch, 'load', return_value=([task], self.ident)), \
                patch.object(batch.pilot, 'credentials') as credentials:
            with self.assertRaisesRegex(ValueError, 'requires reconciliation'):
                batch.launch(target, self.case_root / 'no-env', resume=True)
            credentials.assert_not_called()

    def test_prepaid_tampering_rejected(self):
        real_read = batch.read
        def altered(path):
            row = real_read(path)
            if Path(path) == self.out / 'authorization.json':
                row = copy.deepcopy(row)
                row['prepaid_usd'] = 0
            return row
        with patch.object(batch, 'read', side_effect=altered):
            with self.assertRaisesRegex(ValueError, 'Prior cost not preserved'):
                batch.load(self.out)

    def test_cap_or_code_tampering_rejected(self):
        real_read = batch.read
        for field, value in [('per_model_workers', 16), ('budget_usd', 700),
                             ('lifetime_network_recovery_limit', 100), ('code_sha256', {})]:
            def altered(path):
                row = real_read(path)
                if Path(path) == self.out / 'authorization.json':
                    row = copy.deepcopy(row)
                    row[field] = value
                return row
            with self.subTest(field=field), patch.object(batch, 'read', side_effect=altered):
                with self.assertRaisesRegex(ValueError, 'Frozen continuation differs'):
                    batch.load(self.out)


if __name__ == '__main__':
    unittest.main()
