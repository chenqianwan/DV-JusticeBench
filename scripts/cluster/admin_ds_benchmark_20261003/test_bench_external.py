"""Offline-only safety and receipt tests; no live HTTP or service calls."""
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from scripts.cluster.admin_ds_benchmark_20261003 import bench_external as runner


def reply(content="NONE", finish_reason="stop", refusal=None):
    return runner.canonical({"model": runner.MODEL,
        "choices": [{"finish_reason": finish_reason, "message": {"content": content, "refusal": refusal}}],
        "usage": {"completion_tokens": 5}})


def row(index):
    return {"request_id": str(index), "answer": "original answer", "messages": [
        {"role": "system", "content": "frozen"}, {"role": "user", "content": "frozen question"}]}


def metrics(kv=.1, preemptions=0, generation=5):
    return (f"vllm:num_requests_running 0\nvllm:num_requests_waiting 0\n"
            f"vllm:kv_cache_usage_perc {kv}\nvllm:num_preemptions_total {preemptions}\n"
            f"vllm:generation_tokens_total {generation}\n").encode()


class ExternalTests(unittest.TestCase):
    def output(self, root):
        output = Path(root) / "endpoint"
        output.mkdir()
        (output / "requests").mkdir()
        (output / "telemetry").mkdir()
        return output

    def test_only_exact_authorized_urls(self):
        for base in runner.BASE_URLS:
            runner.validate_url(base + "/models")
            runner.validate_url(base + "/chat/completions")
            runner.validate_url(base[:-3] + "/metrics")
        for url in ("http://localhost/v1/models", runner.BASE_URLS[0] + "/models?x=1",
                    runner.BASE_URLS[0] + "/models/", "https://10.33.4.146:18044/v1/models",
                    "http://u:p@10.33.4.146:18044/v1/models"):
            with self.assertRaises(ValueError):
                runner.validate_url(url)

    def test_fixed_limits_and_unverified_metadata(self):
        self.assertEqual(runner.CONCURRENCY, 128)
        self.assertEqual(runner.REQUEST_DEADLINE_S, 1800)
        self.assertEqual(runner.SAMPLE_INTERVAL_S, 5)
        self.assertEqual(runner.PARAMETERS["max_tokens"], 3000)
        self.assertIsNone(runner.FLAGS["model_revision"])
        self.assertIsNone(runner.FLAGS["configured_max_num_seqs"])
        self.assertFalse(runner.FLAGS["gpu_telemetry_available"])
        for limit in (0, -1, 1801, True):
            with self.assertRaises(ValueError):
                runner.http_once(runner.BASE_URLS[0] + "/models", timeout_s=limit)

    def test_global_intent_rejects_restart_and_output_reuse(self):
        with tempfile.TemporaryDirectory() as root:
            state = Path(root).resolve()
            output = state / "run"
            with runner.single_use_guard(output, state_dir=state):
                self.assertTrue((output / "intent.json").exists())
                with self.assertRaises(BlockingIOError):
                    with runner.single_use_guard(state / "parallel", state_dir=state):
                        pass
            with self.assertRaises(FileExistsError):
                with runner.single_use_guard(state / "different_output", state_dir=state):
                    pass
        with tempfile.TemporaryDirectory() as root:
            state = Path(root).resolve()
            output = state / "existing"
            output.mkdir()
            with self.assertRaises(FileExistsError):
                with runner.single_use_guard(output, state_dir=state):
                    pass
            self.assertFalse((state / "global.intent.json").exists())

    def test_timeout_one_attempt_persisted_no_retry(self):
        with tempfile.TemporaryDirectory() as root:
            output, gate = self.output(root), runner.Gate()
            with patch.object(runner, "http_once", side_effect=TimeoutError()) as http:
                result = runner.request_once(row(0), 0, output, runner.BASE_URLS[0], gate)
                skipped = runner.request_once(row(1), 1, output, runner.BASE_URLS[0], gate)
            self.assertEqual(http.call_count, 1)
            self.assertEqual(http.call_args.kwargs["timeout_s"], 1800)
            self.assertTrue(result["timeout"])
            self.assertEqual(skipped["status"], "not_started")
            terminal = list((output / "requests").glob("*/terminal.json"))
            self.assertEqual(len(terminal), 1)
            self.assertFalse(json.loads(terminal[0].read_text())["automatic_retry_allowed"])

    def test_http_error_stops_next_and_keeps_raw(self):
        with tempfile.TemporaryDirectory() as root:
            output, gate = self.output(root), runner.Gate()
            with patch.object(runner, "http_once", return_value=(503, {}, b"busy")) as http:
                result = runner.request_once(row(0), 0, output, runner.BASE_URLS[0], gate)
                runner.request_once(row(1), 1, output, runner.BASE_URLS[0], gate)
            self.assertEqual(http.call_count, 1)
            self.assertTrue(result["service_error"])
            self.assertEqual(next((output / "requests").glob("*/response.raw.txt")).read_bytes(), b"busy")

    def test_strict_format_truncation_refusal_counts(self):
        values = []
        for raw in (reply(), reply("bad"), reply(finish_reason="length"), reply(refusal="no"), b"not JSON"):
            value = runner.bench.inspect_response(raw.decode(), "original answer", parser=runner.contract.parse_output)
            values.append({**value, "sent_intent": True, "elapsed_s": 1})
        result = runner.summarize(values, [], 5)
        self.assertEqual(result["complete"], 2)
        self.assertEqual(result["valid_format"], 1)
        self.assertEqual(result["truncated"], 1)
        self.assertEqual(result["refusals"], 1)
        self.assertEqual(result["errors"], 1)
        self.assertFalse(result["formal_hallucination_statistics_allowed"])
        self.assertFalse(result["ref_ready"])

    def test_sampler_stops_on_kv_preemption_reset_missing_or_bad_health(self):
        scenarios = [(metrics(.85), 200), (metrics(preemptions=1), 200),
                     (metrics(generation=1), 200), (b"", 200), (metrics(), 503)]
        for raw, health_status in scenarios:
            with self.subTest(raw=raw, status=health_status), tempfile.TemporaryDirectory() as root:
                output, gate = self.output(root), runner.Gate()
                sampler = runner.Sampler(runner.BASE_URLS[0], output, gate)
                with patch.object(runner, "http_once", side_effect=lambda url: (200, {}, metrics()) if url.endswith("metrics") else (200, {}, b"")):
                    sampler.take()
                with patch.object(runner, "http_once", side_effect=lambda url: (200, {}, raw) if url.endswith("metrics") else (health_status, {}, b"")):
                    sampler.take()
                self.assertTrue(gate.stop.is_set())
                self.assertEqual(len(list((output / "telemetry").glob("*.json"))), 2)

    def test_endpoint_max_128_active_and_drain_receipt(self):
        with tempfile.TemporaryDirectory() as root:
            output, gate = self.output(root), runner.Gate()
            sampler = runner.Sampler(runner.BASE_URLS[0], output, gate, interval=.01)
            active, peak, seen = 0, 0, []
            mutex = threading.Lock()
            def fake_request(item, index, out, base, stop_gate):
                nonlocal active, peak
                with mutex:
                    active += 1
                    peak = max(peak, active)
                    seen.append(index)
                time.sleep(.02)
                with mutex:
                    active -= 1
                return {"request_id": item["request_id"], "sent_intent": True, "status": "complete",
                        "complete": True, "valid_format": True, "elapsed_s": .02, "output_tokens": 5,
                        "usage_present": True}
            with patch.object(runner, "request_once", side_effect=fake_request), \
                 patch.object(runner, "wait_drained", return_value=True):
                result = runner.run_endpoint([row(i) for i in range(260)], output, runner.BASE_URLS[0], gate, sampler, {})
            self.assertLessEqual(peak, 128)
            self.assertGreater(peak, 1)
            self.assertEqual(len(set(seen)), 260)
            self.assertEqual(result["complete"], 260)
            self.assertTrue(result["drained"])
            self.assertTrue((output / "round.json").exists())
            self.assertTrue((output / "exit.json").exists())

    def test_old_idle_cannot_prove_drain(self):
        with tempfile.TemporaryDirectory() as root:
            sampler = runner.Sampler(runner.BASE_URLS[0], self.output(root), runner.Gate(), interval=.01)
            sampler.samples.append({"monotonic": 0, "healthy": True, "complete": True,
                                    "metrics": {"running": 0, "waiting": 0}})
            self.assertFalse(runner.wait_drained(sampler, timeout_s=.02))

    def test_status_and_input_failure_never_http(self):
        with tempfile.TemporaryDirectory() as root, patch.object(runner, "http_once", side_effect=AssertionError("network forbidden")) as http:
            self.assertFalse(runner.status(Path(root) / "missing")["exists"])
            with patch.object(runner.bench, "load_requests", return_value=([row(0)], "wrong")):
                with self.assertRaises(ValueError):
                    runner.run("input", Path(root) / "output")
            self.assertEqual(http.call_count, 0)


if __name__ == "__main__":
    unittest.main()
