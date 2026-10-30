"""Single-use, two-endpoint HTTP-only administrator-service benchmark.

No service/allocation management, GPU probes, API keys, proxies, retries,
automatic recovery, or scientific hallucination claims. All client limits are
fixed here; none establish the administrator's server capacity/configuration.
"""
from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
import fcntl
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import signal
import socket
import threading
import time
import uuid
from urllib.parse import urlsplit


HERE = Path(__file__).resolve().parent


def dependency(name, fallback):
    path = HERE / (name + ".py")
    if not path.is_file():
        path = HERE.parent / fallback
    spec = importlib.util.spec_from_file_location("_admin_external_" + name, path)
    if spec is None or spec.loader is None:
        raise ValueError("Frozen dependency unavailable: " + name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path


bench, BENCH_PATH = dependency("bench", "v4_flash_20261002_autotune/bench.py")
contract, CONTRACT_PATH = dependency("contract", "../hallucination_h1_h5_20261002/contract.py")
canonical, digest, now = bench.canonical, bench.digest, bench.now
BASE_URLS = ("http://10.33.4.146:18044/v1", "http://10.33.4.152:18044/v1")
CONCURRENCY = 128
REQUEST_DEADLINE_S = 1800
DRAIN_TIMEOUT_S = 1800
SAMPLE_INTERVAL_S = 5
INPUT_SHA256 = "5b7ce82cd05936b70605a946b495d0bc6023b7b8eab3ce776f38c20a4cd17506"
REQUEST_COUNT = 1024
MODEL = "deepseek_v4_flash"
PARAMETERS = {"model": MODEL, "temperature": 0.1, "max_tokens": 3000,
              "chat_template_kwargs": {"thinking": False}}
FLAGS = {"benchmark_only": True, "ref_ready": False,
         "formal_hallucination_statistics_allowed": False,
         "gpu_telemetry_available": False, "model_revision": None,
         "configured_max_num_seqs": None, "automatic_retry_allowed": False}
HEADER_ALLOWLIST = frozenset(("content-type", "content-length", "date", "server",
                             "request-id", "x-request-id", "retry-after"))


def write_raw(path, data):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())


def write_json(path, value, *, replace=False):
    path = Path(path)
    raw = canonical(value) + b"\n"
    if not replace:
        write_raw(path, raw)
        return
    temporary = path.with_name(path.name + ".tmp." + uuid.uuid4().hex)
    write_raw(temporary, raw)
    os.replace(temporary, path)


def validate_url(url):
    if url not in {base + "/chat/completions" for base in BASE_URLS} | {
        base + "/models" for base in BASE_URLS
    } | {base[:-3] + suffix for base in BASE_URLS for suffix in ("/health", "/metrics")}:
        raise ValueError("URL is not an exact authorized endpoint/path")
    return urlsplit(url)


class HTTPFailure(Exception):
    def __init__(self, error, *, timed_out, status, headers, raw):
        super().__init__(type(error).__name__)
        self.error_type = type(error).__name__
        self.timed_out, self.status, self.headers, self.raw = timed_out, status, headers, raw


def http_once(url, *, body=None, timeout_s=2):
    """One direct request, including a wall-clock deadline and partial evidence.

    http.client ignores proxy environment variables and follows no redirects.
    The watchdog bounds slow headers / trickled data as well as socket waits.
    """
    parts = validate_url(url)
    if type(timeout_s) not in (int, float) or not 0 < timeout_s <= REQUEST_DEADLINE_S:
        raise ValueError("HTTP deadline must be within (0, 1800]")
    if body is not None and not url.endswith("/v1/chat/completions"):
        raise ValueError("POST only permitted to chat/completions")
    deadline = time.monotonic() + timeout_s
    connection = http.client.HTTPConnection(parts.hostname, parts.port, timeout=timeout_s)
    expired = threading.Event()
    transport = []
    chunks, headers, status = [], {}, None

    def abort():
        expired.set()
        if transport:
            try:
                transport[0].shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def remaining():
        left = deadline - time.monotonic()
        if left <= 0 or expired.is_set():
            raise TimeoutError("HTTP wall-clock deadline exceeded")
        if transport:
            transport[0].settimeout(left)

    timer = threading.Timer(timeout_s, abort)
    timer.daemon = True
    timer.start()
    try:
        connection.connect()
        transport.append(connection.sock)
        remaining()
        request_headers = {"Accept": "application/json"}
        if body is not None:
            request_headers["Content-Type"] = "application/json"
        connection.request("POST" if body is not None else "GET", parts.path,
                           body=body, headers=request_headers)
        remaining()
        response = connection.getresponse()
        status = response.status
        headers = {key.lower(): value for key, value in response.getheaders()
                   if key.lower() in HEADER_ALLOWLIST}
        while True:
            remaining()
            chunk = response.read1(65536)
            if not chunk:
                if response.length not in (None, 0):
                    raise http.client.IncompleteRead(b"".join(chunks), response.length)
                break
            chunks.append(chunk)
        remaining()
        return status, headers, b"".join(chunks)
    except Exception as error:
        raise HTTPFailure(error, timed_out=expired.is_set() or isinstance(error, (TimeoutError, socket.timeout)),
                          status=status, headers=headers, raw=b"".join(chunks)) from error
    finally:
        timer.cancel()
        connection.close()


