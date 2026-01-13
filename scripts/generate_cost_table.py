"""
生成100个案例成本对比表（包含Token数和价格）
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from utils.token_tracker import TokenTracker
import pandas as pd
from datetime import datetime

# 获取实际token使用
tracker = TokenTracker()
total_stats = tracker.get_total_stats()

# 计算单案例平均值（基于实际处理的案例数）
cases_processed = 5  # 实际处理的案例数
if total_stats['api_calls'] > 0:
    tokens_per_case = {
        'input': total_stats['input_tokens'] / cases_processed,
        'output': total_stats['output_tokens'] / cases_processed,
        'total': total_stats['total_tokens'] / cases_processed
    }
else:
    # 如果没有数据，使用估算值
    tokens_per_case = {
        'input': 25088,
        'output': 17938,
        'total': 43026
    }

# 推算100个案例
tokens_100_cases = {
    'input': tokens_per_case['input'] * 100,
    'output': tokens_per_case['output'] * 100,
    'total': tokens_per_case['total'] * 100
}

# API定价（每百万tokens，人民币）
PRICING = {
    'DeepSeek-V3': {'input_cny': 2.0, 'output_cny': 8.0},
    'DeepSeek-R1': {'input_cny': 4.0, 'output_cny': 16.0},
    'ChatGPT GPT-4o': {'input_cny': 36.0, 'output_cny': 108.0},
    'ChatGPT GPT-4 Turbo': {'input_cny': 72.0, 'output_cny': 216.0},
    'Gemini 2.5 Pro': {'input_cny': 9.0, 'output_cny': 72.0},
    'Gemini 2.0 Flash': {'input_cny': 0.72, 'output_cny': 2.88},
    '通义千问-Max': {'input_cny': 0.86, 'output_cny': 3.46}
}

def main():
    """主函数"""
    print("=" * 120)
    print("100个案例成本对比表（基于实际Token使用数据）")
    print("=" * 120)
    print()
    
    print(f"实际Token使用统计（{cases_processed}个案例）:")
    print(f"  输入tokens: {total_stats['input_tokens']:,}")
    print(f"  输出tokens: {total_stats['output_tokens']:,}")
    print(f"  总计tokens: {total_stats['total_tokens']:,}")
    print()
    
    print(f"单案例平均Token使用:")
    print(f"  输入tokens: {tokens_per_case['input']:,.0f}")
    print(f"  输出tokens: {tokens_per_case['output']:,.0f}")
    print(f"  总计tokens: {tokens_per_case['total']:,.0f}")
    print()
    
    print(f"100个案例预计Token使用:")
    print(f"  输入tokens: {tokens_100_cases['input']:,.0f}")
    print(f"  输出tokens: {tokens_100_cases['output']:,.0f}")
    print(f"  总计tokens: {tokens_100_cases['total']:,.0f}")
    print()
    
    # 计算各API成本
    table_data = []
    for provider_name, pricing in PRICING.items():
        input_cost = (tokens_100_cases['input'] / 1_000_000) * pricing['input_cny']
        output_cost = (tokens_100_cases['output'] / 1_000_000) * pricing['output_cny']
        total_cost = input_cost + output_cost
        
        table_data.append({
            'API提供商': provider_name,
            '输入Token数': int(tokens_100_cases['input']),
            '输出Token数': int(tokens_100_cases['output']),
            '总Token数': int(tokens_100_cases['total']),
            '输入价格(¥/百万)': pricing['input_cny'],
            '输出价格(¥/百万)': pricing['output_cny'],
            '输入成本(¥)': round(input_cost, 2),
            '输出成本(¥)': round(output_cost, 2),
            '总成本(¥)': round(total_cost, 2)
        })
    
    # 按总成本排序
    table_data.sort(key=lambda x: x['总成本(¥)'])
    
    # 创建DataFrame
    df = pd.DataFrame(table_data)
    
    # 打印表格
    print("=" * 120)
    print("100个案例成本对比表")
    print("=" * 120)
    print()
    
    # 格式化打印
    pd.set_option('display.max_columns', None)
    pd.set_option('display.width', None)
    pd.set_option('display.max_colwidth', None)
    
    print(df.to_string(index=False))
    print()
    
    # 保存到Excel
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'data/100个案例成本对比表_{timestamp}.xlsx'
    
    # 格式化Excel输出
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='成本对比', index=False)
        
        # 获取工作表并调整列宽
        worksheet = writer.sheets['成本对比']
        for idx, col in enumerate(df.columns):
            max_length = max(
                df[col].astype(str).map(len).max(),
                len(col)
            ) + 2
            worksheet.column_dimensions[chr(65 + idx)].width = min(max_length, 20)
    
    print(f"✓ 表格已保存到: {output_file}")
    print()
    
    # 重点显示ChatGPT和Gemini
    print("=" * 120)
    print("重点：ChatGPT 和 Gemini 成本详情")
    print("=" * 120)
    print()
    
    chatgpt_df = df[df['API提供商'].str.contains('ChatGPT')]
    gemini_df = df[df['API提供商'].str.contains('Gemini')]
    
    if not chatgpt_df.empty:
        print("ChatGPT:")
        print(chatgpt_df.to_string(index=False))
        print()
    
    if not gemini_df.empty:
        print("Gemini:")
        print(gemini_df.to_string(index=False))
        print()

if __name__ == '__main__':
    main()

