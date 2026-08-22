#!/usr/bin/env python3
"""Run the Experiment-1 one-case smoke test through the AI_Council xhub route.

The script reads the xhub credential at runtime, but never copies or serializes it.
It intentionally records both the historical model identifier and the operational
identifier used for this smoke run so that substitutions cannot be mistaken for
replication results.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import inspect
import json
import os
import statistics
import sys
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import pandas as pd
import requests
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.data_masking import DataMaskerAPI
from utils.evaluator import AnswerEvaluator


DEFAULT_CASE_ID = "case_20260103_155150_4"
DEFAULT_XHUB_ENV = Path(
    os.environ.get("XHUB_ENV_FILE", str(ROOT.parent / "AI_Council" / ".env"))
).expanduser()
DEFAULT_QUESTION_FILE = ROOT / "data/108个案例_新标准评估_完整版_最终版.xlsx"
DEFAULT_CASE_FILE = ROOT / "data/cases/cases.json"


@dataclass(frozen=True)
class ModelCondition:
    label: str
    historical_model_id: str
    operational_model_id: str
    substitution_reason: str = ""

    @property
    def is_substitution(self) -> bool:
        return self.historical_model_id != self.operational_model_id


MODEL_CONDITIONS = [
    ModelCondition("GPT-4o", "gpt-4o", "gpt-4o"),
    ModelCondition("Gemini 2.5 Flash", "gemini-2.5-flash", "gemini-2.5-flash"),
    ModelCondition("Qwen-Max", "qwen-max", "qwen-max"),
    ModelCondition(
        "DeepSeek thinking",
        "deepseek-reasoner",
        "deepseek-v3.2",
        "历史别名已退役；使用 deepseek-v3.2 并显式开启 thinking 复刻推理条件。",
    ),
    ModelCondition(
        "DeepSeek non-thinking",
        "deepseek-chat",
        "deepseek-v3.2",
        "历史别名已退役；使用 deepseek-v3.2 并显式关闭 thinking 复刻非推理条件。",
    ),
]

SCORER_HISTORICAL_ID = "deepseek-reasoner"
SCORER_OPERATIONAL_ID = "deepseek-v3.2"


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def safe_error(response: requests.Response) -> str:
    try:
        body = response.json()
        if isinstance(body, dict):
            error = body.get("error") or {}
            if isinstance(error, dict) and error.get("message"):
                return str(error["message"])
    except Exception:
        pass
    return response.text[:500]


def usage_dict(body: Dict[str, Any]) -> Dict[str, int]:
    usage = body.get("usage") or {}
    completion_details = usage.get("completion_tokens_details") or {}
    reasoning = (
        completion_details.get("reasoning_tokens")
        or usage.get("reasoning_tokens")
        or 0
    )
    return {
        "prompt_tokens": int(usage.get("prompt_tokens") or 0),
        "completion_tokens": int(usage.get("completion_tokens") or 0),
        "reasoning_tokens": int(reasoning),
        "total_tokens": int(usage.get("total_tokens") or 0),
    }


def add_usage(target: Dict[str, int], addition: Dict[str, int]) -> None:
    for key in ("prompt_tokens", "completion_tokens", "reasoning_tokens", "total_tokens"):
        target[key] = target.get(key, 0) + int(addition.get(key, 0))


def pipeline_call_snapshot(result: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Keep an auditable receipt without duplicating full answer/reasoning text."""
    if not result:
        return None
    content = str(result.get("content") or "")
    reasoning = str(result.get("reasoning_content") or "")
    return {
        "ok": bool(result.get("ok")),
        "stage": result.get("stage"),
        "requested_model": result.get("requested_model"),
        "response_model": result.get("response_model"),
        "response_id": result.get("response_id"),
        "request_id": result.get("request_id"),
        "system_fingerprint": result.get("system_fingerprint"),
        "finish_reason": result.get("finish_reason"),
        "latency_seconds": result.get("latency_seconds"),
        "retry_count": result.get("retry_count"),
        "usage": result.get("usage") or {},
        "attempt_receipts": result.get("attempt_receipts") or [],
        "error": result.get("error") or "",
        "content_chars": len(content),
        "content_sha256": sha256_text(content) if content else None,
        "reasoning_chars": len(reasoning),
        "reasoning_sha256": sha256_text(reasoning) if reasoning else None,
    }


