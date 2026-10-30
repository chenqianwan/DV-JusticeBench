"""Offline regression tests for the separately budgeted historical-20 supplement.

All HTTP transport is mocked; files created by tests are confined to temporary
directories. The historical input/output files are never edited.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext, redirect_stderr
import copy
import io
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from scripts.cross_judge_budget_guard import BudgetStop
from scripts import run_cross_judging_original20_rebuilt_xhub as runner


def successful_part(raw, field, value=None):
    value = value if value is not None else "某男1、某女1的法律文本"
    return dict(field=field, status="success", source_sha256=runner.sha256_text(raw),
                value=value, value_sha256=runner.sha256_text(value), dispatch_count=1,
                calls=[dict(ok=True, finish_reason="stop")])


def synthetic_source():
    ids = ["case_20251230_101232_5"] + [f"case_fixture_{n}" for n in range(1, 20)]
    raw = {cid: dict(case_id=cid, title="标题", case_text="原始事实", judge_decision="原始裁判") for cid in ids}
    answers = []
    for cid in ids:
        for condition, family in runner.ANSWER_FAMILY.items():
            for qn in range(1, 6):
                question, answer = f"问题 {qn}", f"法律回答 {qn}"
                aid = f"{cid}::q{qn}::{condition}"
                failure = aid == "case_20251230_101232_5::q1::Qwen-Max"
                answers.append(dict(answer_id=aid, case_id=cid, question_number=qn,
                                    question=question, answer=answer,
                                    answer_condition=condition, answer_family=family,
                                    question_sha256=runner.sha256_text(question),
                                    answer_sha256=runner.sha256_text(answer),
                                    input_hash=None, masked_case_sha256=None, masked_judgment_sha256=None,
                                    source_answer_is_processing_error=failure))
    return dict(case_ids=ids, answers=answers), raw


class ClassificationTests(unittest.TestCase):
    def setUp(self):
        self.task = dict(task_id="rating_test", answer_id="answer_test", judge_model_id="gpt-4o",
                         **{key: "frozen_" + key for key in runner.HASH_KEYS})

    def record(self, **changes):
        return dict(self.task, **changes)

    def test_known_text_refusal_is_na_even_when_legacy_total_is_zero(self):
        record = self.record(status="success", evaluation={"总分": 0}, scoring={
            "content": "The prompt could not be submitted. The prompt contains sensitive words."})
        status, value, _ = runner.classify(self.task, record, [])
        self.assertEqual((status, value), ("refused", None))
        self.assertEqual(record["evaluation"]["总分"], 0)

    def test_true_zero_is_valid_and_success_overrides_old_refusal_evidence(self):
        record = self.record(status="success", evaluation={"总分": 0, "详细评价": "规范依据相关性：0分"})
        evidence = [dict(model="gpt-4o", messages_sha256=self.task["messages_sha256"], error_code="content_filter")]
        self.assertEqual(runner.classify(self.task, record, evidence)[:2], ("valid", 0))

    def test_http_400_requires_model_and_frozen_messages_evidence(self):
        record = self.record(status="failed", error="HTTPError: 400 Client Error")
        evidence = dict(model="gpt-4o", messages_sha256=self.task["messages_sha256"], error_code="content_filter")
        self.assertEqual(runner.classify(self.task, record, [evidence])[:2], ("refused", None))
        for wrong in [dict(evidence, model="qwen-max"), dict(evidence, messages_sha256="other"),
                      dict(evidence, error_code="invalid_request")]:
            self.assertEqual(runner.classify(self.task, record, [wrong])[0], "failed")
        self.assertEqual(runner.classify(self.task, record, [])[0], "failed")

    def test_transport_failed_remains_retryable_and_nonfinite_not_valid(self):
        for error in runner.TRANSPORT_ERRORS:
            self.assertEqual(runner.classify(self.task, self.record(status="failed", error=error), [])[0], "transport_failed")
        for value in [float("nan"), float("inf"), True, None]:
            record = self.record(status="success", evaluation={"总分": value})
            self.assertNotEqual(runner.classify(self.task, record, [])[0], "valid")

    def test_resume_checks_every_input_hash_before_classification(self):
        record = self.record(status="success", evaluation={"总分": 10})
        for key in ("task_id", "answer_id", *runner.HASH_KEYS):
            changed = dict(record, **{key: "changed"})
            with self.subTest(key=key), self.assertRaisesRegex(RuntimeError, "mismatch"):
                runner.classify(self.task, changed, [])

    def test_unchanged_legacy_task_sends_frozen_messages_and_parameters(self):
        answer = dict(answer="旧模型回答保持不变", masked_judgment="脱敏裁判", question="原问题", masked_case_text="脱敏事实")
        text = "\n".join(f"【{dimension}】得分：2分" for dimension in runner.legacy.AnswerEvaluator(api=object()).scoring_criteria)
        expected_hash = runner.json_hash(runner.payload_for(answer))
        for label, judge in runner.JUDGES.items():
            client = Mock()
            client.chat.return_value = dict(ok=True, content=text, reasoning_content="", usage={}, finish_reason="stop")
            task = dict(self.task, judge_label=label, judge_model_id=judge["model_id"], messages_sha256=expected_hash)
            result = runner.run_new_task(task, answer, client)
            self.assertEqual(result["status"], "success")
            client.chat.assert_called_once()
            sent = client.chat.call_args.kwargs
            self.assertEqual(runner.json_hash(sent["messages"]), expected_hash)
            self.assertEqual((sent["temperature"], sent["max_tokens"]), (.3, 3000))
            self.assertEqual((sent["model"], sent["thinking"]), (judge["model_id"], judge["thinking"]))


class MaskAndAssemblyTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.raw = dict(title="原标题", case_text="原始事实", judge_decision="原始裁判")
        self.guard = Mock()
        self.guard.evidence.return_value = []
        self.guard.stopped = threading.Event()
        self.guard.scope.side_effect = lambda _: nullcontext()
        self.stop = threading.Event()

    def save(self, field, record):
        path = runner.mask_path(self.out, "case_test", "GPT-4o", field)
        runner.atomic_write_json(path, record)
        return path

    def test_valid_cached_parts_skip_all_api_calls(self):
        for field, text in self.raw.items():
            self.save(field, successful_part(text, field))
        with patch.object(runner.DataMaskerAPI, "mask_text_with_api") as mask:
            self.assertTrue(runner.run_mask_group(self.out, "case_test", "GPT-4o", self.raw, Mock(), self.guard, self.stop))
            mask.assert_not_called()

    def test_successful_cache_corruption_is_not_regenerated(self):
        record = successful_part(self.raw["title"], "title")
        record["value"] = "被外部修改的值"
        path = self.save("title", record)
        before = path.read_bytes()
        with patch.object(runner.DataMaskerAPI, "mask_text_with_api") as mask:
            with self.assertRaisesRegex(RuntimeError, "Frozen successful mask"):
                runner.run_mask_group(self.out, "case_test", "GPT-4o", self.raw, Mock(), self.guard, self.stop)
            mask.assert_not_called()
        self.assertEqual(path.read_bytes(), before)

    def test_cached_text_refusal_is_terminal_on_restart(self):
        record = dict(source_sha256=runner.sha256_text(self.raw["title"]), field="title", status="failed",
                      dispatch_count=1, value="The prompt could not be submitted. The prompt contains sensitive words")
        self.save("title", record)
        with patch.object(runner.DataMaskerAPI, "mask_text_with_api") as mask:
            self.assertFalse(runner.run_mask_group(self.out, "case_test", "GPT-4o", self.raw, Mock(), self.guard, self.stop))
            mask.assert_not_called()

    def test_empty_failed_or_truncated_mask_is_not_usable(self):
        for change in [dict(value=None), dict(value=""), dict(status="failed"),
                       dict(calls=[dict(ok=False)]), dict(calls=[dict(ok=True, finish_reason="length")])]:
            record = dict(successful_part("原始事实", "case_text"), **change)
            self.assertFalse(runner.valid_mask_part(record, "原始事实", "case_text"))
        self.assertFalse(runner.valid_mask_part(successful_part("old", "case_text"), "changed", "case_text"))

    def test_mask_failure_never_substitutes_original_text(self):
        self.save("title", successful_part(self.raw["title"], "title"))
        self.save("case_text", dict(field="case_text", source_sha256=runner.sha256_text(self.raw["case_text"]),
                                    status="failed", dispatch_count=2))
        with patch.object(runner.DataMaskerAPI, "mask_text_with_api", return_value=None) as mask:
            self.assertFalse(runner.run_mask_group(self.out, "case_test", "GPT-4o", self.raw, Mock(), self.guard, self.stop))
            mask.assert_called_once_with("原始事实", is_title=False)
        result = runner.load_json(runner.mask_path(self.out, "case_test", "GPT-4o", "case_text"))
        self.assertEqual(result["status"], "failed")
        self.assertIsNone(result["value"])
        self.assertEqual(result["dispatch_count"], 3)

    def test_499_old_answers_have_1497_new_cross_family_ratings(self):
        old, raw_cases = synthetic_source()
        original = copy.deepcopy(old)
        matrix = {c: [l for l, j in runner.JUDGES.items() if j["family"] != f] for c, f in runner.ANSWER_FAMILY.items()}
        runner.atomic_write_json(self.out / "plan.json", dict(judge_matrix=matrix))
        for cid in old["case_ids"]:
            for condition in runner.CONDITIONS:
                for field, text in raw_cases[cid].items():
                    if field != "case_id":
                        runner.atomic_write_json(runner.mask_path(self.out, cid, condition, field), successful_part(text, field))
        answers, tasks, groups = runner.assembled_sources(self.out, old, raw_cases)
        self.assertEqual((len(answers), len(tasks), groups), (499, 1497, 100))
        self.assertEqual(old, original)
        self.assertEqual(len({t["task_id"] for t in tasks}), 1497)
        self.assertTrue(all(t["source"] == "new_api" and t["input_ready"] for t in tasks))
        self.assertTrue(all(t["answer_family"] != t["judge_family"] for t in tasks))
        self.assertTrue(all(t["messages_sha256"] for t in tasks))
        self.assertNotIn("case_20251230_101232_5::q1::Qwen-Max", {a["answer_id"] for a in answers})
        lookup = {a["answer_id"]: a for a in original["answers"]}
        for answer in answers:
            self.assertEqual(answer["question"], lookup[answer["answer_id"]]["question"])
            self.assertEqual(answer["answer"], lookup[answer["answer_id"]]["answer"])
        self.assertEqual(sum(t["judge_family"] == "DeepSeek" for t in tasks), 299)


class FrozenSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.out = self.base / "supplement"
        self.old_dir = self.base / "old88"
        self.source = self.old_dir / "original20_answers.json"
        self.corpus = self.base / "corpus.json"
        self.old, self.raw = synthetic_source()
        runner.atomic_write_json(self.source, self.old)
        runner.atomic_write_json(self.corpus, self.raw)
        runner.atomic_write_json(self.old_dir / "manifest_ready88.json", dict(hashes=runner.frozen_hashes()))
        runner.atomic_write_json(self.old_dir / "market_snapshot.json", dict(quota_per_unit=500000))
        self.patches = [patch.object(runner, "OLD", self.old_dir), patch.object(runner, "SOURCE", self.source),
                        patch.object(runner, "CORPUS", self.corpus)]
        for p in self.patches:
            p.start()
            self.addCleanup(p.stop)

    def test_prepare_preserves_originals_and_restart_rejects_changed_source(self):
        protected = {path: path.read_bytes() for path in [self.source, self.corpus,
                     self.old_dir / "manifest_ready88.json", self.old_dir / "market_snapshot.json"]}
        plan, _, _ = runner.prepare(self.out)
        saved = (self.out / "plan.json").read_bytes()
        repeated, _, _ = runner.prepare(self.out)
        self.assertEqual(plan, repeated)
        self.assertEqual((self.out / "plan.json").read_bytes(), saved)
        self.assertTrue(all(path.read_bytes() == data for path, data in protected.items()))
        changed = copy.deepcopy(self.old)
        changed["answers"][0]["answer"] += "来源已修改"
        changed["answers"][0]["answer_sha256"] = runner.sha256_text(changed["answers"][0]["answer"])
        runner.atomic_write_json(self.source, changed)
        with self.assertRaisesRegex(AssertionError, "Frozen supplement plan/source changed"):
            runner.prepare(self.out)
        self.assertEqual((self.out / "plan.json").read_bytes(), saved)

    def test_cli_refuses_original_run_as_output_before_any_preparation(self):
        with patch("sys.argv", ["runner", "--output-dir", str(self.old_dir)]), patch.object(runner, "prepare") as prepare, redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit):
                runner.main()
            prepare.assert_not_called()
        self.assertFalse((self.old_dir / "plan.json").exists())

    def test_score_retry_archives_prior_attempt_without_touching_source(self):
        answer = copy.deepcopy(self.old["answers"][1])
        task = dict(task_id="rating_fixture", answer_id=answer["answer_id"], input_hash="frozen-input")
        old_record = dict(task, status="failed", error="ProxyError")
        new_record = dict(task, status="success", evaluation={"总分": 0})
        path = self.out / "ratings" / "rating_fixture.json"
        runner.atomic_write_json(path, old_record)
        guard = Mock()
        guard.scope.side_effect = lambda _: nullcontext()
        guard.evidence.return_value = []
        original_source = self.source.read_bytes()
        with patch.object(runner, "run_new_task", return_value=copy.deepcopy(new_record)) as scoring:
            result = runner.score_worker(self.out, task, answer, Mock(), guard)
            self.assertEqual(scoring.call_count, 1)
        self.assertEqual(result["evaluation"]["总分"], 0)
        archives = list((self.out / "prior_attempts").glob("rating_fixture_*.json"))
        self.assertEqual(len(archives), 1)
        self.assertEqual(runner.load_json(archives[0]), old_record)
        self.assertEqual(runner.load_json(self.out / "dispatches/rating_fixture.json")["count"], 1)
        self.assertEqual(self.source.read_bytes(), original_source)


class ScopedGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        balance = Mock()
        balance.json.return_value = {"data": {"total_used": 1000}}
        self.get = patch("scripts.cross_judge_budget_guard.requests.get", return_value=balance).start()
        self.post = patch("scripts.cross_judge_budget_guard.requests.post").start()
        self.addCleanup(patch.stopall)
        self.payload = dict(model="gpt-4o", max_tokens=1000, messages=[dict(role="user", content="prompt")])

    def guard(self, budget=40):
        return runner.ScopedGuard("https://unit.test/v1", "test-secret", self.out,
                                  {"gpt-4o": (2.5, 10)}, 500000, budget, prepaid_usd=0)

    def response(self, code=200, body=None):
        result = Mock(status_code=code, text="response")
        result.json.return_value = body if body is not None else dict(usage=dict(prompt_tokens=100, completion_tokens=200))
        return result

    def test_task_scopes_are_thread_local_durable_and_payloads_unchanged(self):
        guard = self.guard()
        self.post.return_value = self.response()
        barrier = threading.Barrier(2)
        def call(scope):
            payload = dict(self.payload, messages=[dict(role="user", content=scope)])
            with guard.scope(scope):
                barrier.wait(timeout=5)
                guard.post("https://unit.test/v1/chat/completions", json=payload)
            return payload
        with ThreadPoolExecutor(max_workers=2) as pool:
            payloads = list(pool.map(call, ["mask::one", "rating::two"]))
        self.assertEqual(self.post.call_count, 2)
        sent = [c.kwargs["json"] for c in self.post.call_args_list]
        self.assertCountEqual(sent, payloads)
        self.assertEqual(guard.snapshot()["prepaid_usd"], 0)
        self.assertEqual(guard.snapshot()["budget_usd"], 40)
        self.assertAlmostEqual(guard.snapshot()["estimated_all_http_usd"], .0045)
        restarted = self.guard()
        for payload in payloads:
            scope = payload["messages"][0]["content"]
            evidence = restarted.evidence(scope)
            self.assertEqual(len(evidence), 1)
            self.assertEqual(evidence[0]["messages_sha256"], runner.json_hash(payload["messages"]))
            receipt = runner.load_json(Path(evidence[0]["path"]))
            self.assertEqual(receipt["work_scope"], scope)
        self.assertEqual(restarted.snapshot()["next_id"], 3)

    def test_refusal_receipt_attribution_non_dict_response_and_no_key_serialization(self):
        guard = self.guard()
        self.post.side_effect = [self.response(400, dict(error=dict(code="content_filter"))), self.response(500, ["unavailable"])]
        with guard.scope("rating::blocked"):
            guard.post("https://unit.test/v1/chat/completions", json=self.payload,
                       headers={"Authorization": "Bearer test-secret"})
        with guard.scope("rating::broken"):
            guard.post("https://unit.test/v1/chat/completions", json=self.payload)
        self.assertEqual(guard.evidence("rating::blocked")[0]["error_code"], "content_filter")
        self.assertIsNone(guard.evidence("rating::broken")[0]["error_code"])
        self.assertEqual(guard.snapshot()["completed_http_attempts"], 2)
        self.assertNotIn("test-secret", "".join(path.read_text() for path in self.out.rglob("*.json")))

    def test_budget_stop_prevents_http_and_cannot_expand_on_resume(self):
        guard = self.guard(budget=.000001)
        with guard.scope("rating::bounded"), self.assertRaises(BudgetStop):
            guard.post("https://unit.test/v1/chat/completions", json=self.payload)
        self.post.assert_not_called()
        self.assertTrue(self.guard(budget=.000001).stopped.is_set())
        with self.assertRaises(BudgetStop):
            self.guard(budget=40)


if __name__ == "__main__":
    unittest.main()
