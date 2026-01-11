# 法律AI研究平台 - Web功能流程图

## 系统架构流程图

```mermaid
graph TB
    Start([用户访问系统]) --> Home[主页]
    Home --> Tab1[案例管理]
    Home --> Tab2[AI分析]
    Home --> Tab3[分析结果]
    
    Tab1 --> AddCase[添加案例]
    Tab1 --> ImportExcel[Excel批量导入]
    Tab1 --> ViewCases[查看案例列表]
    Tab1 --> DeleteCase[删除案例]
    Tab1 --> DownloadTemplate[下载导入模板]
    
    AddCase --> SaveCase[保存案例到系统]
    ImportExcel --> ParseExcel[解析Excel文件]
    ParseExcel --> ValidateCases[验证案例数据]
    ValidateCases --> SaveCase
    
    Tab2 --> SelectCases[选择案例<br/>支持多选]
    SelectCases --> GenQuestions{生成问题?}
    GenQuestions -->|是| GenerateQ[调用API生成问题]
    GenQuestions -->|否| DirectAnalysis[直接分析]
    GenerateQ --> ShowQuestions[显示生成的问题]
    ShowQuestions --> SelectQuestion[选择问题]
    SelectQuestion --> DirectAnalysis
    DirectAnalysis --> BatchAnalysis{批量分析?}
    BatchAnalysis -->|是| StartBatch[启动批量分析任务]
    BatchAnalysis -->|否| SingleAnalysis[单个分析]
    
    StartBatch --> ShowProgress[显示进度条]
    ShowProgress --> PollProgress[轮询进度]
    PollProgress --> UpdateUI[更新UI显示]
    UpdateUI --> CheckComplete{完成?}
    CheckComplete -->|否| PollProgress
    CheckComplete -->|是| ShowResults[显示结果]
    
    SingleAnalysis --> CallAPI[调用DeepSeek API]
    CallAPI --> GetResult[获取分析结果]
    GetResult --> HasJudge{有法官判决?}
    HasJudge -->|是| Compare[对比分析]
    HasJudge -->|否| SaveResult[保存结果]
    Compare --> SaveResult
    SaveResult --> ShowResults
    
    Tab3 --> ViewResults[查看所有结果]
    ViewResults --> ExportExcel[导出Excel]
    ExportExcel --> DownloadFile[下载文件]
    
    style Start fill:#e1f5ff
    style Home fill:#fff4e1
    style Tab1 fill:#e8f5e9
    style Tab2 fill:#f3e5f5
    style Tab3 fill:#fff3e0
    style SaveCase fill:#c8e6c9
    style ShowResults fill:#c8e6c9
    style DownloadFile fill:#c8e6c9
```

## 详细功能流程图

### 1. 案例管理流程

```mermaid
sequenceDiagram
    participant User as 用户
    participant Web as Web界面
    participant API as Flask API
    participant DB as 案例管理器
    
    User->>Web: 打开案例管理标签页
    Web->>API: GET /api/cases
    API->>DB: 获取所有案例
    DB-->>API: 返回案例列表
    API-->>Web: JSON响应
    Web-->>User: 显示案例列表
    
    alt 手动添加案例
        User->>Web: 填写案例表单
        User->>Web: 点击提交
        Web->>API: POST /api/cases
        API->>DB: 添加案例
        DB-->>API: 返回案例ID
        API-->>Web: 成功响应
        Web-->>User: 显示成功消息
    else Excel批量导入
        User->>Web: 选择Excel文件
        User->>Web: 点击上传
        Web->>API: POST /api/import_cases
        API->>API: 解析Excel文件
        API->>DB: 批量添加案例
        DB-->>API: 返回导入结果
        API-->>Web: 成功响应
        Web-->>User: 显示导入结果
    end
```

### 2. AI分析流程（单个）

```mermaid
sequenceDiagram
    participant User as 用户
    participant Web as Web界面
    participant API as Flask API
    participant DeepSeek as DeepSeek API
    participant DB as 数据存储
    
    User->>Web: 选择案例
    User->>Web: 点击生成问题（可选）
    Web->>API: POST /api/generate_questions
    API->>DeepSeek: 调用生成问题API
    DeepSeek-->>API: 返回问题列表
    API-->>Web: 返回问题
    Web-->>User: 显示问题
    
    User->>Web: 选择问题或输入问题
    User->>Web: 点击开始分析
    Web->>API: POST /api/analyze
    API->>DeepSeek: 调用分析API
    DeepSeek-->>API: 返回分析结果
    
    alt 有法官判决
        API->>DeepSeek: 调用对比分析API
        DeepSeek-->>API: 返回对比结果
        API->>API: 计算相似度指标
    end
    
    API->>DB: 保存分析结果
    API-->>Web: 返回完整结果
    Web-->>User: 显示分析结果
```

