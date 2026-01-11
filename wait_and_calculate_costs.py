"""
等待评估脚本完成，然后计算成本
"""
import time
import subprocess
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from calculate_costs import main as calculate_costs_main

def check_process_running(process_name):
    """检查进程是否在运行"""
    try:
        result = subprocess.run(
            ['pgrep', '-f', process_name],
            capture_output=True,
            text=True
        )
        return len(result.stdout.strip()) > 0
    except:
        return False

def main():
    """主函数"""
    print("等待评估脚本完成...")
    print("=" * 60)
    
    max_wait_time = 3600  # 最多等待1小时
    check_interval = 10   # 每10秒检查一次
    elapsed = 0
    
    while elapsed < max_wait_time:
        if not check_process_running('test_5_cases_with_new_rubric.py'):
            print("\n评估脚本已完成！")
            print("=" * 60)
            print()
            break
        
        elapsed += check_interval
        minutes = elapsed // 60
        seconds = elapsed % 60
        print(f"\r已等待: {minutes}分{seconds}秒...", end='', flush=True)
        time.sleep(check_interval)
    else:
        print("\n等待超时，但继续计算成本...")
    
    print()
    print()
    
    # 计算成本
    calculate_costs_main()

if __name__ == '__main__':
    main()

