# Primary 三模型仅回答冒烟

本入口仅执行 **1 案 × 5 题 × 3 模型 = 15 条逻辑回答**，不评分，不启动全量。全量 215 案 / 3,225 条清单只为后续核对准备，无执行授权。原 18 个脏案永久排除。

模型 ID：`gpt-4o`、`gemini-2.5-flash`、`qwen-max`。输入直接复用已冻结 DS `task_mapping.jsonl` 中的 `answer_messages`，并与历史 108 案 `analysis_messages` 源码及 commit 逐字核验。`temperature=0.3`、初始 3,000 token、仅一次长度重试到 6,000 token，不传 `thinking`，不添加裁判理由、标题或额外提示词。

## 入口

从工程根目录运行：

```sh
python3 -B scripts/local_primary_answers_20260929/run.py prepare
python3 -B scripts/local_primary_answers_20260929/run.py market
python3 -B scripts/local_primary_answers_20260929/run.py estimate --market data/pkulaw_primary_three_answers_20260929/market_<时间>.json
python3 -B scripts/local_primary_answers_20260929/run.py run --market data/pkulaw_primary_three_answers_20260929/market_<时间>.json --budget-usd <本次明确预算> --workers 5 --timeout 180
python3 -B scripts/local_primary_answers_20260929/run.py status
python3 -B scripts/local_primary_answers_20260929/run.py resume --market data/pkulaw_primary_three_answers_20260929/market_<同一时间>.json --budget-usd <同一预算> --workers 5 --timeout 180
```

`prepare`、`estimate`、`status` 无网络。`market` 仅只读 GET 当前 XHub 目录、供应商价格/额度单位、余额；运行时沿用 AI_Council 的 `.env` 读取方式，密钥不打印、不复制。`run`、`resume` 才可能发生付费请求。预算必填，不沿用其他实验额度，不自动扩充；价格快照须与端点一致、24 小时内、三个原模型 ID 均可用，单位价格须可由保存的供应商 metadata 复核。缓存折扣不计，按供应商已公开的最高分组倍率保守预留，实际账单另行对账。

## 持久化和异常

- 每次物理请求由既有 `cross_judge_budget_guard.GuardedTransport` 在发送前预留，写入 `budget_ledger.json`、`http_receipts/`，含 payload、响应、usage、估计费用及未知费用上界。并发、重试均计入同一预算，预付费用设为 0。
- 每题目录另有不可覆盖的完整输入、候选意图、完整 raw body、响应 SHA、终态。guard 阻止发送时，候选意图对应 `not_sent=true` 回执，不能当成已经调用。
- 超时、传输不确定性及 HTTP 408/504 记为 `needs_reconcile`，停止继续调度，绝不自动重发。已在途请求等待回执落盘。
- 有限接口重试和错误/空回答轮次沿用历史上限。明确拒绝、内容过滤不反复重试；6,000 token 后仍截断就保留截断。
- 成功终态及失败终态均不可覆盖。恢复要求独占文件锁、同一冻结输入/脚本/价格/预算/参数，以及上一次进程退出回执。缺退出回执、无回执意图或已有调用但缺终态，必须先离线核对；不伪造回执、不删除记录绕过保护。
- 接口的 `response_model` 原值保留；与请求 ID 不同时单独标识，供冒烟复核，不擅自换模型。

## 离线测试

```sh
python3 -B -m unittest discover -s scripts/local_primary_answers_20260929 -p 'test_*.py' -v
```

测试覆盖成功不重跑、仅一次长度升级、拒绝不重试、有限接口重试、超时不重发、缺终态不重发、预算阻止不发送、完整响应保存、敏感 header 排除，以及逐物理请求累计费用。

异常重试的技术细节只保留在审计资料；论文主结果无需展开这些实现细节。
