# DVJUSTICE: A Benchmark for Value-Laden Legal Reasoning in Domestic Violence Cases

<div align="center">

[![License](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11+-green.svg)](https://www.python.org/)
[![Benchmark](https://img.shields.io/badge/benchmark-DVJUSTICE-orange.svg)](#benchmark-overview)

**A systematic benchmark for evaluating Large Language Models on value-laden legal reasoning in Chinese domestic violence adjudication**

[Benchmark](#benchmark-overview) • [Quick Start](#quick-start) • [Results](#experimental-results) • [Leaderboard](#leaderboard)

</div>

---

## 📖 Overview

**DVJUSTICE** is a novel benchmark designed to measure how well Large Language Models (LLMs) handle **value-laden adjudication** in complex domestic violence cases within the Chinese legal system. Unlike prior legal AI benchmarks that focus on multiple-choice questions or bar exam tasks, DVJUSTICE targets the discretionary layer of judging—where doctrine meets relational context and moral stakes.

### Why DVJUSTICE?

- **🎯 Domain-Specific**: Focuses on domestic violence cases where value judgments are central, not peripheral
- **📏 Structured Evaluation**: Five-dimension rubric grounded in civil-law deductive reasoning and value balancing
- **🔍 Reliability Signals**: Tracks concrete failure modes (e.g., abandoned-law citations, empty outputs) beyond average scores
- **🌍 Real-World Grounding**: Built from 108 actual Chinese court decisions, including Supreme People's Court typical cases
- **🤖 Multi-Model Coverage**: Evaluates 7 state-of-the-art LLMs (GPT-4o, GPT-5, Claude Opus 4, Gemini 2.5 Flash, Qwen-Max, DeepSeek-V3, DeepSeek-R1)

### Key Contributions

1. **Novel Benchmark**: 108 cases → 540 structured questions targeting normative reasoning, subsumption chains, evidence appraisal, and empathy alignment
2. **Operationalized Rubric**: Six quantifiable dimensions adapted to Chinese civil-law adjudication, with inter-annotator agreement ≥75%
3. **Meta-Evaluation Layer**: Tests whether LLMs can grade legal reasoning with stability (LLM-as-a-judge reliability)
4. **Comprehensive Analysis**: Multi-dimensional evaluation including quality-reliability-cost trade-offs, tail risk, and abandoned-law citations

---

## 📊 Benchmark Overview

### Dataset Statistics

| Metric | Value |
|--------|-------|
| **Total Cases** | 108 |
| **Questions per Case** | 5 |
| **Total Questions** | 540 |
| **Case Source** | China Judgments Online + Supreme People's Court Typical Cases (2025) |
| **Domain** | Domestic Violence Adjudication |
| **Language** | Chinese |
| **Evaluation Dimensions** | 5 core dimensions + impactful error flags |
| **Score Range** | 0–20 points (0–4 per dimension) |
| **Cross-Model Slice** | 20 cases (100 questions) for multi-model comparison |

### Five-Dimension Rubric

DVJUSTICE evaluates LLM outputs on a 0–4 scale per dimension, totaling 20 points:

| Dimension | Description | Weight |
|-----------|-------------|--------|
| **Normative Basis Relevance** | Accuracy and operational relevance of cited statutes, interpretations, and legal authority | 4 pts |
| **Subsumption Chain Alignment** | Completeness of the deductive chain: issue → norm → elements/factors → facts → sub-conclusions → final conclusion | 4 pts |
| **Value Balancing & Empathy Alignment** | Reasonableness of value judgments, avoidance of victim-blaming or stigmatizing inferences | 4 pts |
| **Key Facts & Issue Coverage** | Accuracy in capturing decisive facts and disputes, with proper evidence mapping and no fabrication | 4 pts |
| **Outcome & Remedy Alignment** | Consistency of proposed disposition and relief with the reference court decision | 4 pts |

**Impactful Error Flags**: Fabricated norms, core fact inversion, abandoned-law citations, procedural confusion → trigger score caps and deductions.

Full rubric: [`static/evaluate/Scoring_Rubric_v1.0_English.md`](static/evaluate/Scoring_Rubric_v1.0_English.md)

### Four-Stage Evaluation Pipeline

```
┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐    ┌──────────────────┐
│  De-Identification│ -> │ Question Generation│ -> │  LLM Response   │ -> │   Evaluation    │
│  & Paraphrasing  │    │  (5 per case)      │    │  Generation     │    │  (Human + Meta) │
│  (LLM-assisted)  │    │  (LLM-assisted)    │    │  (Multi-model)  │    │  (Rubric-based) │
└──────────────────┘    └──────────────────┘    └──────────────────┘    └──────────────────┘
```

1. **De-Identification**: Automated de-identification and paraphrasing to prevent memorization and protect privacy
2. **Question Generation**: Five structured dispute questions per case, focusing on normative reasoning and value judgment
3. **Response Generation**: Multi-model parallel processing with standardized prompts (temperature=0.3, timeout=180s, max_tokens up to 16k)
4. **Evaluation**: Expert legal annotators score outputs using the five-dimension rubric, with optional meta-evaluation (LLM-as-a-judge)

### Evaluated Models

| Model | Provider | Version | Mode |
|-------|----------|---------|------|
| **DeepSeek-Thinking** | DeepSeek | R1 | Reasoning (CoT) |
| **DeepSeek** | DeepSeek | V3 | Chat (Standard) |
| **Gemini** | Google | 2.5 Flash | Standard |
| **Claude** | Anthropic | Opus 4 | Standard |
| **Qwen-Max** | Alibaba Cloud | Max | Standard |
| **GPT-4o** | OpenAI | 4o | Standard |
| **GPT-5** | OpenAI | 5 (Preview) | Standard (excluded from main ranking due to 65% empty-answer rate) |

---

## 🚀 Quick Start

### Prerequisites

- Python 3.11+
- API keys for evaluated models (OpenAI, Anthropic, Google, Alibaba Cloud, DeepSeek)

### Installation

```bash
# 1. Clone the repository
git clone https://github.com/chenqianwan/huangyidan1.git
cd huangyidan

# 2. Install dependencies
pip install -r requirements.txt

# 3. Configure environment variables
# Create a .env file with your API keys
DEEPSEEK_API_KEY=your_deepseek_api_key
OPENAI_API_KEY=your_openai_api_key
ANTHROPIC_API_KEY=your_anthropic_api_key
GOOGLE_API_KEY=your_google_api_key
ALIBABA_API_KEY=your_alibaba_api_key
```

### Run Evaluation

```bash
# Evaluate a single case with all models
./test_single_case_all_models.sh case_20251230_134952_90

# Evaluate multiple cases with a specific model
python process_cases.py \
  --model deepseek \
  --use_ds_questions data/108个案例_新标准评估_完整版_最终版.xlsx \
  --case_ids case_001 case_002 case_003 \
  --standalone

# Generate reports and charts for existing results
python scripts/generate_advanced_conference_charts.py
```

### Evaluate Your Own Cases

```python
from utils.ai_api import query_ai_api
from utils.evaluator import evaluate_response

# 1. Prepare your case (de-identified)
case_text = "..."
questions = ["Question 1?", "Question 2?", ...]

# 2. Get LLM responses
responses = []
for q in questions:
    resp = query_ai_api(model="deepseek", prompt=q, context=case_text)
    responses.append(resp)

# 3. Evaluate (requires expert annotation or meta-evaluation)
scores = [evaluate_response(r, rubric) for r in responses]
```

---

## 🏆 Leaderboard

Results on the 20-case evaluation slice (100 questions). Full benchmark (108 cases, 540 questions) results available upon request.

| Rank | Model | Avg Score (/20) | Percentage | Max | Min | 10th %ile | Abandoned Laws | Avg Tokens |
|------|-------|----------------|------------|-----|-----|-----------|----------------|------------|
| 🥇 1 | **DeepSeek-Thinking** | 16.45 | 82.3% | 20.00 | 2.60 | 14.00 | 7/100 (7.0%) | 5,038 |
| 🥈 2 | **DeepSeek** | 16.42 | 82.1% | 20.00 | 5.12 | 13.00 | 1/100 (1.0%) | 4,247 |
| 🥉 3 | **Gemini** | 15.63 | 78.1% | 20.00 | 7.00 | 12.00 | 0/100 (0.0%) | 3,126 |
| 4 | Claude | 13.11 | 65.5% | 19.40 | 3.20 | 9.00 | 10/100 (10.0%) | 7,684 |
| 5 | Qwen-Max | 10.36 | 51.8% | 19.40 | 0.00 | 6.00 | 7/100 (7.0%) | 5,663 |
| 6 | GPT-4o | 9.39 | 46.9% | 17.60 | 0.00 | 4.00 | 16/100 (16.0%) | 4,878 |
| — | GPT-5* | (16.60) | (83.0%) | 20.00 | 6.80 | — | 0/35 (0.0%) | — |

*\*GPT-5 excluded from main ranking due to 65% empty-answer rate (65/100 failures). Score shown is average of 35 successful responses.*

**Key Findings**:
- **Top Tier**: DeepSeek variants lead with >82% accuracy, statistically tied (CI overlap)
- **Reliability Gap**: Abandoned-law citations range from 0% (Gemini) to 16% (GPT-4o)
- **Cost-Quality Trade-off**: Claude uses 80% more tokens than DeepSeek but scores 3.3 points lower
- **Tail Risk**: DeepSeek-Thinking's 10th percentile (14.00) exceeds most models' averages

---

## 📊 Experimental Results

### Multi-Dimensional Performance Analysis

#### 1. Average Score Comparison

![Average Score](data/results_20260112_unified_e8fd22b9/chart_avg_score_20260117_235927.png)

The top two models (DeepSeek-Thinking: 16.45, DeepSeek: 16.42) are statistically indistinguishable. The gap between tiers is substantial: top tier outperforms GPT-4o by >7 points (35%).

#### 2. Quality-Reliability-Cost Trade-off (Pareto Frontier)

![Pareto Trade-off](data/results_20260112_unified_e8fd22b9/chart_pareto_tradeoff_20260117_235926.png)

DeepSeek variants occupy the efficiency frontier: highest scores with moderate token usage and superior reliability. No model dominates all three dimensions simultaneously.

#### 3. Abandoned-Law Citations (Reliability Signal)

![Abandoned Laws](data/results_20260112_unified_e8fd22b9/chart_abandoned_laws_20260117_235927.png)

Concrete failure mode: citing repealed or inapplicable authority. Gemini (0%) and DeepSeek (1%) excel; GPT-4o fails most often (16%).

#### 4. Five-Dimension Breakdown (Heatmap)

![Dimension Heatmap](data/results_20260112_unified_e8fd22b9/chart_heatmap_dimensions_20260117_235927.png)

Top-tier models excel in **Subsumption Chain Alignment** (3.39–3.42/4) and **Key Facts Coverage** (3.25–3.30/4). Lower-ranked models show steepest drops in these dimensions. **Value Balancing** remains challenging across all models (range: 2.29–3.28/4).

#### 5. Tail Risk Analysis (10th Percentile & CVaR@10%)

![Tail Risk](data/results_20260112_unified_e8fd22b9/chart_tail_risk_20260117_235927.png)

For legal applications, worst-case performance matters. DeepSeek-Thinking's 10th percentile (14.00/20) exceeds most models' averages. GPT-4o exhibits severe tail risk (10th: 4.00, CVaR: 5.70).

#### 6. Efficiency: Quality vs. Compute Cost

![Quality vs Tokens](data/results_20260112_unified_e8fd22b9/chart_quality_vs_tokens_20260117_235927.png)

DeepSeek variants achieve top scores with moderate token budgets (4,247–5,038). Claude uses 7,684 tokens (50–80% more) but scores lower. Token cost and quality are weakly correlated (r ≈ -0.10).

#### 7. Bootstrap Confidence Intervals (Statistical Robustness)

![Bootstrap CI](data/results_20260112_unified_e8fd22b9/chart_score_bootstrap_ci_20260117_235927.png)

95% confidence intervals (10,000 resamples) confirm ranking robustness despite moderate sample size. Top-tier CIs overlap; lower tiers fully separated.

#### 8. Additional Charts

<details>
<summary>Click to expand: Error Statistics, Token Usage, Score Distribution, Reliability Gating</summary>

**Impactful Error Breakdown**

![Errors](data/results_20260112_unified_e8fd22b9/chart_errors_20260117_235928.png)

GPT-4o: 156 total errors (2 major, 86 obvious, 68 minor). DeepSeek: 30 total (0 major, 4 obvious, 26 minor).

**Token Usage Comparison**

![Token Usage](data/results_20260112_unified_e8fd22b9/chart_token_usage_20260117_235928.png)

**Score Distribution (Violin Plot)**

![Distribution](data/results_20260112_unified_e8fd22b9/chart_distribution_20260117_235928.png)

**Reliability-Gated Ranking (Abandoned-Law Rate < 1%)**

![Reliability Gating](data/results_20260112_unified_e8fd22b9/chart_reliability_gating_20260117_235927.png)

Only DeepSeek (1.0%) and Gemini (0.0%) pass the 1% threshold.

**Success/Partial/Fail Percentage**

![Percentage](data/results_20260112_unified_e8fd22b9/chart_percentage_20260117_235928.png)

**Overall Ranking Visualization**

![Ranking](data/results_20260112_unified_e8fd22b9/chart_ranking_20260117_235928.png)

**Metrics Heatmap (Min/Max/Range per Dimension)**

![Metrics Heatmap](data/results_20260112_unified_e8fd22b9/chart_heatmap_metrics_20260117_235928.png)

</details>

---

## 📂 Data Access

### Benchmark Data

- **Full Benchmark** (108 cases, 540 questions): Available upon request for academic research
- **20-Case Evaluation Slice**: Included in `data/results_20260112_unified_e8fd22b9/20个案例_统一评估结果_108cases.xlsx`
- **Detailed Reports**: `data/results_20260112_unified_e8fd22b9/results_详细报告_20260112_172609.txt`
- **Chart Usage Guide**: `data/results_20260112_unified_e8fd22b9/图表使用指南_Chart_Guide.txt`

### Data Format

Each case entry includes:
- **Case ID**: Unique identifier
- **Background Facts**: De-identified narrative
- **Judicial Keywords**: Court-assigned tags
- **Judicial Reasoning**: Judge's analysis (reference)
- **Disposition**: Final ruling
- **Questions** (5 per case): Structured dispute questions
- **LLM Responses**: Outputs from each evaluated model
- **Scores**: Five-dimension scores + impactful error flags

### Data Request

For access to the full benchmark dataset, please contact:
- **Huang Yidan**: huangyidan@hkgai.org
- **Chen Long**: chenlong@hkgai.org

We require a brief description of your intended research use and agreement to the data use terms (non-commercial, research-only).

---

## 🔬 Methodology

### Rubric Design

DVJUSTICE's rubric mirrors how Chinese courts justify outcomes in domestic violence cases:

1. **Deductive Structure**: Judge states governing norm (major premise) → maps disputed facts (minor premise) → reaches reasoned conclusion
2. **Value-Laden Discretion**: Standards like "reasonableness," "fault," "danger," and "protectability" require culturally situated judgment
3. **Empathy Alignment**: Outputs must avoid victim-blaming, stigmatizing inferences, or secondary harm

Full rubric: [`static/evaluate/Scoring_Rubric_v1.0_English.md`](static/evaluate/Scoring_Rubric_v1.0_English.md)

### Inter-Annotator Agreement

Meta-evaluation (LLM-as-a-judge) shows ≥75% agreement with expert human annotations across five task settings, validating rubric operationalizability.

### Experimental Controls

- **Fixed Evaluation Subset**: 20-case slice (100 questions) for cross-model comparison
- **Standardized Prompts**: Identical system prompts and question formats across models
- **Temperature Setting**: 0.3 (optimized for legal analysis consistency)
- **Timeout & Retry**: 180s timeout, 2-round retry for truncation/rate-limit/empty outputs
- **Max Tokens**: Progressive doubling (initial → 8k → 16k) to accommodate long-form legal reasoning
- **Fresh Sessions**: No conversational carryover between questions

---

## 📁 Repository Structure

```
dvjustice/
├── README.md                       # This file
├── requirements.txt                # Python dependencies
├── config.py                       # Configuration (API endpoints, model settings)
├── process_cases.py                # Main evaluation pipeline
│
├── utils/                          # Core modules
│   ├── ai_api.py                   # Unified LLM API interface
│   ├── deepseek_api.py             # DeepSeek-specific wrapper
│   ├── evaluator.py                # Rubric-based evaluation engine
│   ├── data_masking.py             # De-identification & paraphrasing
│   ├── question_generator.py       # Structured question generation
│   └── ...
│
├── scripts/                        # Analysis & visualization
│   ├── generate_advanced_conference_charts.py  # Generate all charts (Pareto, CI, etc.)
│   ├── generate_results_report.py              # Generate detailed text reports
│   ├── test_single_case_all_models.sh          # Batch evaluation script
│   └── ...
│
├── static/evaluate/                # Rubric documentation
│   └── Scoring_Rubric_v1.0_English.md
│
├── data/                           # Benchmark data & results
│   ├── 108个案例_新标准评估_完整版_最终版.xlsx  # Full benchmark (on request)
│   └── results_20260112_unified_e8fd22b9/      # 20-case evaluation results
│       ├── 20个案例_统一评估结果_108cases.xlsx  # Multi-model scores & responses
│       ├── results_详细报告_20260112_172609.txt # Detailed report
│       ├── 图表使用指南_Chart_Guide.txt          # Chart usage guide
│       └── chart_*.png                          # 14 visualization charts
│
└── app.py                          # Optional web interface (Flask)
```

---

## 🎯 Use Cases

### Academic Research

- **Benchmark New Models**: Evaluate emerging LLMs on value-laden legal reasoning
- **Validate Legal AI Methods**: Test prompt engineering, retrieval augmentation, or fine-tuning strategies
- **Study Failure Modes**: Analyze abandoned-law citations, fact hallucinations, value misalignment
- **Cross-Domain Transfer**: Adapt DVJUSTICE's rubric to other high-stakes adjudication domains

### Industry Applications

- **Model Selection**: Compare commercial LLMs for legal advisory products
- **Quality Assurance**: Monitor AI-generated legal outputs for reliability signals
- **Risk Assessment**: Measure tail risk and worst-case performance before deployment

---

## 🛠️ Technical Stack

- **Python**: 3.11+
- **Data Processing**: pandas, openpyxl, numpy
- **Visualization**: matplotlib, seaborn (advanced charts: Pareto, Bootstrap CI, CVaR)
- **API Integration**: OpenAI, Anthropic, Google Gemini, Alibaba Qwen, DeepSeek
- **Concurrency**: concurrent.futures for parallel model evaluation
- **Optional Web UI**: Flask 3.0

---

## 🤝 Contributing

We welcome contributions! Please:
1. **Report Issues**: Use [GitHub Issues](https://github.com/chenqianwan/DV-JusticeBench/issues) for bugs or questions
2. **Submit Pull Requests**: For code improvements, additional models, or rubric extensions
3. **Share Results**: If you evaluate new models, consider contributing leaderboard entries

---

## 📄 License

This project is licensed under the **MIT License**. See [LICENSE](LICENSE) for details.

**Data Use Terms**:
- The benchmark dataset (108 cases) is derived from publicly available Chinese court decisions
- De-identified and paraphrased to protect privacy and prevent memorization
- Non-commercial, research-only use; redistribution requires permission

---

## 👥 Authors

<table>
<tr>
<td align="center" width="50%">
  <img src="static/20251229-160906.png" alt="Huang Yidan" width="100">
  <br>
  <strong>Huang Yidan</strong>
  <br>
  Law School Researcher
  <br>
  📧 <a href="mailto:huangyidan@hkgai.org">huangyidan@hkgai.org</a>
</td>
<td align="center" width="50%">
  <img src="static/Weixin Image_2025-12-29_161222_207.jpg" alt="Chen Long" width="100">
  <br>
  <strong>Chen Long</strong>
  <br>
  Computer Science Researcher
  <br>
  📧 <a href="mailto:chenlong@hkgai.org">chenlong@hkgai.org</a>
</td>
</tr>
</table>

---

## 📬 Contact

- **General Inquiries**: [GitHub Issues](https://github.com/chenqianwan/DV-JusticeBench/issues)
- **Data Access Requests**: huangyidan@hkgai.org, chenlong@hkgai.org
- **Collaboration Opportunities**: We welcome partnerships with legal AI researchers and practitioners
- **GitHub Repository**: [https://github.com/chenqianwan/DV-JusticeBench](https://github.com/chenqianwan/DV-JusticeBench)

---

## 🌟 Acknowledgments

### Data Sources

This benchmark is built upon real-world judicial decisions from:
- **China Judgments Online** (https://wenshu.court.gov.cn/)
- **Supreme People's Court 2025 Typical Anti-Domestic Violence Cases** (https://www.court.gov.cn/)

We thank the Chinese judiciary for making these decisions publicly available for research and education.

### Funding

This work is funded in part by:
- **HKUST Start-up Fund** (R9911)
- **Theme-based Research Scheme Grant** (T45-205/21-N)
- **InnoHK Initiative** of the Innovation and Technology Commission of the Hong Kong Special Administrative Region Government
- **Research Funding under HKUST-DXM AI for Finance Joint Laboratory** (DXM25EG01)

### Open Source

**🎉 This project is fully open source!**

- **Code**: All evaluation scripts, APIs, and web interface are available at [https://github.com/chenqianwan/DV-JusticeBench](https://github.com/chenqianwan/DV-JusticeBench)
- **Data**: The benchmark dataset (108 cases, 540 questions) and detailed results are available for academic research
- **License**: MIT License - free for non-commercial research use

We encourage the research community to use, extend, and build upon DVJUSTICE. Contributions are welcome!

---

## 📚 Related Resources

### Benchmark Papers
- **LegalBench**: [Guha et al., 2023] – Multi-task legal reasoning benchmark
- **LAiW**: [Dai et al., 2023] – Legal AI in the wild
- **LawBench**: [Fei et al., 2023] – Chinese legal understanding benchmark

### Legal AI Ethics
- **Legitimacy Frames**: How users assess automated legal authority (social science perspective)
- **Normative Uncertainty**: [Lacroix, 2024] – Fluency masking thin justification
- **AI Empathy**: [Haugeland] – "Computers don't give a damn" (structural worry in care-adjacent tasks)

### Value Alignment
- **Legal Value Alignment**: [Sun et al., 2025] – Jury-like tasks with bias detection
- **Self-Dialog Training**: [Pang et al., 2024] – Norm learning improvements
- **Case-Based Reasoning**: [Feng et al., 2024] – Precedent-anchored outputs

---

<div align="center">

### ⭐ If DVJUSTICE helps your research, please star this repository!

**Made for Legal AI Research**

[![Star on GitHub](https://img.shields.io/github/stars/chenqianwan/DV-JusticeBench?style=social)](https://github.com/chenqianwan/DV-JusticeBench)

</div>
