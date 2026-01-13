#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
合并16个案例和4个案例的结果，生成完整的20个案例数据集
"""
import pandas as pd
import os
from datetime import datetime

def merge_cases():
    """合并16+4个案例，并加入GPT-5数据"""
    
    # 文件路径
    file_16 = 'data/results_20260112_unified_e8fd22b9/16个案例_统一评估结果_108cases.xlsx'
    file_4 = 'data/results_20260112_unified_e8fd22b9/4个案例_统一评估结果_108cases.xlsx'
    file_gpt5 = 'data/results_20260111_151734/GPT5_20个案例评估_20260111_153605.xlsx'
    output_dir = 'data/results_20260112_unified_e8fd22b9'
    output_file = os.path.join(output_dir, '20个案例_统一评估结果_108cases.xlsx')
    
    print("=" * 80)
    print("合并16+4个案例结果")
    print("=" * 80)
    print()
    
    # 读取两个文件的所有sheet
    print(f"读取文件1: {file_16}")
    excel_16 = pd.ExcelFile(file_16)
    sheets_16 = excel_16.sheet_names
    print(f"  Sheet: {sheets_16}")
    
    print(f"\n读取文件2: {file_4}")
    excel_4 = pd.ExcelFile(file_4)
    sheets_4 = excel_4.sheet_names
    print(f"  Sheet: {sheets_4}")
    
    # 读取GPT-5数据（如果存在）
    gpt5_data = None
    if os.path.exists(file_gpt5):
        print(f"\n读取GPT-5文件: {file_gpt5}")
        try:
            excel_gpt5 = pd.ExcelFile(file_gpt5)
            gpt5_sheet = excel_gpt5.sheet_names[0]  # 通常是Sheet1
            gpt5_data = pd.read_excel(file_gpt5, sheet_name=gpt5_sheet)
            print(f"  GPT-5数据: {len(gpt5_data)}行, {gpt5_data['案例ID'].nunique() if '案例ID' in gpt5_data.columns else 'N/A'}个案例")
        except Exception as e:
            print(f"  ⚠️ 读取GPT-5文件失败: {e}")
            gpt5_data = None
    else:
        print(f"\n⚠️ GPT-5文件不存在: {file_gpt5}")
    
    # 获取所有唯一的模型名称
    all_models = list(set(sheets_16 + sheets_4))
    if gpt5_data is not None:
        all_models.append('GPT-5')
    print(f"\n所有模型: {all_models}")
    print()
    
    # 合并每个模型的数据
    merged_data = {}
    
    for model in all_models:
        print(f"处理模型: {model}")
        
        # 读取16个案例的数据
        df_16 = None
        if model in sheets_16:
            df_16 = pd.read_excel(file_16, sheet_name=model)
            print(f"  16个案例: {len(df_16)}行")
        else:
            print(f"  ⚠️ 16个案例文件中没有 {model}")
        
        # 读取4个案例的数据
        df_4 = None
        if model in sheets_4:
            df_4 = pd.read_excel(file_4, sheet_name=model)
            print(f"  4个案例: {len(df_4)}行")
        else:
            print(f"  ⚠️ 4个案例文件中没有 {model}")
        
        # 合并数据
        if df_16 is not None and df_4 is not None:
            df_merged = pd.concat([df_16, df_4], ignore_index=True)
            merged_data[model] = df_merged
            print(f"  ✓ 合并后: {len(df_merged)}行 (期望: 100行)")
        elif df_16 is not None:
            merged_data[model] = df_16
            print(f"  ⚠️ 只有16个案例数据: {len(df_16)}行")
        elif df_4 is not None:
            merged_data[model] = df_4
            print(f"  ⚠️ 只有4个案例数据: {len(df_4)}行")
        else:
            print(f"  ✗ 没有数据可合并")
        
        print()
    
    # 处理GPT-5数据（单独添加，不与其他模型合并）
    if gpt5_data is not None:
        print(f"处理模型: GPT-5")
        print(f"  GPT-5数据: {len(gpt5_data)}行")
        merged_data['GPT-5'] = gpt5_data
        print(f"  ✓ GPT-5已添加")
        print()
    
    # 保存合并后的文件
    print("=" * 80)
    print(f"保存合并结果到: {output_file}")
    print("=" * 80)
    
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        for model, df in merged_data.items():
            df.to_excel(writer, sheet_name=model, index=False)
            print(f"  ✓ {model}: {len(df)}行")
    
    print()
    print("=" * 80)
    print("✓ 合并完成！")
    print("=" * 80)
    print(f"输出文件: {output_file}")
    print()
    
    # 统计信息
    print("统计信息:")
    for model, df in merged_data.items():
        if '案例ID' in df.columns:
            unique_cases = df['案例ID'].nunique()
            print(f"  {model}: {len(df)}行, {unique_cases}个案例")
        else:
            print(f"  {model}: {len(df)}行")

if __name__ == '__main__':
    merge_cases()
