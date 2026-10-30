"""Local-only tests: no Slurm, network, GPU or inference processes."""
import importlib.util
import json
import os
from types import SimpleNamespace
from unittest import mock
from pathlib import Path
import signal
import tempfile
import unittest

SPEC = importlib.util.spec_from_file_location('dvj_allocation_supervisor', Path(__file__).with_name('allocation_supervisor.py'))
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class FakeProcess:
    def __init__(self, pid=1234):
        self.pid = pid
        self.code = None

    def poll(self):
        return self.code


class SupervisorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.starts = []
        self.stops = []
        self.process = None

        def start(attempt):
            self.starts.append(attempt)
            self.process = FakeProcess(1000 + len(self.starts))
            return self.process, {'pid': self.process.pid, 'started': 'original', 'ppid': 1}

        def stop(process):
            self.stops.append(process.pid)
            if process.code is None:
                process.code = -signal.SIGTERM
            return []

        self.start, self.stop = start, stop
        self.supervisor = MODULE.AllocationSupervisor(self.root, '123', start, stop)

    def request(self, ident, job='123'):
        (self.root / 'restart_request.json').write_text(json.dumps({'job_id': job, 'request_id': ident}))

    def test_failure_keeps_allocation_without_auto_retry(self):
        self.supervisor.step()
        self.process.code = 2
        for _ in range(5):
            self.supervisor.step()
        self.assertEqual(self.starts, ['001'])
        self.assertTrue(self.supervisor.state['waiting_for_repair'])
        self.assertEqual(self.supervisor.state['service_exit_code'], 2)
        self.assertNotEqual(self.supervisor.state['allocation_state'], 'ready')

    def test_successful_exit_also_holds_allocation(self):
        self.supervisor.step()
        self.process.code = 0
        self.supervisor.step()
        self.assertTrue(self.supervisor.state['waiting_for_repair'])
        self.assertEqual(self.starts, ['001'])

    def test_explicit_restart_once_and_unique_audit(self):
        self.supervisor.step()
        self.process.code = 2
        self.supervisor.step()
        self.request('fix-cli-1')
        self.supervisor.step()
        self.assertEqual(self.starts, ['001', '002'])
        self.assertFalse((self.root / 'restart_request.json').exists())
        self.assertEqual(len(list((self.root / 'restart_requests').glob('*.request.json'))), 1)
        self.process.code = 1
        self.supervisor.step()
        self.request('fix-cli-1')
        self.supervisor.step()
        self.assertEqual(len(self.starts), 2)
        self.assertEqual(self.supervisor.state['last_request_decision']['reason'], 'duplicate_request_id')
        self.assertEqual(len(list((self.root / 'restart_requests').glob('*.decision.json'))), 2)

    def test_wrong_job_and_running_service_are_rejected(self):
        self.supervisor.step()
        self.request('wrong', job='456')
        self.supervisor.step()
        self.assertEqual(self.supervisor.state['last_request_decision']['reason'], 'wrong_job_id')
        self.request('premature')
        self.supervisor.step()
        self.assertEqual(self.supervisor.state['last_request_decision']['reason'], 'service_still_running')
        self.assertEqual(self.starts, ['001'])

    def test_malformed_request_is_archived_without_exiting(self):
        self.supervisor.step()
        (self.root / 'restart_request.json').write_text('{broken')
        self.supervisor.step()
        self.assertFalse((self.root / 'restart_request.json').exists())
        self.assertFalse(self.supervisor.state['last_request_decision']['accepted'])
        self.assertEqual(self.starts, ['001'])

    def test_symlink_request_never_reads_target(self):
        self.supervisor.step()
        (self.root / 'restart_request.json').symlink_to('/etc/passwd')
        self.supervisor.step()
        self.assertEqual(self.supervisor.state['request_error_type'], 'RuntimeError')
        self.assertEqual(self.starts, ['001'])

    def test_spawn_failure_is_not_retried(self):
        def fail(attempt):
            self.starts.append(attempt)
            raise OSError('potentially sensitive error not recorded')
        self.supervisor.start_process = fail
        self.supervisor.step()
        self.supervisor.step()
        self.assertEqual(self.starts, ['001'])
        self.assertEqual(self.supervisor.state['launch_error_type'], 'OSError')
        self.assertNotIn('sensitive', (self.root / 'allocation_status.json').read_text())

    def test_supervisor_recovery_does_not_automatically_launch(self):
        self.supervisor.step()
        self.process.code = 2
        self.supervisor.step()
        recovered = MODULE.AllocationSupervisor(self.root, '123', self.start, self.stop)
        recovered.step()
        self.assertEqual(self.starts, ['001'])
        self.assertTrue(recovered.state['waiting_for_repair'])

    def test_shutdown_stops_service_and_records_signal(self):
        self.supervisor.step()
        self.supervisor.shutdown('SIGTERM')
        self.assertEqual(self.stops, [1001])
        self.assertEqual(self.supervisor.state['allocation_state'], 'stopped')
        self.assertEqual(self.supervisor.state['stop_reason'], 'SIGTERM')

    def test_other_job_cannot_reuse_state(self):
        with self.assertRaises(RuntimeError):
            MODULE.AllocationSupervisor(self.root, '999', self.start, self.stop)

    def test_claimed_request_is_not_replayed_after_recovery(self):
        self.supervisor.step()
        self.process.code = 2
        self.supervisor.step()
        # Simulate a crash after durable rename, before decision/state update.
        (self.root / 'restart_requests' / 'claimed.request.json').write_text(
            json.dumps({'job_id': 123, 'request_id': 'crash-window'}))
        recovered = MODULE.AllocationSupervisor(self.root, '123', self.start, self.stop)
        self.request('crash-window')
        recovered.step()
        self.assertEqual(self.starts, ['001'])
        self.assertEqual(recovered.state['last_request_decision']['reason'], 'duplicate_request_id')

    def test_surviving_process_blocks_new_attempt(self):
        self.supervisor.step()
        survivor = {'pid': 2000, 'ppid': 1, 'started': 'owned'}
        self.supervisor.stop_process = lambda p: [survivor]
        self.supervisor.survivors_alive = lambda rows: rows
        self.process.code = 2
        self.supervisor.step()
        self.request('blocked-until-clean')
        self.supervisor.step()
        self.assertEqual(self.starts, ['001'])
        self.assertEqual(self.supervisor.state['last_request_decision']['reason'], 'previous_processes_still_present')

    def test_escaped_output_rejected(self):
        with self.assertRaises(RuntimeError):
            MODULE.safe_path(self.root, self.root.parent / 'outside.json')


