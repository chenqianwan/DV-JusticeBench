import unittest
from pathlib import Path
import json
import tempfile
from unittest.mock import patch

from scripts import report_cross_judge_rebuilt108_20260908 as report

from scripts.report_cross_judge_rebuilt108_20260908 import (
    CONDITIONS, MATRIX, answer_consensus, complete_case_summary,
    normalize_rows, score_matrix, status_counts,
)


def row(case, condition, question, judge, score=10, status="valid"):
    answer_id = f"{case}::q{question}::{condition}"
    return {
        "task_id": f"{answer_id}::{judge}", "answer_id": answer_id,
        "case_id": case, "answer_condition": condition, "judge_label": judge,
        "status": status, "analysis_total": score,
    }


class RebuiltReportTests(unittest.TestCase):
    def test_refusal_is_missing_and_real_zero_is_valid(self):
        rows = normalize_rows([
            row("a", "GPT-4o", 1, "Gemini 2.5 Flash", 0, "still_refused"),
            row("a", "GPT-4o", 1, "Qwen-Max", 0),
        ], "old")
        self.assertIsNone(rows[0]["analysis_total"])
        self.assertEqual(rows[1]["analysis_total"], 0)
        counts = status_counts(rows, 3)
        self.assertEqual(counts, {"valid": 1, "refused": 1, "transport_failed": 0, "failed": 0, "pending": 1})
        self.assertIsNone(next(iter(answer_consensus(rows).values()))["consensus"])

    def test_common_cases_exclude_one_generation_failure(self):
        rows = []
        for case in ("complete", "generation_failed"):
            for condition in CONDITIONS:
                for question in range(1, 6):
                    if (case, condition, question) == ("generation_failed", "GPT-4o", 1):
                        continue
                    for index, judge in enumerate(MATRIX[condition]):
                        rows.append(row(case, condition, question, judge, [0, 10, 20][index]))
        summary = complete_case_summary(answer_consensus(normalize_rows(rows, "new")))
        self.assertEqual(summary["complete_case_ids"], ["complete"])
        self.assertEqual(summary["answers_with_three_valid_judges"], 49)
        self.assertTrue(all(r["case_n"] == 1 and r["mean"] == 10 for r in summary["summary"]))

    def test_off_design_and_duplicate_ratings_rejected(self):
        wrong = row("a", "GPT-4o", 1, "GPT-4o")
        with self.assertRaises(ValueError):
            normalize_rows([wrong], "new")
        valid = row("a", "GPT-4o", 1, "Qwen-Max")
        with self.assertRaises(ValueError):
            normalize_rows([valid, valid], "new")

    def test_empty_matrix_preserves_design_without_scores(self):
        matrix = score_matrix([])
        self.assertFalse(matrix[0]["judges"]["GPT-4o"]["assigned"])
        self.assertTrue(matrix[0]["judges"]["Qwen-Max"]["assigned"])
        self.assertIsNone(matrix[0]["judges"]["Qwen-Max"]["mean"])

    def test_duplicate_design_key_with_different_answer_and_task_ids_is_rejected(self):
        first = row("a", "GPT-4o", 1, "Qwen-Max")
        duplicate = dict(first, answer_id="another::q1::answer", task_id="another-task")
        with self.assertRaisesRegex(ValueError, "case/question/condition/judge"):
            normalize_rows([first, duplicate], "new")

    def test_pending_manifest_fills_missing_overlay_without_inventing_a_score(self):
        task = dict(row("new", "GPT-4o", 1, "Qwen-Max"), input_ready=False)
        rows = report.fill_pending_rows([], [], {"tasks": [task]}, Path("/offline/new"))
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["status"], "pending")
        self.assertIsNone(rows[0]["analysis_total"])
        self.assertTrue(rows[0]["report_synthesized_pending"])

    def test_budget_keeps_frozen40_and_effective70_separate_from_reserved_spend(self):
        budget = report.budget_summary({"budget_usd": 40}, {"budget": {"budget_usd": 40}},
                                       {"budget_usd": 70, "estimated_all_http_usd": 31,
                                        "observed_debit_usd": 30, "accounted_including_prepaid_usd": 39,
                                        "unknown_upper_usd": 5, "reserved_inflight_usd": 3}, {"new_cap": 70})
        self.assertEqual(budget["frozen_plan_cap_usd"], 40)
        self.assertEqual(budget["effective_cap_usd"], 70)
        self.assertEqual(budget["observed_account_debit_usd"], 30)
        self.assertEqual(budget["guard_accounted_usd_not_actual_spend"], 39)

    def test_mask_truncation_is_missing_input_not_failed_rating_request(self):
        new_dir = Path("/offline/new")
        entry = normalize_rows([dict(row("new", "GPT-4o", 1, "Qwen-Max", None, "pending"),
                                    input_ready=False)], "rebuilt_inputs_20")[0]
        mask_path = new_dir / "mask_parts/new/GPT-4o/case_text.json"
        values = {
            new_dir / "mask_parts/new/GPT-4o/title.json": {"status": "success"},
            mask_path: {"status": "failed", "dispatch_count": 3, "calls": [{"ok": True, "finish_reason": "length"}]},
        }
        with patch.object(report.Snapshot, "read", side_effect=lambda path, default=None, **_: values.get(Path(path), default)):
            details = report.merge_details(report.Snapshot(), [], [entry], [], {}, Path("/offline/old"), new_dir)
        self.assertEqual(details[0]["detailed_status"], "missing_input")
        self.assertEqual(details[0]["input_blocking_cause"], "truncated")
        self.assertEqual(details[0]["input_blockers"][0]["path"], str(mask_path))
        self.assertEqual(report.detailed_counts(details)["transport_failed"], 0)
        self.assertIsNone(details[0]["analysis_total"])

    def test_failed_scoring_truncation_distinguished_but_valid_score_not_reparsed(self):
        path = Path("/offline/new/ratings/failure.json")
        entries = normalize_rows([
            dict(row("new", "GPT-4o", 1, "Qwen-Max", None, "failed"), rating_path=str(path)),
            dict(row("new", "GPT-4o", 1, "Gemini 2.5 Flash", 0), rating_path=str(path)),
        ], "rebuilt_inputs_20")
        with patch.object(report.Snapshot, "read", return_value={"scoring": {"finish_reason": "length"}}):
            details = report.merge_details(report.Snapshot(), [], entries, [], {}, Path("/offline/old"), Path("/offline/new"))
        by_judge = {r["judge_label"]: r for r in details}
        self.assertEqual(by_judge["Qwen-Max"]["detailed_status"], "truncated")
        self.assertEqual(by_judge["Gemini 2.5 Flash"]["detailed_status"], "valid")
        self.assertEqual(by_judge["Gemini 2.5 Flash"]["analysis_total"], 0)


