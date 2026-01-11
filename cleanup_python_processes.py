#!/usr/bin/env python3
"""
清理残留的Python multiprocessing进程
安全清理之前脚本留下的孤儿进程
"""
import subprocess
import sys
import time

def find_multiprocessing_processes():
    """查找所有multiprocessing相关进程"""
    try:
        result = subprocess.run(
            ['ps', 'aux'],
            capture_output=True,
            text=True,
            errors='ignore'
        )
        
        processes = []
        for line in result.stdout.split('\n'):
            if 'multiprocessing' in line.lower() and 'grep' not in line:
                parts = line.split()
                if len(parts) > 1:
                    try:
                        pid = int(parts[1])
                        mem_mb = float(parts[5]) / 1024
                        cmd = ' '.join(parts[10:])
                        processes.append({
                            'pid': pid,
                            'mem': mem_mb,
                            'cmd': cmd
                        })
                    except (ValueError, IndexError):
                        continue
        
        return processes
    except Exception as e:
        print(f"查找进程失败: {e}", file=sys.stderr)
        return []

def cleanup_processes(processes, dry_run=False):
    """清理进程"""
    if not processes:
        print("没有发现残留进程")
        return 0
    
    total_mem = sum(p['mem'] for p in processes)
    print(f"发现 {len(processes)} 个残留进程，总内存占用: {total_mem:.2f} MB ({total_mem/1024:.2f} GB)")
    print()
    
    if dry_run:
        print("【模拟模式】以下进程将被清理:")
        for p in processes[:10]:  # 只显示前10个
            print(f"  PID {p['pid']}: {p['mem']:.2f} MB - {p['cmd'][:60]}")
        if len(processes) > 10:
            print(f"  ... 还有 {len(processes)-10} 个进程")
        print()
        print("要实际清理，请运行: python3 cleanup_python_processes.py --clean")
        return 0
    
    print("正在清理进程...")
    cleaned = 0
    failed = 0
    
    for p in processes:
        try:
            # 先尝试TERM信号（优雅退出）
            subprocess.run(['kill', '-TERM', str(p['pid'])], 
                         check=False, 
                         capture_output=True,
                         timeout=1)
            time.sleep(0.1)
            
            # 检查进程是否还在
            check_result = subprocess.run(
                ['ps', '-p', str(p['pid'])],
                capture_output=True
            )
            
            if check_result.returncode == 0:
                # 进程还在，使用KILL信号
                subprocess.run(['kill', '-KILL', str(p['pid'])], 
                             check=False,
                             capture_output=True,
                             timeout=1)
            
            cleaned += 1
            if cleaned % 10 == 0:
                print(f"  已清理 {cleaned}/{len(processes)} 个进程...")
                
        except Exception as e:
            failed += 1
            print(f"  清理进程 {p['pid']} 失败: {e}", file=sys.stderr)
    
    print()
    print(f"清理完成: {cleaned} 个进程已清理")
    if failed > 0:
        print(f"失败: {failed} 个进程")
    
    return cleaned

def main():
    import argparse
    parser = argparse.ArgumentParser(description='清理残留的Python multiprocessing进程')
    parser.add_argument('--clean', action='store_true', help='实际清理进程（默认是模拟模式）')
    parser.add_argument('--dry-run', action='store_true', help='模拟模式（默认）')
    args = parser.parse_args()
    
    print("=" * 70)
    print("Python进程清理工具")
    print("=" * 70)
    print()
    
    processes = find_multiprocessing_processes()
    
    if args.clean:
        cleaned = cleanup_processes(processes, dry_run=False)
    else:
        cleaned = cleanup_processes(processes, dry_run=True)
    
    print("=" * 70)
    
    if cleaned > 0:
        print(f"✅ 成功清理 {cleaned} 个进程")
    elif not args.clean:
        print("💡 使用 --clean 参数来实际清理进程")
    
    return 0 if cleaned >= 0 else 1

if __name__ == '__main__':
    sys.exit(main())


