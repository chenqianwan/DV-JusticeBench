# Python进程泄漏分析

## 问题发现

### 发现的异常情况

- **Python进程总数**: 54 个
- **multiprocessing子进程**: 53 个
- **总内存占用**: 1.3 GB
- **启动时间**: 2024年12月22日（已运行16天！）
- **问题**: 这些进程的父进程已经退出，但子进程没有被正确清理

### 进程详情

所有进程都是 `multiprocessing.spawn` 和 `multiprocessing.resource_tracker` 的子进程：
- `multiprocessing.spawn` 进程：用于创建子进程
- `multiprocessing.resource_tracker` 进程：用于跟踪资源

**父进程**: 都是 `launchd` (PID 1)，说明主进程已经退出，但子进程成为孤儿进程

---

## 问题诊断

### 这是典型的内存泄漏问题

1. **主进程退出但子进程未清理**:
   - 之前运行的Python脚本使用了 `multiprocessing`
   - 主进程可能因为异常退出或被强制终止
   - 子进程没有被正确清理，成为孤儿进程

2. **进程残留**:
   - 这些进程已经运行了16天
   - 虽然单个进程内存占用不大（约30MB），但累积起来有1.3GB
   - 如果多次运行脚本，会不断累积

3. **可能的原因**:
   - 脚本异常退出（Ctrl+C、异常等）
   - 没有正确使用 `process.join()` 或 `pool.close()`
   - 没有使用 `try-finally` 确保清理
   - 信号处理不当

---

## 影响

### 当前影响

- **内存占用**: 1.3 GB（虽然不算特别大，但会累积）
- **进程数**: 54个进程占用系统资源
- **潜在问题**: 如果继续运行脚本，进程会不断累积

### 长期影响

- 如果每次运行脚本都留下进程，会不断累积
- 最终可能导致系统资源耗尽
- 48GB内存虽然大，但如果进程不断累积，也会有问题

---

## 解决方案

### 立即清理（推荐）

#### 方法1: 清理所有multiprocessing进程

```bash
# 清理所有multiprocessing.spawn进程
ps aux | grep 'multiprocessing.spawn' | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null

# 清理所有resource_tracker进程
ps aux | grep 'resource_tracker' | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null
```

#### 方法2: 更安全的方式（只清理resource_tracker）

```bash
# 只清理resource_tracker进程（更安全）
ps aux | grep 'resource_tracker' | grep -v grep | awk '{print $2}' | xargs kill 2>/dev/null
```

#### 方法3: 使用pkill（更简单）

```bash
# 清理所有multiprocessing相关进程
pkill -f "multiprocessing.spawn"
pkill -f "resource_tracker"
```

### 预防措施

#### 1. 修改脚本，确保正确清理

在所有使用 `multiprocessing` 的脚本中添加：

```python
import multiprocessing
import signal
import sys

def cleanup_processes():
    """清理所有子进程"""
    for process in multiprocessing.active_children():
        process.terminate()
        process.join(timeout=1)
        if process.is_alive():
            process.kill()

def signal_handler(sig, frame):
    """信号处理，确保清理"""
    cleanup_processes()
    sys.exit(0)

# 注册信号处理
signal.signal(signal.SIGINT, signal_handler)
signal.signal(signal.SIGTERM, signal_handler)

# 使用try-finally确保清理
try:
    # 你的代码
    with multiprocessing.Pool() as pool:
        # 使用pool
        pass
finally:
    cleanup_processes()
```

#### 2. 使用上下文管理器

```python
from contextlib import contextmanager

@contextmanager
def process_pool(*args, **kwargs):
    """确保进程池被正确清理"""
    pool = multiprocessing.Pool(*args, **kwargs)
    try:
        yield pool
    finally:
        pool.close()
        pool.join()
        pool.terminate()

# 使用
with process_pool() as pool:
    # 使用pool
    pass
```

#### 3. 定期清理脚本

创建一个定期清理脚本：

```python
#!/usr/bin/env python3
"""清理残留的multiprocessing进程"""
import subprocess
import sys

def cleanup_multiprocessing_processes():
    """清理multiprocessing残留进程"""
    try:
        # 查找multiprocessing进程
        result = subprocess.run(
            ['ps', 'aux'],
            capture_output=True,
            text=True
        )
        
        processes_to_kill = []
        for line in result.stdout.split('\n'):
            if 'multiprocessing' in line and 'grep' not in line:
                parts = line.split()
                if len(parts) > 1:
                    pid = parts[1]
                    processes_to_kill.append(pid)
        
        if processes_to_kill:
            print(f"发现 {len(processes_to_kill)} 个残留进程，正在清理...")
            for pid in processes_to_kill:
                try:
                    subprocess.run(['kill', pid], check=False)
                    print(f"已清理进程 {pid}")
                except Exception as e:
                    print(f"清理进程 {pid} 失败: {e}")
            print("清理完成")
        else:
            print("没有发现残留进程")
            
    except Exception as e:
        print(f"清理失败: {e}", file=sys.stderr)
        sys.exit(1)

if __name__ == '__main__':
    cleanup_multiprocessing_processes()
```

---

## 检查哪些脚本可能有问题

### 可能的问题脚本

检查以下脚本，确保它们正确清理进程：

1. `test_5_cases_with_new_rubric.py` - 使用了 `ThreadPoolExecutor` 和 `multiprocessing`
2. `process_template_to_analysis.py` - 使用了并发处理
3. `test_masking_and_questions.py` - 使用了 `ThreadPoolExecutor`
4. `evaluate_answers.py` - 使用了并发处理
5. 其他使用 `concurrent.futures` 或 `multiprocessing` 的脚本

### 检查要点

1. 是否使用了 `try-finally` 确保清理？
2. 是否调用了 `pool.close()` 和 `pool.join()`？
3. 是否处理了信号（SIGINT, SIGTERM）？
4. 是否使用了上下文管理器？

---

## 建议

### 立即行动

1. **清理残留进程**（使用上面的命令）
2. **检查并修复脚本**，确保正确清理
3. **创建定期清理脚本**，防止进程累积

### 长期优化

1. **代码审查**: 检查所有使用并发的脚本
2. **添加清理逻辑**: 确保所有脚本都有清理机制
3. **监控**: 定期检查是否有残留进程
4. **测试**: 确保脚本异常退出时也能正确清理

---

## 总结

**问题**: 发现53个Python multiprocessing子进程残留，已运行16天，占用1.3GB内存

**原因**: 之前运行的脚本主进程退出时，子进程没有被正确清理

**影响**: 虽然当前影响不大，但会不断累积，最终可能导致问题

**解决**: 
1. 立即清理残留进程
2. 修复脚本，确保正确清理
3. 创建定期清理机制


