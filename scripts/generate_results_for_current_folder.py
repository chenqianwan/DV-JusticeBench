#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
为当前文件夹生成报告和图表
适配当前文件夹的文件格式：20个案例_统一评估结果_108cases.xlsx（多sheet格式）
"""
import pandas as pd
import os
import sys
from datetime import datetime

# 添加项目根目录到路径
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 导入生成报告和图表的函数
from generate_detailed_results_report import generate_report
from generate_7models_charts import main as generate_charts_main

def adapt_data_for_scripts():
    """将当前文件夹的多sheet Excel文件转换为脚本期望的格式"""
    current_folder = 'data/results_20260112_unified_e8fd22b9'
    unified_file = os.path.join(current_folder, '20个案例_统一评估结果_108cases.xlsx')
    
    if not os.path.exists(unified_file):
        print(f"错误：找不到文件 {unified_file}")
        return False
    
    print("=" * 80)
    print("适配数据格式...")
    print("=" * 80)
    
    # 读取多sheet文件
    excel_file = pd.ExcelFile(unified_file)
    print(f"找到 {len(excel_file.sheet_names)} 个sheet: {excel_file.sheet_names}")
    
    # 模型名称映射（从sheet名称到文件名）
    model_name_mapping = {
        'GPT-4o': 'GPT4O_20个案例评估',
        'GPT-5': 'GPT5_20个案例评估',
        'DeepSeek': 'DEEPSEEK_20个案例评估',
        'DeepSeek-NoThinking': 'DEEPSEEK_20个案例评估',
        'DeepSeek-Thinking': 'DEEPSEEK_THINKING_20个案例评估',
        'Gemini 2.5 Flash': 'GEMINI_20个案例评估',
        'Claude Opus 4': 'CLAUDE_20个案例评估',
        'Qwen-Max': 'QWEN_20个案例评估'
    }
    
    # 为每个模型创建单独的文件（如果不存在）
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    created_files = []
    
    for sheet_name in excel_file.sheet_names:
        # 跳过GPT-5（对比时不采纳）
        if sheet_name == 'GPT-5':
            print(f"  跳过 {sheet_name}（对比时不采纳）")
            continue
            
        # 确定文件名（使用脚本期望的格式）
        if sheet_name == 'DeepSeek':
            # DeepSeek thinking模式
            file_name = f'DEEPSEEK_THINKING_20个案例评估_{timestamp}.xlsx'
        elif sheet_name == 'DeepSeek-NoThinking':
            # DeepSeek 非thinking模式
            file_name = f'DEEPSEEK_20个案例评估_{timestamp}.xlsx'
        elif sheet_name == 'GPT-4o':
            # GPT-4o使用特定格式以便脚本识别
            file_name = f'GPT4O_20个案例评估_{timestamp}.xlsx'
        else:
            # 其他模型
            base_name = model_name_mapping.get(sheet_name, sheet_name.upper().replace(' ', '_'))
            file_name = f'{base_name}_{timestamp}.xlsx'
        
        output_file = os.path.join(current_folder, file_name)
        
        # 读取数据并保存
        df = pd.read_excel(unified_file, sheet_name=sheet_name)
        df.to_excel(output_file, index=False, sheet_name='Sheet1')
        created_files.append((sheet_name, file_name))
        print(f"  ✓ {sheet_name} -> {file_name} ({len(df)}行)")
    
    print()
    print(f"✓ 已创建 {len(created_files)} 个模型文件")
    return True

def main():
    """主函数"""
    print("=" * 80)
    print("为当前文件夹生成报告和图表")
    print("=" * 80)
    print()
    
    # 步骤1：适配数据格式
    if not adapt_data_for_scripts():
        return
    
    print()
    print("=" * 80)
    print("生成详细报告...")
    print("=" * 80)
    
    # 步骤2：生成报告
    try:
        generate_report()
    except Exception as e:
        print(f"生成报告时出错: {e}")
        import traceback
        traceback.print_exc()
    
    print()
    print("=" * 80)
    print("生成图表...")
    print("=" * 80)
    
    # 步骤3：生成图表
    try:
        generate_charts_main()
    except Exception as e:
        print(f"生成图表时出错: {e}")
        import traceback
        traceback.print_exc()
    
    print()
    print("=" * 80)
    print("✓ 完成！")
    print("=" * 80)

if __name__ == '__main__':
    main()
