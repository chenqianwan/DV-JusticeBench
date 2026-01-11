"""
重新计算100个案例的处理成本（使用正确的价格数据）
包括DeepSeek、Qwen、ChatGPT、Gemini、Grok等API
"""
import json
from datetime import datetime

# 汇率
EXCHANGE_RATE = 7.2  # 1 USD = 7.2 CNY

# API定价（每百万tokens）
# 基于实际市场价格和用户提供的信息
PRICING = {
    'deepseek_v3': {
        'name': 'DeepSeek-V3',
        'input_cny': 2.0,      # 2元 / 百万tokens（基于用户提供的图片）
        'output_cny': 8.0,     # 8元 / 百万tokens
        'input_usd': 2.0 / EXCHANGE_RATE,  # 约$0.28
        'output_usd': 8.0 / EXCHANGE_RATE,  # 约$1.11
        'supports_thinking': True,
        'note': '标准模型，性价比最高，新项目推荐'
    },
    'deepseek_r1': {
        'name': 'DeepSeek-R1',
        'input_cny': 4.0,      # 4元 / 百万tokens（基于用户提供的图片）
        'output_cny': 16.0,   # 16元 / 百万tokens
        'input_usd': 4.0 / EXCHANGE_RATE,  # 约$0.56
        'output_usd': 16.0 / EXCHANGE_RATE,  # 约$2.22
        'supports_thinking': False,
        'note': '推理模型，实时交互，价格是V3的2倍'
    },
    'qwen': {
        'name': '通义千问-Max',
        'input_usd': 0.12,     # $0.12 / 百万tokens
        'output_usd': 0.48,   # $0.48 / 百万tokens
        'input_cny': 0.12 * EXCHANGE_RATE,  # 约¥0.86
        'output_cny': 0.48 * EXCHANGE_RATE,  # 约¥3.46
        'supports_thinking': False,
        'note': '阿里云，国内访问稳定'
    },
    'chatgpt_gpt4': {
        'name': 'ChatGPT GPT-4',
        'input_usd': 30.0,    # $30 / 百万tokens（基于用户搜索的价格）
        'output_usd': 60.0,  # $60 / 百万tokens
        'input_cny': 30.0 * EXCHANGE_RATE,  # ¥216
        'output_cny': 60.0 * EXCHANGE_RATE,  # ¥432
        'supports_thinking': False,
        'note': '标准GPT-4模型'
    },
    'chatgpt_gpt4o': {
        'name': 'ChatGPT GPT-4o',
        'input_usd': 5.0,     # $5 / 百万tokens（最新价格）
        'output_usd': 15.0,  # $15 / 百万tokens
        'input_cny': 5.0 * EXCHANGE_RATE,  # ¥36
        'output_cny': 15.0 * EXCHANGE_RATE,  # ¥108
        'supports_thinking': False,
        'note': 'GPT-4o，价格已降低'
    },
    'chatgpt_gpt4_turbo': {
        'name': 'ChatGPT GPT-4 Turbo',
        'input_usd': 10.0,    # $10 / 百万tokens
        'output_usd': 30.0,  # $30 / 百万tokens
        'input_cny': 10.0 * EXCHANGE_RATE,  # ¥72
        'output_cny': 30.0 * EXCHANGE_RATE,  # ¥216
        'supports_thinking': False,
        'note': 'GPT-4 Turbo版本'
    },
    'gemini_25_pro': {
        'name': 'Gemini 2.5 Pro',
        'input_usd': 1.25,    # $1.25 / 百万tokens（基础）
        'output_usd': 10.0,  # $10 / 百万tokens（基础）
        'input_cny': 1.25 * EXCHANGE_RATE,  # ¥9
        'output_cny': 10.0 * EXCHANGE_RATE,  # ¥72
        'supports_thinking': False,
        'note': '多模态，超长上下文（100万tokens）'
    },
    'gemini_20_flash': {
        'name': 'Gemini 2.0 Flash',
        'input_usd': 0.10,   # $0.10 / 百万tokens
        'output_usd': 0.40,  # $0.40 / 百万tokens
        'input_cny': 0.10 * EXCHANGE_RATE,  # ¥0.72
        'output_cny': 0.40 * EXCHANGE_RATE,  # ¥2.88
        'supports_thinking': False,
        'note': '轻量级，价格最低'
    },
    'grok': {
        'name': 'Grok 4 Fast',
        'input_usd': 0.20,   # $0.20 / 百万tokens
        'output_usd': 0.50,  # $0.50 / 百万tokens
        'input_cny': 0.20 * EXCHANGE_RATE,  # ¥1.44
        'output_cny': 0.50 * EXCHANGE_RATE,  # ¥3.60
        'supports_thinking': False,
        'note': '实时信息，集成X平台'
    }
}

