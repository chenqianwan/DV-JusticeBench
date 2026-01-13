"""
显示Token使用统计
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

from utils.token_tracker import TokenTracker

def main():
    """主函数"""
    tracker = TokenTracker()
    
    # DeepSeek-V3价格
    pricing = {
        'input_cny': 2.0,   # ¥2 / 百万tokens
        'output_cny': 8.0   # ¥8 / 百万tokens
    }
    
    tracker.print_summary(pricing)
    
    # 显示最近的使用记录
    usage_data = tracker.usage_data
    if usage_data['sessions']:
        print('=' * 60)
        print('最近10次API调用记录:')
        print('=' * 60)
        for record in usage_data['sessions'][-10:]:
            print(f"[{record['timestamp']}] {record['api_type']}: "
                  f"输入={record['input_tokens']}, 输出={record['output_tokens']}, "
                  f"总计={record['total_tokens']}")
        print()

if __name__ == '__main__':
    main()

