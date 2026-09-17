# DV-JusticeBench 补充实验 · 北大法宝 4,193 案筛选与 Prepare 阶段结果

> 本目录记录**补充实验**（回应 AI & Law 审稿意见的大规模扩展）中，对北大法宝 2023–2025 家庭暴力相关判决 4,193 案的**筛选（Stage 1–2）与 Prepare（Stage 3–4）**阶段的产出与质量结论。作答（answering）与评分（scoring）阶段仍**暂停在 `paused_before_answering`**，等待负责人明确解除后启动。

原始输入：`data/pkulaw_2023_2025_release/pipeline_ready_cases.json`（4,193 案）
本目录输出：脱敏三字段 + 每案 5 道法律争议题（共 **11,240 题**）+ 各阶段审计。

---

## 1. 流水线总览

```
输入: 4193 案
        │
Stage 1 关键词粗筛（title ∪ case_text ∪ judge_decision 含"家庭暴力/家暴"）
        │  → 保留 2594  剔除 1599
        ▼
Stage 2 模型三分类（DS 打 primary / secondary / irrelevant）
        │  → primary 204 · secondary 2197 · irrelevant 193
        ▼
选取范围（放宽档）: primary ∪ (secondary AND court_addressed_dv=true)
        │  → 231 案作主实验准备
        │  另外单独准备 2022 案 secondary 作补充分析集
        ▼
Stage 3 脱敏（DS 分别脱敏 title/case_text/judge_decision）
        │  → 出现跨字段编号漂移 40% 等问题
        ▼
Stage 4 Fix 1–4 质量修复
        ├── Fix 1: 修 `generate_questions()` 解析器（.split('、') 误伤问题）
        ├── Fix 2: 后处理正则消除数字粘连（"某女1 65000" 而非 "某女165000"）
        ├── Fix 3: 后处理正则清理具名法院、省市残留
        └── Fix 4: 合并三字段一次脱敏 + 输出 person_map，保证跨字段一致
        ▼
最终输出（此处即 `*_fix4.jsonl`）
```

---

## 2. 数据集统计

| 集合 | 案例数 | 每案题数 | 总题数 |
|------|-------:|--------:|-------:|
| **Primary（主实验集）** | **226** | 5 | **1,130** |
| **Secondary（补充分析集）** | **2,022** | 5 | **10,110** |
| 合计 | **2,248** | 5 | **11,240** |
| 失败案（超长文本 12–16k 字，脱敏超时）| 5 | — | — |

Primary 的争议类型分布（多标签）：
- 是否构成家暴 192
- 离婚事由 141
- 精神/物质损害赔偿 97
- 子女抚养影响 24
- 刑事量刑 11
- 人身安全保护令 6

---

## 3. 最终质量对比（v1 vs fix4）

自动化扫四类问题：问题起句截断 / 数字粘连 / 地名残留 / **原被告角色漂移**（关键指标）。

| 问题 | v1 primary | **fix4 primary** | v1 secondary | **fix4 secondary** |
|------|-----------|------------------|--------------|--------------------|
| 问题起句截断 | 51 (22.1%) | **0 (0.0%)** | 615 (30.4%) | **0 (0.0%)** |
| 数字粘连 | 26 (11.3%) | **0 (0.0%)** | 126 (6.2%) | **0 (0.0%)** |
| 地名残留 | 8 (3.5%) | **0 (0.0%)** | 16 (0.8%) | **1 (0.0%)** |
| **原被告漂移** | 87 (37.7%) | **5 (2.2%)** ⬇94% | 942 (46.6%) | **80 (4.0%)** ⬇91% |

- 20 案专家标注复核（`expert_review_sample_20.xlsx`）— 与自动扫描高度吻合（专家看到漂移 35% vs 自动扫 37.7%）
- fix4 剩余的 2–4% 漂移主要是**多方当事人（如三方以上）**等复杂案，属于误判空间较小的情形
- 附带产出 `person_map` 可用于事后审计每案的人物映射

---

## 4. 交付清单

### 筛选阶段
- `stage1_kept_case_ids.json` — 关键词粗筛保留的 2,594 个 case_id
- `stage2_classified.jsonl` — DS 三分类的完整明细（含 label / court_addressed_dv / dispute_types / confidence / reason）

### Prepare 阶段（★ 表示最终版）
```
prepare_output/                            # Primary 226 案
├── masked_cases_fix4.jsonl              ★ 三字段脱敏 + person_map
├── questions_fix4.jsonl                 ★ 226 × 5 题
├── checkpoint_fix4.json                    state=paused_before_answering
└── failures_fix4.jsonl                     5 案超长文本超时

prepare_output_secondary/                  # Secondary 2022 案
├── masked_cases_fix4.jsonl              ★ 三字段脱敏 + person_map
├── questions_fix4.jsonl                 ★ 2022 × 5 题
├── checkpoint_fix4.json                    state=paused_before_answering
└── failures_fix4.jsonl                     0
```

