# DV-JusticeBench 复现指南

本文档提供完整的实验复现步骤，确保可以重现论文中的所有实验结果。

---

## 📋 前置要求

### 系统要求
- **操作系统**: macOS, Linux, 或 Windows
- **Python版本**: Python 3.11 或更高版本
- **内存**: 建议 8GB 以上
- **磁盘空间**: 至少 2GB 可用空间

### API密钥要求
需要准备以下API密钥（至少一个）：
- **DeepSeek API Key** (推荐，主要使用)
- OpenAI API Key (可选，用于GPT模型)
- Anthropic API Key (可选，用于Claude模型)
- Qwen API Key (可选，用于Qwen模型)

---

## 🔧 环境配置

### 1. 克隆仓库
```bash
git clone <repository-url>
cd huangyidan
```

### 2. 安装Python依赖
```bash
pip install -r requirements.txt
```

依赖包括：
- Flask==3.0.0
- requests==2.31.0
- pandas==2.1.4
- openpyxl==3.1.2
- python-dotenv==1.0.0

### 3. 配置API密钥

**方式一：环境变量（推荐）**
```bash
export DEEPSEEK_API_KEY=your_deepseek_api_key_here
export OPENAI_API_KEY=your_openai_api_key_here  # 可选
export ANTHROPIC_API_KEY=your_anthropic_api_key_here  # 可选
export QWEN_API_KEY=your_qwen_api_key_here  # 可选
```

**方式二：创建 .env 文件**
在项目根目录创建 `.env` 文件：
```bash
DEEPSEEK_API_KEY=your_deepseek_api_key_here
OPENAI_API_KEY=your_openai_api_key_here
ANTHROPIC_API_KEY=your_anthropic_api_key_here
QWEN_API_KEY=your_qwen_api_key_here
```

**方式三：运行时输入**
程序启动时会提示输入API密钥（仅支持DeepSeek）。

---

## 📊 数据准备

### 1. 案例数据
案例数据位于 `data/cases/` 目录。确保该目录存在：
```bash
mkdir -p data/cases
```

### 2. 最终结果数据
最终评估结果位于：
```
data/results_20260112_unified_e8fd22b9/
├── 20个案例_统一评估结果_108cases.xlsx  # 主要结果文件
├── chart_*.png                          # 所有图表
└── results_详细报告_20260112_172609.txt  # 详细报告
```

---

## 🚀 复现步骤

### 步骤1：启动Web应用（可选）

如果需要使用Web界面进行交互式操作：

```bash
python app.py
```

或使用启动脚本：
```bash
python scripts/run.py
```

访问 `http://localhost:5001` 或 `http://localhost:5001/v2` (V2界面)

### 步骤2：批量导入案例（如果需要）

```bash
python scripts/batch_import_cases.py
```

### 步骤3：运行模型评估

**核心评估脚本**：
```bash
bash scripts/run_models_unified_parallel.sh
```

此脚本会：
1. 对指定案例进行多模型并行评估
2. 生成标准化答案
3. 使用五维评估标准进行评分
4. 输出结果到 `data/results/` 目录

### 步骤4：评估答案质量

```bash
python scripts/evaluate_answers.py
```

### 步骤5：生成图表

**生成所有会议用图表**：
```bash
python scripts/generate_advanced_conference_charts.py
```

此脚本会生成以下图表：
- `chart_ranking.png` - 模型排名
- `chart_token_usage.png` - Token使用对比
- `chart_pareto_tradeoff.png` - 帕累托权衡
- `chart_heatmap_dimensions.png` - 维度热力图
- `chart_errors.png` - 错误统计
- 以及其他分析图表

**其他图表生成脚本**（可选）：
```bash
python scripts/generate_comparison_charts.py      # 对比图表
python scripts/generate_comprehensive_charts.py   # 综合图表
python scripts/generate_7models_charts.py         # 7模型图表
python scripts/plot_radar_chart.py                # 雷达图
```

---

## 📈 验证结果

### 1. 检查结果文件
确认以下文件已生成：
- `data/results_20260112_unified_e8fd22b9/20个案例_统一评估结果_108cases.xlsx`
- `data/results_20260112_unified_e8fd22b9/chart_*.png` (14个图表文件)

### 2. 验证数据完整性
打开Excel文件，检查：
- 案例数量：20个案例
- 问题数量：100个问题（每个案例5个问题）
- 模型数量：6-7个模型
- 评估维度：5个维度 + 错误标志

### 3. 验证图表
检查所有PNG图表文件：
- 图表清晰可读
- 数据标签正确
- 颜色和样式符合预期

### 4. 预期结果

根据论文中的结果，预期排名应为：
1. DeepSeek-Thinking (16.45/20)
2. DeepSeek (16.42/20)
3. Gemini (15.63/20)
4. Claude (13.11/20)
5. Qwen-Max (10.36/20)
6. GPT-4o (9.39/20)

---

## 🔍 关键参数说明

### 评估参数
- **Temperature**: 0.3 (案例分析)
- **Max Tokens**: 3,000 → 16,000 (渐进式增加)
- **Timeout**: 180秒
- **Retry**: 最多3次（指数退避）

### 并发配置
- **默认并发数**: 50个工作线程
- **可在 `config.py` 中调整**: `MAX_CONCURRENT_WORKERS`

### 评估标准
五维评估标准详见：`static/evaluate/Scoring_Rubric_v1.0_English.md`

---

## 🐛 故障排除

### 问题1：API调用失败
**症状**: 出现 "API request failed" 错误

**解决方案**:
1. 检查API密钥是否正确设置
2. 检查网络连接
3. 查看API限制说明：`docs/API限制说明.md`
4. 检查API余额和速率限制

### 问题2：内存不足
**症状**: 程序崩溃或运行缓慢

**解决方案**:
1. 减少并发数：修改 `config.py` 中的 `MAX_CONCURRENT_WORKERS`
2. 分批处理案例
3. 关闭其他占用内存的程序

### 问题3：图表生成失败
**症状**: 图表文件未生成或损坏

**解决方案**:
1. 检查数据文件是否存在
2. 确认pandas和matplotlib版本正确
3. 查看错误日志

### 问题4：端口被占用
**症状**: Web应用无法启动

**解决方案**:
```bash
# 查找占用端口的进程
lsof -i :5001

# 杀死进程（替换PID）
kill -9 <PID>

# 或使用其他端口
export FLASK_PORT=5002
python app.py
```

---

## 📝 详细文档

- **启动说明**: `docs/启动说明.md`
- **构建说明**: `docs/BUILD.md`
- **API限制**: `docs/API限制说明.md`
- **工作流程**: `docs/workflow_description.md`
- **温度参数设计**: `docs/temperature_design_explanation.md`

---

## ✅ 复现检查清单

- [ ] Python 3.11+ 已安装
- [ ] 所有依赖已安装 (`pip install -r requirements.txt`)
- [ ] API密钥已配置
- [ ] 案例数据已准备
- [ ] 评估脚本成功运行
- [ ] 结果Excel文件已生成
- [ ] 所有图表已生成
- [ ] 结果与论文中的结果一致

---

## 📞 支持

如有问题，请查看：
- **README.md**: 项目概述和快速开始
- **docs/**: 详细文档目录
- **GitHub Issues**: 提交问题报告

---

*最后更新: 2026-01-23*
