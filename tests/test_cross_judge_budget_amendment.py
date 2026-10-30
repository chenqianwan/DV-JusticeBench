"""Offline tests for the exact authorized supplement budget amendment."""
import copy
import fcntl
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from scripts import cross_judge_budget_amendment as amendment
from scripts.cross_judge_budget_guard import GuardedTransport


class BudgetAmendmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.object(amendment, "AUTHORIZED_RUN", self.out).start()
        self.lock = (self.out / "run.lock").open("w+")
        fcntl.flock(self.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        self.addCleanup(self.lock.close)
        self.plan = dict(budget_usd=40, expected_cases=20, expected_ratings=1497,
                         input_provenance="rebuilt_20260908", frozen_hashes={"sentinel": "unchanged"})
        self.ledger = dict(schema_version=1, budget_usd=40, prepaid_usd=0,
                           prices_usd_per_million={"judge": [1.0, 2.0]}, quota_per_unit=500000,
                           safety_factor=1.1, estimated_all_http_usd=10.0,
                           unknown_upper_usd=20.0, observed_debit_usd=9.0,
                           completed_http_attempts=90, unknown_http_attempts=4, next_id=91,
                           reservations={}, reserved_inflight_usd=0,
                           accounted_including_prepaid_usd=31.0, unreserved_budget_usd=9.0,
                           initial_used_quota=1000, latest_used_quota=2000,
                           balance_anchor_cost_usd=0, last_balance_attempt_epoch=99999999999,
                           stopped=True, stop_reason="Insufficient unreserved budget for next physical HTTP attempt",
                           stopped_at="prior-stop", started_at="original-start", updated_at="original-update",
                           extra_historical_evidence={"unchanged": True})
        self.write("plan.json", self.plan)
        self.write("budget_ledger.json", self.ledger)
        (self.out / "http_receipts").mkdir()
        self.write("http_receipts/http_0000001.json", {"settled": True, "sentinel": "do not modify"})

    def write(self, name, value):
        (self.out / name).write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def read(self, name):
        return json.loads((self.out / name).read_text(encoding="utf-8"))

    def apply(self, **kwargs):
        return amendment.apply_authorized_budget_amendment(self.out, lock_handle=self.lock, **kwargs)

    def test_amendment_preserves_plan_receipts_costs_and_backup(self):
        before_ledger = (self.out / "budget_ledger.json").read_bytes()
        before_plan = (self.out / "plan.json").read_bytes()
        before_receipt = (self.out / "http_receipts/http_0000001.json").read_bytes()
        self.assertEqual(self.apply(), 70)
        after = self.read("budget_ledger.json")
        changed = {"budget_usd", "stopped", "stop_reason", "stopped_at", "updated_at",
                   "accounted_including_prepaid_usd", "reserved_inflight_usd", "unreserved_budget_usd"}
        for field in self.ledger.keys() - changed:
            self.assertEqual(after[field], self.ledger[field], field)
        self.assertFalse(after["stopped"])
        self.assertNotIn("stop_reason", after)
        self.assertEqual(after["accounted_including_prepaid_usd"], 31)
        self.assertEqual(after["unreserved_budget_usd"], 39)
        self.assertEqual((self.out / "plan.json").read_bytes(), before_plan)
        self.assertEqual((self.out / "http_receipts/http_0000001.json").read_bytes(), before_receipt)
        backup = self.out / "budget_amendments" / (amendment.AMENDMENT_ID + ".original_ledger.json")
        self.assertEqual(backup.read_bytes(), before_ledger)
        self.assertEqual(backup.stat().st_mode & 0o777, 0o444)
        record = self.read("budget_amendments/" + amendment.AMENDMENT_ID + ".authorization.json")
        self.assertEqual(record["user_quote"], "70以内都可以，跑完就好")

    def test_idempotent_call_does_not_rewrite_any_artifacts(self):
        self.apply()
        before = {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.out.rglob("*.json")}
        self.assertEqual(self.apply(), 70)
        self.assertEqual(before, {p: (p.read_bytes(), p.stat().st_mtime_ns) for p in self.out.rglob("*.json")})

    def test_guard_resumes_same_accounting_at_70_without_network(self):
        self.apply()
        with patch("scripts.cross_judge_budget_guard.requests.get") as get, patch("scripts.cross_judge_budget_guard.requests.post") as post:
            guard = GuardedTransport("https://unit.test/v1", "not-real", self.out,
                                     {"judge": (1, 2)}, 500000, 70, prepaid_usd=0)
            self.assertFalse(guard.stopped.is_set())
            self.assertEqual(guard.state["unknown_upper_usd"], 20)
            self.assertEqual(guard.state["next_id"], 91)
            get.assert_not_called()
            post.assert_not_called()

    def test_subsequent_70_stop_is_not_cleared_or_costs_reset(self):
        self.apply()
        current = self.read("budget_ledger.json")
        current.update(stopped=True, stop_reason="Observed or conservatively estimated expenditure reached budget",
                       estimated_all_http_usd=50, observed_debit_usd=51, next_id=150)
        self.write("budget_ledger.json", current)
        before = (self.out / "budget_ledger.json").read_bytes()
        self.assertEqual(self.apply(), 70)
        self.assertEqual((self.out / "budget_ledger.json").read_bytes(), before)

    def test_refuses_unlocked_or_wrong_lock(self):
        fcntl.flock(self.lock, fcntl.LOCK_UN)
        with self.assertRaises(amendment.BudgetAmendmentError):
            self.apply()
        fcntl.flock(self.lock, fcntl.LOCK_EX)
        with (self.out / "other.lock").open("w+") as other:
            with self.assertRaises(amendment.BudgetAmendmentError):
                amendment.apply_authorized_budget_amendment(self.out, lock_handle=other)

    def test_refuses_active_reservations_without_modifying_ledger(self):
        self.ledger["reservations"] = {"http_0000091": 0.5}
        self.write("budget_ledger.json", self.ledger)
        before = (self.out / "budget_ledger.json").read_bytes()
        with self.assertRaises(amendment.BudgetAmendmentError):
            self.apply()
        self.assertEqual((self.out / "budget_ledger.json").read_bytes(), before)
        self.assertFalse((self.out / "budget_amendments").exists())

    def test_refuses_nonbudget_stop(self):
        self.ledger["stop_reason"] = "Cannot price request for unknown model or non-JSON payload"
        self.write("budget_ledger.json", self.ledger)
        with self.assertRaises(amendment.BudgetAmendmentError):
            self.apply()

    def test_refuses_wrong_quote_other_directory_and_new_ledger(self):
        with self.assertRaises(amendment.BudgetAmendmentError):
            self.apply(user_quote="spend without limit")
        with self.assertRaises(amendment.BudgetAmendmentError):
            amendment.apply_authorized_budget_amendment(self.out / "new", lock_handle=self.lock)
        (self.out / "budget_ledger.json").unlink()
        with self.assertRaises(amendment.BudgetAmendmentError):
            self.apply()

    def test_refuses_unaudited_raised_ledger_and_wrong_original_cap(self):
        for cap in (70, 50):
            with self.subTest(cap=cap):
                self.ledger["budget_usd"] = cap
                self.write("budget_ledger.json", self.ledger)
                with self.assertRaises(amendment.BudgetAmendmentError):
                    self.apply()

    def test_refuses_already_exhausted_new_budget(self):
        self.ledger["observed_debit_usd"] = 70
        self.write("budget_ledger.json", self.ledger)
        with self.assertRaises(amendment.BudgetAmendmentError):
            self.apply()

    def test_recovery_after_audit_before_ledger_replacement(self):
        real_publish = amendment._publish

        def crash(path, data, *, immutable):
            if not immutable:
                raise OSError("simulated power loss")
            return real_publish(path, data, immutable=immutable)

        with patch.object(amendment, "_publish", side_effect=crash):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(self.read("budget_ledger.json")["budget_usd"], 40)
        self.assertEqual(self.apply(), 70)
        self.assertEqual(self.read("budget_ledger.json")["unknown_upper_usd"], 20)

    def test_recovery_after_backup_before_authorization_record(self):
        real_publish = amendment._publish

        def crash(path, data, *, immutable):
            if str(path).endswith(".authorization.json"):
                raise OSError("simulated power loss")
            return real_publish(path, data, immutable=immutable)

        with patch.object(amendment, "_publish", side_effect=crash):
            with self.assertRaises(OSError):
                self.apply()
        self.assertEqual(self.apply(), 70)

    def test_refuses_changed_frozen_plan_after_amendment(self):
        self.apply()
        self.plan["frozen_hashes"]["sentinel"] = "changed"
        self.write("plan.json", self.plan)
        with self.assertRaises(amendment.BudgetAmendmentError):
            self.apply()

    def test_refuses_accounting_reset_after_amendment(self):
        self.apply()
        current = self.read("budget_ledger.json")
        current["unknown_upper_usd"] = 0
        self.write("budget_ledger.json", current)
        with self.assertRaises(amendment.BudgetAmendmentError):
            self.apply()

    def test_refuses_symlink_ledger(self):
        ledger = self.out / "budget_ledger.json"
        ledger.rename(self.out / "actual_ledger.json")
        ledger.symlink_to(self.out / "actual_ledger.json")
        with self.assertRaises(amendment.BudgetAmendmentError):
            self.apply()


if __name__ == "__main__":
    unittest.main()
