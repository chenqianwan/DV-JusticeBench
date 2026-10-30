"""Finite, resumeless H1-H5 performance rounds, using Python's standard library.

These are engineering diagnostics, not formal hallucination classifications.
The caller owns/validates the allocation and server identity/capacity. This file
never starts/stops a server, changes its settings, or retries a request. A round
is safe to hand back to a server-changing controller only when ``drained`` is
true (local futures finished and two subsequent healthy server-idle samples).
"""
from __future__ import annotations

from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime, timezone
import hashlib
import http.client
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import socket
import ssl
import subprocess
import threading
import time
from urllib.parse import urlsplit


MODEL = "deepseek_v4_flash"
MODEL_REVISION = "60d8d70770c6776ff598c94bb586a859a38244f1"
PARAMETERS = {
    "model": MODEL, "temperature": 0.1, "max_tokens": 3000,
    "chat_template_kwargs": {"thinking": False},
}
MIN_REQUESTS = 1024
DRAIN_TIMEOUT_S = 1800
REQUIRED_METRICS = ("running", "waiting", "kv_cache_usage", "preemptions_total", "generation_tokens_total")


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(value).hexdigest()


def now():
    return datetime.now(timezone.utc).isoformat()


def _contract_module():
    # The deployment stages the unchanged contract alongside this file. The
    # fallback is solely for offline tests in the source checkout.
    path = Path(__file__).with_name("contract.py")
    if not path.is_file():
        path = Path(__file__).resolve().parents[2] / "hallucination_h1_h5_20261002" / "contract.py"
    spec = importlib.util.spec_from_file_location("_autotune_h1_h5_contract", path)
    if spec is None or spec.loader is None:
        raise ValueError("H1-H5 contract unavailable")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def _load_json(text):
    def invalid(value):
        raise ValueError("Invalid JSON constant: " + value)
    return json.loads(text, object_pairs_hook=_unique_keys, parse_constant=invalid)


def load_requests(path):
    raw = Path(path).read_bytes()
    rows, ids = [], set()
    for line in raw.decode("utf-8").splitlines():
        if not line.strip():
            raise ValueError("Blank request row")
        row = _load_json(line)
        if not isinstance(row, dict) or not {"request_id", "messages", "answer"}.issubset(row):
            raise ValueError("Missing request fields")
        if set(row) - {"request_id", "messages", "answer", "packet_sha256"}:
            raise ValueError("Unexpected request fields")
        request_id = row["request_id"]
        if not isinstance(request_id, str) or not request_id or request_id in ids:
            raise ValueError("Missing or duplicate request ID")
        ids.add(request_id)
        if not isinstance(row["answer"], str) or not row["answer"].strip():
            raise ValueError("Missing original answer")
        messages = row["messages"]
        if not isinstance(messages, list) or len(messages) != 2:
            raise ValueError("Frozen system/user messages required")
        for message, role in zip(messages, ("system", "user")):
            if not isinstance(message, dict) or set(message) != {"role", "content"} or message["role"] != role:
                raise ValueError("Invalid message structure")
            if not isinstance(message["content"], str) or not message["content"].strip():
                raise ValueError("Missing prompt content")
        # Packet hashes, when supplied, are the canonical packet hash used by
        # contract.py, not a hash of an arbitrarily formatted JSON string.
        if "packet_sha256" in row:
            packet = _load_json(messages[1]["content"])
            if not isinstance(packet, dict) or packet.get("answer") != row["answer"]:
                raise ValueError("Packet/original answer mismatch")
            if row["packet_sha256"] != digest(canonical(packet)):
                raise ValueError("Frozen packet hash mismatch")
        rows.append(row)
    if len(rows) < MIN_REQUESTS:
        raise ValueError(f"At least {MIN_REQUESTS} identical frozen requests required for every round")
    return rows, digest(raw)


def science_hash(input_sha256, contract_sha256):
    return digest(canonical({
        "parameters": PARAMETERS, "model_revision": MODEL_REVISION,
        "input_sha256": input_sha256, "contract_sha256": contract_sha256,
        "retries": 0, "length_retry": False, "engineering_only": True,
    }))


