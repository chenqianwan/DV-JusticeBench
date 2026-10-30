"""Single-use, bounded sentence-ID validation retry; never a legal audit.

Two fixed administrator endpoints, 128 frozen inputs each, client limit 128.
No retry, service mutation, resource management, paid API, or formal statistics.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
import fcntl
import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import threading
import time
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent


def dependency(name, fallback):
    path = HERE / (name + ".py")
    if not path.is_file():
        path = HERE.parent / fallback
    spec = importlib.util.spec_from_file_location("_sentence_retry_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.resolve()


ext, EXT_PATH = dependency("bench_external", "admin_ds_benchmark_20261003/bench_external.py")
contract, CONTRACT_PATH = dependency("sentence_contract", "../hallucination_h1_h5_sentence_ids_20261003/contract.py")
canonical, digest, now = ext.canonical, ext.digest, ext.now
write_json, write_raw = ext.write_json, ext.write_raw
BASE_URLS, PARAMETERS = ext.BASE_URLS, ext.PARAMETERS
CONCURRENCY, COUNT, DEADLINE = 128, 128, 1800
PREVIOUS_ROOT = Path("/aifs4su/hansirui_3rd/chenlong/dvjustice_admin_ds_benchmark_20261003_c128")
FLAGS = {**ext.FLAGS, "reference_ready": False, "sentence_anchor_validation_only": True,
         "legal_correctness_verified": False}


def load_inputs(requests_path, manifest_path):
    manifest = json.loads(Path(manifest_path).read_bytes())
    paths = {"runner.py": Path(__file__).resolve(), "bench_external.py": EXT_PATH,
             "bench.py": ext.BENCH_PATH, "contract.py": ext.CONTRACT_PATH,
             "sentence_contract.py": CONTRACT_PATH}
    if set(manifest.get("code_sha256", {})) != set(paths):
        raise ValueError("Exact frozen dependency set required")
    for name, path in paths.items():
        if digest(path.read_bytes()) != manifest["code_sha256"][name]:
            raise ValueError("Frozen code hash mismatch: " + name)
    raw = Path(requests_path).read_bytes()
    if digest(raw) != manifest["input_sha256"] or manifest["request_count"] != COUNT:
        raise ValueError("Frozen input hash/count mismatch")
    if manifest["previous_run_root"] != str(PREVIOUS_ROOT):
        raise ValueError("Previous-run boundary mismatch")
    rows = [json.loads(line) for line in raw.splitlines() if line.strip()]
    if len(rows) != COUNT or len({row["request_id"] for row in rows}) != COUNT:
        raise ValueError("Exactly 128 unique frozen requests required")
    for row in rows:
        if not isinstance(row["request_id"], str) or not row["request_id"]:
            raise ValueError("Nonempty request ID required")
        answer = row["answer"]
        if not isinstance(answer, str) or not answer or digest(answer.encode()) != row["answer_sha256"]:
            raise ValueError("Original answer hash mismatch")
        if contract.build_sentences(answer) != row["sentences"]:
            raise ValueError("Sentence table is not the exact deterministic original mapping")
        messages = row["messages"]
        if not isinstance(messages, list) or len(messages) != 2:
            raise ValueError("Exactly system/user messages required")
        for message, role in zip(messages, ("system", "user")):
            if set(message) != {"role", "content"} or message["role"] != role:
                raise ValueError("Invalid frozen message structure")
            if not isinstance(message["content"], str) or not message["content"].strip():
                raise ValueError("Empty frozen message")
        if digest(messages[1]["content"].encode()) != row["packet_sha256"]:
            raise ValueError("User packet hash mismatch")
        packet = json.loads(messages[1]["content"])
        if not isinstance(packet, dict) or set(packet) != {
                "case_text", "question", "judge_decision", "reference_pack", "sentence_table"}:
            raise ValueError("Exact delivered packet fields required")
        if packet["sentence_table"] != row["sentences"]:
            raise ValueError("Delivered sentence table differs from validation table")
    return rows, manifest


def read_identity(pid, proc_root=Path("/proc")):
    directory = proc_root / str(pid)
    if not directory.exists():
        return None
    raw = (directory / "stat").read_text()
    fields = raw[raw.rfind(")") + 2:].split()
    return {"pid": pid, "state": fields[0], "start_ticks": fields[19]}


@contextmanager
def previous_finished(root=PREVIOUS_ROOT, proc_root=Path("/proc")):
    """Hold the old client lock; require recorded drain and no old executor."""
    root = Path(root)
    if root.resolve() != root:
        raise ValueError("Previous root must not traverse symlinks")
    receipt = json.loads((root / "launch_receipt.json").read_bytes())
    identity = receipt["identity"]
    if not identity or not identity["correct_runner"] or identity["pid"] != receipt["pid"]:
        raise ValueError("Previous launch identity incomplete")
    pid = receipt["pid"]
    if type(pid) is not int or pid <= 0:
        raise ValueError("Invalid previous PID")
    live = read_identity(pid, proc_root)
    if live and live["start_ticks"] == str(identity["start_ticks"]) and live["state"] != "Z":
        raise RuntimeError("Previous client still exists; no overlapping retry")
    paths = [root / "results" / "exit.json"] + [
        root / "results" / urlsplit(base).hostname / "exit.json" for base in BASE_URLS]
    exits = []
    for path in paths:
        value = json.loads(path.read_bytes())
        if value.get("completed") is not True or value.get("drained") is not True:
            raise RuntimeError("Previous run lacks completed/drained exit receipt")
        exits.append({"path": str(path), "sha256": digest(path.read_bytes())})
    descriptor = os.open(root / "code" / "global.lock", os.O_RDWR | getattr(os, "O_NOFOLLOW", 0))
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield {"at": now(), "previous_root": str(root), "previous_pid": pid,
               "recorded_start_ticks": identity["start_ticks"], "live_identity": live,
               "exit_receipts": exits, "old_lock_exclusively_held": True}
    finally:
        os.close(descriptor)


def request_once(row, index, output, base_url, gate):
    with gate.lock:
        if gate.stop.is_set():
            return {"request_id": row["request_id"], "status": "not_started", "sent_intent": False}
        started = time.monotonic()
        directory = output / "requests" / f"{index:06d}_{digest(row['request_id'].encode())[:16]}"
        directory.mkdir(mode=0o700)
        payload = {**PARAMETERS, "messages": row["messages"]}
        body = canonical(payload)
        write_json(directory / "intent.json", {"at": now(), "request_id": row["request_id"],
                   "payload": payload, "payload_sha256": digest(body), "attempt": 1,
                   "answer_sha256": row["answer_sha256"], "packet_sha256": row["packet_sha256"],
                   "sentences_sha256": digest(canonical(row["sentences"])), "timeout_s": DEADLINE, **FLAGS})
    result = {"request_id": row["request_id"], "sent_intent": True, "complete": False,
              "valid_format": False, "output_tokens": 0, "usage_present": False,
              "service_error": False, "timeout": False, "oom": False, **FLAGS}
    validated = {}

    def parse(text, answer):
        result["native_output_kind"] = "NONE" if text.strip() == "NONE" else "JSON"
        result["json_syntax_valid"] = None
        if result["native_output_kind"] == "JSON":
            try:
                json.loads(text)
                result["json_syntax_valid"] = True
            except (TypeError, ValueError):
                result["json_syntax_valid"] = False
        try:
            native = contract.parse_output(text, answer, row["sentences"])
            hydrated = contract.hydrate_output(native, answer, row["sentences"])
            validated.update(native=native, hydrated=hydrated)
            return native
        except (ValueError, TypeError, KeyError) as error:
            result["format_error_reason"] = str(error)[:300]
            raise

    try:
        status, headers, raw = ext.http_once(base_url + "/chat/completions", body=body, timeout_s=DEADLINE)
        write_raw(directory / "response.raw.txt", raw)
        write_json(directory / "response.http.json", {"at": now(), "status": status, "headers": headers,
                   "raw_sha256": digest(raw), "body_complete": True})
        result.update(ext.bench.inspect_response(raw.decode("utf-8", errors="replace"), row["answer"],
                                                status=status, parser=parse))
        if result["valid_format"]:
            write_json(directory / "parsed.native.json", validated["native"])
            write_json(directory / "hydrated.json", validated["hydrated"])
            result["event_count"] = 0 if validated["native"] == "NONE" else len(validated["native"])
            result["anchors_validated"] = None if validated["native"] == "NONE" else True
            result["hydrated_quote_exact"] = None if validated["native"] == "NONE" else True
        if result["service_error"]:
            gate.halt(base_url + ":request_service_error")
    except Exception as error:
        timed_out = isinstance(error, (TimeoutError, socket.timeout)) or (
            isinstance(error, ext.HTTPFailure) and error.timed_out)
        result.update(status="timeout_needs_reconcile" if timed_out else "transport_or_recording_error_needs_reconcile",
                      timeout=timed_out, service_error=not timed_out, error_type=type(error).__name__,
                      valid_format=False, anchors_validated=False, hydrated_quote_exact=False)
        gate.halt(base_url + (":request_timeout" if timed_out else ":request_transport_or_recording_error"))
        if isinstance(error, ext.HTTPFailure):
            write_raw(directory / "response.partial.raw.txt", error.raw)
            write_json(directory / "response.http.json", {"at": now(), "status": error.status, "headers": error.headers,
                       "raw_sha256": digest(error.raw), "body_complete": False})
    result.update(at=now(), elapsed_s=time.monotonic() - started)
    write_json(directory / "terminal.json", result)
    return result


def summarize(results, samples, elapsed):
    value = ext.summarize(results, samples, elapsed)
    value.update(format_error_reasons=dict(Counter(item.get("format_error_reason", "unknown")
                  for item in results if item.get("status") == "complete_invalid_format")),
                 json_syntax_errors=sum(item.get("json_syntax_valid") is False for item in results),
                 json_syntax_valid_but_contract_invalid=sum(item.get("json_syntax_valid") is True
                    and item.get("status") == "complete_invalid_format" for item in results),
                 hydrated_quote_exact=sum(item.get("hydrated_quote_exact") is True for item in results),
                 event_count=sum(item.get("event_count", 0) for item in results),
                 none_count=sum(item.get("format_kind") == "NONE" for item in results), **FLAGS)
    return value


def run_endpoint(rows, output, base_url, gate, sampler, models):
    started = time.monotonic()
    results, worker_errors, scheduled = [], [], 0

    def progress():
        write_json(output / "progress.json", {"at": now(), "base_url": base_url, "planned": len(rows),
                   "scheduled": scheduled, "concurrency": CONCURRENCY, "stop_reasons": list(gate.reasons),
                   **summarize(results, list(sampler.samples), time.monotonic() - started)}, replace=True)

    failure, finished, drained = None, None, False
    try:
        with ext.dispatch_pool(gate) as pool:
            pending, last_progress = {}, 0
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
                if time.monotonic() - last_progress >= ext.SAMPLE_INTERVAL_S:
                    progress()
                    last_progress = time.monotonic()
        finished = time.monotonic()
        progress()
    except BaseException as error:
        gate.halt(base_url + ":endpoint_runner_failed:" + type(error).__name__)
        failure = error
    finally:
        finished = finished or time.monotonic()
        try:
            drained = ext.wait_drained(sampler)
        finally:
            sampler.finish()
    if failure is not None:
        write_json(output / "exit.json", {"at": now(), "completed": False, "drained": drained,
                   "error_type": type(failure).__name__, **FLAGS})
        raise failure
    attempted = {item["request_id"] for item in results if item.get("sent_intent")}
    uncertain = {item["request_id"] for item in worker_errors}
    not_started = [row["request_id"] for row in rows if row["request_id"] not in attempted | uncertain]
    write_json(output / "not_started.json", {"request_ids": not_started, "count": len(not_started), **FLAGS})
    result = {"at": now(), "base_url": base_url, "concurrency": CONCURRENCY, "planned": len(rows),
              "not_started": len(not_started), "worker_exceptions": worker_errors, "models": models,
              "drained": drained, "local_futures_finished": True, "stop_reasons": list(gate.reasons),
              "observations_complete": bool(sampler.samples) and all(s["complete"] for s in sampler.samples)
                                       and drained and not worker_errors,
              **summarize(results, list(sampler.samples), finished - started)}
    write_json(output / "round.json", result)
    write_json(output / "exit.json", {"at": now(), "completed": not worker_errors, "drained": drained,
                                     "round_sha256": digest(canonical(result)), **FLAGS})
    progress()
    return result


def run(requests_path, manifest_path, output):
    rows, manifest = load_inputs(requests_path, manifest_path)
    with previous_finished() as prior, ext.single_use_guard(output, state_dir=HERE) as root:
        gate, handlers = ext.Gate(), {}
        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                handlers[signum] = signal.signal(signum, lambda number, frame: gate.halt("signal:" + str(number)))
        samplers, outputs, models, rounds, failures = {}, {}, {}, {}, []
        try:
            write_json(root / "start.json", {"at": now(), "pid": os.getpid(), "manifest": manifest,
                       "input_sha256": manifest["input_sha256"], "planned_per_endpoint": COUNT,
                       "concurrency_per_endpoint": CONCURRENCY, "parameters": PARAMETERS,
                       "prior_run_verification": prior, **FLAGS})
            for base in BASE_URLS:
                directory = root / urlsplit(base).hostname
                directory.mkdir(mode=0o700)
                for name in ("requests", "telemetry"):
                    (directory / name).mkdir(mode=0o700)
                outputs[base], samplers[base] = directory, ext.Sampler(base, directory, gate)
            with ThreadPoolExecutor(max_workers=2) as pool:
                futures = {base: pool.submit(ext.read_models, base, outputs[base], gate) for base in BASE_URLS}
                for base, future in futures.items():
                    models[base] = future.result()
                futures = {base: pool.submit(samplers[base].take) for base in BASE_URLS}
                for base, future in futures.items():
                    if not ext.server_idle(future.result()):
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
                    done, _ = wait(pending, timeout=ext.SAMPLE_INTERVAL_S, return_when=FIRST_COMPLETED)
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
            write_json(root / "exit.json", {"at": now(), "completed": not failures and not any(
                       item["worker_exceptions"] for item in rounds.values()), "drained": result["drained"],
                       "round_sha256": digest(canonical(result)), **FLAGS})
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
            for signum, handler in handlers.items():
                signal.signal(signum, handler)


def status(output):
    value = ext.status(output)
    preflight = value.get("preflight")
    if preflight:
        value["preflight"] = {key: preflight.get(key) for key in ("at", "allowed_to_dispatch", "stop_reasons")}
    value.update(FLAGS)
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("run")
    launch.add_argument("--requests", required=True)
    launch.add_argument("--manifest", required=True)
    launch.add_argument("--output", required=True)
    inspect = commands.add_parser("status")
    inspect.add_argument("--output", required=True)
    args = parser.parse_args()
    result = run(args.requests, args.manifest, args.output) if args.command == "run" else status(args.output)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 2 if args.command == "run" and (result["failures"] or result["stop_reasons"] or not result["drained"]) else 0


if __name__ == "__main__":
    raise SystemExit(main())