class XHubClient:
    def __init__(self, api_key: str, base_url: str, timeout: float = 300.0):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def list_models(self) -> Dict[str, Any]:
        started = time.perf_counter()
        response = requests.get(
            f"{self.base_url}/models",
            headers=self.headers,
            timeout=min(self.timeout, 60),
        )
        latency = time.perf_counter() - started
        if response.status_code != 200:
            return {
                "ok": False,
                "http_status": response.status_code,
                "error": safe_error(response),
                "latency_seconds": round(latency, 3),
                "models": [],
            }
        body = response.json()
        ids = sorted(str(item.get("id", "")) for item in body.get("data", []))
        return {
            "ok": True,
            "http_status": response.status_code,
            "error": "",
            "latency_seconds": round(latency, 3),
            "models": ids,
        }

    def chat(
        self,
        *,
        model: str,
        messages: List[Dict[str, str]],
        max_tokens: int,
        temperature: Optional[float],
        stage: str,
        thinking: Optional[bool] = None,
        max_attempts: int = 3,
        auto_retry_on_truncate: bool = True,
    ) -> Dict[str, Any]:
        attempt_receipts: List[Dict[str, Any]] = []
        accumulated_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }
        current_max_tokens = max_tokens
        total_latency = 0.0
        last_error = ""

        # Historical clients used two truncation rounds.  Each round made up to
        # three HTTP attempts, waiting 1s/2s after request failures.  A first-round
        # token truncation doubled max_tokens (capped at 16k) and started round 2.
        for retry_round in range(2):
            truncated = False
            payload: Dict[str, Any] = {
                "model": model,
                "messages": messages,
                "max_tokens": current_max_tokens,
            }
            if temperature is not None:
                payload["temperature"] = temperature
            if thinking is not None:
                payload["thinking"] = {
                    "type": "enabled" if thinking else "disabled"
                }

            for attempt in range(1, max_attempts + 1):
                receipt_base = {
                    "retry_round": retry_round + 1,
                    "attempt": attempt,
                    "requested_model": model,
                    "max_tokens": current_max_tokens,
                    "temperature": temperature,
                    "thinking": thinking,
                }
                started = time.perf_counter()
                try:
                    response = requests.post(
                        f"{self.base_url}/chat/completions",
                        headers=self.headers,
                        json=payload,
                        timeout=self.timeout,
                    )
                    latency = time.perf_counter() - started

                    if response.status_code == 429:
                        total_latency += latency
                        last_error = safe_error(response)
                        attempt_receipts.append(
                            {
                                **receipt_base,
                                "http_status": response.status_code,
                                "latency_seconds": round(latency, 3),
                                "error": last_error,
                            }
                        )
                        retry_after = int(response.headers.get("Retry-After", 60))
                        time.sleep(retry_after)
                        continue

                    response.raise_for_status()
                    body = response.json()
                    total_latency += latency
                except requests.RequestException as exc:
                    latency = time.perf_counter() - started
                    total_latency += latency
                    last_error = f"{type(exc).__name__}: {exc}"
                    attempt_receipts.append(
                        {
                            **receipt_base,
                            "http_status": getattr(getattr(exc, "response", None), "status_code", None),
                            "latency_seconds": round(latency, 3),
                            "error": last_error,
                        }
                    )
                    if attempt < max_attempts:
                        time.sleep(attempt)
                        continue
                    break

                call_usage = usage_dict(body)
                add_usage(accumulated_usage, call_usage)
                choices = body.get("choices") or []
                choice = choices[0] if choices else {}
                message = choice.get("message") or {}
                content = str(message.get("content") or "")
                reasoning = str(message.get("reasoning_content") or "")
                finish_reason = str(choice.get("finish_reason") or "")
                attempt_receipts.append(
                    {
                        **receipt_base,
                        "http_status": response.status_code,
                        "latency_seconds": round(latency, 3),
                        "request_id": response.headers.get("x-request-id") or body.get("id"),
                        "response_id": body.get("id"),
                        "response_model": body.get("model"),
                        "system_fingerprint": body.get("system_fingerprint"),
                        "finish_reason": finish_reason,
                        "usage": call_usage,
                        "content_chars": len(content),
                        "reasoning_chars": len(reasoning),
                        "error": "",
                    }
                )

                truncated = finish_reason in {"length", "max_tokens"}
                if truncated and auto_retry_on_truncate and retry_round == 0:
                    current_max_tokens = min(current_max_tokens * 2, 16000)
                    break

                return {
                    "ok": True,
                    "stage": stage,
                    "content": content,
                    "reasoning_content": reasoning,
                    "requested_model": model,
                    "response_model": body.get("model"),
                    "response_id": body.get("id"),
                    "request_id": response.headers.get("x-request-id") or body.get("id"),
                    "system_fingerprint": body.get("system_fingerprint"),
                    "finish_reason": finish_reason,
                    "latency_seconds": round(total_latency, 3),
                    "retry_count": max(0, len(attempt_receipts) - 1),
                    "usage": accumulated_usage,
                    "attempt_receipts": attempt_receipts,
                    "error": "",
                }

            if truncated and retry_round == 0:
                continue
            break

        return {
            "ok": False,
            "stage": stage,
            "content": "",
            "reasoning_content": "",
            "requested_model": model,
            "response_model": None,
            "response_id": None,
            "request_id": None,
            "system_fingerprint": None,
            "finish_reason": "",
            "latency_seconds": round(total_latency, 3),
            "retry_count": max(0, len(attempt_receipts) - 1),
            "usage": accumulated_usage,
            "attempt_receipts": attempt_receipts,
            "error": last_error or "Unknown xhub error",
        }