class Gate:
    def __init__(self):
        self.lock = threading.RLock()
        self.stop = threading.Event()
        self.reasons = []

    def halt(self, reason):
        with self.lock:
            if reason not in self.reasons:
                self.reasons.append(reason)
            self.stop.set()


def server_idle(sample):
    metrics = sample.get("metrics", {})
    return (sample.get("complete") is True and sample.get("healthy") is True
            and metrics.get("running") == 0 and metrics.get("waiting") == 0)


class Sampler:
    def __init__(self, base_url, output, gate, interval=SAMPLE_INTERVAL_S):
        if base_url not in BASE_URLS or not 0 < interval <= SAMPLE_INTERVAL_S:
            raise ValueError("Unauthorized endpoint or sample interval")
        self.base_url, self.output, self.gate, self.interval = base_url, output, gate, interval
        self.samples = []
        self.condition = threading.Condition()
        self.finished = threading.Event()
        self.thread = None
        self.previous = {}

    def halt(self, reason):
        self.gate.halt(self.base_url + ":" + reason)

    def take(self):
        sample = {"at": now(), "monotonic": time.monotonic(), "healthy": False,
                  "metrics": {}, **FLAGS}
        for name in ("health", "metrics"):
            try:
                status, headers, raw = http_once(self.base_url[:-3] + "/" + name)
                sample.update({name + "_status": status, name + "_headers": headers,
                               name + "_raw": raw.decode("utf-8", errors="replace"),
                               name + "_raw_sha256": digest(raw)})
                if name == "health":
                    sample["healthy"] = status == 200
                elif status == 200:
                    sample["metrics"] = bench.parse_metrics(sample["metrics_raw"])
            except Exception as error:
                sample[name + "_error_type"] = type(error).__name__
                if isinstance(error, HTTPFailure):
                    sample.update({name + "_status": error.status, name + "_headers": error.headers,
                                   name + "_raw": error.raw.decode("utf-8", errors="replace"),
                                   name + "_raw_sha256": digest(error.raw)})
        metrics = sample["metrics"]
        sample["complete"] = (sample.get("metrics_status") == 200
                              and type(sample.get("health_status")) is int
                              and all(metrics.get(key) is not None for key in bench.REQUIRED_METRICS))
        if not sample["healthy"]:
            self.halt("health_failed")
        if not sample["complete"]:
            self.halt("telemetry_incomplete")
        if metrics.get("kv_cache_usage") is not None and metrics["kv_cache_usage"] >= .85:
            self.halt("kv_cache_ge_85_percent")
        for key in ("preemptions_total", "generation_tokens_total"):
            value = metrics.get(key)
            if value is not None and key in self.previous:
                if value < self.previous[key]:
                    sample["complete"] = False
                    self.halt("server_counter_reset:" + key)
                elif key == "preemptions_total" and value > self.previous[key]:
                    self.halt("new_preemptions")
            if value is not None:
                self.previous[key] = value
        with self.condition:
            sample["sequence"] = len(self.samples)
            write_json(self.output / "telemetry" / f"{sample['sequence']:06d}.json", sample)
            self.samples.append(sample)
            self.condition.notify_all()
        return sample

    def start(self):
        def loop():
            next_at = time.monotonic() + self.interval
            while not self.finished.wait(max(0, next_at - time.monotonic())):
                try:
                    self.take()
                except Exception as error:
                    self.halt("sampler_recording_failed:" + type(error).__name__)
                    with self.condition:
                        self.condition.notify_all()
                    return
                next_at = max(next_at + self.interval, time.monotonic())
        self.thread = threading.Thread(target=loop, name="http-telemetry", daemon=True)
        self.thread.start()

    def finish(self):
        self.finished.set()
        if self.thread:
            self.thread.join(timeout=10)
            if self.thread.is_alive():
                self.halt("sampler_did_not_stop")


