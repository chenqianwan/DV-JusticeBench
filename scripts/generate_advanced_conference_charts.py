#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate advanced charts for the unified 20-case slice.

Data source of scores/errors: multi-sheet Excel (single source of truth)
  - data/results_YYYYMMDD_unified_xxx/20个案例_统一评估结果_108cases.xlsx

Token telemetry (optional): parsed from the folder's detailed report table if present
  - results_详细报告_*.txt (Token usage section)

Outputs: PNG files written to the same results folder, following existing naming style:
  chart_<name>_<timestamp>.png
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Iterable, List, Optional, Tuple

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


# Matplotlib: English labels, consistent style
plt.rcParams["font.family"] = "Arial"
plt.rcParams["axes.unicode_minus"] = False

try:
    plt.style.use("seaborn-v0_8-whitegrid")
except Exception:
    try:
        plt.style.use("seaborn-whitegrid")
    except Exception:
        plt.style.use("default")


DIMENSION_COLS: List[str] = [
    "规范依据相关性_得分",
    "涵摄链条对齐度_得分",
    "价值衡量与同理心对齐度_得分",
    "关键事实与争点覆盖度_得分",
    "裁判结论与救济配置一致性_得分",
]

DIMENSION_LABELS: Dict[str, str] = {
    "规范依据相关性_得分": "Normative Basis\nRelevance",
    "涵摄链条对齐度_得分": "Subsumption Chain\nAlignment",
    "价值衡量与同理心对齐度_得分": "Value & Empathy\nAlignment",
    "关键事实与争点覆盖度_得分": "Key Facts & Issues\nCoverage",
    "裁判结论与救济配置一致性_得分": "Judgment & Relief\nConsistency",
}


SHEET_TO_MODEL: Dict[str, str] = {
    "DeepSeek-NoThinking": "DeepSeek",
    "DeepSeek": "DeepSeek-Thinking",
    "Gemini 2.5 Flash": "Gemini",
    "Claude Opus 4": "Claude",
    "Qwen-Max": "Qwen-Max",
    "GPT-4o": "GPT-4o",
    "GPT-5": "GPT-5",
}

# Consistent colors (match existing palette)
MODEL_COLORS: Dict[str, str] = {
    "DeepSeek": "#2E86AB",
    "DeepSeek-Thinking": "#1A5F7A",
    "Gemini": "#6A994E",
    "Claude": "#A23B72",
    "Qwen-Max": "#2E86AB",  # keep consistent with your prior charts
    "GPT-4o": "#A23B72",
    "GPT-5": "#C73E1D",
}

MAIN_MODELS: List[str] = [
    "DeepSeek-Thinking",
    "DeepSeek",
    "Gemini",
    "Claude",
    "Qwen-Max",
    "GPT-4o",
]

def _order_with_gpt_last(models: List[str]) -> List[str]:
    """Keep relative order, but force GPT-4o to the end if present."""
    if "GPT-4o" not in models:
        return models
    return [m for m in models if m != "GPT-4o"] + ["GPT-4o"]


# Heuristic abandoned-law detector:
# We rely on evaluator text commonly marking obsolete law with explicit phrases.
ABANDONED_KEYWORDS = [
    "已废止",
    "已废除",
    "废止",
    "废除",
    "已失效",
    "失效",
    "过期",
    "不再适用",
    "旧法",
    "已修订",
]


@dataclass(frozen=True)
class TokenTelemetry:
    avg_input: Optional[float]
    avg_output: Optional[float]
    avg_total: Optional[float]
    api_calls: Optional[float]


@dataclass
class ModelStats:
    model: str
    n: int
    avg_score: float
    p10_score: float
    cvar10_score: float
    abandoned_count: int
    abandoned_rate: float
    major_errors: int
    obvious_errors: int
    minor_errors: int
    refusal_count: int
    refusal_rate: float
    avg_len_chars: Optional[float]
    token: TokenTelemetry
    dim_means: Dict[str, float]


def _safe_str(x) -> str:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return ""
    return str(x)


def is_abandoned_law_row(row: pd.Series) -> bool:
    hay = " ".join(
        [
            _safe_str(row.get("详细评价", "")),
            _safe_str(row.get("重大错误", "")),
            _safe_str(row.get("明显错误", "")),
            _safe_str(row.get("微小错误", "")),
            _safe_str(row.get("错误标记", "")),
        ]
    )
    if not hay:
        return False
    return any(k in hay for k in ABANDONED_KEYWORDS)


def is_refusal_row(row: pd.Series) -> bool:
    # Treat empty answer as refusal/non-response in this slice.
    ans = row.get("AI回答", None)
    if ans is None or (isinstance(ans, float) and np.isnan(ans)):
        return True
    if isinstance(ans, str) and ans.strip() == "":
        return True
    return False


