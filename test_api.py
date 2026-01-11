#!/usr/bin/env python3
"""
小型测试脚本
用于测试DeepSeek API调用和成本估算
"""
import os
import sys
import json
from datetime import datetime
from utils.deepseek_api import DeepSeekAPI
from utils.case_manager import case_manager

# DeepSeek API 定价（2024年参考价格）
PRICING = {
    'input_per_million': 0.14,   # 输入：$0.14 / 百万token
    'output_per_million': 0.56,   # 输出：$0.56 / 百万token
    'exchange_rate': 7.2          # 美元对人民币汇率
}

def calculate_cost(input_tokens, output_tokens):
    """计算成本"""
    input_cost = (input_tokens / 1_000_000) * PRICING['input_per_million']
    output_cost = (output_tokens / 1_000_000) * PRICING['output_per_million']
    total_cost_usd = input_cost + output_cost
    total_cost_cny = total_cost_usd * PRICING['exchange_rate']
    return {
        'input_tokens': input_tokens,
        'output_tokens': output_tokens,
        'total_tokens': input_tokens + output_tokens,
        'input_cost_usd': input_cost,
        'output_cost_usd': output_cost,
        'total_cost_usd': total_cost_usd,
        'total_cost_cny': total_cost_cny
    }

def test_single_analysis():
    """测试单次案例分析"""
    print("=" * 60)
    print("测试1: 单次案例分析")
    print("=" * 60)
    
    # 获取第一个案例
    cases = case_manager.get_all_cases()
    if not cases:
        print("❌ 没有找到案例，请先添加案例")
        return None
    
    test_case = cases[0]
    case_text = test_case.get('case_text', '')
    test_question = "这个案件的主要争议点是什么？"
    
    print(f"\n📋 测试案例: {test_case.get('title', '未命名')}")
    print(f"📝 案例长度: {len(case_text)} 字符")
    print(f"❓ 测试问题: {test_question}")
    print("\n正在调用API...")
    
    try:
        api = DeepSeekAPI()
        start_time = datetime.now()
        
        # 调用API
        result = api.analyze_case(case_text, test_question)
        
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        # 获取token使用情况（如果API返回）
        # 注意：需要修改API调用以获取token信息
        print(f"\n✅ 分析完成！")
        print(f"⏱️  耗时: {duration:.2f} 秒")
        print(f"📊 结果长度: {len(result)} 字符")
        print(f"\n📄 分析结果预览（前200字符）:")
        print("-" * 60)
        print(result[:200] + "..." if len(result) > 200 else result)
        print("-" * 60)
        
        return {
            'success': True,
            'duration': duration,
            'result_length': len(result),
            'result_preview': result[:200]
        }
        
    except Exception as e:
        print(f"\n❌ 测试失败: {str(e)}")
        return {'success': False, 'error': str(e)}

def test_generate_questions():
    """测试生成问题"""
    print("\n" + "=" * 60)
    print("测试2: 生成测试问题")
    print("=" * 60)
    
    cases = case_manager.get_all_cases()
    if not cases:
        print("❌ 没有找到案例")
        return None
    
    test_case = cases[0]
    case_text = test_case.get('case_text', '')
    num_questions = 5  # 测试生成5个问题
    
    print(f"\n📋 测试案例: {test_case.get('title', '未命名')}")
    print(f"📝 案例长度: {len(case_text)} 字符")
    print(f"❓ 生成问题数量: {num_questions}")
    print("\n正在调用API...")
    
    try:
        api = DeepSeekAPI()
        start_time = datetime.now()
        
        questions = api.generate_questions(case_text, num_questions)
        
        end_time = datetime.now()
        duration = (end_time - start_time).total_seconds()
        
        print(f"\n✅ 生成完成！")
        print(f"⏱️  耗时: {duration:.2f} 秒")
        print(f"📊 生成问题数量: {len(questions)}")
        print(f"\n📋 生成的问题:")
        print("-" * 60)
        for i, q in enumerate(questions, 1):
            print(f"{i}. {q}")
        print("-" * 60)
        
        return {
            'success': True,
            'duration': duration,
            'questions_count': len(questions),
            'questions': questions
        }
        
    except Exception as e:
        print(f"\n❌ 测试失败: {str(e)}")
        return {'success': False, 'error': str(e)}

