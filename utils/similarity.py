"""
相似度计算模块
计算AI判决与法官判决的相似度指标
"""
import re
from typing import Dict, Optional


class SimilarityCalculator:
    """相似度计算器"""
    
    @staticmethod
    def calculate_text_similarity(text1: str, text2: str) -> float:
        """
        计算两个文本的简单相似度（基于共同词汇）
        
        Args:
            text1: 文本1
            text2: 文本2
            
        Returns:
            相似度分数 (0-1)
        """
        if not text1 or not text2:
            return 0.0
        
        # 提取中文词汇
        def extract_words(text):
            # 提取中文字符和常见法律术语
            words = re.findall(r'[\u4e00-\u9fa5]+', text)
            return set(words)
        
        words1 = extract_words(text1)
        words2 = extract_words(text2)
        
        if not words1 or not words2:
            return 0.0
        
        # 计算Jaccard相似度
        intersection = len(words1 & words2)
        union = len(words1 | words2)
        
        if union == 0:
            return 0.0
        
        return intersection / union
    
    @staticmethod
    def extract_key_phrases(text: str) -> set:
        """
        提取关键短语（法律术语、判决关键词等）
        
        Args:
            text: 文本
            
        Returns:
            关键短语集合
        """
        # 常见法律关键词
        legal_keywords = [
            '判决', '裁定', '认定', '适用', '依据', '违反', '构成', '责任',
            '赔偿', '损失', '证据', '事实', '法律', '法规', '条款', '规定',
            '支持', '驳回', '撤销', '维持', '变更', '确认', '无效', '有效'
        ]
        
        phrases = set()
        for keyword in legal_keywords:
            if keyword in text:
                phrases.add(keyword)
        
        # 提取判决结果相关词汇
        result_patterns = [
            r'支持[^，。]*',
            r'驳回[^，。]*',
            r'认定[^，。]*',
            r'判决[^，。]*',
        ]
        
        for pattern in result_patterns:
            matches = re.findall(pattern, text)
            phrases.update(matches)
        
        return phrases
    
    @staticmethod
    def calculate_metrics(ai_decision: str, judge_decision: str) -> Dict:
        """
        计算AI判决与法官判决的相似度指标
        
        Args:
            ai_decision: AI判决文本
            judge_decision: 法官判决文本
            
        Returns:
            包含各项指标的字典
        """
        if not ai_decision or not judge_decision:
            return {
                'overall_similarity': 0.0,
                'keyword_similarity': 0.0,
                'result_consistency': 0.0,
                'legal_basis_similarity': 0.0,
                'reasoning_similarity': 0.0,
                'has_judge_decision': False
            }
        
        # 1. 整体文本相似度
        overall_similarity = SimilarityCalculator.calculate_text_similarity(
            ai_decision, judge_decision
        )
        
        # 2. 关键词相似度
        ai_keywords = SimilarityCalculator.extract_key_phrases(ai_decision)
        judge_keywords = SimilarityCalculator.extract_key_phrases(judge_decision)
        
        if ai_keywords or judge_keywords:
            keyword_intersection = len(ai_keywords & judge_keywords)
            keyword_union = len(ai_keywords | judge_keywords)
            keyword_similarity = keyword_intersection / keyword_union if keyword_union > 0 else 0.0
        else:
            keyword_similarity = 0.0
        
        # 3. 判决结果一致性（简单判断）
        result_consistency = SimilarityCalculator._check_result_consistency(
            ai_decision, judge_decision
        )
        
        # 4. 法律依据相似度
        legal_basis_similarity = SimilarityCalculator._calculate_legal_basis_similarity(
            ai_decision, judge_decision
        )
        
        # 5. 推理过程相似度（基于结构相似性）
        reasoning_similarity = SimilarityCalculator._calculate_reasoning_similarity(
            ai_decision, judge_decision
        )
        
        return {
            'overall_similarity': round(overall_similarity * 100, 2),
            'keyword_similarity': round(keyword_similarity * 100, 2),
            'result_consistency': round(result_consistency * 100, 2),
            'legal_basis_similarity': round(legal_basis_similarity * 100, 2),
            'reasoning_similarity': round(reasoning_similarity * 100, 2),
            'has_judge_decision': True,
            'ai_keywords_count': len(ai_keywords),
            'judge_keywords_count': len(judge_keywords),
            'common_keywords_count': len(ai_keywords & judge_keywords)
        }
    
    @staticmethod
    def _check_result_consistency(ai_decision: str, judge_decision: str) -> float:
        """
        检查判决结果一致性
        
        Returns:
            一致性分数 (0-1)
        """
        # 提取判决结果关键词
        result_keywords = ['支持', '驳回', '认定', '判决', '维持', '撤销', '变更']
        
        ai_results = [kw for kw in result_keywords if kw in ai_decision]
        judge_results = [kw for kw in result_keywords if kw in judge_decision]
        
        if not ai_results or not judge_results:
            return 0.5  # 无法判断，给中等分数
        
        # 检查是否有共同的结果关键词
        common_results = set(ai_results) & set(judge_results)
        
        if common_results:
            return 0.8  # 有共同结果，较高一致性
        else:
            return 0.3  # 结果不同，较低一致性
    
    @staticmethod
    def _calculate_legal_basis_similarity(ai_decision: str, judge_decision: str) -> float:
        """
        计算法律依据相似度
        
        Returns:
            相似度分数 (0-1)
        """
        # 提取法律条文引用
        law_pattern = r'第[一二三四五六七八九十\d]+条|《[^》]+》|法[^，。]*'
        
        ai_laws = set(re.findall(law_pattern, ai_decision))
        judge_laws = set(re.findall(law_pattern, judge_decision))
        
        if not ai_laws and not judge_laws:
            return 0.5  # 都没有明确引用，给中等分数
        
        if not ai_laws or not judge_laws:
            return 0.2  # 只有一个有引用，相似度较低
        
        # 计算法律依据的相似度
        intersection = len(ai_laws & judge_laws)
        union = len(ai_laws | judge_laws)
        
        return intersection / union if union > 0 else 0.0
    
    @staticmethod
    def _calculate_reasoning_similarity(ai_decision: str, judge_decision: str) -> float:
        """
        计算推理过程相似度（基于文本结构和长度）
        
        Returns:
            相似度分数 (0-1)
        """
        # 基于文本长度和结构的简单相似度
        ai_length = len(ai_decision)
        judge_length = len(judge_decision)
        
        if ai_length == 0 or judge_length == 0:
            return 0.0
        
        # 长度相似度
        length_ratio = min(ai_length, judge_length) / max(ai_length, judge_length)
        
        # 段落数量相似度
        ai_paragraphs = len([p for p in ai_decision.split('\n') if p.strip()])
        judge_paragraphs = len([p for p in judge_decision.split('\n') if p.strip()])
        
        if ai_paragraphs == 0 or judge_paragraphs == 0:
            paragraph_ratio = 0.5
        else:
            paragraph_ratio = min(ai_paragraphs, judge_paragraphs) / max(ai_paragraphs, judge_paragraphs)
        
        # 综合相似度
        return (length_ratio * 0.6 + paragraph_ratio * 0.4)


# 创建全局实例
similarity_calculator = SimilarityCalculator()