def wait_drained(sampler, timeout_s=DRAIN_TIMEOUT_S):
    if not 0 < timeout_s <= DRAIN_TIMEOUT_S:
        raise ValueError("Drain deadline exceeds 1800 seconds")
    after = time.monotonic()
    deadline = after + timeout_s
    idle = 0
    with sampler.condition:
        seen = len(sampler.samples)
        while time.monotonic() < deadline:
            for sample in sampler.samples[seen:]:
                seen += 1
                if sample["monotonic"] < after:
                    continue
                idle = idle + 1 if server_idle(sample) else 0
                if idle >= 2:
                    return True
            if sampler.thread and not sampler.thread.is_alive():
                return False
            sampler.condition.wait(min(sampler.interval + 1, max(0, deadline - time.monotonic())))
    return False


def request_once(row, index, output, base_url, gate):
    """Reserve at most once under the stop gate, then finish without retries."""
    with gate.lock:
        if gate.stop.is_set():
            return {"request_id": row["request_id"], "status": "not_started", "sent_intent": False}
        started = time.monotonic()
        directory = output / "requests" / f"{index:06d}_{digest(row['request_id'].encode())[:16]}"
        directory.mkdir(mode=0o700)
        payload = {**PARAMETERS, "messages": row["messages"]}
        body = canonical(payload)
        write_json(directory / "intent.json", {
            "request_id": row["request_id"], "at": now(), "payload": payload,
            "payload_sha256": digest(body), "answer_sha256": digest(row["answer"].encode()),
            "timeout_s": REQUEST_DEADLINE_S, "attempt": 1, **FLAGS})
    result = {"request_id": row["request_id"], "sent_intent": True, "complete": False,
              "valid_format": False, "output_tokens": 0, "usage_present": False,
              "service_error": False, "timeout": False, "oom": False, **FLAGS}
    try:
        status, headers, raw = http_once(base_url + "/chat/completions", body=body,
                                        timeout_s=REQUEST_DEADLINE_S)
        write_raw(directory / "response.raw.txt", raw)
        write_json(directory / "response.http.json", {"at": now(), "status": status,
                   "headers": headers, "raw_sha256": digest(raw), "body_complete": True})
        result.update(bench.inspect_response(raw.decode("utf-8", errors="replace"), row["answer"],
                                             status=status, parser=contract.parse_output))
        if result["service_error"]:
            gate.halt(base_url + ":request_service_error")
    except Exception as error:
        timed_out = isinstance(error, (TimeoutError, socket.timeout)) or (
            isinstance(error, HTTPFailure) and error.timed_out)
        result.update(status="timeout_needs_reconcile" if timed_out else "transport_or_recording_error_needs_reconcile",
                      timeout=timed_out, service_error=not timed_out, error_type=type(error).__name__)
        gate.halt(base_url + (":request_timeout" if timed_out else ":request_transport_or_recording_error"))
        if isinstance(error, HTTPFailure):
            write_raw(directory / "response.partial.raw.txt", error.raw)
            write_json(directory / "response.http.json", {"at": now(), "status": error.status,
                       "headers": error.headers, "raw_sha256": digest(error.raw), "body_complete": False})
    result.update(at=now(), elapsed_s=time.monotonic() - started)
    write_json(directory / "terminal.json", result)
    return result


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * quantile) - 1)]