class LegacyDeepSeekV32Adapter:
    """Expose the old DeepSeek API surface while routing to xhub deepseek-v3.2."""

    provider = "deepseek"

    def __init__(self, client: XHubClient):
        self.client = client
        self.last_call: Optional[Dict[str, Any]] = None
        self.calls: List[Dict[str, Any]] = []

    def _make_request(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 2000,
        auto_retry_on_truncate: bool = True,
        use_thinking: bool = False,
    ) -> Dict[str, Any]:
        result = self.client.chat(
            model="deepseek-v3.2",
            messages=messages,
            max_tokens=max_tokens,
            temperature=temperature,
            thinking=use_thinking,
            stage="legacy_deepseek",
            auto_retry_on_truncate=auto_retry_on_truncate,
        )
        self.last_call = result
        self.calls.append(result)
        if not result["ok"]:
            raise RuntimeError(result["error"])
        return {
            "id": result.get("response_id"),
            "model": result.get("response_model"),
            "system_fingerprint": result.get("system_fingerprint"),
            "choices": [
                {
                    "message": {
                        "content": result["content"],
                        "reasoning_content": result.get("reasoning_content", ""),
                    },
                    "finish_reason": result.get("finish_reason", ""),
                }
            ],
            "usage": result.get("usage", {}),
        }

    def analyze_case(
        self,
        case_text: str,
        question: Optional[str] = None,
        use_thinking: bool = True,
    ) -> Dict[str, str]:
        request_kwargs = {
            "model": "deepseek-v3.2",
            "messages": analysis_messages(case_text, question),
            "max_tokens": 3000,
            "temperature": 0.3,
            "thinking": use_thinking,
            "stage": "scoring" if question is None else "generation",
        }
        result = self.client.chat(**request_kwargs)
        self.last_call = result
        self.calls.append(result)
        if not result["ok"]:
            raise RuntimeError(result["error"])

        # DeepSeekAPI.analyze_case historically retried an empty visible answer
        # up to three additional times, with 2s pauses between failed retries.
        if not result["content"].strip():
            for empty_retry in range(1, 4):
                result = self.client.chat(**request_kwargs)
                self.last_call = result
                self.calls.append(result)
                if result["ok"] and result["content"].strip():
                    break
                if empty_retry < 3:
                    time.sleep(2)
            if not result["ok"]:
                raise RuntimeError(result["error"])
            if not result["content"].strip():
                raise RuntimeError("API返回content为空，重试3次后仍失败")
        return {
            "answer": result["content"],
            "thinking": result.get("reasoning_content", ""),
        }


def analysis_messages(case_text: str, question: Optional[str]) -> List[Dict[str, str]]:
    if question:
        prompt = f"""请作为法律专家分析以下案例，并回答相关问题。

案例内容：
{case_text}

问题：{question}

请提供详细的法律分析，包括：
1. 案件事实梳理
2. 法律适用分析
3. 判决建议
4. 法律依据

请用中文回答。"""
    else:
        prompt = f"""请作为法律专家分析以下案例。

案例内容：
{case_text}

请提供详细的法律分析，包括：
1. 案件事实梳理
2. 法律适用分析
3. 判决建议
4. 法律依据

请用中文回答。"""
    return [
        {
            "role": "system",
            "content": "你是一位专业的法律专家，擅长分析法律案例并提供专业的法律意见。",
        },
        {"role": "user", "content": prompt},
    ]


def nonempty_error_count(rows: Iterable[Dict[str, Any]], level: str) -> int:
    return sum(bool((row.get("evaluation") or {}).get("错误详情", {}).get(level)) for row in rows)


def fmt_number(value: Optional[float], digits: int = 2) -> str:
    return "N/A" if value is None else f"{value:.{digits}f}"


