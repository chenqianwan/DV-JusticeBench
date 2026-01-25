"""
Token使用统计工具
用于跟踪和统计API调用的token消耗
"""
import json
import os
from datetime import datetime
from typing import Dict, Optional
from collections import defaultdict


class TokenTracker:
    """Token使用统计器"""
    
    def __init__(self, log_file: str = None):
        """
        初始化Token统计器
        
        Args:
            log_file: 日志文件路径，如果不提供则使用默认路径
        """
        if log_file is None:
            log_file = os.path.join(os.path.dirname(__file__), '..', 'data', 'token_usage.json')
        
        self.log_file = log_file
        self.usage_data = self._load_usage_data()
        
        # 当前会话统计
        self.session_stats = {
            'input_tokens': 0,
            'output_tokens': 0,
            'reasoning_tokens': 0,
            'total_tokens': 0,
            'api_calls': 0,
            'start_time': datetime.now().isoformat()
        }
    
    def _load_usage_data(self) -> Dict:
        """加载历史使用数据"""
        if os.path.exists(self.log_file):
            try:
                with open(self.log_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except (json.JSONDecodeError, IOError):
                return self._init_usage_data()
        return self._init_usage_data()
    
    def _init_usage_data(self) -> Dict:
        """初始化使用数据结构"""
        return {
            'total_stats': {
                'input_tokens': 0,
                'output_tokens': 0,
                'reasoning_tokens': 0,
                'total_tokens': 0,
                'api_calls': 0
            },
            'sessions': [],
            'by_date': defaultdict(lambda: {
                'input_tokens': 0,
                'output_tokens': 0,
                'reasoning_tokens': 0,
                'total_tokens': 0,
                'api_calls': 0
            })
        }
    
    def record_usage(self, usage: Dict, api_type: str = 'unknown'):
        """
        记录一次API调用的token使用情况
        
        Args:
            usage: API返回的usage字典，包含 prompt_tokens, completion_tokens, total_tokens 等
            api_type: API调用类型（如 'masking', 'generate_questions', 'analyze_case', 'evaluate'）
        """
        input_tokens = usage.get('prompt_tokens', 0)
        output_tokens = usage.get('completion_tokens', 0)
        total_tokens = usage.get('total_tokens', 0)
        reasoning_tokens = usage.get('reasoning_tokens', 0)
        
        # 更新会话统计
        self.session_stats['input_tokens'] += input_tokens
        self.session_stats['output_tokens'] += output_tokens
        self.session_stats['reasoning_tokens'] += reasoning_tokens
        self.session_stats['total_tokens'] += total_tokens
        self.session_stats['api_calls'] += 1
        
        # 更新总统计
        self.usage_data['total_stats']['input_tokens'] += input_tokens
        self.usage_data['total_stats']['output_tokens'] += output_tokens
        self.usage_data['total_stats']['reasoning_tokens'] += reasoning_tokens
        self.usage_data['total_stats']['total_tokens'] += total_tokens
        self.usage_data['total_stats']['api_calls'] += 1
        
        # 按日期统计
        today = datetime.now().strftime('%Y-%m-%d')
        self.usage_data['by_date'][today]['input_tokens'] += input_tokens
        self.usage_data['by_date'][today]['output_tokens'] += output_tokens
        self.usage_data['by_date'][today]['reasoning_tokens'] += reasoning_tokens
        self.usage_data['by_date'][today]['total_tokens'] += total_tokens
        self.usage_data['by_date'][today]['api_calls'] += 1
        
        # 记录详细调用
        call_record = {
            'timestamp': datetime.now().isoformat(),
            'api_type': api_type,
            'input_tokens': input_tokens,
            'output_tokens': output_tokens,
            'reasoning_tokens': reasoning_tokens,
            'total_tokens': total_tokens
        }
        self.usage_data['sessions'].append(call_record)
        
        # 保存到文件
        self._save_usage_data()
    
    def _save_usage_data(self):
        """保存使用数据到文件"""
        os.makedirs(os.path.dirname(self.log_file), exist_ok=True)
        
        # 转换defaultdict为普通dict以便JSON序列化
        data_to_save = {
            'total_stats': self.usage_data['total_stats'],
            'sessions': self.usage_data['sessions'][-1000:],  # 只保留最近1000条
            'by_date': dict(self.usage_data['by_date'])
        }
        
        with open(self.log_file, 'w', encoding='utf-8') as f:
            json.dump(data_to_save, f, ensure_ascii=False, indent=2)
    
    def get_session_stats(self) -> Dict:
        """获取当前会话统计"""
        self.session_stats['end_time'] = datetime.now().isoformat()
        return self.session_stats.copy()
    
    def get_total_stats(self) -> Dict:
        """获取总统计"""
        return self.usage_data['total_stats'].copy()
    
    def get_daily_stats(self, date: str = None) -> Dict:
        """
        获取指定日期的统计
        
        Args:
            date: 日期字符串（YYYY-MM-DD），如果不提供则返回今天
        """
        if date is None:
            date = datetime.now().strftime('%Y-%m-%d')
        return self.usage_data['by_date'].get(date, {
            'input_tokens': 0,
            'output_tokens': 0,
            'reasoning_tokens': 0,
            'total_tokens': 0,
            'api_calls': 0
        })
    
    def calculate_cost(self, pricing: Dict) -> Dict:
        """
        计算成本
        
        Args:
            pricing: 价格字典，格式 {'input_cny': 2.0, 'output_cny': 8.0}
        
        Returns:
            成本字典
        """
        total_stats = self.get_total_stats()
        
        input_cost = (total_stats['input_tokens'] / 1_000_000) * pricing.get('input_cny', 2.0)
        output_cost = (total_stats['output_tokens'] / 1_000_000) * pricing.get('output_cny', 8.0)
        
        # reasoning tokens通常按输出价格计算
        reasoning_cost = (total_stats['reasoning_tokens'] / 1_000_000) * pricing.get('output_cny', 8.0)
        
        total_cost = input_cost + output_cost + reasoning_cost
        
        return {
            'input_cost_cny': input_cost,
            'output_cost_cny': output_cost,
            'reasoning_cost_cny': reasoning_cost,
            'total_cost_cny': total_cost,
            'input_tokens': total_stats['input_tokens'],
            'output_tokens': total_stats['output_tokens'],
            'reasoning_tokens': total_stats['reasoning_tokens'],
            'total_tokens': total_stats['total_tokens'],
            'api_calls': total_stats['api_calls']
        }
    
    def print_summary(self, pricing: Dict = None):
        """打印统计摘要"""
        if pricing is None:
            pricing = {'input_cny': 2.0, 'output_cny': 8.0}  # DeepSeek-V3默认价格
        
        total_stats = self.get_total_stats()
        session_stats = self.get_session_stats()
        cost_info = self.calculate_cost(pricing)
        
        print('=' * 60)
        print('Token使用统计摘要')
        print('=' * 60)
        print()
        
        print('总统计（所有历史记录）:')
        print(f"  输入tokens: {total_stats['input_tokens']:,}")
        print(f"  输出tokens: {total_stats['output_tokens']:,}")
        if total_stats['reasoning_tokens'] > 0:
            print(f"  推理tokens: {total_stats['reasoning_tokens']:,}")
        print(f"  总计tokens: {total_stats['total_tokens']:,}")
        print(f"  API调用次数: {total_stats['api_calls']:,}")
        print()
        
        print('当前会话统计:')
        print(f"  输入tokens: {session_stats['input_tokens']:,}")
        print(f"  输出tokens: {session_stats['output_tokens']:,}")
        if session_stats['reasoning_tokens'] > 0:
            print(f"  推理tokens: {session_stats['reasoning_tokens']:,}")
        print(f"  总计tokens: {session_stats['total_tokens']:,}")
        print(f"  API调用次数: {session_stats['api_calls']:,}")
        print()
        
        print('成本统计:')
        print(f"  输入成本: ¥{cost_info['input_cost_cny']:.2f}")
        print(f"  输出成本: ¥{cost_info['output_cost_cny']:.2f}")
        if cost_info['reasoning_cost_cny'] > 0:
            print(f"  推理成本: ¥{cost_info['reasoning_cost_cny']:.2f}")
        print(f"  总成本: ¥{cost_info['total_cost_cny']:.2f}")
        print()
        
        # 计算平均每次调用
        if total_stats['api_calls'] > 0:
            avg_input = total_stats['input_tokens'] / total_stats['api_calls']
            avg_output = total_stats['output_tokens'] / total_stats['api_calls']
            avg_total = total_stats['total_tokens'] / total_stats['api_calls']
            print('平均每次API调用:')
            print(f"  输入tokens: {avg_input:.0f}")
            print(f"  输出tokens: {avg_output:.0f}")
            print(f"  总计tokens: {avg_total:.0f}")
            print()


# 全局实例
token_tracker = TokenTracker()

