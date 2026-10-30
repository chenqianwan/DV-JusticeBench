"""Offline, finite concurrency policy for a *benchmark*, not legal adjudication.

``next_action(rounds)`` consumes completed, cumulative-free round observations.
Every round must contain the fields in ``REQUIRED_FIELDS``. ``complete`` means
successful, non-truncated, full responses; ``valid_format`` counts strictly
validated event JSON or NONE among those responses. Neither a valid format nor
NONE is evidence that an answer has (or does not have) a legal hallucination.

The workload hash must identify the exact same input set/order. The science hash
must cover prompt, model revision, sampling parameters, output limits, reference
pack, and validation rules. Only concurrency is allowed to differ between rounds.
``preemptions_delta`` is the *new* preemption count during that round, not the
server's lifetime counter. KV values are fractions, not percentages.

The caller must quiesce its own benchmark requests before changing server limits.
This module performs no I/O and never allocates GPUs, calls an API, changes model
parameters, or authorizes full-corpus hallucination statistics.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence


MAX_CONCURRENCY = 512
MAX_ROUNDS = 6
KV_CEILING = 0.75
MIN_COMPLETION_RATE = 0.98
MIN_FORMAT_RATE = 0.98
MAX_QUALITY_DROP = 0.02
MIN_GAIN = 0.10
NEAR_BEST_FRACTION = 0.95

REQUIRED_FIELDS = (
    "concurrency", "input_sha256", "science_sha256", "elapsed_s", "attempted",
    "complete", "valid_format", "output_tokens", "timeouts", "ooms",
    "service_errors", "preemptions_delta", "peak_kv_cache",
    "observations_complete",
)
_COUNT_FIELDS = (
    "attempted", "complete", "valid_format", "output_tokens", "timeouts",
    "ooms", "service_errors", "preemptions_delta",
)
_LEVELS = {32, 64, 128, 192, 256, 384, 512}


def _answer(action: str, reason: str, **details) -> dict:
    return {
        "action": action,
        "reason": reason,
        "benchmark_only": True,
        "formal_hallucination_statistics_allowed": False,
        **details,
    }


def _metrics(row: Mapping) -> dict:
    return {
        "completion_rate": row["complete"] / row["attempted"],
        "format_rate": row["valid_format"] / row["complete"] if row["complete"] else 0.0,
        "complete_responses_per_second": row["complete"] / row["elapsed_s"],
        "output_tokens_per_second": row["output_tokens"] / row["elapsed_s"],
    }


def _validate(rounds: Sequence) -> str | None:
    if len(rounds) > MAX_ROUNDS:
        return "round_limit_exceeded"
    seen = set()
    fingerprint = None
    for index, row in enumerate(rounds):
        if not isinstance(row, Mapping):
            return f"round_{index}:not_a_mapping"
        missing = [key for key in REQUIRED_FIELDS if key not in row]
        if missing:
            return f"round_{index}:missing_observations:{','.join(missing)}"
        concurrency = row["concurrency"]
        if type(concurrency) is not int or concurrency not in _LEVELS:
            return f"round_{index}:invalid_concurrency"
        if concurrency in seen:
            return f"round_{index}:duplicate_concurrency"
        seen.add(concurrency)
        if index == 0 and concurrency != 128:
            return "first_round_must_be_128"
        for key in ("input_sha256", "science_sha256"):
            value = row[key]
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                return f"round_{index}:invalid_{key}"
        pair = row["input_sha256"], row["science_sha256"]
        if fingerprint is not None and pair != fingerprint:
            return f"round_{index}:input_or_scientific_parameters_changed"
        fingerprint = pair
        for key in _COUNT_FIELDS:
            if type(row[key]) is not int or row[key] < 0:
                return f"round_{index}:invalid_{key}"
        if not (0 <= row["valid_format"] <= row["complete"] <= row["attempted"]) or row["attempted"] == 0:
            return f"round_{index}:inconsistent_response_counts"
        for key in ("elapsed_s", "peak_kv_cache"):
            value = row[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                return f"round_{index}:invalid_{key}"
        if row["elapsed_s"] <= 0 or not 0 <= row["peak_kv_cache"] <= 1:
            return f"round_{index}:invalid_time_or_kv_fraction"
        if row["observations_complete"] is not True:
            return f"round_{index}:observations_incomplete"
    return None


def assess_round(row: Mapping, baseline: Mapping) -> dict:
    """Assess an already schema-validated round against the original 128 round."""
    measures = _metrics(row)
    reference = _metrics(baseline)
    failures = []
    for key in ("timeouts", "ooms", "service_errors", "preemptions_delta"):
        if row[key]:
            failures.append(key)
    if row["peak_kv_cache"] >= KV_CEILING:
        failures.append("kv_ceiling")
    if measures["completion_rate"] + 1e-12 < MIN_COMPLETION_RATE:
        failures.append("completion_below_floor")
    if measures["format_rate"] + 1e-12 < MIN_FORMAT_RATE:
        failures.append("format_below_floor")
    if measures["completion_rate"] + MAX_QUALITY_DROP + 1e-12 < reference["completion_rate"]:
        failures.append("completion_degraded_vs_128")
    if measures["format_rate"] + MAX_QUALITY_DROP + 1e-12 < reference["format_rate"]:
        failures.append("format_degraded_vs_128")
    return {"concurrency": row["concurrency"], "stable": not failures, "failures": failures, **measures}


def _select(assessments: list[dict], reason: str) -> dict:
    stable = [item for item in assessments if item["stable"]]
    if not stable:
        return _answer("blocked", reason, selected_concurrency=None, assessments=assessments)
    peak = max(item["complete_responses_per_second"] for item in stable)
    near = [item for item in stable if item["complete_responses_per_second"] + 1e-12 >= peak * NEAR_BEST_FRACTION]
    chosen = min(near, key=lambda item: item["concurrency"])
    return _answer(
        "select", reason, selected_concurrency=chosen["concurrency"],
        selected_metrics=chosen, best_stable_throughput=peak,
        assessments=assessments,
    )


def next_action(rounds: Sequence[Mapping]) -> dict:
    """Return ``test`` (concurrency), ``select`` (selected_concurrency), or ``blocked``.

    128 -> 256 -> 512 is the only exponential ladder. A failed 256 or 512
    permits one midpoint (192 or 384), then selection. An unstable baseline
    permits 64 -> 32, stopping at the first stable fallback. A stable upgrade
    with <10% complete-response throughput gain ends the ladder. Selection
    chooses the smallest stable concurrency within 95% of the best throughput.
    Missing or incomparable evidence blocks the decision, never a blind rise.
    """
    if isinstance(rounds, (str, bytes)) or not isinstance(rounds, Sequence):
        return _answer("blocked", "rounds_must_be_a_sequence", selected_concurrency=None)
    invalid = _validate(rounds)
    if invalid:
        return _answer("blocked", invalid, selected_concurrency=None)
    if not rounds:
        return _answer("test", "initial_baseline", concurrency=128)

    # Reject an unplanned/skipped ladder: its measurements cannot silently become
    # a recommendation made under this policy.
    for index in range(1, len(rounds)):
        expected = next_action(rounds[:index])
        if expected["action"] != "test" or expected["concurrency"] != rounds[index]["concurrency"]:
            return _answer("blocked", f"round_{index}:unexpected_round_sequence", selected_concurrency=None)

    assessments = [assess_round(row, rounds[0]) for row in rounds]
    last = assessments[-1]
    level = last["concurrency"]
    if len(rounds) >= MAX_ROUNDS:
        return _select(assessments, "round_limit_reached")

    if not assessments[0]["stable"]:
        if last["stable"]:
            return _select(assessments, "stable_lower_concurrency_found")
        if level in (128, 64):
            return _answer("test", "baseline_or_fallback_unstable", concurrency=level // 2, assessments=assessments)
        return _select(assessments, "no_stable_baseline_or_fallback")

    if level in (192, 384):
        return _select(assessments, "midpoint_finished")
    if not last["stable"]:
        midpoint = {256: 192, 512: 384}.get(level)
        if midpoint:
            return _answer("test", "failed_upgrade_try_one_midpoint", concurrency=midpoint, assessments=assessments)
        return _select(assessments, "unstable_round")
    if level == MAX_CONCURRENCY:
        return _select(assessments, "maximum_concurrency_reached")
    if len(assessments) > 1:
        previous = assessments[-2]["complete_responses_per_second"]
        gain = last["complete_responses_per_second"] / previous - 1.0
        if gain + 1e-12 < MIN_GAIN:
            return _select(assessments, "throughput_gain_below_10_percent")
    return _answer("test", "stable_upgrade", concurrency=min(level * 2, MAX_CONCURRENCY), assessments=assessments)
