"""Offline tests for the one-case 16k masking exception; no paid calls."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
import copy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock

from scripts import run_cross_judging_original20_rebuilt_xhub as runner


def reply(finish_reason="stop"):
    return dict(ok=True, content="某男1与某女1的脱敏法律文本", finish_reason=finish_reason,
                reasoning_content="", usage={})


def successful(raw, field):
    value = "既有成功脱敏文本"
    return dict(field=field, status="success", dispatch_count=1,
                source_sha256=runner.sha256_text(raw), value=value,
                value_sha256=runner.sha256_text(value), calls=[reply()])


class TokenAmendmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.out = Path(self.temp.name)
        self.cid = runner.TOKEN_AMENDMENT_CASE
        self.raw = dict(title="原始标题", case_text="原始事实文本", judge_decision="原始裁判文本")
        runner.atomic_write_json(self.out / "plan.json", dict(frozen="original protocol", budget_usd=40))
        runner.atomic_write_json(self.out / "raw_cases_snapshot.json", {self.cid: self.raw})
        for condition in runner.CONDITIONS:
            self.save(condition, "title", successful(self.raw["title"], "title"))
            self.save(condition, "case_text", self.failed_part(condition))
        self.guard = Mock()
        self.guard.evidence.return_value = []
        self.guard.stopped = threading.Event()
        self.guard.scope.side_effect = lambda _: nullcontext()
        self.guard.recovery_plan = {"mask_limits": {
            self.scope(condition): 4 for condition in runner.CONDITIONS}}
        self.stop = Mock()
        self.stop.is_set.return_value = False

    def scope(self, condition):
        return f"mask::{self.cid}::{condition}::case_text"

    def failed_part(self, condition, count=4):
        return dict(case_id=self.cid, condition=condition, field="case_text", status="failed",
                    dispatch_count=count, source_sha256=runner.sha256_text(self.raw["case_text"]),
                    calls=[reply("length")], value="截断文本")

    def save(self, condition, field, record, cid=None):
        path = runner.mask_path(self.out, cid or self.cid, condition, field)
        runner.atomic_write_json(path, record)
        return path

    def authorize(self):
        self.guard.token_amendment = runner.prepare_token_amendment(self.out)
        return self.guard.token_amendment

    def test_authorization_exact_scope_preserves_plan_and_failed_records(self):
        protected = {p: p.read_bytes() for p in [self.out / "plan.json", *self.out.glob("mask_parts/*/*/*.json")]}
        amendment = self.authorize()
        self.assertEqual(amendment["scopes"], [self.scope(c) for c in runner.CONDITIONS])
        self.assertEqual(amendment["max_tokens"], 16000)
        self.assertEqual(set(amendment["dispatch_limits"].values()), {7})
        self.assertEqual(len(amendment["evidence"]), 5)
        self.assertTrue(all(e["before_dispatch_count"] == 4 for e in amendment["evidence"]))
        self.assertTrue(all(p.read_bytes() == data for p, data in protected.items()))
        self.assertEqual(set(self.guard.recovery_plan["mask_limits"].values()), {4})

    def test_authorization_persists_fixed_seven_after_progress_or_success(self):
        amendment = self.authorize()
        frozen = (self.out / runner.TOKEN_AMENDMENT_FILE).read_bytes()
        self.save("GPT-4o", "case_text", self.failed_part("GPT-4o", count=6))
        self.save("Qwen-Max", "case_text", successful(self.raw["case_text"], "case_text"))
        self.assertEqual(runner.prepare_token_amendment(self.out), amendment)
        self.assertEqual((self.out / runner.TOKEN_AMENDMENT_FILE).read_bytes(), frozen)
        self.assertEqual(set(amendment["dispatch_limits"].values()), {7})

    def test_saved_authorization_rejects_scope_case_hash_path_or_parameter_tampering(self):
        amendment = self.authorize()
        path = self.out / runner.TOKEN_AMENDMENT_FILE
        for key, value in [
            ("case_id", "other_case"), ("field", "judge_decision"),
            ("scopes", ["mask::other_case::GPT-4o::case_text"]),
            ("dispatch_limits", {scope: 8 for scope in amendment["scopes"]}),
            ("plan_sha256", "not-frozen"), ("output_dir", "/different/run"),
            ("max_tokens", 32000), ("authorization", "not the user quote"),
        ]:
            with self.subTest(key=key):
                runner.atomic_write_json(path, dict(amendment, **{key: value}))
                with self.assertRaisesRegex(RuntimeError, "does not match"):
                    runner.prepare_token_amendment(self.out)
        runner.atomic_write_json(path, amendment)
        runner.atomic_write_json(self.out / "plan.json", dict(frozen="modified"))
        with self.assertRaisesRegex(RuntimeError, "does not match"):
            runner.prepare_token_amendment(self.out)

    def test_creation_requires_all_five_fourth_dispatch_truncations_and_source_hash(self):
        original = self.failed_part("GPT-4o")
        for changes in [dict(status="success"), dict(dispatch_count=3), dict(dispatch_count=5),
                        dict(source_sha256="different"), dict(calls=[reply("stop")])]:
            with self.subTest(changes=changes):
                self.save("GPT-4o", "case_text", dict(original, **changes))
                with self.assertRaisesRegex(RuntimeError, "fourth-dispatch truncated"):
                    runner.prepare_token_amendment(self.out)
                self.assertFalse((self.out / runner.TOKEN_AMENDMENT_FILE).exists())
        self.save("GPT-4o", "case_text", original)
        self.authorize()

    def test_only_target_facts_16k_judgment_4k_titles_cached_and_prompt_unchanged(self):
        self.authorize()
        client = Mock()
        client.chat.side_effect = lambda **_: reply()
        originals = {runner.mask_path(self.out, self.cid, c, "title"):
                     runner.mask_path(self.out, self.cid, c, "title").read_bytes()
                     for c in runner.CONDITIONS}
        for condition in runner.CONDITIONS:
            self.assertTrue(runner.run_mask_group(self.out, self.cid, condition, self.raw,
                                                 client, self.guard, self.stop))
        sent = [c.kwargs for c in client.chat.call_args_list]
        self.assertEqual([c["max_tokens"] for c in sent], [16000, 4000] * 5)
        for field_index, field in enumerate(("case_text", "judge_decision")):
            baseline_client = Mock()
            baseline_client.chat.return_value = reply()
            masker = runner.DataMaskerAPI.__new__(runner.DataMaskerAPI)
            masker.api = runner.legacy.LegacyDeepSeekV32Adapter(baseline_client)
            masker.mask_text_with_api(self.raw[field], is_title=False)
            baseline = baseline_client.chat.call_args.kwargs
            for actual in sent[field_index::2]:
                expected = dict(baseline, max_tokens=16000 if field == "case_text" else 4000)
                self.assertEqual(actual, expected)
        self.assertTrue(all(p.read_bytes() == data for p, data in originals.items()))
        first_count = client.chat.call_count
        for condition in runner.CONDITIONS:
            self.assertTrue(runner.run_mask_group(self.out, self.cid, condition, self.raw,
                                                 client, self.guard, self.stop))
        self.assertEqual(client.chat.call_count, first_count)
        self.assertEqual(len(list((self.out / "prior_attempts").glob("mask_*.json"))), 5)

    def test_non_target_case_all_fields_remain_4k(self):
        self.authorize()
        client = Mock()
        client.chat.return_value = reply()
        self.assertTrue(runner.run_mask_group(self.out, "different_case", "GPT-4o", self.raw,
                                             client, self.guard, self.stop))
        self.assertEqual([c.kwargs["max_tokens"] for c in client.chat.call_args_list], [4000] * 3)

    def test_exhausted_attempts_resume_without_reset_or_judgment_dispatch(self):
        self.authorize()
        client = Mock()
        client.chat.return_value = reply("length")
        for _ in range(2):
            self.guard.token_amendment = runner.prepare_token_amendment(self.out)
            self.assertFalse(runner.run_mask_group(self.out, self.cid, "GPT-4o", self.raw,
                                                  client, self.guard, self.stop))
        self.assertEqual(client.chat.call_count, 3)
        self.assertEqual([c.kwargs["max_tokens"] for c in client.chat.call_args_list], [16000] * 3)
        current = runner.load_json(runner.mask_path(self.out, self.cid, "GPT-4o", "case_text"))
        self.assertEqual((current["dispatch_count"], current["status"]), (7, "failed"))
        archived = [runner.load_json(p)["dispatch_count"] for p in (self.out / "prior_attempts").glob("*.json")]
        self.assertEqual(sorted(archived), [4, 5, 6])
        self.assertFalse(runner.mask_path(self.out, self.cid, "GPT-4o", "judge_decision").exists())

    def test_parallel_target_and_non_target_requests_never_mutate_shared_client(self):
        self.authorize()
        client = Mock()
        client.chat.side_effect = lambda **_: reply()
        with ThreadPoolExecutor(max_workers=2) as pool:
            target = pool.submit(runner.run_mask_group, self.out, self.cid, "GPT-4o", self.raw,
                                 client, self.guard, self.stop)
            other = pool.submit(runner.run_mask_group, self.out, "other_case", "GPT-4o", self.raw,
                                client, self.guard, self.stop)
            self.assertTrue(target.result())
            self.assertTrue(other.result())
        tokens = [c.kwargs["max_tokens"] for c in client.chat.call_args_list]
        self.assertEqual(tokens.count(16000), 1)
        self.assertEqual(tokens.count(4000), 4)
        self.assertNotIn("max_tokens", client.__dict__)

    def test_successful_cache_corruption_is_not_regenerated_under_amendment(self):
        self.authorize()
        part = successful(self.raw["case_text"], "case_text")
        part["value"] = "externally changed without hash update"
        self.save("GPT-4o", "case_text", part)
        client = Mock()
        with self.assertRaisesRegex(RuntimeError, "Frozen successful mask"):
            runner.run_mask_group(self.out, self.cid, "GPT-4o", self.raw, client, self.guard, self.stop)
        client.chat.assert_not_called()


class OverrideClientTests(unittest.TestCase):
    def test_wrapper_changes_only_token_ceiling_and_does_not_mutate_input(self):
        client = Mock()
        client.chat.return_value = reply()
        wrapper = runner.MaskTokenOverrideClient(client, 16000)
        kwargs = dict(model="deepseek-v3.2", max_tokens=4000, temperature=.3, thinking=False,
                      stage="legacy_deepseek", auto_retry_on_truncate=True,
                      messages=[{"role": "user", "content": "unchanged prompt"}])
        original = copy.deepcopy(kwargs)
        wrapper.chat(**kwargs)
        self.assertEqual(kwargs, original)
        self.assertEqual(client.chat.call_args.kwargs, dict(original, max_tokens=16000))

    def test_wrapper_rejects_scorer_or_changed_model_parameter_before_http(self):
        client = Mock()
        wrapper = runner.MaskTokenOverrideClient(client, 16000)
        kwargs = dict(model="deepseek-v3.2", max_tokens=4000, temperature=.3, thinking=False,
                      stage="legacy_deepseek", messages=[])
        for changes in [dict(model="gpt-4o"), dict(max_tokens=3000), dict(temperature=.7),
                        dict(thinking=True), dict(thinking=None)]:
            with self.subTest(changes=changes), self.assertRaisesRegex(RuntimeError, "non-mask"):
                wrapper.chat(**dict(kwargs, **changes))
        client.chat.assert_not_called()


if __name__ == "__main__":
    unittest.main()