def parse_token_telemetry_from_report(report_txt_path: str) -> Dict[str, TokenTelemetry]:
    """
    Parse token usage table from results_详细报告_*.txt (section 三、Token使用统计).
    Rows may contain '数据不可用'.
    """
    if not os.path.exists(report_txt_path):
        return {}

    with open(report_txt_path, "r", encoding="utf-8") as f:
        text = f.read()

    # Locate section header
    m = re.search(r"三、Token使用统计.*?\n=+\n\n(.*?)\n\n四、AI回答长度统计", text, flags=re.S)
    if not m:
        return {}

    table = m.group(1).splitlines()
    # Skip header and separator lines
    data_lines = []
    for line in table:
        if not line.strip():
            continue
        if line.strip().startswith("模型"):
            continue
        if set(line.strip()) == {"-"}:
            continue
        data_lines.append(line.rstrip("\n"))

    out: Dict[str, TokenTelemetry] = {}
    # Example line:
    # DeepSeek                   2908             1409             4317              280
    # DeepSeek-Thinking 数据不可用 ...
    for line in data_lines:
        # Split by 2+ spaces to keep model name with hyphen
        parts = re.split(r"\s{2,}", line.strip())
        if not parts:
            continue
        model = parts[0]
        # Normalize to our paper names
        model = {
            "DeepSeek-Thinking": "DeepSeek-Thinking",
            "DeepSeek": "DeepSeek",
            "Gemini": "Gemini",
            "Claude": "Claude",
            "Qwen-Max": "Qwen-Max",
            "GPT-4o": "GPT-4o",
            "GPT-5": "GPT-5",
        }.get(model, model)

        def parse_num(x: str) -> Optional[float]:
            x = x.strip()
            if not x or "不可用" in x:
                return None
            try:
                return float(x)
            except Exception:
                return None

        avg_input = parse_num(parts[1]) if len(parts) > 1 else None
        avg_output = parse_num(parts[2]) if len(parts) > 2 else None
        avg_total = parse_num(parts[3]) if len(parts) > 3 else None
        api_calls = parse_num(parts[4]) if len(parts) > 4 else None
        out[model] = TokenTelemetry(avg_input=avg_input, avg_output=avg_output, avg_total=avg_total, api_calls=api_calls)

    return out


def merge_token_fallback(token_map: Dict[str, TokenTelemetry], results_dir: str) -> Dict[str, TokenTelemetry]:
    """
    (Deprecated) Previously used to backfill missing token telemetry (e.g., GPT-4o) from
    other result folders. This can silently mix runs and produce incorrect charts.
    Kept for backward compatibility but should be opt-in only.
    """
    if token_map.get("GPT-4o") and token_map["GPT-4o"].avg_total is not None:
        return token_map

    # Known previous results folder often contains GPT-4o token rows.
    candidates = [
        os.path.join(os.path.dirname(results_dir), "results_20260111_151734", "results_详细报告_20260111_161242.txt"),
    ]
    for p in candidates:
        if os.path.exists(p):
            fb = parse_token_telemetry_from_report(p)
            # Merge only missing entries
            for k, v in fb.items():
                cur = token_map.get(k)
                if cur is None or cur.avg_total is None:
                    token_map[k] = v
            break
    return token_map


def load_unified_excel(excel_path: str) -> Dict[str, pd.DataFrame]:
    xls = pd.ExcelFile(excel_path)
    data: Dict[str, pd.DataFrame] = {}
    for sheet in xls.sheet_names:
        if sheet not in SHEET_TO_MODEL:
            continue
        model = SHEET_TO_MODEL[sheet]
        df = pd.read_excel(excel_path, sheet_name=sheet)
        data[model] = df
    return data


def compute_model_stats(
    model: str,
    df: pd.DataFrame,
    token_map: Dict[str, TokenTelemetry],
) -> ModelStats:
    # Ensure numeric
    df = df.copy()
    df["总分"] = pd.to_numeric(df.get("总分"), errors="coerce")
    for c in DIMENSION_COLS:
        df[c] = pd.to_numeric(df.get(c), errors="coerce")

    scores = df["总分"].dropna().to_numpy()
    if len(scores) == 0:
        raise ValueError(f"No scores for model: {model}")

    abandoned_flags = df.apply(is_abandoned_law_row, axis=1)
    abandoned_count = int(abandoned_flags.sum())
    n = int(len(df))
    abandoned_rate = abandoned_count / n if n else 0.0

    major_errors = int(df.get("重大错误").notna().sum()) if "重大错误" in df.columns else 0
    obvious_errors = int(df.get("明显错误").notna().sum()) if "明显错误" in df.columns else 0
    minor_errors = int(df.get("微小错误").notna().sum()) if "微小错误" in df.columns else 0

    refusal_flags = df.apply(is_refusal_row, axis=1) if "AI回答" in df.columns else pd.Series([False] * len(df))
    refusal_count = int(refusal_flags.sum())
    refusal_rate = refusal_count / n if n else 0.0

    # Length stats (characters) for non-empty answers
    avg_len = None
    if "AI回答" in df.columns:
        non_empty = df["AI回答"].dropna().astype(str).map(lambda s: s.strip()).map(len)
        non_empty = non_empty[non_empty > 0]
        if len(non_empty) > 0:
            avg_len = float(non_empty.mean())

    dim_means = {c: float(df[c].mean()) for c in DIMENSION_COLS}

    p10 = float(np.quantile(scores, 0.10))
    # CVaR10: mean of bottom 10%
    cutoff = np.quantile(scores, 0.10)
    cvar10 = float(scores[scores <= cutoff].mean()) if np.any(scores <= cutoff) else p10

    token = token_map.get(model, TokenTelemetry(None, None, None, None))

    return ModelStats(
        model=model,
        n=n,
        avg_score=float(np.mean(scores)),
        p10_score=p10,
        cvar10_score=cvar10,
        abandoned_count=abandoned_count,
        abandoned_rate=abandoned_rate,
        major_errors=major_errors,
        obvious_errors=obvious_errors,
        minor_errors=minor_errors,
        refusal_count=refusal_count,
        refusal_rate=refusal_rate,
        avg_len_chars=avg_len,
        token=token,
        dim_means=dim_means,
    )


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _save(fig: plt.Figure, out_path: str) -> None:
    fig.tight_layout()
    fig.savefig(out_path, dpi=300, bbox_inches="tight")
    plt.close(fig)