### 3. AI分析流程（批量）

```mermaid
sequenceDiagram
    participant User as 用户
    participant Web as Web界面
    participant API as Flask API
    participant ThreadPool as 线程池
    participant DeepSeek as DeepSeek API
    participant Progress as 进度跟踪
    
    User->>Web: 选择多个案例
    User->>Web: 点击批量分析
    Web->>API: POST /api/analyze_batch
    API->>API: 创建任务ID
    API->>Progress: 初始化进度跟踪
    API-->>Web: 返回任务ID
    
    Web->>Web: 开始轮询进度
    Web->>API: GET /api/batch_progress/<task_id>
    
    par 并发处理
        API->>ThreadPool: 提交分析任务1
        API->>ThreadPool: 提交分析任务2
        API->>ThreadPool: 提交分析任务N
    end
    
    ThreadPool->>DeepSeek: 调用API（并发）
    DeepSeek-->>ThreadPool: 返回结果
    
    ThreadPool->>Progress: 更新进度
    Progress-->>API: 返回进度信息
    API-->>Web: 返回进度JSON
    Web-->>User: 更新进度条
    
    ThreadPool->>ThreadPool: 所有任务完成
    Progress->>Progress: 标记完成
    API-->>Web: 返回最终结果
    Web-->>User: 显示所有结果
```

### 4. 结果导出流程

```mermaid
sequenceDiagram
    participant User as 用户
    participant Web as Web界面
    participant API as Flask API
    participant Excel as Excel导出器
    participant File as 文件系统
    
    User->>Web: 打开分析结果标签页
    Web->>API: GET /api/results
    API->>API: 获取所有结果
    API-->>Web: 返回结果列表
    Web-->>User: 显示结果列表
    
    User->>Web: 点击导出Excel
    Web->>API: GET /api/export_results
    API->>Excel: 生成Excel文件
    Excel->>File: 保存文件
    File-->>Excel: 返回文件路径
    Excel-->>API: 返回文件路径
    API-->>Web: 返回文件流
    Web-->>User: 触发下载
```

## 数据流图

```mermaid
graph LR
    A[用户输入] --> B[Web前端]
    B --> C[Flask API]
    C --> D[案例管理器]
    C --> E[DeepSeek API]
    C --> F[Excel导入/导出]
    D --> G[JSON文件存储]
    F --> H[Excel文件]
    E --> I[AI分析结果]
    I --> J[结果存储]
    J --> K[Excel导出]
    
    style A fill:#e1f5ff
    style B fill:#fff4e1
    style C fill:#e8f5e9
    style E fill:#f3e5f5
    style I fill:#c8e6c9
```

## 主要功能模块

### 模块1: 案例管理
- ✅ 手动添加案例
- ✅ Excel批量导入
- ✅ 案例列表查看
- ✅ 案例删除
- ✅ 导入模板下载

### 模块2: AI分析
- ✅ 单个案例分析
- ✅ 批量案例分析（支持多选）
- ✅ 问题自动生成
- ✅ 进度实时显示
- ✅ 结果对比分析（如有法官判决）

### 模块3: 结果管理
- ✅ 结果列表查看
- ✅ Excel批量导出
- ✅ 相似度指标计算

## API端点列表

| 方法 | 路径 | 功能 |
|------|------|------|
| GET | `/` | 主页 |
| GET | `/api/cases` | 获取所有案例 |
| POST | `/api/cases` | 添加案例 |
| GET | `/api/cases/<id>` | 获取指定案例 |
| DELETE | `/api/cases/<id>` | 删除案例 |
| POST | `/api/import_cases` | Excel批量导入 |
| GET | `/api/download_template` | 下载导入模板 |
| POST | `/api/generate_questions` | 生成问题 |
| POST | `/api/analyze` | 单个分析 |
| POST | `/api/analyze_batch` | 批量分析 |
| GET | `/api/batch_progress/<task_id>` | 查询进度 |
| GET | `/api/results` | 获取所有结果 |
| GET | `/api/export_results` | 导出Excel |

## 技术特点

1. **并发处理**：使用ThreadPoolExecutor实现批量分析的并发处理
2. **进度跟踪**：实时显示批量分析的进度和状态
3. **错误处理**：完善的错误处理和重试机制
4. **速率限制**：API调用速率限制，防止超出限制
5. **Excel支持**：支持Excel导入和导出，方便批量操作