def parse_metrics(text):
    series = {}
    for line in text.splitlines():
        match = re.match(r'^([\w:]+)(?:\{.*\})?\s+([^\s]+)(?:\s|$)', line)
        if match:
            try:
                value = float(match.group(2))
            except ValueError:
                continue
            if math.isfinite(value) and value >= 0:
                series.setdefault(match.group(1), []).append(value)
    def get(names, maximum=False):
        for name in names:
            if name in series:
                return max(series[name]) if maximum else sum(series[name])
        return None
    return {
        "running": get(("vllm:num_requests_running",)),
        "waiting": get(("vllm:num_requests_waiting",)),
        "kv_cache_usage": get(("vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc"), True),
        "preemptions_total": get(("vllm:num_preemptions_total",)),
        "generation_tokens_total": get(("vllm:generation_tokens_total",)),
    }


def parse_gpu(text):
    result = []
    fields = ("index", "name", "utilization_pct", "memory_used_mib", "memory_total_mib", "power_w", "temperature_c")
    for line in text.strip().splitlines():
        parts = [item.strip() for item in line.split(",")]
        if len(parts) != len(fields):
            raise ValueError("Malformed GPU observation")
        item = dict(zip(fields, parts))
        item["index"] = int(item["index"])
        for field in fields[2:]:
            item[field] = float(item[field])
            if not math.isfinite(item[field]) or item[field] < 0:
                raise ValueError("Missing/nonfinite GPU observation")
        if item["utilization_pct"] > 100 or item["memory_total_mib"] <= 0:
            raise ValueError("Invalid GPU utilization/memory observation")
        result.append(item)
    if len(result) != 8 or len({row["index"] for row in result}) != 8:
        raise ValueError("Exactly eight distinct GPU observations required")
    return result


def inspect_response(raw, answer, *, status=200, parser=None):
    """Structural response inspection; never certify legal truth or NONE."""
    result = {
        "http_status": status, "complete": False, "valid_format": False,
        "output_tokens": 0, "usage_present": False, "service_error": False,
        "oom": bool(not 200 <= status < 300 and re.search(r"out[ _-]of[ _-]memory|CUDA_ERROR_OUT_OF_MEMORY", raw, re.I)),
        "formal_hallucination_statistics_allowed": False,
    }
    if not 200 <= status < 300:
        result.update(status="http_error", service_error=True)
        return result
    try:
        response = _load_json(raw)
        if not isinstance(response, dict):
            raise ValueError("Response is not an object")
        if response.get("error"):
            result["oom"] = bool(re.search(r"out[ _-]of[ _-]memory|CUDA_ERROR_OUT_OF_MEMORY", canonical(response["error"]).decode(), re.I))
            raise ValueError("Error envelope in success response")
        usage = response.get("usage") or {}
        tokens = usage.get("completion_tokens")
        if type(tokens) is int and tokens >= 0:
            result.update(output_tokens=tokens, usage_present=True)
        result["response_id"] = response.get("id")
        result["response_model"] = response.get("model")
        choices = response["choices"]
        if not isinstance(choices, list) or len(choices) != 1:
            raise ValueError("Exactly one response choice required")
        choice = choices[0]
        reason = choice["finish_reason"]
        content = choice["message"].get("content")
        result["finish_reason"] = reason
        if response.get("model") != MODEL:
            raise ValueError("Unexpected response model identity")
        if choice["message"].get("refusal"):
            result["status"] = "refusal"
            return result
        if reason != "stop":
            result["status"] = "truncated" if reason == "length" else "non_stop"
            return result
        if not isinstance(content, str) or not content.strip():
            raise ValueError("Empty/nontext response content")
        result.update(complete=True, status="complete_invalid_format")
        try:
            if parser is None:
                parser = _contract_module()[0].parse_output
            parsed = parser(content, answer)
            result.update(valid_format=True, status="complete", format_kind="NONE" if parsed == "NONE" else "events")
        except (ValueError, TypeError, KeyError) as error:
            result["format_error_type"] = type(error).__name__
    except (ValueError, TypeError, KeyError, IndexError) as error:
        result.update(status="protocol_error", service_error=True, protocol_error_type=type(error).__name__)
    return result


