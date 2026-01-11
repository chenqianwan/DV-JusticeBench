"""
进程清理工具模块
提供信号处理和进程清理功能，防止进程泄漏
"""
import signal
import sys
import concurrent.futures
import multiprocessing
import atexit
import threading

# 全局变量，跟踪所有executor
_active_executors = []
_executor_lock = threading.Lock()


def register_executor(executor):
    """注册executor，确保在退出时清理"""
    with _executor_lock:
        _active_executors.append(executor)


def unregister_executor(executor):
    """取消注册executor"""
    with _executor_lock:
        if executor in _active_executors:
            _active_executors.remove(executor)


def cleanup_all_executors():
    """清理所有注册的executor"""
    with _executor_lock:
        for executor in _active_executors[:]:  # 复制列表，避免修改时迭代
            try:
                if hasattr(executor, 'shutdown'):
                    executor.shutdown(wait=False)
            except Exception as e:
                print(f"清理executor失败: {e}", file=sys.stderr)
        _active_executors.clear()


def cleanup_multiprocessing_processes():
    """清理所有multiprocessing子进程"""
    try:
        for process in multiprocessing.active_children():
            try:
                process.terminate()
                process.join(timeout=1)
                if process.is_alive():
                    process.kill()
            except Exception as e:
                print(f"清理进程 {process.pid} 失败: {e}", file=sys.stderr)
    except Exception as e:
        print(f"清理multiprocessing进程失败: {e}", file=sys.stderr)


def signal_handler(sig, frame):
    """信号处理函数，确保清理所有资源"""
    print("\n收到中断信号，正在清理资源...", file=sys.stderr, flush=True)
    
    # 清理所有executor
    cleanup_all_executors()
    
    # 清理multiprocessing进程
    cleanup_multiprocessing_processes()
    
    print("清理完成，退出...", file=sys.stderr, flush=True)
    sys.exit(0)


def setup_signal_handlers():
    """设置信号处理器"""
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    
    # 注册退出时的清理函数
    atexit.register(cleanup_all_executors)
    atexit.register(cleanup_multiprocessing_processes)


class SafeThreadPoolExecutor(concurrent.futures.ThreadPoolExecutor):
    """带自动清理的ThreadPoolExecutor"""
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        register_executor(self)
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        result = super().__exit__(exc_type, exc_val, exc_tb)
        unregister_executor(self)
        return result


# 初始化时设置信号处理器
setup_signal_handlers()


