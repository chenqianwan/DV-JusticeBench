"""Offline tests for the user-authorized hourly secondary reconnect controller.

Production inputs and receipts are read-only. Every HTTP request is forbidden;
controller tests mock process launch before execution. All
test-created artifacts live in TemporaryDirectory.
"""
from collections import Counter
from contextlib import ExitStack
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import run_secondary_hourly_reconnect as batch


class HourlyReconnectTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = patch('requests.sessions.Session.request',
                         side_effect=AssertionError('HTTP forbidden in offline tests')).start()
        cls.addClassCleanup(patch.stopall)
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.root = Path(cls.tmp.name)
        cls.tasks, cls.audit = batch.audit_sources(batch.INITIAL_SOURCES)
        cls.original = {batch.pilot.task_id(t): t for t in
                        batch.read(batch.INITIAL_SOURCES[0] / 'input_tasks.json')['tasks']}
        cls.out = cls.root / 'frozen_batch'
        with patch.object(batch.pilot, 'validate_market'):
            batch.prepare_batch(cls.out, batch.INITIAL_SOURCES[-1] / 'market_snapshot.json',
                                cls.tasks, cls.audit, cls.root / 'controller')
        cls.loaded_tasks, cls.auth = batch.load_batch(cls.out)

    def setUp(self):
        self.tmp_case = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_case.cleanup)
        self.case_root = Path(self.tmp_case.name)

    @classmethod
    def tearDownClass(cls):
        cls.http.assert_not_called()

    def test_actual_three_source_partition(self):
        selected = {batch.pilot.task_id(t) for t in self.tasks}
        prior = self.audit['prior_terminal']
        self.assertEqual(len(self.tasks), 16046)
        self.assertEqual(len(selected), 16046)
        self.assertEqual(len(prior), 14014)
        self.assertFalse(selected & set(prior))
        self.assertEqual(selected | set(prior), set(self.original))
        self.assertEqual(Counter(r['status'] for r in prior.values()),
                         {'ok': 13833, 'content_filter': 97, 'needs_reconcile': 84})
        self.assertEqual(Counter(t['model'] for t in self.tasks),
                         {'gpt-4o': 5873, 'gemini-2.5-flash': 2976, 'qwen-max': 7197})

    def test_all_pending_tasks_have_never_been_sent(self):
        for task in self.tasks:
            tid = batch.pilot.task_id(task)
            for source in batch.INITIAL_SOURCES:
                directory = source / 'results' / tid
                self.assertFalse(list(directory.glob('attempt_*.request.json')), tid)
                self.assertFalse(list(directory.glob('attempt_*.response.json')), tid)
                self.assertFalse((directory / 'terminal.json').exists(), tid)

    def test_every_payload_exact_and_dirty_cases_excluded(self):
        excluded = {x['case_id'] for x in
                    batch.read(batch.INITIAL_SOURCES[0] / 'excluded_cases.json')}
        self.assertEqual(len(excluded), 18)
        for task in self.tasks:
            self.assertEqual(task, self.original[batch.pilot.task_id(task)])
            self.assertEqual(task['split'], 'secondary')
            self.assertNotIn(task['case_id'], excluded)
            for maximum in (3000, 6000):
                self.assertEqual(batch.pilot.payload(task, maximum),
                                 {'model': task['model'], 'messages': task['messages'],
                                  'temperature': .3, 'max_tokens': maximum})

    def test_all_three_cost_ledgers_form_one_chain(self):
        ledgers = [batch.read(source / 'results/budget_ledger.json')
                   for source in batch.INITIAL_SOURCES]
        for first, second in zip(ledgers, ledgers[1:]):
            self.assertEqual(second['prepaid_usd'], first['accounted_including_prepaid_usd'])
        self.assertEqual(self.audit['prepaid_usd'], 149.4882372998792)
        self.assertEqual(self.audit['prepaid_usd'], ledgers[-1]['accounted_including_prepaid_usd'])
        self.assertEqual(batch.BUDGET, 350.0)
        self.assertAlmostEqual(batch.BUDGET - self.audit['prepaid_usd'], 200.5117627001208)
        self.assertTrue(all(not ledger['reservations'] for ledger in ledgers))

    def test_live_source_identity_blocks_audit(self):
        with patch.object(batch.previous, 'is_alive', return_value=True):
            with self.assertRaisesRegex(ValueError, 'alive|running|active'):
                batch.audit_sources(batch.INITIAL_SOURCES)

    def test_missing_source_exit_blocks_audit(self):
        source = batch.INITIAL_SOURCES[0]
        target = sorted((source / 'sessions').glob('*.exit.json'))[-1]
        real_exists = Path.exists
        def exists(path):
            return False if path == target else real_exists(path)
        with patch.object(Path, 'exists', exists):
            with self.assertRaisesRegex(ValueError, 'exit receipt|missing.*exit'):
                batch.audit_sources(batch.INITIAL_SOURCES)

    def test_outstanding_source_reservation_blocks_audit(self):
        ledger_path = batch.INITIAL_SOURCES[0] / 'results/budget_ledger.json'
        real_read = batch.read
        def altered(path):
            row = real_read(path)
            if Path(path) == ledger_path:
                row = copy.deepcopy(row)
                row['reservations'] = {'offline-request': {'reserve_usd': 1.0}}
            return row
        with patch.object(batch, 'read', side_effect=altered):
            with self.assertRaises(ValueError):
                batch.audit_sources(batch.INITIAL_SOURCES)

    def test_source_abnormal_exit_blocks_audit(self):
        target = sorted((batch.INITIAL_SOURCES[0] / 'sessions').glob('*.exit.json'))[-1]
        real_read = batch.read
        def altered(path):
            row = real_read(path)
            if Path(path) == target:
                row = copy.deepcopy(row)
                row['error_type'] = 'RuntimeError'
            return row
        with patch.object(batch, 'read', side_effect=altered):
            with self.assertRaisesRegex(ValueError, 'manual diagnosis'):
                batch.audit_sources(batch.INITIAL_SOURCES)

    def test_frozen_source_change_is_rejected(self):
        target = batch.INITIAL_SOURCES[-1] / 'results/summary.json'
        real_bytes = Path.read_bytes
        def altered(path):
            data = real_bytes(path)
            return data + b' ' if path == target else data
        with patch.object(Path, 'read_bytes', altered):
            with self.assertRaisesRegex(ValueError, 'source artifact changed'):
                batch.load_batch(self.out)

    def test_frozen_protocol_and_budget_tampering_rejected(self):
        real_read = batch.read
        for field, value in [('prepaid_usd', 0), ('budget_usd', 700),
                             ('per_model_workers', 16), ('warm_workers', 4),
                             ('cooldown_seconds', 1), ('code_sha256', {})]:
            def altered(path):
                row = real_read(path)
                if Path(path) == self.out / 'authorization.json':
                    row = copy.deepcopy(row)
                    row[field] = value
                return row
            with self.subTest(field=field), patch.object(batch, 'read', side_effect=altered):
                with self.assertRaisesRegex(ValueError, 'Frozen hourly|cost differs'):
                    batch.load_batch(self.out, check_sources=False)

    def test_orphan_request_is_isolated_not_resent(self):
        tid = batch.pilot.task_id(self.tasks[0])
        directory = batch.INITIAL_SOURCES[0] / 'results' / tid
        fake_request = directory / 'attempt_001.request.json'
        real_glob = Path.glob
        def altered(path, pattern):
            if path == directory and pattern == 'attempt_*.request.json':
                return iter([fake_request])
            return real_glob(path, pattern)
        with patch.object(Path, 'glob', altered):
            pending, audit = batch.audit_sources(batch.INITIAL_SOURCES)
        self.assertEqual(len(pending), 16045)
        self.assertNotIn(tid, {batch.pilot.task_id(t) for t in pending})
        self.assertIn(tid, audit['isolated_nonterminal_ids'])

    def test_new_batch_inherits_latest_cost_and_excludes_new_unknown(self):
        with tempfile.TemporaryDirectory(dir=batch.pilot.ROOT, prefix='.offline-hourly-') as directory:
            root = Path(directory)
            source = root / 'fourth'
            tasks = self.tasks[:2]
            auth = {'prepaid_usd': self.audit['prepaid_usd'], 'files_sha256': {}}
            batch.write(source / 'authorization.json', auth)
            batch.write(source / 'input_tasks.json', {'tasks': tasks})
            batch.write(source / 'source_audit.json', self.audit)
            batch.write(source / 'sessions/one.intent.json', {})
            batch.write(source / 'sessions/one.exit.json', {'exit_code': 2, 'error_type': None})
            batch.write(source / 'results/summary.json', {
                'in_flight': 0, 'terminal': 2, 'counts': {'ok': 1, 'needs_reconcile': 1}})
            batch.write(source / 'results/budget_ledger.json', {
                'budget_usd': 350.0, 'prepaid_usd': self.audit['prepaid_usd'],
                'accounted_including_prepaid_usd': self.audit['prepaid_usd'] + 1,
                'reservations': {}, 'stopped': False})
            for task, status in zip(tasks, ('ok', 'needs_reconcile')):
                result = source / 'results' / batch.pilot.task_id(task)
                batch.write(result / 'attempt_001.request.json', {'offline_test': True})
                batch.write(result / 'attempt_001.response.json', {'offline_test': True})
                batch.write(result / 'terminal.json', {
                    'task_sha256': batch.sha(batch.canonical(task)), 'status': status})
            real_validate = batch.validate_source
            def validate(path):
                return (tasks, auth) if path == source else real_validate(path)
            with patch.object(batch, 'validate_source', side_effect=validate):
                pending, audit = batch.audit_sources([*batch.INITIAL_SOURCES, source])
            self.assertEqual(len(pending), 16044)
            self.assertEqual(audit['prepaid_usd'], 150.4882372998792)
            self.assertEqual(audit['counts']['needs_reconcile'], 85)
            self.assertFalse({batch.pilot.task_id(t) for t in tasks} &
                             {batch.pilot.task_id(t) for t in pending})
            target = root / 'next'
            with patch.object(batch.pilot, 'validate_market'):
                batch.prepare_batch(target, batch.INITIAL_SOURCES[-1] / 'market_snapshot.json',
                                    pending, audit, root / 'controller')
            next_auth = batch.read(target / 'authorization.json')
            self.assertEqual(next_auth['prepaid_usd'], 150.4882372998792)
            self.assertEqual(next_auth['budget_usd'], 350.0)
            self.assertTrue((source / 'results' / batch.pilot.task_id(tasks[1]) / 'terminal.json').exists())

    def runner(self):
        class Transport:
            stopped = threading.Event()
        return batch.HourlyRunner([], self.case_root / 'results', Transport(),
                                  'https://invalid.example/v1', 'offline', threading.Event())

    def test_one_worker_warmup_and_maximum_four(self):
        runner = self.runner()
        for model in batch.pilot.MODELS:
            self.assertEqual(runner.capacity(model), 1)
            runner.successes[model] = 7
            self.assertEqual(runner.capacity(model), 1)
            runner.successes[model] = 8
            self.assertEqual(runner.capacity(model), 4)
            runner.successes[model] = 10000
            runner.limits[model] = 16
            self.assertEqual(runner.capacity(model), 4)
            runner.limits[model] = 2
            self.assertEqual(runner.capacity(model), 2)

    def test_three_unknowns_stop_until_hourly_controller(self):
        runner = self.runner()
        with patch.object(batch.base.RecoveryRunner, 'task', return_value={'status': 'needs_reconcile'}):
            runner.task(self.tasks[0])
            runner.task(self.tasks[1])
            self.assertFalse(runner.stop.is_set())
            runner.task(self.tasks[2])
        self.assertTrue(runner.stop.is_set())
        self.assertEqual(runner.global_unknown_streak, 3)
        with patch.object(batch.base, 'health') as health:
            self.assertFalse(runner.recover_connection())
            health.assert_not_called()
        fresh = self.runner()
        self.assertEqual(fresh.global_unknown_streak, 0)
        self.assertTrue(list(fresh.results.glob('events/*.json')))

    def test_success_breaks_global_unknown_streak(self):
        runner = self.runner()
        with patch.object(batch.base.RecoveryRunner, 'task', side_effect=[
                {'status': s} for s in ('needs_reconcile', 'needs_reconcile', 'ok', 'needs_reconcile')]):
            for task in self.tasks[:4]:
                runner.task(task)
        self.assertFalse(runner.stop.is_set())
        self.assertEqual(runner.global_unknown_streak, 1)

    def controller_context(self):
        """Controller control flow only: no original locks/processes/network."""
        stack = ExitStack()
        self.addCleanup(stack.close)
        coordinator = self.case_root / 'controller'
        tiny_audit = copy.deepcopy(self.audit)
        tiny_audit['files_sha256'] = {}
        tiny_audit['never_sent_ids'] = [batch.pilot.task_id(self.tasks[0])]
        stack.enter_context(patch.object(batch, 'source_roots', return_value=[]))
        stack.enter_context(patch.object(batch, 'live_client', return_value=None))
        audit = stack.enter_context(patch.object(batch, 'audit_sources', return_value=(self.tasks[:1], tiny_audit)))
        stack.enter_context(patch.object(batch.pilot, 'credentials', return_value=('https://invalid.example/v1', 'offline')))
        stack.enter_context(patch.object(batch.base, 'configure_route'))
        stack.enter_context(patch.object(batch, 'choose_market', return_value=batch.INITIAL_SOURCES[-1] / 'market_snapshot.json'))
        stack.enter_context(patch.object(batch.pilot, 'validate_market'))
        stack.enter_context(patch.object(batch.previous, 'identity_pid', return_value={'pid': 123456, 'process_start': 'offline'}))
        stack.enter_context(patch.object(batch.time, 'sleep'))
        spawn = stack.enter_context(patch.object(batch.subprocess, 'Popen'))
        spawn.return_value.pid = 123456
        return coordinator, audit, spawn

    def test_failed_first_probe_consumes_full_hour_without_post(self):
        coordinator, audit, spawn = self.controller_context()
        with patch.object(batch.time, 'time', return_value=10000), \
                patch.object(batch.base, 'health', return_value={'ok': False, 'error_type': 'SSLError'}) as probe:
            result = batch.reconnect(coordinator, self.case_root / 'no-env')
            self.assertFalse(result['launched'])
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(batch.cooldown_remaining(coordinator), 3600)
            again = batch.reconnect(coordinator, self.case_root / 'no-env')
            self.assertEqual(again['cooldown_remaining_s'], 3600)
            self.assertEqual(probe.call_count, 1)
            self.assertEqual(audit.call_count, 1)
        spawn.assert_not_called()
        self.assertEqual(len(list((coordinator / 'reconnect_attempts').glob('*.intent.json'))), 1)
        self.assertEqual(len(list((coordinator / 'reconnect_attempts').glob('*.outcome.json'))), 1)
        with patch.object(batch.time, 'time', return_value=13599):
            self.assertEqual(batch.cooldown_remaining(coordinator), 1)
        with patch.object(batch.time, 'time', return_value=13600):
            self.assertEqual(batch.cooldown_remaining(coordinator), 0)

    def test_failed_second_probe_consumes_hour_and_does_not_launch(self):
        coordinator, _, spawn = self.controller_context()
        with patch.object(batch.time, 'time', return_value=10000), \
                patch.object(batch.base, 'health', side_effect=[{'ok': True}, {'ok': False}]) as probe:
            result = batch.reconnect(coordinator, self.case_root / 'no-env')
            self.assertFalse(result['launched'])
            self.assertEqual(probe.call_count, 2)
            self.assertEqual(batch.cooldown_remaining(coordinator), 3600)
        spawn.assert_not_called()

    def test_authentication_failure_blocks_later_hourly_attempts(self):
        coordinator, _, spawn = self.controller_context()
        with patch.object(batch.time, 'time', return_value=10000), \
                patch.object(batch.base, 'health', return_value={'ok': False, 'http_status': 401}) as probe:
            result = batch.reconnect(coordinator, self.case_root / 'no-env')
            self.assertTrue(result['needs_attention'])
            self.assertFalse(result['launched'])
            self.assertEqual(probe.call_count, 1)
        with patch.object(batch.time, 'time', return_value=14000), \
                patch.object(batch.base, 'health') as probe:
            later = batch.reconnect(coordinator, self.case_root / 'no-env')
            self.assertTrue(later['needs_attention'])
            probe.assert_not_called()
        spawn.assert_not_called()

    def test_live_client_is_noop_without_probe_or_new_attempt(self):
        coordinator, audit, spawn = self.controller_context()
        with patch.object(batch, 'live_client', return_value={'pid': 123, 'process_start': 'same'}), \
                patch.object(batch.base, 'health') as probe:
            result = batch.reconnect(coordinator, self.case_root / 'no-env')
        self.assertTrue(result['already_running'])
        audit.assert_not_called()
        probe.assert_not_called()
        spawn.assert_not_called()
        self.assertFalse(list((coordinator / 'reconnect_attempts').glob('*.intent.json')))

    def test_success_requires_two_probes_and_launches_once_with_inherited_cost(self):
        coordinator, _, spawn = self.controller_context()
        with patch.object(batch.time, 'time', return_value=10000), \
                patch.object(batch.base, 'health', return_value={'ok': True}) as probe:
            result = batch.reconnect(coordinator, self.case_root / 'no-env')
            self.assertTrue(result['launched'])
            self.assertEqual(probe.call_count, 2)
            self.assertEqual(spawn.call_count, 1)
            auth = batch.read(Path(result['batch']) / 'authorization.json')
            self.assertEqual(auth['prepaid_usd'], 149.4882372998792)
            self.assertEqual(auth['budget_usd'], 350)
            again = batch.reconnect(coordinator, self.case_root / 'no-env')
            self.assertFalse(again['launched'])
            self.assertEqual(spawn.call_count, 1)
        self.assertEqual(auth['in_worker_reconnections'], 0)

    def test_another_hour_creates_independent_attempt_history(self):
        coordinator, _, spawn = self.controller_context()
        with patch.object(batch.time, 'time', return_value=10000), \
                patch.object(batch.base, 'health', return_value={'ok': False}):
            batch.reconnect(coordinator, self.case_root / 'no-env')
        before = {path: path.read_bytes() for path in (coordinator / 'reconnect_attempts').glob('*.json')}
        with patch.object(batch.time, 'time', return_value=13600), \
                patch.object(batch.base, 'health', return_value={'ok': False}):
            batch.reconnect(coordinator, self.case_root / 'no-env')
        self.assertEqual(len(list((coordinator / 'reconnect_attempts').glob('*.intent.json'))), 2)
        self.assertTrue(all(path.read_bytes() == data for path, data in before.items()))
        spawn.assert_not_called()

    def test_held_source_lock_blocks_audit_and_probe(self):
        coordinator, audit, spawn = self.controller_context()
        source_lock = self.case_root / 'source/run.lock'
        with batch.previous.exclusive(source_lock), \
                patch.object(batch, 'source_locks', return_value=[source_lock]), \
                patch.object(batch.base, 'health') as probe:
            with self.assertRaisesRegex(ValueError, 'holds lock'):
                batch.reconnect(coordinator, self.case_root / 'no-env')
        audit.assert_not_called()
        probe.assert_not_called()
        spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
