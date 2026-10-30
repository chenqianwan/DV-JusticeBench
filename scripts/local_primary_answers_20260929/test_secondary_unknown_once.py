"""Offline safety checks for the single, explicitly authorized unknown retry.

Only synthetic tasks and temporary files are used. No production inputs, .env,
HTTP, real workers, or subprocesses are read, contacted, or started.
"""
from collections import Counter
from contextlib import ExitStack
import copy
import json
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import run_secondary_unknown_once as batch


def synthetic_task(number, model='gpt-4o'):
    messages = [{'role': 'user', 'content': f'Frozen synthetic facts {number}'}]
    return dict(split='secondary', case_id=f'offline-{number}', question_id='q1',
                model=model, messages=messages, question_text='Frozen question',
                prompt_hash=batch.sha(batch.canonical(messages)),
                question_hash=batch.sha('Frozen question'))


class UnknownOnceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.fixture_tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.fixture_tmp.cleanup)
        cls.fixture_root = Path(cls.fixture_tmp.name)
        cls.source_root = cls.fixture_root/'original'
        cls.selected = [synthetic_task(i, model)
                        for model, count in batch.EXPECTED.items() for i in range(count)]
        cls.retained = [synthetic_task(1000+i, 'gpt-4o')
                        for i, _ in enumerate(sorted(batch.pilot.TERMINAL-{'needs_reconcile'}))]
        cls.original_tasks = cls.selected+cls.retained
        cls.original = {batch.pilot.task_id(t): t for t in cls.original_tasks}
        batch.write(cls.source_root/'input_tasks.json', dict(tasks=cls.original_tasks))
        batch.write(cls.source_root/'market_snapshot.json', {
            'prices_usd_per_million': {m: [1.0, 2.0] for m in batch.pilot.MODELS},
            'quota_per_unit': 500000})
        for current in cls.selected:
            directory = cls.source_root/'results'/batch.pilot.task_id(current)
            batch.write(directory/'frozen_input.json', current)
            batch.write(directory/'terminal.json', {
                'task_sha256': batch.sha(batch.canonical(current)), 'status': 'needs_reconcile'})
            batch.write(directory/'attempt_001.request.json', {
                'task_id': batch.pilot.task_id(current), 'payload': batch.pilot.payload(current, 3000)})
            batch.write(directory/'attempt_001.response.json', {
                'transport_error': 'ReadTimeout', 'completed_at': '2000-01-01T00:00:00+00:00'})
        for current, status in zip(cls.retained, sorted(batch.pilot.TERMINAL-{'needs_reconcile'})):
            batch.write(cls.source_root/'results'/batch.pilot.task_id(current)/'terminal.json', {
                'task_sha256': batch.sha(batch.canonical(current)), 'status': status})

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=self.fixture_root)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.out = self.root/'retry'
        self.env = self.root/'must-not-read.env'
        self.http = self.enterContext(patch('requests.sessions.Session.request',
            side_effect=AssertionError('HTTP forbidden in offline tests')))
        self.spawn = self.enterContext(patch.object(batch.subprocess, 'Popen',
            side_effect=AssertionError('Real process launch forbidden')))
        self.credentials = self.enterContext(patch.object(batch.pilot, 'credentials',
            side_effect=AssertionError('Credential/.env reads forbidden')))
        self.enterContext(patch.object(batch.pilot, 'ROOT', self.fixture_root))
        self.enterContext(patch.object(batch, 'code_identity', return_value={'offline': 'frozen'}))
        self.enterContext(patch.object(batch.source, 'INITIAL_SOURCES', (self.source_root,)))
        self.enterContext(patch.object(batch.source, 'COORDINATOR', self.root/'coordinator'))
        self.enterContext(patch.object(batch.source.prior_hourly, 'COORDINATOR', self.root/'old-coordinator'))
        self.enterContext(patch.object(batch.source, 'source_roots', return_value=[self.source_root]))
        self.enterContext(patch.object(batch.base.old, 'load', return_value=(self.original_tasks, {})))
        # Fail closed if any test accidentally reads production data or a credential file.
        original_read_text, original_read_bytes = Path.read_text, Path.read_bytes
        def guarded_read(method, path, *args, **kwargs):
            if not path.is_relative_to(self.fixture_root) or path.suffix == '.env':
                raise AssertionError(f'Non-fixture read forbidden: {path}')
            return method(path, *args, **kwargs)
        self.enterContext(patch.object(Path, 'read_text',
            lambda path, *args, **kwargs: guarded_read(original_read_text, path, *args, **kwargs)))
        self.enterContext(patch.object(Path, 'read_bytes',
            lambda path, *args, **kwargs: guarded_read(original_read_bytes, path, *args, **kwargs)))

    def tearDown(self):
        self.http.assert_not_called()
        self.spawn.assert_not_called()
        self.credentials.assert_not_called()

    def prepare(self):
        batch.prepare(self.out)
        return batch.load(self.out)

    def terminal(self, current, status, *, request=True, response=True):
        directory = self.out/'results'/batch.pilot.task_id(current)
        if status is not None:
            batch.write(directory/'terminal.json', {
                'task_sha256': batch.sha(batch.canonical(current)), 'status': status})
        if request:
            batch.write(directory/'attempt_001.request.json', {'task_id': batch.pilot.task_id(current)})
        if response:
            batch.write(directory/'attempt_001.response.json', {'transport_error': 'ReadTimeout'})
        return directory

    def source_audit(self):
        selected = {batch.pilot.task_id(t) for t in self.selected}
        return dict(source_roots=[str(self.source_root)], files_sha256={'original/ledger': 'frozen'},
            prepaid_usd=287.123456789, isolated_nonterminal_ids=[],
            prior_terminal={tid: dict(source=str(self.source_root), model=t['model'],
                status='needs_reconcile' if tid in selected else 'ok') for tid, t in self.original.items()})

    def test_selection_is_exact_116_and_excludes_all_decided_statuses(self):
        selected = batch.unknown_snapshot([self.source_root], self.original)
        self.assertEqual(len(selected), 116)
        self.assertEqual(Counter(v['model'] for v in selected.values()), batch.EXPECTED)
        self.assertEqual(set(selected), {batch.pilot.task_id(t) for t in self.selected})
        self.assertFalse(set(selected) & {batch.pilot.task_id(t) for t in self.retained})
        for entry in selected.values():
            self.assertTrue(entry['original_charge_may_have_occurred'])
            self.assertFalse(entry['reusable_local_answer'])
            self.assertEqual(len(entry['receipt_sha256']), 4)

    def later_unknown_fixture(self):
        """Eight later unknowns are original tasks but outside this authorization."""
        later_root = self.root/'later-original-batch'
        later = [synthetic_task(2000+i, 'qwen-max') for i in range(8)]
        original = dict(self.original)
        for current in later:
            tid = batch.pilot.task_id(current)
            original[tid] = current
            directory = later_root/'results'/tid
            batch.write(directory/'terminal.json', {
                'task_sha256': batch.sha(batch.canonical(current)), 'status': 'needs_reconcile'})
            batch.write(directory/'attempt_001.request.json', {
                'task_id': tid, 'payload': batch.pilot.payload(current, 3000)})
            batch.write(directory/'attempt_001.response.json', {
                'transport_error': 'ReadTimeout', 'completed_at': batch.now()})
        return [self.source_root, later_root], original, later

    def scope_fixture(self):
        scope = self.root/'original-116-scope.json'
        batch.write(scope, dict(count=116, by_model=batch.EXPECTED,
            tasks=[dict(task_id=batch.pilot.task_id(t), model=t['model']) for t in self.selected]))
        return scope

    def test_fixed_scope_ignores_eight_later_recent_unknowns(self):
        roots, original, later = self.later_unknown_fixture()
        requested = {batch.pilot.task_id(t) for t in self.selected}
        selected = batch.unknown_snapshot(roots, original, requested_ids=requested)
        self.assertEqual(set(selected), requested)
        self.assertEqual(Counter(row['model'] for row in selected.values()), batch.EXPECTED)
        self.assertFalse(set(selected) & {batch.pilot.task_id(t) for t in later})
        self.assertEqual(selected, batch.unknown_snapshot([self.source_root], self.original))
        # An unscoped request still fails rather than silently widening this round.
        with self.assertRaisesRegex(ValueError, 'too recent'):
            batch.unknown_snapshot(roots, original)

    def test_fixed_scope_still_rejects_original_selected_success(self):
        roots, original, _ = self.later_unknown_fixture()
        requested = {batch.pilot.task_id(t) for t in self.selected}
        terminal = self.source_root/'results'/batch.pilot.task_id(self.selected[0])/'terminal.json'
        real_read = batch.read
        def changed_to_success(path):
            row = real_read(path)
            if Path(path) == terminal:
                row['status'] = 'ok'
            return row
        with patch.object(batch, 'read', side_effect=changed_to_success):
            with self.assertRaisesRegex(ValueError, '116-answer unknown set changed'):
                batch.unknown_snapshot(roots, original, requested_ids=requested)

    def test_fixed_scope_rejects_success_or_missing_identity(self):
        requested = {batch.pilot.task_id(t) for t in self.selected}
        original_id = batch.pilot.task_id(self.selected[0])
        for wrong_id in [batch.pilot.task_id(self.retained[0]), 'task_not_in_original']:
            for replace in [True, False]:
                ids = (requested-{original_id} if replace else requested) | {wrong_id}
                with self.subTest(wrong_id=wrong_id, replace=replace):
                    with self.assertRaisesRegex(ValueError, 'unknown set changed|identity set differs'):
                        batch.unknown_snapshot([self.source_root], self.original, requested_ids=ids)

    def test_prepare_fixed_scope_freezes_provenance_and_excludes_later_eight(self):
        roots, original, later = self.later_unknown_fixture()
        scope = self.scope_fixture()
        scope_bytes = scope.read_bytes()
        with patch.object(batch.source, 'source_roots', return_value=roots), \
                patch.object(batch.base.old, 'load', return_value=(list(original.values()), {})):
            prepared = batch.prepare(self.out, scope_audit=scope)
        tasks, auth = batch.load(self.out)
        self.assertEqual(prepared['tasks'], 116)
        self.assertEqual({batch.pilot.task_id(t) for t in tasks}, {batch.pilot.task_id(t) for t in self.selected})
        self.assertFalse({batch.pilot.task_id(t) for t in tasks} & {batch.pilot.task_id(t) for t in later})
        self.assertEqual(Counter(t['model'] for t in tasks), batch.EXPECTED)
        reconciliation = batch.read(self.out/'reconciliation.json')
        self.assertEqual(reconciliation['scope_audit'], str(scope))
        self.assertEqual(reconciliation['scope_audit_sha256'], batch.sha(scope_bytes))
        self.assertEqual(scope.read_bytes(), scope_bytes)
        self.assertEqual(auth['files_sha256']['reconciliation.json'],
                         batch.sha((self.out/'reconciliation.json').read_bytes()))
        reconciliation['scope_audit_sha256'] = 'changed provenance'
        batch.write(self.out/'reconciliation.json', reconciliation)
        with self.assertRaisesRegex(ValueError, 'Retry plan changed'):
            batch.load(self.out)

    def test_prepare_fixed_scope_rejects_wrong_count_model_and_identity(self):
        scope = self.scope_fixture()
        valid = batch.read(scope)
        cases = [('count', 124), ('by_model', dict(batch.EXPECTED, **{'qwen-max': 53})),
                 ('tasks', valid['tasks'][:-1])]
        for field, value in cases:
            batch.write(scope, dict(valid, **{field: value}))
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'scope audit differs'):
                batch.prepare(self.out, scope_audit=scope)
            self.assertFalse((self.out/'authorization.json').exists())
        for wrong_id in [batch.pilot.task_id(self.retained[0]), 'task_not_in_original']:
            changed = copy.deepcopy(valid)
            changed['tasks'][0]['task_id'] = wrong_id
            batch.write(scope, changed)
            with self.subTest(wrong_id=wrong_id), self.assertRaisesRegex(ValueError, 'unknown set changed'):
                batch.prepare(self.out, scope_audit=scope)
            self.assertFalse((self.out/'authorization.json').exists())

    def test_prepare_cli_defaults_to_original_audited_scope(self):
        scope = self.scope_fixture()
        with patch.object(batch, 'SCOPE_AUDIT', scope), \
                patch.object(batch.sys, 'argv', ['run_secondary_unknown_once.py', 'prepare', '--out', str(self.out)]), \
                patch.object(batch.os, 'umask'), patch('builtins.print'), \
                patch.object(batch, 'prepare', return_value={'prepared': True}) as prepare:
            batch.main()
        prepare.assert_called_once_with(self.out, scope)

    def test_selection_rejects_wrong_counts_duplicate_roots_and_out_of_scope(self):
        unknown = self.selected[0]
        tid = batch.pilot.task_id(unknown)
        real_read = batch.read
        def without_one(path):
            row = real_read(path)
            if Path(path) == self.source_root/'results'/tid/'terminal.json':
                row['status'] = 'ok'
            return row
        with patch.object(batch, 'read', side_effect=without_one):
            with self.assertRaisesRegex(ValueError, '116-answer unknown set changed'):
                batch.unknown_snapshot([self.source_root], self.original)
        with self.assertRaisesRegex(ValueError, 'Duplicate/out-of-scope'):
            batch.unknown_snapshot([self.source_root, self.source_root], self.original)
        with self.assertRaisesRegex(ValueError, 'Duplicate/out-of-scope'):
            batch.unknown_snapshot([self.source_root], {k: v for k, v in self.original.items() if k != tid})

    def test_selection_rejects_recent_wrong_payload_or_non_network_receipt(self):
        tid = batch.pilot.task_id(self.selected[0])
        directory = self.source_root/'results'/tid
        cases = [
            ('terminal.json', {'task_sha256': 'wrong'}, 'fingerprint differs'),
            ('attempt_001.request.json', {'payload': {'temperature': .9}}, 'non-network response'),
            ('attempt_001.response.json', {'transport_error': None, 'http_status': 504}, 'non-network response'),
            ('attempt_001.response.json', {'completed_at': batch.now()}, 'too recent'),
        ]
        real_read = batch.read
        for filename, changes, error in cases:
            def altered(path):
                row = real_read(path)
                if Path(path) == directory/filename:
                    row.update(changes)
                return row
            with self.subTest(filename=filename, changes=changes), patch.object(batch, 'read', side_effect=altered):
                with self.assertRaisesRegex(ValueError, error):
                    batch.unknown_snapshot([self.source_root], self.original)

    def test_prepare_load_preserves_science_and_frozen_authorization(self):
        tasks, auth = self.prepare()
        self.assertEqual(auth['budget_usd'], 350)
        self.assertFalse(auth['automatic_second_unknown_round'])
        self.assertFalse(auth['scoring_performed'])
        self.assertEqual((auth['per_model_workers'], auth['timeout']), (16, 300))
        self.assertEqual(auth['requested_ids'], sorted(batch.pilot.task_id(t) for t in self.selected))
        for current in tasks:
            original = self.original[batch.pilot.task_id(current)]
            self.assertEqual(current, original)
            for limit in (3000, 6000):
                self.assertEqual(batch.pilot.payload(current, limit), batch.pilot.payload(original, limit))
                self.assertEqual(batch.pilot.payload(current, limit), dict(
                    model=current['model'], messages=current['messages'], temperature=.3, max_tokens=limit))
        before = {p.name: p.read_bytes() for p in self.out.glob('*.json')}
        self.assertEqual(batch.prepare(self.out), dict(already_prepared=True, tasks=116))
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.out.glob('*.json')})

    def test_load_rejects_protocol_code_input_and_old_receipt_tampering(self):
        self.prepare()
        real_read = batch.read
        for field, value in [('budget_usd', 700), ('temperature', .8),
                             ('automatic_second_unknown_round', True), ('code_sha256', {})]:
            def altered(path):
                row = real_read(path)
                if Path(path) == self.out/'authorization.json':
                    row[field] = value
                return row
            with self.subTest(field=field), patch.object(batch, 'read', side_effect=altered):
                with self.assertRaisesRegex(ValueError, 'protocol/code differs'):
                    batch.load(self.out)
        inputs = self.out/'input_tasks.json'
        row = batch.read(inputs)
        row['tasks'][0]['messages'][0]['content'] = 'changed scientific facts'
        batch.write(inputs, row)
        with self.assertRaisesRegex(ValueError, 'Retry plan changed'):
            batch.load(self.out)
        # Even a recomputed manifest digest cannot authorize changed original payloads.
        auth = batch.read(self.out/'authorization.json')
        auth['files_sha256']['input_tasks.json'] = batch.sha(inputs.read_bytes())
        batch.write(self.out/'authorization.json', auth)
        with self.assertRaisesRegex(ValueError, 'scientific payload changed'):
            batch.load(self.out)

    def test_load_rechecks_original_unknown_receipt_hashes(self):
        self.prepare()
        original_bytes = Path.read_bytes
        target = self.source_root/'results'/batch.pilot.task_id(self.selected[0])/'terminal.json'
        def altered(path):
            return b'changed old receipt' if path == target else original_bytes(path)
        with patch.object(Path, 'read_bytes', altered):
            with self.assertRaisesRegex(ValueError, 'Old unknown receipt changed'):
                batch.load(self.out)

    def test_every_terminal_including_new_unknown_is_done_not_replayed(self):
        tasks = self.selected[:len(batch.pilot.TERMINAL)]
        for current, status in zip(tasks, sorted(batch.pilot.TERMINAL)):
            self.terminal(current, status)
        counts, _, done = batch.retry_done(tasks, self.out/'results')
        self.assertEqual(counts, Counter({status: 1 for status in batch.pilot.TERMINAL}))
        self.assertEqual(done, {batch.pilot.task_id(t) for t in tasks})
        with patch.object(batch, 'load', return_value=(tasks, {})), \
                patch.object(batch.previous, 'previous_guard'):
            result = batch.launch(self.out, self.env, resume=True)
        self.assertEqual(result, dict(launched=False, complete=True, counts=dict(counts)))

    def test_sent_nonterminal_or_missing_response_never_resumes(self):
        for status, response, error in [(None, True, 'nonterminal sent request'),
                                        (None, False, 'nonterminal sent request'),
                                        ('needs_reconcile', False, 'missing response')]:
            with self.subTest(status=status, response=response):
                out = self.out
                self.out = out/f'{status}-{response}'
                self.terminal(self.selected[0], status, response=response)
                with patch.object(batch, 'load', return_value=([self.selected[0]], {})), \
                        patch.object(batch.previous, 'previous_guard'):
                    with self.assertRaisesRegex(ValueError, error):
                        batch.launch(self.out, self.env, resume=True)
                self.out = out

    def test_only_unattempted_tasks_remain_after_new_unknown(self):
        unknown, unattempted = self.selected[:2]
        self.terminal(unknown, 'needs_reconcile')
        _, _, done = batch.retry_done([unknown, unattempted], self.out/'results')
        self.assertEqual(done, {batch.pilot.task_id(unknown)})
        self.assertNotIn(batch.pilot.task_id(unattempted), done)

    def test_network_unknown_is_attempted_once_and_same_terminal_is_reused(self):
        current = self.selected[0]
        transport = Mock(stopped=threading.Event())
        transport.post.side_effect = TimeoutError('synthetic transport error')
        runner = batch.source.HourlyRunner([current], self.out/'results', transport,
            'https://invalid.example/v1', 'offline', threading.Event())
        first = runner.task(current)
        second = runner.task(current)
        self.assertEqual(first['status'], 'needs_reconcile')
        self.assertEqual(first['attempts'], 1)
        self.assertEqual(second, first)
        transport.post.assert_called_once()
        _, _, done = batch.retry_done([current], self.out/'results')
        self.assertEqual(done, {batch.pilot.task_id(current)})

    def test_inherited_http_retry_is_finite_and_preserves_original_payload(self):
        current = self.selected[0]
        response = SimpleNamespace(status_code=503, headers={}, text=json.dumps({'error': {'code': 'unavailable'}}))
        transport = Mock()
        transport.post.return_value = response
        runner = batch.pilot.Runner([current], self.out/'results', transport,
            'https://invalid.example/v1', 'offline', threading.Event(), sleep=lambda _: None)
        result = runner.task(current)
        self.assertEqual(result['status'], 'interface_error')
        self.assertEqual(result['attempts'], 9)
        self.assertLessEqual(transport.post.call_count, batch.pilot.MAX_ATTEMPTS)
        for call in transport.post.call_args_list:
            self.assertEqual(call.kwargs['json'], batch.pilot.payload(current, 3000))
            self.assertEqual(call.kwargs['timeout'], 300)
        self.assertEqual(runner.task(current), result)
        self.assertEqual(transport.post.call_count, 9)

    def test_inherited_length_retry_is_only_once_at_6000(self):
        current = self.selected[0]
        def response(finish):
            return SimpleNamespace(status_code=200, headers={}, text=json.dumps({
                'choices': [{'message': {'content': 'synthetic answer'}, 'finish_reason': finish}]}))
        transport = Mock()
        transport.post.side_effect = [response('length'), response('length')]
        runner = batch.pilot.Runner([current], self.out/'results', transport,
            'https://invalid.example/v1', 'offline', threading.Event(), sleep=lambda _: None)
        result = runner.task(current)
        self.assertEqual((result['status'], result['attempts']), ('truncated', 2))
        self.assertTrue(result['length_retried'])
        self.assertEqual([call.kwargs['json'] for call in transport.post.call_args_list],
                         [batch.pilot.payload(current, 3000), batch.pilot.payload(current, 6000)])
        self.assertEqual(runner.task(current), result)
        self.assertEqual(transport.post.call_count, 2)

    def idle_state(self, live=None):
        return dict(live_client=live, never_started_or_isolated_nonterminal=0,
                    in_flight=0, counts={'ok': 29944, 'needs_reconcile': 116})

    def test_live_original_client_waits_without_audit_or_api(self):
        stop = threading.Event()
        with ExitStack() as stack, \
                patch.object(batch.source, 'status', return_value=self.idle_state({'pid': 123})), \
                patch.object(batch.source, 'audit_sources') as audit, \
                patch.object(batch.base, 'health') as health, \
                patch.object(stop, 'wait', side_effect=lambda seconds: stop.set()) as wait:
            self.assertEqual(batch.wait_for_source(self.out, stop, stack), (None, None))
        wait.assert_called_once_with(30)
        audit.assert_not_called()
        health.assert_not_called()
        self.assertEqual(batch.read(self.out/'wait_status.json')['source_live'], {'pid': 123})

    def test_source_wait_timeout_is_clean_and_resume_allowed(self):
        session = self.mock_worker_setup(self.selected)
        with patch.object(batch.source, 'status', return_value=self.idle_state({'pid': 999})), \
                patch.object(batch.time, 'monotonic', side_effect=[0, 7200]), \
                patch.object(batch.base, 'health') as health, \
                patch.object(batch.base, 'schedule') as schedule:
            self.assertEqual(batch.worker(self.out, self.env, session), {'exit_code': 2})
        health.assert_not_called()
        schedule.assert_not_called()
        receipt = batch.read(self.out/'sessions/offline.exit.json')
        self.assertEqual(receipt['exit_code'], 2)
        self.assertIsNone(receipt['error_type'])
        wait = batch.read(self.out/'wait_status.json')
        self.assertEqual(wait['phase'], 'source_wait_timed_out_cleanly')
        self.assertTrue(wait['no_retry_POST_sent'])
        self.assertTrue(wait['resume_allowed_after_source_is_ready'])

    def test_status_detects_live_launch_before_start_and_checks_identity(self):
        tasks = self.selected[:1]
        self.enterContext(patch.object(batch, 'load', return_value=(tasks, {})))
        prefix = self.out/'sessions/offline'
        batch.write(prefix.with_suffix('.intent.json'), {})
        launched = dict(pid=123, process_start='launch-start')
        batch.write(prefix.with_suffix('.launch.json'), launched)
        with patch.object(batch.previous, 'is_alive', side_effect=lambda row: row == launched) as alive:
            self.assertEqual(batch.status(self.out)['live_client'], launched)
        alive.assert_called_once_with(launched)
        started = dict(pid=456, process_start='different-start')
        batch.write(prefix.with_suffix('.start.json'), started)
        with patch.object(batch.previous, 'is_alive', side_effect=lambda row: row == launched):
            self.assertEqual(batch.status(self.out)['live_client'], launched)
        with patch.object(batch.previous, 'is_alive', return_value=False):
            self.assertIsNone(batch.status(self.out)['live_client'])

    def test_successful_source_wait_holds_all_lineage_and_controller_locks(self):
        audit = self.source_audit()
        paths = [batch.source.COORDINATOR/'control.lock', batch.source.COORDINATOR/'run.lock',
                 *batch.source.source_locks([self.source_root])]
        with ExitStack() as stack, \
                patch.object(batch.source, 'status', return_value=self.idle_state()), \
                patch.object(batch.source, 'live_client', return_value=None), \
                patch.object(batch.source, 'audit_sources', return_value=([], audit)):
            self.assertEqual(batch.wait_for_source(self.out, threading.Event(), stack), ([self.source_root], audit))
            for path in paths:
                with self.subTest(path=path), self.assertRaisesRegex(ValueError, 'holds lock'):
                    with batch.previous.exclusive(path):
                        pass
        for path in paths:
            with batch.previous.exclusive(path):
                pass

    def test_contended_source_lock_or_late_client_blocks_source_audit(self):
        for late_client in (False, True):
            stop = threading.Event()
            with self.subTest(late_client=late_client), ExitStack() as stack, ExitStack() as blocker, \
                    patch.object(batch.source, 'status', return_value=self.idle_state()), \
                    patch.object(batch.source, 'live_client', return_value={'pid': 123} if late_client else None), \
                    patch.object(batch.source, 'audit_sources') as audit, \
                    patch.object(stop, 'wait', side_effect=lambda seconds: stop.set()):
                if not late_client:
                    blocker.enter_context(batch.previous.exclusive(self.source_root/'results/run.lock'))
                self.assertEqual(batch.wait_for_source(self.out, stop, stack), (None, None))
                audit.assert_not_called()

    def test_pending_or_isolated_nonterminal_source_is_not_ready(self):
        for pending, isolated in [([self.selected[0]], []), ([], ['task_isolated'])]:
            audit = self.source_audit()
            audit['isolated_nonterminal_ids'] = isolated
            stop = threading.Event()
            with self.subTest(pending=bool(pending)), ExitStack() as stack, \
                    patch.object(batch.source, 'status', return_value=self.idle_state()), \
                    patch.object(batch.source, 'live_client', return_value=None), \
                    patch.object(batch.source, 'audit_sources', return_value=(pending, audit)), \
                    patch.object(stop, 'wait', side_effect=lambda seconds: stop.set()):
                self.assertEqual(batch.wait_for_source(self.out, stop, stack), (None, None))

    def test_source_snapshot_inherits_full_prepaid_and_is_frozen(self):
        tasks, _ = self.prepare()
        audit = self.source_audit()
        prepaid = batch.prepare_source_snapshot(self.out, tasks, [self.source_root], audit)
        self.assertEqual(prepaid, 287.123456789)
        self.assertEqual(batch.read(self.out/'source_snapshot.json'), audit)
        self.assertEqual((self.out/'market_snapshot.json').read_bytes(),
                         (self.source_root/'market_snapshot.json').read_bytes())
        self.assertEqual(batch.prepare_source_snapshot(self.out, tasks, [self.source_root], audit), prepaid)
        for field, value in [('prepaid_usd', 0), ('files_sha256', {}), ('source_roots', [])]:
            changed = copy.deepcopy(audit)
            changed[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'snapshot changed'):
                batch.prepare_source_snapshot(self.out, tasks, [self.source_root], changed)

    def test_snapshot_rejects_changed_success_source_or_incomplete_coverage(self):
        tasks, _ = self.prepare()
        tid = batch.pilot.task_id(tasks[0])
        for field, value in [('status', 'ok'), ('source', 'another-source')]:
            audit = self.source_audit()
            audit['prior_terminal'][tid][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, 'do not overwrite a success'):
                batch.prepare_source_snapshot(self.out, tasks, [self.source_root], audit)
        audit = self.source_audit()
        del audit['prior_terminal'][batch.pilot.task_id(self.retained[0])]
        with self.assertRaisesRegex(ValueError, 'Incomplete original coverage'):
            batch.prepare_source_snapshot(self.out, tasks, [self.source_root], audit)

    def mock_worker_setup(self, tasks):
        session = 'offline'
        prefix = self.out/'sessions'/session
        identity = {'pid': 123, 'process_start': 'offline'}
        batch.write(self.out/'authorization.json', {'offline': True})
        batch.write(prefix.with_suffix('.launch.json'), identity)
        batch.write(prefix.with_suffix('.intent.json'), {
            'authorization_sha256': batch.sha((self.out/'authorization.json').read_bytes())})
        self.enterContext(patch.object(batch, 'load', return_value=(tasks, {})))
        self.enterContext(patch.object(batch.previous, 'identity_pid', return_value=identity))
        self.enterContext(patch.object(batch.signal, 'signal'))
        return session

    def test_mocked_worker_never_reads_credentials_while_original_is_live(self):
        session = self.mock_worker_setup(self.selected)
        original_event = threading.Event
        stop = original_event()
        with patch.object(batch.threading, 'Event', return_value=stop), \
                patch.object(batch.source, 'status', return_value=self.idle_state({'pid': 999})), \
                patch.object(stop, 'wait', side_effect=lambda seconds: stop.set()), \
                patch.object(batch.base, 'health') as health, \
                patch.object(batch.base, 'schedule') as schedule:
            self.assertEqual(batch.worker(self.out, self.env, session), {'exit_code': 2})
        health.assert_not_called()
        schedule.assert_not_called()
        self.assertEqual(batch.read(self.out/'sessions/offline.exit.json')['error_type'], None)

    def test_mocked_worker_passes_cumulative_prepaid_and_total_350_cap(self):
        session = self.mock_worker_setup(self.selected)
        prepaid = 287.123456789
        batch.write(self.out/'market_snapshot.json', batch.read(self.source_root/'market_snapshot.json'))
        counts = Counter({'ok': 115, 'needs_reconcile': 1})
        done = {batch.pilot.task_id(t) for t in self.selected}
        runner = Mock()
        runner.pause.is_set.return_value = False
        runner.capacity.return_value = 16
        transport = Mock()
        transport.snapshot.return_value = {'budget_usd': 350.0, 'prepaid_usd': prepaid}
        with patch.object(batch, 'wait_for_source', return_value=([self.source_root], self.source_audit())), \
                patch.object(batch, 'prepare_source_snapshot', return_value=prepaid), \
                patch.object(batch, 'retry_done', return_value=(counts, {m: Counter() for m in batch.pilot.MODELS}, done)), \
                patch.object(batch.pilot, 'credentials', return_value=('https://invalid.example/v1', 'offline')), \
                patch.object(batch.base, 'configure_route') as route, \
                patch.object(batch.base, 'health', return_value={'ok': True}), \
                patch.object(batch.pilot, 'validate_market'), \
                patch.object(batch.pilot, 'GuardedTransport', return_value=transport) as create_transport, \
                patch.object(batch.source, 'HourlyRunner', return_value=runner), \
                patch.object(batch.base, 'schedule') as schedule, \
                patch.object(batch, 'merge'):
            self.assertEqual(batch.worker(self.out, self.env, session), {'exit_code': 2})
        self.assertEqual(create_transport.call_args.args[-1], 350.0)
        self.assertEqual(create_transport.call_args.kwargs, {'prepaid_usd': prepaid})
        self.assertEqual(schedule.call_args.args[2], done)
        route.assert_called_once_with('https://invalid.example/v1', 'direct')
        self.assertIsNone(batch.read(self.out/'sessions/offline.exit.json')['error_type'])

    def merge_fixture(self):
        # 30,060 tiny index rows, but no full-size scientific inputs or result trees.
        index = {f'old-{model}-{i}': dict(source=str(self.source_root), model=model, status='ok')
                 for model in batch.pilot.MODELS for i in range(10020)}
        for current in self.selected:
            tid = batch.pilot.task_id(current)
            original_number = int(current['case_id'].removeprefix('offline-'))
            index.pop(f'old-{current["model"]}-{original_number}')
            index[tid] = dict(source=str(self.source_root), model=current['model'], status='needs_reconcile')
        audit = dict(prior_terminal=index)
        ledger = dict(budget_usd=350, prepaid_usd=287.123456789,
                      accounted_including_prepaid_usd=288.0, reservations={}, stopped=False)
        self.enterContext(patch.object(batch, 'load', return_value=(self.selected, {})))
        real_read = batch.read
        def read_fixture(path):
            if Path(path) == self.out/'source_snapshot.json':
                return audit
            if Path(path) == self.out/'results/budget_ledger.json':
                return ledger
            return real_read(path)
        self.enterContext(patch.object(batch, 'read', side_effect=read_fixture))
        return audit, ledger

    def test_merge_replaces_only_selected_unknowns_and_keeps_10020_each(self):
        audit, ledger = self.merge_fixture()
        statuses = ['ok', 'content_filter', 'needs_reconcile']
        for current, status in zip(self.selected, statuses):
            self.terminal(current, status)
        with patch.object(batch, 'write') as write:
            report = batch.merge(self.out)
        index = write.call_args_list[0].args[1]
        self.assertEqual(len(index), 30060)
        self.assertEqual(Counter(row['model'] for row in index.values()), {m: 10020 for m in batch.pilot.MODELS})
        self.assertEqual(report['retry_terminal'], 3)
        self.assertEqual(report['cumulative_budget'], ledger)
        self.assertTrue(report['original_results_retained'])
        self.assertFalse(report['scoring_performed'])
        for current, status in zip(self.selected, statuses):
            row = index[batch.pilot.task_id(current)]
            self.assertEqual((row['replacement_round'], row['status']), (1, status))
            self.assertIn(str(self.source_root), row['original_terminal'])
        self.assertEqual(index[batch.pilot.task_id(self.selected[3])]['replacement_round'], 0)
        self.assertTrue(all(row['source'] == str(self.source_root) for row in audit['prior_terminal'].values()))

    def test_merge_refuses_success_overwrite_missing_rows_and_model_imbalance(self):
        audit, _ = self.merge_fixture()
        current = self.selected[0]
        self.terminal(current, 'ok')
        tid = batch.pilot.task_id(current)
        audit['prior_terminal'][tid]['status'] = 'ok'
        with patch.object(batch, 'write') as write:
            with self.assertRaisesRegex(ValueError, 'non-unknown terminal'):
                batch.merge(self.out)
            write.assert_not_called()
        audit['prior_terminal'][tid]['status'] = 'needs_reconcile'
        row = audit['prior_terminal'].pop('old-gpt-4o-9999')
        with patch.object(batch, 'write') as write:
            with self.assertRaisesRegex(ValueError, 'coverage differs'):
                batch.merge(self.out)
            write.assert_not_called()
        audit['prior_terminal']['old-gpt-4o-9999'] = dict(row, model='qwen-max')
        with patch.object(batch, 'write') as write:
            with self.assertRaisesRegex(ValueError, 'coverage differs'):
                batch.merge(self.out)
            write.assert_not_called()


if __name__ == '__main__':
    unittest.main()
