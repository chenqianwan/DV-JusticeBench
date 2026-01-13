"""
估算100个案例的处理成本
包括DeepSeek、Qwen、ChatGPT三种API
区分普通模式和思考模式
"""
import json
from datetime import datetime

# API定价（每百万tokens，USD）
PRICING = {
    'deepseek': {
        'input': 0.14,      # $0.14 / 百万tokens
        'output': 0.56,     # $0.56 / 百万tokens
        'thinking_input': 0.14,   # 思考模式输入价格相同
        'thinking_output': 0.56,  # 思考模式输出价格相同（但token消耗更多）
        'model_normal': 'deepseek-chat',
        'model_thinking': 'deepseek-reasoner',
        'supports_thinking': True
    },
    'qwen': {
        'input': 0.12,      # $0.12 / 百万tokens (通义千问-Max)
        'output': 0.48,     # $0.48 / 百万tokens
        'thinking_input': 0.12,   # Qwen可能不支持思考模式，使用普通价格
        'thinking_output': 0.48,
        'model_normal': 'qwen-turbo',
        'model_thinking': 'qwen-turbo',  # Qwen可能不支持思考模式
        'supports_thinking': False
    },
    'chatgpt': {
        'input': 10.00,     # $10.00 / 百万tokens (GPT-4 Turbo)
        'output': 30.00,    # $30.00 / 百万tokens
        'thinking_input': 10.00,  # ChatGPT可能不支持思考模式，使用普通价格
        'thinking_output': 30.00,
        'model_normal': 'gpt-4o',
        'model_thinking': 'gpt-4o',  # ChatGPT可能不支持思考模式
        'supports_thinking': False
    },
    'gemini': {
        'input': 1.25,      # $1.25 / 百万tokens (Gemini 1.5 Pro标准)
        'output': 5.00,     # $5.00 / 百万tokens
        'thinking_input': 1.25,   # Gemini可能不支持思考模式，使用普通价格
        'thinking_output': 5.00,
        'model_normal': 'gemini-1.5-pro',
        'model_thinking': 'gemini-1.5-pro',  # Gemini可能不支持思考模式
        'supports_thinking': False
    },
    'grok': {
        'input': 0.20,      # $0.20 / 百万tokens (Grok 4 Fast)
        'output': 0.50,     # $0.50 / 百万tokens
        'thinking_input': 0.20,   # Grok可能不支持思考模式，使用普通价格
        'thinking_output': 0.50,
        'model_normal': 'grok-beta',
        'model_thinking': 'grok-beta',  # Grok可能不支持思考模式
        'supports_thinking': False
    }
}

# 汇率
EXCHANGE_RATE = 7.2  # 1 USD = 7.2 CNY

# 基于实际处理的5个案例的token消耗估算
# 每个案例的处理流程：
# 1. 脱敏处理（1次调用，普通模式）
# 2. 生成5个问题（1次调用，普通模式）
# 3. 生成5个AI回答（5次调用，思考模式）
# 4. 评估5个回答（5次调用，思考模式）

# 单次API调用的token消耗估算（基于实际运行的5个案例数据）
# 从实际运行输出中观察到的token消耗范围：
# - 脱敏：输入200-500，输出10-20
# - 生成问题：输入3000-8000，输出150-500
# - 生成AI回答：输入2000-11000，输出1500-4500（普通），2000-6000（思考模式）
# - 评估回答：输入5000-13000，输出1500-4000（普通），2000-6000（思考模式）
TOKEN_ESTIMATES = {
    'masking': {
        'input': 300,      # 脱敏处理输入（案例标题+内容+判决，平均）
        'output': 15,      # 脱敏处理输出（脱敏后的文本）
        'thinking': False
    },
    'generate_questions': {
        'input': 5000,     # 生成问题输入（案例内容，平均）
        'output': 300,     # 生成问题输出（5个问题）
        'thinking': False
    },
    'generate_answer': {
        'input': 6000,     # 生成AI回答输入（案例内容+问题，平均）
        'output': 3000,    # 生成AI回答输出（普通模式，平均）
        'output_thinking': 4500,  # 思考模式输出（包含thinking内容，平均）
        'thinking': True
    },
    'evaluate_answer': {
        'input': 9000,     # 评估输入（AI回答+法官判决+问题+评分标准，平均）
        'output': 2500,    # 评估输出（普通模式，平均）
        'output_thinking': 4000,  # 思考模式输出（包含thinking内容，平均）
        'thinking': True
    }
}


