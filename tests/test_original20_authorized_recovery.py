"""Offline checks for the single additional recovery dispatch authorized Sept 9."""
from contextlib import ExitStack, nullcontext
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from scripts import run_cross_judging_original20_rebuilt_xhub as runner


class AuthorizedRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.guard = Mock()
        self.guard.evidence.return_value = []
        self.guard.scope.side_effect = lambda _: nullcontext()
        self.guard.stopped = threading.Event()
        runner.atomic_write_json(self.out / "plan.json", {"budget_usd": 40})

    def task(self, tid):
        return dict(task_id=tid, answer_id=tid, case_id="case", question_number=1,
                    answer_condition="Qwen-Max", judge_label="GPT-4o", judge_model_id="gpt-4o",
                    input_ready=True, **{k: "frozen" for k in runner.HASH_KEYS})

    def test_recovery_is_frozen_and_never_selects_success_or_refusal(self):
        tasks = [self.task(tid) for tid in ("budget", "transport", "valid", "refused", "other")]
        runner.atomic_write_json(self.out / "manifest.json", {"tasks": tasks})
        errors = {"budget": "BudgetStop: Insufficient unreserved budget", "transport": "ProxyError", "other": "HTTPError: 400"}
        for task in tasks:
            tid = task["task_id"]
            record = dict(task, status="failed", error=errors.get(tid, ""))
            if tid in {"valid", "refused"}:
                record.update(status="success", evaluation={"总分": 0}, scoring={"content":
                    "The prompt could not be submitted. The prompt contains sensitive words" if tid == "refused" else "真实零分"})
            runner.atomic_write_json(self.out / "ratings" / (tid + ".json"), record)
            runner.atomic_write_json(self.out / "dispatches" / (tid + ".json"), {"count": 3})
        first = runner.prepare_recovery(self.out, self.guard)
        self.assertEqual(first["rating_limits"], {"budget": 4, "transport": 4})
        runner.atomic_write_json(self.out / "dispatches/budget.json", {"count": 4})
        self.assertEqual(runner.prepare_recovery(self.out, self.guard), first)
        runner.atomic_write_json(self.out / "plan.json", {"budget_usd": 70})
        with self.assertRaisesRegex(RuntimeError, "frozen run"):
            runner.prepare_recovery(self.out, self.guard)

    def test_fourth_mask_failure_is_archived_and_not_repeated(self):
        raw = {"title": "原题", "case_text": "原文", "judge_decision": "裁判"}
        title = dict(field="title", status="success", source_sha256=runner.sha256_text(raw["title"]),
                     value="匿名标题", value_sha256=runner.sha256_text("匿名标题"), calls=[{"ok": True, "finish_reason": "stop"}])
        runner.atomic_write_json(runner.mask_path(self.out, "case", "Qwen-Max", "title"), title)
        prior = dict(field="case_text", case_id="case", condition="Qwen-Max", status="failed", dispatch_count=3,
                     source_sha256=runner.sha256_text(raw["case_text"]), calls=[{"ok": True, "finish_reason": "length"}])
        path = runner.mask_path(self.out, "case", "Qwen-Max", "case_text")
        runner.atomic_write_json(path, prior)
        runner.atomic_write_json(self.out / "manifest.json", {"tasks": []})
        self.guard.recovery_plan = runner.prepare_recovery(self.out, self.guard)
        self.assertEqual(self.guard.recovery_plan["mask_limits"], {"mask::case::Qwen-Max::case_text": 4})
        with patch.object(runner.DataMaskerAPI, "mask_text_with_api", return_value=None) as masker:
            for _ in range(2):
                self.assertFalse(runner.run_mask_group(self.out, "case", "Qwen-Max", raw, Mock(), self.guard, threading.Event()))
            masker.assert_called_once_with("原文", is_title=False)
        self.assertEqual(runner.load_json(path)["dispatch_count"], 4)
        archives = list((self.out / "prior_attempts").glob("mask_*.json"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(runner.load_json(archives[0]), prior)

    def test_budget_failure_gets_only_one_authorized_dispatch(self):
        task = self.task("budget")
        path = self.out / "ratings/budget.json"
        dp = self.out / "dispatches/budget.json"
        record = dict(task, status="failed", error="BudgetStop: Insufficient unreserved budget")
        runner.atomic_write_json(path, record)
        runner.atomic_write_json(dp, {"count": 1})
        self.guard.recovery_plan = {"rating_limits": {"budget": 2}}
        def fail_again(*_):
            runner.atomic_write_json(dp, {"count": 2})
            return record
        with patch.object(runner, "snapshot", return_value=({"ratings_counts": {}}, [{"answer_id": "budget"}], [task])), \
                patch.object(runner, "score_worker", side_effect=fail_again) as worker, patch.object(runner, "emit"):
            for _ in range(2):
                runner.score_ready(self.out, {}, {}, {}, SimpleNamespace(workers=1), Mock(), self.guard, threading.Event())
            worker.assert_called_once()

    def test_main_restores_persisted_limits_without_flag(self):
        runner.atomic_write_json(self.out / runner.RECOVERY_FILE, {"existing": True})
        runner.atomic_write_json(self.out / "market_snapshot.json", {
            "public_default_group_prices_usd_per_million": {}, "quota_per_unit": 500000})
        limits = {"rating_limits": {"budget": 2}}
        state = {"mask_groups_ready": 95, "all_eligible_tasks_terminal": False}
        self.guard.stopped.set()  # No task dispatch; test only resume wiring.
        with ExitStack() as stack:
            stack.enter_context(patch("sys.argv", ["runner", "--execute", "--phase", "mask", "--output-dir", str(self.out)]))
            stack.enter_context(patch.object(runner, "prepare", return_value=({}, {"case_ids": []}, {})))
            stack.enter_context(patch.object(runner, "dotenv_values", return_value={"UNIFIED_API_BASE_URL": "https://offline.invalid/v1", "UNIFIED_API_KEY": "offline"}))
            stack.enter_context(patch.object(runner, "ScopedGuard", return_value=self.guard))
            stack.enter_context(patch.object(runner.legacy, "requests"))
            stack.enter_context(patch.object(runner.legacy, "XHubClient"))
            stack.enter_context(patch.object(runner.signal, "signal"))
            stack.enter_context(patch.object(runner, "emit"))
            stack.enter_context(patch.object(runner, "snapshot", return_value=(state, [], [])))
            recovery = stack.enter_context(patch.object(runner, "prepare_recovery", return_value=limits))
            runner.main()
            recovery.assert_called_once_with(self.out.resolve(), self.guard)
        self.assertEqual(self.guard.recovery_plan, limits)


if __name__ == "__main__":
    unittest.main()