class ProcessCleanupTests(unittest.TestCase):
    def test_tracks_separate_session_and_does_not_signal_reused_pid(self):
        snapshot = {10: {'pid': 10, 'ppid': 1, 'started': 'a'},
                    20: {'pid': 20, 'ppid': 10, 'started': 'b'},
                    30: {'pid': 30, 'ppid': 20, 'started': 'c'}}
        tree = MODULE.ProcessTree(10, lambda: dict(snapshot))
        snapshot[20] = {'pid': 20, 'ppid': 1, 'started': 'new-unrelated'}
        snapshot[30]['ppid'] = 1
        alive = tree.refresh()
        self.assertEqual(set(alive), {10, 30})

    def test_term_then_kill_is_bounded_and_reports_survivors(self):
        tick = [0.0]
        sent = []
        class Tree:
            def refresh(self):
                return {10: {'pid': 10, 'ppid': 1, 'started': 'a'}}
        process = FakeProcess(10)
        survivors = MODULE.stop_tree(process, Tree(), grace=1.0, kill_timeout=0.5,
            clock=lambda: tick[0], sleep=lambda n: tick.__setitem__(0, tick[0] + n),
            send=lambda pid, sig: sent.append((pid, sig)))
        self.assertLessEqual(tick[0], 1.51)
        self.assertIn((10, signal.SIGTERM), sent)
        self.assertIn((10, signal.SIGKILL), sent)
        self.assertEqual(survivors[0]['pid'], 10)



class ProcInventoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.proc = Path(self.tmp.name)

    def proc_stat(self, pid, ppid=1, started='100', state='S'):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        # Index 19 after the comm field is field 22, starttime.
        fields = [state, str(ppid)] + ['0'] * 17 + [str(started)]
        (directory / 'stat').write_text(str(pid) + ' (test worker) ' + ' '.join(fields))
        return directory / 'stat'

    def test_main_requires_pidfd_before_starting_any_process(self):
        with mock.patch.dict(MODULE.os.environ, {'SLURM_JOB_ID': '123', 'SLURM_JOB_NUM_NODES': '1'}), \
             mock.patch.object(MODULE.os, 'getuid', return_value=1083), \
             mock.patch.object(MODULE, 'signal', SimpleNamespace()), \
             mock.patch.object(MODULE.subprocess, 'Popen') as popen:
            with self.assertRaisesRegex(RuntimeError, 'PID-pinned signalling'):
                MODULE.main()
        popen.assert_not_called()

    def test_single_level_inventory_never_uses_recursive_glob(self):
        self.proc_stat(10)
        self.proc_stat(20, ppid=10, started='200')
        (self.proc / '30').mkdir()  # Exited after enumeration, before stat.
        self.proc_stat(40, state='Z')
        with mock.patch.object(Path, 'glob', side_effect=AssertionError('recursive glob forbidden')):
            snapshot = MODULE.process_snapshot(self.proc)
        self.assertEqual(set(snapshot), {10, 20})
        self.assertEqual(snapshot[20]['started'], '200')

    def test_stat_read_disappearance_is_normal_not_incomplete(self):
        stat_path = self.proc_stat(10)
        self.proc_stat(20)
        original = Path.read_text

        def racing_read(path, *args, **kwargs):
            if path == stat_path:
                raise FileNotFoundError('PID vanished')
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, 'read_text', racing_read):
            self.assertEqual(set(MODULE.process_snapshot(self.proc)), {20})

    def test_enumeration_failure_never_returns_partial_inventory(self):
        class RacingDirectory:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                return False
            def __iter__(self):
                yield SimpleNamespace(name='10')
                raise FileNotFoundError('entry disappeared during enumeration')

        self.proc_stat(10)
        with mock.patch.object(MODULE.os, 'scandir', return_value=RacingDirectory()):
            with self.assertRaises(MODULE.ProcessSnapshotError) as caught:
                MODULE.process_snapshot(self.proc)
        self.assertEqual(caught.exception.reason, 'proc_enumeration_unavailable')

    def test_permission_and_malformed_stat_are_explicit_failures(self):
        path = self.proc_stat(10)
        with mock.patch.object(Path, 'read_text', side_effect=PermissionError('private details')):
            with self.assertRaises(MODULE.ProcessSnapshotError) as caught:
                MODULE.process_snapshot(self.proc)
        self.assertEqual(caught.exception.reason, 'proc_stat_unavailable')
        self.assertNotIn('private', str(caught.exception))
        path.write_text('10 malformed')
        with self.assertRaises(MODULE.ProcessSnapshotError) as caught:
            MODULE.process_snapshot(self.proc)
        self.assertEqual(caught.exception.reason, 'proc_stat_malformed')

    def test_process_stat_lookup_race_does_not_hide_other_errors(self):
        self.proc_stat(10)
        with mock.patch.object(Path, 'stat', side_effect=ProcessLookupError()):
            self.assertEqual(MODULE.process_snapshot(self.proc), {})
        with mock.patch.object(Path, 'stat', side_effect=OSError('I/O failure')):
            with self.assertRaises(MODULE.ProcessSnapshotError):
                MODULE.process_snapshot(self.proc)

    def test_non_utf8_process_name_does_not_drop_launched_handle(self):
        path = self.proc_stat(10)
        path.write_bytes(path.read_bytes().replace(b'test worker', b'test\xffworker'))
        self.assertEqual(MODULE.process_snapshot(self.proc)[10]['started'], '100')


