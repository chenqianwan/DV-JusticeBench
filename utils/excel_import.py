"""
Excel导入模块
用于批量导入案例数据
"""
import pandas as pd
import os
from datetime import datetime
from typing import List, Dict
from config import RESULTS_DIR, DATA_DIR


class ExcelImporter:
    """Excel导入器"""
    
    def __init__(self, temp_dir: str = None):
        """
        初始化导入器
        
        Args:
            temp_dir: 临时文件存储目录（可选，默认使用系统临时目录）
        """
        if temp_dir:
            self.temp_dir = temp_dir
            os.makedirs(self.temp_dir, exist_ok=True)
        else:
            import tempfile
            self.temp_dir = tempfile.gettempdir()
    
    def parse_excel(self, filepath: str) -> List[Dict]:
        """
        解析Excel文件，提取案例数据
        
        Args:
            filepath: Excel文件路径
            
        Returns:
            案例数据列表，每个案例包含：
                - title: 案例标题
                - case_text: 案例内容
                - judge_decision: 法官判决（可选）
                - case_date: 案例日期（可选）
        """
        try:
            # 读取Excel文件
            df = pd.read_excel(filepath, engine='openpyxl')
            
            # 检查必需的列
            required_columns = ['案例标题', '案例内容']
            missing_columns = [col for col in required_columns if col not in df.columns]
            if missing_columns:
                raise ValueError(f"Excel文件缺少必需的列: {', '.join(missing_columns)}")
            
            # 解析数据
            cases = []
            for index, row in df.iterrows():
                # 跳过空行
                if pd.isna(row.get('案例标题')) or pd.isna(row.get('案例内容')):
                    continue
                
                case = {
                    'title': str(row['案例标题']).strip(),
                    'case_text': str(row['案例内容']).strip(),
                    'judge_decision': str(row.get('法官判决', '')).strip() if pd.notna(row.get('法官判决')) else '',
                    'case_date': str(row.get('案例日期', '')).strip() if pd.notna(row.get('案例日期')) else '',
                }
                
                # 验证数据
                if not case['title'] or not case['case_text']:
                    continue
                
                cases.append(case)
            
            return cases
            
        except Exception as e:
            raise Exception(f"解析Excel文件失败: {str(e)}")
    
    def validate_cases(self, cases: List[Dict]) -> Dict:
        """
        验证案例数据
        
        Args:
            cases: 案例数据列表
            
        Returns:
            验证结果字典，包含：
                - valid: 有效案例列表
                - invalid: 无效案例列表（带错误信息）
        """
        valid_cases = []
        invalid_cases = []
        
        for i, case in enumerate(cases, start=2):  # 从第2行开始（第1行是标题）
            errors = []
            
            # 验证标题
            if not case.get('title') or not case['title'].strip():
                errors.append('案例标题不能为空')
            
            # 验证内容
            if not case.get('case_text') or not case['case_text'].strip():
                errors.append('案例内容不能为空')
            
            # 验证日期格式（如果提供）
            if case.get('case_date'):
                try:
                    datetime.strptime(case['case_date'], '%Y-%m-%d')
                except:
                    errors.append(f'案例日期格式错误，应为YYYY-MM-DD格式')
            
            if errors:
                invalid_cases.append({
                    'row': i,
                    'title': case.get('title', ''),
                    'errors': errors
                })
            else:
                valid_cases.append(case)
        
        return {
            'valid': valid_cases,
            'invalid': invalid_cases
        }