def calculate_case_cost(provider: str, use_thinking: bool = True):
    """
    计算单个案例的成本
    
    Args:
        provider: API提供商 ('deepseek', 'qwen', 'chatgpt')
        use_thinking: 是否使用思考模式
    
    Returns:
        成本字典（USD和CNY）
    """
    pricing = PRICING[provider]
    
    # 1. 脱敏处理（普通模式）
    masking_input = TOKEN_ESTIMATES['masking']['input']
    masking_output = TOKEN_ESTIMATES['masking']['output']
    masking_cost = (
        (masking_input / 1_000_000) * pricing['input'] +
        (masking_output / 1_000_000) * pricing['output']
    )
    
    # 2. 生成问题（普通模式）
    gen_q_input = TOKEN_ESTIMATES['generate_questions']['input']
    gen_q_output = TOKEN_ESTIMATES['generate_questions']['output']
    gen_q_cost = (
        (gen_q_input / 1_000_000) * pricing['input'] +
        (gen_q_output / 1_000_000) * pricing['output']
    )
    
    # 3. 生成5个AI回答
    if use_thinking and pricing['supports_thinking']:
        answer_output = TOKEN_ESTIMATES['generate_answer']['output_thinking']
        answer_pricing_input = pricing['thinking_input']
        answer_pricing_output = pricing['thinking_output']
    else:
        answer_output = TOKEN_ESTIMATES['generate_answer']['output']
        answer_pricing_input = pricing['input']
        answer_pricing_output = pricing['output']
    
    answer_input = TOKEN_ESTIMATES['generate_answer']['input']
    answer_cost_per = (
        (answer_input / 1_000_000) * answer_pricing_input +
        (answer_output / 1_000_000) * answer_pricing_output
    )
    answer_cost_total = answer_cost_per * 5  # 5个问题
    
    # 4. 评估5个回答
    if use_thinking and pricing['supports_thinking']:
        eval_output = TOKEN_ESTIMATES['evaluate_answer']['output_thinking']
        eval_pricing_input = pricing['thinking_input']
        eval_pricing_output = pricing['thinking_output']
    else:
        eval_output = TOKEN_ESTIMATES['evaluate_answer']['output']
        eval_pricing_input = pricing['input']
        eval_pricing_output = pricing['output']
    
    eval_input = TOKEN_ESTIMATES['evaluate_answer']['input']
    eval_cost_per = (
        (eval_input / 1_000_000) * eval_pricing_input +
        (eval_output / 1_000_000) * eval_pricing_output
    )
    eval_cost_total = eval_cost_per * 5  # 5个评估
    
    # 总成本
    total_cost_usd = masking_cost + gen_q_cost + answer_cost_total + eval_cost_total
    
    # 计算token统计
    total_input = masking_input + gen_q_input + (answer_input * 5) + (eval_input * 5)
    total_output = masking_output + gen_q_output + (answer_output * 5) + (eval_output * 5)
    
    return {
        'masking_cost_usd': masking_cost,
        'gen_q_cost_usd': gen_q_cost,
        'answer_cost_usd': answer_cost_total,
        'eval_cost_usd': eval_cost_total,
        'total_cost_usd': total_cost_usd,
        'total_cost_cny': total_cost_usd * EXCHANGE_RATE,
        'total_input_tokens': total_input,
        'total_output_tokens': total_output,
        'total_tokens': total_input + total_output
    }


