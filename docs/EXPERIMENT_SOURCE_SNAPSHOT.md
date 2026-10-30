# 本地实验源码归档（2026-10-30）

本次将此前仅存在于本地工作区的实验脚本、测试、提示词协议、抽样清单和历史实验说明纳入版本控制。它保存已有实现，不表示所有历史入口已经成为可直接运行的统一流水线，也不更新各实验的执行授权或预算。

## 内容与入口

| 范围 | 主要位置 |
|---|---|
| 108 案跨模型互评、历史 20 案补充与报告 | `scripts/run_cross_judging_full108_xhub.py`、`scripts/run_cross_judging_original20_rebuilt_xhub.py`、`scripts/report_cross_judge_rebuilt108_20260908.py` |
| 请求预算、费用记录与恢复保护 | `scripts/cross_judge_budget_guard.py`、`scripts/cross_judge_budget_amendment.py`、`tests/` |
| 三模型作答及各次恢复入口 | `scripts/local_primary_answers_20260929/` |
| 集群部署、作答、评分与审计 | `scripts/cluster/` 下按日期保存的目录 |
| 相关性分类、幻觉审计协议及实现 | `prompts/`、`scripts/classify_original108_xhub_20261002.py`、`scripts/classify_original108_xhub_auxfix_20261002.py`、`scripts/hallucination_audit_20261002/`、`scripts/hallucination_h1_h5_20261002/`、`scripts/hallucination_h1_h5_sentence_ids_20261003/` |
| 抽样清单 | `data/additional_20_cases_20260819.json`、`data/remaining_68_cases_20260821.json`、`data/cross_judge_expansion20_cases_20260822.json`、`data/cross_judge_expansion20_source_20260822.json` |

仓库根目录带日期的实验报告和 Prompt 核对文档是历史记录，反映各自撰写时的方案和状态。不得将其中的“当前”“已授权”或预算描述直接用于新的实验运行。原有 4,193 案交接文件也不是此后所有实验的统一状态页。

## 运行依赖与归档边界

- 本次未提交大规模原始输出、HTTP 回执、集群 `records/`、运行状态、密钥或临时文件。复现历史报告仍需对应的数据归档；抽样清单不能替代模型回答、评分和来源数据。
- 部分入口依赖冻结的数据、源码哈希或 Git 提交。保留原有源码，避免仅为整理路径而破坏历史校验。更改入口前应核对其依赖及校验约束。
- 部分集群脚本使用特定服务器、账户、模型和目录。`admin_ds_h1h5_retry3_20261006`、诊断脚本和北大法宝爬虫启动脚本中还存在本机绝对路径；这些属于历史环境配置，需要在迁移时适配。
- 三模型作答入口还引用相邻 `AI_Council` 项目的环境配置。该配置及其中凭据未纳入本次提交。
- Python 依赖应安装在实际使用的解释器中。本地 `.venv` 当前缺少 `requests`、`python-dotenv` 等依赖；仓库根目录 `requirements.txt` 并未覆盖所有新增的集群和绘图工具。

## 已完成的检查

- 新增 Python 文件通过语法解析，新增 JSON 文件通过格式解析。
- 对本次候选文本进行了常见硬编码凭据模式扫描，未发现匹配；该检查不等同于完整安全审计。
- `tests/` 下 94 项离线测试通过，执行时禁止测试进程建立网络连接。未运行付费实验或提交集群作业，也未对所有集群脚本完成端到端验证。
- 三个幻觉审计协议目录的离线测试分别通过 98、17、38 项，合计 153 项。三模型作答目录的补充测试在 60 秒限制内未完成，已终止测试进程；不将该目录记为测试通过。

绘图脚本的既有未提交修改、旧图片删除、论文附件和大体积实验结果均不属于本次源码归档。
