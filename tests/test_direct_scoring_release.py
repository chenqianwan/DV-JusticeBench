"""Offline checks for the explicit as-generated scoring release.

No API requests are made, no experiment source is edited, and any runtime test
files are confined to temporary directories. Releasing the hold is not a claim
that the known person-identifier conflict has been corrected.
"""

from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from scripts import report_cross_judge_rebuilt108_20260908 as report
from scripts import run_cross_judging_original20_rebuilt_xhub as runner


class DirectScoringReportTests(unittest.TestCase):
    def build(self, *, active):
        old_dir, new_dir = Path("/offline-fixture/old"), Path("/offline-fixture/new")
        old_cases = [f"old_{index:02d}" for index in range(88)]
        new_cases = [f"new_{index:02d}" for index in range(20)]
        old_rows, new_rows, new_answers = [], [], []
        for cohort, cases, rows in (("old", old_cases, old_rows), ("new", new_cases, new_rows)):
            for case_id in cases:
                for condition in report.CONDITIONS:
                    for question in range(1, 6):
                        if (cohort, case_id, condition, question) == ("new", "new_00", "Qwen-Max", 1):
                            continue
                        answer_id = f"{case_id}::q{question}::{condition}"
                        answer = dict(answer_id=answer_id, case_id=case_id,
                                      answer_condition=condition, question_number=question)
                        if cohort == "new":
                            new_answers.append(answer)
                        for judge in report.MATRIX[condition]:
                            rows.append(dict(answer, task_id=answer_id + "::" + judge,
                                             judge_label=judge, status="valid" if cohort == "old" else "pending",
                                             analysis_total=10 if cohort == "old" else None,
                                             input_ready=case_id == "new_00"))
        hold = dict(active=active, findings=["Historical and rebuilt person identifiers conflict."],
                    released_by_user_instruction="直接评分", scoring_policy="as_generated_no_entity_correction",
                    identity_conflict_resolved=False)
        values = {
            old_dir / "analysis_snapshot.json": dict(complete_case_ids=old_cases, updated_at="fixture"),
            old_dir / "rating_status_overlay.json": dict(records=old_rows),
            new_dir / "plan.json": dict(case_ids=new_cases, expected_valid_answers=499,
                                         expected_ratings=1497, excluded_generation_failures=1, budget_usd=40),
            new_dir / "state.json": dict(phase="scoring", mask_groups_ready=10, mask_groups_expected=100),
            new_dir / "quality_hold.json": hold,
            new_dir / "source_answers.json": new_answers,
            new_dir / "rating_status_overlay.json": dict(records=new_rows),
        }
        with patch.object(report.Snapshot, "read", side_effect=lambda path, default=None, **_: values.get(Path(path), default)):
            analysis, markdown = report.build_report(old_dir, new_dir)
        return analysis, markdown, hold

    def test_release_discloses_uncorrected_aliases_and_preserves_authority(self):
        analysis, markdown, hold = self.build(active=False)
        self.assertTrue(analysis["direct_scoring_with_unresolved_aliases"])
        self.assertEqual(analysis["quality_hold"], hold)
        self.assertFalse(analysis["quality_hold"]["identity_conflict_resolved"])
        self.assertEqual(analysis["phase"], "scoring")
        self.assertIn("尚未修正", markdown)
        self.assertIn("按生成后的输入直接评分", markdown)
        self.assertIn("需专家复核", markdown)
        self.assertIn("不能把相关差异全部归因于评分模型", markdown)
        self.assertNotIn("人物编号冲突已修复", markdown)
        self.assertEqual(analysis["scorable_rating_tasks"], 8097)
        self.assertEqual(analysis["new_budget_cap_usd"], 40)

    def test_active_hold_is_still_reported_as_a_pause(self):
        analysis, markdown, _ = self.build(active=True)
        self.assertEqual(analysis["phase"], "input_quality_hold")
        self.assertFalse(analysis["direct_scoring_with_unresolved_aliases"])
        self.assertIn("已暂停", markdown)
        self.assertNotIn("本批按生成后的输入直接评分", markdown)