def md_escape(value: Any) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def build_markdown(result: Dict[str, Any]) -> str:
    rows = result["rows"]
    metadata = result["metadata"]
    input_info = result["input"]
    summaries = result["summaries"]
    all_generated = sum(item["generation_success"] for item in summaries)
    all_scored = sum(item["scoring_success"] for item in summaries)
    condition_count = len(metadata["model_conditions"])
    expected_total = input_info["question_count"] * condition_count
    overall_status = "PASS" if all_generated == expected_total and all_scored == expected_total else "PARTIAL"
    masking_scope = input_info.get("masking_scope", "shared")
    if masking_scope == "per-condition":
        masking_expected = condition_count
        masking_note = "每个模型条件独立调用一次旧 DataMaskerAPI 流程；每个条件内5题共享"
    else:
        masking_expected = 1
        masking_note = "五个模型条件共享同一份脱敏输入"

    lines = [
        "# 实验 1：1-case 五条件历史流水线复刻冒烟报告",
        "",
        f"> 执行时间：{metadata['executed_at']}",
        f"> xhub endpoint：`{metadata['endpoint_host']}`",
        f"> case：`{input_info['case_id']}`（{input_info['case_title']}）",
        f"> 规模：1 case × 5 questions × {condition_count} conditions；不含 GPT-5 和 Claude Opus",
        f"> 冒烟判定：**{overall_status}**",
        "",
        "## 1. 结论",
        "",
        f"本次通过 AI_Council 的 xhub 路由完成 **{all_generated}/{expected_total}** 份模型回答和 **{all_scored}/{expected_total}** 份 `deepseek-v3.2` thinking 自动评分。",
        "",
    ]
    if metadata.get("postprocessed_at"):
        lines.extend(
            [
                f"原始评分文本于 {metadata['postprocessed_at']} 使用修正后的错误标记解析器重新计算；该步骤没有再次调用 API。修正内容是避免把“无重大错误”和错误区块后的补充分析误计为重大错误。",
                "",
            ]
        )
    substitutions = [item for item in metadata["model_conditions"] if item["historical_model_id"] != item["operational_model_id"]]
    if substitutions:
        lines.extend(
            [
                "该结果按旧流水线的 prompt、采样参数、DeepSeek API 脱敏、评分 prompt、legacy 错误解析与扣分、整题重试和失败记 0 语义执行；退役的 DeepSeek 别名统一映射为 `deepseek-v3.2` 的 thinking 开关。",
                "",
            ]
        )
    lines.extend(
        [
            "## 2. 分阶段状态",
            "",
            "| 阶段 | 预期 | 实际 | 状态 | 说明 |",
            "|---|---:|---:|---|---|",
            f"| case 与问题加载 | 1 case / 5 questions | 1 / {input_info['question_count']} | **PASS** | 5 个问题全部非空且唯一 |",
            f"| DeepSeek API 脱敏与输入冻结 | {masking_expected} 份条件输入 | SHA-256 `{input_info['input_hash'][:16]}…` | **PASS** | `deepseek-v3.2` non-thinking；{masking_note} |",
            f"| xhub 模型目录 | {condition_count} 个条件 / {len(set(item['operational_model_id'] for item in metadata['model_conditions']))} 个 operational IDs | {metadata['catalog_present_count']}/{condition_count} | **{'PASS' if metadata['catalog_present_count'] == condition_count else 'PARTIAL'}** | 两个 DeepSeek 条件共享 ID、用 thinking 开关区分 |",
            f"| 回答生成 | {expected_total} | {all_generated} | **{'PASS' if all_generated == expected_total else 'PARTIAL'}** | 保持旧流程：整题三次重试；最终失败写 0 分 |",
            f"| 自动评分 | {all_generated} | {all_scored} | **{'PASS' if all_scored == all_generated else 'PARTIAL'}** | 评分器：`{metadata['scorer_operational_id']}` |",
            f"| 聚合报告 | {condition_count} 个条件 | {len(summaries)} | **PASS** | 同时保留题级 JSON 和运行 receipt |",
            "",
            "## 2.1 请求与处理契约",
            "",
            "| 阶段/条件 | model | temperature | max_tokens | thinking | 固定实现 |",
            "|---|---|---:|---:|---|---|",
            "| 标题、案情、裁判文书脱敏 | `deepseek-v3.2` | 0.3 | 4000 | disabled | 旧 `DataMaskerAPI` prompt |",
            "| GPT-4o 回答 | `gpt-4o` | 0.3 | 3000 | 未传 | 旧回答 prompt |",
            "| Gemini 回答 | `gemini-2.5-flash` | 0.3 | 3000 | 未传 | 旧回答 prompt |",
            "| Qwen 回答 | `qwen-max` | 0.3 | 3000 | 未传 | 旧回答 prompt |",
            "| DeepSeek thinking 回答 | `deepseek-v3.2` | 0.3 | 3000 | enabled | 旧回答 prompt |",
            "| DeepSeek non-thinking 回答 | `deepseek-v3.2` | 0.3 | 3000 | disabled | 旧回答 prompt |",
            "| 自动评分 | `deepseek-v3.2` | 0.3 | 3000 | enabled | 旧评分 prompt + legacy 解析/扣分 |",
            "",
            "未显式设置 `top_p`、`top_k`、`seed`、frequency penalty 或 presence penalty；由 API 使用默认值。HTTP timeout 固定为 180 秒。",
            "",
            "## 3. 模型映射与调用状态",
            "",
            "| 条件 | 历史 ID | 本次 operational ID | 替换 | 生成 | 评分 | 响应 model |",
            "|---|---|---|---|---:|---:|---|",
        ]
    )
    summary_by_label = {item["condition"]: item for item in summaries}
    for condition in metadata["model_conditions"]:
        summary = summary_by_label[condition["label"]]
        lines.append(
            "| {label} | `{historical}` | `{operational}` | {substitution} | {gen}/5 | {score}/5 | `{responses}` |".format(
                label=md_escape(condition["label"]),
                historical=md_escape(condition["historical_model_id"]),
                operational=md_escape(condition["operational_model_id"]),
                substitution="是（仅冒烟）" if condition["historical_model_id"] != condition["operational_model_id"] else "否",
                gen=summary["generation_success"],
                score=summary["scoring_success"],
                responses=md_escape(", ".join(summary["observed_response_models"]) or "N/A"),
            )
        )
    lines.extend(
        [
            "",
            "## 4. 条件级结果",
            "",
            "| 条件 | 平均分 /20 | 最低–最高 | 微小错误题 | 明显错误题 | 重大错误题 | 生成 tokens | 评分 tokens | 平均生成延迟(s) |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for summary in summaries:
        score_range = "N/A" if summary["min_score"] is None else f"{summary['min_score']:.2f}–{summary['max_score']:.2f}"
        lines.append(
            f"| {md_escape(summary['condition'])} | {fmt_number(summary['mean_score'])} | {score_range} | {summary['minor_error_questions']} | {summary['moderate_error_questions']} | {summary['major_error_questions']} | {summary['generation_total_tokens']} | {summary['scoring_total_tokens']} | {fmt_number(summary['mean_generation_latency_seconds'])} |"
        )

    lines.extend(
        [
            "",
            "## 5. 五道题的总分矩阵",
            "",
            "| 条件 | Q1 | Q2 | Q3 | Q4 | Q5 | 平均 |",
            "|---|---:|---:|---:|---:|---:|---:|",
        ]
    )
    for condition in metadata["model_conditions"]:
        condition_rows = sorted(
            [row for row in rows if row["condition"] == condition["label"]],
            key=lambda row: row["question_number"],
        )
        scores = [
            (row.get("evaluation") or {}).get("总分")
            for row in condition_rows
        ]
        valid_scores = [float(score) for score in scores if score is not None]
        score_cells = [fmt_number(float(score)) if score is not None else "N/A" for score in scores]
        mean_score = statistics.mean(valid_scores) if valid_scores else None
        lines.append(
            f"| {md_escape(condition['label'])} | {' | '.join(score_cells)} | {fmt_number(mean_score)} |"
        )

    lines.extend(
        [
            "",
            "## 6. Token 与运行开销",
            "",
            "| 项目 | 数值 |",
            "|---|---:|",
            f"| 回答生成 prompt tokens | {result['totals']['generation_prompt_tokens']} |",
            f"| 回答生成 completion tokens | {result['totals']['generation_completion_tokens']} |",
            f"| 其中 generation reasoning tokens | {result['totals']['generation_reasoning_tokens']} |",
            f"| 自动评分 prompt tokens | {result['totals']['scoring_prompt_tokens']} |",
            f"| 自动评分 completion tokens | {result['totals']['scoring_completion_tokens']} |",
            f"| 其中 scoring reasoning tokens | {result['totals']['scoring_reasoning_tokens']} |",
            f"| API 累计延迟（所有请求求和） | {result['totals']['api_latency_seconds']:.2f} s |",
            "| 费用 | xhub 响应不返回计费金额；以 xhub 账户账单为准 |",
            "",
            "## 7. 失败与重试",
            "",
            "| 条件 | 问题 | 阶段 | 错误 |",
            "|---|---:|---|---|",
        ]
    )
    failed_rows = []
    for row in rows:
        if not row.get("generation", {}).get("ok"):
            failed_rows.append((row["condition"], row["question_number"], "生成", row["generation"].get("error", "")))
        elif not row.get("scoring", {}).get("ok"):
            failed_rows.append((row["condition"], row["question_number"], "评分", row["scoring"].get("error", "")))
    if failed_rows:
        for condition, question_number, stage, error in failed_rows:
            lines.append(f"| {md_escape(condition)} | Q{question_number} | {stage} | {md_escape(error)} |")
    else:
        lines.append("| — | — | — | 无失败 |")

    lines.extend(
        [
            "",
            "## 8. 解释边界与正式实验门禁",
            "",
            "- 本报告只证明历史流水线在当前 API 映射下能否工作；单个 case 不能用于模型排名。",
            "- `deepseek-reasoner` 与 `deepseek-chat` 均显式映射到 `deepseek-v3.2`，分别开启和关闭 thinking。",
            "- 为复刻历史统计语义，整题三次处理均失败时按旧代码写入 0 分；失败率仍须单独报告。",
            "- 当前复刻模式固定使用 legacy 错误解析器；修正版解析器不得混入历史对照结果。",
            "- 当前自动评分仍是单一 DeepSeek 家族评分器；论文结果需增加异家族敏感性评分和人工校准。",
            "- GPT-4o 官方模型页说明 snapshot 可用于锁定模型行为：<https://developers.openai.com/api/docs/models/gpt-4o>。正式实验应优先使用可用的固定 snapshot。",
            "",
            "## 9. 可复现文件",
            "",
            f"- 原始题级结果与 API receipts：`{metadata['raw_result_path']}`",
            f"- 输入 hash：`{input_info['input_hash']}`",
            f"- prompt 模板 hash：`{metadata['prompt_template_hash']}`",
            *(
                [f"- runner 源码 hash：`{metadata['runner_source_sha256']}`"]
                if metadata.get("runner_source_sha256") else []
            ),
            "- 报告和原始 JSON 均不包含 xhub API key。",
            "",
            f"**最终判定：`{overall_status}`。**",
            "",
        ]
    )
    return "\n".join(lines)


def summarize_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    summaries: List[Dict[str, Any]] = []
    for condition in MODEL_CONDITIONS:
        condition_rows = [row for row in rows if row["condition"] == condition.label]
        generated = [row for row in condition_rows if row.get("generation", {}).get("ok")]
        scored = [row for row in generated if row.get("scoring", {}).get("ok")]
        evaluated = [row for row in condition_rows if row.get("evaluation") is not None]
        scores = [float(row["evaluation"]["总分"]) for row in evaluated]
        response_models = sorted(
            {
                str(row["generation"].get("response_model"))
                for row in generated
                if row["generation"].get("response_model")
            }
        )
        summaries.append(
            {
                "condition": condition.label,
                "historical_model_id": condition.historical_model_id,
                "operational_model_id": condition.operational_model_id,
                "is_substitution": condition.is_substitution,
                "generation_success": len(generated),
                "scoring_success": len(scored),
                "mean_score": round(statistics.mean(scores), 2) if scores else None,
                "min_score": round(min(scores), 2) if scores else None,
                "max_score": round(max(scores), 2) if scores else None,
                "minor_error_questions": nonempty_error_count(evaluated, "微小错误"),
                "moderate_error_questions": nonempty_error_count(evaluated, "明显错误"),
                "major_error_questions": nonempty_error_count(evaluated, "重大错误"),
                "generation_total_tokens": sum(row["generation"]["usage"]["total_tokens"] for row in generated),
                "scoring_total_tokens": sum(row["scoring"]["usage"]["total_tokens"] for row in scored),
                "mean_generation_latency_seconds": round(
                    statistics.mean(row["generation"]["latency_seconds"] for row in generated), 2
                ) if generated else None,
                "observed_response_models": response_models,
            }
        )
    return summaries


def totals_from_rows(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    totals = {
        "generation_prompt_tokens": 0,
        "generation_completion_tokens": 0,
        "generation_reasoning_tokens": 0,
        "scoring_prompt_tokens": 0,
        "scoring_completion_tokens": 0,
        "scoring_reasoning_tokens": 0,
        "api_latency_seconds": 0.0,
    }
    for row in rows:
        generation = row.get("generation") or {}
        scoring = row.get("scoring") or {}
        generation_usage = generation.get("usage") or {}
        scoring_usage = scoring.get("usage") or {}
        totals["generation_prompt_tokens"] += int(generation_usage.get("prompt_tokens") or 0)
        totals["generation_completion_tokens"] += int(generation_usage.get("completion_tokens") or 0)
        totals["generation_reasoning_tokens"] += int(generation_usage.get("reasoning_tokens") or 0)
        totals["scoring_prompt_tokens"] += int(scoring_usage.get("prompt_tokens") or 0)
        totals["scoring_completion_tokens"] += int(scoring_usage.get("completion_tokens") or 0)
        totals["scoring_reasoning_tokens"] += int(scoring_usage.get("reasoning_tokens") or 0)
        totals["api_latency_seconds"] += float(generation.get("latency_seconds") or 0)
        totals["api_latency_seconds"] += float(scoring.get("latency_seconds") or 0)
    totals["api_latency_seconds"] = round(totals["api_latency_seconds"], 3)
    return totals


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--xhub-env", type=Path, default=DEFAULT_XHUB_ENV)
    parser.add_argument("--case-id", default=DEFAULT_CASE_ID)
    parser.add_argument("--question-file", type=Path, default=DEFAULT_QUESTION_FILE)
    parser.add_argument("--case-file", type=Path, default=DEFAULT_CASE_FILE)
    parser.add_argument(
        "--masking-scope",
        choices=("shared", "per-condition"),
        default="shared",
        help="shared freezes one mask for all conditions; per-condition reproduces the historical separate model jobs.",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument(
        "--workers",
        type=int,
        default=25,
        help="Legacy one-case concurrency: five questions for each of five model conditions.",
    )
    args = parser.parse_args()

    cfg = dotenv_values(args.xhub_env)
    api_key = str(cfg.get("UNIFIED_API_KEY") or "")
    base_url = str(cfg.get("UNIFIED_API_BASE_URL") or "").rstrip("/")
    if not api_key or not base_url:
        raise SystemExit("xhub env must define UNIFIED_API_KEY and UNIFIED_API_BASE_URL")

    with args.case_file.open("r", encoding="utf-8") as handle:
        cases = json.load(handle)
    if args.case_id not in cases:
        raise SystemExit(f"case not found: {args.case_id}")
    case = cases[args.case_id]

    frame = pd.read_excel(args.question_file)
    selected = frame[frame["案例ID"] == args.case_id].sort_values("问题编号")
    questions = selected["问题"].fillna("").astype(str).tolist()
    if len(questions) != 5 or any(not question.strip() for question in questions):
        raise SystemExit("selected case must contain exactly five non-empty questions")
    if len(set(questions)) != 5:
        raise SystemExit("selected case questions must be unique")

    client = XHubClient(api_key, base_url, timeout=180.0)
    raw_case_text = str(case.get("content", case.get("case_text", "")) or "")
    raw_judge = str(case.get("judge_decision", "") or "")

    def build_masked_input(masking_condition: str) -> Dict[str, Any]:
        print(f"[masking] start condition={masking_condition}", flush=True)
        masking_api = LegacyDeepSeekV32Adapter(client)
        masker = DataMaskerAPI.__new__(DataMaskerAPI)
        masker.api = masking_api
        masked = masker.mask_case_with_api(
            {
                "title": str(case.get("title") or ""),
                "case_text": raw_case_text,
                "judge_decision": raw_judge,
            }
        )
        masked_case_text = str(masked.get("case_text_masked") or "")
        masked_judge = str(masked.get("judge_decision_masked") or "")
        masked_title = str(masked.get("title_masked") or "")
        if not masked_case_text or not masked_judge:
            raise SystemExit(f"masking produced an empty case or judgment: {masking_condition}")
        input_hash = sha256_text(
            json.dumps(
                {
                    "case_id": args.case_id,
                    "masking_condition": masking_condition,
                    "masked_title": masked_title,
                    "masked_case_text": masked_case_text,
                    "masked_judge": masked_judge,
                    "questions": questions,
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        result = {
            "masking_condition": masking_condition,
            "masked_title": masked_title,
            "masked_case_text": masked_case_text,
            "masked_judgment": masked_judge,
            "masked_case_chars": len(masked_case_text),
            "masked_judgment_chars": len(masked_judge),
            "input_hash": input_hash,
            "masking_calls": masking_api.calls,
        }
        print(
            f"[masking] done condition={masking_condition} "
            f"case_chars={len(masked_case_text)} judgment_chars={len(masked_judge)}",
            flush=True,
        )
        return result

    if args.masking_scope == "per-condition":
        condition_inputs = {
            condition.label: build_masked_input(condition.label)
            for condition in MODEL_CONDITIONS
        }
    else:
        shared_input = build_masked_input("shared")
        condition_inputs = {
            condition.label: shared_input
            for condition in MODEL_CONDITIONS
        }

    unique_inputs = {
        item["masking_condition"]: item
        for item in condition_inputs.values()
    }
    input_hash = sha256_text(
        json.dumps(
            {
                label: item["input_hash"]
                for label, item in sorted(unique_inputs.items())
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    prompt_template_hash = sha256_text(
        json.dumps(
            {
                "analysis_template": analysis_messages("{CASE}", "{QUESTION}"),
                "masking_prompt_source": inspect.getsource(DataMaskerAPI.mask_text_with_api),
                "evaluation_prompt_source": inspect.getsource(AnswerEvaluator._call_evaluation_api),
                "legacy_flag_parser_source": inspect.getsource(AnswerEvaluator._detect_flags_legacy),
                "penalty_source": inspect.getsource(AnswerEvaluator._apply_penalty_for_flags),
                "scorer_historical_id": SCORER_HISTORICAL_ID,
                "scorer_operational_id": SCORER_OPERATIONAL_ID,
                "scorer_thinking": True,
                "temperature": 0.3,
                "max_tokens": 3000,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )

    catalog = client.list_models()
    catalog_ids = set(catalog.get("models") or [])
    catalog_present_count = sum(condition.operational_model_id in catalog_ids for condition in MODEL_CONDITIONS)
    print(
        f"[catalog] status={catalog.get('http_status')} "
        f"operational_ids={catalog_present_count}/{len(MODEL_CONDITIONS)}",
        flush=True,
    )

    rows: List[Dict[str, Any]] = []
    for condition in MODEL_CONDITIONS:
        condition_input = condition_inputs[condition.label]
        for question_number, question in enumerate(questions, 1):
            rows.append(
                {
                    "case_id": args.case_id,
                    "case_title": str(case.get("title") or ""),
                    "masked_title": condition_input["masked_title"],
                    "input_hash": condition_input["input_hash"],
                    "question_number": question_number,
                    "question": question,
                    "question_hash": sha256_text(question),
                    "condition": condition.label,
                    "historical_model_id": condition.historical_model_id,
                    "operational_model_id": condition.operational_model_id,
                    "is_substitution": condition.is_substitution,
                    "substitution_reason": condition.substitution_reason,
                }
            )

    condition_lookup = {condition.label: condition for condition in MODEL_CONDITIONS}

    def process_question(row: Dict[str, Any]) -> Dict[str, Any]:
        """Mirror the old per-question unit: generate, score, and retry both together."""
        condition = condition_lookup[row["condition"]]
        condition_input = condition_inputs[row["condition"]]
        masked_case_text = condition_input["masked_case_text"]
        masked_judge = condition_input["masked_judgment"]
        is_deepseek = condition.operational_model_id == "deepseek-v3.2"
        thinking = condition.label == "DeepSeek thinking" if is_deepseek else None
        last_generation: Optional[Dict[str, Any]] = None
        last_scoring: Optional[Dict[str, Any]] = None
        last_error = ""
        pipeline_attempt_history: List[Dict[str, Any]] = []

        for pipeline_attempt in range(1, 4):
            generation: Optional[Dict[str, Any]] = None
            scoring: Optional[Dict[str, Any]] = None
            try:
                generation = client.chat(
                    model=condition.operational_model_id,
                    messages=analysis_messages(masked_case_text, row["question"]),
                    max_tokens=3000,
                    temperature=0.3,
                    thinking=thinking,
                    stage="generation",
                )
                last_generation = generation
                if not generation["ok"]:
                    raise RuntimeError(generation["error"])
                if not generation["content"].strip():
                    raise RuntimeError("AI回答为空（answer长度=0字符）")

                scoring_api = LegacyDeepSeekV32Adapter(client)
                evaluator = AnswerEvaluator(
                    api=scoring_api,
                    flag_parser_version="legacy",
                )
                evaluation = evaluator.evaluate_answer(
                    ai_answer=generation["content"],
                    judge_decision=masked_judge,
                    question=row["question"],
                    case_text=masked_case_text,
                )
                scoring = scoring_api.last_call
                if not scoring or not scoring["ok"]:
                    raise RuntimeError((scoring or {}).get("error", "scoring returned no receipt"))
                last_scoring = scoring
                pipeline_attempt_history.append(
                    {
                        "pipeline_attempt": pipeline_attempt,
                        "status": "success",
                        "error": "",
                        "generation": pipeline_call_snapshot(generation),
                        "scoring": pipeline_call_snapshot(scoring),
                    }
                )
                return {
                    "condition": row["condition"],
                    "question_number": row["question_number"],
                    "generation": generation,
                    "scoring": scoring,
                    "evaluation": evaluation,
                    "pipeline_attempt": pipeline_attempt,
                    "pipeline_attempt_history": pipeline_attempt_history,
                    "processing_error": "",
                }
            except Exception as exc:
                last_error = str(exc)
                pipeline_attempt_history.append(
                    {
                        "pipeline_attempt": pipeline_attempt,
                        "status": "failed",
                        "error": last_error,
                        "generation": pipeline_call_snapshot(generation),
                        "scoring": pipeline_call_snapshot(scoring),
                    }
                )
                if pipeline_attempt < 3:
                    time.sleep(2)

        empty_usage = {
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "reasoning_tokens": 0,
            "total_tokens": 0,
        }
        generation = last_generation or {
            "ok": False,
            "stage": "generation",
            "content": "",
            "reasoning_content": "",
            "requested_model": condition.operational_model_id,
            "response_model": None,
            "latency_seconds": 0,
            "retry_count": 0,
            "usage": empty_usage.copy(),
            "attempt_receipts": [],
            "error": last_error,
        }
        scoring = last_scoring or {
            "ok": False,
            "stage": "scoring",
            "content": "",
            "reasoning_content": "",
            "requested_model": SCORER_OPERATIONAL_ID,
            "response_model": None,
            "latency_seconds": 0,
            "retry_count": 0,
            "usage": empty_usage.copy(),
            "attempt_receipts": [],
            "error": last_error,
        }
        zero_scores = {
            dimension: 0.0 for dimension in AnswerEvaluator(flag_parser_version="legacy").scoring_criteria
        }
        return {
            "condition": row["condition"],
            "question_number": row["question_number"],
            "generation": generation,
            "scoring": scoring,
            "evaluation": {
                "总分": 0.0,
                "百分制": 0.0,
                "各维度得分": zero_scores,
                "详细评价": f"处理失败：{last_error}（已重试3次）",
                "错误标记": "",
                "错误详情": {"微小错误": [], "明显错误": [], "重大错误": []},
                "分档": "处理失败",
            },
            "pipeline_attempt": 3,
            "pipeline_attempt_history": pipeline_attempt_history,
            "processing_error": f"{last_error}（已重试3次）",
        }

    completed = 0
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {executor.submit(process_question, row): row for row in rows}
        for future in concurrent.futures.as_completed(futures):
            item = future.result()
            target = next(
                row for row in rows
                if row["condition"] == item["condition"] and row["question_number"] == item["question_number"]
            )
            target.update(item)
            completed += 1
            score_value = (item.get("evaluation") or {}).get("总分")
            print(
                f"[pipeline {completed}/{len(rows)}] {item['condition']} Q{item['question_number']} "
                f"generation={item['generation']['ok']} scoring={item['scoring']['ok']} "
                f"attempt={item['pipeline_attempt']} score={score_value}",
                flush=True,
            )

    rows.sort(key=lambda row: ([item.label for item in MODEL_CONDITIONS].index(row["condition"]), row["question_number"]))
    args.output_dir.mkdir(parents=True, exist_ok=True)
    raw_result_path = args.output_dir / "raw_results.json"
    executed_at = datetime.now(ZoneInfo("Asia/Hong_Kong")).isoformat(timespec="seconds")
    result: Dict[str, Any] = {
        "metadata": {
            "executed_at": executed_at,
            "endpoint_host": urlparse(base_url).hostname,
            "case_file": str(args.case_file.resolve()),
            "question_file": str(args.question_file.resolve()),
            "model_conditions": [asdict(condition) for condition in MODEL_CONDITIONS],
            "scorer_historical_id": SCORER_HISTORICAL_ID,
            "scorer_operational_id": SCORER_OPERATIONAL_ID,
            "catalog_check": {key: value for key, value in catalog.items() if key != "models"},
            "catalog_present_count": catalog_present_count,
            "prompt_template_hash": prompt_template_hash,
            "runner_source_sha256": sha256_text(Path(__file__).read_text(encoding="utf-8")),
            "raw_result_path": str(raw_result_path.resolve()),
            "credential_serialized": False,
            "flag_parser_version": "legacy",
            "masking_model": "deepseek-v3.2",
            "masking_thinking": False,
            "masking_scope": args.masking_scope,
            "masking_calls": [
                {
                    **call,
                    "masking_condition": masking_condition,
                }
                for masking_condition, masked_input in unique_inputs.items()
                for call in masked_input["masking_calls"]
            ],
        },
        "input": {
            "case_id": args.case_id,
            "case_title": str(case.get("title") or ""),
            "raw_case_chars": len(raw_case_text),
            "raw_judgment_chars": len(raw_judge),
            "masking_scope": args.masking_scope,
            "masking_run_count": len(unique_inputs),
            "question_count": len(questions),
            "input_hash": input_hash,
            "condition_inputs": unique_inputs,
            "questions": questions,
        },
        "rows": rows,
    }
    result["summaries"] = summarize_rows(rows)
    result["totals"] = totals_from_rows(rows)

    raw_result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(build_markdown(result), encoding="utf-8")
    print(f"[done] raw={raw_result_path}", flush=True)
    print(f"[done] report={args.report}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