def _write_json(path, value, *, replace=False):
    path = Path(path)
    temp = path.with_name(path.name + ".tmp")
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "wb") as handle:
        handle.write(canonical(value) + b"\n")
        handle.flush()
        os.fsync(handle.fileno())
    if path.exists() and not replace:
        raise FileExistsError(str(path))
    os.replace(temp, path)


def _write_raw(path, text):
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())


def _http(url, api_key, *, body=None, timeout_s=3):
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.fragment:
        raise ValueError("Invalid service URL")
    deadline = time.monotonic() + timeout_s
    cls = http.client.HTTPSConnection if parts.scheme == "https" else http.client.HTTPConnection
    kwargs = {"timeout": timeout_s}
    if parts.scheme == "https":
        kwargs["context"] = ssl.create_default_context()
    connection = cls(parts.hostname, parts.port, **kwargs)
    try:
        connection.connect()
        transport_socket = connection.sock
        def remaining():
            value = deadline - time.monotonic()
            if value <= 0:
                raise TimeoutError("HTTP deadline exceeded")
            transport_socket.settimeout(value)
        remaining()
        path = parts.path or "/"
        if parts.query:
            path += "?" + parts.query
        headers = {"Authorization": "Bearer " + api_key, "Content-Type": "application/json"}
        connection.request("POST" if body is not None else "GET", path, body=body, headers=headers)
        remaining()
        response = connection.getresponse()
        chunks = []
        while True:
            remaining()
            chunk = response.read1(65536)
            if not chunk:
                break
            chunks.append(chunk)
        allowed_headers = {key.lower(): value.replace(api_key, "[REDACTED]")
                           for key, value in response.getheaders()
                           if key.lower() in ("request-id", "x-request-id", "retry-after")}
        raw = b"".join(chunks).decode("utf-8", errors="replace").replace(api_key, "[REDACTED]")
        return response.status, allowed_headers, raw
    finally:
        connection.close()