def plot_pareto(stats: List[ModelStats], output_dir: str) -> str:
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_pareto_tradeoff_{ts}.png")

    fig, ax = plt.subplots(figsize=(11, 7))

    # Only plot main models + GPT-5 as optional gray hollow
    label_offsets = {
        # Avoid collisions around bottom-right cluster
        "DeepSeek": (-10, 8),
        "Gemini": (-6, -4),
        "DeepSeek-Thinking": (8, 8),
        "GPT-4o": (8, 8),
        "Qwen-Max": (8, 8),
        "Claude": (8, 8),
        "GPT-5": (8, 8),
    }
    for s in stats:
        color = MODEL_COLORS.get(s.model, "#808080")
        x = s.avg_score
        y = s.abandoned_rate

        # Token sizing: if unavailable, keep a small fixed size and hollow marker
        if s.token.avg_total is None:
            size = 120
            face = "none"
            edge = color
            lw = 2.0
        else:
            # scale sizes to a reasonable range
            size = float(np.interp(s.token.avg_total, [3000, 6000], [140, 420]))
            face = color
            edge = "black"
            lw = 1.2

        # GPT-5: always hollow gray to signal excluded
        if s.model == "GPT-5":
            face = "none"
            edge = "#666666"
            lw = 2.0
            color = "#666666"
            size = 140

        ax.scatter([x], [y], s=size, c=face, edgecolors=edge, linewidths=lw, alpha=0.9, marker="o")

        label = f"{s.model}\n({x:.2f}, {int(round(y*100))}%)"
        dx, dy = label_offsets.get(s.model, (8, 8))
        ax.annotate(
            label,
            (x, y),
            textcoords="offset points",
            xytext=(dx, dy),
            fontsize=9,
            ha="left" if dx >= 0 else "right",
            va="bottom",
        )

    ax.set_title("Quality–Reliability–Cost Trade-off (100 Questions)", fontsize=14, fontweight="bold")
    ax.set_xlabel("Average Total Score (out of 20)", fontsize=12, fontweight="bold")
    ax.set_ylabel("Abandoned-Law Rate", fontsize=12, fontweight="bold")
    ax.set_ylim(-0.005, max(s.abandoned_rate for s in stats if s.model != "GPT-5") * 1.25)
    ax.set_xlim(min(s.avg_score for s in stats if s.model != "GPT-5") - 0.5, max(s.avg_score for s in stats if s.model != "GPT-5") + 0.5)

    # Guidance box (avoid overlapping the data cluster near bottom-right)
    ax.text(
        0.98,
        0.98,
        "Better: higher score and lower staleness",
        transform=ax.transAxes,
        ha="right",
        va="top",
        fontsize=10,
        fontweight="bold",
        bbox=dict(boxstyle="round,pad=0.35", facecolor="white", alpha=0.9, edgecolor="#999999"),
    )

    # Legend note for marker styling
    ax.text(
        0.02,
        0.02,
        "Marker size scaled by avg tokens (if available)\nHollow marker: token telemetry unavailable / excluded model",
        transform=ax.transAxes,
        fontsize=9,
        bbox=dict(boxstyle="round,pad=0.4", facecolor="white", alpha=0.9, edgecolor="#999999"),
    )

    _save(fig, out)
    return out


def bootstrap_ci_mean(scores: np.ndarray, n_boot: int = 5000, seed: int = 42) -> Tuple[float, float, float]:
    rng = np.random.default_rng(seed)
    n = len(scores)
    means = np.empty(n_boot, dtype=float)
    for i in range(n_boot):
        samp = rng.choice(scores, size=n, replace=True)
        means[i] = float(np.mean(samp))
    lo, hi = np.quantile(means, [0.025, 0.975])
    return float(np.mean(scores)), float(lo), float(hi)


def plot_bootstrap_ci(model_dfs: Dict[str, pd.DataFrame], output_dir: str) -> str:
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_score_bootstrap_ci_{ts}.png")

    models = MAIN_MODELS[:]  # stable only
    mean_map: Dict[str, float] = {}
    lo_map: Dict[str, float] = {}
    hi_map: Dict[str, float] = {}
    for m in models:
        df = model_dfs[m]
        s = pd.to_numeric(df["总分"], errors="coerce").dropna().to_numpy()
        mean, l, h = bootstrap_ci_mean(s, n_boot=4000, seed=42)
        mean_map[m] = mean
        lo_map[m] = l
        hi_map[m] = h

    # sort by mean descending for readability, then force GPT-4o to appear last
    models = sorted(models, key=lambda m: mean_map[m], reverse=True)
    models = _order_with_gpt_last(models)
    means = [mean_map[m] for m in models]
    lo = [lo_map[m] for m in models]
    hi = [hi_map[m] for m in models]

    y = np.arange(len(models))
    fig, ax = plt.subplots(figsize=(10, 6))
    ax.errorbar(
        means,
        y,
        xerr=[np.array(means) - np.array(lo), np.array(hi) - np.array(means)],
        fmt="o",
        color="#222222",
        ecolor="#444444",
        elinewidth=2,
        capsize=4,
        markersize=7,
    )
    for yi, m, mean in zip(y, models, means):
        ax.scatter([mean], [yi], s=90, color=MODEL_COLORS.get(m, "#808080"), edgecolor="black", linewidth=0.8, zorder=3)
        ax.annotate(
            f"{mean:.2f}",
            (mean, yi),
            textcoords="offset points",
            xytext=(10, -10),
            fontsize=10,
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.9, edgecolor="none"),
        )

    ax.set_yticks(y)
    ax.set_yticklabels(models, fontsize=11)
    ax.set_xlabel("Mean Total Score (out of 20)", fontsize=12, fontweight="bold")
    ax.set_title("Mean Score with 95% Bootstrap CI (100 Questions)", fontsize=14, fontweight="bold")
    ax.grid(axis="x", alpha=0.3)
    _save(fig, out)
    return out