def summarize(results, samples, elapsed):
    attempted = [item for item in results if item.get("sent_intent")]
    metrics = [sample.get("metrics", {}) for sample in samples]
    latencies = [item["elapsed_s"] for item in attempted if "elapsed_s" in item]
    complete_latencies = [item["elapsed_s"] for item in attempted if item.get("complete")]
    def series(key):
        return [item[key] for item in metrics if item.get(key) is not None]
    preemptions = series("preemptions_total")
    generation = series("generation_tokens_total")
    reset = any(right < left for values in (preemptions, generation)
                for left, right in zip(values, values[1:]))
    complete = sum(bool(item.get("complete")) for item in attempted)
    tokens = sum(item.get("output_tokens", 0) for item in attempted)
    return {
        "attempted": len(attempted), "terminal_records": len(attempted), "complete": complete,
        "valid_format": sum(bool(item.get("valid_format")) for item in attempted),
        "truncated": sum(item.get("status") == "truncated" for item in attempted),
        "refusals": sum(item.get("status") == "refusal" for item in attempted),
        "non_stop": sum(item.get("status") == "non_stop" for item in attempted),
        "invalid_format": sum(item.get("status") == "complete_invalid_format" for item in attempted),
        "errors": sum(bool(item.get("service_error")) for item in attempted),
        "timeouts": sum(bool(item.get("timeout")) for item in attempted),
        "output_tokens": tokens, "usage_missing": sum(not item.get("usage_present") for item in attempted),
        "requests_elapsed_s": elapsed, "completed_req_per_s": complete / elapsed if elapsed > 0 else None,
        "attempted_req_per_s": len(attempted) / elapsed if elapsed > 0 else None,
        "output_tokens_per_s": tokens / elapsed if elapsed > 0 else None,
        "output_tokens_source": "response usage.completion_tokens; missing usage is not estimated",
        "latency_s": {"population": "all attempted with terminal elapsed_s", "p50": percentile(latencies, .5),
                      "p95": percentile(latencies, .95), "max": max(latencies, default=None)},
        "complete_latency_s": {"p50": percentile(complete_latencies, .5),
                               "p95": percentile(complete_latencies, .95), "max": max(complete_latencies, default=None)},
        "peak_server_running": max(series("running"), default=None),
        "peak_server_waiting": max(series("waiting"), default=None),
        "peak_kv_cache": max(series("kv_cache_usage"), default=None),
        "preemptions_delta": preemptions[-1] - preemptions[0] if preemptions and not reset else None,
        "generation_tokens_delta": generation[-1] - generation[0] if generation and not reset else None,
        "counter_reset_detected": reset, "telemetry_samples": len(samples), **FLAGS,
    }


@contextmanager
def dispatch_pool(gate):
    # Halt before ThreadPoolExecutor.__exit__ waits on the existing futures.
    with ThreadPoolExecutor(max_workers=CONCURRENCY, thread_name_prefix="admin-http") as pool:
        try:
            yield pool
        except BaseException as error:
            gate.halt("dispatch_or_recording_failed:" + type(error).__name__)
            raise


def run_endpoint(rows, output, base_url, gate, sampler, models):
    started = time.monotonic()
    results, worker_errors, scheduled = [], [], 0
    def progress():
        value = {"at": now(), "base_url": base_url, "planned": len(rows), "scheduled": scheduled,
                 "stop_reasons": list(gate.reasons), **summarize(results, list(sampler.samples), time.monotonic() - started)}
        write_json(output / "progress.json", value, replace=True)
    failure, requests_finished, drained = None, None, False
    try:
        with dispatch_pool(gate) as pool:
            progress()
            pending = {}
            last_progress = 0
            while pending or (scheduled < len(rows) and not gate.stop.is_set()):
                while len(pending) < CONCURRENCY and scheduled < len(rows) and not gate.stop.is_set():
                    index = scheduled
                    pending[pool.submit(request_once, rows[index], index, output, base_url, gate)] = index
                    scheduled += 1
                if not pending:
                    break
                done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    index = pending.pop(future)
                    try:
                        results.append(future.result())
                    except Exception as error:
                        gate.halt(base_url + ":worker_recording_error")
                        item = {"request_id": rows[index]["request_id"], "status": "worker_error_needs_reconcile",
                                "error_type": type(error).__name__, "at": now(), **FLAGS}
                        worker_errors.append(item)
                        write_json(output / f"worker_exception_{index:06d}.json", item)
                if time.monotonic() - last_progress >= SAMPLE_INTERVAL_S:
                    progress()
                    last_progress = time.monotonic()
        requests_finished = time.monotonic()
        progress()
    except BaseException as error:
        gate.halt(base_url + ":endpoint_runner_failed:" + type(error).__name__)
        failure = error
    finally:
        if requests_finished is None:
            requests_finished = time.monotonic()
        try:
            drained = wait_drained(sampler)
        finally:
            sampler.finish()
    if failure is not None:
        write_json(output / "exit.json", {"at": now(), "completed": False, "drained": drained,
                   "error_type": type(failure).__name__, "stop_reasons": list(gate.reasons), **FLAGS})
        raise failure
    attempted_ids = {item["request_id"] for item in results if item.get("sent_intent")}
    uncertain_ids = {item["request_id"] for item in worker_errors}
    not_started = [row["request_id"] for row in rows if row["request_id"] not in attempted_ids | uncertain_ids]
    write_json(output / "not_started.json", {"request_ids": not_started, "count": len(not_started), **FLAGS})
    result = {"at": now(), "base_url": base_url, "concurrency": CONCURRENCY, "planned": len(rows),
              "not_started": len(not_started), "worker_exceptions": worker_errors, "models": models,
              "drained": drained, "local_futures_finished": True,
              "server_idle_consecutive_samples": 2 if drained else 0,
              "drain_elapsed_s": time.monotonic() - requests_finished,
              "stop_reasons": list(gate.reasons),
              "observations_complete": bool(sampler.samples) and all(s["complete"] for s in sampler.samples)
                                       and drained and not worker_errors,
              **summarize(results, list(sampler.samples), requests_finished - started)}
    write_json(output / "round.json", result)
    write_json(output / "exit.json", {"at": now(), "completed": True, "drained": drained,
                                     "round_sha256": digest(canonical(result)), **FLAGS})
    progress()
    return result


