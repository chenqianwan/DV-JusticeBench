"""Offline checks for the versioned 48-concurrency hourly continuation.

No HTTP or real subprocess launch. Temporary artifacts only; production lineage
is read-only. Source accounting/selection is also checked against immutable data.
"""
from collections import Counter
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import run_secondary_hourly_reconnect48 as batch


class Reconnect48Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.http = patch('requests.sessions.Session.request', side_effect=AssertionError('HTTP forbidden')).start()
        cls.addClassCleanup(patch.stopall)
        cls.tasks, cls.audit = batch.audit_sources(batch.INITIAL_SOURCES)
        cls.original = {batch.pilot.task_id(t): t for t in
                        batch.read(batch.INITIAL_SOURCES[0]/'input_tasks.json')['tasks']}

    @classmethod
    def tearDownClass(cls):
        cls.http.assert_not_called()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def runner(self):
        class Transport:
            stopped = threading.Event()
        return batch.HourlyRunner([], self.root/'results', Transport(),
                                  'https://invalid.example/v1', 'offline', threading.Event())

    def test_immediate_16_per_model_and_adaptive_limit(self):
        runner = self.runner()
        self.assertEqual(batch.base.WORKERS, 16)
        for model in batch.pilot.MODELS:
            self.assertEqual(runner.capacity(model), 16)
            runner.successes[model] = 10000
            self.assertEqual(runner.capacity(model), 16)
            runner.limits[model] = 8
            self.assertEqual(runner.capacity(model), 8)
            runner.limits[model] = 4
            self.assertEqual(runner.capacity(model), 4)

    def test_scheduler_dispatches_48_and_drains_after_stop(self):
        runner = self.runner()
        tasks = [dict(self.tasks[0], model=m, question_id=f'offline-{i}')
                 for m in batch.pilot.MODELS for i in range(20)]
        release = threading.Event()
        finished = []
        def task(current):
            if not release.wait(5):
                raise AssertionError('Scheduler did not checkpoint initial dispatch')
            return current
        def checkpoint(result, inflight):
            if not release.is_set():
                self.assertEqual(inflight, 48)
                runner.stop.set()
                release.set()
            if result:
                finished.append(result)
        with patch.object(runner, 'task', side_effect=task):
            batch.base.schedule(tasks, runner, set(), checkpoint)
        self.assertEqual(Counter(t['model'] for t in finished), {m:16 for m in batch.pilot.MODELS})

    def test_503_adaptive_reduction_preserved(self):
        runner = self.runner()
        task = self.tasks[0]
        directory = runner.results/batch.pilot.task_id(task)
        for n in range(20):
            batch.write(directory/f'attempt_{n:03d}.response.json', {'http_status': 503 if n < 6 else 200})
        with patch.object(batch.pilot.Runner, 'task', return_value={'status':'ok'}):
            runner.task(task)
        self.assertEqual(runner.capacity(task['model']), 4)
        self.assertEqual(runner.server_errors[task['model']], 6)

    def test_three_unknowns_stop_without_in_worker_reconnect(self):
        runner = self.runner()
        with patch.object(batch.base.RecoveryRunner, 'task', return_value={'status':'needs_reconcile'}):
            for t in self.tasks[:3]:
                runner.task(t)
        self.assertTrue(runner.stop.is_set())
        with patch.object(batch.base, 'health') as probe:
            self.assertFalse(runner.recover_connection())
            probe.assert_not_called()

    def test_signal_during_length_retry_leaves_receipt_not_replayed(self):
        stop = threading.Event()
        class Transport:
            def post(self, *args, **kwargs):
                stop.set()
                class Response:
                    status_code, headers = 200, {}
                    text = json.dumps({'choices':[{'message':{'content':'partial'},'finish_reason':'length'}]})
                return Response()
        task = self.tasks[0]
        inner = batch.pilot.Runner([task], self.root, Transport(), 'https://invalid.example/v1', 'offline', stop)
        self.assertIsNone(inner.task(task))
        directory = self.root/batch.pilot.task_id(task)
        self.assertTrue((directory/'attempt_001.response.json').exists())
        self.assertFalse((directory/'terminal.json').exists())
        with self.assertRaisesRegex(ValueError, 'Nonterminal attempted'):
            inner.task(task)

    def test_prior_hourly_uses_prior_frozen_loader_and_both_code_hashes(self):
        root = batch.prior_hourly.COORDINATOR/'batches/batch_offline'
        with patch.object(batch.prior_hourly, 'load_batch', return_value=('old','auth')) as load:
            self.assertEqual(batch.validate_source(root), ('old','auth'))
        load.assert_called_once_with(root, check_sources=False)
        identity = batch.code_identity()
        for module in (batch.prior_hourly, batch):
            self.assertIn(str(Path(module.__file__).resolve().relative_to(batch.pilot.ROOT)), identity)

    def test_old_controller_is_locked_and_all_lineage_is_discovered(self):
        locks = batch.source_locks(batch.INITIAL_SOURCES)
        self.assertIn(batch.prior_hourly.COORDINATOR/'control.lock', locks)
        self.assertIn(batch.prior_hourly.COORDINATOR/'run.lock', locks)
        old = batch.prior_hourly.source_roots(batch.prior_hourly.COORDINATOR)
        self.assertEqual(batch.source_roots(self.root), old)

    def test_initial_sources_preserve_science_unknowns_and_cost(self):
        selected = {batch.pilot.task_id(t) for t in self.tasks}
        self.assertEqual(len(selected), 16046)
        self.assertEqual(selected | set(self.audit['prior_terminal']), set(self.original))
        self.assertFalse(selected & set(self.audit['prior_attempted_ids']))
        self.assertEqual(self.audit['counts']['needs_reconcile'], 84)
        self.assertEqual(self.audit['prepaid_usd'], 149.4882372998792)
        self.assertEqual(batch.BUDGET, 350)
        excluded = {r['case_id'] for r in batch.read(batch.INITIAL_SOURCES[0]/'excluded_cases.json')}
        for task in self.tasks:
            self.assertEqual(task, self.original[batch.pilot.task_id(task)])
            self.assertNotIn(task['case_id'], excluded)
            self.assertEqual(batch.pilot.payload(task,3000),
                dict(model=task['model'],messages=task['messages'],temperature=.3,max_tokens=3000))

    def test_freeze_load_and_protocol_tamper_rejected(self):
        out = self.root/'batch'
        with patch.object(batch.pilot, 'validate_market'):
            batch.prepare_batch(out, batch.INITIAL_SOURCES[-1]/'market_snapshot.json',
                                self.tasks,self.audit,self.root/'coordinator')
        _, auth = batch.load_batch(out,check_sources=False)
        self.assertEqual(auth['per_model_workers'],16)
        self.assertEqual(auth['warm_workers'],16)
        self.assertEqual(auth['warm_successes'],0)
        self.assertEqual(auth['timeout'],300)
        self.assertEqual(auth['prepaid_usd'],self.audit['prepaid_usd'])
        real_read = batch.read
        for field,value in [('per_model_workers',4),('budget_usd',700),('prepaid_usd',0),('temperature',.8)]:
            def altered(path):
                row = real_read(path)
                if Path(path) == out/'authorization.json':
                    row=copy.deepcopy(row)
                    row[field]=value
                return row
            with self.subTest(field=field),patch.object(batch,'read',side_effect=altered):
                with self.assertRaises(ValueError):
                    batch.load_batch(out,check_sources=False)

    def test_failed_probe_retains_hour_cooldown_no_launch(self):
        coordinator = self.root/'coordinator'
        with patch.object(batch,'source_roots',return_value=[]), \
             patch.object(batch,'source_locks',return_value=[]), \
             patch.object(batch,'live_client',return_value=None), \
             patch.object(batch,'audit_sources',return_value=(self.tasks,self.audit)), \
             patch.object(batch.pilot,'credentials',return_value=('https://invalid.example/v1','offline')), \
             patch.object(batch.base,'configure_route'), \
             patch.object(batch.base,'health',return_value={'ok':False}) as probe, \
             patch.object(batch.subprocess,'Popen',side_effect=AssertionError('No process launch')) as spawn, \
             patch.object(batch.time,'time',return_value=10000):
            self.assertFalse(batch.reconnect(coordinator,self.root/'env')['launched'])
            self.assertEqual(batch.cooldown_remaining(coordinator),3600)
            self.assertEqual(batch.reconnect(coordinator,self.root/'env')['cooldown_remaining_s'],3600)
            self.assertEqual(probe.call_count,1)
            spawn.assert_not_called()

    def test_live_client_prevents_probe_and_launch(self):
        with patch.object(batch,'source_roots',return_value=[]), \
             patch.object(batch,'live_client',return_value={'pid':123,'process_start':'offline'}), \
             patch.object(batch.base,'health') as probe, \
             patch.object(batch.subprocess,'Popen') as spawn:
            self.assertTrue(batch.reconnect(self.root/'coordinator',self.root/'env')['already_running'])
        probe.assert_not_called()
        spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