def plot_tail_risk(model_dfs: Dict[str, pd.DataFrame], output_dir: str) -> str:
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_tail_risk_{ts}.png")

    rows = []
    for m in MAIN_MODELS:
        s = pd.to_numeric(model_dfs[m]["总分"], errors="coerce").dropna().to_numpy()
        p10 = float(np.quantile(s, 0.10))
        cutoff = np.quantile(s, 0.10)
        cvar10 = float(s[s <= cutoff].mean()) if np.any(s <= cutoff) else p10
        rows.append((m, float(np.mean(s)), p10, cvar10))

    # sort by mean descending (GPT-4o already last by mean, but keep invariant explicit)
    rows.sort(key=lambda x: x[1], reverse=True)
    models = [r[0] for r in rows]
    means = [r[1] for r in rows]
    p10s = [r[2] for r in rows]
    cvars = [r[3] for r in rows]

    x = np.arange(len(models))
    fig, ax = plt.subplots(figsize=(11, 6))

    ax.bar(x, means, color=[MODEL_COLORS.get(m, "#808080") for m in models], alpha=0.75, edgecolor="black", linewidth=1.0, label="Mean")
    ax.plot(x, p10s, marker="o", color="#111111", linewidth=2.0, label="10th percentile")
    ax.plot(x, cvars, marker="s", color="#444444", linewidth=2.0, linestyle="--", label="CVaR@10% (mean of bottom 10%)")

    for xi, mean in zip(x, means):
        ax.text(xi, mean + 0.25, f"{mean:.2f}", ha="center", va="bottom", fontsize=9, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(models, rotation=15, ha="right")
    ax.set_ylabel("Total Score (out of 20)", fontsize=12, fontweight="bold")
    ax.set_title("Tail Risk Beyond the Mean (100 Questions)", fontsize=14, fontweight="bold")
    ax.grid(axis="y", alpha=0.25)
    ax.legend(fontsize=10, loc="upper left", bbox_to_anchor=(1.01, 1.0), frameon=True)
    _save(fig, out)
    return out


def plot_efficiency(stats: List[ModelStats], output_dir: str) -> str:
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_quality_vs_tokens_{ts}.png")

    # Only models with token telemetry
    pts = [s for s in stats if s.token.avg_total is not None and s.model in MAIN_MODELS]
    fig, ax = plt.subplots(figsize=(10, 6))

    for s in pts:
        x = float(s.token.avg_total)
        y = s.avg_score
        ax.scatter([x], [y], s=180, color=MODEL_COLORS.get(s.model, "#808080"), edgecolor="black", linewidth=1.0)
        # Prevent label from spilling out (Gemini sits near right edge)
        if s.model == "Gemini":
            ax.annotate(s.model, (x, y), textcoords="offset points", xytext=(-8, 8), fontsize=10, ha="right")
        else:
            ax.annotate(s.model, (x, y), textcoords="offset points", xytext=(7, 7), fontsize=10)

    ax.set_xlabel("Average Total Tokens per Response (answer generation)", fontsize=12, fontweight="bold")
    ax.set_ylabel("Average Total Score (out of 20)", fontsize=12, fontweight="bold")
    ax.set_title("Quality vs Compute Cost", fontsize=14, fontweight="bold")
    # add right padding so labels never clip
    xmin, xmax = ax.get_xlim()
    ax.set_xlim(xmin, xmax + (xmax - xmin) * 0.06)
    ax.grid(alpha=0.25)
    _save(fig, out)
    return out


def plot_reliability_gating(stats: List[ModelStats], output_dir: str, threshold: float = 0.01) -> str:
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_reliability_gating_{ts}.png")

    # Use only main models
    s_map = {s.model: s for s in stats if s.model in MAIN_MODELS}
    ordered = sorted(MAIN_MODELS, key=lambda m: s_map[m].avg_score, reverse=True)
    ordered = _order_with_gpt_last(ordered)

    scores = [s_map[m].avg_score for m in ordered]
    rates = [s_map[m].abandoned_rate for m in ordered]
    colors = []
    for m, r in zip(ordered, rates):
        if r <= threshold:
            colors.append(MODEL_COLORS.get(m, "#808080"))
        else:
            colors.append("#B0B0B0")  # gray out if fails threshold

    fig, ax = plt.subplots(figsize=(10.5, 6))
    x = np.arange(len(ordered))
    bars = ax.bar(x, scores, color=colors, alpha=0.85, edgecolor="black", linewidth=1.0)

    for i, (bar, m, r) in enumerate(zip(bars, ordered, rates)):
        ax.text(bar.get_x() + bar.get_width()/2, bar.get_height() + 0.25, f"{scores[i]:.2f}", ha="center", va="bottom", fontsize=9, fontweight="bold")
        ax.text(bar.get_x() + bar.get_width()/2, 0.30, f"{int(round(r*100))}%", ha="center", va="bottom", fontsize=9)

    ax.axhline(0, color="black", linewidth=0.8)
    ax.set_xticks(x)
    ax.set_xticklabels(ordered, rotation=15, ha="right")
    ax.set_ylabel("Average Total Score (out of 20)", fontsize=12, fontweight="bold")
    ax.set_title(f"Reliability-Gated Ranking (abandoned-law ≤ {int(threshold*100)}%)", fontsize=14, fontweight="bold")
    # Put the note outside the plotting area (avoid covering bars/labels)
    ax.text(
        1.01,
        1.00,
        "Gray bars fail the reliability threshold\nBottom labels show abandoned-law rate",
        transform=ax.transAxes,
        fontsize=9,
        va="top",
        ha="left",
        bbox=dict(boxstyle="round,pad=0.4", facecolor="white", alpha=0.9, edgecolor="#999999"),
    )
    ax.grid(axis="y", alpha=0.25)
    fig.subplots_adjust(right=0.80)
    _save(fig, out)
    return out


def plot_baseline_avg_score(stats: List[ModelStats], output_dir: str) -> str:
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_avg_score_{ts}.png")
    s_map = {s.model: s for s in stats if s.model in MAIN_MODELS}
    order = _order_with_gpt_last([m for m in MAIN_MODELS if m in s_map])
    scores = [s_map[m].avg_score for m in order]
    colors = [MODEL_COLORS.get(m, "#808080") for m in order]

    fig, ax = plt.subplots(figsize=(12, 6))
    bars = ax.bar(order, scores, color=colors, alpha=0.85, edgecolor="black", linewidth=1.2)
    for bar, sc in zip(bars, scores):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.25, f"{sc:.2f}", ha="center", va="bottom", fontsize=11, fontweight="bold")

    ax.set_ylabel("Average Total Score (out of 20)", fontsize=12, fontweight="bold")
    ax.set_title("Average Total Score Comparison (20 Cases)", fontsize=14, fontweight="bold")
    plt.xticks(rotation=15, ha="right")
    ax.grid(axis="y", alpha=0.25)
    _save(fig, out)
    return out


