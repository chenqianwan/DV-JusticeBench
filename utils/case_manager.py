"""
案例管理模块
支持案例的存储、检索和管理
"""
import json
import os
from datetime import datetime
from typing import Dict, List, Optional
from config import CASES_DIR


class CaseManager:
    """案例管理器"""
    
    def __init__(self, cases_dir: str = None):
        """
        初始化案例管理器
        
        Args:
            cases_dir: 案例存储目录，如果不提供则从config读取
        """
        self.cases_dir = cases_dir or CASES_DIR
        os.makedirs(self.cases_dir, exist_ok=True)
        self.cases_file = os.path.join(self.cases_dir, 'cases.json')
        self._load_cases()
    
    def _load_cases(self):
        """从文件加载案例列表"""
        if os.path.exists(self.cases_file):
            try:
                with open(self.cases_file, 'r', encoding='utf-8') as f:
                    self.cases = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.cases = {}
        else:
            self.cases = {}
    
    def _save_cases(self):
        """保存案例列表到文件"""
        with open(self.cases_file, 'w', encoding='utf-8') as f:
            json.dump(self.cases, f, ensure_ascii=False, indent=2)
    
    def add_case(self, title: str, case_text: str, judge_decision: str = "", 
                 case_date: str = "", metadata: Dict = None) -> str:
        """
        添加新案例
        
        Args:
            title: 案例标题
            case_text: 案例文本内容
            judge_decision: 法官判决（可选）
            case_date: 案例日期（可选）
            metadata: 其他元数据（可选）
            
        Returns:
            案例ID
        """
        case_id = f"case_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{len(self.cases)}"
        
        case_data = {
            'id': case_id,
            'title': title,
            'case_text': case_text,
            'judge_decision': judge_decision,
            'case_date': case_date or datetime.now().strftime('%Y-%m-%d'),
            'created_at': datetime.now().isoformat(),
            'metadata': metadata or {}
        }
        
        self.cases[case_id] = case_data
        self._save_cases()
        return case_id
    
    def get_case(self, case_id: str) -> Optional[Dict]:
        """
        获取指定案例
        
        Args:
            case_id: 案例ID
            
        Returns:
            案例数据字典，不存在返回None
        """
        return self.cases.get(case_id)
    
    def get_all_cases(self) -> List[Dict]:
        """
        获取所有案例列表
        
        Returns:
            案例列表
        """
        return list(self.cases.values())
    
    def update_case(self, case_id: str, **kwargs) -> bool:
        """
        更新案例信息
        
        Args:
            case_id: 案例ID
            **kwargs: 要更新的字段
            
        Returns:
            更新成功返回True，案例不存在返回False
        """
        if case_id not in self.cases:
            return False
        
        for key, value in kwargs.items():
            if key in self.cases[case_id]:
                self.cases[case_id][key] = value
        
        self.cases[case_id]['updated_at'] = datetime.now().isoformat()
        self._save_cases()
        return True
    
    def delete_case(self, case_id: str) -> bool:
        """
        删除案例
        
        Args:
            case_id: 案例ID
            
        Returns:
            删除成功返回True，案例不存在返回False
        """
        if case_id not in self.cases:
            return False
        
        del self.cases[case_id]
        self._save_cases()
        return True
    
    def search_cases(self, keyword: str) -> List[Dict]:
        """
        搜索案例（按标题和内容）
        
        Args:
            keyword: 搜索关键词
            
        Returns:
            匹配的案例列表
        """
        keyword_lower = keyword.lower()
        results = []
        
        for case in self.cases.values():
            if (keyword_lower in case.get('title', '').lower() or 
                keyword_lower in case.get('case_text', '').lower()):
                results.append(case)
        
        return results


# 创建全局实例
case_manager = CaseManager()