class Sampler:
    def __init__(self, *, root_url, api_key, output, stop_new, interval):
        self.root_url, self.api_key, self.output = root_url, api_key, output
        self.stop_new, self.interval = stop_new, interval
        self.finished = threading.Event()
        self.condition = threading.Condition()
        self.samples, self.stop_reasons = [], []
        self.thread = None
        self.last_preemptions = None
        self.last_generation_tokens = None

    def halt(self, reason):
        with self.condition:
            if reason not in self.stop_reasons:
                self.stop_reasons.append(reason)
            self.stop_new.set()
            self.condition.notify_all()

    def take(self):
        sample = {"at": now(), "monotonic": time.monotonic(), "healthy": False, "gpus": [], "metrics": {}}
        try:
            status, _, _ = _http(self.root_url + "/health", self.api_key)
            sample.update(health_status=status, healthy=status == 200)
        except Exception as error:
            sample["health_error"] = type(error).__name__
        try:
            status, _, raw = _http(self.root_url + "/metrics", self.api_key)
            sample.update(metrics_status=status, metrics_raw=raw)
            if status == 200:
                sample["metrics"] = parse_metrics(raw)
        except Exception as error:
            sample["metrics_error"] = type(error).__name__
        try:
            process = subprocess.run([
                "nvidia-smi", "--query-gpu=index,name,utilization.gpu,memory.used,memory.total,power.draw,temperature.gpu",
                "--format=csv,noheader,nounits",
            ], capture_output=True, text=True, timeout=5)
            sample["gpu_returncode"] = process.returncode
            if process.returncode:
                raise ValueError("nvidia_smi_failed")
            sample["gpus"] = parse_gpu(process.stdout)
        except Exception as error:
            sample["gpu_error"] = type(error).__name__
        metrics = sample["metrics"]
        sample["complete"] = (
            # A measured 503 is negative evidence, not missing evidence. This
            # permits policy's lower-concurrency fallback after proven drain.
            type(sample.get("health_status")) is int and sample.get("metrics_status") == 200 and
            all(metrics.get(key) is not None for key in REQUIRED_METRICS) and len(sample["gpus"]) == 8
        )
        if not sample["healthy"]:
            self.halt("health_failed")
        if not sample["complete"]:
            self.halt("telemetry_incomplete")
        if any(gpu["temperature_c"] >= 90 for gpu in sample["gpus"]):
            self.halt("gpu_temperature_ge_90c")
        if metrics.get("kv_cache_usage", 0) is not None and metrics.get("kv_cache_usage", 0) >= .85:
            self.halt("kv_cache_ge_85_percent")
        preemptions = metrics.get("preemptions_total")
        if preemptions is not None:
            if self.last_preemptions is not None:
                if preemptions > self.last_preemptions:
                    self.halt("new_preemptions")
                if preemptions < self.last_preemptions:
                    sample["complete"] = False
                    self.halt("server_counter_reset")
            self.last_preemptions = preemptions
        generation_tokens = metrics.get("generation_tokens_total")
        if generation_tokens is not None:
            if self.last_generation_tokens is not None and generation_tokens < self.last_generation_tokens:
                sample["complete"] = False
                self.halt("server_counter_reset")
            self.last_generation_tokens = generation_tokens
        with self.condition:
            sample["sequence"] = len(self.samples)
            self.samples.append(sample)
            _write_json(self.output / "telemetry" / f"{sample['sequence']:06d}.json", sample)
            self.condition.notify_all()
        return sample

    def start(self):
        def loop():
            next_at = time.monotonic() + self.interval
            while not self.finished.wait(max(0, next_at - time.monotonic())):
                try:
                    self.take()
                except Exception as error:
                    self.halt("sampler_failed_" + type(error).__name__)
                    return
                next_at = max(next_at + self.interval, time.monotonic())
        self.thread = threading.Thread(target=loop, name="autotune-telemetry", daemon=True)
        self.thread.start()

    def finish(self):
        self.finished.set()
        if self.thread:
            self.thread.join(timeout=20)
        if self.thread and self.thread.is_alive():
            self.halt("sampler_did_not_stop")


def _request_once(row, index, output, base_url, api_key, timeout_s, parser, sampler):
    started = time.monotonic()
    request_dir = output / "requests" / f"{index:06d}_{digest(row['request_id'].encode())[:16]}"
    request_dir.mkdir(mode=0o700)
    payload = {**PARAMETERS, "messages": row["messages"]}
    body = canonical(payload)
    _write_json(request_dir / "intent.json", {
        "request_id": row["request_id"], "at": now(), "payload": payload,
        "payload_sha256": digest(body), "timeout_s": timeout_s, "attempt": 1,
        "benchmark_only": True, "automatic_retry_allowed": False,
    })
    result = {"request_id": row["request_id"], "sent_intent": True, "complete": False,
              "valid_format": False, "output_tokens": 0, "usage_present": False,
              "service_error": False, "oom": False, "timeout": False}
    try:
        status, headers, raw = _http(base_url + "/chat/completions", api_key, body=body, timeout_s=timeout_s)
        _write_raw(request_dir / "response.raw.txt", raw)
        _write_json(request_dir / "response.http.json", {"at": now(), "status": status, "headers": headers, "raw_sha256": digest(raw.encode())})
        result.update(inspect_response(raw, row["answer"], status=status, parser=parser))
        if result["oom"]:
            sampler.halt("request_oom")
        if result["service_error"]:
            sampler.halt("request_service_error")
    except (TimeoutError, socket.timeout):
        result.update(status="timeout_needs_reconcile", timeout=True)
        sampler.halt("request_timeout")
    except Exception as error:
        # A connection error cannot prove the server did not receive the body.
        result.update(status="transport_or_recording_error_needs_reconcile", service_error=True,
                      error_type=type(error).__name__)
        sampler.halt("request_transport_or_recording_error")
    result.update(at=now(), elapsed_s=time.monotonic() - started, benchmark_only=True,
                  formal_hallucination_statistics_allowed=False)
    _write_json(request_dir / "terminal.json", result)
    return result


