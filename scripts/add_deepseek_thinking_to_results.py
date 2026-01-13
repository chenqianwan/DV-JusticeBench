#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
从108个案例的Excel文件中提取DeepSeek thinking模式的数据（最后20个案例）
并加入到results文件夹中
"""
import pandas as pd
import os
import shutil
from datetime import datetime

def extract_last_20_cases():
    """从108个案例的Excel中提取最后20个案例的数据"""
    file_path = 'data/108个案例_新标准评估_完整版_最终版.xlsx'
    
    if not os.path.exists(file_path):
        print(f"Error: File not found: {file_path}")
        return None
    
    print(f"Reading file: {file_path}")
    
    # 使用openpyxl直接读取，避免pandas的segmentation fault
    try:
        # 先尝试读取少量数据查看结构
        df_sample = pd.read_excel(file_path, nrows=10, engine='openpyxl')
        print(f"Sample columns: {list(df_sample.columns)[:15]}")
        
        # 读取完整数据
        print("Reading full file...")
        df = pd.read_excel(file_path, engine='openpyxl')
        print(f"Total rows: {len(df)}")
        
        # 检查案例ID列
        if '案例ID' not in df.columns:
            print("Error: '案例ID' column not found")
            print(f"Available columns: {list(df.columns)}")
            return None
        
        # 获取最后20个唯一的案例ID
        unique_case_ids = df['案例ID'].unique()
        print(f"Total unique case IDs: {len(unique_case_ids)}")
        
        last_20_case_ids = unique_case_ids[-20:]
        print(f"Last 20 case IDs: {last_20_case_ids[:5]}... (showing first 5)")
        
        # 提取最后20个案例的数据
        df_last_20 = df[df['案例ID'].isin(last_20_case_ids)].copy()
        print(f"Extracted rows: {len(df_last_20)}")
        print(f"Expected: 20 cases × 5 questions = 100 rows")
        
        # 检查并补充缺失的列
        required_cols = {
            '案例ID': '案例ID',
            '案例标题': '案例标题',
            '问题编号': '问题编号',
            '问题': '问题',
            'AI回答': 'AI回答',
            'AI回答Thinking': 'AI回答Thinking',  # thinking模式应该有这个
            '使用的模型': '使用的模型',
            '总分': '总分',
            '百分制': '百分制',
            '详细评价': '详细评价',
            '评价Thinking': '评价Thinking',  # thinking模式应该有这个
            '重大错误': '重大错误',
            '明显错误': '明显错误',
            '微小错误': '微小错误'
        }
        
        # 检查缺失的列并补充
        for col_name, default_value in required_cols.items():
            if col_name not in df_last_20.columns:
                print(f"  Adding missing column: {col_name}")
                if col_name == '使用的模型':
                    df_last_20[col_name] = 'DeepSeek-Reasoner (Thinking)'
                elif col_name in ['AI回答Thinking', '评价Thinking']:
                    df_last_20[col_name] = ''  # 如果原数据没有，设为空
                elif col_name in ['重大错误', '明显错误', '微小错误']:
                    df_last_20[col_name] = None
                else:
                    df_last_20[col_name] = ''
        
        # 确保使用的模型列正确
        if '使用的模型' in df_last_20.columns:
            df_last_20['使用的模型'] = 'DeepSeek-Reasoner (Thinking)'
        
        # 添加评估维度得分列（如果缺失）
        dimension_cols = [
            '规范依据相关性_得分',
            '涵摄链条对齐度_得分',
            '价值衡量与同理心对齐度_得分',
            '关键事实与争点覆盖度_得分',
            '裁判结论与救济配置一致性_得分'
        ]
        
        for col in dimension_cols:
            if col not in df_last_20.columns:
                print(f"  Adding missing dimension column: {col}")
                df_last_20[col] = 0.0
        
        print(f"\n✓ Data extraction complete")
        print(f"  Rows: {len(df_last_20)}")
        print(f"  Columns: {len(df_last_20.columns)}")
        
        return df_last_20
        
    except Exception as e:
        print(f"Error reading file: {e}")
        import traceback
        traceback.print_exc()
        return None

def save_to_results_folder(df):
    """保存到results文件夹"""
    # 找到最新的results文件夹
    results_dirs = [d for d in os.listdir('data') if d.startswith('results_') and os.path.isdir(os.path.join('data', d))]
    if not results_dirs:
        print("Error: No results folder found")
        return None
    
    latest_dir = sorted(results_dirs)[-1]
    results_path = os.path.join('data', latest_dir)
    
    # 生成文件名
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(results_path, f'DEEPSEEK_THINKING_20个案例评估_{timestamp}.xlsx')
    
    # 保存Excel文件
    print(f"\nSaving to: {output_file}")
    df.to_excel(output_file, index=False, engine='openpyxl')
    print(f"✓ Saved successfully")
    
    return output_file

def main():
    print("=" * 80)
    print("Extracting DeepSeek Thinking Mode Data from 108 Cases Excel")
    print("=" * 80)
    print()
    
    # 提取数据
    df = extract_last_20_cases()
    
    if df is None:
        print("\n✗ Failed to extract data")
        return
    
    # 保存到results文件夹
    output_file = save_to_results_folder(df)
    
    if output_file:
        print(f"\n✓ Success! File saved to: {output_file}")
        print(f"\nNext steps:")
        print(f"  1. Update generate_detailed_results_report.py to include DeepSeek-Thinking")
        print(f"  2. Update generate_7models_charts.py to include DeepSeek-Thinking")
        print(f"  3. Regenerate reports and charts")
    else:
        print("\n✗ Failed to save file")

if __name__ == '__main__':
    main()
