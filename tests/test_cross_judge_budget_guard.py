"""Offline tests: never contacts XHub or sends paid model requests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import requests

from scripts.cross_judge_budget_guard import BudgetStop, GuardedTransport


class BudgetGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.balance = Mock()
        self.balance.json.return_value = {"data": {"total_used": 1000}}
        self.balance_get = patch("scripts.cross_judge_budget_guard.requests.get", return_value=self.balance).start()
        self.post = patch("scripts.cross_judge_budget_guard.requests.post").start()
        self.addCleanup(patch.stopall)
        self.payload = {"model": "judge", "messages": [{"role": "user", "content": "hello"}], "max_tokens": 1000}

    def guard(self, budget=110, prepaid=0.929528):
        return GuardedTransport("https://unit.test/v1", "test-secret", self.out,
                                {"judge": (1.0, 2.0)}, 500000, budget, prepaid_usd=prepaid)

    @staticmethod
    def response(status=200, usage=None):
        result = Mock(status_code=status, text="no usage")
        result.json.return_value = {"usage": usage or {"prompt_tokens": 100, "completion_tokens": 200}}
        return result

    def test_success_response_payload_and_headers_unchanged(self):
        guard = self.guard()
        response = self.response()
        self.post.return_value = response
        headers = {"Authorization": "Bearer test-secret"}
        self.assertIs(guard.post("https://unit.test/v1/chat/completions", json=self.payload, headers=headers), response)
        self.post.assert_called_once_with("https://unit.test/v1/chat/completions", json=self.payload, headers=headers)
        state = guard.snapshot()
        self.assertAlmostEqual(state["estimated_all_http_usd"], 0.0005)
        self.assertAlmostEqual(state["accounted_including_prepaid_usd"], .929528 + 1.1 * .0005)
        self.assertEqual(state["reservations"], {})
        self.assertNotIn("test-secret", "".join(p.read_text() for p in self.out.rglob("*.json")))

    def test_budget_cap_prevents_physical_call(self):
        guard = self.guard(budget=0.93)
        with self.assertRaises(BudgetStop):
            guard.post("https://unit.test/v1/chat/completions", json=self.payload)
        self.post.assert_not_called()
        self.assertTrue(guard.stopped.is_set())
        restarted = self.guard(budget=0.93)
        self.assertTrue(restarted.stopped.is_set())

    def test_resume_preserves_cost_and_monotone_call_ids(self):
        self.post.return_value = self.response()
        guard = self.guard()
        guard.post("https://unit.test", json=self.payload)
        restarted = self.guard()
        restarted.post("https://unit.test", json=self.payload)
        self.assertAlmostEqual(restarted.state["estimated_all_http_usd"], 0.001)
        self.assertEqual(restarted.state["next_id"], 3)
        self.assertEqual(len(list((self.out / "http_receipts").glob("*.json"))), 2)
        self.assertEqual(self.balance_get.call_count, 1)

    def test_network_uncertainty_charged_upper_and_resume_not_stuck(self):
        guard = self.guard()
        self.post.side_effect = requests.Timeout("test-secret was redacted")
        with self.assertRaises(requests.Timeout):
            guard.post("https://unit.test", json=self.payload)
        upper = guard.state["unknown_upper_usd"]
        self.assertGreater(upper, 0)
        self.assertEqual(guard.state["reservations"], {})
        self.assertAlmostEqual(self.guard().state["unknown_upper_usd"], upper)
        self.assertNotIn("test-secret", "".join(p.read_text() for p in self.out.rglob("*.json")))

    def test_crash_after_receipt_before_ledger_exactly_once(self):
        guard = self.guard()
        state = guard.snapshot()
        state["reservations"] = {"http_0000001": 0.1}
        state["next_id"] = 2
        guard.path.write_text(json.dumps(state))
        receipt_path = self.out / "http_receipts/http_0000001.json"
        receipt_path.parent.mkdir()
        receipt_path.write_text(json.dumps({"settled": True, "list_estimated_usd": .01, "unknown_upper_usd": 0}))
        restarted = self.guard()
        self.assertAlmostEqual(restarted.state["estimated_all_http_usd"], .01)
        self.assertEqual(restarted.state["reservations"], {})
        self.assertAlmostEqual(self.guard().state["estimated_all_http_usd"], .01)

    def test_crash_without_final_receipt_conservatively_settled(self):
        guard = self.guard()
        state = guard.snapshot()
        state["reservations"] = {"http_0000001": 0.1}
        state["next_id"] = 2
        guard.path.write_text(json.dumps(state))
        restarted = self.guard()
        self.assertAlmostEqual(restarted.state["unknown_upper_usd"], .1)
        self.assertEqual(restarted.state["reservations"], {})
        self.assertAlmostEqual(self.guard().state["unknown_upper_usd"], .1)

    def test_each_physical_retry_has_receipt_and_usage(self):
        guard = self.guard()
        failed = Mock(status_code=400)
        failed.json.return_value = {"error": "content filter"}
        self.post.side_effect = [failed, self.response()]
        guard.post("https://unit.test", json=self.payload)
        guard.post("https://unit.test", json=self.payload)
        self.assertEqual(guard.state["completed_http_attempts"], 2)
        self.assertEqual(guard.state["unknown_upper_usd"], 0)
        self.assertAlmostEqual(guard.state["estimated_all_http_usd"], .0005)

    def test_metering_failure_is_throttled_not_a_model_failure(self):
        self.balance_get.side_effect = requests.HTTPError("429")
        guard = self.guard()
        self.post.return_value = self.response()
        guard.post("https://unit.test", json=self.payload)
        guard.refresh_balance(force=True)
        self.assertEqual(self.balance_get.call_count, 1)
        self.assertEqual(guard.state["completed_http_attempts"], 1)
        self.assertIsNone(guard.state["initial_used_quota"])


if __name__ == "__main__":
    unittest.main()
