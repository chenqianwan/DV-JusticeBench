# 法律AI研究平台

基于Python和Flask的Web应用，用于研究AI系统在法律实践中的应用。支持案例管理、AI分析、问题生成和结果导出。

## 功能特性

- 📚 **案例管理**: 导入和管理法律案例（支持文本和JSON格式）
- 🤖 **AI分析**: 使用DeepSeek API进行法律案例分析
- ❓ **问题生成**: 自动生成测试问题集
- 📊 **结果对比**: 对比AI判决与法官判决的差异
- 📥 **Excel导出**: 将分析结果导出为Excel文件

## 技术栈

- **后端**: Flask 3.0
- **API**: DeepSeek API
- **数据处理**: pandas, openpyxl
- **前端**: HTML/CSS/JavaScript

## 安装步骤

1. 克隆或下载项目

2. 安装依赖：
```bash
pip install -r requirements.txt
```

3. 配置环境变量：
创建 `.env` 文件，添加以下内容：
```
DEEPSEEK_API_KEY=your_deepseek_api_key_here
SECRET_KEY=your_secret_key_here
DEBUG=False
```

4. 运行应用：
```bash
python app.py
```

5. 访问应用：
打开浏览器访问 `http://localhost:5000`

## 快速开始

### 方式一：使用启动脚本
```bash
./start.sh
```

### 方式二：手动启动
```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置环境变量（.env文件已创建，包含API密钥）
# 如需修改，编辑 .env 文件

# 3. 启动应用
python app.py
```

## 使用说明

### 1. 案例管理
- **添加案例**: 在"案例管理"标签页填写案例信息
  - 案例标题（必填）
  - 案例内容（必填）
  - 案例日期（可选）
  - 法官判决（可选，用于后续对比分析）
- **查看案例**: 案例列表显示所有已保存的案例
- **删除案例**: 点击案例卡片上的"删除"按钮
- **导出案例**: 点击"导出案例"按钮，将案例列表导出为Excel

### 2. AI分析
- **选择案例**: 在"AI分析"标签页选择要分析的案例
- **生成问题**: 
  - 设置问题数量（默认10个）
  - 点击"生成问题"按钮
  - 生成的问题会显示在下方，点击问题可自动填入分析框
- **执行分析**:
  - 可选择输入具体问题进行分析，或留空进行整体分析
  - 点击"开始分析"按钮
  - 系统会调用DeepSeek API进行分析
  - 如果有法官判决，会自动进行差异对比

### 3. 分析结果
- **查看结果**: 在"分析结果"标签页查看所有分析记录
- **导出Excel**: 点击"导出Excel"按钮，将所有分析结果导出为Excel文件
  - 包含案例信息、问题、AI判决、法官判决、差异对比等完整数据

## 功能特性详解

- **智能问题生成**: 基于案例内容自动生成测试问题，涵盖事实认定、法律适用、判决理由等
- **AI法律分析**: 使用DeepSeek API进行专业的法律案例分析
- **差异对比**: 自动对比AI判决与法官判决的差异，包括判决结果、法律依据、推理过程等
- **Excel导出**: 支持导出案例和分析结果，方便后续研究和报告撰写

## 项目结构

```
huangyidan/
├── app.py                 # Flask主应用
├── config.py             # 配置文件
├── requirements.txt      # Python依赖
├── templates/            # HTML模板
├── static/              # 静态资源
├── data/                # 数据目录
└── utils/              # 工具模块
```

## 打包为可执行文件

### 方式一：使用打包脚本（推荐）
```bash
./build.sh
```

打包完成后，可执行文件位于 `dist/法律AI研究平台/法律AI研究平台`

### 方式二：手动打包
```bash
# 安装PyInstaller
pip install pyinstaller

# 执行打包
pyinstaller pyinstaller.spec
```

### 运行打包后的程序
```bash
# 直接运行，会提示输入API密钥
./dist/法律AI研究平台/法律AI研究平台
```

或者设置环境变量后运行：
```bash
export DEEPSEEK_API_KEY=your_api_key_here
./dist/法律AI研究平台/法律AI研究平台
```

## 运行方式

### 开发模式
```bash
python run.py
# 或
python app.py
```

### 打包后的可执行文件
直接运行可执行文件，首次运行会提示输入DeepSeek API密钥。

## 注意事项

- 确保已获取DeepSeek API密钥
- 案例数据存储在 `data/cases/` 目录
- 分析结果存储在 `data/results/` 目录
- 生产环境请修改 `SECRET_KEY` 和关闭 `DEBUG` 模式
- 打包后的程序会在运行时提示输入API密钥，也可以设置环境变量 `DEEPSEEK_API_KEY` 来避免每次输入

