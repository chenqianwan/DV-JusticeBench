"""Durable, conservative accounting around unchanged XHub HTTP requests.

This module never changes a scoring payload, response, or retry policy. It only
decides whether a physical request has sufficient unreserved budget, and writes
an audit receipt. The caller must hold an exclusive process-level run lock.
"""
from __future__ import annotations

import copy
import json
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit

import requests


class BudgetStop(RuntimeError):
    """No further paid request is permitted by the frozen run budget."""


def _now():
    return datetime.now(timezone.utc).isoformat()


def _write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _read_json(path):
    with Path(path).open(encoding="utf-8") as handle:
        return json.load(handle)


class GuardedTransport:
    """Requests-compatible proxy with per-attempt reservations and restart safety.

    Account balance polling is throttled to at most once per 300 seconds,
    including failed polls and resumes. Account debits may include other work on
    the same key: that can stop this run early, but cannot expand its budget.
    An unaccounted interrupted request is conservatively charged its reservation
    on restart; it is not automatically resent here. A receipt whose usage was
    durably written just before a crash is reconciled exactly once instead.
    """

    SAFETY_FACTOR = 1.1
    RESERVE_FACTOR = 2.0
    BALANCE_INTERVAL_SECONDS = 300

    def __init__(self, base, key, out, prices, quota_unit, budget, prepaid_usd=0.929528):
        parsed = urlsplit(base)
        self.origin = f"{parsed.scheme}://{parsed.netloc}"
        self.base, self.key, self.out = base, key, Path(out)
        self.prices = {name: [float(rate[0]), float(rate[1])] for name, rate in prices.items()}
        self.unit, self.budget = float(quota_unit), float(budget)
        self.prepaid_usd = float(prepaid_usd)
        if (not math.isfinite(self.budget) or self.budget <= 0 or
                not math.isfinite(self.unit) or self.unit <= 0 or
                not math.isfinite(self.prepaid_usd) or self.prepaid_usd < 0 or
                any(not math.isfinite(x) or x < 0 for p in self.prices.values() for x in p)):
            raise ValueError("Invalid budget, prepaid cost, quota unit, or prices")
        self.headers = {"Authorization": "Bearer " + key}
        self.lock = threading.RLock()
        self.balance_lock = threading.Lock()
        self.stopped = threading.Event()
        self.path = self.out / "budget_ledger.json"
        self.receipts = self.out / "http_receipts"
        if self.path.exists():
            self.state = _read_json(self.path)
            if (self.state.get("schema_version") != 1 or
                    self.state["budget_usd"] != self.budget or
                    self.state["prepaid_usd"] != self.prepaid_usd or
                    self.state["prices_usd_per_million"] != self.prices or
                    self.state["quota_per_unit"] != self.unit):
                raise BudgetStop("Budget, prepaid amount, prices, or ledger schema differs from frozen run")
            self._reconcile_interrupted_calls()
            if self.state.get("stopped"):
                self.stopped.set()
        else:
            self.state = {
                "schema_version": 1, "started_at": _now(),
                "budget_usd": self.budget, "prepaid_usd": self.prepaid_usd,
                "prices_usd_per_million": self.prices, "quota_per_unit": self.unit,
                "safety_factor": self.SAFETY_FACTOR,
                "initial_used_quota": None, "latest_used_quota": None,
                "observed_debit_usd": 0.0, "balance_anchor_cost_usd": 0.0,
                "last_balance_attempt_epoch": None,
                "estimated_all_http_usd": 0.0, "unknown_upper_usd": 0.0,
                "reservations": {}, "next_id": 1,
                "completed_http_attempts": 0, "unknown_http_attempts": 0,
                "stopped": False,
            }
            self._save()
        self.refresh_balance()

    def __getattr__(self, name):
        return getattr(requests, name)

    def _redact(self, value):
        if isinstance(value, str):
            return value.replace(self.key, "[REDACTED]") if self.key else value
        if isinstance(value, dict):
            sensitive = {"authorization", "api_key", "apikey", "x-api-key", "access_token", "refresh_token", "password", "secret"}
            return {k: "[REDACTED]" if str(k).lower() in sensitive else self._redact(v)
                    for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [self._redact(item) for item in value]
        return value

    def _accounted(self):
        # Unknown attempts are already charged a 2x upper bound, so do not add
        # the known-usage safety factor to those reservations a second time.
        conservative = (self.SAFETY_FACTOR * self.state["estimated_all_http_usd"] +
                        self.state["unknown_upper_usd"])
        return max(conservative, self.state["observed_debit_usd"]) + self.prepaid_usd

    def _save(self):
        self.state["updated_at"] = _now()
        self.state["accounted_including_prepaid_usd"] = self._accounted()
        self.state["reserved_inflight_usd"] = sum(self.state["reservations"].values())
        self.state["unreserved_budget_usd"] = (self.budget - self.state["accounted_including_prepaid_usd"] -
                                               self.state["reserved_inflight_usd"])
        _write_json(self.path, self.state)

    def snapshot(self):
        with self.lock:
            return copy.deepcopy(self.state)

    def _stop(self, reason):
        self.stopped.set()
        self.state["stopped"] = True
        self.state["stop_reason"] = reason
        self.state["stopped_at"] = _now()
        self._save()

    def refresh_balance(self, force=False):
        """Return latest quota counter; failure is recorded, never fatal.

        ``force`` is accepted for batch-boundary callers but cannot bypass the
        300-second safety throttle. List-price accounting continues meanwhile.
        """
        if not self.balance_lock.acquire(blocking=False):
            return self.state.get("latest_used_quota")
        try:
            with self.lock:
                attempted = self.state.get("last_balance_attempt_epoch")
                current = time.time()
                if attempted is not None and current - attempted < self.BALANCE_INTERVAL_SECONDS:
                    return self.state.get("latest_used_quota")
                self.state["last_balance_attempt_epoch"] = current
                self._save()
            try:
                response = requests.get(self.origin + "/api/usage/token/", headers=self.headers, timeout=20)
                response.raise_for_status()
                used = int(response.json()["data"]["total_used"])
                if used < 0:
                    raise ValueError("Negative quota counter")
            except Exception as exc:
                with self.lock:
                    self.state["balance_error"] = self._redact(type(exc).__name__ + ": " + str(exc))
                    self._save()
                return self.state.get("latest_used_quota")
            with self.lock:
                if self.state["initial_used_quota"] is None:
                    self.state["initial_used_quota"] = used
                    # If initial metering failed, do not discard already known
                    # spending when the first successful observation arrives.
                    self.state["balance_anchor_cost_usd"] = (self.state["estimated_all_http_usd"] +
                                                            self.state["unknown_upper_usd"])
                self.state["latest_used_quota"] = used
                debit = (max(0, used - self.state["initial_used_quota"]) / self.unit +
                         self.state["balance_anchor_cost_usd"])
                self.state["observed_debit_usd"] = max(self.state["observed_debit_usd"], debit)
                self.state["balance_checked_at"] = _now()
                self.state.pop("balance_error", None)
                if self._accounted() >= self.budget:
                    self._stop("Observed or conservatively estimated expenditure reached budget")
                else:
                    self._save()
                return used
        finally:
            self.balance_lock.release()

    def used_quota(self):
        """Throttled counterpart to the pilot's metering helper."""
        return self.refresh_balance()

    def _settle(self, call_id, receipt):
        if call_id not in self.state["reservations"]:
            return  # Receipt was already incorporated by an atomic ledger write.
        self.state["estimated_all_http_usd"] += receipt.get("list_estimated_usd", 0.0)
        self.state["unknown_upper_usd"] += receipt.get("unknown_upper_usd", 0.0)
        self.state["completed_http_attempts"] += 1
        if receipt.get("unknown_upper_usd", 0):
            self.state["unknown_http_attempts"] += 1
        self.state["reservations"].pop(call_id)

    def _reconcile_interrupted_calls(self):
        for call_id, reserve in list(self.state["reservations"].items()):
            receipt_path = self.receipts / (call_id + ".json")
            receipt = _read_json(receipt_path) if receipt_path.exists() else {}
            if not receipt.get("settled"):
                receipt.update({"call_id": call_id, "reserved_usd": reserve,
                                "list_estimated_usd": 0.0, "unknown_upper_usd": reserve,
                                "settled": True, "reconciled_after_restart": True,
                                "reconciled_at": _now(),
                                "accounting_note": "Interrupted request: reservation charged conservatively; no automatic replay"})
                _write_json(receipt_path, self._redact(receipt))
            self._settle(call_id, receipt)
        self._save()

    def post(self, url, **kwargs):
        # Throttled and fail-open *only for metering*. Paid calls still require a
        # durable reservation against conservative token-price accounting.
        if self.stopped.is_set():
            raise BudgetStop(self.state.get("stop_reason", "Budget guard stopped"))
        self.refresh_balance()
        payload = kwargs.get("json")
        if not isinstance(payload, dict) or payload.get("model") not in self.prices:
            with self.lock:
                self._stop("Cannot price request for unknown model or non-JSON payload")
            raise BudgetStop(self.state["stop_reason"])
        price = self.prices[payload["model"]]
        max_tokens = payload.get("max_tokens", payload.get("max_completion_tokens"))
        if not isinstance(max_tokens, int) or isinstance(max_tokens, bool) or max_tokens <= 0:
            with self.lock:
                self._stop("Cannot reserve request without a positive output-token ceiling")
            raise BudgetStop(self.state["stop_reason"])
        input_upper = len(json.dumps(payload.get("messages", []), ensure_ascii=False).encode("utf-8")) + 1024
        reserve = self.RESERVE_FACTOR * (input_upper * price[0] + max_tokens * price[1]) / 1e6
        with self.lock:
            if self.stopped.is_set():
                raise BudgetStop(self.state.get("stop_reason", "Budget guard stopped"))
            pending = sum(self.state["reservations"].values())
            if self._accounted() + pending + reserve > self.budget:
                self._stop("Insufficient unreserved budget for next physical HTTP attempt")
                raise BudgetStop(self.state["stop_reason"])
            call_id = f"http_{self.state['next_id']:07d}"
            self.state["next_id"] += 1
            self.state["reservations"][call_id] = reserve
            self._save()
        started = time.monotonic()
        receipt = {"call_id": call_id, "started_at": _now(), "request": self._redact(payload),
                   "reserved_usd": reserve, "settled": False, "response": None, "usage": {}}
        _write_json(self.receipts / (call_id + ".json"), receipt)
        try:
            response = requests.post(url, **kwargs)
            receipt["http_status"] = response.status_code
            try:
                body = response.json()
                receipt["response"] = self._redact(body)
                receipt["usage"] = body.get("usage") or {} if isinstance(body, dict) else {}
            except (ValueError, TypeError):
                receipt["response_text"] = self._redact(response.text[:1000])
            return response
        except BaseException as exc:
            receipt["error"] = self._redact(type(exc).__name__ + ": " + str(exc))
            raise
        finally:
            receipt["latency_seconds"] = round(time.monotonic() - started, 3)
            usage = receipt["usage"]
            inp = usage.get("prompt_tokens") if isinstance(usage, dict) else None
            out = usage.get("completion_tokens") if isinstance(usage, dict) else None
            measured = all(isinstance(x, (int, float)) and not isinstance(x, bool) and
                           math.isfinite(x) and x >= 0 for x in (inp, out))
            receipt["list_estimated_usd"] = (inp * price[0] + out * price[1]) / 1e6 if measured else 0.0
            # Explicit client rejection without usage is treated as unbilled;
            # network failure, server failure, or missing successful usage is not.
            rejected = 400 <= receipt.get("http_status", 0) < 500
            receipt["unknown_upper_usd"] = 0.0 if measured or rejected else reserve
            receipt["settled"] = True
            receipt["completed_at"] = _now()
            _write_json(self.receipts / (call_id + ".json"), self._redact(receipt))
            with self.lock:
                self._settle(call_id, receipt)
                if self._accounted() >= self.budget:
                    self._stop("Observed or conservatively estimated expenditure reached budget")
                else:
                    self._save()
