import unittest
import tempfile
from pathlib import Path
from .controller import cleanup_attested, original_ready, initialize_rounds


class SafetyTests(unittest.TestCase):
    def setUp(self):
        self.stopped = dict(job_id='407198', allocation_state='waiting_for_repair',
            waiting_for_repair=True, cleanup_unknown=False, cleanup_survivors=[],
            service_pid=None, service_identity=None, process_inspection='verified')

    def test_cleanup_requires_every_attestation(self):
        self.assertTrue(cleanup_attested(self.stopped))
        for key in self.stopped:
            row = self.stopped.copy()
            del row[key]
            if key not in ('service_pid', 'service_identity'):
                self.assertFalse(cleanup_attested(row), key)
        for key, value in [('cleanup_unknown', True), ('cleanup_survivors', [{'pid': 1}]),
                           ('service_pid', 1), ('job_id', '402816'), ('process_inspection', 'unavailable')]:
            self.assertFalse(cleanup_attested(dict(self.stopped, **{key: value})))

    def test_ready_does_not_mean_merely_process_running(self):
        row = dict(self.stopped, allocation_state='service_running', service_pid=123,
                   service_identity={'pid': 123, 'started': '45'}, waiting_for_repair=False)
        endpoint = dict(job_id='407198', status='ready', max_num_seqs=128)
        self.assertTrue(original_ready(row, endpoint))
        for changed in [dict(endpoint, job_id='402816'), dict(endpoint, status='starting'),
                        dict(endpoint, max_num_seqs=16)]:
            self.assertFalse(original_ready(row, changed))
        self.assertFalse(original_ready(dict(row, service_pid=124), endpoint))
        self.assertFalse(original_ready(dict(row, cleanup_unknown=True), endpoint))

    def test_first_run_round_parent_is_created_exclusively(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            parent = initialize_rounds(root)
            (parent / 'c128').mkdir(parents=False, exist_ok=False)
            self.assertTrue((parent / 'c128').is_dir())
            with self.assertRaises(FileExistsError):
                initialize_rounds(root)


if __name__ == '__main__':
    unittest.main()
