# DV-JusticeBench 补充实验方案（面向 *Artificial Intelligence and Law*）

> 版本：v1.0  
> 制定日期：2026-08-17  
> 目标：补齐全量跨模型证据、增加大规模稳健性测试，并建立可与模型直接比较的纯人工基线。  
> 价格口径：文中 API 单价按 2026-08-17 可查的官方公开价格估算；执行前必须重新截图或存档价格页。人民币换算仅采用内部预算汇率 `1 USD = 7.20 CNY`，不是汇率预测。

## 1. 执行摘要

建议采用三级、相互补充的实验结构：

1. **主实验：108 cases 全量跨模型评测**  
   对现有 108 个案例、每案 5 个问题全部进行跨模型推理，即每个模型 540 个回答。以 6 个核心条件为主，并将 GPT-5 作为可选补充条件。主实验回答“不同模型在同一批价值密集型司法问题上的平均质量、维度差异和严重错误风险是否稳定”。
2. **外部稳健性实验：2,000 cases 压力测试**  
   每案只保留 1 个预先定义的高风险问题；200 个锚定案例由全部 6 个模型回答，其余 1,800 个案例按分层随机方式只分配给 1 个模型。总计约 3,000 个模型回答，而不是完整交叉设计所需的 12,000 个回答，减少约 75% 的生成和评估量。
3. **纯人工基线：30 cases / 150 questions**  
   3 名法律专家在与模型相同的材料、法条包和输出约束下独立作答。采用平衡不完全区组设计，每题至少由 2 名专家回答，其中 30 题由 3 名专家全部回答，共约 330 份纯人工答案。人工答案与 3 个预先指定的锚定模型答案混合、去来源、盲评。

不得把以下三件事混称为“专家打标”：

- 108 个案例的结构化字段和来源核验；
- 540 个问题的专家审阅与修订；
- 模型回答的人工评分或人工答案基线。

补充实验应分别报告三者的样本量、人员数量、分配规则和一致性指标。

## 2. 推荐规模与预算总览

### 2.1 推荐方案

