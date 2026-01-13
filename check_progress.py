#!/usr/bin/env python3
"""
检查当前进度：统计已完成的请求
"""
import pandas as pd
import os
import glob
from datetime import datetime

# 查找最新的结果文件
pattern = 'data/GPT-o3_case_20260103_155150_3_20个问题测试_*.xlsx'
files = glob.glob(pattern)

if files:
    # 找到最新的文件
    latest_file = max(files, key=os.path.getmtime)
    print(f"找到结果文件: {latest_file}")
    
    # 读取数据
    df = pd.read_excel(latest_file, engine='openpyxl')
    
    print("\n" + "=" * 60)
    print("当前进度统计")
    print("=" * 60)
    print(f"总问题数: {len(df)}")
    
    # 统计成功和失败
    success_count = len(df[df['是否空回答'] == '否'])
    empty_count = len(df[df['是否空回答'] == '是'])
    
    print(f"成功回答数: {success_count}")
    print(f"空回答/失败数: {empty_count}")
    print(f"成功率: {success_count/len(df)*100:.1f}%")
    
    # 显示有错误的问题
    if '处理错误' in df.columns:
        error_count = df['处理错误'].notna().sum()
        if error_count > 0:
            print(f"\n有错误的问题数: {error_count}")
            print("\n错误详情:")
            error_rows = df[df['处理错误'].notna()]
            for idx, row in error_rows.iterrows():
                print(f"\n问题 {row['问题编号']}: {row['问题'][:60]}...")
                print(f"  错误类型: {row['处理错误']}")
                if '错误详情' in row and pd.notna(row['错误详情']):
                    print(f"  错误详情: {str(row['错误详情'])[:200]}...")
    
    # 显示成功的问题
    if success_count > 0:
        print(f"\n成功的问题:")
        success_rows = df[df['是否空回答'] == '否']
        for idx, row in success_rows.iterrows():
            answer_len = len(str(row['AI回答'])) if pd.notna(row['AI回答']) else 0
            print(f"  问题 {row['问题编号']}: {answer_len} 字符")
    
else:
    print("未找到结果文件")
    print("脚本可能还在运行中或尚未保存结果")