# Token消耗估算（单个案例）
# 基于实际处理的5个案例数据
TOKEN_USAGE = {
    'masking': {
        'input': 300,
        'output': 15
    },
    'question_gen': {
        'input': 5000,
        'output': 300
    },
    'ai_answer': {
        'input_per_call': 6000,
        'output_per_call_normal': 3000,
        'output_per_call_thinking': 4500
    },
    'evaluation': {
        'input_per_call': 9000,
        'output_per_call_normal': 2500,
        'output_per_call_thinking': 4000
    }
}

def calculate_single_case_tokens():
    """计算单个案例的token消耗"""
    total_input = (
        TOKEN_USAGE['masking']['input'] +
        TOKEN_USAGE['question_gen']['input'] +
        TOKEN_USAGE['ai_answer']['input_per_call'] * 5 +
        TOKEN_USAGE['evaluation']['input_per_call'] * 5
    )
    
    total_output_normal = (
        TOKEN_USAGE['masking']['output'] +
        TOKEN_USAGE['question_gen']['output'] +
        TOKEN_USAGE['ai_answer']['output_per_call_normal'] * 5 +
        TOKEN_USAGE['evaluation']['output_per_call_normal'] * 5
    )
    
    total_output_thinking = (
        TOKEN_USAGE['masking']['output'] +
        TOKEN_USAGE['question_gen']['output'] +
        TOKEN_USAGE['ai_answer']['output_per_call_thinking'] * 5 +
        TOKEN_USAGE['evaluation']['output_per_call_thinking'] * 5
    )
    
    return {
        'input': total_input,
        'output_normal': total_output_normal,
        'output_thinking': total_output_thinking
    }

def calculate_cost(provider_key, tokens, use_thinking=False):
    """计算成本"""
    pricing = PRICING[provider_key]
    
    # 优先使用CNY价格（如果可用）
    if 'input_cny' in pricing:
        input_cost_cny = (tokens['input'] / 1_000_000) * pricing['input_cny']
        if use_thinking and pricing['supports_thinking']:
            output_cost_cny = (tokens['output_thinking'] / 1_000_000) * pricing['output_cny']
        else:
            output_cost_cny = (tokens['output_normal'] / 1_000_000) * pricing['output_cny']
        total_cost_cny = input_cost_cny + output_cost_cny
        total_cost_usd = total_cost_cny / EXCHANGE_RATE
    else:
        input_cost_usd = (tokens['input'] / 1_000_000) * pricing['input_usd']
        if use_thinking and pricing['supports_thinking']:
            output_cost_usd = (tokens['output_thinking'] / 1_000_000) * pricing['output_usd']
        else:
            output_cost_usd = (tokens['output_normal'] / 1_000_000) * pricing['output_usd']
        total_cost_usd = input_cost_usd + output_cost_usd
        total_cost_cny = total_cost_usd * EXCHANGE_RATE
    
    return {
        'input_cost_usd': input_cost_usd if 'input_cost_usd' in locals() else total_cost_usd * (tokens['input'] / (tokens['input'] + (tokens['output_thinking'] if use_thinking else tokens['output_normal']))),
        'output_cost_usd': output_cost_usd if 'output_cost_usd' in locals() else total_cost_usd * ((tokens['output_thinking'] if use_thinking else tokens['output_normal']) / (tokens['input'] + (tokens['output_thinking'] if use_thinking else tokens['output_normal']))),
        'total_cost_usd': total_cost_usd,
        'total_cost_cny': total_cost_cny
    }

