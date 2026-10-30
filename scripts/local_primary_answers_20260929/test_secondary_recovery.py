"""Offline recovery checks: temporary artifacts only, all HTTP disabled."""
from collections import Counter
import copy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

import run_secondary_recovery as batch


def task(number=0, model='gpt-4o'):
    messages = [{'role': 'user', 'content': 'Frozen facts and question ' + str(number)}]
    return dict(split='secondary', case_id='offline-' + str(number), question_id='q1',
                model=model, messages=messages, prompt_hash=batch.sha(batch.canonical(messages)),
                question_text='Question', question_hash=batch.sha('Question'))


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.http_get = self.enterContext(patch('requests.get', side_effect=AssertionError('No HTTP in offline test')))
        self.http_post = self.enterContext(patch('requests.post', side_effect=AssertionError('No paid HTTP in offline test')))

    def tearDown(self):
        self.http_get.assert_not_called()
        self.http_post.assert_not_called()

    def fixture(self):
        source = self.root / 'source'
        output = self.root / 'recovery'
        tasks = [task(i) for i in range(6)]
        self.enterContext(patch.object(batch, 'SOURCE', source))
        self.enterContext(patch.object(batch.old, 'load', return_value=(tasks, {})))
        self.enterContext(patch.object(batch, 'code_identity', return_value={'offline': 'frozen'}))
        self.enterContext(patch.object(batch.pilot, 'validate_market'))
        batch.write(source / 'authorization.json', {'scope': 'offline'})
        batch.write(source / 'input_tasks.json', {'tasks': tasks})
        batch.write(source / 'sessions/001.intent.json', {})
        batch.write(source / 'sessions/001.exit.json', {'exit_code': 2})
        batch.write(source / 'results/summary.json', {'in_flight': 0})
        batch.write(source / 'results/budget_ledger.json', {
            'reservations': {}, 'stopped': False, 'accounted_including_prepaid_usd': 37.42220557928978})
        for index, status in ((0, 'ok'), (1, 'needs_reconcile')):
            current = tasks[index]
            directory = source / 'results' / batch.pilot.task_id(current)
            batch.write(directory / 'frozen_input.json', current)
            batch.write(directory / 'terminal.json', {
                'task_sha256': batch.sha(batch.canonical(current)), 'status': status,
                'answer': 'retained answer' if status == 'ok' else ''})
            batch.write(directory / 'attempt_001.request.json', {'task_id': batch.pilot.task_id(current)})
            batch.write(directory / 'attempt_001.response.json', {'transport_error': 'ProxyError'} if index else {'http_status': 200})
        market = self.root / 'market.json'
        batch.write(market, {'offline': True})
        return source, output, tasks, market

    def runner(self):
        class Transport:
            def __init__(self):
                self.stopped = threading.Event()
        return batch.RecoveryRunner([], self.root / 'results', Transport(),
                                    'https://invalid.example/v1', 'offline-key', threading.Event())

    def test_source_partition_budget_and_immutable_science(self):
        source, output, tasks, market = self.fixture()
        before = {str(p.relative_to(source)): p.read_bytes() for p in source.rglob('*') if p.is_file()}
        summary = batch.prepare(output, market, None, 'system')
        recovered, authorization = batch.load(output)
        self.assertEqual(summary['tasks'], 4)
        self.assertEqual(summary['retained'], 1)
        self.assertEqual(summary['deferred_unknown'], 1)
        self.assertEqual({batch.pilot.task_id(t) for t in recovered},
                         {batch.pilot.task_id(t) for t in tasks[2:]})
        self.assertEqual(authorization['budget_usd'], 350)
        self.assertEqual(authorization['prepaid_usd'], 37.42220557928978)
        self.assertEqual(authorization['scoring_performed'], False)
        for relative, content in before.items():
            self.assertEqual((source / relative).read_bytes(), content)
        for recovered_task in recovered:
            original = next(t for t in tasks if batch.pilot.task_id(t) == batch.pilot.task_id(recovered_task))
            for maximum in (3000, 6000):
                self.assertEqual(batch.pilot.payload(recovered_task, maximum), batch.pilot.payload(original, maximum))

    def test_preserved_source_change_blocks_load(self):
        source, output, tasks, market = self.fixture()
        batch.prepare(output, market, None, 'system')
        terminal = source / 'results' / batch.pilot.task_id(tasks[0]) / 'terminal.json'
        value = batch.read(terminal)
        value['answer'] = 'changed'
        batch.write(terminal, value)
        with self.assertRaisesRegex(ValueError, 'Preserved source changed'):
            batch.load(output)

    def test_success_cannot_be_selected_for_replay(self):
        source, output, tasks, market = self.fixture()
        reconciliation = self.root / 'reconciliation.json'
        batch.write(reconciliation, {'tasks': [{'task_id': batch.pilot.task_id(tasks[0]),
            'safe_retry': True, 'reason': 'not permitted', 'receipt_sha256': {}}]})
        with self.assertRaisesRegex(ValueError, 'outside exact unknown set'):
            batch.prepare(output, market, reconciliation, 'system')

    def test_prior_process_or_missing_exit_blocks(self):
        source, output, tasks, market = self.fixture()
        with patch.object(batch.previous, 'previous_guard', side_effect=ValueError('Previous batch client is still alive')):
            with self.assertRaisesRegex(ValueError, 'still alive'):
                batch.prepare(output, market, None, 'system')
        (source / 'sessions/001.exit.json').unlink()
        with self.assertRaisesRegex(ValueError, 'exit receipt'):
            batch.prepare(output, market, None, 'system')

    def test_source_lock_blocks_second_client(self):
        source, output, tasks, market = self.fixture()
        with batch.previous.exclusive(source / 'results/run.lock'):
            with self.assertRaisesRegex(ValueError, 'holds lock'):
                batch.prepare(output, market, None, 'system')

    def test_budget_exhausted_or_inflight_source_blocks(self):
        source, output, tasks, market = self.fixture()
        ledger = source / 'results/budget_ledger.json'
        value = batch.read(ledger)
        value['accounted_including_prepaid_usd'] = 350
        batch.write(ledger, value)
        with self.assertRaisesRegex(ValueError, 'exhausted budget'):
            batch.prepare(output, market, None, 'system')
        value['accounted_including_prepaid_usd'] = 37
        value['reservations'] = {'pending': 1}
        batch.write(ledger, value)
        with self.assertRaisesRegex(ValueError, 'not quiescent'):
            batch.prepare(output, market, None, 'system')

    def test_unknown_recovery_receipt_blocks_resume(self):
        current = task()
        batch.write(self.root / batch.pilot.task_id(current) / 'terminal.json', {
            'task_sha256': batch.sha(batch.canonical(current)), 'status': 'needs_reconcile'})
        with self.assertRaisesRegex(ValueError, 'requires reconciliation'):
            batch.previous.scan([current], self.root, reject_unknown=True)

    def test_warmup_only_promotes_after_eight_successes(self):
        runner = self.runner()
        current = task()
        self.assertEqual(runner.capacity(current['model']), 8)
        with patch.object(batch.pilot.Runner, 'task', return_value={'status': 'content_filter'}):
            for _ in range(10):
                runner.task(current)
        self.assertEqual(runner.capacity(current['model']), 8)
        with patch.object(batch.pilot.Runner, 'task', return_value={'status': 'ok'}):
            for _ in range(7):
                runner.task(current)
            self.assertEqual(runner.capacity(current['model']), 8)
            runner.task(current)
        self.assertEqual(runner.capacity(current['model']), 16)

    def test_unknown_pauses_dispatch_without_stopping_active_work(self):
        runner = self.runner()
        current = task()
        runner.successes[current['model']] = 8
        with patch.object(batch.pilot.Runner, 'task', return_value={'status': 'needs_reconcile'}):
            for _ in range(3):
                runner.task(current)
        self.assertTrue(runner.pause.is_set())
        self.assertFalse(runner.stop.is_set())
        self.assertLessEqual(runner.capacity(current['model']), 8)

    def test_rate_limit_reduces_concurrency(self):
        runner = self.runner()
        current = task()
        runner.successes[current['model']] = 8
        directory = runner.results / batch.pilot.task_id(current)
        batch.write(directory / 'attempt_001.response.json', {'http_status': 429})
        with patch.object(batch.pilot.Runner, 'task', return_value={'status': 'ok'}):
            runner.task(current)
            self.assertLessEqual(runner.capacity(current['model']), 8)
            for _ in range(11):
                runner.task(current)
        self.assertLessEqual(runner.capacity(current['model']), 4)

    def test_server_error_window_reduces_concurrency(self):
        runner = self.runner()
        current = task()
        runner.successes[current['model']] = 8
        directory = runner.results / batch.pilot.task_id(current)
        receipt = directory / 'attempt_001.response.json'
        with patch.object(batch.pilot.Runner, 'task', return_value={'status': 'ok'}):
            batch.write(receipt, {'http_status': 200})
            for _ in range(18):
                runner.task(current)
            self.assertEqual(runner.capacity(current['model']), 16)
            batch.write(receipt, {'http_status': 503})
            for _ in range(2):
                runner.task(current)
            self.assertLessEqual(runner.capacity(current['model']), 8)
            for _ in range(5):
                runner.task(current)
        self.assertLessEqual(runner.capacity(current['model']), 4)

    def test_unknown_without_receipt_evidence_is_not_approved(self):
        source, output, tasks, market = self.fixture()
        reconciliation = self.root / 'reconciliation.json'
        batch.write(reconciliation, {'tasks': [{'task_id': batch.pilot.task_id(tasks[1]),
            'safe_retry': True, 'reason': 'unsupported claim', 'receipt_sha256': {}}]})
        with self.assertRaises(ValueError):
            batch.prepare(output, market, reconciliation, 'system')

    def test_status_before_launch_uses_recovery_manifest(self):
        source, output, tasks, market = self.fixture()
        batch.prepare(output, market, None, 'system')
        result = batch.status(output)
        self.assertEqual(result['tasks'], 4)
        self.assertEqual(result['retained_source_terminal'], 1)
        self.assertEqual(result['deferred_source_unknown'], 1)

    def test_budget_stop_cannot_be_recovered_by_probe(self):
        runner = self.runner()
        runner.transport.stopped.set()
        with patch.object(batch.pilot.Runner, 'task', return_value=None):
            runner.task(task())
        self.assertTrue(runner.stop.is_set())
        runner.pause.set()
        with patch.object(batch, 'health') as probe:
            self.assertFalse(runner.recover_connection())
            probe.assert_not_called()
        self.assertTrue(runner.stop.is_set())

    def test_probe_recovery_requires_two_successes_and_never_posts(self):
        runner = self.runner()
        runner.pause.set()
        with patch.object(runner.stop, 'wait', return_value=False), \
                patch.object(batch, 'health', side_effect=[{'ok': True}, {'ok': False}, {'ok': True}, {'ok': True}]) as probe:
            self.assertTrue(runner.recover_connection())
        self.assertEqual(probe.call_count, 4)
        self.assertFalse(runner.pause.is_set())
        self.assertFalse(runner.stop.is_set())
        self.assertTrue(all(runner.capacity(m) <= 8 for m in batch.pilot.MODELS))

    def test_recovery_cycles_are_finite(self):
        runner = self.runner()
        runner.recovery_cycles = 3
        runner.pause.set()
        with patch.object(batch, 'health') as probe:
            self.assertFalse(runner.recover_connection())
            probe.assert_not_called()
        self.assertTrue(runner.stop.is_set())

    def test_schedule_drains_inflight_before_probe_and_never_replays(self):
        tasks = [task(i) for i in range(6)]
        completed, started, inflight = [], [], set()
        lock = threading.Lock()
        barrier = threading.Barrier(2)

        class Runner:
            stop, pause = threading.Event(), threading.Event()
            def capacity(self, model):
                return 2
            def task(self, current):
                number = int(current['case_id'].split('-')[1])
                with lock:
                    started.append(number)
                    inflight.add(number)
                if number < 2:
                    barrier.wait(timeout=5)
                    self.pause.set()
                with lock:
                    inflight.remove(number)
                return {'number': number, 'status': 'needs_reconcile' if number < 2 else 'ok'}
            def recover_connection(self):
                if inflight:
                    raise AssertionError('Probe ran before inflight drain')
                self.pause.clear()
                return True

        runner = Runner()
        batch.schedule(tasks, runner, set(), lambda value, count: completed.append(value) if value else None)
        self.assertEqual(sorted(started), list(range(6)))
        self.assertEqual(len(started), len(set(started)))
        self.assertEqual(len(completed), 6)

    def test_schedule_obeys_sixteen_per_model_and_forty_eight_total(self):
        tasks = [task(i, model) for model in batch.pilot.MODELS for i in range(16)]
        barrier = threading.Barrier(48)
        lock = threading.Lock()
        running, peaks = Counter(), Counter()
        peak_total = [0]

        class Runner:
            stop, pause = threading.Event(), threading.Event()
            def capacity(self, model):
                return 16
            def task(self, current):
                model = current['model']
                with lock:
                    running[model] += 1
                    peaks[model] = max(peaks[model], running[model])
                    peak_total[0] = max(peak_total[0], sum(running.values()))
                barrier.wait(timeout=5)
                with lock:
                    running[model] -= 1
                return {'status': 'ok'}

        batch.schedule(tasks, Runner(), set(), lambda *_: None)
        self.assertEqual(peak_total[0], 48)
        self.assertEqual(set(peaks.values()), {16})

    def test_schedule_respects_stop_without_resuming_pause(self):
        runner = self.runner()
        runner.stop.set()
        runner.pause.set()
        with patch.object(runner, 'task') as send, patch.object(runner, 'recover_connection') as recover:
            batch.schedule([task()], runner, set(), lambda *_: None)
        send.assert_not_called()
        recover.assert_not_called()

    def test_direct_route_keeps_tls_and_limits_no_proxy_to_xhub_host(self):
        with patch.dict('os.environ', {'NO_PROXY': 'localhost', 'no_proxy': 'localhost'}, clear=True):
            batch.configure_route('https://invalid.example/v1', 'direct')
            self.assertEqual(batch.os.environ['NO_PROXY'], 'localhost,invalid.example')
            self.assertEqual(batch.os.environ['no_proxy'], 'localhost,invalid.example')


if __name__ == '__main__':
    unittest.main()