@contextmanager
def single_use_guard(output, *, state_dir=HERE):
    """A durable run intent forbids restart even after lock release/crash."""
    output = Path(output).absolute()
    if output.resolve() != output:
        raise ValueError("Output path must not traverse symlinks")
    state_dir = Path(state_dir)
    descriptor = os.open(state_dir / "global.lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        if (state_dir / "global.intent.json").exists():
            raise FileExistsError("Global intent exists; automatic restart/recovery forbidden")
        if output.exists():
            raise FileExistsError("Output exists; overwrite forbidden")
        output.mkdir(mode=0o700, parents=False, exist_ok=False)
        intent = {"at": now(), "pid": os.getpid(), "output": str(output), "base_urls": BASE_URLS, **FLAGS}
        write_json(state_dir / "global.intent.json", intent)
        write_json(output / "intent.json", intent)
        yield output
    finally:
        os.close(descriptor)


def read_models(base_url, output, gate):
    record = {"at": now(), "base_url": base_url, "model_name": MODEL, "context": None, **FLAGS}
    try:
        status, headers, raw = http_once(base_url + "/models", timeout_s=10)
        write_raw(output / "models.raw.json", raw)
        record.update(http_status=status, headers=headers, raw_sha256=digest(raw))
        value = json.loads(raw)
        record["response"] = value
        models = [row for row in value.get("data", []) if row.get("id") == MODEL]
        if status != 200 or len(models) != 1:
            raise ValueError("Required live served model not confirmed")
        record["context"] = models[0].get("max_model_len")
    except Exception as error:
        record["error_type"] = type(error).__name__
        gate.halt(base_url + ":models_unavailable_or_mismatch")
    write_json(output / "models.json", record)
    return record


def run(requests_path, output):
    rows, input_hash = bench.load_requests(requests_path)
    if input_hash != INPUT_SHA256 or len(rows) != REQUEST_COUNT:
        raise ValueError("Frozen 1024-request input SHA256/count mismatch")
    if CONCURRENCY != 128 or REQUEST_DEADLINE_S != 1800 or SAMPLE_INTERVAL_S != 5:
        raise ValueError("Fixed benchmark limits changed")
    with single_use_guard(output) as root:
        gate = Gate()
        previous_handlers = {}
        # Graceful interruption stops dispatch; in-flight work and drain continue.
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous_handlers[signum] = signal.signal(signum, lambda number, frame: gate.halt("signal:" + str(number)))
        write_json(root / "start.json", {"at": now(), "pid": os.getpid(), "requests_path": str(Path(requests_path).resolve()),
                   "input_sha256": input_hash, "planned_per_endpoint": len(rows), "base_urls": BASE_URLS,
                   "concurrency_per_endpoint": CONCURRENCY, "parameters": PARAMETERS,
                   "request_deadline_s": REQUEST_DEADLINE_S, "sample_interval_s": SAMPLE_INTERVAL_S,
                   "drain_deadline_s": DRAIN_TIMEOUT_S, "runner_sha256": digest(Path(__file__).read_bytes()),
                   "bench_dependency_sha256": digest(BENCH_PATH.read_bytes()),
                   "contract_sha256": digest(CONTRACT_PATH.read_bytes()), **FLAGS})
        samplers, outputs, models, rounds, failures = {}, {}, {}, {}, []
        try:
            for base in BASE_URLS:
                directory = root / urlsplit(base).hostname
                directory.mkdir(mode=0o700)
                (directory / "requests").mkdir(mode=0o700)
                (directory / "telemetry").mkdir(mode=0o700)
                outputs[base] = directory
                write_json(directory / "start.json", {"at": now(), "base_url": base,
                           "concurrency": CONCURRENCY, "input_sha256": input_hash,
                           "planned": len(rows), "parameters": PARAMETERS, **FLAGS})
                samplers[base] = Sampler(base, directory, gate)
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = {pool.submit(read_models, base, outputs[base], gate): base for base in BASE_URLS}
                for future, base in pending.items():
                    models[base] = future.result()
                pending = {pool.submit(samplers[base].take): base for base in BASE_URLS}
                for future, base in pending.items():
                    if not server_idle(future.result()):
                        gate.halt(base + ":not_healthy_idle_before_dispatch")
            write_json(root / "preflight.json", {"at": now(), "allowed_to_dispatch": not gate.stop.is_set(),
                       "models": models, "initial_samples": {base: samplers[base].samples[-1] for base in BASE_URLS},
                       "stop_reasons": list(gate.reasons), **FLAGS})
            for sampler in samplers.values():
                sampler.start()
            with ThreadPoolExecutor(max_workers=2) as pool:
                pending = {pool.submit(run_endpoint, rows, outputs[base], base, gate, samplers[base], models[base]): base
                           for base in BASE_URLS}
                while pending:
                    done, _ = wait(pending, timeout=SAMPLE_INTERVAL_S, return_when=FIRST_COMPLETED)
                    for future in done:
                        base = pending.pop(future)
                        try:
                            rounds[base] = future.result()
                        except Exception as error:
                            gate.halt(base + ":endpoint_runner_failed")
                            failures.append({"base_url": base, "error_type": type(error).__name__})
                    write_json(root / "progress.json", {"at": now(), "finished_endpoints": list(rounds),
                               "pending_endpoints": list(pending.values()), "failures": failures,
                               "stop_reasons": list(gate.reasons), **FLAGS}, replace=True)
            result = {"at": now(), "endpoints": rounds, "failures": failures,
                      "drained": len(rounds) == 2 and all(value["drained"] for value in rounds.values()),
                      "stop_reasons": list(gate.reasons), **FLAGS}
            write_json(root / "round.json", result)
            write_json(root / "exit.json", {"at": now(), "completed": not failures,
                       "drained": result["drained"], "round_sha256": digest(canonical(result)), **FLAGS})
            return result
        except BaseException as error:
            gate.halt("global_runner_failed:" + type(error).__name__)
            if not (root / "exit.json").exists():
                write_json(root / "exit.json", {"at": now(), "completed": False, "drained": False,
                           "error_type": type(error).__name__, "stop_reasons": list(gate.reasons), **FLAGS})
            raise
        finally:
            for sampler in samplers.values():
                sampler.finish()
            for signum, handler in previous_handlers.items():
                signal.signal(signum, handler)


def status(output):
    """Read receipts only. Missing exit never authorizes a restart."""
    root = Path(output)
    result = {"output": str(root.absolute()), "exists": root.is_dir(), **FLAGS}
    def read(path):
        if not path.is_file():
            return None
        try:
            return json.loads(path.read_bytes())
        except (OSError, ValueError) as error:
            return {"receipt_error": type(error).__name__}
    for name in ("intent", "start", "preflight", "progress", "round", "exit"):
        result[name] = read(root / (name + ".json"))
    result["endpoints"] = {urlsplit(base).hostname: {
        name: read(root / urlsplit(base).hostname / (name + ".json"))
        for name in ("models", "progress", "round", "exit")} for base in BASE_URLS}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("run")
    launch.add_argument("--requests", required=True)
    launch.add_argument("--output", required=True)
    inspect = commands.add_parser("status")
    inspect.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run(args.requests, args.output) if args.command == "run" else status(args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    if args.command == "run" and (result["failures"] or result["stop_reasons"] or not result["drained"]):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