class FullMergedReportTests(unittest.TestCase):
    def fixture(self):
        old_dir, new_dir = Path("/offline-fixture/old"), Path("/offline-fixture/new")
        old_cases, new_cases = [f"old_{i:02d}" for i in range(88)], [f"new_{i:02d}" for i in range(20)]
        old_rows, new_rows, source = [], [], []
        for cohort, cases, rows in (("old", old_cases, old_rows), ("new", new_cases, new_rows)):
            for case in cases:
                for condition in CONDITIONS:
                    for q in range(1, 6):
                        if (cohort, case, condition, q) == ("new", "new_00", "Qwen-Max", 1):
                            continue
                        ready = case != "new_00"
                        if cohort == "new":
                            source.append(dict(answer_id=f"{case}::q{q}::{condition}", case_id=case,
                                               answer_condition=condition, question_number=q, input_ready=ready))
                        for judge in MATRIX[condition]:
                            entry = row(case, condition, q, judge, score=10 if ready else None,
                                        status="valid" if ready else "pending")
                            entry["input_ready"] = ready
                            if cohort == "old":
                                entry["rating_path"] = f"/offline/raw-old/{entry['task_id']}.json"
                            rows.append(entry)
        values = {
            old_dir / "analysis_snapshot.json": dict(complete_case_ids=old_cases),
            old_dir / "rating_status_overlay.json": dict(records=old_rows),
            new_dir / "plan.json": dict(case_ids=new_cases, expected_valid_answers=499,
                                         expected_ratings=1497, excluded_generation_failures=1, budget_usd=40),
            new_dir / "state.json": dict(phase="finished_with_missing", mask_groups_ready=95, mask_groups_expected=100),
            new_dir / "quality_hold.json": dict(active=False, scoring_policy="as_generated_no_entity_correction",
                                                  identity_conflict_resolved=False),
            new_dir / "source_answers.json": source,
            new_dir / "rating_status_overlay.json": dict(records=new_rows),
            new_dir / "budget_ledger.json": dict(budget_usd=70, estimated_all_http_usd=31,
                                                   observed_debit_usd=30, accounted_including_prepaid_usd=39),
            new_dir / "budget_amendments/20260909_user_40_to_70.authorization.json": {"new_cap": 70},
        }
        return old_dir, new_dir, values

    def complete_fixture(self, *, with_amendment=True):
        old_dir, new_dir, values = self.fixture()
        for answer in values[new_dir / "source_answers.json"]:
            answer["input_ready"] = True
        rows = values[new_dir / "rating_status_overlay.json"]["records"]
        for entry in rows:
            entry.update(input_ready=True, status="valid", analysis_total=10)
        rows[0].update(status="refused", analysis_total=None, raw_total=0)
        values[new_dir / "state.json"].update(phase="finished", mask_groups_ready=100)
        if with_amendment:
            scopes = [f"mask::new_00::{condition}::case_text" for condition in CONDITIONS]
            values[new_dir / "token_amendment_20260909.json"] = dict(
                case_id="new_00", field="case_text", max_tokens=16000,
                previous_initial_max_tokens=4000, previous_retry_max_tokens=8000,
                scopes=scopes, dispatch_limits={scope: 7 for scope in scopes},
                authorization="User explicitly permits a longer output for the remaining case.",
                plan_sha256="frozen-plan-hash",
            )
        return old_dir, new_dir, values

    def test_full_merge_has8097_unique_slots_and_explicit_remaining_missing_inputs(self):
        old_dir, new_dir, values = self.fixture()
        with patch.object(report.Snapshot, "read", side_effect=lambda path, default=None, **_: values.get(Path(path), default)):
            analysis, markdown = report.build_report(old_dir, new_dir)
        records = analysis["merged_records"]
        self.assertEqual(len(records), 8097)
        self.assertEqual(len({tuple(r["canonical_key"]) for r in records}), 8097)
        self.assertEqual(analysis["cohort_detailed_status_counts"]["combined"]["missing_input"], 72)
        self.assertEqual(analysis["cohort_detailed_status_counts"]["combined"]["valid"], 8025)
        self.assertEqual(analysis["new_budget_cap_usd"], 70)
        self.assertEqual(analysis["new_budget_details"]["frozen_plan_cap_usd"], 40)
        self.assertIn("不能称 108 案全量互评完成", markdown)
        self.assertIn("缺少输入", markdown)
        self.assertIn("尚未修正", markdown)
        self.assertIn("专家复核尚未完成", markdown)
        self.assertIn("不是实际支出", markdown)
        self.assertEqual(len(analysis["old_complete_case_analysis"]["complete_case_ids"]), 88)
        self.assertEqual(len(analysis["new_complete_case_analysis"]["complete_case_ids"]), 19)
        self.assertTrue(all(r["analysis_total"] is None for r in records if r["detailed_status"] == "missing_input"))

    def test_cli_writes_separate_merge_json_and_hash_without_touching_input(self):
        old_dir, new_dir, values = self.fixture()
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory)
            with patch.object(report.Snapshot, "read", side_effect=lambda path, default=None, **_: values.get(Path(path), default)), \
                    patch("sys.argv", ["report", "--old-dir", str(old_dir), "--new-dir", str(new_dir), "--output-dir", str(out)]), \
                    patch("builtins.print"):
                report.main()
            merged = json.loads((out / "merged_rating_details.json").read_text())
            analysis = json.loads((out / "analysis_snapshot.json").read_text())
            self.assertEqual(len(merged["records"]), 8097)
            self.assertNotIn("merged_records", analysis)
            self.assertEqual(len(analysis["merged_detail"]["sha256"]), 64)
            self.assertEqual(analysis["merged_detail"]["path"], str((out / "merged_rating_details.json").resolve()))
            self.assertTrue((out / "108案_互评补充结果.md").exists())

    def test_complete_attempts_claim108_but_refusals_remain_na(self):
        old_dir, new_dir, values = self.complete_fixture()
        with patch.object(report.Snapshot, "read", side_effect=lambda path, default=None, **_: values.get(Path(path), default)):
            analysis, markdown = report.build_report(old_dir, new_dir)
        self.assertTrue(analysis["completed_mutual_evaluation_attempts"])
        self.assertFalse(analysis["all_eligible_scores_available"])
        self.assertTrue(analysis["progress"].startswith("108 案已完成互评尝试"))
        self.assertIn("1 条拒评仍为 NA", markdown)
        self.assertNotIn("20 案补充互评尚未完成", markdown)
        refused = [r for r in analysis["merged_records"] if r["status"] == "refused"]
        self.assertEqual(len(refused), 1)
        self.assertIsNone(refused[0]["analysis_total"])
        self.assertEqual(refused[0]["raw_total"], 0)
        self.assertIn("人物编号与旧回答存在冲突，尚未修正", markdown)
        self.assertIn("专家复核尚未完成", markdown)

    def test_token_exception_discloses_single_case_and_marks_only_its72_slots(self):
        old_dir, new_dir, values = self.complete_fixture()
        with patch.object(report.Snapshot, "read", side_effect=lambda path, default=None, **_: values.get(Path(path), default)):
            analysis, markdown = report.build_report(old_dir, new_dir)
        marked = [r for r in analysis["merged_records"] if "masking_token_limit_exception" in r]
        self.assertEqual(len(marked), 72)
        self.assertEqual({r["case_id"] for r in marked}, {"new_00"})
        self.assertTrue(all(r["input_rebuilt"] for r in marked))
        self.assertEqual(analysis["masking_token_exception_rating_slots"], 72)
        self.assertEqual(analysis["masking_token_amendment"], values[new_dir / "token_amendment_20260909.json"])
        self.assertIn("4,000（原截断重试 8,000）→ 本案获准 16,000", markdown)
        self.assertIn("累计 dispatch 上限 7，不是重新清零", markdown)
        self.assertIn("保留原 4 次外层尝试记录", markdown)
        self.assertIn("评分 prompt、模型、参数、评分标准、解析与扣分规则保持不变", markdown)
        self.assertIn("不代表全部脱敏参数完全相同", markdown)

    def test_no_token_amendment_does_not_invent_an_exception(self):
        old_dir, new_dir, values = self.complete_fixture(with_amendment=False)
        with patch.object(report.Snapshot, "read", side_effect=lambda path, default=None, **_: values.get(Path(path), default)):
            analysis, markdown = report.build_report(old_dir, new_dir)
        self.assertEqual(analysis["masking_token_amendment"], {})
        self.assertEqual(analysis["masking_token_exception_rating_slots"], 0)
        self.assertIsNone(analysis["masking_token_amendment_path"])
        self.assertNotIn("经授权的脱敏参数例外", markdown)
        self.assertNotIn("本案获准 16,000", markdown)

    def test_terminal_scores_with_stale_input_state_do_not_claim_all108_complete(self):
        old_dir, new_dir, values = self.complete_fixture()
        values[new_dir / "state.json"]["mask_groups_ready"] = 95
        with patch.object(report.Snapshot, "read", side_effect=lambda path, default=None, **_: values.get(Path(path), default)):
            analysis, markdown = report.build_report(old_dir, new_dir)
        self.assertFalse(analysis["completed_mutual_evaluation_attempts"])
        self.assertNotIn("108 案已完成互评尝试", markdown)
        self.assertIn("输入准备状态尚未核对齐全", markdown)


if __name__ == "__main__":
    unittest.main()
