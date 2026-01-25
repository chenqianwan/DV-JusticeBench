"""
数据脱敏工具模块
用于对案例文本进行脱敏处理，隐藏地名、人名、时间等敏感信息
支持两种方式：正则表达式脚本脱敏 和 DeepSeek API脱敏
"""
import re
from typing import Dict, Optional
from utils.deepseek_api import DeepSeekAPI
from utils.ai_api import UnifiedAIAPI


class DataMasker:
    """数据脱敏工具"""
    
    def __init__(self):
        # 常见地名模式（按从具体到抽象的顺序，避免重复匹配）
        self.location_patterns = [
            # 具体地址（包含数字的地址，如"乐府江南3-3-306"、"乐府江南3号楼3单元306号"）
            # 注意：排除日期格式，避免误匹配
            (r'[\u4e00-\u9fa5]+\d+号楼\d+单元\d+号', '某地址'),
            (r'[\u4e00-\u9fa5]+[\d\-]+[\d\-]+[\d号]*(?!月|日|时|分)', '某地址'),
            # 省级行政区
            (r'([\u4e00-\u9fa5]{2,8})(?:省|自治区|直辖市|特别行政区)', '某省'),
            # 市级行政区
            (r'([\u4e00-\u9fa5]{2,8})(?:市|州|盟|地区)', '某市'),
            # 县级行政区
            (r'([\u4e00-\u9fa5]{2,8})(?:县|区|旗|自治县|自治旗)', '某县'),
            # 街道/路名
            (r'([\u4e00-\u9fa5]{2,8})(?:路|街|道|巷|弄|胡同)', '某路'),
            # 具体地址（门牌号等）
            (r'([\u4e00-\u9fa5]{2,8})(?:号|栋|幢|单元|室|层)', '某号'),
        ]
        
        # 常见法律术语（不脱敏）
        self.legal_terms = {
            '原告', '被告', '上诉人', '被上诉人', '法院', '本院', '一审', '二审', '审理', '提供', 
            '共同', '一起', '申请', '裁定', '判决', '认为', '经', '的', '和', '与', '在', '为', 
            '是', '有', '不', '也', '都', '就', '还', '可以', '能够', '应该', '需要', '必须', 
            '可能', '如果', '因为', '所以', '但是', '然而', '因此', '据此', '依法', '根据', 
            '按照', '依照', '依据', '关于', '对于', '由于', '鉴于', '经过', '通过', '进行', 
            '完成', '实现', '达到', '取得', '获得', '得到', '受到', '遭受', '汉族', '朝鲜族', 
            '满族', '回族', '某省', '某市', '某县', '某路', '某号', '某年', '某月', '某日', '出生',
            '民事', '刑事', '行政', '纠纷', '案件', '诉讼', '代理', '委托', '代理', '诉讼',
            '法院', '法庭', '审判', '审理', '判决', '裁定', '决定', '执行', '执行', '执行',
        }
        
        # 人名模式（需要结合上下文，更精确的匹配）
        self.name_patterns = [
            # 处理"某女"、"某男"格式（如"邴某女"、"张雨女"、"刘聪魁,男"）- 优先处理
            (r'([\u4e00-\u9fa5]{2,4})(?:[,，]?)(?:女|男)(?=[，。；：\s、]|$)', self._mask_name_with_gender),
            (r'([\u4e00-\u9fa5]{1,2})某(?:女|男)(?=[，。；：\s、]|$)', r'某'),
            # 原告/被告/上诉人/被上诉人 + 姓名（保持角色，支持有/无冒号，支持后面跟顿号、逗号等）
            # 使用负向前瞻确保后面不是常见动词，避免误匹配
            (r'(原告|被告|上诉人|被上诉人|原审原告|原审被告|申请人|被申请人)([：:：]?)([\u4e00-\u9fa5]{2,4})(?=[，。；：\s、与在和为是向]|$)', r'\1\2某'),
            # 证人姓名
            (r'(证人|证明人)([：:：]?)([\u4e00-\u9fa5]{2,4})(?=[，。；：\s、]|$)', r'\1\2某'),
            # 法官/书记员姓名
            (r'(审判长|审判员|书记员|法官助理|代理审判员)([：:：]?)([\u4e00-\u9fa5]{2,4})(?=[，。；：\s、]|$)', r'\1\2某'),
            # 委托代理人姓名
            (r'(委托诉讼代理人|委托代理人|诉讼代理人)([：:：]?)([\u4e00-\u9fa5]{2,4})(?=[，。；：\s、]|$)', r'\1\2某'),
            # 处理"某某"、"某甲"、"某乙"等模式（支持顿号分隔）
            (r'([\u4e00-\u9fa5])某某(?=[，。；：\s、等]|$)', r'某'),
            (r'([\u4e00-\u9fa5])某(?:甲|乙|丙|丁|戊|己|庚|辛|壬|癸)(?=[，。；：\s、]|$)', r'某'),
            # 处理"王某某"、"许某某"等格式（在"等"字之前或单独出现）
            (r'([\u4e00-\u9fa5])某某(?=等|$)', r'某'),
            # 处理"王某"、"李某"、"张某"等格式（常见姓氏+某）
            (r'([\u4e00-\u9fa5])某(?=[，。；：\s、与在和为是向等]|$)', r'某'),
        ]
        
        # 时间模式
        self.date_patterns = [
            # 中文数字日期（完整格式）
            (r'[二三四五六七八九〇零一二三四五六七八九十]+年[一二三四五六七八九十]+月[一二三四五六七八九十]+[日号]', '某年某月某日'),
            # 阿拉伯数字日期（完整格式）
            (r'\d{4}年\d{1,2}月\d{1,2}日', '某年某月某日'),
            # 阿拉伯数字日期（简化格式，如2024-12-30）
            (r'\d{4}[-/]\d{1,2}[-/]\d{1,2}', '某年某月某日'),
            # 年份
            (r'\d{4}年', '某年'),
            # 具体月日和时间（即使年份已脱敏，月日和时间也需要脱敏）
            (r'某年\d{1,2}月\d{1,2}日\d{1,2}时\d{1,2}分', '某年某月某日某时某分'),
            (r'某年\d{1,2}月\d{1,2}日\d{1,2}时', '某年某月某日某时'),
            (r'某年\d{1,2}月\d{1,2}日', '某年某月某日'),
            # 处理已经部分脱敏的时间格式
            (r'某年某月某日\d{1,2}时\d{1,2}分', '某年某月某日某时某分'),
            (r'某年某月某日\d{1,2}时', '某年某月某日某时'),
        ]
        
        # 敏感信息模式
        self.sensitive_patterns = [
            # 案件编号（优先处理，避免被其他规则影响）
            # 格式：(2024)京行终6147号、（2019）最高法民辖29号、（2024）赣1021民初1553号
            (r'[（(]\d{4}[）)][\u4e00-\u9fa5]{0,10}\d{1,10}[\u4e00-\u9fa5]{0,5}\d{0,10}号', '（某年）某号'),
            (r'[（(]\d{4}[）)][\u4e00-\u9fa5]{0,10}\d{1,10}号', '（某年）某号'),
            # 文档编号格式（如：海政复决字〔2023〕314号、京公海行罚决字〔2023〕50316号）
            (r'[\u4e00-\u9fa5]+字[〔\[]\d{4}[〕\]]\d+号', '某字〔某年〕某号'),
            # 身份证号
            (r'公民身份号码[：:]?[Xx\d]{15,18}', '公民身份号码XXX'),
            (r'身份证号[：:]?[Xx\d]{15,18}', '身份证号XXX'),
            (r'身份证号码[：:]?[Xx\d]{15,18}', '身份证号码XXX'),
            # 电话号码（手机号）
            (r'1[3-9]\d{9}', 'XXX'),
            # 电话号码（座机）
            (r'\d{3,4}[-－]\d{7,8}', 'XXX'),
            # 银行账号（部分隐藏的格式）
            (r'\d{2,4}[×*Xx]{4,}\d{2,4}', 'XXX'),
            # 银行账号（完整格式）
            (r'账号[：:]?\d{10,}', '账号XXX'),
            (r'账户[：:]?\d{10,}', '账户XXX'),
        ]
    
    def mask_text(self, text: str) -> str:
        """
        对文本进行脱敏处理
        
        Args:
            text: 原始文本
            
        Returns:
            脱敏后的文本
        """
        if not text:
            return text
        
        masked_text = text
        
        # 1. 脱敏敏感信息（先处理，避免被后续规则影响）
        for pattern, replacement in self.sensitive_patterns:
            masked_text = re.sub(pattern, replacement, masked_text)
        
        # 2. 脱敏时间（在脱敏地名之前，避免日期被误匹配为地址）
        for pattern, replacement in self.date_patterns:
            masked_text = re.sub(pattern, replacement, masked_text)
        
        # 3. 脱敏地名（从具体到抽象）
        for pattern, replacement in self.location_patterns:
            masked_text = re.sub(pattern, replacement, masked_text)
        
        # 4. 脱敏人名（最后处理，因为可能包含地名等）
        for pattern, replacement in self.name_patterns:
            if callable(replacement):
                masked_text = re.sub(pattern, replacement, masked_text)
            else:
                masked_text = re.sub(pattern, replacement, masked_text)
        
        # 5. 处理独立出现的中文姓名（2-4个字符，排除法律术语）
        # 使用更精确的匹配，避免误匹配
        # 只匹配明确的人名模式，避免匹配"原告"、"被告"等词
        masked_text = re.sub(r'([\u4e00-\u9fa5]{2,4})(?=[，。；：\s、与在和为是向等]|$)', self._mask_standalone_name, masked_text)
        
        # 6. 最后清理：确保"原告"、"被告"等词没有被误脱敏
        # 如果被误脱敏为"原某"、"被某"等，恢复为原词
        corrections = {
            '原某': '原告',
            '被某': '被告',
            '上诉某': '上诉人',
            '被上诉某': '被上诉人',
        }
        for wrong, correct in corrections.items():
            masked_text = masked_text.replace(wrong, correct)
        
        return masked_text
    
    def _mask_name_with_gender(self, match) -> str:
        """处理带性别的姓名（如"张雨女"、"刘聪魁,男"、"李某,女"）"""
        full_match = match.group(0)
        name = match.group(1)
        
        # 如果是法律术语，不处理
        if name in self.legal_terms:
            return full_match
        
        # 提取性别标记
        if '女' in full_match:
            return '某女'
        elif '男' in full_match:
            return '某男'
        else:
            return '某'
    
    def _mask_standalone_name(self, match) -> str:
        """处理独立出现的人名（更保守的策略，避免误匹配）"""
        name = match.group(1)
        
        # 如果已经是"某"或包含"某"，不处理
        if '某' in name:
            return name
        
        # 如果是法律术语，不处理（包括"原告"、"被告"等）
        if name in self.legal_terms:
            return name
        
        # 检查是否是常见法律术语的一部分（避免误匹配）
        legal_prefixes = {'原', '被', '上', '审', '法', '法', '诉', '讼', '判', '裁', '决', '执', '行', '委', '托', '代', '理', '证', '人', '书', '记', '员', '法', '官', '助'}
        if name[0] in legal_prefixes and name in ['原告', '被告', '上诉人', '被上诉人', '法院', '本院', '一审', '二审', '审理', '提供', '申请', '裁定', '判决', '认为', '证人', '证明人', '审判长', '审判员', '书记员', '法官助理', '代理审判员', '委托诉讼代理人', '委托代理人', '诉讼代理人']:
            return name
        
        # 常见姓氏（单字）+ 名字（1-2字）的模式
        common_surnames = {'李', '王', '张', '刘', '陈', '杨', '黄', '赵', '吴', '周', '徐', '孙', '马', '朱', '胡', '郭', '何', '高', '林', '罗', '郑', '梁', '谢', '宋', '唐', '许', '韩', '冯', '邓', '曹', '彭', '曾', '肖', '田', '董', '袁', '潘', '于', '蒋', '蔡', '余', '杜', '叶', '程', '苏', '魏', '吕', '丁', '任', '沈', '姚', '卢', '姜', '崔', '钟', '谭', '陆', '汪', '范', '金', '石', '廖', '贾', '夏', '韦', '付', '方', '白', '邹', '孟', '熊', '秦', '邱', '江', '尹', '薛', '闫', '段', '雷', '侯', '龙', '史', '陶', '黎', '贺', '顾', '毛', '郝', '龚', '邵', '万', '钱', '严', '覃', '武', '戴', '莫', '孔', '向', '汤', '哈', '童', '甘'}
        
        # 如果是常见姓氏开头的2-4字，可能是人名（包括复姓和少数民族姓名）
        if len(name) >= 2 and len(name) <= 4 and name[0] in common_surnames:
            return '某'
        
        # 处理少数民族姓名（如"甘斯景旺"）和其他特殊格式
        # 如果是2-4个字符的中文，且不是法律术语，可能是人名
        if len(name) >= 2 and len(name) <= 4:
            # 排除明显不是人名的词
            non_name_words = {'法院', '本院', '一审', '二审', '审理', '提供', '共同', '一起', '申请', '裁定', '判决', '认为', '证人', '证明人', '审判长', '审判员', '书记员', '法官助理', '代理审判员', '委托诉讼代理人', '委托代理人', '诉讼代理人', '汉族', '朝鲜族', '满族', '回族', '民事', '刑事', '行政', '纠纷', '案件', '诉讼', '代理', '委托', '法院', '法庭', '审判', '审理', '判决', '裁定', '决定', '执行'}
            if name not in non_name_words:
                return '某'
        
        return name
    
    def mask_case(self, case: Dict) -> Dict:
        """
        对案例进行脱敏处理（使用正则表达式脚本）
        
        Args:
            case: 案例字典，包含case_text和judge_decision
            
        Returns:
            脱敏后的案例字典，添加case_text_masked和judge_decision_masked字段
        """
        masked_case = case.copy()
        
        # 脱敏案例内容
        if 'case_text' in masked_case:
            masked_case['case_text_masked'] = self.mask_text(str(masked_case.get('case_text', '')))
        
        # 脱敏法官判决
        if 'judge_decision' in masked_case:
            masked_case['judge_decision_masked'] = self.mask_text(str(masked_case.get('judge_decision', '')))
        
        return masked_case


