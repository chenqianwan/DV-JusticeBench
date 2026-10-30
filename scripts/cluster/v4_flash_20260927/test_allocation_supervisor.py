"""Local-only tests: no Slurm, network, GPU or inference processes."""
import importlib.util
import json
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


if __name__ == '__main__':
    unittest.main()