class ProcessInventoryGapTests(unittest.TestCase):
    def test_initial_scan_failure_preserves_tree_for_later_recovery(self):
        row = {'pid': 10, 'ppid': 1, 'started': 'a'}
        snapshots = iter([MODULE.ProcessSnapshotError('proc_enumeration_unavailable'), {10: row}])

        def snapshot():
            value = next(snapshots)
            if isinstance(value, Exception):
                raise value
            return value

        tree = MODULE.ProcessTree(10, snapshot)
        self.assertTrue(tree.discovery_gap)
        self.assertEqual(tree.owned, {})
        self.assertEqual(tree.refresh(), {10: row})
        self.assertFalse(tree.discovery_gap)

    def test_scan_failure_retains_owned_descendant_identity(self):
        current = {10: {'pid': 10, 'ppid': 1, 'started': 'a'},
                   20: {'pid': 20, 'ppid': 10, 'started': 'b'}}
        fault = [False]

        def snapshot():
            if fault[0]:
                raise MODULE.ProcessSnapshotError('proc_stat_unavailable')
            return dict(current)

        tree = MODULE.ProcessTree(10, snapshot)
        fault[0] = True
        with self.assertRaises(MODULE.ProcessSnapshotError):
            tree.refresh()
        self.assertEqual(set(tree.owned), {10, 20})
        fault[0] = False
        current[20] = dict(current[20], ppid=1)
        self.assertEqual(set(tree.refresh()), {10, 20})

    def test_parent_exit_during_gap_requires_reconciliation_not_empty_tree(self):
        current = {10: {'pid': 10, 'ppid': 1, 'started': 'a'}}
        fault = [False]

        def snapshot():
            if fault[0]:
                raise MODULE.ProcessSnapshotError('proc_enumeration_unavailable')
            return dict(current)

        tree = MODULE.ProcessTree(10, snapshot)
        fault[0] = True
        with self.assertRaises(MODULE.ProcessSnapshotError):
            tree.refresh()
        fault[0] = False
        current.clear()
        with self.assertRaises(MODULE.ProcessSnapshotError) as caught:
            tree.refresh()
        self.assertEqual(caught.exception.reason, 'unreconciled_descendants_after_scan_gap')
        self.assertIn(10, tree.owned)

    def test_missing_initial_identity_cannot_claim_cleanup_complete(self):
        tree = MODULE.ProcessTree(10, lambda: {})
        with self.assertRaises(MODULE.ProcessSnapshotError):
            tree.refresh()

    def test_pid_reuse_not_added_via_reparented_unrelated_process(self):
        current = {10: {'pid': 10, 'ppid': 1, 'started': 'a'},
                   20: {'pid': 20, 'ppid': 10, 'started': 'b'}}
        tree = MODULE.ProcessTree(10, lambda: dict(current))
        current[20] = {'pid': 20, 'ppid': 1, 'started': 'other'}
        self.assertEqual(set(tree.refresh()), {10})
        self.assertEqual(tree.owned[20]['started'], 'b')

    def test_pidfd_does_not_signal_reused_pid(self):
        original = {'pid': 10, 'started': 'old'}
        with mock.patch.object(MODULE.os, 'pidfd_open', return_value=77, create=True), \
             mock.patch.object(MODULE.signal, 'pidfd_send_signal', create=True) as send, \
             mock.patch.object(MODULE.os, 'close') as close, \
             mock.patch.object(MODULE, 'read_process_identity', return_value={'pid': 10, 'started': 'new'}):
            MODULE.signal_owned(original, signal.SIGTERM)
        send.assert_not_called()
        close.assert_called_once_with(77)

    def test_pidfd_signals_pinned_identity_not_numeric_pid(self):
        original = {'pid': 10, 'started': 'old'}
        with mock.patch.object(MODULE.os, 'pidfd_open', return_value=77, create=True), \
             mock.patch.object(MODULE.signal, 'pidfd_send_signal', create=True) as send, \
             mock.patch.object(MODULE.os, 'close') as close, \
             mock.patch.object(MODULE, 'read_process_identity', return_value=original):
            MODULE.signal_owned(original, signal.SIGTERM)
        send.assert_called_once_with(77, signal.SIGTERM)
        close.assert_called_once_with(77)


