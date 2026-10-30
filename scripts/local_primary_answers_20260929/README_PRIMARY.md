# Primary 三模型作答批次

仅 primary 215 案 × 5 题 × 3 模型，共 3,225 份回答。复用已完成的 15 份冒烟回答及原始回执，新增网络队列 3,210 份。secondary、脱敏、出题、评分均不在本入口内。

沿用原 `run.py` 的请求、提示词、参数和重试实现：`gpt-4o`、`gemini-2.5-flash`、`qwen-max`；temperature 0.3，初始 3,000 tokens，长度截断时仅升至 6,000 一次。不添加 thinking 参数。每模型 8 并发，总计最多 24；单请求 300 秒。

原冒烟目录保持不变。新目录为 `data/pkulaw_primary_three_answers_full_20260929`；新授权在 `authorization.json` 中，不能通过修改旧 manifest 的 `full_run_authorized=false` 放行。

## 执行

```bash
python3 -B scripts/local_primary_answers_20260929/run_primary_batch.py prepare --market <新核验的 market JSON>
python3 -B scripts/local_primary_answers_20260929/run_primary_batch.py launch
python3 -B scripts/local_primary_answers_20260929/run_primary_batch.py status
# 仅在旧客户端确定退出、退出回执存在、无未知请求后：
python3 -B scripts/local_primary_answers_20260929/run_primary_batch.py resume
```

`prepare` 纯本地；`launch` 和 `resume` 启动独立后台进程，退出对话不会结束队列。后台进程、启动 intent、PID/开始时间、输出日志、退出回执都在 `sessions/`。独占文件锁防止重复启动；无退出回执、仍存活进程、未知请求或已发请求无终态时不自动恢复。必须先核对原请求，不能盲目重发。成功结果永不重跑或覆盖。

本批保守工程预算保护为 **$40（包含已付冒烟 $0.128728）**，不是声称用户明确指定了 $40。新预算账本独立；旧账本、HTTP 消费记录仅作为审计副本保存，不再次累计。`accounted` 含安全裕量和保守预留，不等于平台实际账单；真实费用仍需 XHub 明细对账。

输入、原 helper、新 runner、预算保护代码、价格快照、预算和参数均哈希冻结。更改这些内容会阻止启动/恢复；不要修改已启动批次的冻结代码。原始回包保留，包括 XHub 返回的具体模型版本。本批只作答，不启动集群作业。

## 离线测试

```bash
python3 -B -m unittest discover -s scripts/local_primary_answers_20260929 -p test_primary_batch.py -v
```

测试不调用付费 API、不启动后台队列、不连接集群。
