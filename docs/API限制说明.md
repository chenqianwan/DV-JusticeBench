# API调用限制和并发说明

## 当前实现状态

### 1. 提问次数限制

**理论上无限制**：
- 应用本身没有设置提问次数上限
- 可以连续进行多次分析
- 实际限制取决于：
  - DeepSeek API账户的配额限制
  - API密钥的计费额度
  - DeepSeek官方对账户的速率限制

### 2. 并发处理状态

**当前实现：串行处理（无并发）**

#### 单个案例分析
- ✅ 单次请求，无并发问题
- 请求超时时间：180秒
- 重试机制：最多3次，指数退避

#### 多个案例分析
- ⚠️ **串行处理**：逐个案例依次分析
- 代码位置：`app.py` 第490-518行
- 处理方式：
  ```python
  for i in range(cases.length):
      # 逐个分析，等待上一个完成后再处理下一个
      analyze_case(case[i])
  ```
- **问题**：如果分析10个案例，每个需要60秒，总耗时约10分钟

### 3. 速率限制处理

**当前状态：未实现速率限制处理**

- ❌ 没有处理HTTP 429（Too Many Requests）错误
- ❌ 没有请求队列机制
- ❌ 没有速率限制检测
- ✅ 有重试机制（网络错误时）

## DeepSeek API 常见限制

根据一般API服务惯例，DeepSeek API可能存在的限制：

### 速率限制（Rate Limits）
- **每分钟请求数（RPM）**：通常为 20-60 次/分钟
- **每秒请求数（RPS）**：通常为 2-5 次/秒
- **并发请求数**：通常为 5-10 个并发

### 配额限制（Quota）
- **每日请求数**：取决于账户类型
- **Token使用量**：取决于计费计划
- **月度配额**：根据订阅计划

## 潜在问题

### 1. 批量分析时的限制
当选择多个案例进行分析时：
- 串行处理导致总耗时很长
- 如果API有速率限制，可能触发429错误
- 没有错误恢复机制

### 2. 高频率使用
如果用户快速连续点击：
- 可能触发API速率限制
- 没有请求队列，可能导致请求失败
- 错误提示不够明确

## 建议改进方案

### 方案1：添加速率限制检测（推荐）
```python
# 在 deepseek_api.py 中添加
import time
from collections import deque

class DeepSeekAPI:
    def __init__(self):
        self.request_times = deque(maxlen=60)  # 记录最近60秒的请求
        self.min_interval = 0.5  # 最小请求间隔（秒）
    
    def _rate_limit_check(self):
        """检查速率限制"""
        now = time.time()
        # 移除60秒前的记录
        while self.request_times and now - self.request_times[0] > 60:
            self.request_times.popleft()
        
        # 如果最近60秒内请求超过50次，等待
        if len(self.request_times) >= 50:
            wait_time = 60 - (now - self.request_times[0])
            if wait_time > 0:
                time.sleep(wait_time)
        
        # 确保最小间隔
        if self.request_times:
            last_time = self.request_times[-1]
            if now - last_time < self.min_interval:
                time.sleep(self.min_interval - (now - last_time))
        
        self.request_times.append(time.time())
```

### 方案2：处理429错误
```python
def _make_request(self, ...):
    for attempt in range(self.max_retries):
        try:
            response = requests.post(...)
            
            # 处理速率限制错误
            if response.status_code == 429:
                retry_after = int(response.headers.get('Retry-After', 60))
                print(f"触发速率限制，等待 {retry_after} 秒后重试...")
                time.sleep(retry_after)
                continue
            
            response.raise_for_status()
            ...
```

### 方案3：并发处理（高级）
使用异步或线程池实现并发分析：
```python
import concurrent.futures

# 使用线程池并发处理
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
    futures = [executor.submit(analyze_case, case) for case in cases]
    results = [f.result() for f in futures]
```

## 当前使用建议

### 1. 单个案例分析
- ✅ 可以正常使用，无并发问题
- ✅ 建议每次分析间隔至少1-2秒

### 2. 批量分析（多个案例）
- ⚠️ 建议每次选择不超过5个案例
- ⚠️ 预计每个案例需要30-90秒
- ⚠️ 总耗时 = 案例数 × 单次分析时间

### 3. 高频率使用
- ⚠️ 避免快速连续点击"开始分析"按钮
- ⚠️ 建议操作间隔至少2-3秒
- ⚠️ 如果遇到429错误，等待1分钟后重试

## 监控和调试

### 查看API调用情况
应用会在控制台输出：
- `[Token使用] 输入: X, 输出: Y, 总计: Z`
- `[DeepSeek API] 开始分析案例...`
- `API请求失败，X秒后重试...`

### 常见错误
1. **429 Too Many Requests**：触发速率限制
   - 解决方案：等待1分钟后重试
2. **网络超时**：请求超过180秒
   - 解决方案：检查案例文本长度，可能需要分段处理
3. **API密钥错误**：401 Unauthorized
   - 解决方案：检查 `.env` 文件中的API密钥

## 总结

| 项目 | 当前状态 | 限制 |
|------|---------|------|
| 提问次数 | ✅ 无限制 | 取决于API账户配额 |
| 并发处理 | ❌ 串行处理 | 无并发，逐个处理 |
| 速率限制 | ⚠️ 未处理 | 可能触发429错误 |
| 重试机制 | ✅ 已实现 | 最多3次，指数退避 |
| 超时设置 | ✅ 已设置 | 180秒 |

**建议**：对于日常使用，当前实现已经足够。如果需要进行大量批量分析，建议：
1. 分批处理（每次5-10个案例）
2. 在非高峰时段使用
3. 监控API调用情况，避免触发限制