def estimate_cost_for_500_questions():
    """估算500个问题的成本"""
    print("\n" + "=" * 60)
    print("成本估算: 500个问题")
    print("=" * 60)
    
    # 基于之前的估算
    # 单次分析：输入约1590 tokens，输出约2000 tokens
    single_analysis = calculate_cost(1590, 2000)
    
    # 500次分析
    total_500 = {
        'input_tokens': single_analysis['input_tokens'] * 500,
        'output_tokens': single_analysis['output_tokens'] * 500,
        'total_tokens': single_analysis['total_tokens'] * 500,
        'input_cost_usd': single_analysis['input_cost_usd'] * 500,
        'output_cost_usd': single_analysis['output_cost_usd'] * 500,
        'total_cost_usd': single_analysis['total_cost_usd'] * 500,
        'total_cost_cny': single_analysis['total_cost_cny'] * 500
    }
    
    print(f"\n📊 单次分析成本:")
    print(f"   输入Token: {single_analysis['input_tokens']:,}")
    print(f"   输出Token: {single_analysis['output_tokens']:,}")
    print(f"   总Token: {single_analysis['total_tokens']:,}")
    print(f"   成本: ${single_analysis['total_cost_usd']:.6f} (¥{single_analysis['total_cost_cny']:.4f})")
    
    print(f"\n💰 500次分析总成本:")
    print(f"   总输入Token: {total_500['input_tokens']:,}")
    print(f"   总输出Token: {total_500['output_tokens']:,}")
    print(f"   总Token: {total_500['total_tokens']:,}")
    print(f"   总成本: ${total_500['total_cost_usd']:.4f} (¥{total_500['total_cost_cny']:.2f})")
    
    # 如果有50%需要对比分析
    comparison_cost = calculate_cost(3550, 1800)
    total_with_comparison = {
        'analysis_cost': total_500['total_cost_cny'],
        'comparison_cost': comparison_cost['total_cost_cny'] * 250,  # 250次对比
        'total_cost_cny': total_500['total_cost_cny'] + (comparison_cost['total_cost_cny'] * 250)
    }
    
    print(f"\n💰 包含对比分析的成本（假设50%有法官判决）:")
    print(f"   分析成本: ¥{total_with_comparison['analysis_cost']:.2f}")
    print(f"   对比成本: ¥{total_with_comparison['comparison_cost']:.2f}")
    print(f"   总成本: ¥{total_with_comparison['total_cost_cny']:.2f}")
    
    return total_500

def main():
    """主测试函数"""
    print("=" * 60)
    print("法律AI研究平台 - API功能测试")
    print("=" * 60)
    print(f"测试时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print()
    
    # 检查API密钥
    api_key = os.getenv('DEEPSEEK_API_KEY', '')
    if not api_key:
        print("⚠️  警告: 未检测到DEEPSEEK_API_KEY环境变量")
        print("   请设置环境变量或确保config.json中有API密钥")
        print()
    
    results = {}
    
    # 测试1: 单次分析
    results['test1_analysis'] = test_single_analysis()
    
    # 等待一下，避免API限流
    import time
    time.sleep(2)
    
    # 测试2: 生成问题
    results['test2_generate'] = test_generate_questions()
    
    # 成本估算
    results['cost_estimation'] = estimate_cost_for_500_questions()
    
    # 总结
    print("\n" + "=" * 60)
    print("测试总结")
    print("=" * 60)
    
    if results['test1_analysis'] and results['test1_analysis'].get('success'):
        print("✅ 测试1（单次分析）: 通过")
    else:
        print("❌ 测试1（单次分析）: 失败")
    
    if results['test2_generate'] and results['test2_generate'].get('success'):
        print("✅ 测试2（生成问题）: 通过")
    else:
        print("❌ 测试2（生成问题）: 失败")
    
    print(f"\n💰 500个问题预估成本: ¥{results['cost_estimation']['total_cost_cny']:.2f}")
    
    print("\n" + "=" * 60)
    print("测试完成！")
    print("=" * 60)
    
    # 保存测试结果
    result_file = f"test_results_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    with open(result_file, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2, default=str)
    print(f"\n📄 测试结果已保存到: {result_file}")

if __name__ == '__main__':
    try:
        main()
    except KeyboardInterrupt:
        print("\n\n测试已中断")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ 测试过程中发生错误: {str(e)}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


