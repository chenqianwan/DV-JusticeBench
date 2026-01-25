"""
统一的AI API接口
支持在DeepSeek、ChatGPT、Qwen和Claude之间切换
"""
from typing import List, Optional, Dict
from config import API_PROVIDER
from utils.deepseek_api import DeepSeekAPI
from utils.unified_model_api import UnifiedModelAPI
from utils.qwen_api import QwenAPI


class UnifiedAIAPI:
    """
    统一的AI API接口
    根据配置自动选择使用DeepSeek或ChatGPT
    """
    
    def __init__(self, provider: str = None, model: str = None):
        """
        初始化统一API接口
        
        Args:
            provider: API提供商（'deepseek'、'chatgpt'、'qwen' 或 'claude'），如果不提供则从config读取
            model: 模型名称，如 'gpt-4o', 'claude-3-5-sonnet-20241022' 等
        """
        self.provider = (provider or API_PROVIDER).lower()
        
        if self.provider == 'chatgpt':
            self.api = UnifiedModelAPI(model=model)
            self.api_name = 'ChatGPT'
            if model:
                print(f"[统一API] 使用提供商: {self.api_name}, 模型: {model}")
            else:
                print(f"[统一API] 使用提供商: {self.api_name}")
        elif self.provider == 'qwen':
            # Qwen使用统一模型API（通过xhub.chat endpoint）
            self.api = UnifiedModelAPI(model=model or 'qwen-max')
            self.api_name = 'Qwen'
            if model:
                print(f"[统一API] 使用提供商: {self.api_name}, 模型: {model}")
            else:
                print(f"[统一API] 使用提供商: {self.api_name}")
        elif self.provider == 'claude':
            # Claude使用统一模型API
            self.api = UnifiedModelAPI(model=model or 'claude-opus-4-20250514')
            self.api_name = 'Claude'
            if model:
                print(f"[统一API] 使用提供商: {self.api_name}, 模型: {model}")
            else:
                print(f"[统一API] 使用提供商: {self.api_name}")
        else:
            self.api = DeepSeekAPI()
            self.api_name = 'DeepSeek'
            print(f"[统一API] 使用提供商: {self.api_name}")
    
    def analyze_case(self, case_text: str, question: str = None, use_thinking: bool = True) -> Dict[str, str]:
        """
        分析法律案例
        
        Args:
            case_text: 案例文本
            question: 可选的问题，如果提供则针对问题进行分析
            use_thinking: 是否使用thinking模式（仅DeepSeek支持）
            
        Returns:
            包含'answer'和'thinking'的字典，如果未启用thinking则'thinking'为空字符串
        """
        # 如果API支持thinking模式（DeepSeek），传递参数
        if hasattr(self.api, 'analyze_case') and 'use_thinking' in self.api.analyze_case.__code__.co_varnames:
            return self.api.analyze_case(case_text, question, use_thinking=use_thinking)
        else:
            # 其他API（ChatGPT, Qwen, Claude）不支持thinking，返回普通结果
            result = self.api.analyze_case(case_text, question)
            if isinstance(result, dict):
                return result
            else:
                return {'answer': result, 'thinking': ''}
    
    def generate_questions(self, case_text: str, num_questions: int = 10) -> List[str]:
        """
        基于案例生成测试问题
        
        Args:
            case_text: 案例文本
            num_questions: 要生成的问题数量
            
        Returns:
            问题列表
        """
        return self.api.generate_questions(case_text, num_questions)
    
    def generate_questions_with_judge_answers(self, case_text: str, judge_decision: str, num_questions: int = 5) -> List[Dict]:
        """
        基于案例和法官判决生成问题，并提取法官判决中的回答
        
        Args:
            case_text: 案例文本
            judge_decision: 法官判决文本
            num_questions: 要生成的问题数量
            
        Returns:
            问题列表，每个问题包含 {'question': '...', 'judge_answer': '...'}
        """
        return self.api.generate_questions_with_judge_answers(case_text, judge_decision, num_questions)
    
    def compare_decisions(self, ai_decision: str, judge_decision: str) -> str:
        """
        对比AI判决和法官判决的差异
        
        Args:
            ai_decision: AI生成的判决
            judge_decision: 法官的实际判决
            
        Returns:
            差异分析文本
        """
        return self.api.compare_decisions(ai_decision, judge_decision)
    
    def get_provider_name(self) -> str:
        """获取当前使用的API提供商名称"""
        return self.api_name


# 创建全局实例（根据配置自动选择）
ai_api = UnifiedAIAPI()