| 实验模块 | 推荐规模 | 主要产出 | API 预算 | 人工与数据预算 | 为什么适合 AI & Law 级别 | 规模依据 |
|---|---:|---|---:|---:|---|---|
| 108-case 全量跨模型 | 108 cases × 5 questions × 6 core models = 3,240 outputs；可选 GPT-5 后为 3,780 outputs | 完整模型排名、五维差异、严重错误、案例级置信区间 | 约 ¥2,500；含 GPT-5 和双自动评分器约 ¥2,700–3,500 | 若单独做 10% 双人复核约 ¥33,000–67,000；与人工基线合并后可降至约 ¥20,000–40,000 | 540 个专家审阅问题已达到中型、专家密集型法律推理基准的合理规模；重点应转向完整交叉、案例聚类统计和可复现性，而不是继续扩大同质问题 | AI & Law 已发表研究使用 [200 个法律任务、4,000 个模型回答、800 次人工评阅](https://link.springer.com/article/10.1007/s10506-024-09406-w)；[PLawBench](https://arxiv.org/abs/2601.16669) 使用 850 个问题评测 10 个模型；[LegalBench](https://proceedings.neurips.cc/paper_files/paper/2023/hash/89e44582fd28ddfea1ea4dcb0ebbf4b0-Abstract-Datasets_and_Benchmarks.html) 包含 162 个任务并评测 20 个模型 |
| 2,000-case 压力测试 | 2,000 cases × 1 question；200-anchor × 6 models + 1,800 single-assignment = 3,000 outputs | 拒答、超时、截断、旧法、虚构法源、事实反转和长尾稳定性 | 约 ¥2,200–3,000 | 自动清洗后的人工审计约 ¥33,000–66,000；若从原始裁判文书开始，再加数据工程/法务质检约 ¥30,000–80,000 | 该规模不用于精细模型排序，而用于估计低频但高风险的失败率；2,000 案可把 5% 总体错误率的 95% 区间半宽压到约 1 个百分点 | [LegalBench-RAG](https://arxiv.org/abs/2408.10343) 包含 6,858 个专家标注问答对；[PLawBench](https://arxiv.org/abs/2601.16669) 为 850 个真实法律实践问题。2,000 案位于二者之间，适合作为广覆盖压力测试而非全人工精评 |
| 纯人工基线（推荐） | 30 cases × 5 questions；3 名专家；约 330 份人工答案；另取 3 个模型作盲评锚点 | 人类表现分布、人类间差异、模型相对人类的差距、自动评分器校准 | 模型答案可复用主实验结果，增量 API 成本接近 0 | 约 ¥112,000–229,000 | 纯人工基线的价值在于角色隔离、盲评和同条件比较，不在于把全部 540 题都交给专家作答 | AI & Law 的 [Comma](https://link.springer.com/article/10.1007/s10506-026-09520-x) 人评采用 20 份裁判、3 名法律专家、每位约 320 个复杂样本；前述德国商法研究另有 800 次人工案例评阅 |

### 2.2 总预算区间

| 版本 | 纯人工基线 | 108-case 人工审计 | 2,000-case 审计 | 数据准备 | API | 总预算 |
|---|---:|---:|---:|---:|---:|---:|
| 最低可发表版 | 20 cases / 100 questions | 与人工基线最大化合并 | 只复核 5% 随机样本及全部严重告警 | 已有干净 2,000-case 数据池 | ¥5,000–8,000 | **约 ¥128,000–255,000** |
| 推荐版 | 30 cases / 150 questions | 补足模型与案由分层审计 | 5% 随机样本、全部严重告警、双人复核 | 已有干净 2,000-case 数据池 | ¥5,000–8,000 | **约 ¥170,000–340,000** |
| 推荐版（从原始文书开始） | 同上 | 同上 | 同上 | 增加去重、解析、脱敏、抽样质检 | ¥5,000–10,000 | **约 ¥200,000–420,000** |

说明：人工成本是预算假设而非市场均价。本文按普通法律专家 `¥300–600/小时`、专家答案撰写 `¥400–800/小时`、高级仲裁/方法负责人 `¥600–1,000/小时`估算。执行时应以实际合作协议替换。

## 3. 实验一：108 cases 全量跨模型补充实验

### 3.1 研究问题

- RQ1：现有 20-case 排名扩展到全部 108 cases 后是否稳定？
- RQ2：模型差异主要来自案例构成、问题维度，还是模型本身？
- RQ3：平均得分相近的模型是否具有不同的严重错误和尾部风险？
- RQ4：thinking 与 non-thinking 模式的差异，在全部案例上是否仍然存在？

### 3.2 实验单位与任务量

- 一级实验单位：`case_id`，共 108 个。
- 二级重复测量：每案 5 个问题，共 540 个问题。
- 模型条件：建议 6 个核心条件，并将 GPT-5 作为可选第 7 条件。
- 核心 6 条件的回答量：`108 × 5 × 6 = 3,240`。
- 加入 GPT-5 后的回答量：`108 × 5 × 7 = 3,780`。
- 不得将 540 个问题当作 540 个独立案例；推断统计必须按 case 聚类。

### 3.3 模型版本策略

补充实验必须选择以下两种策略之一，不能混合：

**策略 A：历史复现实验**

- 使用原始实验中完全相同的模型快照、端点和参数。
- 任一历史快照不可访问时，不得静默替换为同名新版本。
- 不可访问的模型作为缺失条件报告，或者整套实验切换到策略 B。

**策略 B：当代模型更新实验（更推荐）**

- 在同一时间窗口内对全部模型重新运行 108 cases。
- 使用执行日可调用的固定快照，而非可变的 `latest` 别名。
- 新结果单独命名为 `DV-JusticeBench-2026Q3`，不得与旧 20-case 分数直接拼表。

当前预算表暂按论文现有模型家族计算：GPT-4o、Claude Opus 4、Gemini 2.5 Flash、Qwen-Max、DeepSeek thinking、DeepSeek non-thinking，并将 GPT-5 列为可选条件。需要注意，[Claude Opus 4 官方价格页](https://platform.claude.com/docs/en/about-claude/pricing)显示其已退出大部分直接渠道，[DeepSeek 官方价格页](https://api-docs.deepseek.com/quick_start/pricing/)也显示旧 `deepseek-chat` / `deepseek-reasoner` 别名已迁移或退役，因此执行前必须完成端点可用性检查。

### 3.4 推理协议

- 每个问题开启全新会话，不携带前题历史。
- 所有模型使用同一匿名案例材料、同一问题文本和同一输出结构。
- 禁止联网、搜索、工具调用和外部 RAG，除非所有条件均使用同一冻结法条库。
- 主运行温度保持与论文一致；建议 `temperature = 0.3`。
- 输出上限、reasoning budget、超时、重试规则必须预注册。
- 只允许：一次瞬时网络错误重试、一次明确截断重试。拒答和稳定性失败不得被无限重跑后覆盖。
- 所有失败均保留原始记录，`failure` 本身是可靠性结果。
- 记录 `provider`、`model_id`、snapshot、请求参数、prompt hash、response id、token usage、latency、finish reason、error、retry count 和时间戳。

### 3.5 评分架构

建议采用“三层评分”，避免把单一 LLM 评分器伪装成人工金标准：

1. **自动主评分器**：对全部输出进行五维评分和错误标记。
2. **自动敏感性评分器**：使用不同模型家族，对全部输出或至少全部锚定输出复评。
3. **人工校准集**：至少覆盖全部输出的 10%，按模型、案由、问题维度、分数分位和错误类型分层抽取；每份由 2 名法律专家独立评分，分歧样本由第三人处理。

评分前必须：

- 移除模型名称、provider 字段和可识别的固定话术；
- 随机化输出顺序；
- 将同一问题的不同模型回答分散呈现，避免排序和锚定效应；
- 保存原始分数、裁罚前分数、错误标记和裁罚后分数；
- 明确主评分是人工还是自动。如果主体仍是自动评分，应写成 “LLM-based evaluation calibrated against blinded legal-expert ratings”。

### 3.6 统计分析

主终点：五维合计分数（0–20）。

建议模型：

```text
score ~ model + dimension + model:dimension + case_type
        + (1 | case_id) + (1 | case_id:question_id)
```

并行报告：

- 案例聚类 bootstrap 95% CI，至少 10,000 次重采样；
- 同题配对的模型差值及 Holm 多重比较校正；
- 拒答、超时、旧法引用、虚构法源、核心事实反转等二元结果的混合效应 logistic 回归或 GEE；
- 10th percentile、最差案例均值和 Conditional Value-at-Risk，避免只报告平均分；
- 每种模型的失败率分母必须包含拒答和空答案，不能只按成功回答计算。

### 3.7 基于现有 pilot 的规模论证

对现有 [20-case 工作簿](<data/results_20260112_unified_e8fd22b9/20个案例_统一评估结果_108cases.xlsx>)按案例计算后：

- 不同模型的 case-level 均值标准差约为 `1.9–2.7` 分；
- 20 cases 时，模型均值 95% CI 的半宽约为 `0.84–1.19` 分；
- 扩展到 108 cases 后，预计半宽缩至约 `0.36–0.51` 分；
- 以 pilot 中同案模型差值的离散程度估计，108 cases 对配对模型差异的 80% power 最小可检出差异约为 `0.39–0.63` 分。

因此，108 cases 的主要价值是提高估计精度并稳定相邻模型比较。样本量理由同时采用 pilot 精度和敏感性功效，而不是只援引“同领域通常做多少”。这种写法符合 Lakens 关于按估计精度、最小关注效应和功效解释样本量的建议：[Sample Size Justification](https://doi.org/10.1525/collabra.33267)。

## 4. 实验二：2,000 cases 压力测试

### 4.1 目标

压力测试不是第二套完整排行榜。它回答的是：在更宽的案例分布、文书长度和程序类型下，系统的失败率和长尾风险是否仍然可接受。

优先终点：

- API 失败、超时、空回答、拒答和截断率；
- 旧法、错误条号、虚构法源和引用—命题不匹配；
- 核心事实反转、当事人混淆和程序阶段混淆；
- 输出长度、token、延迟和成本；
- 自动评分器与人工复核在严重错误上的召回率和精确率。

### 4.2 问题生成

- 每案只生成或选择 1 个 `sentinel question`，总计 2,000 个问题。
- 五类问题维度尽量均衡，每个维度约 400 案。
- 问题必须指向裁判中的真实争点，不做纯事实回忆。
- 问题生成器与被测模型分离；问题不能依据某个被测模型的失败结果事后修改。
- 先在 100 个不进入主分析的案例上冻结问题模板和质量门槛。

### 4.3 不完全交叉设计

采用锚定式平衡不完全区组设计：

| 部分 | 案例数 | 每案模型数 | 输出数 | 用途 |
|---|---:|---:|---:|---|
| Anchor set | 200 | 6 | 1,200 | 直接跨模型比较和压力集校准 |
| Breadth set | 1,800 | 1 | 1,800 | 广覆盖失败率和分布外风险 |
| 合计 | 2,000 | — | 3,000 | 比 2,000 × 6 = 12,000 的完整交叉少 75% |

Breadth set 必须按以下变量分层随机分配模型：

- 民事/刑事/行政及主要案由；
- 审级和程序阶段；
- 年份；
- 地区；
- 文书长度和争点复杂度；
- 五个问题维度。

每个核心模型最终约获得 500 个案例：200 个公共 anchor + 约 300 个独立 breadth 案例。

### 4.4 精度说明

- 每模型约 `n = 500` 时，若某严重错误率为 5%，简单二项近似下 95% CI 半宽约为 1.9 个百分点；若错误率为 10%，半宽约为 2.6 个百分点。
- 2,000 案总体上，5% 错误率的区间半宽约为 1 个百分点。
- 该精度足以判断低频但高风险故障是否为偶发事件，也足以支撑分层错误画像。

### 4.5 人工审计

至少人工审计：

- 5% 分层随机样本，即约 150 个输出；
- 所有自动系统标记为 major error 的输出；
- 自动评分器之间差值超过预注册阈值的输出；
- 至少 5% 的自动系统判定“无错误”样本，用于估计漏检率。

人工审计采用双人独立标注；第三人只处理严重类别不一致或任一维度相差超过 1 分的样本。对压力测试而言，人工重点是错误存在性与严重度，不要求对所有 3,000 个回答完成五维精细打分。

## 5. 实验三：纯人工 baseline

### 5.1 为什么不能直接把法官判决当作人类 baseline

法官判决可以作为 reference decision，但不应直接算作“人类答题表现”：问题本身由判决反向生成，评分规则又强调与判决一致，直接给判决打分会形成结构性循环。纯人工 baseline 应当让法律专家在看不到模型答案和评分结果的情况下，独立完成与模型相同的任务。

### 5.2 推荐设计

- 从 108 cases 中分层抽取 30 cases，共 150 questions。
- 预先保证民刑比例、案由、审级、地区、年份、问题维度和难度覆盖。
- 3 名具有相应法律训练和实务/研究经验的专家担任 answer writers。
- 每题由 2 名专家独立作答；另抽 30 题由第 3 名专家也作答。
- 人工答案总量：`150 × 2 + 30 = 330`。
- 提供与模型完全相同的匿名事实材料、问题和冻结法条包。
- 人类与模型均禁止互联网检索；若允许法条包，则 3 个锚定模型必须在相同法条包条件下重新运行。
- 每题建议限时 15 分钟、正文上限约 1,200 个汉字；超限只记录，不事后截断正文。

最低预算方案可以退至 20 cases / 100 questions，但必须在分析前预注册，不能根据结果是否显著决定样本量。

### 5.3 共同盲评

在 150 题上预先指定 3 个模型锚点：一个现有 pilot 高分模型、一个中位模型、一个低分或高错误模型。不能在 108-case 新结果出来后再挑选最有利于论文叙述的模型。

共同评分池约包括：

- 330 份人工答案；
- `150 × 3 = 450` 份模型答案；
- 合计约 780 份答案。

评分要求：

- 评分者不能看到答案来源；
- 评分者不得评分自己写的答案；
- 每份答案至少由 2 名独立评分者评分；
- 20% 样本由第 3 名评分者复评；
- 人工答案和模型答案混合、随机、去模型化话术后呈现；
- 报告人类个体、human pooled、模型个体三类结果，不能只报告一个“专家平均分”。

一致性指标：

- 0–4 有序评分：优先报告 weighted Krippendorff's alpha 或 ordinal ICC；
- 多名评分者不应笼统写成 “Cohen's kappa across three experts”；
- 二元错误标签可报告 Fleiss' kappa、Krippendorff's alpha 和原始一致率；
- 同时报告每个维度的分歧分布和仲裁比例。

### 5.4 人工基线成本拆分

推荐 30-case 设计的预算公式：

| 工作 | 数量与假设 | 估计工时 | 费率假设 | 预算 |
|---|---|---:|---:|---:|
| 专家独立作答 | 330 answers × 15 min | 82.5 h | ¥400–800/h | ¥33,000–66,000 |
| 盲评第一、第二评分 | 780 outputs × 2 ratings × 8 min | 208 h | ¥300–600/h | ¥62,400–124,800 |
| 20% 第三评分 | 156 outputs × 8 min | 20.8 h | ¥300–600/h | ¥6,240–12,480 |
| 培训、试标和仲裁 | 约 16–26 h | 16–26 h | ¥600–1,000/h | ¥9,600–26,000 |
| **合计** | — | — | — | **约 ¥111,000–229,000** |

此规模并非随意选择。AI & Law 的 Comma 研究使用 20 份裁判、3 名法律专家，并让每位评价约 320 个复杂样本；本方案的最低版与其人评案例规模相当，推荐版提高到 30 案以增强案由覆盖和人类基线稳定性。人评界面、随机化、评分说明和样本量都应完整披露，参见 [Best practices for the human evaluation of automatically generated text](https://aclanthology.org/W19-8643/) 和 [GENIE](https://aclanthology.org/2022.emnlp-main.787/)。

## 6. API 成本明细

### 6.1 计算公式

```text
cost = outputs × (input_tokens × input_price + output_tokens × output_price)
       / 1,000,000 × 1.25
```

其中 1.25 是 token 波动、一次重试和少量截断重跑的预算缓冲。表内没有计算缓存或 batch 折扣，因此偏保守。

单回答 token 预算以现有 [20-case pilot 的汇总记录](README.md#-leaderboard)和 [详细运行报告](data/results_20260112_unified_e8fd22b9/results_详细报告_20260112_172609.txt)为起点；现有不同模型平均总 token 约为 3,126–7,684。正式运行时应先用 20 个非分析案例重新测量各模型的 input、visible output 和 reasoning tokens，再锁定最终预算。

### 6.2 108-case 回答生成成本

| 条件 | 官方价格（USD / 1M tokens） | 单回答预算 | 540 answers（含 25% buffer） | 人民币约值 | 官方来源 |
|---|---:|---:|---:|---:|---|
| GPT-4o | input $2.50 / output $10 | 3k input + 2k output | $18.56 | ¥134 | [OpenAI GPT-4o](https://developers.openai.com/api/docs/models/gpt-4o) |
| Claude Opus 4 | input $15 / output $75 | 3k input + 5k output | $283.50 | ¥2,041 | [Anthropic pricing](https://platform.claude.com/docs/en/about-claude/pricing) |
| Gemini 2.5 Flash | input $0.30 / output $2.50 | 3.2k input + 2.7k output | $5.20 | ¥37 | [Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing) |
| Qwen-Max（中国大陆） | input $0.345 / output $1.377 | 2.8k input + 2.9k output | $3.35 | ¥24 | [Alibaba Cloud Model Studio pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing) |
| DeepSeek V4 Pro thinking | input $0.435 / output $0.87 | 3k input + 5k output | $3.82 | ¥27 | [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/) |
| DeepSeek V4 Pro non-thinking | input $0.435 / output $0.87 | 3k input + 2k output | $2.06 | ¥15 | [DeepSeek pricing](https://api-docs.deepseek.com/quick_start/pricing/) |
| GPT-5（可选） | input $1.25 / output $10 | 3k input + 3k output | $22.78 | ¥164 | [OpenAI GPT-5](https://developers.openai.com/api/docs/models/gpt-5) |
| **合计：6 core models** | — | 3,240 answers | **$316.49** | **¥2,279** | — |
| **合计：含 GPT-5** | — | 3,780 answers | **$339.27** | **¥2,443** | — |

双自动评分器的保守增量预算：

- DeepSeek V4 Flash 主评分器：约 $7.28；
- GPT-5 mini 敏感性评分器：约 $23.63；
- 合计约 $30.91，即约 ¥223。

因此 108-case、7 条件、双自动评分器的 API 预算约为 **¥2,700**；建议实拨 **¥3,500**，用于价格变化、失败重试和日志复跑。

### 6.3 2,000-case 压力测试成本

锚定式不完全交叉设计共约 3,000 个回答，每个核心模型约 500 个回答：

- 6 个模型回答生成：约 $293，即约 ¥2,110；
- 自动主评分全部 3,000 个回答，第二评分器复评 anchor 与高风险样本：约 $15–25，即约 ¥110–180；
- 自动解析、脱敏和 sentinel-question 生成：按低价模型计通常低于 ¥200，但建议预留 ¥500；
- API 总预算建议按 **¥3,000** 立项。

如果将 Claude Opus 4 换成较低价的同厂商活跃模型，API 成本会明显下降；但这属于模型条件变更，必须在实验开始前确定并重新命名实验版本。

## 7. 质量门槛与停止规则

### 7.1 模型运行门槛

- 某条件成功回答率低于 90%：不进入主质量排名，但保留在可靠性结果中。
- 某条件因端点变更导致前后不是同一快照：暂停该条件；不得把两个版本合并。
- 单个问题重试超过预注册上限：保留失败，不继续“刷成功答案”。
- 发现 prompt 泄露模型名称、参考答案或评分标签：受影响批次全部作废重跑。

### 7.2 自动评分器门槛

- 与人工评分的 ordinal agreement 未达到预注册阈值时，自动分数只能作为次要或探索性结果。
- 建议目标：加权一致性或 ICC 不低于 0.75；严重错误召回率不低于 0.90。
- 不只报告相关系数；同时报告绝对分差、混淆矩阵、漏判严重错误数和按模型分组的偏差。
- LLM judge 可能存在位置、长度和自我偏好，应保持盲评、多评分器和人工校准；可参考 [MT-Bench / Chatbot Arena 的 LLM-as-a-Judge 研究](https://arxiv.org/abs/2306.05685)。

### 7.3 人工评分门槛

- 正式标注前完成不少于 20 个训练样本的试标。
- 任一维度一致性明显低于其他维度时，先修订说明并重新试标，不能直接进入正式评分。
- 保存每位评分者的原始分数，不用仲裁分覆盖原始分。
- 报告评分者培训、专业背景、费率或补偿方式、平均用时、分歧率和退出情况。

## 8. 数据和代码交付物

至少保留下列可审计文件：

```text
experiment_manifest.yaml       # 实验版本、模型、快照、参数、日期
case_manifest.csv              # 案例分层变量、纳入排除原因
question_manifest.csv          # 问题维度、生成与人工修订记录
run_log.jsonl                  # 每次 API 调用、token、延迟、错误和重试
responses.jsonl                # 原始模型输出；不可只保存最终 Excel
scores_llm_raw.csv             # 每个自动评分器的原始分数
scores_human_raw.csv           # 每位专家的原始分数
adjudication_log.csv           # 仲裁原因和最终决定
human_answers.jsonl            # 纯人工答案及匿名 author_id
analysis.py / analysis.R       # 统计分析脚本
environment.lock              # 依赖版本
cost_ledger.csv                # 每次调用与每项人工工作的实际成本
README.md                      # 一键复现说明
```

对外发布时可依据伦理和裁判文书许可限制提供脱敏版本，但论文审稿阶段至少应能提供可验证的汇总、哈希和审计轨迹。

## 9. 推荐实施顺序

1. **第 1 周：冻结协议**  
   冻结模型条件、提示词、法条包、重试规则、主终点、人工抽样和统计计划。
2. **第 2 周：管线小样本验证**  
   使用不进入分析的 10–20 个案例验证调用、日志、去标识化和评分器。
3. **第 2–3 周：完成 108-case 全量运行**  
   先生成回答，再统一盲化和评分；不得边看排名边改 prompt。
4. **第 3–6 周：纯人工 baseline**  
   专家作答、盲评、第三评分和仲裁并行进行。
5. **第 4–6 周：2,000-case 压力测试**  
   先完成 200-case anchor，再进行 1,800-case 分层单模型分配。
6. **第 7 周：锁库和统计**  
   冻结数据版本后运行预注册分析，生成主表、置信区间和错误案例附录。

## 10. 论文中建议采用的口径

建议在 Methods 中明确写成：

> The benchmark comprises 108 expert-verified cases and 540 expert-reviewed questions. All 540 questions were evaluated across the full set of model conditions. To assess out-of-distribution reliability at lower cost, we additionally conducted a 2,000-case anchored incomplete-block stress test, in which 200 cases were evaluated by all models and the remaining 1,800 cases were randomly assigned to one model within predefined strata. A separate human baseline was constructed on a stratified 30-case subset: legally trained experts independently answered the same questions under matched information and time constraints, and human and model responses were blindly rated using the same rubric.

如果实际执行缩减为 20-case 人工基线，应将 `30-case` 如实改成 `20-case`，并说明这是基于资源约束和同领域人评先例的预注册选择。

## 11. 核心参考文献与价格来源

### 11.1 实验规模和方法

1. Weidlich et al., *The digital transformation of jurisprudence: an evaluation of ChatGPT-4’s applicability to solve cases in business law*, **Artificial Intelligence and Law**：200 个法律任务、4,000 个模型回答、800 次人工案例评阅。<https://link.springer.com/article/10.1007/s10506-024-09406-w>
2. *Comma: A multi-task and multi-lingual dataset of constitutional verdicts*, **Artificial Intelligence and Law**：人评部分使用 20 份裁判、3 名法律专家、每位 320 个复杂样本。<https://link.springer.com/article/10.1007/s10506-026-09520-x>
3. Guha et al., *LegalBench*：162 个法律推理任务，评测 20 个开源和商业模型。<https://proceedings.neurips.cc/paper_files/paper/2023/hash/89e44582fd28ddfea1ea4dcb0ebbf4b0-Abstract-Datasets_and_Benchmarks.html>
4. Shi et al., *PLawBench*：850 个问题、13 类实践场景、约 12,500 个 rubric items，评测 10 个模型。<https://arxiv.org/abs/2601.16669>
5. Pipitone and Alami, *LegalBench-RAG*：6,858 个专家标注问答对。<https://arxiv.org/abs/2408.10343>
6. Lakens, *Sample Size Justification*：建议依据目标精度、最小关注效应和功效，而不是只使用领域惯例。<https://doi.org/10.1525/collabra.33267>
7. van der Lee et al., *Best practices for the human evaluation of automatically generated text*。<https://aclanthology.org/W19-8643/>
8. Khashabi et al., *GENIE: Toward Reproducible and Standardized Human Evaluation for Text Generation*。<https://aclanthology.org/2022.emnlp-main.787/>
9. Zheng et al., *Judging LLM-as-a-Judge with MT-Bench and Chatbot Arena*。<https://arxiv.org/abs/2306.05685>

### 11.2 官方价格页

- OpenAI GPT-4o：<https://developers.openai.com/api/docs/models/gpt-4o>
- OpenAI GPT-5：<https://developers.openai.com/api/docs/models/gpt-5>
- Anthropic Claude：<https://platform.claude.com/docs/en/about-claude/pricing>
- Google Gemini API：<https://ai.google.dev/gemini-api/docs/pricing>
- DeepSeek API：<https://api-docs.deepseek.com/quick_start/pricing/>
- Alibaba Cloud Model Studio / Qwen：<https://www.alibabacloud.com/help/en/model-studio/model-pricing>

## 12. 最终决策建议

- **必须做**：108 cases × 5 questions 的完整跨模型运行；这是解决当前 20-case 结果外推问题的核心证据。
- **必须做**：独立、盲评、同条件的纯人工 baseline；最低 20 cases / 100 questions，推荐 30 cases / 150 questions。
- **推荐做**：2,000-case 锚定式不完全区组压力测试；不要做 2,000 × 全模型 × 5 问题的暴力穷举。
- **不推荐做**：让单一 LLM 同时生成、评分并决定错误，再把结果称为专家标注。
- **不推荐做**：为了节省人工费用，把法官原判直接当成人类答题 baseline。
- **预算优先级**：先保证人工 baseline 和严重错误审计，再增加更多模型。API token 费不是主要成本，可靠的法律专家时间才是主要成本。
