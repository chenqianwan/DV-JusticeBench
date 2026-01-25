# 项目清理计划 - 会议提交版本

**目标**: 创建一个纯净、可复现的工程版本，供会议组委会审查  
**分支**: `cleanup`  
**日期**: 2026-01-23

---

## 📋 清理原则

1. **保留**: 核心代码、最终结果数据、复现脚本、论文文件
2. **删除**: AI痕迹、旧数据版本、临时脚本、开发文档、调试文件
3. **清理**: 代码注释中的AI提示、临时变量名、调试信息

---

## 🗂️ 清理清单

### 一、删除旧数据版本 ✅

#### 1.1 data/ 目录下的旧结果文件夹
- [ ] `data/results_20260111_151734/` - 旧版本结果（保留最新版本）
- [ ] `data/results_20260112_105927/` - 中间版本
- [ ] `data/results_详细报告_*.txt` - 旧版详细报告（根目录下）

#### 1.2 旧的评估Excel文件
- [ ] `data/[标注102个]108个案例_新标准评估_完整版_20260107_150532.xlsx`
- [ ] `data/5个案例_新标准评估_20260105_123124.xlsx`
- [ ] `data/5个案例_新标准评估_20260108_111136.xlsx`
- [ ] `data/CLAUDE_1个案例评估_20260111_142120.xlsx`
- [ ] `data/DEEPSEEK_5个案例评估_20260110_083956.xlsx`
- [ ] `data/DEEPSEEK_5个案例评估_20260110_183602.xlsx`
- [ ] `data/GEMINI_5个案例评估_20260109_234613.xlsx`
- [ ] `data/GEMINI_5个案例评估_20260110_181717.xlsx`
- [ ] `data/GPT-o3_5个案例评估_提取自20个案例.xlsx`
- [ ] `data/GPT4O_5个案例评估_20260109_232015.xlsx`
- [ ] `data/GPT4O_5个案例评估_20260110_084652.xlsx`
- [ ] `data/GPT4O_5个案例评估_20260110_164932.xlsx`
- [ ] `data/所有模型5个案例评估结果_20260110_181657/` - 旧图表文件夹

#### 1.3 旧的图表文件夹
- [ ] `data/Case_Evaluation_Charts_English_20260107_144901/`
- [ ] `data/案例评估图表_20260107_142949/`
- [ ] `data/案例评估图表_优化版_20260107_143610/`
- [ ] `data/案例评估图表_带数据标识_20260107_143333/`
- [ ] `data/模型对比图表_20260110_084906/`

#### 1.4 保留的最终数据 ✅
- ✅ `data/results_20260112_unified_e8fd22b9/` - **保留**（最终结果）
- ✅ `data/cases/` - **保留**（案例数据）
- ✅ `data/108个案例_新标准评估_完整版_最终版.xlsx` - **保留**（如果需要）

---

### 二、删除旧脚本和临时脚本 ✅

#### 2.1 测试脚本（scripts/）
- [ ] `scripts/test_*.py` - 所有测试脚本
- [ ] `scripts/test_*.sh` - 所有测试shell脚本
- [ ] `scripts/check_*.py` - 检查脚本
- [ ] `scripts/fix_*.py` - 修复脚本（一次性使用）
- [ ] `scripts/analyze_*.py` - 分析脚本（临时）

#### 2.2 临时/调试脚本
- [ ] `scripts/cleanup_python_processes.py` - 进程清理（开发用）
- [ ] `scripts/calculate_costs.py` - 成本计算（临时）
- [ ] `scripts/estimate_100_cases_cost.py` - 成本估算（临时）
- [ ] `scripts/wait_and_calculate_costs.py` - 等待计算（临时）
- [ ] `scripts/重新计算成本_正确价格.py` - 价格修正（临时）
- [ ] `scripts/verify_*.py` - 验证脚本（临时）
- [ ] `scripts/monitor_parallel_runs.sh` - 监控脚本（开发用）

#### 2.3 旧版本脚本
- [ ] `scripts/get_last_10_cases*.py` - 多个版本（保留最新）
- [ ] `scripts/generate_*.py` - 检查哪些是最终版本需要的