def plot_baseline_abandoned_laws(stats: List[ModelStats], output_dir: str) -> str:
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_abandoned_laws_{ts}.png")
    s_map = {s.model: s for s in stats if s.model in MAIN_MODELS}
    order = _order_with_gpt_last([m for m in MAIN_MODELS if m in s_map])
    counts = [s_map[m].abandoned_count for m in order]
    colors = [MODEL_COLORS.get(m, "#808080") for m in order]

    fig, ax = plt.subplots(figsize=(12, 6))
    bars = ax.bar(order, counts, color=colors, alpha=0.85, edgecolor="black", linewidth=1.2)
    for bar, c in zip(bars, counts):
        ax.text(bar.get_x() + bar.get_width() / 2, bar.get_height() + 0.3, f"{int(c)}", ha="center", va="bottom", fontsize=11, fontweight="bold")

    ax.set_ylabel("Questions with Abandoned-Law Citations", fontsize=12, fontweight="bold")
    ax.set_title("Abandoned-Law Citations (Lower is Better)", fontsize=14, fontweight="bold")
    plt.xticks(rotation=15, ha="right")
    ax.grid(axis="y", alpha=0.25)
    _save(fig, out)
    return out


def plot_baseline_heatmap_dimensions(stats: List[ModelStats], output_dir: str) -> str:
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_heatmap_dimensions_{ts}.png")
    s_map = {s.model: s for s in stats if s.model in MAIN_MODELS}
    order = _order_with_gpt_last([m for m in MAIN_MODELS if m in s_map])

    mat = np.array([[s_map[m].dim_means[c] for m in order] for c in DIMENSION_COLS], dtype=float)
    fig, ax = plt.subplots(figsize=(12, 7))
    im = ax.imshow(mat, cmap="YlOrRd", aspect="auto")
    ax.set_xticks(np.arange(len(order)))
    ax.set_xticklabels(order, rotation=15, ha="right")
    ax.set_yticks(np.arange(len(DIMENSION_COLS)))
    ax.set_yticklabels([DIMENSION_LABELS[c] for c in DIMENSION_COLS])

    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat[i, j]
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=10, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.85, edgecolor="none"))

    ax.set_title("Model Performance Heatmap: Average Scores by Dimension (20 Cases)", fontsize=14, fontweight="bold", pad=18)
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Average Score", fontsize=11, fontweight="bold")
    _save(fig, out)
    return out


