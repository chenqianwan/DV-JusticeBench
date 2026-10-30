"""One explicitly authorized, auditable budget amendment for historical 20 cases.

No network calls, task dispatch, prompt changes, or new ledgers are permitted.
The caller must already hold this run's exclusive ``run.lock`` and must apply
this amendment before constructing the transport with the new cumulative cap.
"""
from __future__ import annotations

import copy
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile


AUTHORIZED_RUN = (Path(__file__).resolve().parents[1] /
                  "data/experiment1_cross_judge_original20_rebuilt_xhub_20260908")
AUTHORIZED_QUOTE = "70以内都可以，跑完就好"
OLD_CAP_USD = 40.0
NEW_CAP_USD = 70.0
AMENDMENT_ID = "20260909_user_40_to_70"
BUDGET_STOP_REASONS = {
    "Observed or conservatively estimated expenditure reached budget",
    "Insufficient unreserved budget for next physical HTTP attempt",
}


class BudgetAmendmentError(RuntimeError):
    """Authorization or safe-resume evidence is missing or inconsistent."""


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")


def _read_existing(path):
    if path.is_symlink() or not path.is_file():
        raise BudgetAmendmentError(f"Expected existing, non-symlink file: {path.name}")
    data = path.read_bytes()
    try:
        value = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise BudgetAmendmentError(f"Invalid JSON: {path.name}") from exc
    if not isinstance(value, dict):
        raise BudgetAmendmentError(f"Expected object: {path.name}")
    return data, value


