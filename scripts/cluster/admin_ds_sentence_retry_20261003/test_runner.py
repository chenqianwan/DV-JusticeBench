"""Offline tests only: no HTTP, SSH, Slurm, model requests or service changes."""
import fcntl
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.cluster.admin_ds_sentence_retry_20261003 import runner


def row(index=0):
    answer = "报警记录可以作为证据。法院仍需审查真实性。"
    packet = json.dumps({"sentence_table": runner.contract.build_sentences(answer), "case_text": "测试案情",
                         "question": "测试问题", "judge_decision": "测试裁判理由", "reference_pack": {}}, ensure_ascii=False)
    return {"request_id": "request-" + str(index), "answer": answer,
            "answer_sha256": runner.digest(answer.encode()), "sentences": runner.contract.build_sentences(answer),
            "packet_sha256": runner.digest(packet.encode()),
            "messages": [{"role": "system", "content": "Only validate anchors."}, {"role": "user", "content": packet}]}


def event(sentence_ids=None):
    return {"sentence_ids": sentence_ids or ["S000001"], "category": "H1", "reason": "材料不支持",
            "correct_content": "测试参考内容", "confidence": .8}


def reply(content="NONE", finish_reason="stop", refusal=None):
    return runner.canonical({"model": "deepseek_v4_flash",
        "choices": [{"finish_reason": finish_reason, "message": {"content": content, "refusal": refusal}}],
        "usage": {"completion_tokens": 9}})


