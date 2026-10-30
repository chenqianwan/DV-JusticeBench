"""Resumable, disjoint full-corpus H1–H5 candidate screening.

Only untouched inputs are eligible after a drained, audited session. Sent or
ambiguous requests are never replayed by this runner. No legal truth claims,
service administration, paid API, scientific-parameter changes, or retries.
"""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from contextlib import contextmanager
import fcntl
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import threading
import time
from urllib.parse import urlsplit

HERE = Path(__file__).resolve().parent


def dependency(name, fallback):
    path = HERE / (name + ".py")
    if not path.is_file():
        path = HERE.parent / fallback
    spec = importlib.util.spec_from_file_location("_full_h1h5_" + name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, path.resolve()


pilot, PILOT_PATH = dependency("pilot_runner", "admin_ds_sentence_retry_20261003/runner.py")
ext, contract = pilot.ext, pilot.contract
canonical, digest, now = ext.canonical, ext.digest, ext.now
write_json = ext.write_json
BASE_URLS, PARAMETERS = ext.BASE_URLS, ext.PARAMETERS
HOSTS = tuple(urlsplit(base).hostname for base in BASE_URLS)
TOTAL, CONCURRENCY = 55236, 128
FLAGS = {**pilot.FLAGS, "benchmark_only": False, "provisional": True,
         "reference_ready": False, "ref_ready": False,
         "sentence_anchor_validation_only": False,
         "formal_hallucination_statistics_allowed": False,
         "legal_correctness_verified": False, "none_is_verified_negative": False}
# Private imported modules, not edits to any frozen source or prompt. Their HTTP
# and parsing implementations are identical; only output provenance changes.
pilot.FLAGS = FLAGS
ext.FLAGS = FLAGS


def file_sha(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(block)
    return hasher.hexdigest()


def safe_child(root, relative):
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Relative frozen file path required")
    path = root / relative
    if path.resolve() != path or not path.is_relative_to(root) or ".." in Path(relative).parts:
        raise ValueError("Frozen path must not escape or traverse symlinks")
    return path


def read_rows(path):
    with Path(path).open("rb") as stream:
        for line_number, raw in enumerate(stream, 1):
            if not raw.strip():
                raise ValueError("Blank frozen row at line " + str(line_number))
            row = json.loads(raw)
            if not isinstance(row, dict):
                raise ValueError("Frozen row must be an object")
            yield row


def code_paths():
    return {"runner.py": Path(__file__).resolve(), "pilot_runner.py": PILOT_PATH,
            "bench_external.py": pilot.EXT_PATH, "bench.py": ext.BENCH_PATH,
            "contract.py": ext.CONTRACT_PATH, "sentence_contract.py": pilot.CONTRACT_PATH}


def verify_manifest(root):
    """Stream and authenticate every shard; retain compact identity metadata only."""
    root = Path(root).absolute()
    if root.resolve() != root:
        raise ValueError("Root must be a canonical absolute path")
    prepared = root / "prepared"
    manifest_path = prepared / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    paths = code_paths()
    if set(manifest.get("code_sha256", {})) != set(paths):
        raise ValueError("Exact six frozen code dependencies required")
    for name, path in paths.items():
        if file_sha(path) != manifest["code_sha256"][name]:
            raise ValueError("Frozen code hash mismatch: " + name)
    if manifest.get("request_count") != TOTAL or set(manifest.get("shards", {})) != set(HOSTS):
        raise ValueError("Exactly 55,236 inputs on the two fixed endpoints required")
    identities, indexes = {}, set()
    for host in HOSTS:
        shard = manifest["shards"][host]
        path = safe_child(prepared, shard["path"])
        if shard["count"] != TOTAL // 2 or file_sha(path) != shard["sha256"]:
            raise ValueError("Frozen shard hash/count mismatch")
        count = 0
        for row in read_rows(path):
            rid, index = row.get("request_id"), row.get("global_index")
            if not isinstance(rid, str) or not rid or type(index) is not int or not 0 <= index < TOTAL:
                raise ValueError("Invalid stable input identity")
            if rid in identities or index in indexes:
                raise ValueError("Duplicate request ID/index across disjoint shards")
            for key in ("answer_sha256", "packet_sha256"):
                if not re.fullmatch(r"[0-9a-f]{64}", row.get(key, "")):
                    raise ValueError("Frozen answer/packet digest required")
            identities[rid] = {"host": host, "global_index": index,
                               "answer_sha256": row["answer_sha256"], "packet_sha256": row["packet_sha256"]}
            indexes.add(index)
            count += 1
        if count != shard["count"]:
            raise ValueError("Actual frozen shard count mismatch")
    if len(identities) != TOTAL or indexes != set(range(TOTAL)):
        raise ValueError("Full identity coverage required")
    reused = manifest.get("reused")
    if not isinstance(reused, dict):
        raise ValueError("Explicit frozen reuse manifest required")
    path = safe_child(prepared, reused["path"])
    if file_sha(path) != reused["sha256"]:
        raise ValueError("Frozen reuse hash mismatch")
    reuse, count = {}, 0
    for row in read_rows(path):
        rid = row["request_id"]
        if rid in reuse or rid not in identities:
            raise ValueError("Unknown or duplicated reused input")
        expected = identities[rid]
        if any(row.get(key) != expected[key] for key in expected):
            raise ValueError("Reused input differs from its assigned frozen input")
        terminal = row.get("terminal")
        if not isinstance(terminal, dict) or terminal.get("request_id") != rid or terminal.get("sent_intent") is not True:
            raise ValueError("Reused terminal lacks exact sent identity")
        if not isinstance(terminal.get("status"), str):
            raise ValueError("Reused status is missing")
        reuse[rid] = {**expected, "terminal": terminal, "source": "frozen_pilot_reuse"}
        count += 1
    if count != reused["count"]:
        raise ValueError("Actual reuse count mismatch")
    return manifest, identities, reuse


def validate_row(row):
    answer = row["answer"]
    if not isinstance(answer, str) or not answer or digest(answer.encode()) != row["answer_sha256"]:
        raise ValueError("Original answer hash mismatch")
    contract.validate_sentences(answer, row["sentences"])
    messages = row["messages"]
    if not isinstance(messages, list) or len(messages) != 2:
        raise ValueError("Exactly system/user messages required")
    for message, role in zip(messages, ("system", "user")):
        if set(message) != {"role", "content"} or message["role"] != role:
            raise ValueError("Invalid frozen message structure")
        if not isinstance(message["content"], str) or not message["content"].strip():
            raise ValueError("Empty frozen message")
    if digest(messages[1]["content"].encode()) != row["packet_sha256"]:
        raise ValueError("Delivered packet hash mismatch")
    packet = json.loads(messages[1]["content"])
    if not isinstance(packet, dict) or set(packet) != {
            "case_text", "question", "judge_decision", "reference_pack", "sentence_table"}:
        raise ValueError("Exact delivered packet fields required")
    if packet["sentence_table"] != row["sentences"]:
        raise ValueError("Delivered sentence table differs from validation table")


def is_live(identity):
    if not isinstance(identity, dict) or type(identity.get("pid")) is not int or not identity.get("start_ticks"):
        raise ValueError("Recorded true PID/start identity required")
    current = pilot.read_identity(identity["pid"])
    return bool(current and current["start_ticks"] == str(identity["start_ticks"]) and current["state"] != "Z")


def prior_sessions(root):
    sessions = root / "sessions"
    if not sessions.exists():
        return []
    values = []
    for directory in sorted(sessions.iterdir()):
        if not directory.is_dir() or directory.resolve() != directory:
            raise RuntimeError("Unexpected session path")
        start = json.loads((directory / "start.json").read_bytes())
        if is_live(start["identity"]):
            raise RuntimeError("Prior client still exists")
        receipt = json.loads((directory / "exit.json").read_bytes())
        if receipt.get("local_futures_finished") is not True or receipt.get("drained") is not True:
            raise RuntimeError("Prior client lacks real finished/drained receipt")
        values.append(directory)
    return values


@contextmanager
def coordinator(root, timeout_s=10):
    root = Path(root).absolute()
    if root.resolve() != root or not root.is_dir():
        raise ValueError("Existing canonical root required")
    fd = os.open(root / "coordinator.lock", os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        deadline = time.monotonic() + timeout_s
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise RuntimeError("Coordinator is still owned by another client")
                time.sleep(min(.05, max(0, deadline - time.monotonic())))
        yield
    finally:
        os.close(fd)


def scan_history(root, identities, reused, directories):
    """Every extant attempt directory is excluded, even a pre-intent crash."""
    seen = dict(reused)
    by_index = {value["global_index"]: rid for rid, value in identities.items()}
    for session in directories:
        for host in HOSTS:
            requests = session / host / "requests"
            if not requests.exists():
                continue
            for directory in requests.iterdir():
                match = re.fullmatch(r"(\d{6})_([0-9a-f]{16})", directory.name)
                if not match or not directory.is_dir() or directory.resolve() != directory:
                    raise RuntimeError("Unexpected request evidence path")
                index = int(match[1])
                rid = by_index.get(index)
                if rid is None or identities[rid]["host"] != host or digest(rid.encode())[:16] != match[2]:
                    raise RuntimeError("Evidence does not belong to frozen shard")
                if rid in seen:
                    raise RuntimeError("Duplicate attempt/reuse evidence; reconciliation required")
                intent_path = directory / "intent.json"
                if intent_path.exists():
                    intent = json.loads(intent_path.read_bytes())
                    if (intent.get("request_id") != rid or
                            any(intent.get(key) != identities[rid][key] for key in ("answer_sha256", "packet_sha256")) or
                            digest(canonical(intent.get("payload"))) != intent.get("payload_sha256")):
                        raise RuntimeError("Intent identity/hash binding mismatch")
                terminal_path = directory / "terminal.json"
                if terminal_path.exists():
                    terminal = json.loads(terminal_path.read_bytes())
                    if terminal.get("request_id") != rid or terminal.get("sent_intent") is not True:
                        raise RuntimeError("Terminal identity/intent mismatch")
                    if not intent_path.is_file():
                        raise RuntimeError("Terminal exists without preserved intent")
                else:
                    terminal = {"request_id": rid, "status": "missing_terminal_needs_reconcile",
                                "sent_intent": None, **FLAGS}
                seen[rid] = {**identities[rid], "terminal": terminal, "source": str(directory)}
    return seen


def count_records(records):
    values = [record["terminal"] for record in records.values()]
    counts = Counter(row.get("status", "unknown") for row in values)
    return {"covered": len(values), "statuses": dict(counts),
            "valid_format": sum(row.get("valid_format") is True for row in values),
            "none_candidates": sum(row.get("format_kind") == "NONE" for row in values),
            "unknown": sum("reconcile" in row.get("status", "") for row in values),
            "event_count": sum(row.get("event_count", 0) for row in values), **FLAGS}


class CompactSampler(ext.Sampler):
    """Full telemetry remains append-only on disk; bounded-size records in RAM."""
    def take(self):
        sample = super().take()
        with self.condition:
            for key in list(sample):
                if key.endswith("_raw") or key.endswith("_headers"):
                    del sample[key]
        return sample


def available_rows(path, seen):
    for row in read_rows(path):
        if row["request_id"] not in seen:
            validate_row(row)
            yield row


def run_endpoint(root, directory, host, manifest, seen, gate):
    base = BASE_URLS[HOSTS.index(host)]
    for name in ("requests", "telemetry"):
        (directory / name).mkdir(mode=0o700)
    sampler = CompactSampler(base, directory, gate)
    models, scheduled, finished, failure = None, 0, 0, None
    results, worker_errors = {}, []
    exhausted, drained, local_finished = False, False, False
    started = time.monotonic()
    baseline = {rid: value for rid, value in seen.items() if value["host"] == host}
    total = manifest["shards"][host]["count"]
    iterator = available_rows(safe_child(root / "prepared", manifest["shards"][host]["path"]), seen)

    def progress():
        current = count_records(results)
        merged = count_records({**baseline, **results})
        sample = sampler.samples[-1] if sampler.samples else None
        value = {"at": now(), "host": host, "concurrency": CONCURRENCY, "planned": total,
                 "reused_or_prior": len(baseline), "scheduled_this_session": scheduled,
                 "finished_this_session": finished, "in_flight": scheduled - finished,
                 "untouched": total - len(baseline) - scheduled,
                 "session_results": current, "cumulative": merged,
                 "latest_telemetry": sample, "elapsed_s": time.monotonic() - started,
                 "stop_reasons": list(gate.reasons), "worker_errors": worker_errors, **FLAGS}
        write_json(directory / "progress.json", value, replace=True)
        return value

    try:
        models = ext.read_models(base, directory, gate)
        sample = sampler.take()
        if not ext.server_idle(sample):
            gate.halt(base + ":not_healthy_idle_before_dispatch")
        write_json(directory / "preflight.json", {"at": now(), "models": models,
                   "allowed_to_dispatch": not gate.stop.is_set(), "sample": sample, **FLAGS})
        sampler.start()
        with ext.dispatch_pool(gate) as pool:
            pending, last_progress = {}, 0
            while pending or (not exhausted and not gate.stop.is_set()):
                while len(pending) < CONCURRENCY and not exhausted and not gate.stop.is_set():
                    try:
                        row = next(iterator)
                    except StopIteration:
                        exhausted = True
                        break
                    pending[pool.submit(pilot.request_once, row, row["global_index"], directory, base, gate)] = row["request_id"]
                    scheduled += 1
                if not pending:
                    break
                done, _ = wait(pending, timeout=1, return_when=FIRST_COMPLETED)
                for future in done:
                    rid = pending.pop(future)
                    finished += 1
                    try:
                        terminal = future.result()
                        if terminal.get("sent_intent"):
                            results[rid] = {"terminal": terminal}
                        else:
                            # The stop gate prevented the request before intent.
                            scheduled -= 1
                            finished -= 1
                    except Exception as error:
                        gate.halt(base + ":worker_recording_error")
                        item = {"at": now(), "request_id": rid, "error_type": type(error).__name__, **FLAGS}
                        worker_errors.append(item)
                        write_json(directory / ("worker_exception_" + digest(rid.encode())[:16] + ".json"), item)
                        results[rid] = {"terminal": {"request_id": rid,
                            "status": "worker_error_needs_reconcile", **FLAGS}}
                if time.monotonic() - last_progress >= ext.SAMPLE_INTERVAL_S:
                    progress()
                    last_progress = time.monotonic()
        local_finished = True
    except BaseException as error:
        gate.halt(base + ":endpoint_runner_failed:" + type(error).__name__)
        failure = type(error).__name__
        # ext.dispatch_pool waits for already-submitted futures on its exit,
        # even after an iterator/recording failure; requests may lack terminals.
        local_finished = True
    finally:
        if sampler.thread:
            try:
                drained = ext.wait_drained(sampler)
            finally:
                sampler.finish()
        else:
            # No inference was submitted if setup failed before sampler start.
            drained = bool(sampler.samples and ext.server_idle(sampler.samples[-1]) and scheduled == 0)
        iterator.close()
    value = progress()
    value.update(drained=drained, local_futures_finished=local_finished,
                 exhausted=exhausted, failure=failure,
                 corpus_endpoint_complete=value["untouched"] == 0 and value["in_flight"] == 0)
    write_json(directory / "round.json", value)
    write_json(directory / "exit.json", {"at": now(), "drained": drained,
               "local_futures_finished": local_finished, "failure": failure,
               "round_sha256": digest(canonical(value)), **FLAGS})
    return value


def run(root, session):
    root = Path(root).absolute()
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", session):
        raise ValueError("Safe unique session ID required")
    with coordinator(root):
        previous = prior_sessions(root)
        manifest, identities, reused = verify_manifest(root)
        seen = scan_history(root, identities, reused, previous)
        sessions = root / "sessions"
        sessions.mkdir(mode=0o700, exist_ok=True)
        directory = sessions / session
        directory.mkdir(mode=0o700)
        identity = pilot.read_identity(os.getpid())
        if identity is None:
            raise RuntimeError("Cannot establish actual client identity")
        gates = {host: ext.Gate() for host in HOSTS}
        handlers, rounds, failures = {}, {}, []
        write_json(directory / "start.json", {"at": now(), "session": session, "identity": identity,
                   "manifest_sha256": file_sha(root / "prepared" / "manifest.json"),
                   "parameters": PARAMETERS, "concurrency_per_endpoint": CONCURRENCY,
                   "prior_coverage": count_records(seen), "untouched": TOTAL - len(seen),
                   "previous_sessions": [path.name for path in previous], **FLAGS})
        write_json(root / "latest_session.json", {"session": session, "at": now()}, replace=True)

        def halt_all(reason):
            for gate in gates.values():
                gate.halt(reason)

        if threading.current_thread() is threading.main_thread():
            for signum in (signal.SIGINT, signal.SIGTERM):
                handlers[signum] = signal.signal(signum, lambda number, frame: halt_all("signal:" + str(number)))
        try:
            for host in HOSTS:
                (directory / host).mkdir(mode=0o700)
            with ThreadPoolExecutor(max_workers=2, thread_name_prefix="endpoint-coordinator") as pool:
                pending = {pool.submit(run_endpoint, root, directory / host, host, manifest, seen, gates[host]): host
                           for host in HOSTS}
                while pending:
                    done, _ = wait(pending, timeout=5, return_when=FIRST_COMPLETED)
                    for future in done:
                        host = pending.pop(future)
                        try:
                            rounds[host] = future.result()
                        except Exception as error:
                            failures.append({"host": host, "error_type": type(error).__name__})
                            gates[host].halt("endpoint_coordinator_failed")
                    write_json(directory / "progress.json", {"at": now(), "session": session,
                               "finished_endpoints": list(rounds), "pending_endpoints": list(pending.values()),
                               "failures": failures, "identity": identity, **FLAGS}, replace=True)
            drained = len(rounds) == 2 and all(value["drained"] for value in rounds.values())
            local_finished = len(rounds) == 2 and all(value["local_futures_finished"] for value in rounds.values())
            # Audit append-only evidence rather than trusting in-memory futures.
            actual = scan_history(root, identities, reused, previous + [directory])
            audit = count_records(actual)
            result = {"at": now(), "session": session, "drained": drained,
                      "local_futures_finished": local_finished, "failures": failures,
                      "coverage": audit, "untouched": TOTAL - len(actual),
                      "all_inputs_accounted": len(actual) == TOTAL,
                      "corpus_complete": len(actual) == TOTAL and audit["unknown"] == 0
                                         and drained and local_finished and not failures,
                      "endpoints": rounds, **FLAGS}
            write_json(directory / "round.json", result)
            write_json(directory / "exit.json", {"at": now(), "session": session,
                       "drained": drained, "local_futures_finished": local_finished,
                       "corpus_complete": result["corpus_complete"],
                       "all_inputs_accounted": result["all_inputs_accounted"],
                       "round_sha256": digest(canonical(result)), **FLAGS})
            return result
        except BaseException as error:
            halt_all("global_runner_failed:" + type(error).__name__)
            if not (directory / "exit.json").exists():
                write_json(directory / "exit.json", {"at": now(), "local_futures_finished": False,
                           "drained": False, "error_type": type(error).__name__, **FLAGS})
            raise
        finally:
            for signum, handler in handlers.items():
                signal.signal(signum, handler)


def status(root):
    """Read compact status only: do not scan 55k raw evidence files each heartbeat."""
    root = Path(root).absolute()
    latest = root / "latest_session.json"
    if not latest.exists():
        return {"state": "not_started", **FLAGS}
    session = json.loads(latest.read_bytes())["session"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", session):
        raise RuntimeError("Unsafe latest session identity")
    directory = root / "sessions" / session
    start = json.loads((directory / "start.json").read_bytes())
    value = {"at": now(), "session": session, "identity": start["identity"],
             "client_alive": is_live(start["identity"]), "endpoints": {}, **FLAGS}
    for name in ("progress", "exit", "round"):
        path = directory / (name + ".json")
        if path.exists():
            item = json.loads(path.read_bytes())
            if name == "round":
                item = {key: item[key] for key in item if key != "endpoints"}
            value[name] = item
    for host in HOSTS:
        value["endpoints"][host] = {}
        for name in ("progress", "exit"):
            path = directory / host / (name + ".json")
            if path.exists():
                value["endpoints"][host][name] = json.loads(path.read_bytes())
    value["state"] = "running" if value["client_alive"] else (
        "exited" if "exit" in value else "missing_exit_needs_reconcile")
    return value


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    launch = commands.add_parser("run")
    launch.add_argument("--root", required=True)
    launch.add_argument("--session", required=True)
    inspect = commands.add_parser("status")
    inspect.add_argument("--root", required=True)
    args = parser.parse_args()
    result = run(args.root, args.session) if args.command == "run" else status(args.root)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