def _sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _publish(path, data, *, immutable):
    """Fsync then publish atomically; immutable audit files never get replaced."""
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
            if immutable:
                os.fchmod(handle.fileno(), 0o444)
                os.fsync(handle.fileno())
        if immutable:
            try:
                os.link(temporary, path)
            except FileExistsError:
                if path.is_symlink() or path.read_bytes() != data:
                    raise BudgetAmendmentError(f"Immutable audit conflict: {path.name}")
        else:
            os.replace(temporary, path)
        _sync_directory(path.parent)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _require_owned_lock(out, lock_handle):
    path = out / "run.lock"
    if path.is_symlink() or not path.is_file():
        raise BudgetAmendmentError("Existing run.lock is required")
    try:
        owned = os.fstat(lock_handle.fileno())
        actual = path.stat()
        if (owned.st_dev, owned.st_ino) != (actual.st_dev, actual.st_ino):
            raise BudgetAmendmentError("Lock handle does not identify this run.lock")
        # A second open-file description must conflict with the existing lock.
        # Merely passing an unlocked file handle is not accepted.
        with path.open("r+") as probe:
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                pass
            else:
                fcntl.flock(probe, fcntl.LOCK_UN)
                raise BudgetAmendmentError("Caller must already hold exclusive run.lock")
        # Also prove the provided description, rather than another process,
        # owns the exclusive lock. Reasserting an owned flock is a no-op.
        fcntl.flock(lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (AttributeError, OSError, ValueError) as exc:
        raise BudgetAmendmentError("Caller does not own exclusive run.lock") from exc


def _nonnegative(value, name):
    if (not isinstance(value, (int, float)) or isinstance(value, bool) or
            not math.isfinite(value) or value < 0):
        raise BudgetAmendmentError(f"Invalid accounting field: {name}")
    return float(value)


def _validate_ledger(ledger):
    if ledger.get("schema_version") != 1 or ledger.get("prepaid_usd") != 0:
        raise BudgetAmendmentError("Not this supplement's existing ledger schema/prepaid amount")
    if ledger.get("reservations") != {}:
        raise BudgetAmendmentError("Active or unreconciled reservations prohibit amendment")
    if not isinstance(ledger.get("stopped"), bool):
        raise BudgetAmendmentError("Invalid stopped flag")
    if ledger["stopped"] and ledger.get("stop_reason") not in BUDGET_STOP_REASONS:
        raise BudgetAmendmentError("Only a budget-caused stop can be amended")
    for field in ("estimated_all_http_usd", "unknown_upper_usd", "observed_debit_usd",
                  "prepaid_usd", "completed_http_attempts", "unknown_http_attempts", "next_id"):
        _nonnegative(ledger.get(field), field)
    if ledger.get("safety_factor") != 1.1:
        raise BudgetAmendmentError("Unexpected accounting safety factor")
    if not ledger.get("prices_usd_per_million") or not ledger.get("quota_per_unit"):
        raise BudgetAmendmentError("Missing frozen price/quota evidence")


def _amended_ledger(original, authorized_at):
    amended = copy.deepcopy(original)
    amended["budget_usd"] = NEW_CAP_USD
    amended["stopped"] = False
    amended.pop("stop_reason", None)
    amended.pop("stopped_at", None)
    amended["updated_at"] = authorized_at
    accounted = max(original["safety_factor"] * original["estimated_all_http_usd"] +
                    original["unknown_upper_usd"], original["observed_debit_usd"]) + original["prepaid_usd"]
    if accounted >= NEW_CAP_USD:
        raise BudgetAmendmentError("Conservative expenditure already exhausts the authorized new cap")
    amended["accounted_including_prepaid_usd"] = accounted
    amended["reserved_inflight_usd"] = 0.0
    amended["unreserved_budget_usd"] = NEW_CAP_USD - accounted
    amended["budget_amendment_ids"] = list(original.get("budget_amendment_ids", [])) + [AMENDMENT_ID]
    return amended


def apply_authorized_budget_amendment(out, *, lock_handle, user_quote=AUTHORIZED_QUOTE):
    """Apply the authorized cumulative $40->$70 amendment; return ``70.0``.

    ``plan.json`` stays byte-for-byte unchanged. Original ledger bytes and a
    user-authorization record are published immutably before the atomic ledger
    replacement. Calling again after success (or an interrupted publication)
    is safe. A later stop at $70 is deliberately NOT cleared on a repeated call.
    """
    out = Path(out)
    if out.is_symlink() or not out.is_dir() or out.resolve() != AUTHORIZED_RUN.resolve():
        raise BudgetAmendmentError("Amendment is authorized only for the existing original20 run")
    if user_quote != AUTHORIZED_QUOTE:
        raise BudgetAmendmentError("Exact user authorization is required")
    _require_owned_lock(out, lock_handle)
    plan_bytes, plan = _read_existing(out / "plan.json")
    if (plan.get("budget_usd") != OLD_CAP_USD or plan.get("expected_cases") != 20 or
            plan.get("expected_ratings") != 1497 or plan.get("input_provenance") != "rebuilt_20260908"):
        raise BudgetAmendmentError("Frozen source plan does not identify the authorized supplement")
    ledger_path = out / "budget_ledger.json"
    ledger_bytes, ledger = _read_existing(ledger_path)
    _validate_ledger(ledger)
    if ledger.get("budget_usd") not in (OLD_CAP_USD, NEW_CAP_USD):
        raise BudgetAmendmentError("Only the specifically authorized $40->$70 transition is allowed")
    audit = out / "budget_amendments"
    if audit.is_symlink() or (audit.exists() and not audit.is_dir()):
        raise BudgetAmendmentError("Unsafe audit directory")
    backup = audit / (AMENDMENT_ID + ".original_ledger.json")
    record_path = audit / (AMENDMENT_ID + ".authorization.json")
    if ledger["budget_usd"] == NEW_CAP_USD and not record_path.exists():
        raise BudgetAmendmentError("Raised ledger has no authorization audit; do not assume it is valid")
    if record_path.exists():
        _, record = _read_existing(record_path)
        original_bytes, original = _read_existing(backup)
        expected = dict(schema_version=1, amendment_id=AMENDMENT_ID,
                        run_directory=str(out.resolve()), user_quote=AUTHORIZED_QUOTE,
                        previous_cumulative_cap_usd=OLD_CAP_USD,
                        authorized_cumulative_cap_usd=NEW_CAP_USD,
                        original_ledger_file=backup.name, original_ledger_sha256=_sha(original_bytes),
                        frozen_plan_sha256=_sha(plan_bytes))
        if any(record.get(k) != v for k, v in expected.items()) or not record.get("authorized_at"):
            raise BudgetAmendmentError("Authorization audit chain does not match this run/plan/backup")
        _validate_ledger(original)
        if original.get("budget_usd") != OLD_CAP_USD:
            raise BudgetAmendmentError("Audit backup is not the original $40 ledger")
        amended_bytes = _json_bytes(_amended_ledger(original, record["authorized_at"]))
        if record.get("amended_ledger_sha256") != _sha(amended_bytes):
            raise BudgetAmendmentError("Amended ledger hash does not match authorization audit")
        if ledger["budget_usd"] == NEW_CAP_USD:
            if AMENDMENT_ID not in ledger.get("budget_amendment_ids", []):
                raise BudgetAmendmentError("Raised ledger is missing the recorded amendment identifier")
            # Later paid calls may legitimately advance counters. Never reset
            # those counters or clear a new stop when invoked idempotently.
            for field in ("prepaid_usd", "prices_usd_per_million", "quota_per_unit", "safety_factor"):
                if ledger.get(field) != original.get(field):
                    raise BudgetAmendmentError(f"Frozen ledger field changed: {field}")
            for field in ("estimated_all_http_usd", "unknown_upper_usd", "observed_debit_usd",
                          "completed_http_attempts", "unknown_http_attempts", "next_id"):
                if ledger[field] < original[field]:
                    raise BudgetAmendmentError(f"Accounting counter was reset: {field}")
            return NEW_CAP_USD
        if ledger_bytes != original_bytes:
            raise BudgetAmendmentError("Original ledger changed after the authorization audit was published")
    else:
        original_bytes, original = ledger_bytes, ledger
        authorized_at = datetime.now(timezone.utc).isoformat()
        amended_bytes = _json_bytes(_amended_ledger(original, authorized_at))
        audit.mkdir(exist_ok=True)
        _publish(backup, original_bytes, immutable=True)
        record = dict(schema_version=1, amendment_id=AMENDMENT_ID,
                      run_directory=str(out.resolve()), user_quote=AUTHORIZED_QUOTE,
                      authorized_at=authorized_at,
                      previous_cumulative_cap_usd=OLD_CAP_USD,
                      authorized_cumulative_cap_usd=NEW_CAP_USD,
                      original_ledger_file=backup.name, original_ledger_sha256=_sha(original_bytes),
                      frozen_plan_sha256=_sha(plan_bytes), amended_ledger_sha256=_sha(amended_bytes),
                      scope="Cumulative cap for this same original20 supplement, not $70 of new spending; frozen plan, inputs, prompts, models and receipts unchanged.")
        _publish(record_path, _json_bytes(record), immutable=True)
    _publish(ledger_path, amended_bytes, immutable=False)
    return NEW_CAP_USD
