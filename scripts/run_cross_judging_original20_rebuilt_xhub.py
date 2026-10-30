#!/usr/bin/env python3
"""Rebuild missing historical inputs, then cross-judge unchanged old answers.

This is a separately budgeted supplement, not a historical-input replay.
No question/answer generation, no edits to the old 88-case run, no DS score reuse.
The original masker, transport, evaluator, parsing and penalty methods are reused.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import contextmanager
import concurrent.futures as futures
import copy
import fcntl
import hashlib
import inspect
import json
import math
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from dotenv import dotenv_values
from scripts import run_experiment1_smoke_xhub as legacy
from scripts.cross_judge_budget_guard import GuardedTransport
from scripts.run_cross_family_judging_sample20_xhub import (
    ANSWER_FAMILY, JUDGES, atomic_write_json, file_sha256, load_json,
    now_iso, result_is_valid, run_new_task, sha256_text, task_id,
)
from scripts.run_cross_judging_full108_xhub import frozen_hashes
from scripts.pilot_cross_judging_budget_xhub import payload_for
from utils.data_masking import DataMaskerAPI

OUT = ROOT / "data/experiment1_cross_judge_original20_rebuilt_xhub_20260908"
OLD = ROOT / "data/experiment1_cross_judge_full108_xhub_20260908"
SOURCE = OLD / "original20_answers.json"
CORPUS = ROOT / "data/cases/cases.json"
CONDITIONS = list(ANSWER_FAMILY)
HASH_KEYS = ("input_hash", "question_sha256", "answer_sha256", "masked_case_sha256", "masked_judgment_sha256", "messages_sha256")
REFUSAL_CODES = {"content_filter", "prompt_blocked", "content_policy_violation", "safety_violation"}
REFUSAL_PREFIXES = (
    "the prompt could not be submitted. the prompt contains sensitive words",
    "the prompt could not be submitted. this prompt contains sensitive words",
)
BUDGET_AUTHORIZATION = "70以内都可以，跑完就好"
RECOVERY_FILE = "recovery_authorization_20260909.json"
TOKEN_AMENDMENT_FILE = "token_amendment_20260909.json"
TOKEN_AMENDMENT_CASE = "case_20251230_101232_5"
TRANSPORT_ERRORS = ("ProxyError", "ReadTimeout", "ConnectTimeout", "ConnectionError", "Timeout")
ALIASES = re.compile(r"某(?:男|女|人)[0-9一二三四五六七八九十]+|某[甲乙丙丁戊己庚辛壬癸]|[甲乙丙丁]某")


def json_hash(value):
    return sha256_text(json.dumps(value, ensure_ascii=False, sort_keys=True))


def safe_slug(condition):
    return re.sub(r"[^A-Za-z0-9_-]", "_", condition)


def emit(event, **fields):
    print(json.dumps(dict(event=event, time=now_iso(), **fields), ensure_ascii=False), flush=True)


def known_refusal(text):
    return str(text or "").strip().lower().startswith(REFUSAL_PREFIXES)


class ScopedGuard(GuardedTransport):
    """Add local task attribution to receipts without changing any HTTP payload."""

    def __init__(self, *args, **kwargs):
        self.work_local = threading.local()
        self.work_receipts = defaultdict(dict)
        super().__init__(*args, **kwargs)
        for path in self.receipts.glob("*.json"):
            receipt = load_json(path)
            self._index_receipt(receipt)

    def _index_receipt(self, receipt):
        work = receipt.get("work_scope")
        if work:
            response = receipt.get("response")
            error = (response.get("error") or {}) if isinstance(response, dict) else {}
            request = receipt.get("request")
            request = request if isinstance(request, dict) else {}
            self.work_receipts[work][receipt["call_id"]] = dict(
                call_id=receipt["call_id"], model=request.get("model"),
                messages_sha256=json_hash(request.get("messages", [])),
                http_status=receipt.get("http_status"), error_code=error.get("code") if isinstance(error, dict) else None,
                error=receipt.get("error"), path=str(self.receipts / (receipt["call_id"] + ".json")),
            )

    def _settle(self, call_id, receipt):
        scope = getattr(self.work_local, "scope", None)
        if scope and not receipt.get("work_scope"):
            receipt["work_scope"] = scope
            atomic_write_json(self.receipts / (call_id + ".json"), receipt)
        self._index_receipt(receipt)
        super()._settle(call_id, receipt)

    @contextmanager
    def scope(self, scope):
        old = getattr(self.work_local, "scope", None)
        self.work_local.scope = scope
        try:
            yield
        finally:
            self.work_local.scope = old

    def evidence(self, scope):
        with self.lock:
            return list(copy.deepcopy(self.work_receipts.get(scope, {})).values())


def prepare(out):
    old = load_json(SOURCE)
    corpus = load_json(CORPUS)
    answers = old["answers"]
    assert len(answers) == 500 and len(old["case_ids"]) == 20
    assert len({a["answer_id"] for a in answers}) == 500
    for a in answers:
        assert sha256_text(a["question"]) == a["question_sha256"]
        assert sha256_text(a["answer"]) == a["answer_sha256"]
    excluded = [a for a in answers if a["source_answer_is_processing_error"]]
    assert len(excluded) == 1
    assert excluded[0]["answer_id"] == "case_20251230_101232_5::q1::Qwen-Max"
    raw_cases = {}
    for cid in old["case_ids"]:
        raw = corpus[cid]
        raw_cases[cid] = dict(case_id=cid, title=str(raw.get("title") or ""),
                             case_text=str(raw.get("content") or raw.get("case_text") or ""),
                             judge_decision=str(raw.get("judge_decision") or ""))
        assert raw_cases[cid]["case_text"].strip() and raw_cases[cid]["judge_decision"].strip()
    current_hashes = frozen_hashes()
    baseline = load_json(OLD / "manifest_ready88.json")["hashes"]
    assert current_hashes == baseline, "Frozen scoring code changed"
    current_hashes.update(masking_prompt_source_sha256=sha256_text(inspect.getsource(DataMaskerAPI.mask_text_with_api)),
                          legacy_judge_task_source_sha256=sha256_text(inspect.getsource(run_new_task)),
                          legacy_http_source_sha256=sha256_text(inspect.getsource(legacy.XHubClient.chat)))
    market = load_json(OLD / "market_snapshot.json")
    plan = dict(schema_version=1, input_provenance="rebuilt_20260908", budget_usd=40.0,
                case_ids=old["case_ids"], expected_cases=20, expected_answer_slots=500,
                expected_valid_answers=499, expected_ratings=1497, expected_mask_groups=100,
                masking_scope="per-condition", masking_fields=["title", "case_text", "judge_decision"],
                excluded_generation_failures=[dict(answer_id=a["answer_id"], case_id=a["case_id"],
                    question_number=a["question_number"], answer_condition=a["answer_condition"],
                    reason="Historical generation failed; retain missing, do not regenerate or rate an error string.") for a in excluded],
                source_files={str(SOURCE): file_sha256(SOURCE), str(CORPUS): file_sha256(CORPUS)},
                frozen_hashes=current_hashes, judge_matrix={c: [l for l,j in JUDGES.items() if j["family"] != f] for c,f in ANSWER_FAMILY.items()},
                parameters=dict(masking_model="deepseek-v3.2", masking_thinking=False,
                                masking_temperature=0.3, masking_max_tokens=4000,
                                scoring_temperature=0.3, scoring_max_tokens=3000,
                                per_task_legacy_retries="unchanged", terminal_refusals_not_redispatched=True,
                                max_transport_dispatches=3),
                note="Newly rebuilt inputs; all eligible judges freshly score them, no historical DS scores reused. Prior 88 files are read-only.")
    if (out / "plan.json").exists():
        assert load_json(out / "plan.json") == plan, "Frozen supplement plan/source changed"
        assert load_json(out / "source_snapshot.json") == old
        assert load_json(out / "raw_cases_snapshot.json") == raw_cases
    else:
        atomic_write_json(out / "source_snapshot.json", old)
        atomic_write_json(out / "raw_cases_snapshot.json", raw_cases)
        atomic_write_json(out / "market_snapshot.json", market)
        atomic_write_json(out / "plan.json", plan)
    return plan, old, raw_cases


def mask_path(out, cid, condition, field):
    return out / "mask_parts" / cid / safe_slug(condition) / (field + ".json")


def valid_mask_part(record, raw_text, field):
    if record.get("source_sha256") != sha256_text(raw_text) or record.get("field") != field:
        return False
    if record.get("status") != "success" or not isinstance(record.get("value"), str):
        return False
    if record.get("value_sha256") != sha256_text(record["value"]):
        return False
    if not raw_text and field == "title":
        return record["value"] == ""
    calls = record.get("calls") or []
    return bool(record["value"].strip() and not known_refusal(record["value"]) and calls and
                all(c.get("ok") for c in calls) and
                calls[-1].get("finish_reason") not in {"length", "max_tokens"} and
                record.get("value_sha256") == sha256_text(record["value"]))


def prepare_recovery(out, guard):
    """Freeze one extra dispatch for diagnosed failures; never reset counters."""
    path = out / RECOVERY_FILE
    plan_hash = file_sha256(out / "plan.json")
    if path.exists():
        recovery = load_json(path)
        if recovery.get("plan_sha256") != plan_hash or recovery.get("output_dir") != str(out.resolve()):
            raise RuntimeError("Recovery authorization does not match this frozen run")
        return recovery
    recovery = dict(schema_version=1, created_at=now_iso(), output_dir=str(out.resolve()),
                    plan_sha256=plan_hash, authorization="失败的重试尝试解决",
                    policy="One additional dispatch per diagnosed existing failure; protocol unchanged.",
                    mask_limits={}, rating_limits={}, evidence=[])
    for part_path in sorted((out / "mask_parts").glob("*/*/*.json")):
        part = load_json(part_path)
        if part.get("status") != "failed" or int(part.get("dispatch_count", 0)) < 3:
            continue
        scope = f"mask::{part['case_id']}::{part['condition']}::{part['field']}"
        if known_refusal(part.get("value")) or any(r.get("error_code") in REFUSAL_CODES for r in guard.evidence(scope)):
            continue
        calls = part.get("calls") or []
        truncated = any(c.get("finish_reason") in {"length", "max_tokens"} for c in calls)
        transport = any(any(token in str(c.get("error") or "") for token in TRANSPORT_ERRORS) for c in calls)
        if not (truncated or transport):
            continue
        recovery["mask_limits"][scope] = int(part["dispatch_count"]) + 1
        recovery["evidence"].append(dict(scope=scope, path=str(part_path), sha256=file_sha256(part_path),
                                          before_count=part["dispatch_count"], reason="truncated" if truncated else "transport_failed"))
    for task in load_json(out / "manifest.json")["tasks"]:
        path_rating = out / "ratings" / (task["task_id"] + ".json")
        record = load_json(path_rating) if path_rating.exists() else {}
        scope = "rating::" + task["task_id"]
        status, _, reason = classify(task, record, guard.evidence(scope))
        if not task.get("input_ready") or not (status == "transport_failed" or
                (status == "failed" and str(reason).startswith("BudgetStop:"))):
            continue
        dispatch_path = out / "dispatches" / (task["task_id"] + ".json")
        count = load_json(dispatch_path)["count"] if dispatch_path.exists() else 0
        recovery["rating_limits"][task["task_id"]] = count + 1
        recovery["evidence"].append(dict(scope=scope, path=str(path_rating), sha256=file_sha256(path_rating),
                                          before_count=count, reason=reason))
    atomic_write_json(path, recovery)
    return recovery


def recovery_limits(guard, key):
    return guard.__dict__.get("recovery_plan", {}).get(key, {})


def prepare_token_amendment(out):
    """Authorize 16k only for the five remaining facts masks, not the scorer."""
    path = out / TOKEN_AMENDMENT_FILE
    scopes = [f"mask::{TOKEN_AMENDMENT_CASE}::{condition}::case_text" for condition in CONDITIONS]
    expected = dict(schema_version=1, output_dir=str(out.resolve()), plan_sha256=file_sha256(out / "plan.json"),
                    case_id=TOKEN_AMENDMENT_CASE, field="case_text", max_tokens=16000,
                    previous_initial_max_tokens=4000, previous_retry_max_tokens=8000,
                    scopes=scopes, dispatch_limits={scope: 7 for scope in scopes},
                    authorization="提高token长度直接做那一条，就剩一个了")
    if path.exists():
        amendment = load_json(path)
        if any(amendment.get(key) != value for key, value in expected.items()):
            raise RuntimeError("Token amendment does not match its authorized case, scopes or frozen plan")
        return amendment
    raw = load_json(out / "raw_cases_snapshot.json")[TOKEN_AMENDMENT_CASE]["case_text"]
    evidence = []
    for condition, scope in zip(CONDITIONS, scopes):
        part_path = mask_path(out, TOKEN_AMENDMENT_CASE, condition, "case_text")
        part = load_json(part_path)
        if (part.get("status") != "failed" or part.get("dispatch_count") != 4 or
                part.get("source_sha256") != sha256_text(raw) or
                (part.get("calls") or [{}])[-1].get("finish_reason") not in {"length", "max_tokens"}):
            raise RuntimeError("16k amendment requires the diagnosed fourth-dispatch truncated facts masks")
        evidence.append(dict(scope=scope, previous_path=str(part_path), previous_sha256=file_sha256(part_path),
                             source_sha256=part["source_sha256"], before_dispatch_count=4))
    amendment = dict(expected, created_at=now_iso(), evidence=evidence,
                     note="Only these case_text HTTP max_tokens change; all prompts, title/judgment masks, scorer parameters and existing successes remain unchanged. Up to three dispatches under the new cap, old attempts retained.")
    atomic_write_json(path, amendment)
    return amendment


class MaskTokenOverrideClient:
    """A per-call wrapper; the shared scorer/transport are never mutated."""
    def __init__(self, client, max_tokens):
        self.client = client
        self.max_tokens = max_tokens

    def chat(self, **kwargs):
        if (kwargs.get("model") != "deepseek-v3.2" or kwargs.get("max_tokens") != 4000 or
                kwargs.get("temperature") != 0.3 or kwargs.get("thinking") is not False):
            raise RuntimeError("16k mask override received a non-mask request")
        return self.client.chat(**dict(kwargs, max_tokens=self.max_tokens))


def run_mask_group(out, cid, condition, raw, client, guard, stop):
    for field in ("title", "case_text", "judge_decision"):
        path = mask_path(out, cid, condition, field)
        previous = load_json(path) if path.exists() else {}
        raw_text = raw[field]
        if previous and previous.get("source_sha256") != sha256_text(raw_text):
            raise RuntimeError("Source differs from saved mask; refusing replacement")
        if valid_mask_part(previous, raw_text, field):
            continue
        if previous.get("status") == "success":
            raise RuntimeError("Frozen successful mask failed validation; do not overwrite or regenerate it")
        scope = f"mask::{cid}::{condition}::{field}"
        if known_refusal(previous.get("value")) or any(r.get("error_code") in REFUSAL_CODES for r in guard.evidence(scope)):
            return False
        attempt = int(previous.get("dispatch_count", 0))
        limit = recovery_limits(guard, "mask_limits").get(scope, 3)
        token_amendment = guard.__dict__.get("token_amendment", {})
        scoped_tokens = scope in token_amendment.get("scopes", [])
        if scoped_tokens:
            limit = token_amendment["dispatch_limits"][scope]
        while attempt < limit and not stop.is_set() and not guard.stopped.is_set():
            attempt += 1
            if previous:
                atomic_write_json(out / "prior_attempts" / f"mask_{sha256_text(scope)[:20]}_{time.time_ns()}.json", previous)
            record = dict(case_id=cid, condition=condition, field=field,
                          source_sha256=sha256_text(raw_text), dispatch_count=attempt,
                          status="in_progress", started_at=now_iso())
            if scoped_tokens:
                record.update(max_tokens_override=token_amendment["max_tokens"],
                              token_amendment_path=str(out / TOKEN_AMENDMENT_FILE))
            atomic_write_json(path, record)
            masking_client = MaskTokenOverrideClient(client, token_amendment["max_tokens"]) if scoped_tokens else client
            adapter = legacy.LegacyDeepSeekV32Adapter(masking_client)
            masker = DataMaskerAPI.__new__(DataMaskerAPI)
            masker.api = adapter
            with guard.scope(scope):
                value = masker.mask_text_with_api(raw_text, is_title=(field == "title"))
            record.update(value=value, value_sha256=sha256_text(value) if isinstance(value,str) else None,
                          calls=adapter.calls, status="success", completed_at=now_iso(),
                          receipt_evidence=guard.evidence(scope))
            if not valid_mask_part(record, raw_text, field):
                record["status"] = "failed"
                record["reason"] = "Masking failed/empty/refused/truncated; original text is never substituted."
            atomic_write_json(path, record)
            if record["status"] == "success":
                break
            if known_refusal(value) or any(r.get("error_code") in REFUSAL_CODES for r in guard.evidence(scope)):
                return False
            previous = record
            if attempt < limit:
                stop.wait(min(2**attempt, 8))
        latest = load_json(path) if path.exists() else {}
        if not valid_mask_part(latest, raw_text, field):
            return False
    return True


def assembled_sources(out, old, raw_cases):
    groups = {}
    for cid in old["case_ids"]:
        for condition in CONDITIONS:
            fields = {}
            for field in ("title", "case_text", "judge_decision"):
                path = mask_path(out, cid, condition, field)
                part = load_json(path) if path.exists() else {}
                if valid_mask_part(part, raw_cases[cid][field], field):
                    fields[field] = part["value"]
            if len(fields) == 3:
                groups[(cid, condition)] = fields
    answers, tasks, checks = [], [], []
    for source in old["answers"]:
        if source["source_answer_is_processing_error"]:
            continue
        a = copy.deepcopy(source)
        cid, condition = a["case_id"], a["answer_condition"]
        masked = groups.get((cid, condition))
        a.update(input_ready=bool(masked), ready_for_cross_judging=bool(masked),
                 messages_sha256=None,
                 input_provenance="rebuilt_20260908", source_inputs_missing=[] if masked else ["masked_case_text", "masked_judgment"])
        if masked:
            a.update(masked_title=masked["title"], masked_case_text=masked["case_text"], masked_judgment=masked["judge_decision"])
            a["masked_case_sha256"] = sha256_text(a["masked_case_text"])
            a["masked_judgment_sha256"] = sha256_text(a["masked_judgment"])
            a["input_hash"] = json_hash(dict(case_id=cid, condition=condition, masked=masked))
            a["messages_sha256"] = json_hash(payload_for(a))
            context_aliases = set(ALIASES.findall(a["masked_case_text"] + "\n" + a["masked_judgment"]))
            q_only = sorted(set(ALIASES.findall(a["question"])) - context_aliases)
            answer_only = sorted(set(ALIASES.findall(a["answer"])) - context_aliases)
            checks.append(dict(answer_id=a["answer_id"], case_id=cid, condition=condition,
                               input_aliases=sorted(context_aliases), question_aliases_not_in_inputs=q_only,
                               answer_aliases_not_in_inputs=answer_only,
                               semantic_identity_verified=False,
                               note="Alias-set differences are review hints, not evidence of role mismatch; no text was changed."))
        answers.append(a)
        for label, judge in JUDGES.items():
            if judge["family"] == a["answer_family"]:
                continue
            tasks.append(dict(task_id=task_id(a["answer_id"], label), answer_id=a["answer_id"],
                              case_id=cid, question_number=a["question_number"], answer_condition=condition,
                              answer_family=a["answer_family"], judge_label=label, judge_family=judge["family"],
                              judge_model_id=judge["model_id"], judge_thinking=judge["thinking"],
                              source="new_api", input_provenance="rebuilt_20260908", input_ready=bool(masked),
                              **{k:a[k] for k in HASH_KEYS}))
    assert len(answers) == 499 and len(tasks) == 1497
    atomic_write_json(out / "source_answers.json", answers)
    atomic_write_json(out / "manifest.json", dict(tasks=tasks, expected_ratings=1497,
                       input_provenance="rebuilt_20260908", judge_matrix=load_json(out / "plan.json")["judge_matrix"]))
    atomic_write_json(out / "identity_review.json", dict(records=checks, automatic_checks_only=True,
                       warning="Rebuilt aliases must be reviewed against original roles and unchanged old answers; no automatic identity certification."))
    return answers, tasks, len(groups)


def classify(task, record, evidence):
    if not record:
        return "pending", None, "No completed rating record."
    for key in ("task_id", "answer_id", *HASH_KEYS):
        if record.get(key) != task.get(key):
            raise RuntimeError(f"Frozen rating input mismatch: {task['task_id']} / {key}")
    text = (record.get("scoring") or {}).get("content") or (record.get("evaluation") or {}).get("详细评价") or ""
    if known_refusal(text):
        return "refused", None, "Explicit sensitive-content refusal text; legacy raw score retained, analysis NA."
    score = (record.get("evaluation") or {}).get("总分")
    if record.get("status") == "success" and isinstance(score,(int,float)) and not isinstance(score,bool) and math.isfinite(score):
        return "valid", score, "Unchanged legacy evaluation total, including its existing penalties."
    if any(r.get("model") == task["judge_model_id"] and
           r.get("messages_sha256") == task.get("messages_sha256") and
           r.get("error_code") in REFUSAL_CODES for r in evidence):
        return "refused", None, "Provider content refusal confirmed by task-scoped physical receipt."
    error = str(record.get("error") or "")
    if any(token in error for token in TRANSPORT_ERRORS):
        return "transport_failed", None, error
    return "failed", None, error or "No valid final score."


def snapshot(out, plan, old, raw_cases, phase, guard=None):
    answers, tasks, group_count = assembled_sources(out, old, raw_cases)
    rows = []
    for task in tasks:
        path = out / "ratings" / (task["task_id"] + ".json")
        record = load_json(path) if path.exists() else {}
        evidence = guard.evidence("rating::" + task["task_id"]) if guard else (record.get("supplement_receipt_evidence") or [])
        status, score, reason = classify(task, record, evidence)
        rows.append(dict(task_id=task["task_id"], answer_id=task["answer_id"], case_id=task["case_id"],
                         question_number=task["question_number"], answer_condition=task["answer_condition"], judge_label=task["judge_label"],
                         status=status, analysis_total=score, raw_status=record.get("status"),
                         raw_total=(record.get("evaluation") or {}).get("总分"), reason=reason,
                         input_ready=task["input_ready"], rating_path=str(path), receipt_evidence=evidence))
    counts = Counter(r["status"] for r in rows)
    state = dict(updated_at=now_iso(), pid=os.getpid(), phase=phase, expected_cases=20,
                 mask_groups_ready=group_count, mask_groups_expected=100,
                 valid_source_answers=499, excluded_generation_failures=1, expected_ratings=1497,
                 input_ready_ratings=sum(t["input_ready"] for t in tasks), ratings_counts=dict(counts),
                 all_eligible_tasks_terminal=group_count==100 and counts["valid"]+counts["refused"]==1497,
                 all_eligible_scores_available=counts["valid"]==1497,
                 input_provenance="rebuilt_20260908", budget=guard.snapshot() if guard else None)
    atomic_write_json(out / "rating_status_overlay.json", dict(updated_at=state["updated_at"], records=rows))
    atomic_write_json(out / "state.json", state)
    return state, answers, tasks


def score_worker(out, task, answer, client, guard):
    tid = task["task_id"]
    path = out / "ratings" / (tid + ".json")
    dispatch_path = out / "dispatches" / (tid + ".json")
    dispatch = load_json(dispatch_path) if dispatch_path.exists() else {"count":0}
    atomic_write_json(dispatch_path, dict(count=dispatch["count"]+1, started_at=now_iso(), input_hash=task["input_hash"]))
    if path.exists():
        atomic_write_json(out / "prior_attempts" / f"{tid}_{time.time_ns()}.json", load_json(path))
    with guard.scope("rating::" + tid):
        record = run_new_task(task, answer, client)
    record["supplement_receipt_evidence"] = guard.evidence("rating::" + tid)
    # Metadata only; raw evaluation, score, status and parser outputs stay intact.
    atomic_write_json(path, record)
    return record


def score_ready(out, plan, old, raw_cases, args, client, guard, stop):
    """Score the currently frozen ready inputs; retained results are never rerun."""
    _, answers, tasks = snapshot(out, plan, old, raw_cases, "scoring", guard)
    lookup = {a["answer_id"]:a for a in answers}
    for cycle in range(3):
        pending=[]
        for task in tasks:
            path=out/"ratings"/(task["task_id"]+".json")
            record=load_json(path) if path.exists() else {}
            status,_,_=classify(task,record,guard.evidence("rating::"+task["task_id"]))
            dp=out/"dispatches"/(task["task_id"]+".json")
            count=load_json(dp)["count"] if dp.exists() else 0
            authorized = recovery_limits(guard, "rating_limits")
            limit = authorized.get(task["task_id"], 3)
            budget_recovery = (task["task_id"] in authorized and status == "failed" and
                               str(record.get("error") or "").startswith("BudgetStop:"))
            if task["input_ready"] and (status in {"pending","transport_failed"} or budget_recovery) and count<limit:
                pending.append(task)
        if not pending or stop.is_set() or guard.stopped.is_set(): break
        if cycle: stop.wait(30)
        iterator, active = iter(pending), {}
        emit("scoring_cycle", cycle=cycle+1, pending=len(pending))
        with futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            def dispatch_scores():
                while len(active)<args.workers and not stop.is_set() and not guard.stopped.is_set():
                    task=next(iterator,None)
                    if task is None: break
                    active[pool.submit(score_worker,out,task,lookup[task["answer_id"]],client,guard)]=task
            dispatch_scores()
            last=0.0
            while active:
                finished,_=futures.wait(active,timeout=10,return_when=futures.FIRST_COMPLETED)
                for f in finished:
                    task=active.pop(f)
                    record=f.result()
                    status,_,_=classify(task,record,guard.evidence("rating::"+task["task_id"]))
                    emit("rating",task_id=task["task_id"],judge=task["judge_label"],status=status)
                dispatch_scores()
                if time.monotonic()-last>=30:
                    state,_,_=snapshot(out,plan,old,raw_cases,"scoring",guard)
                    emit("progress",ratings_counts=state["ratings_counts"],budget=guard.snapshot())
                    last=time.monotonic()


def main():
    ap = argparse.ArgumentParser(__doc__)
    ap.add_argument("--execute", action="store_true")
    ap.add_argument("--detach", action="store_true")
    ap.add_argument("--phase", choices=["mask", "score", "all"], default="all")
    ap.add_argument("--mask-limit", type=int, default=0, help="Bounded smoke groups; zero = all 100.")
    ap.add_argument("--workers", type=int, default=16)
    ap.add_argument("--mask-workers", type=int, default=5)
    ap.add_argument("--budget-usd", type=float, default=40)
    ap.add_argument("--recover-failures-once", action="store_true", help="Persist one explicitly authorized recovery dispatch for diagnosed failures.")
    ap.add_argument("--extend-last-case-mask-to-16000", action="store_true", help="User-authorized token extension for only the five remaining case_text masks.")
    ap.add_argument("--output-dir", type=Path, default=OUT)
    ap.add_argument("--xhub-env", type=Path, default=ROOT.parent / "AI_Council/.env")
    args = ap.parse_args()
    if args.budget_usd not in {40, 70}: ap.error("This supplement permits its original $40 or explicitly authorized cumulative $70 ceiling.")
    if args.recover_failures_once and not args.execute: ap.error("recover-failures-once requires execute")
    if args.extend_last_case_mask_to_16000 and (not args.execute or args.budget_usd != 70):
        ap.error("The single-case 16k amendment requires execute and the authorized cumulative $70 budget")
    if not 1 <= args.workers <= 16 or not 1 <= args.mask_workers <= 5: ap.error("Invalid worker count")
    if args.mask_limit < 0: ap.error("mask-limit cannot be negative")
    out = args.output_dir.resolve()
    if out == OLD.resolve(): ap.error("Never overwrite the original 88-case directory")
    out.mkdir(parents=True, exist_ok=True)
    if args.detach:
        if not args.execute: ap.error("detach requires execute")
        argv = [sys.executable, str(Path(__file__).resolve())] + [a for a in sys.argv[1:] if a != "--detach"]
        with (out / "run.log").open("ab", buffering=0) as log:
            proc = subprocess.Popen(argv, cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
        emit("spawned", pid=proc.pid, log=str(out / "run.log"))
        return 0
    with (out / "run.lock").open("a+") as lock:
        try: fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError: raise SystemExit("Supplement already running; no duplicate calls dispatched")
        plan, old, raw_cases = prepare(out)
        hold_path = out / "quality_hold.json"
        quality_hold = load_json(hold_path) if hold_path.exists() else {}
        if quality_hold.get("active"):
            state, _, _ = snapshot(out, plan, old, raw_cases, "input_quality_hold")
            state["quality_hold"] = quality_hold
            atomic_write_json(out / "state.json", state)
            emit("input_quality_hold", state=state)
            return 3
        if not args.execute:
            state, _, _ = snapshot(out, plan, old, raw_cases, "prepared")
            emit("prepared", state=state)
            return 0
        cfg = dotenv_values(args.xhub_env)
        market = load_json(out / "market_snapshot.json")
        if args.budget_usd == 70:
            from scripts.cross_judge_budget_amendment import apply_authorized_budget_amendment
            args.budget_usd = apply_authorized_budget_amendment(out, lock_handle=lock, user_quote=BUDGET_AUTHORIZATION)
        guard = ScopedGuard(cfg["UNIFIED_API_BASE_URL"].rstrip("/"), cfg["UNIFIED_API_KEY"], out,
                            market["public_default_group_prices_usd_per_million"], market["quota_per_unit"],
                            args.budget_usd, prepaid_usd=0)
        if args.recover_failures_once or (out / RECOVERY_FILE).exists():
            guard.recovery_plan = prepare_recovery(out, guard)
        if args.extend_last_case_mask_to_16000 or (out / TOKEN_AMENDMENT_FILE).exists():
            guard.token_amendment = prepare_token_amendment(out)
        legacy.requests = guard
        client = legacy.XHubClient(cfg["UNIFIED_API_KEY"], cfg["UNIFIED_API_BASE_URL"].rstrip("/"), timeout=180)
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT): signal.signal(sig, lambda *_: stop.set())
        emit("started", phase=args.phase, budget_usd=args.budget_usd, workers=args.workers,
             recovery=bool(guard.__dict__.get("recovery_plan")),
             single_case_mask_tokens=(guard.__dict__.get("token_amendment") or {}).get("max_tokens"))
        if args.phase == "all":
            # Resume already-ready scores immediately, then prepare the rest.
            # This changes only dispatch order, not any model request or retry.
            score_ready(out, plan, old, raw_cases, args, client, guard, stop)
        if args.phase in {"all", "mask"}:
            groups = [(cid, condition) for cid in old["case_ids"] for condition in CONDITIONS]
            if args.mask_limit: groups = groups[:args.mask_limit]
            iterator, active = iter(groups), {}
            with futures.ThreadPoolExecutor(max_workers=args.mask_workers) as pool:
                def dispatch_masks():
                    while len(active) < args.mask_workers and not stop.is_set() and not guard.stopped.is_set():
                        item = next(iterator, None)
                        if item is None: break
                        cid, condition = item
                        active[pool.submit(run_mask_group, out, cid, condition, raw_cases[cid], client, guard, stop)] = item
                dispatch_masks()
                last = 0.0
                while active:
                    finished, _ = futures.wait(active, timeout=10, return_when=futures.FIRST_COMPLETED)
                    for f in finished:
                        cid, condition = active.pop(f)
                        ok = f.result()
                        emit("mask_group", case_id=cid, condition=condition, ok=ok)
                    dispatch_masks()
                    if time.monotonic()-last >= 30:
                        state, _, _ = snapshot(out, plan, old, raw_cases, "masking", guard)
                        emit("progress", mask_groups_ready=state["mask_groups_ready"], budget=guard.snapshot())
                        last = time.monotonic()
        state, answers, tasks = snapshot(out, plan, old, raw_cases, "inputs_prepared", guard)
        if args.phase == "mask" or stop.is_set() or guard.stopped.is_set():
            phase = "budget_paused" if guard.stopped.is_set() else "interrupted" if stop.is_set() else "mask_phase_finished"
            state, _, _ = snapshot(out, plan, old, raw_cases, phase, guard)
            emit("finished", state=state)
            return 0 if state["mask_groups_ready"]==100 else 2
        score_ready(out, plan, old, raw_cases, args, client, guard, stop)
        guard.refresh_balance()
        state,_,_=snapshot(out,plan,old,raw_cases,"finishing",guard)
        phase=("budget_paused" if guard.stopped.is_set() else "interrupted" if stop.is_set() else
               "finished" if state["all_eligible_tasks_terminal"] else "finished_with_missing")
        state,_,_=snapshot(out,plan,old,raw_cases,phase,guard)
        emit("finished",state=state)
        return 0 if state["all_eligible_tasks_terminal"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