class DataMaskerAPI:
    """使用DeepSeek API进行数据脱敏的工具"""
    
    def __init__(self, api_key: str = None, provider: str = None):
        """
        初始化API脱敏工具
        
        Args:
            api_key: API密钥，如果不提供则从config读取
            provider: API提供商（'deepseek' 或 'chatgpt'），如果不提供则从config读取
        """
        # 使用统一的API接口，支持切换提供商
        if provider:
            self.api = UnifiedAIAPI(provider=provider).api
        else:
            self.api = UnifiedAIAPI().api
    
    def mask_text_with_api(self, text: str, is_title: bool = False) -> Optional[str]:
        """
        使用DeepSeek API对文本进行脱敏处理
        
        Args:
            text: 原始文本
            is_title: 是否为案例标题，如果是标题则使用更简洁的prompt
            
        Returns:
            脱敏后的文本，失败返回None
        """
        if not text:
            return text
        
        if is_title:
            # 标题脱敏的简化prompt
            prompt = f"""请对以下法律案例标题进行脱敏处理，要求：
1. 将所有真实人名替换为"某"或"某男"/"某女"（保留性别信息），但是要标记某男1、某男2这种，否则人名会重复错乱
2. 将所有地名（省、市、县、街道、具体地址）替换为"某省"、"某市"、"某县"、"某路"、"某地址"等
3. 将所有时间（年份、日期、具体时间）替换为"某年"、"某月"、"某日"等
4. 将所有案件编号（如"（2024）京行终6147号"）替换为"（某年）某号"
5. 只输出脱敏后的标题，不要添加任何说明、注释或其他内容

原始标题：
{text}

脱敏后的标题："""
        else:
            # 案例内容脱敏的完整prompt
            prompt = f"""请对以下法律案例文本进行脱敏处理，要求：
1. 将所有真实人名替换为"某"或"某男"/"某女"（保留性别信息），但是要标记某男1、某男2这种，否则人名会重复错乱
2. 将所有地名（省、市、县、街道、具体地址）替换为"某省"、"某市"、"某县"、"某路"、"某地址"等
3. 将所有时间（年份、日期、具体时间）替换为"某年"、"某月"、"某日"、"某时"、"某分"等
4. 将所有案件编号（如"（2024）京行终6147号"）替换为"（某年）某号"
5. 将所有文档编号（如"海政复决字〔2023〕314号"）替换为"某字〔某年〕某号"
6. 将所有身份证号、电话号码等敏感信息替换为"XXX"，尤其注意网址要直接删掉
7. **重要：金额、财产数额、赔偿金额、抚养费、诉讼费等数字金额信息不需要脱敏，这是判决的重点，必须完整保留**
8. 除了脱敏操作，尽最大可能保留法律术语和案件逻辑结构不变
9. 只输出脱敏后的文本，不要添加任何说明或注释

原始文本：
{text}

脱敏后的文本："""
        
        messages = [
            {"role": "user", "content": prompt}
        ]
        
        try:
            response = self.api._make_request(messages, temperature=0.3, max_tokens=4000)
            if response and 'choices' in response and len(response['choices']) > 0:
                masked_text = response['choices'][0]['message']['content'].strip()
                # 清理可能的说明文字
                if '脱敏后的文本' in masked_text:
                    masked_text = masked_text.split('脱敏后的文本：', 1)[-1].strip()
                if '原始文本' in masked_text:
                    masked_text = masked_text.split('原始文本', 1)[0].strip()
                return masked_text
            return None
        except Exception as e:
            print(f"API脱敏失败: {str(e)}")
            return None
    
    def mask_case_with_api(self, case: Dict) -> Dict:
        """
        使用DeepSeek API对案例进行脱敏处理
        
        Args:
            case: 案例字典，包含title、case_text和judge_decision
            
        Returns:
            脱敏后的案例字典，添加title_masked、case_text_masked和judge_decision_masked字段
        """
        masked_case = case.copy()
        
        # 脱敏案例标题（通过DeepSeek API）
        if 'title' in masked_case:
            title = str(masked_case.get('title', ''))
            if title:
                masked_case['title_masked'] = self.mask_text_with_api(title, is_title=True)
            else:
                masked_case['title_masked'] = ''
        
        # 脱敏案例内容
        if 'case_text' in masked_case:
            case_text = str(masked_case.get('case_text', ''))
            if case_text:
                masked_case['case_text_masked'] = self.mask_text_with_api(case_text)
            else:
                masked_case['case_text_masked'] = ''
        
        # 脱敏法官判决
        if 'judge_decision' in masked_case:
            judge_decision = str(masked_case.get('judge_decision', ''))
            if judge_decision:
                masked_case['judge_decision_masked'] = self.mask_text_with_api(judge_decision)
            else:
                masked_case['judge_decision_masked'] = ''
        
        return masked_case
