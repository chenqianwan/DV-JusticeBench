"""Offline safety tests; no administrator endpoint is contacted."""
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest import mock

PATH = Path(__file__).with_name("runner.py")
spec = importlib.util.spec_from_file_location("full_runner_tested", PATH)
r = importlib.util.module_from_spec(spec)
spec.loader.exec_module(r)


def make_row(index):
    answer = "第一句话。第二句话。"
    table = r.contract.build_sentences(answer)
    packet = {"case_text": "案情", "question": "问题", "judge_decision": "理由",
              "reference_pack": {}, "sentence_table": table}
    content = json.dumps(packet, ensure_ascii=False)
    return {"request_id": "test_" + str(index), "global_index": index, "answer": answer,
            "sentences": table, "answer_sha256": r.digest(answer.encode()),
            "packet_sha256": r.digest(content.encode()),
            "messages": [{"role": "system", "content": "fixed"}, {"role": "user", "content": content}]}


def json_file(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.canonical(value))


class Fixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.rows = [make_row(index) for index in range(4)]
        self.total = mock.patch.object(r, "TOTAL", 4)
        self.total.start()
        self.addCleanup(self.total.stop)
        self.manifest = {"request_count": 4, "code_sha256": {name: r.file_sha(path) for name, path in r.code_paths().items()},
                         "shards": {}, "reused": {"path": "reused.jsonl", "count": 0}}
        (self.root / "prepared").mkdir()
        for offset, host in enumerate(r.HOSTS):
            path = self.root / "prepared" / (host + ".jsonl")
            path.write_bytes(b"".join(r.canonical(row) + b"\n" for row in self.rows[offset::2]))
            self.manifest["shards"][host] = {"path": path.name, "count": 2, "sha256": r.file_sha(path)}
        reused = self.root / "prepared" / "reused.jsonl"
        reused.write_bytes(b"")
        self.manifest["reused"]["sha256"] = r.file_sha(reused)
        self.freeze()

    def freeze(self):
        json_file(self.root / "prepared" / "manifest.json", self.manifest)

    def identity_map(self):
        return r.verify_manifest(self.root)[1]

    def attempt(self, index=0, session="session_0001", terminal=True, intent=True):
        row = self.rows[index]
        identities = self.identity_map()
        directory = self.root / "sessions" / session / identities[row["request_id"]]["host"] / "requests" / (
            f"{index:06d}_" + r.digest(row["request_id"].encode())[:16])
        directory.mkdir(parents=True)
        payload = {**r.PARAMETERS, "messages": row["messages"]}
        if intent:
            json_file(directory / "intent.json", {"request_id": row["request_id"],
                "answer_sha256": row["answer_sha256"], "packet_sha256": row["packet_sha256"],
                "payload": payload, "payload_sha256": r.digest(r.canonical(payload))})
        if terminal:
            json_file(directory / "terminal.json", {"request_id": row["request_id"], "sent_intent": True,
                "status": "complete", "valid_format": True, "format_kind": "NONE", "event_count": 0})
        return directory


class InputTests(Fixture):
    def test_manifest_disjoint_full_coverage(self):
        manifest, identities, reused = r.verify_manifest(self.root)
        self.assertEqual(len(identities), 4)
        self.assertEqual(reused, {})
        self.assertEqual(manifest["request_count"], 4)

    def test_mutated_shard_rejected(self):
        path = self.root / "prepared" / self.manifest["shards"][r.HOSTS[0]]["path"]
        path.write_bytes(path.read_bytes() + b" ")
        with self.assertRaisesRegex(ValueError, "hash/count"):
            r.verify_manifest(self.root)

    def test_duplicate_shard_identity_rejected(self):
        info = self.manifest["shards"][r.HOSTS[1]]
        path = self.root / "prepared" / info["path"]
        path.write_bytes(r.canonical(self.rows[0]) + b"\n" + r.canonical(self.rows[3]) + b"\n")
        info["sha256"] = r.file_sha(path)
        self.freeze()
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            r.verify_manifest(self.root)

    def test_relative_escape_rejected(self):
        with self.assertRaises(ValueError):
            r.safe_child(self.root, "../elsewhere")

    def test_actual_packet_mapping_required(self):
        row = self.rows[0]
        r.validate_row(row)
        packet = json.loads(row["messages"][1]["content"])
        packet["sentence_table"]["sentences"][0]["text"] = "different"
        row["messages"][1]["content"] = json.dumps(packet)
        row["packet_sha256"] = r.digest(row["messages"][1]["content"].encode())
        with self.assertRaisesRegex(ValueError, "Delivered sentence"):
            r.validate_row(row)

    def test_modified_answer_rejected(self):
        row = self.rows[0]
        row["answer"] += "修改"
        with self.assertRaisesRegex(ValueError, "answer hash"):
            r.validate_row(row)

    def test_reuse_keeps_invalid_outcome(self):
        row = self.rows[0]
        reusable = {key: row[key] for key in ("request_id", "global_index", "answer_sha256", "packet_sha256")}
        reusable.update(host=r.HOSTS[0], terminal={"request_id": row["request_id"], "sent_intent": True,
                                                 "status": "complete_invalid_format"})
        path = self.root / "prepared" / "reused.jsonl"
        path.write_bytes(r.canonical(reusable) + b"\n")
        self.manifest["reused"].update(count=1, sha256=r.file_sha(path))
        self.freeze()
        _, _, reuse = r.verify_manifest(self.root)
        self.assertEqual(reuse[row["request_id"]]["terminal"]["status"], "complete_invalid_format")
        reusable["host"] = r.HOSTS[1]
        path.write_bytes(r.canonical(reusable) + b"\n")
        self.manifest["reused"]["sha256"] = r.file_sha(path)
        self.freeze()
        with self.assertRaisesRegex(ValueError, "assigned"):
            r.verify_manifest(self.root)

    def test_only_untouched_rows_available(self):
        info = self.manifest["shards"][r.HOSTS[0]]
        rows = list(r.available_rows(self.root / "prepared" / info["path"], {"test_0": {}}))
        self.assertEqual([row["request_id"] for row in rows], ["test_2"])