def plot_baseline_heatmap_metrics(model_dfs: Dict[str, pd.DataFrame], stats: List[ModelStats], output_dir: str) -> str:
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_heatmap_metrics_{ts}.png")
    s_map = {s.model: s for s in stats if s.model in MAIN_MODELS}
    order = _order_with_gpt_last([m for m in MAIN_MODELS if m in s_map])

    metric_names = [
        "Avg Score",
        "Percentage",
        "Max Score",
        "Min Score",
        "Major Errors",
        "Moderate Errors",
        "Minor Errors",
        "Abandoned Laws",
    ]

    rows = []
    for m in order:
        df = model_dfs[m]
        total_questions = len(df)
        s = s_map[m]
        score_series = pd.to_numeric(df["总分"], errors="coerce").dropna()
        max_score = float(score_series.max()) if len(score_series) else 0.0
        min_score = float(score_series.min()) if len(score_series) else 0.0

        major = int(df.get("重大错误").notna().sum()) if "重大错误" in df.columns else 0
        moderate = int(df.get("明显错误").notna().sum()) if "明显错误" in df.columns else 0
        minor = int(df.get("微小错误").notna().sum()) if "微小错误" in df.columns else 0

        # Normalize to 0–4 for visualization (match prior heatmap style)
        avg_norm = s.avg_score / 5.0
        pct_norm = (s.avg_score / 20.0 * 100.0) / 25.0
        max_norm = max_score / 5.0
        min_norm = min_score / 5.0
        major_norm = (major / total_questions * 4.0) if total_questions else 0.0
        moderate_norm = (moderate / total_questions * 4.0) if total_questions else 0.0
        minor_norm = (minor / total_questions * 4.0) if total_questions else 0.0
        abandoned_norm = (s.abandoned_count / total_questions * 4.0) if total_questions else 0.0

        rows.append([avg_norm, pct_norm, max_norm, min_norm, major_norm, moderate_norm, minor_norm, abandoned_norm])

    mat = np.array(rows, dtype=float).T  # metrics x models

    fig, ax = plt.subplots(figsize=(12, 7))
    im = ax.imshow(mat, cmap="RdYlGn_r", aspect="auto")
    ax.set_xticks(np.arange(len(order)))
    ax.set_xticklabels(order, rotation=15, ha="right")
    ax.set_yticks(np.arange(len(metric_names)))
    ax.set_yticklabels(metric_names)

    for i in range(mat.shape[0]):
        for j in range(mat.shape[1]):
            v = mat[i, j]
            if np.isnan(v):
                continue
            ax.text(j, i, f"{v:.2f}", ha="center", va="center", fontsize=9, fontweight="bold",
                    bbox=dict(boxstyle="round,pad=0.2", facecolor="white", alpha=0.85, edgecolor="none"))

    ax.set_title("Model Performance Heatmap: Comprehensive Metrics Comparison (20 Cases)", fontsize=14, fontweight="bold", pad=18)
    cbar = fig.colorbar(im, ax=ax)
    cbar.set_label("Normalized Score (0-4)", fontsize=11, fontweight="bold")
    _save(fig, out)
    return out


def plot_baseline_token_usage(
    token_map: Dict[str, TokenTelemetry],
    output_dir: str,
    models_to_show: Optional[List[str]] = None,
) -> Optional[str]:
    """
    Rebuild token usage chart with GPT on the right.
    Uses token telemetry parsed from report(s). Includes models with data;
    if models_to_show is given, also includes missing models as "N/A" so e.g. GPT-4o is not omitted.
    """
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_token_usage_{ts}.png")

    order = _order_with_gpt_last(models_to_show or ["DeepSeek", "Gemini", "Claude", "Qwen-Max", "GPT-4o"])
    models = []
    avg_in = []
    avg_out = []
    avg_total = []
    na_mask = []  # True if row is N/A placeholder

    for m in order:
        t = token_map.get(m)
        if t is not None and t.avg_total is not None:
            models.append(m)
            avg_in.append(t.avg_input)
            avg_out.append(t.avg_output)
            avg_total.append(t.avg_total)
            na_mask.append(False)
        elif models_to_show and m in models_to_show:
            models.append(m)
            avg_in.append(0.0)
            avg_out.append(0.0)
            avg_total.append(0.0)
            na_mask.append(True)

    if not models:
        return None

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    x = np.arange(len(models))
    width = 0.36
    base_in = [0 if na else v for v, na in zip(avg_in, na_mask)]
    base_out = [0 if na else v for v, na in zip(avg_out, na_mask)]
    b1 = ax1.bar(x - width/2, base_in, width, label="Input Tokens", color="#2E86AB", alpha=0.85)
    b2 = ax1.bar(x + width/2, base_out, width, label="Output Tokens", color="#F18F01", alpha=0.85)
    ax1.set_title("Average Token Usage per API Call", fontsize=13, fontweight="bold")
    ax1.set_ylabel("Average Tokens", fontsize=12, fontweight="bold")
    ax1.set_xticks(x)
    labels = [f"{m} (N/A)" if na else m for m, na in zip(models, na_mask)]
    ax1.set_xticklabels(labels, rotation=15, ha="right")
    ax1.legend(fontsize=10)
    ax1.grid(axis="y", alpha=0.25)
    ymax = max(avg_in + avg_out) if any(not na for na in na_mask) else 1
    for bar_grp_idx, bars in enumerate((b1, b2)):
        for i, bar in enumerate(bars):
            h = bar.get_height()
            if h > 0:
                ax1.text(bar.get_x() + bar.get_width()/2, h + ymax * 0.01, f"{int(round(h))}", ha="center", va="bottom", fontsize=9, fontweight="bold")
            elif na_mask[i] and bar_grp_idx == 0:
                ax1.text(bar.get_x() + bar.get_width()/2, ymax * 0.02, "N/A", ha="center", va="bottom", fontsize=8)

    colors = ["#B0B0B0" if na else MODEL_COLORS.get(m, "#808080") for m, na in zip(models, na_mask)]
    b = ax2.bar(models, avg_total, color=colors, alpha=0.85, edgecolor="black", linewidth=1.0)
    ax2.set_xticklabels(labels, rotation=15, ha="right")
    ax2.set_title("Average Total Token Usage per API Call", fontsize=13, fontweight="bold")
    ax2.set_ylabel("Average Total Tokens", fontsize=12, fontweight="bold")
    ax2.grid(axis="y", alpha=0.25)
    tmax = max(avg_total) if any(not na for na in na_mask) else 1
    for bar, tot, na in zip(b, avg_total, na_mask):
        if na:
            ax2.text(bar.get_x() + bar.get_width()/2, tmax * 0.02, "N/A", ha="center", va="bottom", fontsize=9, fontweight="bold")
        else:
            ax2.text(bar.get_x() + bar.get_width()/2, tot + tmax * 0.02, f"{int(round(tot))}", ha="center", va="bottom", fontsize=10, fontweight="bold")

    _save(fig, out)
    return out


