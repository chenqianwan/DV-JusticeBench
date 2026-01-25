# Temperature 和 Top-k 参数设计说明

## 设计原则

本系统采用**任务自适应温度策略**（Task-Adaptive Temperature Strategy），根据不同任务对准确性和多样性的需求，动态调整temperature参数。Top-k参数使用API默认值，保持模型原生采样行为。

## 具体参数配置

### 1. 案例分析任务（`analyze_case`）
- **Temperature: 0.3**
- **设计理由**：
  - 法律案例分析需要高度的准确性和一致性
  - 低temperature（0.3）确保模型倾向于选择概率最高的token，减少随机性
  - 保证相同案例输入产生稳定、可靠的分析结果
  - 避免因随机性导致的法律推理错误

### 2. 问题生成任务（`generate_questions`）
- **Temperature: 0.7**
- **设计理由**：
  - 问题生成需要多样性和创造性
  - 中等temperature（0.7）允许模型探索更多可能的表达方式
  - 确保生成的问题涵盖不同角度和争议焦点
  - 避免生成过于相似或重复的问题

### 3. 文本提取任务（`_extract_answer_from_judgment`）
- **Temperature: 0.1**
- **设计理由**：
  - 文本提取要求极高的保真度，必须完全复制原文
  - 极低temperature（0.1）确保模型严格遵循原文，最小化改写风险
  - 代码注释明确说明："降低temperature确保更准确"
  - 配合后置验证机制（检查提取文本是否在原文中），双重保障文本准确性

### 4. 对比分析任务（`compare_decisions`）
- **Temperature: 0.5**
- **设计理由**：
  - 对比分析需要在准确性和多样性之间平衡
  - 中等偏低temperature（0.5）既保证分析逻辑的准确性，又允许适度的表达多样性
  - 确保对比分析既全面又准确

## Top-k 参数

- **设置方式**：未显式设置，使用API默认值
- **设计理由**：
  - 保持模型原生采样行为，避免过度干预
  - DeepSeek API的默认top-k值已针对模型特性优化
  - 简化参数调优，减少超参数搜索空间

## 理论依据

1. **Temperature与熵的关系**：Temperature控制输出分布的熵，低temperature降低熵，使分布更集中，输出更确定；高temperature增加熵，使分布更均匀，输出更多样。

2. **任务特性匹配**：
   - 确定性任务（分析、提取）→ 低temperature → 高准确性
   - 创造性任务（生成）→ 高temperature → 高多样性
   - 平衡性任务（对比）→ 中temperature → 平衡两者

3. **法律领域特殊性**：法律文本对准确性要求极高，错误可能导致严重后果，因此关键任务（分析、提取）采用低temperature以确保可靠性。

## 精简版本

**英文版本：**
```
We employ a task-adaptive temperature strategy to balance accuracy and diversity across different tasks. For case analysis, we set temperature=0.3 to ensure deterministic and reliable legal reasoning. For question generation, temperature=0.7 promotes diversity in question formulation. For text extraction, temperature=0.1 minimizes paraphrasing and ensures high fidelity to source text. For comparative analysis, temperature=0.5 balances accuracy and expressiveness. Top-k sampling uses API defaults to preserve native model behavior.
```

**中文版本：**
```
采用任务自适应温度策略，根据任务特性动态调整temperature参数。案例分析使用temperature=0.3以确保确定性和可靠性；问题生成使用temperature=0.7以增强多样性；文本提取使用temperature=0.1以最小化改写并保证高保真度；对比分析使用temperature=0.5以平衡准确性和表达多样性。Top-k采样使用API默认值以保持模型原生行为。
```