class RecoveryTests(Fixture):
    def test_unknown_intent_never_eligible(self):
        directory = self.attempt(terminal=False)
        history = r.scan_history(self.root, self.identity_map(), {}, [directory.parents[2]])
        self.assertEqual(history["test_0"]["terminal"]["status"], "missing_terminal_needs_reconcile")
        self.assertEqual(r.count_records(history)["unknown"], 1)

    def test_empty_attempt_directory_is_also_quarantined(self):
        directory = self.attempt(terminal=False, intent=False)
        history = r.scan_history(self.root, self.identity_map(), {}, [directory.parents[2]])
        self.assertIn("test_0", history)

    def test_success_none_is_not_verified_negative(self):
        directory = self.attempt()
        history = r.scan_history(self.root, self.identity_map(), {}, [directory.parents[2]])
        summary = r.count_records(history)
        self.assertEqual(summary["none_candidates"], 1)
        self.assertFalse(summary["none_is_verified_negative"])

    def test_missing_intent_with_terminal_blocks(self):
        directory = self.attempt(intent=False)
        with self.assertRaisesRegex(RuntimeError, "without preserved"):
            r.scan_history(self.root, self.identity_map(), {}, [directory.parents[2]])

    def test_intent_binding_mismatch_blocks(self):
        directory = self.attempt()
        value = json.loads((directory / "intent.json").read_bytes())
        value["packet_sha256"] = "0" * 64
        json_file(directory / "intent.json", value)
        with self.assertRaisesRegex(RuntimeError, "binding"):
            r.scan_history(self.root, self.identity_map(), {}, [directory.parents[2]])

    def test_duplicate_attempt_blocks(self):
        first = self.attempt()
        second = self.attempt(session="session_0002")
        with self.assertRaisesRegex(RuntimeError, "Duplicate attempt"):
            r.scan_history(self.root, self.identity_map(), {}, [first.parents[2], second.parents[2]])

    def test_missing_exit_blocks_resume(self):
        path = self.root / "sessions" / "session_0001"
        json_file(path / "start.json", {"identity": {"pid": 999999999, "start_ticks": "1"}})
        with self.assertRaises(FileNotFoundError):
            r.prior_sessions(self.root)

    def test_live_identity_blocks_resume(self):
        path = self.root / "sessions" / "session_0001"
        json_file(path / "start.json", {"identity": r.pilot.read_identity(os.getpid()) or {"pid": 1, "start_ticks": "1"}})
        with mock.patch.object(r, "is_live", return_value=True):
            with self.assertRaisesRegex(RuntimeError, "still exists"):
                r.prior_sessions(self.root)

    def test_non_drained_exit_blocks_resume(self):
        path = self.root / "sessions" / "session_0001"
        json_file(path / "start.json", {"identity": {"pid": 999999999, "start_ticks": "1"}})
        json_file(path / "exit.json", {"local_futures_finished": True, "drained": False})
        with self.assertRaisesRegex(RuntimeError, "drained"):
            r.prior_sessions(self.root)

    def test_drained_real_exit_allows_resume(self):
        path = self.root / "sessions" / "session_0001"
        json_file(path / "start.json", {"identity": {"pid": 999999999, "start_ticks": "1"}})
        json_file(path / "exit.json", {"local_futures_finished": True, "drained": True})
        self.assertEqual(r.prior_sessions(self.root), [path])

    def test_coordinator_excludes_second_owner(self):
        with r.coordinator(self.root):
            with self.assertRaisesRegex(RuntimeError, "owned"):
                with r.coordinator(self.root, timeout_s=0):
                    self.fail("Second lock owner")

    def test_coordinator_waits_for_short_launch_handover(self):
        fd = os.open(self.root / "coordinator.lock", os.O_CREAT | os.O_RDWR, 0o600)
        r.fcntl.flock(fd, r.fcntl.LOCK_EX)
        timer = threading.Timer(.03, lambda: os.close(fd))
        timer.start()
        with r.coordinator(self.root, timeout_s=.5):
            pass
        timer.join()