class DirectScoringDispatchTests(unittest.TestCase):
    def run_main(self, *, stop_after_initial_scores=False):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        out = Path(temp.name)
        runner.atomic_write_json(out / "quality_hold.json", dict(
            active=False, released_by_user_instruction="直接评分",
            scoring_policy="as_generated_no_entity_correction", identity_conflict_resolved=False))
        runner.atomic_write_json(out / "market_snapshot.json", dict(
            public_default_group_prices_usd_per_million={"gpt-4o": [2.5, 10]}, quota_per_unit=500000))
        events = []
        guard = Mock()
        guard.stopped = threading.Event()
        guard.snapshot.return_value = {"budget_usd": 40, "prepaid_usd": 0}
        state = dict(mask_groups_ready=100, all_eligible_tasks_terminal=True, ratings_counts={})

        def score(*_):
            events.append("score")
            if stop_after_initial_scores:
                guard.stopped.set()

        def mask(*_):
            events.append("mask")
            return True

        with ExitStack() as stack:
            stack.enter_context(patch("sys.argv", ["runner", "--execute", "--phase", "all", "--workers", "1",
                                                      "--mask-workers", "1", "--output-dir", str(out)]))
            stack.enter_context(patch.object(runner, "prepare", return_value=({}, {"case_ids": ["fixture"]}, {"fixture": {}})))
            stack.enter_context(patch.object(runner, "dotenv_values", return_value={
                "UNIFIED_API_BASE_URL": "https://offline.invalid/v1", "UNIFIED_API_KEY": "offline-test-key"}))
            factory = stack.enter_context(patch.object(runner, "ScopedGuard", return_value=guard))
            stack.enter_context(patch.object(runner.legacy, "requests"))
            stack.enter_context(patch.object(runner.legacy, "XHubClient"))
            stack.enter_context(patch.object(runner.signal, "signal"))
            stack.enter_context(patch.object(runner, "emit"))
            stack.enter_context(patch.object(runner, "snapshot", return_value=(state, [], [])))
            stack.enter_context(patch.object(runner, "score_ready", side_effect=score))
            stack.enter_context(patch.object(runner, "run_mask_group", side_effect=mask))
            runner.main()
        factory.assert_called_once()
        self.assertEqual(factory.call_args.args[2], out.resolve())
        self.assertEqual(factory.call_args.args[5], 40)
        self.assertEqual(factory.call_args.kwargs["prepaid_usd"], 0)
        return events

    def test_all_phase_scores_ready_before_masking_then_scores_remaining(self):
        self.assertEqual(self.run_main(), ["score"] + ["mask"] * len(runner.CONDITIONS) + ["score"])

    def test_budget_stop_after_initial_scoring_does_not_dispatch_masks_or_more_scores(self):
        self.assertEqual(self.run_main(stop_after_initial_scores=True), ["score"])

    def test_score_ready_does_not_redispatch_valid_refused_unready_or_exhausted(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        out = Path(temp.name)
        labels = ["pending", "transport", "valid", "refused", "unready", "exhausted", "failed"]
        tasks = [dict(task_id=label, answer_id=label, judge_model_id="gpt-4o", judge_label="GPT-4o",
                      input_ready=label != "unready", **{key: "frozen" for key in runner.HASH_KEYS}) for label in labels]
        answers = [{"answer_id": label} for label in labels]
        for task in tasks:
            label = task["task_id"]
            record = dict(task)
            if label in {"pending", "unready"}:
                continue
            if label in {"valid", "refused"}:
                record.update(status="success", evaluation={"总分": 0}, scoring={"content": (
                    "The prompt could not be submitted. The prompt contains sensitive words" if label == "refused" else "按原规则为0分")})
            else:
                record.update(status="failed", error="ProxyError" if label in {"transport", "exhausted"} else "HTTPError: 400")
            runner.atomic_write_json(out / "ratings" / (label + ".json"), record)
            if label == "exhausted":
                runner.atomic_write_json(out / "dispatches" / (label + ".json"), {"count": 3})
        guard = Mock()
        guard.stopped = threading.Event()
        guard.evidence.return_value = []
        dispatched = []

        def finish(_, task, *__):
            dispatched.append(task["task_id"])
            record = dict(task, status="success", evaluation={"总分": 10})
            runner.atomic_write_json(out / "ratings" / (task["task_id"] + ".json"), record)
            return record

        with patch.object(runner, "snapshot", return_value=({"ratings_counts": {}}, answers, tasks)), \
                patch.object(runner, "score_worker", side_effect=finish), patch.object(runner, "emit"):
            runner.score_ready(out, {}, {}, {}, SimpleNamespace(workers=1), Mock(), guard, threading.Event())
        self.assertEqual(dispatched, ["pending", "transport"])


if __name__ == "__main__":
    unittest.main()
