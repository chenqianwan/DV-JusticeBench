"""Offline parser, hash, response, and drain tests. No live HTTP/GPU calls."""
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from types import SimpleNamespace

from scripts.cluster.v4_flash_20261002_autotune import bench
from scripts.cluster.v4_flash_20261002_autotune.policy import next_action


def reply(content="NONE", finish_reason="stop", model=bench.MODEL, **extra):
    return bench.canonical({"id": "test-response", "model": model,
        "choices": [{"finish_reason": finish_reason, "message": {"content": content}}],
        "usage": {"completion_tokens": 5}, **extra}).decode()


def packet_row(index):
    packet = {"answer": "原始答案", "case_text": "案情", "question": "问题"}
    return {"request_id": f"q{index}", "answer": packet["answer"],
            "messages": [{"role": "system", "content": "固定 H1-H5"},
                         {"role": "user", "content": bench.canonical(packet).decode()}],
            "packet_sha256": bench.digest(bench.canonical(packet))}


class BenchTests(unittest.TestCase):
    def _mock_round(self, directory, *, fail_post=False):
        root = Path(directory).resolve()
        path = root / "requests.jsonl"
        path.write_bytes(b"\n".join(bench.canonical(packet_row(index)) for index in range(4)) + b"\n")
        key = root / "key"
        key.write_text("offline-unit-test-key-not-a-secret-0000")
        key.chmod(0o600)
        gpu = "\n".join(f"{i}, NVIDIA H800, 99, 45000, 80000, 500, 78" for i in range(8))
        metrics = "\n".join([
            "vllm:num_requests_running 0", "vllm:num_requests_waiting 0",
            "vllm:kv_cache_usage_perc 0.2", "vllm:num_preemptions_total 0",
            "vllm:generation_tokens_total 50",
        ])
        calls = []
        def fake_http(url, api_key, *, body=None, timeout_s=3):
            if body is not None:
                calls.append(url)
                if fail_post:
                    raise TimeoutError("offline simulated timeout")
                return 200, {}, reply()
            return (200, {}, metrics) if url.endswith("/metrics") else (200, {}, "")
        with patch.object(bench, "MIN_REQUESTS", 4), patch.object(bench, "_http", side_effect=fake_http), \
             patch.object(bench.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=gpu)):
            result = bench.run_round(concurrency=128, requests_path=path, output_dir=root / "round",
                base_url="http://127.0.0.1:18044/v1", api_key_file=key, sample_interval=.005)
        return result, calls, root / "round"

    def test_metrics_labels_and_aggregation(self):
        text = '\n'.join([
            '# HELP vllm:num_requests_running test',
            'vllm:num_requests_running{model_name="deepseek_v4_flash",engine="0"} 128',
            'vllm:num_requests_running{model_name="deepseek_v4_flash",engine="1"} 4',
            'vllm:num_requests_waiting 0', 'vllm:kv_cache_usage_perc{engine="0"} 0.4',
            'vllm:kv_cache_usage_perc{engine="1"} 0.6',
            'vllm:num_preemptions_total 3', 'vllm:generation_tokens_total 1.2e+5',
        ])
        metrics = bench.parse_metrics(text)
        self.assertEqual(metrics["running"], 132)
        self.assertEqual(metrics["waiting"], 0)
        self.assertEqual(metrics["kv_cache_usage"], .6)
        self.assertEqual(metrics["preemptions_total"], 3)
        self.assertEqual(metrics["generation_tokens_total"], 120000)

    def test_metrics_old_kv_name_and_missing_are_not_zero(self):
        result = bench.parse_metrics("vllm:gpu_cache_usage_perc 0.2\nvllm:num_requests_running NaN")
        self.assertEqual(result["kv_cache_usage"], .2)
        self.assertIsNone(result["running"])
        self.assertIsNone(result["preemptions_total"])

    def test_gpu_requires_eight_complete_observations(self):
        text = "\n".join(f"{i}, NVIDIA H800, 99, 45000, 80000, 500, 78" for i in range(8))
        self.assertEqual(len(bench.parse_gpu(text)), 8)
        for invalid in ("\n".join(text.splitlines()[:7]), text.replace(", 500,", ", [N/A],"), text.replace("0, NVIDIA", "1, NVIDIA")):
            with self.assertRaises(ValueError):
                bench.parse_gpu(invalid)

    def test_stop_none_is_only_structurally_valid_not_formal(self):
        result = bench.inspect_response(reply(), "answer")
        self.assertTrue(result["complete"])
        self.assertTrue(result["valid_format"])
        self.assertEqual(result["format_kind"], "NONE")
        self.assertEqual(result["output_tokens"], 5)
        self.assertFalse(result["formal_hallucination_statistics_allowed"])

    def test_event_quote_validation(self):
        event = [{"quote": "具体事实", "category": "H1", "reason": "案情无记载", "correct_content": "不能据此认定", "confidence": .9}]
        content = bench.canonical(event).decode()
        self.assertTrue(bench.inspect_response(reply(content), "模型说了具体事实")["valid_format"])
        self.assertFalse(bench.inspect_response(reply(content), "其他答案")["valid_format"])

    def test_truncation_not_complete_or_valid_even_if_none(self):
        result = bench.inspect_response(reply(finish_reason="length"), "answer")
        self.assertFalse(result["complete"])
        self.assertFalse(result["valid_format"])
        self.assertEqual(result["status"], "truncated")

    def test_invalid_format_is_complete_but_invalid(self):
        result = bench.inspect_response(reply("```json\nNONE\n```"), "answer")
        self.assertTrue(result["complete"])
        self.assertFalse(result["valid_format"])

    def test_response_model_mismatch_empty_or_bad_schema(self):
        for raw in (reply(model="wrong"), reply(content=""), "not JSON", "[]"):
            result = bench.inspect_response(raw, "answer")
            self.assertFalse(result["complete"])
            self.assertTrue(result["service_error"])

    def test_http_oom_and_missing_usage(self):
        result = bench.inspect_response("CUDA out of memory", "answer", status=500)
        self.assertTrue(result["oom"])
        self.assertTrue(result["service_error"])
        result = bench.inspect_response(reply(usage={}), "answer")
        self.assertFalse(result["usage_present"])
        self.assertTrue(result["complete"])

    def test_oom_words_in_generated_content_are_not_service_oom(self):
        result = bench.inspect_response(reply("The phrase out of memory is not a GPU error here."), "answer")
        self.assertFalse(result["oom"])
        result = bench.inspect_response('{"error":{"message":"CUDA out of memory"}}', "answer")
        self.assertTrue(result["oom"])

    def test_science_hash_fixed_and_sensitive(self):
        value = bench.science_hash("a" * 64, "b" * 64)
        self.assertEqual(len(value), 64)
        self.assertEqual(value, bench.science_hash("a" * 64, "b" * 64))
        self.assertNotEqual(value, bench.science_hash("c" * 64, "b" * 64))
        self.assertNotEqual(value, bench.science_hash("a" * 64, "c" * 64))
        with patch.dict(bench.PARAMETERS, max_tokens=256):
            self.assertNotEqual(value, bench.science_hash("a" * 64, "b" * 64))

    def test_inputs_whole_identical_pool_hash_and_packet_check(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.jsonl"
            rows = [packet_row(index) for index in range(1024)]
            raw = b"\n".join(bench.canonical(row) for row in rows) + b"\n"
            path.write_bytes(raw)
            got, value = bench.load_requests(path)
            self.assertEqual(len(got), 1024)
            self.assertEqual(value, bench.digest(raw))
            rows[0]["packet_sha256"] = "f" * 64
            path.write_bytes(b"\n".join(bench.canonical(row) for row in rows) + b"\n")
            with self.assertRaisesRegex(ValueError, "packet hash"):
                bench.load_requests(path)

    def test_too_small_or_duplicate_input_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "requests.jsonl"
            path.write_bytes(bench.canonical(packet_row(0)) + b"\n")
            with self.assertRaisesRegex(ValueError, "1024"):
                bench.load_requests(path)
            path.write_bytes((bench.canonical(packet_row(0)) + b"\n") * 1024)
            with self.assertRaisesRegex(ValueError, "duplicate request"):
                bench.load_requests(path)

    def test_old_idle_sample_cannot_prove_post_request_drain(self):
        class Fake:
            condition = threading.Condition()
            samples = [{"healthy": True, "metrics_status": 200, "metrics": {"running": 0, "waiting": 0}, "monotonic": 0}]
            interval = .01
            thread = None
        self.assertFalse(bench.wait_drained(Fake(), timeout_s=.025))

    def test_two_new_idle_samples_required_after_busy(self):
        class Fake:
            condition = threading.Condition()
            samples = []
            interval = .005
            thread = None
        sampler = Fake()
        def add():
            for running in (1, 0, 0):
                time.sleep(.01)
                with sampler.condition:
                    sampler.samples.append({"healthy": True, "metrics_status": 200,
                        "metrics": {"running": running, "waiting": 0}, "monotonic": time.monotonic()})
                    sampler.condition.notify_all()
        thread = threading.Thread(target=add)
        thread.start()
        self.assertTrue(bench.wait_drained(sampler, timeout_s=1))
        thread.join()

    def test_live_calls_not_made_by_validation_failure(self):
        with patch.object(bench, "_http", side_effect=AssertionError("Network forbidden")):
            with self.assertRaises(ValueError):
                bench.run_round(concurrency=1024, requests_path="missing", output_dir="missing", base_url="http://localhost/v1", api_key_file="missing")

    def test_mock_round_writes_raw_intent_terminal_and_proves_drain(self):
        with tempfile.TemporaryDirectory() as directory:
            result, calls, output = self._mock_round(directory)
            self.assertTrue(result["drained"])
            self.assertTrue(result["observations_complete"])
            self.assertEqual(result["complete"], 4)
            self.assertEqual(result["valid_format"], 4)
            self.assertEqual(len(calls), 4)
            self.assertEqual(len(list(output.glob("requests/*/intent.json"))), 4)
            self.assertEqual(len(list(output.glob("requests/*/response.raw.txt"))), 4)
            self.assertEqual(len(list(output.glob("requests/*/terminal.json"))), 4)
            self.assertTrue((output / "exit.json").is_file())
            self.assertEqual(next_action([result])["concurrency"], 256)

    def test_timeout_once_then_proven_drain_permits_baseline_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            result, calls, output = self._mock_round(directory, fail_post=True)
            self.assertTrue(result["drained"])
            self.assertTrue(result["observations_complete"])
            self.assertEqual(result["timeouts"], len(calls))
            self.assertLessEqual(len(calls), 4)
            self.assertEqual(result["complete"], 0)
            self.assertEqual(next_action([result])["concurrency"], 64)
            self.assertEqual(len(list(output.glob("requests/*/intent.json"))), len(calls))
            self.assertFalse(list(output.glob("requests/*/response.raw.txt")))


if __name__ == "__main__":
    unittest.main()
