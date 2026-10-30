"""Lossless transport only: reconstruct byte-identical frozen request shards.

No HTTP, no model calls, no changes to frozen runtime code, prompts or inputs.
Uses a shared system string, SHA-addressed contexts, and one original answer.
Sentence tables and exact JSON strings are reconstructed with the frozen
sentence contract and checked against every original shard SHA-256.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

SCHEMA = "dvj-lossless-request-transport-v1"
FIELDS = {"answer", "answer_sha256", "global_index", "messages", "packet_sha256", "request_id", "sentences"}
META = {"answer_sha256", "global_index", "packet_sha256", "request_id"}
CONTEXT = {"case_text", "question", "judge_decision", "reference_pack"}


def need(condition, message):
    if not condition:
        raise ValueError(message)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def file_sha(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def load(path):
    return json.loads(Path(path).read_bytes())


def new_json(path, value):
    with Path(path).open("xb") as stream:
        stream.write(canonical(value) + b"\n")
        stream.flush()
        os.fsync(stream.fileno())


def rows(path):
    with Path(path).open("rb") as stream:
        for raw in stream:
            need(bool(raw.strip()), "Blank transport row")
            yield json.loads(raw), raw


def child(root, name):
    need(isinstance(name, str) and Path(name).name == name and name not in ("", ".", ".."), "Unsafe artifact name")
    path = root / name
    need(path.resolve() == path, "Symlinked transport artifact")
    return path


def contract_from(path, expected):
    path = Path(path).resolve()
    need(file_sha(path) == expected, "Frozen sentence contract hash mismatch")
    spec = importlib.util.spec_from_file_location("_lossless_transport_sentence_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def rebuild(item, context, system, contract):
    need(set(item) == META | {"answer", "context_id"}, "Unexpected compact row fields")
    need(set(context) == CONTEXT, "Unexpected context fields")
    answer = item["answer"]
    need(isinstance(answer, str) and sha(answer.encode()) == item["answer_sha256"], "Original answer digest mismatch")
    table = contract.build_sentences(answer)
    # Reproduce build_messages_from_packet's nested sorted key ordering and its
    # ordinary json.dumps spaces exactly, without needing local prompt files.
    delivered = json.loads(canonical(dict(context, sentence_table=table)))
    user = json.dumps(delivered, ensure_ascii=False, allow_nan=False)
    need(sha(user.encode()) == item["packet_sha256"], "Reconstructed user message digest mismatch")
    return {**{key: item[key] for key in META}, "answer": answer, "sentences": table,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}]}


def pack(prepared, output, contract_path):
    prepared, output = Path(prepared).resolve(), Path(output).resolve()
    need(not output.exists(), "Refuse replacing any existing transport directory")
    manifest = load(prepared / "manifest.json")
    code_hash = manifest["code_sha256"]["sentence_contract.py"]
    contract = contract_from(contract_path, code_hash)
    output.mkdir(parents=True, exist_ok=False, mode=0o700)
    contexts, system, records, mapping = {}, None, 0, {}
    ids, indexes = set(), set()
    with (output / "contexts.jsonl").open("xb") as context_stream:
        for host, original in sorted(manifest["shards"].items()):
            name = original["path"]
            source = child(prepared, name)
            compact_name = "compact_" + name.removeprefix("requests_")
            compact_path = child(output, compact_name)
            original_digest, compact_digest, count = hashlib.sha256(), hashlib.sha256(), 0
            with compact_path.open("xb") as compact_stream:
                for row, raw in rows(source):
                    need(set(row) == FIELDS, "Unexpected frozen row fields")
                    original_digest.update(raw)
                    need(row["request_id"] not in ids and row["global_index"] not in indexes, "Duplicate frozen identity")
                    ids.add(row["request_id"])
                    indexes.add(row["global_index"])
                    messages = row["messages"]
                    need(len(messages) == 2 and messages[0]["role"] == "system" and messages[1]["role"] == "user", "Unexpected messages")
                    candidate = messages[0]["content"]
                    need(system is None or system == candidate, "System prompt changed across frozen rows")
                    system = candidate
                    packet = json.loads(messages[1]["content"])
                    need(set(packet) == CONTEXT | {"sentence_table"}, "Unexpected frozen packet")
                    context = {key: packet[key] for key in CONTEXT}
                    context_bytes = canonical(context)
                    context_id = sha(context_bytes)
                    if context_id not in contexts:
                        contexts[context_id] = context_bytes
                        context_stream.write(canonical({"context_id": context_id, "context": context}) + b"\n")
                    else:
                        need(contexts[context_id] == context_bytes, "Context hash collision")
                    item = {**{key: row[key] for key in META}, "answer": row["answer"], "context_id": context_id}
                    restored = canonical(rebuild(item, context, system, contract)) + b"\n"
                    need(restored == raw, "Lossless row reconstruction differs from original bytes")
                    line = canonical(item) + b"\n"
                    compact_stream.write(line)
                    compact_digest.update(line)
                    count += 1
                    records += 1
                    if records % 10000 == 0:
                        print(json.dumps({"packed_rows": records, "unique_contexts": len(contexts)}), flush=True)
                compact_stream.flush()
                os.fsync(compact_stream.fileno())
            need(count == original["count"] and original_digest.hexdigest() == original["sha256"], "Frozen shard hash/count mismatch")
            mapping[name] = {"host": host, "count": count, "sha256": original["sha256"],
                             "compact_path": compact_name, "compact_sha256": compact_digest.hexdigest()}
        context_stream.flush()
        os.fsync(context_stream.fileno())
    need(records == manifest["request_count"] and indexes == set(range(records)), "Full frozen coverage mismatch")
    with (output / "system.txt").open("xb") as stream:
        stream.write(system.encode())
        stream.flush()
        os.fsync(stream.fileno())
    result = {"schema": SCHEMA, "request_count": records, "context_count": len(contexts),
              "sentence_contract_sha256": code_hash, "frozen_manifest_sha256": file_sha(prepared / "manifest.json"),
              "shards": mapping, "contexts_sha256": file_sha(output / "contexts.jsonl"),
              "system_sha256": sha(system.encode()), "lossless_rows_checked_during_pack": records}
    new_json(output / "manifest.json", result)
    return result


def reconstruct(packed, contract_path, output=None, suffix=""):
    packed = Path(packed).resolve()
    manifest = load(packed / "manifest.json")
    need(manifest["schema"] == SCHEMA, "Unexpected transport schema")
    contract = contract_from(contract_path, manifest["sentence_contract_sha256"])
    need(file_sha(packed / "contexts.jsonl") == manifest["contexts_sha256"], "Context artifact digest mismatch")
    contexts = {}
    for item, _ in rows(packed / "contexts.jsonl"):
        need(set(item) == {"context_id", "context"} and set(item["context"]) == CONTEXT, "Invalid context row")
        key = item["context_id"]
        need(key not in contexts and sha(canonical(item["context"])) == key, "Context digest/identity mismatch")
        contexts[key] = item["context"]
    need(len(contexts) == manifest["context_count"], "Context count mismatch")
    raw_system = (packed / "system.txt").read_bytes()
    need(sha(raw_system) == manifest["system_sha256"], "System prompt digest mismatch")
    system = raw_system.decode("utf-8")
    if output is not None:
        output = Path(output).absolute()
        need(output.resolve() == output, "Symlinked output directory")
        need(suffix in ("", ".staging"), "Unexpected output suffix")
        output.mkdir(parents=True, exist_ok=True, mode=0o700)
    ids, indexes, result = set(), set(), {}
    for original_name, meta in sorted(manifest["shards"].items()):
        source = child(packed, meta["compact_path"])
        need(file_sha(source) == meta["compact_sha256"], "Compact shard digest mismatch")
        target = child(output, original_name + suffix) if output is not None else None
        digest, count = hashlib.sha256(), 0
        stream = target.open("xb") if target is not None else None
        try:
            for item, _ in rows(source):
                need(item["request_id"] not in ids and item["global_index"] not in indexes, "Duplicate reconstructed identity")
                ids.add(item["request_id"])
                indexes.add(item["global_index"])
                need(item["context_id"] in contexts, "Missing referenced context")
                raw = canonical(rebuild(item, contexts[item["context_id"]], system, contract)) + b"\n"
                digest.update(raw)
                if stream is not None:
                    stream.write(raw)
                count += 1
            if stream is not None:
                stream.flush()
                os.fsync(stream.fileno())
        finally:
            if stream is not None:
                stream.close()
        need(count == meta["count"] and digest.hexdigest() == meta["sha256"], "Reconstructed original shard hash/count mismatch")
        result[original_name] = {"count": count, "sha256": digest.hexdigest(), "output": str(target) if target else None}
        print(json.dumps({"reconstructed": original_name, "count": count, "sha256": digest.hexdigest()}), flush=True)
    need(len(ids) == manifest["request_count"] and indexes == set(range(len(ids))), "Reconstructed full identity coverage mismatch")
    return {"verified": True, "request_count": len(ids), "contexts": len(contexts), "shards": result}


def compress_xz(output):
    output = Path(output).resolve()
    tool = shutil.which("xz")
    need(tool is not None, "xz CLI unavailable")
    files = {}
    for path in sorted(output.iterdir()):
        if path.suffix not in (".jsonl", ".json", ".txt"):
            continue
        target = path.with_name(path.name + ".xz")
        with target.open("xb") as stream:
            subprocess.run([tool, "-6", "-c", str(path)], stdout=stream, check=True)
            stream.flush()
            os.fsync(stream.fileno())
        files[path.name] = {"raw_bytes": path.stat().st_size, "sha256": file_sha(path),
                            "transport_path": target.name, "transport_bytes": target.stat().st_size,
                            "transport_sha256": file_sha(target)}
    new_json(output / "compression_manifest.json", files)
    return {"compressed_files": files, "total_transport_bytes": sum(x["transport_bytes"] for x in files.values())}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("pack", "verify", "unpack"))
    parser.add_argument("--prepared", type=Path)
    parser.add_argument("--packed", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--contract", type=Path, required=True)
    parser.add_argument("--suffix", default="")
    parser.add_argument("--xz", action="store_true")
    args = parser.parse_args()
    started = time.monotonic()
    if args.mode == "pack":
        result = pack(args.prepared, args.output, args.contract)
        if args.xz:
            result = {"packed": result, **compress_xz(args.output)}
    else:
        need(args.mode == "verify" or args.output is not None, "Unpack requires output")
        result = reconstruct(args.packed, args.contract, args.output if args.mode == "unpack" else None, args.suffix)
    print(json.dumps({"elapsed_s": time.monotonic() - started, "result": result}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