def plot_baseline_percentage(stats: List[ModelStats], output_dir: str) -> str:
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_percentage_{ts}.png")
    s_map = {s.model: s for s in stats if s.model in MAIN_MODELS}
    order = _order_with_gpt_last([m for m in MAIN_MODELS if m in s_map])
    pct = [s_map[m].avg_score / 20.0 * 100.0 for m in order]
    colors = [MODEL_COLORS.get(m, "#808080") for m in order]

    fig, ax = plt.subplots(figsize=(12, 6))
    bars = ax.bar(order, pct, color=colors, alpha=0.85, edgecolor="black", linewidth=1.2)
    for bar, p in zip(bars, pct):
        ax.text(bar.get_x() + bar.get_width() / 2, p + 1.2, f"{p:.1f}%", ha="center", va="bottom", fontsize=11, fontweight="bold")

    ax.set_ylabel("Average Percentage Score (%)", fontsize=12, fontweight="bold")
    ax.set_title("Average Percentage Score Comparison (20 Cases)", fontsize=14, fontweight="bold")
    ax.set_ylim(0, 100)
    plt.xticks(rotation=15, ha="right")
    ax.grid(axis="y", alpha=0.25)
    _save(fig, out)
    return out


def plot_baseline_errors(model_dfs: Dict[str, pd.DataFrame], output_dir: str) -> str:
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_errors_{ts}.png")

    order = _order_with_gpt_last([m for m in MAIN_MODELS if m in model_dfs])
    major = []
    moderate = []
    minor = []
    for m in order:
        df = model_dfs[m]
        major.append(int(df.get("重大错误").notna().sum()) if "重大错误" in df.columns else 0)
        moderate.append(int(df.get("明显错误").notna().sum()) if "明显错误" in df.columns else 0)
        minor.append(int(df.get("微小错误").notna().sum()) if "微小错误" in df.columns else 0)

    x = np.arange(len(order))
    width = 0.25
    fig, ax = plt.subplots(figsize=(12, 6))
    b1 = ax.bar(x - width, major, width, label="Major Errors", color="#C73E1D", alpha=0.85)
    b2 = ax.bar(x, moderate, width, label="Moderate Errors", color="#F18F01", alpha=0.85)
    b3 = ax.bar(x + width, minor, width, label="Minor Errors", color="#6A994E", alpha=0.85)

    for bars in (b1, b2, b3):
        for bar in bars:
            h = bar.get_height()
            if h > 0:
                ax.text(bar.get_x() + bar.get_width()/2, h + max(major + moderate + minor) * 0.01, f"{int(h)}",
                        ha="center", va="bottom", fontsize=9, fontweight="bold")

    ax.set_xticks(x)
    ax.set_xticklabels(order, rotation=15, ha="right")
    ax.set_ylabel("Number of Errors", fontsize=12, fontweight="bold")
    ax.set_title("Error Statistics Comparison (20 Cases)", fontsize=14, fontweight="bold")
    ax.legend(fontsize=10)
    ax.grid(axis="y", alpha=0.25)
    _save(fig, out)
    return out


def plot_baseline_distribution(model_dfs: Dict[str, pd.DataFrame], output_dir: str) -> str:
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_distribution_{ts}.png")

    order = _order_with_gpt_last([m for m in MAIN_MODELS if m in model_dfs])
    data = [pd.to_numeric(model_dfs[m]["总分"], errors="coerce").dropna() for m in order]
    fig, ax = plt.subplots(figsize=(12, 6))
    bp = ax.boxplot(data, tick_labels=order, patch_artist=True, showmeans=True, meanline=True)

    for patch, m in zip(bp["boxes"], order):
        patch.set_facecolor(MODEL_COLORS.get(m, "#808080"))
        patch.set_alpha(0.6)
    ax.set_ylabel("Total Score (out of 20)", fontsize=12, fontweight="bold")
    ax.set_title("Score Distribution Comparison (Box Plot)", fontsize=14, fontweight="bold")
    ax.grid(axis="y", alpha=0.25)
    plt.xticks(rotation=15, ha="right")
    _save(fig, out)
    return out


