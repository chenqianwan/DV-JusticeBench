# ACM SIGCONF格式合规性检查清单

## 📄 文档结构检查

### 必需元素
- [ ] 使用 `\documentclass[sigconf]{acmart}` 
- [ ] 包含标题 `\title{}`
- [ ] 包含作者信息 `\author{}` 和 `\affiliation{}`
- [ ] 包含摘要 `\begin{abstract}...\end{abstract}`
- [ ] 包含关键词 `\keywords{}`
- [ ] 使用 `\maketitle` 生成标题页
- [ ] 包含参考文献

### 版权信息（投稿后更新）
- [ ] `\setcopyright{}` - 设置版权类型
- [ ] `\acmConference{}` - 会议信息
- [ ] `\acmYear{}` - 年份
- [ ] `\acmDOI{}` - DOI（如有）
- [ ] `\acmISBN{}` - ISBN（如有）

## 📏 格式要求

### 页面设置
- [ ] **不要**修改页边距
- [ ] **不要**修改字体大小
- [ ] **不要**修改行距
- [ ] **不要**使用 `\vspace` 调整间距
- [ ] **不要**使用 `\enlargethispage`

### 字体要求
- [ ] 使用默认的Libertine字体（acmart自动设置）
- [ ] **不要**使用 `\usepackage{times}` 或 `\usepackage{lmodern}`
- [ ] 数学公式使用默认字体

### 页数限制
- [ ] SIGCONF通常限制：**10页**（包括参考文献）
- [ ] 检查编译后的PDF页数

## 🖼️ 图表要求

### 图片格式
- [ ] 使用PDF、PNG或JPEG格式
- [ ] 图片分辨率至少300 DPI
- [ ] 图片宽度不超过列宽或页宽
- [ ] 每个图都有caption `\caption{}`
- [ ] 每个图都有description `\Description{}`（用于无障碍访问）

### 图表位置
- [ ] 使用标准浮动环境 `figure` 或 `table`
- [ ] 图表caption在图下方、表上方
- [ ] 在正文中引用所有图表 `Figure~\ref{}`

## 📚 参考文献要求

### BibTeX设置
- [ ] 使用 `\bibliographystyle{ACM-Reference-Format}`
- [ ] 使用 `\bibliography{references}` 引入bib文件
- [ ] 编译顺序：pdflatex → bibtex → pdflatex × 2

### 引用格式
- [ ] 使用 `\cite{}` 引用文献
- [ ] 所有引用的文献都在references.bib中
- [ ] 所有bib条目都有必需字段（author, title, year等）
- [ ] DOI和URL正确格式化

### 引用风格
- [ ] 默认使用数字引用 [1]
- [ ] 如需作者-年份格式，使用 `\citestyle{acmauthoryear}`

## ✍️ 内容要求

### 章节结构
- [ ] 使用标准命令：`\section`, `\subsection`, `\subsubsection`
- [ ] 章节**保持编号**（不要使用`\section*`）
- [ ] **不要**手动编号

### 文本格式
- [ ] 使用 `\textit{}` 表示斜体
- [ ] 使用 `\textbf{}` 表示粗体
- [ ] 使用 `\texttt{}` 表示代码
- [ ] **不要**使用下划线 `\underline{}`

### 数学公式
- [ ] 行内公式：`$...$` 或 `\(...\)`
- [ ] 独立公式：`\begin{equation}...\end{equation}`
- [ ] 复杂公式使用align环境
- [ ] 引用公式使用 `\eqref{}`

## 🔤 特殊字符和符号

### URL处理
- [ ] 使用 `\url{}` 包装URL
- [ ] 长URL会自动换行

### 特殊符号
- [ ] 使用LaTeX命令：`\%`, `\&`, `\#`, `\_`, `\{`, `\}`
- [ ] 引号使用 ``` `` ``` 和 `''`（不是 `"`）

## 📊 ACM Computing Classification

