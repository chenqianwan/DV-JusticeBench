"""
生成100个案例成本对比图表（PNG格式，英文标签）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import matplotlib.pyplot as plt
import matplotlib
matplotlib.rcParams['font.sans-serif'] = ['Arial', 'DejaVu Sans', 'Liberation Sans']
matplotlib.rcParams['axes.unicode_minus'] = False

import pandas as pd
from datetime import datetime

from utils.token_tracker import TokenTracker

# 获取实际token使用
tracker = TokenTracker()
total_stats = tracker.get_total_stats()

# 计算单案例平均值
cases_processed = 5
if total_stats['api_calls'] > 0:
    tokens_per_case = {
        'input': total_stats['input_tokens'] / cases_processed,
        'output': total_stats['output_tokens'] / cases_processed,
        'total': total_stats['total_tokens'] / cases_processed
    }
else:
    tokens_per_case = {
        'input': 45698,
        'output': 25251,
        'total': 70949
    }

# 推算100个案例
tokens_100_cases = {
    'input': tokens_per_case['input'] * 100,
    'output': tokens_per_case['output'] * 100,
    'total': tokens_per_case['total'] * 100
}

# API定价（每百万tokens，人民币）
PRICING = {
    'Gemini 2.0 Flash': {'input_cny': 0.72, 'output_cny': 2.88},
    'Qwen-Max': {'input_cny': 0.86, 'output_cny': 3.46},
    'DeepSeek-V3': {'input_cny': 2.0, 'output_cny': 8.0},
    'DeepSeek-R1': {'input_cny': 4.0, 'output_cny': 16.0},
    'Gemini 2.5 Pro': {'input_cny': 9.0, 'output_cny': 72.0},
    'ChatGPT GPT-4o': {'input_cny': 36.0, 'output_cny': 108.0},
    'ChatGPT GPT-4 Turbo': {'input_cny': 72.0, 'output_cny': 216.0}
}

def main():
    """主函数"""
    # 计算各API成本
    data = []
    for provider_name, pricing in PRICING.items():
        input_cost = (tokens_100_cases['input'] / 1_000_000) * pricing['input_cny']
        output_cost = (tokens_100_cases['output'] / 1_000_000) * pricing['output_cny']
        total_cost = input_cost + output_cost
        
        data.append({
            'Provider': provider_name,
            'Input Tokens': int(tokens_100_cases['input']),
            'Output Tokens': int(tokens_100_cases['output']),
            'Total Tokens': int(tokens_100_cases['total']),
            'Input Price (CNY/M)': pricing['input_cny'],
            'Output Price (CNY/M)': pricing['output_cny'],
            'Input Cost (CNY)': round(input_cost, 2),
            'Output Cost (CNY)': round(output_cost, 2),
            'Total Cost (CNY)': round(total_cost, 2)
        })
    
    df = pd.DataFrame(data)
    df = df.sort_values('Total Cost (CNY)')
    
    # 创建图表 - 使用更大的尺寸和更好的布局
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(24, 12))
    
    # 左图：总成本柱状图
    colors = ['#2ecc71', '#3498db', '#9b59b6', '#e74c3c', '#f39c12', '#1abc9c', '#34495e']
    bars = ax1.barh(df['Provider'], df['Total Cost (CNY)'], color=colors[:len(df)], height=0.6)
    ax1.set_xlabel('Total Cost (CNY)', fontsize=14, fontweight='bold')
    ax1.set_ylabel('API Provider', fontsize=14, fontweight='bold')
    ax1.set_title('100 Cases Cost Comparison\n(Total Cost)', fontsize=16, fontweight='bold', pad=20)
    ax1.grid(axis='x', alpha=0.3, linestyle='--', linewidth=1)
    ax1.set_xlim(0, max(df['Total Cost (CNY)']) * 1.15)
    
    # 添加数值标签
    for i, (bar, cost) in enumerate(zip(bars, df['Total Cost (CNY)'])):
        ax1.text(cost + max(df['Total Cost (CNY)']) * 0.02, i, 
                f'CNY {cost:.2f}', 
                va='center', fontsize=11, fontweight='bold', color='#2c3e50')
    
    # 右图：成本分解（输入+输出）
    x = range(len(df))
    width = 0.4
    
    bars1 = ax2.barh([i - width/2 for i in x], df['Input Cost (CNY)'], 
                     width, label='Input Cost', color='#3498db', alpha=0.85, edgecolor='white', linewidth=1.5)
    bars2 = ax2.barh([i + width/2 for i in x], df['Output Cost (CNY)'], 
                     width, label='Output Cost', color='#e74c3c', alpha=0.85, edgecolor='white', linewidth=1.5)
    
    ax2.set_yticks(x)
    ax2.set_yticklabels(df['Provider'], fontsize=12)
    ax2.set_xlabel('Cost (CNY)', fontsize=14, fontweight='bold')
    ax2.set_ylabel('API Provider', fontsize=14, fontweight='bold')
    ax2.set_title('100 Cases Cost Breakdown\n(Input vs Output)', fontsize=16, fontweight='bold', pad=20)
    ax2.legend(fontsize=12, loc='lower right', framealpha=0.9)
    ax2.grid(axis='x', alpha=0.3, linestyle='--', linewidth=1)
    ax2.set_xlim(0, max(df['Total Cost (CNY)']) * 1.15)
    
    # 添加数值标签
    for i, (in_cost, out_cost) in enumerate(zip(df['Input Cost (CNY)'], df['Output Cost (CNY)'])):
        if in_cost > 0:
            ax2.text(in_cost + max(df['Total Cost (CNY)']) * 0.02, i - width/2, 
                    f'CNY {in_cost:.1f}', va='center', fontsize=10, fontweight='bold', color='#2c3e50')
        if out_cost > 0:
            ax2.text(out_cost + max(df['Total Cost (CNY)']) * 0.02, i + width/2, 
                    f'CNY {out_cost:.1f}', va='center', fontsize=10, fontweight='bold', color='#2c3e50')
    
    # 添加总体信息
    fig.suptitle(f'100 Cases API Cost Comparison\n'
                 f'Token Usage: {int(tokens_100_cases["input"]):,} input + {int(tokens_100_cases["output"]):,} output = {int(tokens_100_cases["total"]):,} total tokens',
                 fontsize=18, fontweight='bold', y=0.98)
    
    plt.subplots_adjust(top=0.92, bottom=0.08, left=0.08, right=0.95, wspace=0.3)
    
    # 保存为PNG
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'data/100_Cases_Cost_Comparison_{timestamp}.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight', facecolor='white')
    print(f"✓ Chart saved to: {output_file}")
    
    # 同时保存详细数据表格为Excel
    excel_file = f'data/100_Cases_Cost_Table_{timestamp}.xlsx'
    with pd.ExcelWriter(excel_file, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='Cost Comparison', index=False)
        worksheet = writer.sheets['Cost Comparison']
        for idx, col in enumerate(df.columns):
            max_length = max(df[col].astype(str).map(len).max(), len(col)) + 2
            worksheet.column_dimensions[chr(65 + idx)].width = min(max_length, 25)
    
    print(f"✓ Excel table saved to: {excel_file}")
    print()
    
    # 打印表格
    print("=" * 100)
    print("100 Cases Cost Comparison Table")
    print("=" * 100)
    print()
    print(df.to_string(index=False))
    print()

if __name__ == '__main__':
    main()