class RunnerTests(unittest.TestCase):
    def output(self, directory):
        output = Path(directory) / "endpoint"
        output.mkdir()
        (output / "requests").mkdir()
        (output / "telemetry").mkdir()
        return output

    def manifest(self, directory, rows=None):
        directory = Path(directory)
        requests = directory / "requests.jsonl"
        requests.write_bytes(b"".join(runner.canonical(item) + b"\n" for item in (rows or [row(i) for i in range(128)])))
        paths = {"runner.py": Path(runner.__file__), "bench_external.py": runner.EXT_PATH,
                 "bench.py": runner.ext.BENCH_PATH, "contract.py": runner.ext.CONTRACT_PATH,
                 "sentence_contract.py": runner.CONTRACT_PATH}
        manifest = {"input_sha256": runner.digest(requests.read_bytes()), "request_count": 128,
                    "code_sha256": {name: runner.digest(path.read_bytes()) for name, path in paths.items()},
                    "previous_run_root": str(runner.PREVIOUS_ROOT)}
        path = directory / "manifest.json"
        path.write_text(json.dumps(manifest))
        return requests, path, manifest

    def previous(self, directory, state=None, ticks="1234"):
        root = Path(directory).resolve() / "previous"
        root.mkdir()
        (root / "code").mkdir()
        (root / "code" / "global.lock").touch()
        receipt = {"pid": 42, "identity": {"pid": 42, "start_ticks": "1234", "correct_runner": True}}
        (root / "launch_receipt.json").write_text(json.dumps(receipt))
        (root / "results").mkdir()
        for endpoint in (None, "10.33.4.146", "10.33.4.152"):
            path = root / "results" if endpoint is None else root / "results" / endpoint
            path.mkdir(exist_ok=True)
            (path / "exit.json").write_text(json.dumps({"completed": True, "drained": True}))
        proc = Path(directory).resolve() / "proc"
        proc.mkdir()
        if state:
            (proc / "42").mkdir()
            (proc / "42" / "stat").write_text("42 (python client) " + " ".join([state] + ["0"] * 18 + [ticks]))
        return root, proc

    def test_fixed_limits_no_formal_claim(self):
        self.assertEqual((runner.COUNT, runner.CONCURRENCY, runner.DEADLINE), (128, 128, 1800))
        self.assertEqual(runner.PARAMETERS["temperature"], .1)
        self.assertEqual(runner.PARAMETERS["max_tokens"], 3000)
        self.assertEqual(runner.PARAMETERS["chat_template_kwargs"], {"thinking": False})
        for field in ("reference_ready", "formal_hallucination_statistics_allowed", "legal_correctness_verified",
                      "automatic_retry_allowed", "gpu_telemetry_available"):
            self.assertFalse(runner.FLAGS[field])

    def test_load_hashes_and_exact_128(self):
        with tempfile.TemporaryDirectory() as directory:
            requests, manifest, _ = self.manifest(directory)
            rows, _ = runner.load_inputs(requests, manifest)
            self.assertEqual(len(rows), 128)

    def test_changed_input_and_code_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            requests, manifest, value = self.manifest(directory)
            value["code_sha256"]["runner.py"] = "0" * 64
            manifest.write_text(json.dumps(value))
            with self.assertRaisesRegex(ValueError, "code hash"):
                runner.load_inputs(requests, manifest)
        with tempfile.TemporaryDirectory() as directory:
            requests, manifest, _ = self.manifest(directory)
            requests.write_bytes(requests.read_bytes() + b" ")
            with self.assertRaisesRegex(ValueError, "input hash"):
                runner.load_inputs(requests, manifest)

    def test_manifest_must_have_exact_dependencies_and_previous_root(self):
        for mutate in (lambda m: m["code_sha256"].pop("contract.py"),
                       lambda m: m.update(previous_run_root="/tmp/other")):
            with tempfile.TemporaryDirectory() as directory:
                requests, manifest, value = self.manifest(directory)
                mutate(value)
                manifest.write_text(json.dumps(value))
                with self.assertRaises(ValueError):
                    runner.load_inputs(requests, manifest)

    def test_count_unique_answer_table_packet_validation(self):
        mutators = [lambda rows: rows.pop(), lambda rows: rows[1].update(request_id=rows[0]["request_id"]),
                    lambda rows: rows[0].update(answer="changed"),
                    lambda rows: rows[0]["sentences"]["sentences"][0].update(text="changed"),
                    lambda rows: rows[0]["messages"][1].update(content="changed")]
        for mutate in mutators:
            with tempfile.TemporaryDirectory() as directory:
                rows = [row(i) for i in range(128)]
                mutate(rows)
                requests, manifest, _ = self.manifest(directory, rows)
                with self.assertRaises(ValueError):
                    runner.load_inputs(requests, manifest)

    def test_delivered_table_and_exact_packet_fields_validated(self):
        def remove_key(packet):
            packet.pop("judge_decision")

        def add_key(packet):
            packet["answer"] = "unexpected unnumbered source"

        def replace_table(packet):
            packet["sentence_table"] = runner.contract.build_sentences("不同的原回答。")

        for mutate in (remove_key, add_key, replace_table):
            with tempfile.TemporaryDirectory() as directory:
                rows = [row(i) for i in range(128)]
                packet = json.loads(rows[0]["messages"][1]["content"])
                mutate(packet)
                content = json.dumps(packet, ensure_ascii=False)
                rows[0]["messages"][1]["content"] = content
                rows[0]["packet_sha256"] = runner.digest(content.encode())
                requests, manifest, _ = self.manifest(directory, rows)
                with self.assertRaisesRegex(ValueError, "packet fields|Delivered sentence"):
                    runner.load_inputs(requests, manifest)

    def test_previous_missing_pid_accepted_and_lock_held(self):
        with tempfile.TemporaryDirectory() as directory:
            root, proc = self.previous(directory)
            with runner.previous_finished(root, proc) as evidence:
                self.assertIsNone(evidence["live_identity"])
                with (root / "code" / "global.lock").open("rb") as handle:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_previous_live_same_identity_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root, proc = self.previous(directory, "S")
            with self.assertRaisesRegex(RuntimeError, "still exists"):
                with runner.previous_finished(root, proc):
                    pass

    def test_previous_zombie_and_reused_pid_accepted(self):
        for state, ticks in (("Z", "1234"), ("S", "other")):
            with tempfile.TemporaryDirectory() as directory:
                root, proc = self.previous(directory, state, ticks)
                with runner.previous_finished(root, proc):
                    pass

    def test_previous_missing_or_not_drained_receipt_blocks(self):
        for change in (None, {"completed": True, "drained": False}):
            with tempfile.TemporaryDirectory() as directory:
                root, proc = self.previous(directory)
                path = root / "results" / "10.33.4.152" / "exit.json"
                if change is None:
                    path.unlink()
                else:
                    path.write_text(json.dumps(change))
                with self.assertRaises((FileNotFoundError, RuntimeError)):
                    with runner.previous_finished(root, proc):
                        pass

    def test_previous_busy_lock_blocks(self):
        with tempfile.TemporaryDirectory() as directory:
            root, proc = self.previous(directory)
            with (root / "code" / "global.lock").open("rb") as handle:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                with self.assertRaises(BlockingIOError):
                    with runner.previous_finished(root, proc):
                        pass

    def test_valid_id_hydrates_exact_original_no_network(self):
        with tempfile.TemporaryDirectory() as directory:
            output, gate = self.output(directory), runner.ext.Gate()
            payload = json.dumps([event()], ensure_ascii=False)
            with patch.object(runner.ext, "http_once", return_value=(200, {}, reply(payload))) as http:
                result = runner.request_once(row(), 0, output, runner.BASE_URLS[0], gate)
            self.assertEqual(http.call_count, 1)
            self.assertTrue(result["valid_format"])
            self.assertTrue(result["json_syntax_valid"])
            self.assertTrue(result["hydrated_quote_exact"])
            self.assertFalse(result["legal_correctness_verified"])
            target = next((output / "requests").iterdir())
            native = json.loads((target / "parsed.native.json").read_text())
            hydrated = json.loads((target / "hydrated.json").read_text())
            self.assertEqual(native[0]["sentence_ids"], ["S000001"])
            self.assertEqual(hydrated["events"][0]["quote"], row()["sentences"]["sentences"][0]["text"])
            self.assertTrue((target / "intent.json").is_file())
            self.assertTrue((target / "response.raw.txt").is_file())

    def test_none_not_hallucination_truth(self):
        with tempfile.TemporaryDirectory() as directory:
            output, gate = self.output(directory), runner.ext.Gate()
            with patch.object(runner.ext, "http_once", return_value=(200, {}, reply())):
                result = runner.request_once(row(), 0, output, runner.BASE_URLS[0], gate)
            self.assertEqual(result["format_kind"], "NONE")
            self.assertEqual(result["event_count"], 0)
            self.assertIsNone(result["json_syntax_valid"])
            self.assertIsNone(result["hydrated_quote_exact"])
            self.assertIsNone(result["anchors_validated"])
            self.assertFalse(result["formal_hallucination_statistics_allowed"])

    def test_bad_json_vs_unknown_id_distinguished(self):
        for content, syntax in (("[{", False), (json.dumps([event(["S999999"])]), True)):
            with tempfile.TemporaryDirectory() as directory:
                output, gate = self.output(directory), runner.ext.Gate()
                with patch.object(runner.ext, "http_once", return_value=(200, {}, reply(content))):
                    result = runner.request_once(row(), 0, output, runner.BASE_URLS[0], gate)
                self.assertEqual(result["status"], "complete_invalid_format")
                self.assertEqual(result["json_syntax_valid"], syntax)
                self.assertTrue(result["format_error_reason"])
                self.assertFalse(gate.stop.is_set())
                self.assertEqual(list((output / "requests").glob("*/hydrated.json")), [])

    def test_truncated_no_auto_longer_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            output, gate = self.output(directory), runner.ext.Gate()
            with patch.object(runner.ext, "http_once", return_value=(200, {}, reply("[", "length"))) as http:
                result = runner.request_once(row(), 0, output, runner.BASE_URLS[0], gate)
            self.assertEqual(result["status"], "truncated")
            self.assertEqual(http.call_count, 1)
            self.assertEqual(json.loads(http.call_args.kwargs["body"])["max_tokens"], 3000)

    def test_timeout_and_interface_errors_halt_new_once(self):
        for timeout in (True, False):
            with tempfile.TemporaryDirectory() as directory:
                output, gate = self.output(directory), runner.ext.Gate()
                kwargs = {"side_effect": TimeoutError()} if timeout else {"return_value": (503, {}, b"busy")}
                with patch.object(runner.ext, "http_once", **kwargs) as http:
                    result = runner.request_once(row(), 0, output, runner.BASE_URLS[0], gate)
                    skipped = runner.request_once(row(1), 1, output, runner.BASE_URLS[1], gate)
                self.assertEqual(http.call_count, 1)
                self.assertEqual(result["timeout"], timeout)
                self.assertTrue(gate.stop.is_set())
                self.assertEqual(skipped["status"], "not_started")

    def test_intent_exists_before_send_and_duplicate_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            output, gate = self.output(directory), runner.ext.Gate()
            def fake(*args, **kwargs):
                self.assertEqual(len(list((output / "requests").glob("*/intent.json"))), 1)
                return 200, {}, reply()
            with patch.object(runner.ext, "http_once", side_effect=fake) as http:
                runner.request_once(row(), 0, output, runner.BASE_URLS[0], gate)
                with self.assertRaises(FileExistsError):
                    runner.request_once(row(), 0, output, runner.BASE_URLS[0], gate)
            self.assertEqual(http.call_count, 1)

    def test_status_readonly_no_http(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.object(runner.ext, "http_once", side_effect=AssertionError("HTTP forbidden")):
                value = runner.status(directory)
            self.assertTrue(value["exists"])
            self.assertFalse(value["reference_ready"])

    def test_summary_distinguishes_syntax_from_contract(self):
        results = [{"sent_intent": True, "complete": True, "status": "complete_invalid_format",
                    "json_syntax_valid": syntax, "format_error_reason": reason, "elapsed_s": .1}
                   for syntax, reason in ((False, "json"), (True, "unknown ID"))]
        value = runner.summarize(results, [], 1)
        self.assertEqual(value["json_syntax_errors"], 1)
        self.assertEqual(value["json_syntax_valid_but_contract_invalid"], 1)
        self.assertEqual(value["format_error_reasons"], {"json": 1, "unknown ID": 1})


if __name__ == "__main__":
    unittest.main()
