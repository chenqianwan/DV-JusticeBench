# V4 Flash 五天服务申请（2026-09-27 修订）

## 2026-09-28：正式回答队列（最新范围）

- 用户已确认使用128并发开始作答，并明确将18案裁判理由泄漏记录永久排除为脏数据；仅从本次有效实验集合排除，原始数据和审计证据不删除。
- 有效范围：primary 215案、secondary 2,004案，共2,219案、11,095题。原10题回答只有在逐项核验prompt、问题、模型和参数一致后才复用，网络待跑11,085题。
- 四档短输出压测已完成，16/32/64/128并发均无失败和抢占；128档为925.0输出token/s。该值来自256-token性能测试，不代表正式长回答或评分的吞吐。
- 正式作答仍使用原prompt、非思考、temperature=0.3、3000输出token上限，长度截断时仅升至6000一次。不得把短压测响应作为正式答案。
- 正式队列数据目录：`data/pkulaw_v4_answer_batch128_20260928/`；其输入、复用清单、排除清单和运行回执与旧冻结数据隔离。当前操作是回答，不同时启动全量评分。
- 服务仍是job `402816`、`dgx-040`、8张H800，最大序列128；五天截止时间保持不变。正式启动与实时进度以新目录的提交回执和`run_state.json`为准，不能仅凭本段文档判断已运行。

## 2026-09-27 晚：并发压测更新

用户已授权执行案件实验及并发压测。下文的“仅部署服务”和并发16为初始部署记录，不是最新执行范围。

- 已完成并保存 primary、secondary 各1案、共10题的回答冒烟；未评分。
- 全量范围已确认是 primary 217案、secondary 2,020案，共11,185题，后续由DS对DS评分。
- 压测单独使用真实原prompt、非思考模式、256输出token上限；这些响应不是正式实验答案。
- 压测服务容量由16调整为128，客户端依次测试16、32、64，有增益且稳定再测试128。仅调整`max_num_seqs`；TP8、模型权重、eager、FP8 KV、上下文等保持不变。
- 同一job `402816` 内仅重启模型，原五天截止时间 `2026-10-02 21:58:29 +08:00` 不变。原服务日志和配置均保留。
- 本地原始 `launch_service.py` 保留初始版本；调优脚本 `tune_benchmark_service.py` 生成经严格差分验证的128版本并同步远端。远端变更档案在 `changes/benchmark128_20260927/`。
- 压测操作：`dispatch_concurrency_benchmark.py stage`，以及 `start --concurrency 16/32/64/128`、`status`、完成后 `fetch`。各轮有独立提交回执，不允许重复启动或覆盖结果。
- 当前全量输入另有18案潜在裁判内容泄漏警告，原文未改；压测避开这些案件，正式实验前须处理，详见全量目录的 `judgment_leakage_audit.json`。

用户授权：单节点 8 张 H800，申请最长 5 天，不因普通服务启动失败轻易释放资源。仅部署服务，不运行案件实验。

## 本次提交

| 项目 | 回执 |
|---|---|
| Job ID | `402816` |
| 提交时间 | 2026-09-27 18:53:59 +08:00 |
| 调度确认时限 | `5-00:00:00` |
| 提交时状态 | `PENDING`，`QOSMaxJobsPerUserLimit`；同一集群账号的并发作业已达上限，尚未占卡 |
| 完整 CLI 校验 | 在 `Group3_dgx-127` 用实际 vLLM 解析通过；隐藏所有 CUDA 设备，未启动引擎或加载权重 |
| 本地测试 | 15 项 allocation supervisor 测试通过 |

最初一次提交连接中断后，已先核对远端无回执且无同名排队作业，才重新提交。最终只生成以上一份有效作业。没有取消或修改账号内的其他作业。

## 配置

| 项目 | 设置 |
|---|---|
| Slurm 资源 | `llm` 分区、`llm` QoS、`hansirui` account；1 节点 / 8 GPU / 64 CPU / 512 GiB |
| 时限 | `5-00:00:00`，从实际分配资源起计算；排队时间不计入 |
| 优先级 | `nice=0`，无优先级提升 |
| 作业名 | `chenlong_dvj_v4flash`；新提交前检查同名作业，防重复占卡 |
| 模型 | 原版 DeepSeek-V4-Flash preview，revision `60d8d70770c6776ff598c94bb586a859a38244f1`；不是 V4.1 或 0731 |
| 模型路径 | `/aifs4su/models/DeepSeek-V4-Flash`，共享权重只读 |
| 环境 | `/aifs4su/hansirui/vllm-dsv4-venv`，只读复用 |
| 推理参数 | TP=8、上下文 32768、并发序列 16；其余配置沿用上次服务 |
| 接口 | 分配节点后为 `http://<节点>:18044/v1`，模型名 `deepseek_v4_flash`；必须认证 |
| 旧作业 | `397374`，2026-09-25 启动后 81 秒因 CLI 参数错误退出；原文件与日志保留 |

本次修复：`--disable-log-requests` 改为当前 vLLM 支持的 `--no-enable-log-requests`。提交前必须用实际安装环境对**完整 argv** 解析并校验；不是只检查 Python/Bash 语法，也不是用 `--help` 代替解析。

## 服务失败不等于释放 allocation

`service.sbatch` 启动 `allocation_supervisor.py`，再启动 `launch_service.py`。

- 服务正常运行时保持服务，直到 5 天到期或收到终止信号。
- 服务异常退出时记录 `waiting_for_repair`，保留 allocation 等待修复；不反复自动启动同一个错误命令。
- 收到 Slurm TERM/INT、管理员回收、节点故障或时限到期时正常服从调度；不保证节点永不释放，也不绕过时限。
- Slurm 显示 `RUNNING` 只说明 allocation 存活；服务是否可用必须另看 `endpoint.json` 的 `ready` 及冒烟结果。
- 保留 allocation 期间即使服务失败也仍占用 8 张 GPU；这是本次明确授权的行为。

集群根目录：`/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20260927`。

关键文件：`submission.json`（提交回执）、`preflight.json`（资源与哈希）、`cli_validation.json`（完整命令验证）、`allocation_status.json`（资源保留与修复状态）、`endpoint.json`（服务状态）、`api_key`（0600，禁止上传/输出）。每次服务尝试使用独立的 `runs/<job_id>/attempt-001/` 等目录。

本地查询：

```bash
python3 scripts/cluster/v4_flash_20260927/deploy.py status
```

已提交后不要重新执行 `sbatch`。先确认现有 allocation，再进行同一节点内的修复。启动后的验证仅有一条算术冒烟，不调用案件流水线、不更改实验模型配置。

## 同一 allocation 内重启

先检查 `allocation_status.json` 是否为 `waiting_for_repair`，修正并验证配置、同步对应文件哈希后，再在集群服务根目录提交 `restart_request.json`：

```json
{"job_id":"实际作业号","request_id":"fix-001"}
```

`request_id` 每次必须唯一。只接受当前作业号；仍有服务进程或清理残余时拒绝重启。请求及决定保存在 `restart_requests/`，不删除历史尝试，不自动重新申请另一份 8 卡资源。该控制文件只负责重启服务，不延长 Slurm 的 5 天上限。
