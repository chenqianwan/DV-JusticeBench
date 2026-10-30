#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate DV-JusticeBench Dataset Composition and Coverage figure.
Strict academic style (APA/conference): serif, muted palette, no top/right spines.
Aligned with generate_advanced_conference_charts.py.
"""

import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import numpy as np
import textwrap
from pathlib import Path

# --- 1. 全局设置 (严格学术风格，与会议图一致) ---
plt.rcParams["font.family"] = "serif"
plt.rcParams["font.serif"] = ["Times New Roman", "DejaVu Serif", "serif"]
plt.rcParams["font.size"] = 12
plt.rcParams["axes.titlesize"] = 14
plt.rcParams["axes.labelsize"] = 12
plt.rcParams["xtick.labelsize"] = 11
plt.rcParams["ytick.labelsize"] = 11
plt.rcParams["legend.fontsize"] = 11
plt.rcParams["axes.unicode_minus"] = False
plt.rcParams["axes.spines.top"] = False
plt.rcParams["axes.spines.right"] = False
plt.rcParams["axes.grid"] = False
plt.rcParams["figure.facecolor"] = "white"
plt.rcParams["axes.facecolor"] = "white"
plt.rcParams["figure.dpi"] = 300
plt.rcParams["savefig.bbox"] = "tight"
plt.style.use("default")

# 学术配色 (与会议图一致)
COLORS = {
    "primary": "#2C3E50",     # Dark Slate
    "secondary": "#34495E",   # Slate
    "accent": "#7B241C",      # Deep Burgundy (刑事/强调)
    "neutral": "#95A5A6",     # Grey (Others/辅助)
    "donut": ["#2C3E50", "#34495E", "#4A6FA5", "#7F8C8D", "#95A5A6"],
}


def _apply_academic_axes(ax, grid_axis=None):
    """去掉顶/右轴线；可选极淡单轴网格。"""
    ax.spines["top"].set_visible(False)
    ax.spines["right"].set_visible(False)
    if grid_axis:
        ax.grid(True, axis=grid_axis, color="#E0E0E0", linestyle=":", linewidth=0.5, alpha=0.5)


# --- 2. 数据准备 ---

# (a) 案由分布
causes_labels = [
    "Marriage, Family & Inheritance Disputes",
    "Contract, Unjust Enrichment & Related Disputes",
    "Crimes Against Personal Rights",
    "Tort Liability Disputes",
    "Personality Rights Disputes",
    "Property Rights Disputes",
    "Criminal Incidental Civil",
    "Creditor's Revocation Action",
    "Contract Validity Confirmation",
    "Guarantee Contract Disputes",
]
causes_values = [51, 21, 15, 7, 6, 5, 4, 1, 1, 1]
causes_labels = [textwrap.fill(l, 30) for l in causes_labels][::-1]
causes_values = causes_values[::-1]

# (b) 雷达图 — 全部 108，上限 120
radar_labels = ["D1", "D2", "D3", "D4", "D5"]
radar_values = [108, 108, 108, 108, 108]
radar_max = 120

# (c) 地域分布
geo_data = {
    "Gansu": 14, "Beijing": 10, "Henan": 8, "Sichuan": 8, "Shaanxi": 7,
    "Xinjiang": 5, "Guangdong": 5, "Inner Mongolia": 4, "Hunan": 3,
    "Shandong": 3, "Shanxi": 3, "Qinghai": 3, "Hebei": 3,
    "Ningxia": 3, "Jiangxi": 3,
}
others_count = 35
geo_labels = list(geo_data.keys()) + ["Others (10 Prov.)"]
geo_values = list(geo_data.values()) + [others_count]
geo_labels = geo_labels[::-1]
geo_values = geo_values[::-1]

# (d) 案件类型
type_labels = ["Civil", "Criminal", "Criminal Incidental\nCivil"]
type_values = [89, 15, 4]

# (e) 环形图 均分
donut_values = [20, 20, 20, 20, 20]
donut_labels = ["20%", "20%", "20%", "20%", "20%"]
donut_legend = ["D1", "D2", "D3", "D4", "D5"]

# --- 3. 绘图逻辑 ---

fig = plt.figure(figsize=(15, 9))
gs = gridspec.GridSpec(2, 2, width_ratios=[1, 1.1], wspace=0.25, hspace=0.3)

gs_left = gridspec.GridSpecFromSubplotSpec(2, 1, subplot_spec=gs[:, 0], hspace=0.35)
gs_right = gridspec.GridSpecFromSubplotSpec(2, 2, subplot_spec=gs[:, 1], height_ratios=[1.2, 1], wspace=0.3, hspace=0.4)

# --- Plot (a): Cause of Action ---
ax_a = fig.add_subplot(gs_left[0, 0])
bars_a = ax_a.barh(causes_labels, causes_values, color=COLORS["primary"], height=0.6, edgecolor="none")
ax_a.set_title("(a) Distribution by Cause of Action", loc="left", fontweight="bold", pad=10)
_apply_academic_axes(ax_a, "x")
for bar in bars_a:
    w = bar.get_width()
    ax_a.text(w + 0.4, bar.get_y() + bar.get_height() / 2, f"{int(w)}", va="center", fontsize=11, color=COLORS["primary"])

# --- Plot (c): Geographic Coverage ---
ax_c = fig.add_subplot(gs_left[1, 0])
colors_c = [COLORS["neutral"]] + [COLORS["secondary"]] * (len(geo_values) - 1)
bars_c = ax_c.barh(geo_labels, geo_values, color=colors_c, height=0.65, edgecolor="none")
ax_c.set_title("(c) Geographic Coverage", loc="left", fontweight="bold", pad=10)
ax_c.set_xlabel("Number of Cases")
_apply_academic_axes(ax_c, "x")
for bar in bars_c:
    w = bar.get_width()
    ax_c.text(w + 0.2, bar.get_y() + bar.get_height() / 2, f"{int(w)}", va="center", fontsize=10, color=COLORS["secondary"])

# --- Plot (b): Radar ---
ax_b = fig.add_subplot(gs_right[0, :], polar=True)
angles = np.linspace(0, 2 * np.pi, len(radar_labels), endpoint=False).tolist()
radar_values_closed = radar_values + [radar_values[0]]
angles_closed = angles + [angles[0]]

ax_b.plot(angles_closed, radar_values_closed, color=COLORS["primary"], linewidth=1.5)
ax_b.fill(angles_closed, radar_values_closed, color=COLORS["primary"], alpha=0.12)

ax_b.set_xticks(angles)
ax_b.set_xticklabels(radar_labels, size=11, fontweight="bold")
ax_b.set_yticks([30, 60, 90, 120])
ax_b.set_yticklabels(["30", "60", "90", "120"], color=COLORS["neutral"], size=10)
ax_b.set_ylim(0, radar_max)
ax_b.spines["polar"].set_visible(False)
ax_b.grid(color=COLORS["neutral"], linestyle=":", linewidth=0.5, alpha=0.4)
ax_b.set_title("(b) Five Evaluation Dimensions", loc="center", fontweight="bold", pad=18)

ax_b.scatter(angles, radar_values, color=COLORS["primary"], s=28, zorder=5)
# 数值放在内侧避免与轴标签重叠
for ang, val in zip(angles, radar_values):
    ax_b.text(ang, 55, str(val), ha="center", va="center", fontweight="bold", fontsize=11, color=COLORS["primary"])

# --- Plot (d): Case Types ---
ax_d = fig.add_subplot(gs_right[1, 0])
colors_d = [COLORS["primary"], COLORS["accent"], "#AEB6BF"]
bars_d = ax_d.bar(type_labels, type_values, color=colors_d, width=0.5, edgecolor="none")
ax_d.set_title("(d) Case Types", loc="left", fontweight="bold", pad=10)
ax_d.set_ylim(0, 102)
_apply_academic_axes(ax_d, "y")
total_types = sum(type_values)
for bar in bars_d:
    h = bar.get_height()
    pct = (h / total_types) * 100
    ax_d.text(bar.get_x() + bar.get_width() / 2, h + 1.5, f"{int(h)}\n({pct:.1f}%)", ha="center", va="bottom", fontsize=11)

# --- Plot (e): Donut ---
ax_e = fig.add_subplot(gs_right[1, 1])
wedges, texts = ax_e.pie(
    donut_values,
    labels=donut_labels,
    startangle=90,
    colors=COLORS["donut"],
    wedgeprops=dict(width=0.35, edgecolor="white", linewidth=1),
    textprops=dict(color="black", fontsize=10, fontweight="bold"),
    labeldistance=1.12,
)
ax_e.text(0, 0, "540\nQuestions", ha="center", va="center", fontweight="bold", fontsize=11)
ax_e.set_title("(e) Question Distribution", loc="center", fontweight="bold", pad=10)
ax_e.legend(wedges, donut_legend, title="Dim", loc="center left", bbox_to_anchor=(0.88, 0, 0.5, 1), frameon=True, fancybox=False, edgecolor=COLORS["neutral"], fontsize=10)

# --- 4. 布局 (无总标题与副标题) ---
plt.subplots_adjust(left=0.07, right=0.96, top=0.92, bottom=0.06)


def main():
    out_dir = Path(__file__).resolve().parent.parent / "data"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "figure_dataset_composition_coverage.png"
    fig.savefig(out_path, dpi=300, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"Saved: {out_path}")
    return str(out_path)


if __name__ == "__main__":
    main()
