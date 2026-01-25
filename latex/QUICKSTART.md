# Quick Start Guide - 从Word到LaTeX的最后步骤

## ✅ 已完成

我已经为您完成了以下工作：

1. **主LaTeX文件** (`paper.tex`) - 完整转换了10页版本的论文内容
2. **参考文献** (`references.bib`) - 所有引用都已转换为BibTeX格式
3. **编译脚本** (`compile.sh`) - 自动化编译流程
4. **文档说明** (`README.md`) - 详细的使用说明

## ⚠️ 需要您完成的步骤

### 第1步：生成图表文件

论文需要以下5个图表文件（PNG格式）：

1. `chart_token_usage.png` - Token使用对比图
2. `chart_ranking.png` - 模型总体排名图
3. `chart_pareto_tradeoff.png` - 帕累托权衡图（质量-可靠性-成本）
4. `chart_heatmap_dimensions.png` - 模型性能热力图
5. `chart_errors.png` - 错误统计图

**如何生成**：
- 从您的实验结果中导出这些图表
- 确保图表清晰，适合论文打印
- 将PNG文件放在 `latex/` 目录下

**临时方案**：如果暂时没有图表，可以先注释掉LaTeX中的图表部分，或使用占位图。

### 第2步：更新作者信息

在 `paper.tex` 文件中，找到并修改以下部分：

```latex
%% 第116-125行左右
\author{Anonymous Authors}  % <- 改成真实作者名
\affiliation{%
  \institution{Affiliation}  % <- 改成机构名
  \city{City}                % <- 改成城市
  \country{Country}          % <- 改成国家
}
\email{anonymous@example.com}  % <- 改成真实邮箱

\renewcommand{\shortauthors}{Anonymous et al.}  % <- 改成简短作者列表
```

### 第3步：更新会议信息

找到第111-113行：

```latex
\acmConference[ICAIL '26]{International Conference on Artificial Intelligence and Law}{June 2026}{Cambridge, MA, USA}
```

根据实际投稿的会议/期刊更新。

### 第4步：编译LaTeX

```bash
cd /Users/chenlong/WorkSpace/huangyidan/latex
./compile.sh
```

或者手动编译：

```bash
pdflatex paper.tex
bibtex paper
pdflatex paper.tex
pdflatex paper.tex
```

### 第5步：检查生成的PDF

打开 `paper.pdf`，检查：
- [ ] 所有章节都在
- [ ] 图表显示正确
- [ ] 参考文献都有
- [ ] 格式符合ACM要求
- [ ] 页数符合要求（10页以内）

## 📦 上传到Overleaf（推荐）

1. 访问 https://www.overleaf.com
2. 创建新项目 → "Upload Project"
3. 上传整个 `latex/` 目录的内容
4. 设置主文件为 `paper.tex`
5. 编译器选择 `pdfLaTeX`

## 🔧 常见问题

### Q: 缺少acmart.cls文件？
A: Overleaf自带ACM模板。本地编译需要安装TeX Live完整版。

### Q: 图表显示不出来？
A: 检查PNG文件是否在latex目录下，文件名是否匹配（区分大小写）。

### Q: 参考文献格式不对？
A: 确保运行了完整的编译流程（pdflatex → bibtex → pdflatex × 2）。

### Q: 超过10页？
A: 可以：
   - 缩减图表尺寸
   - 精简某些段落
   - 移动部分内容到附录

### Q: 需要添加中文支持？
A: 当前模板已经可以处理中文引用。如需更多中文内容，可添加：
```latex
\usepackage{xeCJK}
\setCJKmainfont{SimSun}  % 或其他中文字体
```

## 📝 下一步

完成上述步骤后，您的LaTeX论文就准备好投稿了！

建议：
1. 让同事审阅PDF
2. 运行拼写检查
3. 验证所有链接可访问
4. 准备补充材料（代码、数据等）

## 💡 提示

- 保留Word版本作为备份
- 在Overleaf上协作编辑更方便
- 定期提交Git版本（如果使用版本控制）
- 投稿前仔细阅读会议的LaTeX指南

---

**需要帮助？** 检查 README.md 获取更多详细信息。