def main():
    print('=' * 80)
    print('100个案例处理成本估算（使用正确的价格数据）')
    print('=' * 80)
    print()
    
    # 计算单个案例的token消耗
    tokens_per_case = calculate_single_case_tokens()
    
    print(f"单个案例Token消耗：")
    print(f"  输入: {tokens_per_case['input']:,} tokens")
    print(f"  输出（普通模式）: {tokens_per_case['output_normal']:,} tokens")
    print(f"  输出（思考模式）: {tokens_per_case['output_thinking']:,} tokens")
    print()
    
    # 计算100个案例的总token消耗
    total_tokens = {
        'input': tokens_per_case['input'] * 100,
        'output_normal': tokens_per_case['output_normal'] * 100,
        'output_thinking': tokens_per_case['output_thinking'] * 100
    }
    
    print(f"100个案例总Token消耗：")
    print(f"  输入: {total_tokens['input']:,} tokens")
    print(f"  输出（普通模式）: {total_tokens['output_normal']:,} tokens")
    print(f"  输出（思考模式）: {total_tokens['output_thinking']:,} tokens")
    print()
    print('=' * 80)
    print()
    
    # 计算各模型成本
    results = []
    
    providers = [
        'deepseek_v3', 'deepseek_r1', 'qwen', 
        'chatgpt_gpt4', 'chatgpt_gpt4o', 'chatgpt_gpt4_turbo',
        'gemini_25_pro', 'gemini_20_flash', 'grok'
    ]
    
    for provider_key in providers:
        pricing = PRICING[provider_key]
        print(f"{pricing['name']}")
        print('-' * 80)
        
        # 普通模式
        cost_normal = calculate_cost(provider_key, tokens_per_case, use_thinking=False)
        cost_100_normal = {
            'total_cost_usd': cost_normal['total_cost_usd'] * 100,
            'total_cost_cny': cost_normal['total_cost_cny'] * 100
        }
        
        print(f"普通模式（单个案例）：")
        print(f"  成本: ${cost_normal['total_cost_usd']:.4f} (¥{cost_normal['total_cost_cny']:.2f})")
        print(f"100个案例成本: ${cost_100_normal['total_cost_usd']:.2f} (¥{cost_100_normal['total_cost_cny']:.2f})")
        
        # 思考模式（如果支持）
        if pricing['supports_thinking']:
            cost_thinking = calculate_cost(provider_key, tokens_per_case, use_thinking=True)
            cost_100_thinking = {
                'total_cost_usd': cost_thinking['total_cost_usd'] * 100,
                'total_cost_cny': cost_thinking['total_cost_cny'] * 100
            }
            print(f"思考模式（单个案例）：")
            print(f"  成本: ${cost_thinking['total_cost_usd']:.4f} (¥{cost_thinking['total_cost_cny']:.2f})")
            print(f"100个案例成本: ${cost_100_thinking['total_cost_usd']:.2f} (¥{cost_100_thinking['total_cost_cny']:.2f})")
            results.append({
                '模型': pricing['name'],
                '模式': '思考模式',
                '成本(USD)': cost_100_thinking['total_cost_usd'],
                '成本(CNY)': cost_100_thinking['total_cost_cny'],
                '说明': pricing['note']
            })
        
        results.append({
            '模型': pricing['name'],
            '模式': '普通模式',
            '成本(USD)': cost_100_normal['total_cost_usd'],
            '成本(CNY)': cost_100_normal['total_cost_cny'],
            '说明': pricing['note']
        })
        
        print()
    
    # 生成对比表
    print('=' * 80)
    print('成本对比表（100个案例，按价格从低到高排序）')
    print('=' * 80)
    print()
    
    results_sorted = sorted(results, key=lambda x: x['成本(CNY)'])
    
    print(f"{'排名':<6} {'模型':<25} {'模式':<12} {'成本(CNY)':<15} {'成本(USD)':<15} {'说明'}")
    print('-' * 120)
    
    for i, result in enumerate(results_sorted, 1):
        print(f"{i:<6} {result['模型']:<25} {result['模式']:<12} "
              f"¥{result['成本(CNY)']:>10.2f}    ${result['成本(USD)']:>10.2f}    {result['说明']}")
    
    print()
    print('=' * 80)
    print('✓ 计算完成！')
    print('=' * 80)
    print()
    print('注意：')
    print('1. DeepSeek-V3和DeepSeek-R1使用人民币定价（基于您提供的图片）')
    print('2. 其他模型使用美元定价，按汇率7.2换算为人民币')
    print('3. 价格可能因时间、地区、使用量等因素有所变化')
    print('4. 建议查看各API提供商官网获取最新价格')

if __name__ == '__main__':
    main()


