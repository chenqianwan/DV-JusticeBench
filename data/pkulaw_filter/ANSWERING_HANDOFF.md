# 作答阶段交接说明（Answering Stage Handoff）

> 本文档说明如何把 `masked_cases_fix4.jsonl` 和 `questions_fix4.jsonl` 送入三个作答模型（GPT-4o / Gemini 2.5 Flash / Qwen-Max），产出评分阶段可以直接使用的模型回答文件。
>
> **接手前置条件**：已收到本目录下 4 个文件（primary 与 secondary 各 2 个 jsonl），已配置 xhub 代理 API 密钥。

---

## 1. 数据文件

| 文件 | 行数 | 每行是 |
|------|------|--------|
| `prepare_output/masked_cases_fix4.jsonl` | 226 | 一个 primary 案（脱敏后 3 字段 + person_map） |
| `prepare_output/questions_fix4.jsonl` | 226 | 一个 primary 案的 5 道题 |
| `prepare_output_secondary/masked_cases_fix4.jsonl` | 2022 | 一个 secondary 案 |
| `prepare_output_secondary/questions_fix4.jsonl` | 2022 | 一个 secondary 案的 5 道题 |

**两个 jsonl 通过 `case_id` 关联**。总规模：2,248 案 × 5 题 = **11,240 题**。

### 每行字段

`masked_cases_fix4.jsonl`：
```json
{
  "case_id": "pkulaw_fbmcli_c_531360792",
  "title_masked": "某女1与某男1离婚纠纷一审民事判决书",
  "case_text_masked": "……脱敏后的案情原文……",
  "judge_decision_masked": "……脱敏后的法官裁判文书（评分才用，作答不给）……",
  "person_map": {"修某": "某女1", "夏某1": "某男1", "夏某2": "某女2"},
  "source_case_text_hash": "sha256..."  // 审计追溯用
}
```

`questions_fix4.jsonl`：
```json
{
  "case_id": "pkulaw_fbmcli_c_531360792",
  "questions": [
    {"question_id": "pkulaw_fbmcli_c_531360792_q1", "question": "在离婚纠纷中……"},
    ...共 5 条
  ]
}
```

---

## 2. 作答任务

对每个 `(case_id, question_id)` 组合，向 **三个作答模型分别调用一次**，得到该模型对该题的回答。

**总请求数**：2,248 案 × 5 题 × 3 模型 = **33,720 次调用**。

三个作答模型：
| 模型 | xhub model ID | 备注 |
|------|--------------|------|
| GPT-4o | `gpt-4o` | |
| Gemini 2.5 Flash | `gemini-2.5-flash` | |
| Qwen-Max | `qwen-max` | |

**API 端点**：xhub 代理 `https://api3.xhub.chat/v1/chat/completions`（OpenAI 兼容格式，三个模型共用一个 `OPENAI_API_KEY` xhub key）。

---

## 3. Prompt 模板（**必须严格使用，与 108 案基线保持一致**）

### System

```
你是一位专业的法律专家，擅长分析法律案例并提供专业的法律意见。
```

### User

```
请作为法律专家分析以下案例，并回答相关问题。

案例内容：
{case_text_masked}

问题：{question}

请提供详细的法律分析，包括：
1. 案件事实梳理
2. 法律适用分析
3. 判决建议
4. 法律依据

请用中文回答。
```

**变量说明**：
- `{case_text_masked}` = 该案 `masked_cases_fix4.jsonl` 里的 `case_text_masked`
- `{question}` = 该题 `questions_fix4.jsonl` 里的 `question`
- **不要**把 `judge_decision_masked` 传给作答模型（那是评分阶段的参考答案）
- **不要**把 `title_masked` 拼进去（原实验也没用）

---

## 4. 请求参数

| 参数 | 值 | 说明 |
|------|-----|------|
| `temperature` | `0.3` | 法律分析要求稳定输出 |
| `max_tokens` | `3000`（初始）→ 8000（截断时补救）→ 16000（仍截断时终极）| 长文本回答渐进式加大 |
| `timeout` | `180s` | HTTP 层 |
| 重试 | 最多 2 轮补救（针对空回答/截断/rate limit）+ 每轮 3 次网络重试 |

**Claude 模型特殊处理**（本次不用 Claude，但如需扩展）：把 system 消息提取到顶层 `system` 字段，`messages` 里只保留 user/assistant。

**GPT-5 特别注意**：原 108 案实验里 GPT-5 有 65% 空回答率，本次不作答，若有人想加入需另行处理空回答统计。

---

## 5. 期望输出格式（供后续 DS 评分直接消费）

**建议格式**：`answers_<model>_<split>.jsonl`，每行一条：