def main():
    """主函数"""
    print('=' * 80)
    print('100个案例处理成本估算')
    print('=' * 80)
    print()
    
    providers = ['deepseek', 'qwen', 'chatgpt', 'gemini', 'grok']
    modes = [
        ('普通模式', False),
        ('思考模式', True)
    ]
    
    results = {}
    
    for provider in providers:
        provider_name = {
            'deepseek': 'DeepSeek',
            'qwen': '通义千问 (Qwen)',
            'chatgpt': 'ChatGPT (GPT-4o)',
            'gemini': 'Gemini 1.5 Pro',
            'grok': 'Grok 4 Fast'
        }[provider]
        
        print(f"\n{'=' * 80}")
        print(f"{provider_name}")
        print(f"{'=' * 80}")
        print()
        
        results[provider] = {}
        
        for mode_name, use_thinking in modes:
            # 检查是否支持思考模式
            if use_thinking and not PRICING[provider]['supports_thinking']:
                print(f"⚠️  {mode_name}: {provider_name} 不支持思考模式，使用普通模式价格")
                use_thinking = False
            
            # 计算单个案例成本
            case_cost = calculate_case_cost(provider, use_thinking)
            
            # 计算100个案例成本
            cost_100_cases_usd = case_cost['total_cost_usd'] * 100
            cost_100_cases_cny = case_cost['total_cost_cny'] * 100
            
            results[provider][mode_name] = {
                'single_case': case_cost,
                '100_cases_usd': cost_100_cases_usd,
                '100_cases_cny': cost_100_cases_cny
            }
            
            print(f"{mode_name}:")
            print(f"  单个案例成本: ${case_cost['total_cost_usd']:.4f} (¥{case_cost['total_cost_cny']:.2f})")
            print(f"  100个案例成本: ${cost_100_cases_usd:.2f} (¥{cost_100_cases_cny:.2f})")
            print(f"  Token消耗（单个案例）:")
            print(f"    输入: {case_cost['total_input_tokens']:,} tokens")
            print(f"    输出: {case_cost['total_output_tokens']:,} tokens")
            print(f"    总计: {case_cost['total_tokens']:,} tokens")
            print()
            
            # 详细分解
            print(f"  成本分解（单个案例）:")
            print(f"    脱敏处理: ${case_cost['masking_cost_usd']:.4f}")
            print(f"    生成问题: ${case_cost['gen_q_cost_usd']:.4f}")
            print(f"    生成AI回答（5次）: ${case_cost['answer_cost_usd']:.4f}")
            print(f"    评估回答（5次）: ${case_cost['eval_cost_usd']:.4f}")
            print()
    
    # 生成对比表格
    print('\n' + '=' * 80)
    print('成本对比表（100个案例）')
    print('=' * 80)
    print()
    
    print(f"{'API提供商':<20} {'模式':<12} {'成本(USD)':<15} {'成本(CNY)':<15} {'是否思考模式':<15}")
    print('-' * 80)
    
    for provider in providers:
        provider_name = {
            'deepseek': 'DeepSeek',
            'qwen': '通义千问',
            'chatgpt': 'ChatGPT',
            'gemini': 'Gemini',
            'grok': 'Grok'
        }[provider]
        
        for mode_name, use_thinking in modes:
            if use_thinking and not PRICING[provider]['supports_thinking']:
                continue
            
            cost_usd = results[provider][mode_name]['100_cases_usd']
            cost_cny = results[provider][mode_name]['100_cases_cny']
            thinking_status = '✅ 是' if use_thinking and PRICING[provider]['supports_thinking'] else '❌ 否'
            
            print(f"{provider_name:<20} {mode_name:<12} ${cost_usd:<14.2f} ¥{cost_cny:<14.2f} {thinking_status:<15}")
    
    print()
    print('=' * 80)
    print('说明：')
    print('=' * 80)
    print('1. 思考模式：DeepSeek支持（deepseek-reasoner），Qwen和ChatGPT不支持')
    print('2. 思考模式会增加输出token消耗（包含推理过程），但价格相同')
    print('3. 成本基于实际处理的5个案例的token消耗估算')
    print('4. 实际成本可能因案例长度、问题复杂度等因素有所差异')
    print('5. 汇率按 1 USD = 7.2 CNY 计算')
    print()
    
    # 推荐方案
    print('=' * 80)
    print('推荐方案')
    print('=' * 80)
    print()
    
    # 找出最便宜的方案
    all_costs = []
    for provider in providers:
        for mode_name, use_thinking in modes:
            if use_thinking and not PRICING[provider]['supports_thinking']:
                continue
            cost = results[provider][mode_name]['100_cases_cny']
            all_costs.append((provider, mode_name, cost))
    
    all_costs.sort(key=lambda x: x[2])
    
    print("成本排名（从低到高）：")
    for i, (provider, mode, cost) in enumerate(all_costs, 1):
        provider_name = {
            'deepseek': 'DeepSeek',
            'qwen': '通义千问',
            'chatgpt': 'ChatGPT',
            'gemini': 'Gemini',
            'grok': 'Grok'
        }[provider]
        thinking_status = '（思考模式）' if mode == '思考模式' and PRICING[provider]['supports_thinking'] else ''
        print(f"  {i}. {provider_name} {mode}{thinking_status}: ¥{cost:.2f}")
    
    print()
    print('=' * 80)
    print('✓ 估算完成！')
    print('=' * 80)


if __name__ == '__main__':
    main()