def _server_idle(sample):
    metrics = sample.get("metrics") or {}
    return sample.get("healthy") is True and sample.get("metrics_status") == 200 and metrics.get("running") == 0 and metrics.get("waiting") == 0


def wait_drained(sampler, *, timeout_s=DRAIN_TIMEOUT_S):
    """Call only AFTER all local futures have finished; never cancels anything."""
    after = time.monotonic()
    deadline = after + timeout_s
    idle_count = 0
    with sampler.condition:
        seen = len(sampler.samples)
        while time.monotonic() < deadline:
            for sample in sampler.samples[seen:]:
                seen += 1
                if sample["monotonic"] < after:
                    continue
                idle_count = idle_count + 1 if _server_idle(sample) else 0
                if idle_count >= 2:
                    return True
            if sampler.thread and not sampler.thread.is_alive():
                return False
            sampler.condition.wait(timeout=min(sampler.interval + 1, max(0, deadline - time.monotonic())))
    return False


def run_round(*, concurrency, requests_path, output_dir, base_url, api_key_file, timeout_s=1800, sample_interval=5):
    """Run one immutable-input round and return the policy observation schema."""
    if type(concurrency) is not int or concurrency not in (32, 64, 128, 192, 256, 384, 512):
        raise ValueError("Unsupported concurrency")
    if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not 0 < timeout_s <= 1800:
        raise ValueError("Request timeout must be in (0, 1800]")
    if isinstance(sample_interval, bool) or not isinstance(sample_interval, (int, float)) or not 0 < sample_interval <= 5:
        raise ValueError("Telemetry interval must be in (0, 5]")
    parts = urlsplit(base_url)
    if parts.scheme not in ("http", "https") or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError("Invalid API base URL")
    if parts.path.rstrip("/") != "/v1":
        raise ValueError("API base URL must end in /v1")
    base_url = base_url.rstrip("/")
    rows, input_hash = load_requests(requests_path)
    contract, contract_path = _contract_module()
    contract_hash = digest(contract_path.read_bytes())
    scientific_hash = science_hash(input_hash, contract_hash)
    key_path = Path(api_key_file).absolute()
    if key_path.resolve() != key_path or not key_path.is_file() or key_path.stat().st_uid != os.getuid() or key_path.stat().st_mode & 0o077:
        raise ValueError("API key file must be owned, private, and not symlinked")
    api_key = key_path.read_text().strip()
    if len(api_key) < 32 or any(char.isspace() for char in api_key):
        raise ValueError("Invalid API key file")
    output = Path(output_dir).absolute()
    if output.resolve() != output:
        raise ValueError("Output path must not traverse symlinks")
    output.mkdir(mode=0o700, parents=False, exist_ok=False)
    (output / "requests").mkdir(mode=0o700)
    (output / "telemetry").mkdir(mode=0o700)
    started = time.monotonic()
    _write_json(output / "start.json", {
        "at": now(), "pid": os.getpid(), "concurrency": concurrency,
        "input_sha256": input_hash, "science_sha256": scientific_hash,
        "contract_sha256": contract_hash, "runner_sha256": digest(Path(__file__).read_bytes()),
        "parameters": PARAMETERS, "model_revision": MODEL_REVISION, "planned": len(rows),
        "timeout_s": timeout_s, "sample_interval": sample_interval,
        "benchmark_only": True, "formal_hallucination_statistics_allowed": False,
    })
    stop_new = threading.Event()
    sampler = Sampler(root_url=base_url[:-3], api_key=api_key, output=output, stop_new=stop_new, interval=sample_interval)
    initial = sampler.take()
    if not _server_idle(initial):
        sampler.halt("server_not_idle_before_round")
    sampler.start()
    results = []
    sent = 0
    worker_exceptions = []
    try:
        with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="h1h5-benchmark") as pool:
            pending = {}
            while pending or (sent < len(rows) and not stop_new.is_set()):
                while len(pending) < concurrency and sent < len(rows) and not stop_new.is_set():
                    index = sent
                    pending[pool.submit(_request_once, rows[index], index, output, base_url, api_key,
                                        timeout_s, contract.parse_output, sampler)] = index
                    sent += 1
                if not pending:
                    break
                done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    index = pending.pop(future)
                    try:
                        results.append(future.result())
                    except Exception as error:
                        item = {"request_id": rows[index]["request_id"], "error_type": type(error).__name__, "at": now(),
                                "status": "worker_error_needs_reconcile", "automatic_retry_allowed": False}
                        worker_exceptions.append(item)
                        _write_json(output / f"worker_exception_{index:06d}.json", item)
                        sampler.halt("worker_exception")
        requests_finished = time.monotonic()
        drained = wait_drained(sampler)
    finally:
        sampler.finish()
    elapsed = time.monotonic() - started
    if sent < len(rows):
        _write_json(output / "not_started.json", {"request_ids": [row["request_id"] for row in rows[sent:]], "count": len(rows) - sent})
    samples = list(sampler.samples)
    metrics = [sample["metrics"] for sample in samples]
    preempt = [item["preemptions_total"] for item in metrics if item.get("preemptions_total") is not None]
    kv = [item["kv_cache_usage"] for item in metrics if item.get("kv_cache_usage") is not None]
    gpus = [gpu for sample in samples for gpu in sample["gpus"]]
    # Missing observations, a counter reset, hot GPUs, client exceptions, or an
    # undrained server never become an automatically selectable stable round.
    observations_complete = (
        len(samples) >= 3 and all(sample["complete"] for sample in samples)
        and drained and not worker_exceptions
        and "gpu_temperature_ge_90c" not in sampler.stop_reasons
        and not any(reason.startswith("sampler_") for reason in sampler.stop_reasons)
        and all(result["usage_present"] for result in results if result.get("http_status") == 200)
    )
    result = {
        "concurrency": concurrency, "input_sha256": input_hash, "science_sha256": scientific_hash,
        "elapsed_s": elapsed, "attempted": sent, "planned": len(rows), "not_started": len(rows) - sent,
        "complete": sum(item["complete"] for item in results),
        "valid_format": sum(item["valid_format"] for item in results),
        "output_tokens": sum(item["output_tokens"] for item in results),
        "timeouts": sum(item.get("timeout", False) for item in results),
        "ooms": sum(item["oom"] for item in results),
        "service_errors": sum(item["service_error"] for item in results) + len(worker_exceptions) + sum(not sample["healthy"] for sample in samples),
        "preemptions_delta": int(max(0, max(preempt) - preempt[0])) if preempt else 0,
        "peak_kv_cache": max(kv) if kv else None,
        "observations_complete": observations_complete,
        "drained": drained, "local_futures_finished": True, "server_idle_consecutive_samples": 2 if drained else 0,
        "requests_elapsed_s": requests_finished - started, "drain_elapsed_s": time.monotonic() - requests_finished,
        "telemetry_samples": len(samples), "stop_reasons": sampler.stop_reasons,
        "gpu_peak_utilization_pct": max((gpu["utilization_pct"] for gpu in gpus), default=None),
        "gpu_peak_memory_used_mib": max((gpu["memory_used_mib"] for gpu in gpus), default=None),
        "gpu_peak_temperature_c": max((gpu["temperature_c"] for gpu in gpus), default=None),
        "peak_server_running": max((item["running"] for item in metrics if item.get("running") is not None), default=None),
        "peak_server_waiting": max((item["waiting"] for item in metrics if item.get("waiting") is not None), default=None),
        "benchmark_only": True, "formal_hallucination_statistics_allowed": False, "at": now(),
    }
    _write_json(output / "round.json", result)
    _write_json(output / "exit.json", {"at": now(), "pid": os.getpid(), "completed": True, "drained": drained,
                                       "round_sha256": digest(canonical(result)), "automatic_retry_allowed": False})
    return result