class RequestTests(Fixture):
    class FakeSampler:
        def __init__(self, base, output, gate):
            self.samples, self.thread = [], None
        def take(self):
            value = {"complete": True, "healthy": True, "metrics": {"running": 0, "waiting": 0}}
            self.samples.append(value)
            return value
        def start(self):
            self.thread = True
        def finish(self):
            pass

    def run_fixture(self, session, http):
        with mock.patch.object(r, "CompactSampler", self.FakeSampler), \
                mock.patch.object(r.ext, "wait_drained", return_value=True), \
                mock.patch.object(r.ext, "read_models", return_value={"id": "deepseek_v4_flash"}), \
                mock.patch.object(r.pilot, "read_identity", return_value={"pid": 555555, "start_ticks": "123", "state": "R"}), \
                mock.patch.object(r, "is_live", return_value=False), \
                mock.patch.object(r.ext, "http_once", side_effect=http):
            return r.run(self.root, session)

    def test_full_disjoint_run_and_second_session_does_not_replay(self):
        sent = []
        def http(url, **kwargs):
            sent.append((url, kwargs["body"]))
            return 200, {}, r.canonical({"model": "deepseek_v4_flash", "choices": [
                {"finish_reason": "stop", "message": {"content": "NONE"}}]})
        first = self.run_fixture("session_0001", http)
        self.assertEqual(len(sent), 4)
        self.assertEqual(first["coverage"]["covered"], 4)
        self.assertTrue(first["corpus_complete"])
        self.assertEqual(first["coverage"]["none_candidates"], 4)
        second = self.run_fixture("session_0002", http)
        self.assertEqual(len(sent), 4)
        self.assertTrue(second["corpus_complete"])

    def test_unknown_isolated_on_resume_and_untouched_continue(self):
        directory = self.attempt(index=0, terminal=False)
        session = directory.parents[2]
        json_file(session / "start.json", {"identity": {"pid": 999999999, "start_ticks": "1"}})
        json_file(session / "exit.json", {"local_futures_finished": True, "drained": True})
        sent = []
        def http(url, **kwargs):
            sent.append(url)
            return 200, {}, r.canonical({"model": "deepseek_v4_flash", "choices": [
                {"finish_reason": "stop", "message": {"content": "NONE"}}]})
        result = self.run_fixture("session_0002", http)
        self.assertEqual(len(sent), 3)
        self.assertEqual(result["coverage"]["unknown"], 1)
        self.assertTrue(result["all_inputs_accounted"])
        self.assertFalse(result["corpus_complete"])

    def test_single_request_provisional_exact_quote(self):
        output = self.root / "output"
        (output / "requests").mkdir(parents=True)
        row = self.rows[0]
        content = json.dumps([{"sentence_ids": ["S000001"], "category": "H1", "reason": "测试",
                               "correct_content": "待复核", "confidence": .8}], ensure_ascii=False)
        response = r.canonical({"model": "deepseek_v4_flash", "choices": [
            {"finish_reason": "stop", "message": {"content": content}}], "usage": {"completion_tokens": 30}})
        with mock.patch.object(r.ext, "http_once", return_value=(200, {}, response)) as http:
            result = r.pilot.request_once(row, 0, output, r.BASE_URLS[0], r.ext.Gate())
        self.assertEqual(http.call_count, 1)
        self.assertTrue(result["valid_format"])
        self.assertTrue(result["provisional"])
        self.assertFalse(result["benchmark_only"])
        self.assertFalse(result["formal_hallucination_statistics_allowed"])
        hydrated = json.loads(next((output / "requests").iterdir()).joinpath("hydrated.json").read_bytes())
        self.assertIn(hydrated["events"][0]["quote"], row["answer"])

    def test_timeout_once_no_retry(self):
        output = self.root / "output"
        (output / "requests").mkdir(parents=True)
        gate = r.ext.Gate()
        with mock.patch.object(r.ext, "http_once", side_effect=TimeoutError()) as http:
            result = r.pilot.request_once(self.rows[0], 0, output, r.BASE_URLS[0], gate)
        self.assertEqual(http.call_count, 1)
        self.assertEqual(result["status"], "timeout_needs_reconcile")
        self.assertTrue(gate.stop.is_set())

    def test_pre_stopped_gate_sends_nothing(self):
        gate = r.ext.Gate()
        gate.halt("stop")
        with mock.patch.object(r.ext, "http_once") as http:
            result = r.pilot.request_once(self.rows[0], 0, self.root, r.BASE_URLS[0], gate)
        http.assert_not_called()
        self.assertFalse(result["sent_intent"])

    def test_endpoint_gates_independent(self):
        gates = {host: r.ext.Gate() for host in r.HOSTS}
        gates[r.HOSTS[0]].halt("network")
        self.assertFalse(gates[r.HOSTS[1]].stop.is_set())


if __name__ == "__main__":
    unittest.main()