### CCS概念（可选但推荐）
- [ ] 从 https://dl.acm.org/ccs 生成CCS代码
- [ ] 在 `\maketitle` 之前插入CCS代码
- [ ] 示例：
```latex
\begin{CCSXML}
... (从ACM网站复制)
\end{CCSXML}
\ccsdesc[500]{Computing methodologies~Artificial intelligence}
```

## 🎨 PDF质量检查

### PDF生成
- [ ] 使用pdfLaTeX编译（不是LaTeX+dvips）
- [ ] 确保所有字体嵌入
- [ ] PDF版本兼容（PDF 1.5或更高）

### 颜色模式
- [ ] 彩色图表适合彩色打印和黑白打印
- [ ] 避免纯红/纯绿组合（色盲友好）

### 超链接
- [ ] 内部引用自动生成超链接
- [ ] 外部URL可点击
- [ ] 超链接不影响打印效果

## 📝 提交前最后检查

### 元数据完整性
- [ ] 作者姓名拼写正确
- [ ] 机构名称正确
- [ ] 邮箱地址有效
- [ ] 会议信息准确

### 内容完整性
- [ ] 所有章节完整
- [ ] 所有图表都能正确显示
- [ ] 所有引用都有对应参考文献
- [ ] 没有"TODO"或占位符文本
- [ ] 摘要不超过150-250词

### 质量检查
- [ ] 全文拼写检查
- [ ] 语法检查
- [ ] 标点符号一致性
- [ ] 术语使用一致

## 🚀 Overleaf快速检查

### 在Overleaf中检查：

1. **编译日志**
   - 点击 "Logs and output files"
   - 查看是否有Error或Warning
   - 黄色警告通常可以忽略，但红色错误必须修复

2. **字数统计**
   - 顶部菜单 → Word Count
   - 检查是否在要求范围内

3. **PDF预览**
   - 右侧预览窗口检查：
     - 页数是否正确
     - 图表是否清晰
     - 布局是否合理

4. **下载PDF**
   - 点击PDF图标下载
   - 在本地PDF阅读器中再次检查

## 🔧 常见问题修复

### 图片不显示
```latex
% 确保路径正确
\includegraphics{figure.png}  % 正确
\includegraphics{./figure.png}  % 也可以
```

### 参考文献为空
```bash
# 完整编译流程
pdflatex paper
bibtex paper
pdflatex paper
pdflatex paper
```

### 超过页数限制
- 压缩图片尺寸：`width=0.8\linewidth` → `width=0.6\linewidth`
- 精简文字
- 移动部分内容到附录

### 中文字符显示问题
```latex
% 如果需要中文（虽然ACM主要是英文）
\usepackage{xeCJK}
\setCJKmainfont{SimSun}
```

## 📦 提交检查表

提交前确认：

- [ ] PDF文件大小 < 10MB
- [ ] PDF文件名有意义（不是"paper.pdf"）
- [ ] 包含所有必需的补充材料
- [ ] 已通过PDF Express检查（如果会议要求）
- [ ] 保留源文件备份（.tex, .bib, 图片等）

---

## ⚠️ ACM禁止的操作

### 绝对禁止：
- ❌ 修改acmart.cls文件
- ❌ 重定义ACM提供的命令
- ❌ 使用 `\vspace` 调整间距
- ❌ 修改页边距、字体大小、行距
- ❌ 使用 `\setlength` 修改任何长度参数
- ❌ 使用非标准的sectioning命令

### 如果违反这些规则：
**论文可能被退回要求修改！**

---

## 💡 Pro Tips

1. **早期经常编译**：不要等到最后才编译，每次修改后都编译检查
2. **使用Git版本控制**：Overleaf支持Git，可以追踪修改历史
3. **参考ACM模板示例**：查看acmart包提供的sample文件
4. **参考已发表论文**：看看往年同会议的论文格式

---

## 📞 获取帮助

- ACM模板文档：https://www.acm.org/publications/proceedings-template
- Overleaf帮助：https://www.overleaf.com/learn
- ACM支持：authors@acm.org
- 会议组织者：查看Call for Papers中的联系方式

---

**最后提醒**：不同ACM会议可能有特殊要求，请务必查看您投稿会议的具体Call for Papers！
