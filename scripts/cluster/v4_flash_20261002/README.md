# DeepSeek V4 Flash 重新申请（2026-10-02）

> 2026-10-02 17:52 +08:00 更新：用户要求改256并发并撤回排队，作业407198已核验CANCELLED（未开始运行），旧自动监测已暂停。此目录保留为128容量历史版本，不再提交。管理员使用新目录`/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20261002_c256/service.sbatch`。下文为原提交历史。

用户授权重新申请服务。2026-10-02 最新用途已改为：对全部 primary + secondary 案件的各模型既有回答做 Layer 1 幻觉统计；不在本服务运行原始 108 案分类。此部署入口只启动模型服务及一道合成健康检查题，不会自动重跑历史作答、评分或分类。幻觉审计是独立任务，待外部法源核验链及冒烟验收就绪后运行。

## 提交回执

- 2026-10-02 10:29:33 +08:00 提交成功，Slurm 作业 **407198**。
- 首次核验：`PENDING`，原因 `Priority`，`AllocTRES=(null)`，尚未分配节点。
- 首次核验 StartTime / EndTime 均为 Unknown；不能承诺排到时间。
- 10:30:23 +08:00 再核验仍为 `PENDING / Priority`；调度器暂估 2026-10-03 15:53:39 开始、2026-10-08 15:53:39 结束。此为排队预测，会随队列变化，不是已分配资源。
- 新 supervisor SHA256：`b9d1119ee645c7cefbdad7b884f85338c930a7c0105fe014d3edd4c85fcf48fb`。
- `stage` 与实际 vLLM 完整参数 `cli-check` 均通过。CLI 检查隐藏全部 GPU，没有启动推理或加载权重。
- 已有有效提交回执；后续只查 `status`，不要重新建立申请。

## 配置与目录

- 远端：`/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20261002`。
- Slurm：`llm` 分区及 QoS，account `hansirui`，单节点、8 GPU、64 CPU、512G 内存、`5-00:00:00`，nice 0。
- 启动器核验实际硬件为 8 张 H800，每张至少 79,000 MiB；不使用其他人的服务或作业。
- 共享模型：`/aifs4su/models/DeepSeek-V4-Flash`，revision `60d8d70770c6776ff598c94bb586a859a38244f1`，只读复用，不复制或下载权重。
- 运行环境：`/aifs4su/hansirui/vllm-dsv4-venv`，vLLM `0.20.0+cu129`。
- 模型名 `deepseek_v4_flash`；端口 18044；服务容量 `max_num_seqs=128`，上下文 32768，与上批已验证服务一致。实际客户端并发由任务单独设置。
- 所有缓存、日志、密钥及回执在个人数据目录中；密钥不写入命令行或报告。

## 修复与验证

旧作业 402816 于 2026-10-02 08:55:20 +08:00 以 FAILED / 1:0 退出；`/proc` glob 枚举遇到进程消失，异常穿透 supervisor 并触发服务清理。不是正常五天到期，也没有据此认定被管理员回收。

新版本改为单层 `/proc` 枚举。单 PID 消失允许跳过；不完整扫描明确记录，不视作空树，不停止健康服务，也不允许重复启动。保留 Popen 句柄和已知进程身份；使用 pidfd 防止误向复用 PID 发信号。启动前检查 pidfd 能力。

本地 52 项测试通过（49 项 supervisor 测试加 3 项部署配置测试）。旧 `v4_flash_20260927` 文件与已完成实验结果不修改。共享模型元文件、46 个分片大小以及关键运行环境源码与旧 preflight 一致；未重新计算全部权重哈希。

## 唯一部署入口

在项目根目录运行：

```sh
python3 -B scripts/cluster/v4_flash_20261002/deploy.py stage
python3 -B scripts/cluster/v4_flash_20261002/deploy.py cli-check
python3 -B scripts/cluster/v4_flash_20261002/deploy.py submit
python3 -B scripts/cluster/v4_flash_20261002/deploy.py status
```

`submit` 有文件锁、持久化提交意图及同名活作业查重。若提交结果不明，先核对远端 `submission.json` 和 Slurm，不重复申请，不删除提交回执。服务就绪以当前 `endpoint.json` 和实际健康检查为准，不能仅凭 RUNNING 判断模型可用。

每步原始结果保存在本目录 `records/`。排到资源后自动启动服务；五天从实际开始运行计算，最终以 Slurm StartTime / EndTime 为准。不得主动释放当前申请或盲目启动第二个服务。

原始 108 案分类输入仅为之前的离线准备，保留不删除、不提交集群。新任务的标准及协议位于 `prompts/hallucination_layer1_20261002/`，输入审计目录为 `data/pkulaw_hallucination_audit_20261002/`。不修改已冻结的部署脚本、资源申请和历史实验结果。