### 审计
- `expert_review_sample_20.xlsx` — 20 案专家标注抽样（脱敏/出题质量 1-5 分 + 备注）

---

## 5. 关键设计决策

### 5.1 模型部署：本地 DGX vs xhub 代理

本次 DeepSeek 用**本地 vLLM 部署**（`deepseek_v4_flash`, 3 节点 dgx-061/103/104），只在作答阶段的 GPT-4o / Gemini / Qwen 走 xhub 代理。

配置见 `config.py` 新增的：
- `DEEPSEEK_API_URLS` — 逗号分隔多节点，`utils/deepseek_api.py` 内实现轮询
- `DEEPSEEK_MODEL_CHAT` / `DEEPSEEK_MODEL_REASONER` — 模型名可覆盖
- `DEEPSEEK_HTTP_TIMEOUT` — 长文本处理时提到 300–600s

### 5.2 Fix 4 的合并脱敏 Prompt（专家迭代版）

关键改动：
- 一次调用输入三字段，返回 person_map + 三字段脱敏
- 显式要求同一自然人在三字段用相同编号
- 性别推断只在"该词明确以该自然人为主体"时使用
- 原被告身份以案情"当事人/审理经过"为准，裁判理由必须对齐
- 数字与人物编号之间强制留空格或标点
- 具名法院整体替换为"某人民法院"

完整 prompt 见 `scripts/pkulaw_supplementary/remask_fix4.py`。

### 5.3 长文本处理策略

Prepare 阶段观察到 case_text 超过 8,000 字的案子容易触发超时。策略：
- 5 案超长（12–16k 字）在旧流水线中失败 → 首次跑时把 case_text 截到 8,000 字重跑成功
- Fix 4 版本沿用 case_text ≤ 8,000 字策略（在 masked_cases_fix4.jsonl 里以 `case_text_truncated_from` 字段标记）

---

## 6. 已知限制

1. **5 个 primary 案例仍失败**：超长文本（合计 12–16k 字），三次重试都在 600s 内没有完成响应
2. **fix4 剩余 5+80 案疑似角色漂移**：多为多方当事人案，实际很多是"某女" vs "某女1" 漏数字的误报
3. **`generate_questions()` prompt 仍是家暴导向**：如果 secondary 里混入非家暴主题案子，出题会不太贴合（filtering stage 已经把非 DV 案剔除，但仍需在 secondary 使用时留意）
4. **未做 Cohen's kappa 人工复核**：补实验文档要求做，可后续用 `expert_review_sample_20.xlsx` 扩大到 100+ 案后计算

---

## 7. 下一步：作答与评分

**当前 checkpoint 状态**：`paused_before_answering`

按 `../../北大法宝_4193案例实验工程交接.md` 规定，作答阶段（三个商业模型答题 + DS 评分）**必须等负责人明确解除暂停** 且完成预算确认后才能启动。

- 作答模型：GPT-4o / Gemini 2.5 Flash / Qwen-Max（走 xhub 代理）
- 每案 5 题 × 3 模型 = 15 份回答 × 2248 案 = **33,720 份回答**
- 评分：仍由 DeepSeek v4 flash（本地 DGX）按原 Rubric 五维评分

---

## 8. 复现命令

```bash
# 环境变量（本地 DGX 部署）
export DEEPSEEK_API_URLS="http://dgx-061:8012/v1/chat/completions,http://dgx-103:8012/v1/chat/completions,http://dgx-104:8012/v1/chat/completions"
export DEEPSEEK_MODEL_CHAT=deepseek_v4_flash
export DEEPSEEK_MODEL_REASONER=deepseek_v4_flash
export DEEPSEEK_HTTP_TIMEOUT=600

# Stage 1+2 筛选
python3 scripts/pkulaw_supplementary/filter_pipeline.py all

# Stage 3 Fix 4 合并脱敏
python3 scripts/pkulaw_supplementary/remask_fix4.py all 10

# Stage 3 后处理（清 Q2/Q3 残留）
python3 scripts/pkulaw_supplementary/postprocess_mask.py

# Stage 4 出题（用 fix4 mask）
python3 scripts/pkulaw_supplementary/regen_questions_fix4.py

# 质量扫描
python3 scripts/pkulaw_supplementary/final_scan.py
```

---

*最后更新: 2026-09-16*
