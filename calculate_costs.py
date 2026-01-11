"""
计算不同API提供商的成本
基于实际token使用情况
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from utils.token_tracker import TokenTracker

# API定价（每百万tokens，人民币）
PRICING = {
    'deepseek_v3': {
        'name': 'DeepSeek-V3',
        'input_cny': 2.0,      # ¥2 / 百万tokens
        'output_cny': 8.0,     # ¥8 / 百万tokens
    },
    'deepseek_r1': {
        'name': 'DeepSeek-R1',
        'input_cny': 4.0,      # ¥4 / 百万tokens
        'output_cny': 16.0,    # ¥16 / 百万tokens
    },
    'chatgpt_gpt4o': {
        'name': 'ChatGPT GPT-4o',
        'input_cny': 36.0,     # ¥36 / 百万tokens ($5 * 7.2)
        'output_cny': 108.0,   # ¥108 / 百万tokens ($15 * 7.2)
    },
    'chatgpt_gpt4_turbo': {
        'name': 'ChatGPT GPT-4 Turbo',
        'input_cny': 72.0,     # ¥72 / 百万tokens ($10 * 7.2)
        'output_cny': 216.0,   # ¥216 / 百万tokens ($30 * 7.2)
    },
    'gemini_25_pro': {
        'name': 'Gemini 2.5 Pro',
        'input_cny': 9.0,      # ¥9 / 百万tokens ($1.25 * 7.2)
        'output_cny': 72.0,    # ¥72 / 百万tokens ($10 * 7.2)
    },
    'gemini_20_flash': {
        'name': 'Gemini 2.0 Flash',
        'input_cny': 0.72,     # ¥0.72 / 百万tokens ($0.10 * 7.2)
        'output_cny': 2.88,    # ¥2.88 / 百万tokens ($0.40 * 7.2)
    },
    'qwen': {
        'name': '通义千问-Max',
        'input_cny': 0.86,     # ¥0.86 / 百万tokens ($0.12 * 7.2)
        'output_cny': 3.46,    # ¥3.46 / 百万tokens ($0.48 * 7.2)
    }
}

def main():
    """主函数"""
    tracker = TokenTracker()
    
    # 获取总统计
    total_stats = tracker.get_total_stats()
    
    if total_stats['api_calls'] == 0:
        print("=" * 60)
        print("还没有token使用记录")
        print("=" * 60)
        print()
        print("请先运行评估脚本（如 test_5_cases_with_new_rubric.py）")
        print("脚本运行时会自动记录token使用情况")
        return
    
    print("=" * 60)
    print("成本对比分析（基于实际Token使用）")
    print("=" * 60)
    print()
    
    print(f"实际Token使用统计:")
    print(f"  输入tokens: {total_stats['input_tokens']:,}")
    print(f"  输出tokens: {total_stats['output_tokens']:,}")
    if total_stats['reasoning_tokens'] > 0:
        print(f"  推理tokens: {total_stats['reasoning_tokens']:,}")
    print(f"  总计tokens: {total_stats['total_tokens']:,}")
    print(f"  API调用次数: {total_stats['api_calls']:,}")
    print()
    
    # 计算各API提供商的成本
    print("=" * 60)
    print("各API提供商成本对比（5个案例）")
    print("=" * 60)
    print()
    
    costs = []
    for provider_key, pricing in PRICING.items():
        cost_info = tracker.calculate_cost(pricing)
        costs.append({
            'provider': pricing['name'],
            'input_cost': cost_info['input_cost_cny'],
            'output_cost': cost_info['output_cost_cny'],
            'reasoning_cost': cost_info.get('reasoning_cost_cny', 0),
            'total_cost': cost_info['total_cost_cny']
        })
    
    # 按总成本排序
    costs.sort(key=lambda x: x['total_cost'])
    
    print(f"{'API提供商':<25} {'输入成本':<12} {'输出成本':<12} {'总成本':<12}")
    print("-" * 60)
    for cost in costs:
        reasoning_str = f" (+推理¥{cost['reasoning_cost']:.2f})" if cost['reasoning_cost'] > 0 else ""
        print(f"{cost['provider']:<25} ¥{cost['input_cost']:>8.2f}   ¥{cost['output_cost']:>8.2f}   ¥{cost['total_cost']:>8.2f}{reasoning_str}")
    
    print()
    print("=" * 60)
    print("重点：ChatGPT 和 Gemini 成本")
    print("=" * 60)
    print()
    
    # ChatGPT成本
    chatgpt_costs = [c for c in costs if 'ChatGPT' in c['provider']]
    for cost in chatgpt_costs:
        print(f"{cost['provider']}:")
        print(f"  输入成本: ¥{cost['input_cost']:.2f}")
        print(f"  输出成本: ¥{cost['output_cost']:.2f}")
        if cost['reasoning_cost'] > 0:
            print(f"  推理成本: ¥{cost['reasoning_cost']:.2f}")
        print(f"  总成本: ¥{cost['total_cost']:.2f}")
        print()
    
    # Gemini成本
    gemini_costs = [c for c in costs if 'Gemini' in c['provider']]
    for cost in gemini_costs:
        print(f"{cost['provider']}:")
        print(f"  输入成本: ¥{cost['input_cost']:.2f}")
        print(f"  输出成本: ¥{cost['output_cost']:.2f}")
        if cost['reasoning_cost'] > 0:
            print(f"  推理成本: ¥{cost['reasoning_cost']:.2f}")
        print(f"  总成本: ¥{cost['total_cost']:.2f}")
        print()
    
    # 与DeepSeek对比
    deepseek_cost = [c for c in costs if 'DeepSeek-V3' in c['provider']][0]
    print("=" * 60)
    print("与DeepSeek-V3对比")
    print("=" * 60)
    print()
    print(f"DeepSeek-V3成本: ¥{deepseek_cost['total_cost']:.2f}")
    print()
    for cost in chatgpt_costs + gemini_costs:
        ratio = cost['total_cost'] / deepseek_cost['total_cost'] if deepseek_cost['total_cost'] > 0 else 0
        print(f"{cost['provider']}: ¥{cost['total_cost']:.2f} ({ratio:.1f}倍)")
    print()

if __name__ == '__main__':
    main()