def plot_baseline_ranking(stats: List[ModelStats], output_dir: str) -> str:
    """
    Two-panel ranking with GPT on the far right (x-axis), avoiding barh ordering ambiguity.
    """
    ts = _timestamp()
    out = os.path.join(output_dir, f"chart_ranking_{ts}.png")
    s_map = {s.model: s for s in stats if s.model in MAIN_MODELS}
    order = _order_with_gpt_last([m for m in MAIN_MODELS if m in s_map])

    scores = [s_map[m].avg_score for m in order]
    abandoned = [s_map[m].abandoned_count for m in order]
    colors = [MODEL_COLORS.get(m, "#808080") for m in order]

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    b1 = ax1.bar(order, scores, color=colors, alpha=0.85, edgecolor="black", linewidth=1.0)
    for bar, sc in zip(b1, scores):
        ax1.text(bar.get_x() + bar.get_width()/2, sc + 0.25, f"{sc:.2f}", ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax1.set_title("Ranking by Average Score (Higher is Better)", fontsize=13, fontweight="bold")
    ax1.set_ylabel("Average Total Score (out of 20)", fontsize=12, fontweight="bold")
    ax1.grid(axis="y", alpha=0.25)
    ax1.set_ylim(0, max(scores) * 1.15)
    ax1.tick_params(axis="x", rotation=15)

    b2 = ax2.bar(order, abandoned, color=colors, alpha=0.85, edgecolor="black", linewidth=1.0)
    for bar, c in zip(b2, abandoned):
        ax2.text(bar.get_x() + bar.get_width()/2, c + 0.3, f"{int(c)}", ha="center", va="bottom", fontsize=10, fontweight="bold")
    ax2.set_title("Ranking by Abandoned-Law Citations (Lower is Better)", fontsize=13, fontweight="bold")
    ax2.set_ylabel("Number of Questions with Abandoned-Law Citations", fontsize=12, fontweight="bold")
    ax2.grid(axis="y", alpha=0.25)
    ax2.set_ylim(0, max(abandoned) * 1.20 if max(abandoned) > 0 else 1)
    ax2.tick_params(axis="x", rotation=15)

    _save(fig, out)
    return out


def main():
    # Auto-detect latest unified results folder or accept argument.
    import argparse

    parser = argparse.ArgumentParser(description="Generate advanced charts for unified Excel results")
    parser.add_argument(
        "--results_dir",
        type=str,
        default=None,
        help="Results directory containing 20个案例_统一评估结果_108cases.xlsx (default: latest data/results_*_unified_*)",
    )
    parser.add_argument(
        "--allow_token_fallback",
        action="store_true",
        help="Allow backfilling missing token telemetry from other result folders (NOT recommended; may mix runs).",
    )
    args = parser.parse_args()

    if args.results_dir:
        results_dir = args.results_dir
    else:
        data_dir = os.path.join(os.getcwd(), "data")
        cand = [os.path.join(data_dir, d) for d in os.listdir(data_dir) if d.startswith("results_") and os.path.isdir(os.path.join(data_dir, d))]
        cand = [d for d in cand if "unified" in os.path.basename(d)]
        if not cand:
            raise SystemExit("No unified results directory found under data/")
        results_dir = sorted(cand)[-1]

    excel_path = os.path.join(results_dir, "20个案例_统一评估结果_108cases.xlsx")
    if not os.path.exists(excel_path):
        raise SystemExit(f"Excel not found: {excel_path}")

    # Token telemetry: parse from detailed report if present
    report_txt = None
    for fn in os.listdir(results_dir):
        if fn.startswith("results_详细报告_") and fn.endswith(".txt"):
            report_txt = os.path.join(results_dir, fn)
            break
    token_map = parse_token_telemetry_from_report(report_txt) if report_txt else {}
    if args.allow_token_fallback:
        token_map = merge_token_fallback(token_map, results_dir)

    model_dfs = load_unified_excel(excel_path)

    # Compute stats for all models present
    stats: List[ModelStats] = []
    for model, df in model_dfs.items():
        try:
            stats.append(compute_model_stats(model, df, token_map))
        except Exception as e:
            print(f"[WARN] skip {model}: {e}")

    # Generate charts (advanced analysis set)
    created: List[str] = []
    created.append(plot_pareto(stats, results_dir))
    created.append(plot_bootstrap_ci({m: model_dfs[m] for m in MAIN_MODELS if m in model_dfs}, results_dir))
    created.append(plot_tail_risk({m: model_dfs[m] for m in MAIN_MODELS if m in model_dfs}, results_dir))
    created.append(plot_efficiency(stats, results_dir))
    created.append(plot_reliability_gating(stats, results_dir, threshold=0.01))

    # Regenerate key baseline charts with GPT on the right
    created.append(plot_baseline_avg_score(stats, results_dir))
    created.append(plot_baseline_abandoned_laws(stats, results_dir))
    created.append(plot_baseline_heatmap_dimensions(stats, results_dir))
    created.append(plot_baseline_heatmap_metrics(model_dfs, stats, results_dir))
    tok_chart = plot_baseline_token_usage(token_map, results_dir)
    if tok_chart:
        created.append(tok_chart)
    created.append(plot_baseline_percentage(stats, results_dir))
    created.append(plot_baseline_errors({m: model_dfs[m] for m in MAIN_MODELS if m in model_dfs}, results_dir))
    created.append(plot_baseline_distribution({m: model_dfs[m] for m in MAIN_MODELS if m in model_dfs}, results_dir))
    created.append(plot_baseline_ranking(stats, results_dir))

    print("\nGenerated advanced charts:")
    for p in created:
        print("  ✓", os.path.basename(p))


if __name__ == "__main__":
    main()

