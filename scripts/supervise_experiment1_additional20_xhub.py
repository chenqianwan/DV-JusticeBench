#!/usr/bin/env python3
"""Keep a frozen Experiment-1 batch running until every case strictly passes.

The supervisor never changes experimental inputs or model parameters.  It only
waits for xhub health, launches the resumable batch runner, and relaunches it
when a process exits before every manifest case passes validation.
"""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import signal
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict
from zoneinfo import ZoneInfo

import requests
from dotenv import dotenv_values


ROOT = Path(__file__).resolve().parents[1]
BATCH_RUNNER = ROOT / "scripts/run_experiment1_additional20_xhub.py"
DEFAULT_MANIFEST = ROOT / "data/additional_20_cases_20260819.json"
DEFAULT_OUTPUT_DIR = ROOT / "data/experiment1_additional20_xhub_20260819"
DEFAULT_REPORT = ROOT / "实验1_新增20case_五条件运行报告_20260819.md"
DEFAULT_XHUB_ENV = Path(
    os.environ.get("XHUB_ENV_FILE", str(ROOT.parent / "AI_Council" / ".env"))
).expanduser()


def now_hk() -> str:
    return datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds")


def atomic_json(path: Path, value: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temporary, path)


def strict_progress(output_dir: Path) -> tuple[int, int, str]:
    path = output_dir / "combined_results.json"
    if not path.exists():
        return 0, 0, "MISSING"
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return 0, 0, "UNREADABLE"
    return (
        int(result.get("completed_case_count") or 0),
        int(result.get("completed_answer_count") or 0),
        str(result.get("status") or "UNKNOWN"),
    )


def batch_pids() -> list[int]:
    probe = subprocess.run(
        ["pgrep", "-f", "python.*run_experiment1_additional20_xhub.py"],
        check=False,
        capture_output=True,
        text=True,
    )
    values: list[int] = []
    for line in probe.stdout.splitlines():
        try:
            pid = int(line.strip())
        except ValueError:
            continue
        if pid != os.getpid():
            values.append(pid)
    return values


def xhub_chat_health(env_path: Path) -> tuple[bool, str]:
    config = dotenv_values(env_path)
    base_url = str(config.get("UNIFIED_API_BASE_URL") or "").rstrip("/")
    api_key = str(config.get("UNIFIED_API_KEY") or "")
    if not base_url or not api_key:
        return False, "missing xhub configuration"
    payload = {
        "model": "deepseek-v3.2",
        "messages": [{"role": "user", "content": "只回复OK"}],
        "max_tokens": 2,
        "temperature": 0.3,
        "thinking": {"type": "disabled"},
    }
    started = time.perf_counter()
    try:
        response = requests.post(
            base_url + "/chat/completions",
            headers={"Authorization": "Bearer " + api_key, "Content-Type": "application/json"},
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        return False, f"{type(exc).__name__}: {exc}"
    latency = time.perf_counter() - started
    if response.status_code != 200:
        return False, f"HTTP {response.status_code} in {latency:.2f}s"
    return True, f"HTTP 200 in {latency:.2f}s"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    parser.add_argument("--xhub-env", type=Path, default=DEFAULT_XHUB_ENV)
    parser.add_argument("--max-case-workers", type=int, default=2)
    parser.add_argument("--per-case-workers", type=int, default=10)
    parser.add_argument("--max-case-attempts", type=int, default=2)
    parser.add_argument("--case-timeout-seconds", type=int, default=5400)
    parser.add_argument("--poll-seconds", type=int, default=60)
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    manifest_case_ids = [str(item["case_id"]) for item in (manifest.get("cases") or [])]
    if not manifest_case_ids or len(set(manifest_case_ids)) != len(manifest_case_ids):
        raise SystemExit("manifest must contain at least one case_id and all case_ids must be unique")
    expected_cases = len(manifest_case_ids)
    expected_answers = expected_cases * 25

    args.output_dir.mkdir(parents=True, exist_ok=True)
    lock_path = args.output_dir / ".supervisor.lock"
    state_path = args.output_dir / "supervisor_state.json"
    lock_handle = lock_path.open("a+", encoding="utf-8")
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("[supervisor] another supervisor already owns the lock", flush=True)
        return 2

    stopping = False

    def stop_handler(_signum: int, _frame: object) -> None:
        nonlocal stopping
        stopping = True

    signal.signal(signal.SIGTERM, stop_handler)
    signal.signal(signal.SIGINT, stop_handler)
    launch_count = 0

    while not stopping:
        completed_cases, completed_answers, aggregate_status = strict_progress(args.output_dir)
        state = {
            "updated_at": now_hk(),
            "supervisor_pid": os.getpid(),
            "completed_cases": completed_cases,
            "completed_answers": completed_answers,
            "aggregate_status": aggregate_status,
            "launch_count": launch_count,
        }
        if completed_cases == expected_cases and completed_answers == expected_answers:
            state["status"] = "COMPLETE"
            state["expected_cases"] = expected_cases
            state["expected_answers"] = expected_answers
            atomic_json(state_path, state)
            print(
                f"[supervisor] strict completion reached: {expected_cases}/{expected_cases} cases, "
                f"{expected_answers}/{expected_answers} units",
                flush=True,
            )
            return 0

        existing = batch_pids()
        if existing:
            state.update({"status": "WAITING_FOR_EXISTING_BATCH", "batch_pids": existing})
            atomic_json(state_path, state)
            print(f"[supervisor] existing batch pids={existing}; waiting", flush=True)
            for _ in range(max(1, args.poll_seconds)):
                if stopping:
                    break
                time.sleep(1)
            continue

        healthy, health_detail = xhub_chat_health(args.xhub_env)
        state["xhub_health"] = health_detail
        if not healthy:
            state["status"] = "WAITING_FOR_XHUB"
            atomic_json(state_path, state)
            print(f"[supervisor] xhub unavailable: {health_detail}; waiting", flush=True)
            for _ in range(max(1, args.poll_seconds)):
                if stopping:
                    break
                time.sleep(1)
            continue

        command = [
            sys.executable,
            str(BATCH_RUNNER),
            "--manifest",
            str(args.manifest),
            "--output-dir",
            str(args.output_dir),
            "--report",
            str(args.report),
            "--max-case-workers",
            str(args.max_case_workers),
            "--per-case-workers",
            str(args.per_case_workers),
            "--max-case-attempts",
            str(args.max_case_attempts),
            "--case-timeout-seconds",
            str(args.case_timeout_seconds),
        ]
        launch_count += 1
        state.update(
            {
                "status": "BATCH_RUNNING",
                "launch_count": launch_count,
                "batch_started_at": now_hk(),
                "xhub_health": health_detail,
            }
        )
        atomic_json(state_path, state)
        print(f"[supervisor] launching batch #{launch_count}: {' '.join(command)}", flush=True)
        completed = subprocess.run(command, cwd=ROOT, check=False)
        print(f"[supervisor] batch #{launch_count} exited code={completed.returncode}", flush=True)

        for _ in range(min(10, max(1, args.poll_seconds))):
            if stopping:
                break
            time.sleep(1)

    completed_cases, completed_answers, aggregate_status = strict_progress(args.output_dir)
    atomic_json(
        state_path,
        {
            "updated_at": now_hk(),
            "supervisor_pid": os.getpid(),
            "status": "STOPPED",
            "completed_cases": completed_cases,
            "completed_answers": completed_answers,
            "aggregate_status": aggregate_status,
            "launch_count": launch_count,
        },
    )
    print("[supervisor] stopped", flush=True)
    return 130


if __name__ == "__main__":
    raise SystemExit(main())
