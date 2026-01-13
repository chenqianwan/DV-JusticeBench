# 工作流程描述（论文用）

## 英文版本

We apply this evaluation framework to outputs from multiple model families, including GPT-based models (via OpenAI API), DeepSeek (including deepseek-chat and deepseek-reasoner with thinking mode), Qwen (Tongyi Qianwen), and our unified legal case evaluation workflow. The workflow consists of four sequential stages: (1) **Data Masking**: sensitive information in case texts and judicial decisions is anonymized using API-based masking to protect privacy while preserving legal semantics; (2) **Question Generation**: five legal controversy questions are automatically generated from each masked case, focusing on legal analysis and value judgment rather than factual queries; (3) **AI Answering**: each question is answered by the target model using task-adaptive temperature settings (0.3 for case analysis, 0.7 for question generation, 0.1 for text extraction) to balance accuracy and diversity; (4) **Answer Evaluation**: AI-generated answers are evaluated against judicial decisions using a five-dimensional rubric covering normative basis relevance, subsumption chain alignment, value judgment and empathy alignment, key facts and dispute coverage, and consistency of judgment conclusion and relief allocation. The entire process is executed in parallel using a thread pool executor with configurable concurrency (default: 50 workers) to efficiently process large-scale case datasets.

## 中文版本

我们将该评估框架应用于多个模型系列的输出，包括基于GPT的模型（通过OpenAI API）、DeepSeek（包括deepseek-chat和具有thinking模式的deepseek-reasoner）、Qwen（通义千问），以及我们的统一法律案例评估工作流程。该工作流程包含四个顺序阶段：（1）**数据脱敏**：使用基于API的脱敏方法对案例文本和司法判决中的敏感信息进行匿名化处理，在保护隐私的同时保留法律语义；（2）**问题生成**：从每个脱敏案例中自动生成五个法律争议问题，侧重于法律分析和价值判断而非事实查询；（3）**AI回答**：目标模型使用任务自适应温度设置（案例分析0.3、问题生成0.7、文本提取0.1）回答每个问题，以平衡准确性和多样性；（4）**答案评估**：使用五维评估标准对AI生成的答案进行评估，涵盖规范依据相关性、涵摄链条对齐度、价值衡量与同理心对齐度、关键事实与争点覆盖度，以及裁判结论与救济配置一致性。整个流程通过可配置并发度的线程池执行器（默认：50个工作线程）并行执行，以高效处理大规模案例数据集。

## 精简版本（适合论文摘要或方法部分）

**英文：**
We evaluate outputs from multiple model families (GPT-based, DeepSeek, Qwen) through a four-stage workflow: data masking, question generation, AI answering with task-adaptive temperature settings, and multi-dimensional evaluation against judicial decisions.

**中文：**
我们通过四阶段工作流程评估多个模型系列（基于GPT、DeepSeek、Qwen）的输出：数据脱敏、问题生成、使用任务自适应温度设置的AI回答，以及针对司法判决的多维评估。

