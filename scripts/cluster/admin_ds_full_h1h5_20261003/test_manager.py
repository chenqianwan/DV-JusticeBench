"""Independent offline tests for the full-audit launch guard; no network."""
from contextlib import contextmanager
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


PATH = Path(__file__).with_name("manager.py")
SPEC = importlib.util.spec_from_file_location("full_manager_under_test", PATH)
manager = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(manager)


@contextmanager
def no_lock(*args, **kwargs):
    yield


class ManagerGuardTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.patches = [patch.object(manager, "ROOT", self.root),
                        patch.object(manager, "safe", side_effect=lambda p: Path(p)),
                        patch.object(manager, "verify_stage", return_value={}),
                        patch.object(manager, "exclusive", no_lock),
                        patch.object(manager, "finished_pilot", no_lock)]
        for entry in self.patches:
            entry.start()
        self.addCleanup(self.directory.cleanup)
        for entry in reversed(self.patches):
            self.addCleanup(entry.stop)

    def launch_with(self, records, mode="resume"):
        with patch.object(manager, "launch_records", return_value=records), \
                patch.object(manager.subprocess, "Popen") as popen:
            try:
                result = manager.launch(mode)
            finally:
                popen.assert_not_called()
        return result

    def test_ambiguous_launch_never_restarts(self):
        with self.assertRaisesRegex(RuntimeError, "Ambiguous"):
            self.launch_with([{"session": "ambiguous", "ambiguous_launch": True}])

    def test_same_live_runner_never_duplicates(self):
        result = self.launch_with([{"session": "live", "same_live_process": True,
                                    "runner_match": True}])
        self.assertEqual(result["action"], "already_running_no_duplicate")

    def test_live_wrong_runner_blocks(self):
        with self.assertRaisesRegex(RuntimeError, "identity mismatch"):
            self.launch_with([{"session": "live", "same_live_process": True,
                               "runner_match": False}])

    def test_start_cannot_replace_prior_launch(self):
        with self.assertRaisesRegex(RuntimeError, "explicit resume"):
            self.launch_with([{"session": "old", "same_live_process": False}], "start")

    def test_resume_requires_previous_launch(self):
        with self.assertRaisesRegex(RuntimeError, "No prior launch"):
            self.launch_with([])

    def test_missing_exit_blocks_resume(self):
        with self.assertRaisesRegex(RuntimeError, "exit/drain receipt incomplete"):
            self.launch_with([{"session": "old", "same_live_process": False, "exit": None}])

    def test_partial_drain_blocks_resume(self):
        for exit_record in ({"drained": False, "local_futures_finished": True},
                            {"drained": True, "local_futures_finished": False}):
            with self.subTest(exit_record=exit_record), \
                    self.assertRaisesRegex(RuntimeError, "exit/drain receipt incomplete"):
                self.launch_with([{"session": "old", "same_live_process": False,
                                   "exit": exit_record}])

    def test_pid_reuse_is_not_live_same(self):
        self.assertFalse(manager.live_same({"pid": 12, "start_ticks": "5"},
                                          {"pid": 12, "start_ticks": "6", "state": "R"}))

    def test_zombie_is_not_live_same(self):
        self.assertFalse(manager.live_same({"pid": 12, "start_ticks": "5"},
                                          {"pid": 12, "start_ticks": "5", "state": "Z"}))

    def test_missing_identity_is_not_live_same(self):
        self.assertFalse(manager.live_same(None, {"pid": 12, "start_ticks": "5", "state": "R"}))
        self.assertFalse(manager.live_same({"pid": 12, "start_ticks": "5"}, None))

    def test_same_tick_different_pid_is_not_same(self):
        self.assertFalse(manager.live_same({"pid": 12, "start_ticks": "5"},
                                          {"pid": 13, "start_ticks": "5", "state": "R"}))

    def test_completed_corpus_never_starts_empty_client(self):
        result = self.launch_with([{"session": "done", "same_live_process": False,
                                    "exit": {"drained": True, "local_futures_finished": True,
                                             "corpus_complete": True}}])
        self.assertEqual(result["action"], "already_complete_no_new_client")

    def test_receipt_session_mismatch_blocks(self):
        directory = self.root / "launches" / "correct_session"
        directory.mkdir(parents=True)
        (directory / "intent.json").write_text(json.dumps({"session": directory.name}))
        (directory / "receipt.json").write_text(json.dumps({
            "session": "wrong_session", "pid": 123,
            "identity": {"pid": 123, "start_ticks": "5"}}))
        with self.assertRaisesRegex(RuntimeError, "identity/session mismatch"):
            manager.launch_records()

    def test_receipt_pid_mismatch_blocks(self):
        directory = self.root / "launches" / "correct_session"
        directory.mkdir(parents=True)
        (directory / "intent.json").write_text(json.dumps({"session": directory.name}))
        (directory / "receipt.json").write_text(json.dumps({
            "session": directory.name, "pid": 123,
            "identity": {"pid": 124, "start_ticks": "5"}}))
        with self.assertRaisesRegex(RuntimeError, "identity/session mismatch"):
            manager.launch_records()


if __name__ == "__main__":
    unittest.main()
