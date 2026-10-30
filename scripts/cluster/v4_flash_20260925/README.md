# DeepSeek V4 Flash 单节点服务

## 提交记录

| 项目 | 配置 / 状态 |
|---|---|
| 提交时间 | 2026-09-25 12:01:56 +08:00 |
| Slurm Job ID | `397374` |
| 作业名 | `chenlong_dvj_v4flash` |
| 申请资源 | 1 节点、8 GPU、64 CPU、512 GiB 内存；启动时校验 8 张 H800 80GB |
| 分区 / QoS / Account | `llm` / `llm` / `hansirui` |
| 优先级调整 | `nice=0`，普通队列，无优先级提升 |
| 运行上限 | 获得资源后 24 小时，不自动 requeue |
| 提交时状态 | `PENDING`，原因 `Priority`；尚未分配节点或完成启动验收 |
| 12:02 查询快照 | 仍在排队；调度器暂估 2026-09-26 11:46:34 开始，可能变化，不是保证 |
| 实验 | 未运行案件实验，只配置一条启动后的算术冒烟测试 |

## 模型与运行环境

- 权重：`/aifs4su/models/DeepSeek-V4-Flash`。
- 版本：DeepSeek-V4-Flash preview 原版权重，不是 V4-Flash-0731、V4.1 或其他用户的定制模型。
- 权重仓库 revision：`60d8d70770c6776ff598c94bb586a859a38244f1`。
- 46 个权重分片合计 159,617,149,040 字节；已检查可读性、大小和配置文件哈希，未重算全部张量文件的 SHA256。
- 复用只读环境：`/aifs4su/hansirui/vllm-dsv4-venv`，vLLM `0.20.0+cu129`，PyTorch `2.11.0+cu128`。
- TP=8，最大上下文 32768 token，最大并发序列 16，GPU memory utilization=0.90。
- 保留 checkpoint 原有 FP4/FP8 混合量化；不新增权重量化，不启用 MTP、DSpark 或 SM100 专用 MegaMoE。
- FP8 KV cache、block size=256、eager 模式。静态兼容性已检查，实际内核兼容性仍以启动和冒烟结果为准。

## 位置与接口

集群服务目录：`/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20260925`。

- 入口：本目录 `service.sbatch` → `launch_service.py`。
- 模型 API 名：`deepseek_v4_flash`。
- 分配节点后，内部 base URL：`http://<实际节点>:18044/v1`；排队时没有可用 endpoint。
- 访问密钥：集群服务目录下 `api_key`，权限 0600。不写入仓库、终端输出或启动命令参数。
- 提交回执：`submission.json`；预检与哈希：`preflight.json`。
- 实际状态与节点：启动后生成 `endpoint.json`。
- 日志：`logs/slurm-397374.out`、`logs/slurm-397374.err`、`runs/397374/server.log`。
- 冒烟结果：`runs/397374/smoke_response.json`。只有 `endpoint.json` 为 `ready` 才表示算术测试通过；`starting` 和 `needs_smoke_review` 不表示验收成功。
- 所有日志、缓存、临时文件均在个人目录；不修改共享模型和运行环境。

## 查询

在本项目根目录运行：

```bash
python3 scripts/cluster/v4_flash_20260925/deploy.py status
```

首次 `stage`、`submit` 已完成，不需重新执行。提交器使用锁和持久化回执防止重复提交；遇到不明提交结果必须核对原作业，不自动另开 8 卡作业。本地 `records/` 留存无密钥预检、回执和查询记录。

这里只提交推理服务，未修改案件流水线、评分标准或模型实验配置，也未启动周期性监控。