class SupervisorScanFailureTests(SupervisorTests):
    def fail_scan(self):
        raise MODULE.ProcessSnapshotError('proc_enumeration_unavailable', 'FileNotFoundError')

    def test_running_service_scan_failure_never_stops_or_restarts(self):
        self.supervisor.step()
        self.supervisor.refresh_processes = self.fail_scan
        self.request('wait-for-inspection')
        for _ in range(3):
            self.supervisor.step()
        self.assertEqual(self.starts, ['001'])
        self.assertEqual(self.stops, [])
        self.assertIs(self.supervisor.process, self.process)
        self.assertTrue((self.root / 'restart_request.json').exists())
        self.assertEqual(self.supervisor.state['process_inspection'], 'unavailable')
        self.assertTrue(self.supervisor.state['cleanup_unknown'])
        self.supervisor.refresh_processes = lambda: {'pid': 1001, 'ppid': 1, 'started': 'original'}
        self.supervisor.step()
        self.assertEqual(self.supervisor.state['process_inspection'], 'verified')
        self.assertFalse(self.supervisor.state['cleanup_unknown'])
        self.assertEqual(self.supervisor.state['last_request_decision']['reason'], 'service_still_running')

    def test_popen_success_tree_initial_scan_failure_keeps_actual_process(self):
        holder = {}

        def snapshot():
            raise MODULE.ProcessSnapshotError('proc_stat_unavailable')

        def start(attempt):
            self.starts.append(attempt)
            self.process = FakeProcess(1001)
            holder['tree'] = MODULE.ProcessTree(self.process.pid, snapshot)
            return self.process, holder['tree'].owned.get(self.process.pid)

        self.supervisor.start_process = start
        self.supervisor.refresh_processes = lambda: holder['tree'].refresh()
        self.supervisor.step()
        self.supervisor.step()
        self.assertEqual(self.starts, ['001'])
        self.assertEqual(self.stops, [])
        self.assertIs(self.supervisor.process, self.process)
        self.assertEqual(self.supervisor.state['process_inspection'], 'unavailable')
        self.assertIsNone(self.supervisor.state.get('launch_error_type'))

    def test_survivor_scan_failure_keeps_previous_survivors_and_request(self):
        self.supervisor.step()
        survivor = {'pid': 2000, 'ppid': 1, 'started': 'known'}
        self.supervisor.stop_process = lambda p: [survivor]
        self.supervisor.survivors_alive = lambda rows: rows
        self.process.code = 2
        self.supervisor.step()
        self.supervisor.survivors_alive = lambda rows: self.fail_scan()
        self.request('cannot-assume-empty')
        self.supervisor.step()
        self.assertEqual(self.supervisor.state['cleanup_survivors'], [survivor])
        self.assertEqual(self.starts, ['001'])
        self.assertTrue((self.root / 'restart_request.json').exists())

    def test_cleanup_scan_failure_preserves_handle_and_blocks_restart(self):
        self.supervisor.step()
        self.supervisor.stop_process = lambda process: self.fail_scan()
        self.process.code = 2
        self.request('do-not-restart-during-uncertain-cleanup')
        self.supervisor.step()
        self.assertIs(self.supervisor.process, self.process)
        self.assertEqual(self.starts, ['001'])
        self.assertTrue(self.supervisor.state['cleanup_unknown'])

    def test_recovered_supervisor_with_lost_tree_requires_manual_reconciliation(self):
        self.supervisor.step()
        recovered = MODULE.AllocationSupervisor(self.root, '123', self.start, self.stop)
        self.request('lost-descendants')
        recovered.step()
        self.assertEqual(self.starts, ['001'])
        self.assertEqual(recovered.state['last_request_decision']['reason'], 'process_cleanup_unverified')
        self.assertEqual(recovered.state['process_inspection'], 'unresolved')

    def test_unrelated_programming_error_not_hidden_as_healthy(self):
        self.supervisor.step()

        def bug():
            raise TypeError('programming bug')

        self.supervisor.refresh_processes = bug
        with self.assertRaises(TypeError):
            self.supervisor.step()

    def test_shutdown_records_unverified_cleanup_instead_of_false_success(self):
        self.supervisor.step()
        self.supervisor.stop_process = lambda process: self.fail_scan()
        self.supervisor.shutdown('SIGTERM')
        self.assertEqual(self.supervisor.state['allocation_state'], 'stopped_cleanup_unverified')
        self.assertTrue(self.supervisor.state['cleanup_unknown'])


if __name__ == '__main__':
    unittest.main()