#### 2.4 保留的核心脚本 ✅
- ✅ `scripts/run_models_unified_parallel.sh` - **保留**（核心运行脚本）
- ✅ `scripts/generate_advanced_conference_charts.py` - **保留**（生成最终图表）
- ✅ `scripts/batch_import_cases.py` - **保留**（批量导入）
- ✅ `scripts/evaluate_answers.py` - **保留**（评估脚本）
- ✅ `scripts/build.sh` - **保留**（构建脚本）

---

### 三、删除不必要的文档 ✅

#### 3.1 开发过程文档（docs/）
- [ ] `docs/100个案例成本估算.md` - 成本分析（临时）
- [ ] `docs/ChatGPT成本分析.md` - 成本分析
- [ ] `docs/DeepSeek价格澄清.md` - 价格说明
- [ ] `docs/Gemini和Grok成本分析.md` - 成本分析
- [ ] `docs/GPT价格差异分析.md` - 价格分析
- [ ] `docs/各模型总花费对比_正确价格.md` - 成本对比
- [ ] `docs/价格差异解释.md` - 价格说明
- [ ] `docs/模型成本对比表.md` - 成本对比
- [ ] `docs/成本估算.md` - 成本估算
- [ ] `docs/Python进程泄漏分析.md` - 调试报告
- [ ] `docs/内存占用分析.md` - 调试报告
- [ ] `docs/内存紧急分析.md` - 调试报告
- [ ] `docs/内存详细分析_小进程累积.md` - 调试报告
- [ ] `docs/系统资源分析.md` - 调试报告
- [ ] `docs/脚本修复总结.md` - 修复记录
- [ ] `docs/空值问题诊断报告.md` - 调试报告
- [ ] `docs/空回答但有评估结果的问题分析.md` - 调试报告
- [ ] `docs/之前5个案例使用的模型.md` - 临时说明

#### 3.2 保留的核心文档 ✅
- ✅ `docs/BUILD.md` - **保留**（构建说明）
- ✅ `docs/启动说明.md` - **保留**（使用说明）
- ✅ `docs/API限制说明.md` - **保留**（API说明）
- ✅ `docs/workflow_description.md` - **保留**（工作流说明）
- ✅ `docs/temperature_design_explanation.md` - **保留**（设计说明）

#### 3.3 根目录下的开发文档
- [ ] `V1_DEPRECATION_NOTICE.md` - V1弃用通知
- [ ] `V2_COMPLETE_IMPROVEMENTS.md` - V2改进说明
- [ ] `V2_ENGLISH_LOCALIZATION.md` - V2本地化说明
- [ ] `V2_IMPLEMENTATION_SUMMARY.md` - V2实现总结
- [ ] `V2_QUICK_START.md` - V2快速开始（可考虑保留或合并到README）
- [ ] `V2_UI_IMPROVEMENTS.md` - V2 UI改进
- [ ] `ACKNOWLEDGMENTS_UPDATE.md` - 致谢更新（检查是否需要）
- [ ] `CLEANUP_SUMMARY.md` - 清理总结（本次清理后删除）

---

### 四、清理AI痕迹 ✅

#### 4.1 代码注释清理
- [ ] 检查 `app.py` 中的注释，删除AI相关提示
- [ ] 检查 `utils/*.py` 中的注释
- [ ] 检查 `scripts/*.py` 中的注释
- [ ] 删除所有 `TODO`、`FIXME`、`HACK`、`XXX` 注释（除非必要）
- [ ] 删除调试用的 `print()` 语句（或改为日志）

#### 4.2 变量名和函数名
- [ ] 检查是否有明显的AI生成变量名（如 `temp_*`, `test_*` 等）
- [ ] 统一命名规范

#### 4.3 临时文件
- [ ] `redundancy_report.txt` - 冗余报告
- [ ] `test_results_20251229_211314.json` - 测试结果
- [ ] `decoding_settings_table.tsv` - 检查是否在论文中使用

---

### 五、清理静态资源 ✅

#### 5.1 static/ 目录
- [ ] `static/20251229-160906.png` - 临时图片
- [ ] `static/dbb44aed2e738bd4b31c42d892db90d6277f9f2f5383.png` - 临时图片
- [ ] `static/Weixin Image_2025-12-29_161222_207.jpg` - 临时图片
- [ ] `static/cases/*.xlsx` - 检查是否需要

