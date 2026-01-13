# API提供商切换说明

## 概述

系统现在支持三种API提供商：
- **DeepSeek**（默认）
- **ChatGPT**（GPT-4o，对应ChatGPT 5.2）
- **Qwen**（通义千问）

可以通过配置轻松切换API提供商，无需修改代码。

## 配置方法

### 方法1：环境变量（推荐）

在 `.env` 文件中添加以下配置：

```bash
# 选择API提供商：'deepseek'、'chatgpt' 或 'qwen'
API_PROVIDER=qwen

# DeepSeek API配置（如果使用DeepSeek）
DEEPSEEK_API_KEY=your_deepseek_api_key

# OpenAI/ChatGPT API配置（如果使用ChatGPT）
OPENAI_API_KEY=your_openai_api_key

# Qwen（通义千问）API配置（如果使用Qwen）
QWEN_API_KEY=your_qwen_api_key
QWEN_API_URL=https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
```

### 方法2：直接修改 config.py

在 `config.py` 文件中修改：

```python
# API提供商选择（'deepseek'、'chatgpt' 或 'qwen'）
API_PROVIDER = 'qwen'  # 改为 'qwen' 使用Qwen
```

## 支持的模型

### DeepSeek
- 默认模型：`deepseek-chat`
- API端点：`https://api.deepseek.com/v1/chat/completions`

### ChatGPT
- 默认模型：`gpt-4o`（ChatGPT 5.2对应GPT-4o）
- API端点：`https://api.openai.com/v1/chat/completions`
- 如需使用其他模型（如 `gpt-4-turbo`），可在 `utils/chatgpt_api.py` 中修改 `self.model` 参数

### Qwen（通义千问）
- 默认模型：`qwen-turbo`
- API端点：`https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions`
- 支持模型：`qwen-turbo`、`qwen-plus`、`qwen-max`等
- 如需使用其他模型，可在 `utils/qwen_api.py` 中修改 `self.model` 参数

## 速率限制配置

### DeepSeek
```bash
DEEPSEEK_MAX_RPM=3000  # 每分钟最大请求数
DEEPSEEK_MAX_RPS=50    # 每秒最大请求数
```

### ChatGPT
```bash
OPENAI_MAX_RPM=5000  # 每分钟最大请求数
OPENAI_MAX_RPS=50    # 每秒最大请求数
```

### Qwen
```bash
QWEN_MAX_RPM=3000  # 每分钟最大请求数
QWEN_MAX_RPS=50    # 每秒最大请求数
```

## 代码使用示例

### 使用统一API接口（推荐）

```python
from utils.ai_api import ai_api

# 自动根据配置选择API提供商
result = ai_api.analyze_case(case_text, question)
questions = ai_api.generate_questions(case_text, num_questions=5)
comparison = ai_api.compare_decisions(ai_decision, judge_decision)
```

### 直接使用特定API

```python
# 使用DeepSeek
from utils.deepseek_api import DeepSeekAPI
api = DeepSeekAPI()
result = api.analyze_case(case_text)

# 使用ChatGPT
from utils.chatgpt_api import ChatGPTAPI
api = ChatGPTAPI()
result = api.analyze_case(case_text)
```

### 动态切换提供商

```python
from utils.ai_api import UnifiedAIAPI

# 使用DeepSeek
deepseek_api = UnifiedAIAPI(provider='deepseek')
result1 = deepseek_api.analyze_case(case_text)

# 使用ChatGPT
chatgpt_api = UnifiedAIAPI(provider='chatgpt')
result2 = chatgpt_api.analyze_case(case_text)

# 使用Qwen
qwen_api = UnifiedAIAPI(provider='qwen')
result3 = qwen_api.analyze_case(case_text)
```

## 功能对比

两种API提供商都支持以下功能：
- ✅ 案例分析（`analyze_case`）
- ✅ 问题生成（`generate_questions`）
- ✅ 判决对比（`compare_decisions`）
- ✅ 数据脱敏（通过 `DataMaskerAPI`）
- ✅ 并发处理
- ✅ 速率限制控制
- ✅ 错误重试机制

## 注意事项

1. **API密钥配置**：确保在使用前正确配置对应API提供商的密钥
2. **成本差异**：ChatGPT和DeepSeek的定价不同，请根据实际需求选择
3. **速率限制**：不同API提供商的速率限制不同，请根据实际情况调整配置
4. **模型版本**：ChatGPT默认使用 `gpt-4o`，如需使用其他版本请修改 `utils/chatgpt_api.py`

## 切换步骤

1. 在 `.env` 文件中设置 `API_PROVIDER=qwen`（或 `chatgpt`、`deepseek`）
2. 配置对应的API密钥：
   - DeepSeek: `DEEPSEEK_API_KEY`
   - ChatGPT: `OPENAI_API_KEY`
   - Qwen: `QWEN_API_KEY`
3. 重启应用或重新加载配置
4. 系统会自动使用新的API提供商

## 验证切换

运行以下命令验证当前使用的API提供商：

```python
from utils.ai_api import ai_api
print(f"当前使用的API提供商: {ai_api.get_provider_name()}")
```