```json
{
  "case_id": "pkulaw_fbmcli_c_531360792",
  "question_id": "pkulaw_fbmcli_c_531360792_q1",
  "model": "gpt-4o",
  "answer": "……模型的完整回答文本……",
  "prompt_hash": "sha256(system+user)",
  "case_text_hash": "从 masked_cases_fix4.jsonl 里的 source_case_text_hash 抄过来",
  "usage": {"prompt_tokens": 1234, "completion_tokens": 567, "total_tokens": 1801},
  "finish_reason": "stop" | "length" | "content_filter",
  "attempts": 1,
  "elapsed_s": 12.3,
  "status": "ok" | "empty" | "truncated" | "error"
}
```

**必须保留的字段**：`case_id`、`question_id`、`model`、`answer`、`status`、`finish_reason`、`usage`、`elapsed_s`、`attempts`。

**status 语义**：
- `ok`：正常完整回答
- `empty`：API 返回空 content（如 GPT-5 常见）
- `truncated`：`finish_reason=length` 且已经用完 3 轮 max_tokens 补救
- `error`：3 次重试后仍失败

**失败案单独存 `answers_<model>_<split>.failures.jsonl`**，不删除，供后续审计。

---

## 6. 并发与预算

**建议并发**：
- xhub 各模型 RPM 上限见 `config.py`（GPT/Gemini/Qwen 都是 5000 RPM / 50 RPS 默认）
- 起步 20 concurrent workers 就够（避免打爆 xhub）

**预算粗估**（可与 xhub 账单实际数据校准）：
- 每次调用平均 ~2000 输入 + ~2000 输出 tokens
- 33,720 次调用总 tokens ≈ 1.35 亿
- 按 GPT-4o 中文长文本单价 ~$5/1M input + $15/1M output → 单模型 ~$200
- **三模型合计约 $500–800 USD**（Gemini 和 Qwen 通常更便宜，此估算保守）

---

## 7. 断点续跑要求

- 输出 jsonl 用 append，程序崩溃或中断后可读取现有 `(case_id, question_id, model)` 集合跳过已完成的
- 每 50 条 flush 一次，防止大量内存丢失
- 失败案单独台账，可后续针对性重跑

---

## 8. 交回给评分方的内容

**主要交付**：
- `answers_gpt-4o_primary.jsonl`（1,130 行）
- `answers_gpt-4o_secondary.jsonl`（10,110 行）
- `answers_gemini-2.5-flash_*.jsonl`（同上×2）
- `answers_qwen-max_*.jsonl`（同上×2）
- 三个模型 × 两个 split = **6 个 jsonl 文件，理论共 33,720 行**

**审计交付**：
- 各模型的 `*.failures.jsonl`
- 总费用台账 / xhub 账单截图

---

## 9. 已知注意事项

1. **不要修改 case_text_masked 或 question 的字符**：任何改动会破坏 hash 追溯，评分方无法对齐
2. **不要合并跨题上下文**：每个 (case_id, question_id) 单独一次会话，模型不看前面的回答
3. **不要用 stream 模式**：断线难恢复，直接用一次性 response
4. **Rate limit 触发时**：xhub 会返回 429，请按 `Retry-After` 头部等待，不要重复打

---

## 10. 快速验证脚本（示意，非交付要求）

```python
# 伪代码
import json, requests
KEY = os.environ["OPENAI_API_KEY"]  # xhub key
URL = "https://api3.xhub.chat/v1/chat/completions"

cases = {json.loads(l)["case_id"]: json.loads(l)
         for l in open("prepare_output/masked_cases_fix4.jsonl")}
questions = [json.loads(l) for l in open("prepare_output/questions_fix4.jsonl")]

for qd in questions[:1]:  # 冒烟 1 案
    case = cases[qd["case_id"]]
    for q in qd["questions"]:
        resp = requests.post(URL,
            headers={"Authorization": f"Bearer {KEY}"},
            json={
                "model": "gpt-4o",
                "messages": [
                    {"role": "system", "content": "你是一位专业的法律专家，擅长分析法律案例并提供专业的法律意见。"},
                    {"role": "user", "content": f"请作为法律专家分析以下案例，并回答相关问题。\n\n案例内容：\n{case['case_text_masked']}\n\n问题：{q['question']}\n\n请提供详细的法律分析，包括：\n1. 案件事实梳理\n2. 法律适用分析\n3. 判决建议\n4. 法律依据\n\n请用中文回答。"},
                ],
                "temperature": 0.3,
                "max_tokens": 3000,
            }, timeout=180)
        print(resp.json()["choices"][0]["message"]["content"][:200])
```

---

## 11. 联系人

- 数据准备方（prepare 阶段）：本仓库 PR 作者
- 评分阶段（后续）：由数据准备方用本地 DGX 的 `deepseek_v4_flash` 承担

---

*最后更新: 2026-09-16*
