#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
检查Excel文件中AI回答为空的情况
"""
import pandas as pd
import os
import sys

def check_empty_answers(file_path):
    """检查文件中AI回答为空的情况"""
    try:
        df = pd.read_excel(file_path)
        
        if 'AI回答' not in df.columns:
            print(f"错误: 文件中没有'AI回答'列")
            return
        
        print('='*80)
        print(f'检查文件: {file_path}')
        print('='*80)
        print(f'总行数: {len(df)}')
        
        # 检查空回答
        empty_mask = (
            df['AI回答'].isna() | 
            (df['AI回答'].astype(str).str.strip() == '') | 
            (df['AI回答'].astype(str).str.lower().isin(['nan', 'none', 'null', '']))
        )
        empty_count = empty_mask.sum()
        
        print(f'AI回答为空的行数: {empty_count}')
        print(f'AI回答有内容的行数: {len(df) - empty_count}')
        
        if empty_count > 0:
            print('\n' + '='*80)
            print('空回答详情:')
            print('='*80)
            
            empty_df = df[empty_mask].copy()
            
            # 按模型统计
            if '使用的模型' in df.columns:
                model_stats = empty_df['使用的模型'].value_counts()
                print('\n按模型统计空回答数量:')
                for model, count in model_stats.items():
                    print(f'  {model}: {count}个')
            
            # 显示详细信息
            print('\n空回答记录详情:')
            for idx, row in empty_df.iterrows():
                print(f'\n行 {idx+2} (Excel行号):')
                print(f'  案例ID: {row.get("案例ID", "N/A")}')
                print(f'  案例标题: {str(row.get("案例标题", "N/A"))[:50]}...')
                print(f'  问题编号: {row.get("问题编号", "N/A")}')
                print(f'  问题: {str(row.get("问题", "N/A"))[:60]}...')
                print(f'  使用的模型: {row.get("使用的模型", "N/A")}')
                
                # 检查是否有Thinking内容
                if 'AI回答Thinking' in df.columns:
                    thinking = row.get('AI回答Thinking', '')
                    if pd.notna(thinking) and str(thinking).strip() != '':
                        print(f'  AI回答Thinking: {str(thinking)[:100]}...')
                
                # 检查评估相关字段
                if '总分' in df.columns:
                    score = row.get('总分', '')
                    print(f'  总分: {score}')
                
                # 显示原始值
                ai_raw = row['AI回答']
                print(f'  AI回答原始值: {repr(ai_raw)}')
                print(f'  AI回答类型: {type(ai_raw)}')
        else:
            print('\n✓ 未发现AI回答为空的记录')
            
            # 检查是否有非常短的回答
            short_mask = df['AI回答'].astype(str).str.len() < 20
            short_count = short_mask.sum()
            if short_count > 0:
                print(f'\n⚠️  发现 {short_count} 个非常短的AI回答 (<20字符):')
                short_df = df[short_mask]
                for idx, row in short_df.head(5).iterrows():
                    print(f'  行 {idx+2}: {len(str(row["AI回答"]))}字符 - {str(row["AI回答"])[:50]}')
        
        # 统计信息
        print('\n' + '='*80)
        print('统计信息:')
        print('='*80)
        
        if '使用的模型' in df.columns:
            print('\n各模型的回答情况:')
            for model in df['使用的模型'].unique():
                model_df = df[df['使用的模型'] == model]
                model_empty = (
                    model_df['AI回答'].isna() | 
                    (model_df['AI回答'].astype(str).str.strip() == '')
                ).sum()
                model_total = len(model_df)
                print(f'  {model}: {model_total - model_empty}/{model_total} 有回答, {model_empty} 空回答')
        
    except Exception as e:
        print(f"错误: {e}")
        import traceback
        traceback.print_exc()

if __name__ == '__main__':
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
    else:
        # 默认检查当前打开的文件
        file_path = 'data/results_20260112_105927/5个案例_统一评估结果_108cases.xlsx'
    
    if not os.path.exists(file_path):
        print(f"错误: 文件不存在: {file_path}")
        sys.exit(1)
    
    check_empty_answers(file_path)
