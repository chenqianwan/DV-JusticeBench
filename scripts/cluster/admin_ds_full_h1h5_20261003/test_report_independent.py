"""Independent review cases for the offline full-corpus report (no network)."""
import importlib.util
from pathlib import Path
import shutil
import unittest

spec = importlib.util.spec_from_file_location('full_report_review_fixture', Path(__file__).with_name('test_report.py'))
fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fixture)
r, write = fixture.r, fixture.write


class IndependentReportTests(unittest.TestCase):
    setUp = fixture.ReportTests.setUp
    build = fixture.ReportTests.build
    attempt = fixture.ReportTests.attempt

    def test_partial_http_body_stays_unknown_despite_valid_text(self):
        directory = self.attempt(1)
        receipt = r.read(directory / 'response.http.json')
        receipt['body_complete'] = False
        write(directory / 'response.http.json', receipt)
        terminal = r.read(directory / 'terminal.json')
        terminal.update(status='timeout_needs_reconcile', complete=False, valid_format=False)
        write(directory / 'terminal.json', terminal)
        result = r.audit(self.snapshot, self.output, allow_partial=True)
        self.assertEqual(result['overall']['statuses']['needs_reconcile'], 1)
        self.assertEqual(result['overall']['statuses']['none_candidate'], 0)

    def test_empty_attempt_directory_is_unknown_not_unsent(self):
        directory = self.attempt(1)
        shutil.rmtree(directory)
        directory.mkdir()
        result = r.audit(self.snapshot, self.output, allow_partial=True)
        self.assertEqual(result['overall']['statuses']['needs_reconcile'], 1)
        self.assertEqual(result['overall']['statuses']['unstarted'], 0)

    def test_wrong_endpoint_evidence_cannot_be_merged(self):
        directory = self.attempt(1)
        wrong = directory.parents[2] / r.HOSTS[0] / 'requests' / directory.name
        directory.rename(wrong)
        with self.assertRaisesRegex(ValueError, 'assigned input'):
            r.audit(self.snapshot, self.output)

    def test_frozen_parser_must_authenticate_before_import(self):
        path = self.snapshot / 'code/sentence_contract.py'
        with path.open('a') as stream:
            stream.write('\nraise RuntimeError("must not execute changed parser")\n')
        with self.assertRaisesRegex(ValueError, 'Staged artifact changed'):
            r.audit(self.snapshot, self.output)

    def test_valid_raw_without_terminal_is_observed_not_accepted(self):
        (self.attempt(1) / 'terminal.json').unlink()
        r.audit(self.snapshot, self.output, allow_partial=True)
        row = next(r.rows(self.output / 'needs_reconcile.jsonl'))
        self.assertTrue(row['observed_raw_parse']['valid_format'])
        self.assertEqual(row['audit_state'], 'needs_reconcile')
        self.assertIsNone(row['native'])
        self.assertIsNone(row['literal_quotes_exact'])

    def test_nondrained_endpoint_blocks_final_even_when_global_claims_done(self):
        path = self.snapshot / 'sessions/session01' / r.HOSTS[0] / 'exit.json'
        receipt = r.read(path)
        receipt['drained'] = False
        write(path, receipt)
        with self.assertRaisesRegex(ValueError, 'drained endpoint'):
            r.audit(self.snapshot, self.output)
        result = r.audit(self.snapshot, self.output, allow_partial=True)
        self.assertFalse(result['real_final_complete_drained_receipt'])

    def test_report_group_slots_sum_to_unique_full_coverage(self):
        result = r.audit(self.snapshot, self.output)
        self.assertEqual(sum(row['slots'] for row in result['by_split_condition']), r.TOTAL)
        self.assertEqual(sum(result['overall']['statuses'].values()), r.TOTAL)
        self.assertEqual(sum(result['overall']['sources'].values()), r.TOTAL)
        records = list(r.rows(self.output / 'provisional_results.jsonl'))
        for record in records:
            self.assertTrue(record['provisional'])
            self.assertFalse(record['reference_ready'])
            self.assertFalse(record['formal_hallucination_statistics_allowed'])
            self.assertFalse(record['semantic_alignment_verified'])


if __name__ == '__main__':
    unittest.main()
