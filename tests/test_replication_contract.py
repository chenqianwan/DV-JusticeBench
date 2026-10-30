import unittest
from unittest.mock import patch

import requests

from scripts.run_experiment1_smoke_xhub import (
    LegacyDeepSeekV32Adapter,
    MODEL_CONDITIONS,
    SCORER_OPERATIONAL_ID,
    XHubClient,
    analysis_messages,
)
from utils.evaluator import AnswerEvaluator


class FakeClient:
    def __init__(self):
        self.calls = []

    def chat(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "ok": True,
            "content": "answer",
            "reasoning_content": "thinking",
            "response_id": "response-id",
            "response_model": kwargs["model"],
            "system_fingerprint": None,
            "finish_reason": "stop",
            "usage": {
                "prompt_tokens": 1,
                "completion_tokens": 1,
                "reasoning_tokens": 1,
                "total_tokens": 2,
            },
            "attempt_receipts": [],
            "latency_seconds": 0,
            "retry_count": 0,
            "error": "",
        }


class ReplicationContractTests(unittest.TestCase):
    def test_answer_prompt_is_the_legacy_prompt_verbatim(self):
        self.assertEqual(
            analysis_messages("CASE", "QUESTION"),
            [
                {
                    "role": "system",
                    "content": "你是一位专业的法律专家，擅长分析法律案例并提供专业的法律意见。",
                },
                {
                    "role": "user",
                    "content": """请作为法律专家分析以下案例，并回答相关问题。

案例内容：
CASE

问题：QUESTION

请提供详细的法律分析，包括：
1. 案件事实梳理
2. 法律适用分析
3. 判决建议
4. 法律依据

请用中文回答。""",
                },
            ],
        )

    def test_deepseek_conditions_share_v32_and_use_thinking_switch(self):
        deepseek = [item for item in MODEL_CONDITIONS if item.label.startswith("DeepSeek")]
        self.assertEqual([item.operational_model_id for item in deepseek], ["deepseek-v3.2", "deepseek-v3.2"])
        self.assertEqual(SCORER_OPERATIONAL_ID, "deepseek-v3.2")

    def test_v32_adapter_preserves_legacy_generation_parameters(self):
        client = FakeClient()
        adapter = LegacyDeepSeekV32Adapter(client)
        adapter.analyze_case("CASE", "QUESTION", use_thinking=True)
        call = client.calls[-1]
        self.assertEqual(call["model"], "deepseek-v3.2")
        self.assertEqual(call["temperature"], 0.3)
        self.assertEqual(call["max_tokens"], 3000)
        self.assertIs(call["thinking"], True)

    def test_masking_adapter_defaults_to_non_thinking(self):
        client = FakeClient()
        adapter = LegacyDeepSeekV32Adapter(client)
        adapter._make_request([{"role": "user", "content": "MASK"}], temperature=0.3, max_tokens=4000)
        call = client.calls[-1]
        self.assertEqual(call["model"], "deepseek-v3.2")
        self.assertEqual(call["temperature"], 0.3)
        self.assertEqual(call["max_tokens"], 4000)
        self.assertIs(call["thinking"], False)

    def test_default_error_parser_is_legacy(self):
        self.assertEqual(AnswerEvaluator().flag_parser_version, "legacy")

    def test_truncation_uses_the_historical_second_round(self):
        class FakeResponse:
            status_code = 200
            headers = {}

            def __init__(self, finish_reason, content):
                self.finish_reason = finish_reason
                self.content = content

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "id": "id",
                    "model": "gpt-4o",
                    "choices": [{
                        "finish_reason": self.finish_reason,
                        "message": {"content": self.content},
                    }],
                    "usage": {},
                }

        responses = [
            FakeResponse("length", "partial"),
            FakeResponse("stop", "complete"),
        ]
        client = XHubClient("key", "https://example.invalid", timeout=180)
        with patch(
            "scripts.run_experiment1_smoke_xhub.requests.post",
            side_effect=responses,
        ) as mocked_post:
            result = client.chat(
                model="gpt-4o",
                messages=[{"role": "user", "content": "x"}],
                max_tokens=3000,
                temperature=0.3,
                stage="generation",
            )

        self.assertTrue(result["ok"])
        self.assertEqual(mocked_post.call_count, 2)
        self.assertEqual(mocked_post.call_args_list[0].kwargs["json"]["max_tokens"], 3000)
        self.assertEqual(mocked_post.call_args_list[1].kwargs["json"]["max_tokens"], 6000)
        self.assertEqual(
            [receipt["retry_round"] for receipt in result["attempt_receipts"]],
            [1, 2],
        )

    def test_http_failures_use_three_attempts_and_one_two_second_backoff(self):
        class ErrorResponse:
            status_code = 500
            headers = {}

            def raise_for_status(self):
                raise requests.HTTPError("500", response=self)

        client = XHubClient("key", "https://example.invalid", timeout=180)
        with patch(
            "scripts.run_experiment1_smoke_xhub.requests.post",
            side_effect=[ErrorResponse(), ErrorResponse(), ErrorResponse()],
        ) as mocked_post, patch(
            "scripts.run_experiment1_smoke_xhub.time.sleep"
        ) as mocked_sleep:
            result = client.chat(
                model="gpt-4o",
                messages=[{"role": "user", "content": "x"}],
                max_tokens=3000,
                temperature=0.3,
                stage="generation",
            )

        self.assertFalse(result["ok"])
        self.assertEqual(mocked_post.call_count, 3)
        self.assertEqual([call.args[0] for call in mocked_sleep.call_args_list], [1, 2])

    def test_429_obeys_retry_after(self):
        class RateLimitedResponse:
            status_code = 429
            headers = {"Retry-After": "7"}
            text = "rate limited"

            def json(self):
                return {"error": {"message": "rate limited"}}

        class SuccessResponse:
            status_code = 200
            headers = {}

            def raise_for_status(self):
                return None

            def json(self):
                return {
                    "id": "id",
                    "model": "gpt-4o",
                    "choices": [{
                        "finish_reason": "stop",
                        "message": {"content": "complete"},
                    }],
                    "usage": {},
                }

        client = XHubClient("key", "https://example.invalid", timeout=180)
        with patch(
            "scripts.run_experiment1_smoke_xhub.requests.post",
            side_effect=[RateLimitedResponse(), SuccessResponse()],
        ), patch(
            "scripts.run_experiment1_smoke_xhub.time.sleep"
        ) as mocked_sleep:
            result = client.chat(
                model="gpt-4o",
                messages=[{"role": "user", "content": "x"}],
                max_tokens=3000,
                temperature=0.3,
                stage="generation",
            )

        self.assertTrue(result["ok"])
        mocked_sleep.assert_called_once_with(7)

    def test_deepseek_empty_content_gets_three_additional_attempts(self):
        class EmptyThenSuccessClient:
            def __init__(self):
                self.calls = []
                self.contents = ["", "", "", "answer"]

            def chat(self, **kwargs):
                self.calls.append(kwargs)
                content = self.contents.pop(0)
                return {
                    "ok": True,
                    "content": content,
                    "reasoning_content": "thinking",
                    "response_id": "response-id",
                    "response_model": kwargs["model"],
                    "system_fingerprint": None,
                    "finish_reason": "stop",
                    "usage": {},
                    "attempt_receipts": [],
                    "latency_seconds": 0,
                    "retry_count": 0,
                    "error": "",
                }

        client = EmptyThenSuccessClient()
        adapter = LegacyDeepSeekV32Adapter(client)
        with patch(
            "scripts.run_experiment1_smoke_xhub.time.sleep"
        ) as mocked_sleep:
            result = adapter.analyze_case("CASE", "QUESTION", use_thinking=True)

        self.assertEqual(result["answer"], "answer")
        self.assertEqual(len(client.calls), 4)
        self.assertEqual([call.args[0] for call in mocked_sleep.call_args_list], [2, 2])


if __name__ == "__main__":
    unittest.main()