#### 5.2 保留的核心资源 ✅
- ✅ `static/css/` - **保留**
- ✅ `static/js/` - **保留**
- ✅ `static/evaluate/Scoring_Rubric_v1.0_English.md` - **保留**

---

### 六、清理构建产物 ✅

#### 6.1 构建目录
- [ ] `build/` - 构建目录（已在.gitignore中）
- [ ] `dist/` - 分发目录（已在.gitignore中）
- [ ] `__pycache__/` - Python缓存（已在.gitignore中）

---

### 七、更新核心文档 ✅

#### 7.1 README.md
- [ ] 清理README中的开发痕迹
- [ ] 确保README只包含最终版本信息
- [ ] 更新复现步骤
- [ ] 删除V1/V2版本说明（统一为最终版本）

#### 7.2 创建复现文档
- [ ] 创建 `REPRODUCTION.md` - 详细的复现步骤
- [ ] 包含数据准备、环境配置、运行步骤
- [ ] 包含预期结果验证

---

### 八、LaTeX论文文件 ✅

#### 8.1 保留LaTeX文件
- ✅ `latex/paper.tex` - **保留**
- ✅ `latex/references.bib` - **保留**
- ✅ `latex/chart_*.png` - **保留**（论文图表）
- ✅ `latex/compile.sh` - **保留**

#### 8.2 清理LaTeX文档
- [ ] `latex/QUICKSTART.md` - 快速开始（可保留）
- [ ] `latex/README.md` - 检查内容是否需要
- [ ] `latex/UPDATE_NOTES.md` - 更新记录（删除）
- [ ] `latex/UPLOAD_TO_OVERLEAF.md` - 上传指南（可保留）
- [ ] `latex/TEMPLATE_COMPLIANCE_CHECK.md` - 模板检查（删除）
- [ ] `latex/check_acm_compliance.md` - 合规检查（删除）

---

## 📝 清理步骤

### 阶段1: 数据清理
1. 备份当前状态
2. 删除旧数据文件夹
3. 删除旧Excel文件
4. 删除旧图表文件夹

### 阶段2: 脚本清理
1. 识别核心脚本
2. 删除测试脚本
3. 删除临时脚本
4. 删除调试脚本

### 阶段3: 文档清理
1. 删除开发文档
2. 删除调试报告
3. 删除成本分析文档
4. 更新README

### 阶段4: 代码清理
1. 清理代码注释
2. 删除调试语句
3. 统一代码风格
4. 检查变量命名

### 阶段5: 最终检查
1. 验证复现流程
2. 检查文件完整性
3. 更新文档
4. 提交清理版本

---

## ✅ 保留的核心文件结构

```
huangyidan/
├── README.md                    # 主文档（需清理）
├── REPRODUCTION.md              # 复现文档（新建）
├── requirements.txt             # 依赖列表
├── config.py                    # 配置文件
├── app.py                       # 主应用
├── process_cases.py             # 案例处理
├── utils/                       # 工具模块
│   ├── ai_api.py
│   ├── case_manager.py
│   ├── data_masking.py
│   ├── evaluator.py
│   └── ...
├── scripts/                     # 核心脚本（清理后）
│   ├── run_models_unified_parallel.sh
│   ├── generate_advanced_conference_charts.py
│   ├── batch_import_cases.py
│   └── evaluate_answers.py
├── data/                        # 数据目录（清理后）
│   ├── cases/                   # 案例数据
│   └── results_20260112_unified_e8fd22b9/  # 最终结果
├── latex/                       # LaTeX论文（清理后）
│   ├── paper.tex
│   ├── references.bib
│   └── chart_*.png
├── templates/                   # 模板文件
├── static/                      # 静态资源（清理后）
└── docs/                        # 文档（清理后）
    ├── BUILD.md
    ├── 启动说明.md
    └── workflow_description.md
```

---

## 🎯 清理后验证清单

- [ ] 项目可以正常启动
- [ ] 可以复现实验结果
- [ ] 所有图表可以生成
- [ ] README清晰完整
- [ ] 没有明显的AI痕迹
- [ ] 代码风格统一
- [ ] 文档专业规范

---

## 📌 注意事项

1. **备份**: 清理前先创建备份分支
2. **测试**: 每阶段清理后测试功能
3. **文档**: 确保复现文档完整
4. **版本**: 保留git历史，但清理工作目录

---

*最后更新: 2026-01-23*
