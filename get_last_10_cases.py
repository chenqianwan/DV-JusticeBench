#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
获取最后10个案例的名称
"""
import pandas as pd
import sys

def main():
    file_path = 'data/108个案例_新标准评估_完整版_最终版.xlsx'
    
    try:
        print("正在读取文件...")
        # 尝试使用openpyxl引擎
        df = pd.read_excel(file_path, engine='openpyxl')
        
        print(f"总行数: {len(df)}")
        print(f"总列数: {len(df.columns)}")
        
        # 检查必要的列
        if '案例ID' not in df.columns:
            print(f"\n错误: 找不到'案例ID'列")
            print(f"可用列: {list(df.columns)}")
            return
        
        if '案例标题' not in df.columns:
            print(f"\n错误: 找不到'案例标题'列")
            print(f"可用列: {list(df.columns)}")
            return
        
        # 获取所有唯一的案例
        case_info = df[['案例ID', '案例标题']].drop_duplicates()
        
        print(f"\n唯一案例数: {len(case_info)}")
        
        # 按案例ID排序
        case_info = case_info.sort_values('案例ID')
        
        # 获取最后10个案例
        last_10_cases = case_info.tail(10)
        
        print("\n" + "=" * 80)
        print("最后10个案例:")
        print("=" * 80)
        
        for idx, (_, row) in enumerate(last_10_cases.iterrows(), 1):
            case_id = row['案例ID']
            case_title = row['案例标题']
            print(f"{idx:2d}. {case_id}")
            print(f"    标题: {case_title}")
            print()
            
    except Exception as e:
        print(f"错误: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)

if __name__ == '__main__':
    main()
