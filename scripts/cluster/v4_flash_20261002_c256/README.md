# DeepSeek V4 Flash：256 并发容量独立部署（2026-10-02）

按用户要求，从冻结的 `../v4_flash_20261002/` 派生本版本，将服务端 `max_num_seqs` 从 128 调整为 256。启动 argv、端点元数据和 CLI 断言一致；实际客户端并发仍由具体任务单独设置。

本版本未复制旧回执、密钥、缓存或实验数据；父部署作业为 407198，预检仅以 `parent_deployment_job` 记录来源，不将其误记为失败作业。

## 验证与撤回状态

- 本地 58 项离线测试全部通过：49 项继承的 supervisor 回归测试、9 项 c256 部署与启动保护测试；Python AST 和 Bash 语法检查通过。
- 新目录已完成远端 `stage`；2026-10-02 17:52:50 +08:00 实际安装 vLLM 的完整 `cli-check` 通过，解析容量为 256，未启动 GPU 推理或加载权重。
- 旧作业 407198 于 2026-10-02 17:52:06 +08:00 被精确撤回；操作后 `scontrol` 与 `sacct` 均确认 `CANCELLED`、Elapsed 0、无 AllocTRES。回执见 `records/cancel_407198_20261002T175206.json`。
- 原 DeepSeek 排队监测已暂停。新 c256 版本仅已准备和验证，**尚未提交替代作业，也未运行服务或实验**。

## 不变的资源与科学配置

权限交接：按用户随后明确授权，远端本部署目录及全部现有内容（24个文件/目录）已通过命名用户ACL给予`hansirui`读写权限，目录另含进入权限；包含目录内已有密钥，但未输出密钥内容。其他用户权限未放开，父目录、共享模型与共享环境未改。原ACL和逐项授权后核验保存在本地`records/acl_hansirui_*.json`。这仅解决文件访问，不改变脚本的UID/Slurm运行限制；管理员若换服务账号部署仍需适配。后续新增文件不承诺自动继承此次授权。

- 远端独立目录：`/aifs4su/hansirui_3rd/chenlong/dvjustice_v4flash_20261002_c256`。
- 同名 Slurm 作业 `chenlong_dvj_v4flash`，继续跨版本查重；新 comment 为 `chenlong-dvjustice-v4flash-20261002-c256`。
- `llm` 分区及 QoS，account `hansirui`，nice 0，单节点、8 GPU、64 CPU、512G 内存、`5-00:00:00`；启动时仍检查实际为 8 张 H800、每张至少 79,000 MiB。
- 模型 `/aifs4su/models/DeepSeek-V4-Flash`，revision `60d8d70770c6776ff598c94bb586a859a38244f1`；只读复用共享权重。
- 运行环境 `/aifs4su/hansirui/vllm-dsv4-venv`，vLLM `0.20.0+cu129`；不修改共享环境。
- TP 8，上下文 32768，批处理 token 上限 8192，GPU 内存利用率 0.90，FP8 KV cache，block size 256；其余模型参数、tokenizer、reasoning parser 和合成健康检查题均不变。
- 模型名 `deepseek_v4_flash`，端口 18044，鉴权及离线模式保持原样；不启动病例实验、分类或幻觉审计。

## 启动与重复提交保护

保留 staging 文件哈希、模型元数据与分片检查、运行环境来源校验、完整 CLI 预检、UID / 单节点 / 8 H800 检查、独占锁、同名活动作业查重、持久化提交意图与失败后禁止自动重试。

`allocation_supervisor.py` 除独立目录外与父版本完全一致：保留 `/proc` 不完整扫描保护、pidfd 身份绑定、清理不确定时禁止重复启动、服务退出后保留申请且仅接受显式重启请求。不会自动建立新的 Slurm 作业。

在项目根目录进行本地离线检查：

```sh
python3 -B -m unittest discover -s scripts/cluster/v4_flash_20261002_c256 -p 'test_*.py' -v
```

测试逐文件限定允许差异为独立目录、版本 comment、父作业来源字段及 128 → 256 的三处容量配置；并验证资源参数、Python / Bash 语法、启动前身份 / 硬件 / 文件完整性保护，以及完整继承的 supervisor 回归测试。不联网、不使用 GPU、不调用 Slurm。

远端准备命令：

```sh
python3 -B scripts/cluster/v4_flash_20261002_c256/deploy.py stage
python3 -B scripts/cluster/v4_flash_20261002_c256/deploy.py cli-check
```

`cli-check` 隐藏全部 GPU，只用实际安装的 vLLM 校验参数，不启动推理或加载权重。以上两个操作均不会提交作业。

`deploy.py` 保留显式 `submit` 与 `status` 模式，但本轮仅准备和验证新版本，不提交替代作业。若以后获得提交授权，需先确认旧同名作业已撤回；若提交结果不明，先核对远端 `submission.json` 与 Slurm，禁止盲目再次提交或删除回执。

所有后续操作的原始结果由部署入口写入本目录 `records/`。旧 `v4_flash_20261002` 部署、其记录及冻结的实验 / 调优配置均保持不变。
