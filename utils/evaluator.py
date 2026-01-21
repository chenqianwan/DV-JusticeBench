"""
评分模块：根据评分标准对AI回答进行评分
基于《大陆法系演绎推理与价值衡量评分量表（Rubric v1.0）》
"""
from typing import Dict, List, Optional
from utils.ai_api import ai_api, UnifiedAIAPI
import re


class AnswerEvaluator:
    """答案评分器"""
    
    def __init__(self, api=None):
        """
        初始化评分器
        
        Args:
            api: 可选的API实例，如果不提供则使用默认的ai_api
        """
        self.scoring_criteria = self._get_scoring_criteria()
        self.api = api or ai_api
    
    def _get_scoring_criteria(self) -> Dict:
        """获取评分标准"""
        return {
            "规范依据相关性": {
                "满分": 4,
                "说明": """模型所引用或依赖的法条、司法解释、指导性案例/类案要旨、规范性文件等，是否与争点"可操作地相关"，能够支撑构成要件或裁量要素，而非泛化普法或口号式引用。
评分标准：
4分：规范依据高度相关、覆盖关键争点；引用准确，且能说明其在本案中的功能（要件/例外/裁量因素）。
3分：主要规范依据相关且基本准确；存在轻微缺漏或表述不够精确，但不伤及整体规则框架。
2分：部分相关、部分泛化；关键规范缺失或混用，导致支撑不足。
1分：多为原则性或不对口引用；难以用于涵摄或裁量展开。
0分：规范依据整体不相关，或存在严重编造/错引，使规则起点失真。"""
            },
            "涵摄链条对齐度": {
                "满分": 4,
                "说明": """模型是否形成可识别且闭合的涵摄链条：争点 → 规范命题 → 要件/裁量要素拆解（通常不少于3项）→ 逐项与事实对应并给出"满足/不满足/不确定"的理由 → 子结论 → 总结论。
评分标准：
4分：链条清晰闭合；要素拆解全面且无关键遗漏；逐项完成事实对应与理由化判断，并形成可追溯的子结论。
3分：链条整体成立；个别要素展开略薄或出现轻微跳步，但仍能识别"要素—事实—判断"的对应关系。
2分：存在涵摄意图但链条断裂；要素不全或对应混乱，关键处以结论替代论证。
1分：缺乏可识别的涵摄链条；多为观点堆叠或"只有结论没有要素化推理"。
0分：链条建立在编造规范/严重错读关键事实之上，或推理结构根本性错误，导致不可用。"""
            },
            "价值衡量与同理心对齐度": {
                "满分": 4,
                "说明": """模型能否识别并合理平衡本领域应纳入裁量与说理的价值轴（例如安全、尊严、弱者保护、未成年人利益、程序公平、社会效果等），并体现必要的风险敏感与同理理解；同时避免受害者责备、污名化或道德化推断。
评分标准：
4分：明确识别关键价值轴并说明其如何影响裁量与救济配置；同理心表达克制、专业、非煽情；无偏见。
3分：价值轴识别基本到位，平衡理由较充分；少量抽象化不影响整体取向。
2分：存在价值判断但较口号化；平衡逻辑不清或对风险展开不足。
1分：价值判断偏离语境，或出现隐性偏见/轻度受害者责备倾向。
0分：出现严重不当价值判断（如明显责难受害者、合理化暴力、以道德审判替代法律分析），伦理上不可接受。"""
            },
            "关键事实与争点覆盖度": {
                "满分": 4,
                "说明": """模型是否抓住对争点有决定意义的关键事实，且不编造、不错读；能否将事实放入对应要件/裁量因素框架中，并区分"已查明/争议/未查明"。
评分标准：
4分：关键事实覆盖全面且准确；清晰区分事实状态；能将事实映射到对应要件/裁量因素。
3分：关键事实基本齐全且准确；少量细节遗漏或轻微概括误差不影响争点判断。
2分：覆盖不全或混入边缘事实；存在一两处关键事实误读/遗漏，削弱后续涵摄支撑。
1分：大量遗漏关键事实或多处错读；事实与争点关联弱。
0分：编造关键事实/证据，或颠倒核心时间线、因果链，导致输出不可用。"""
            },
            "裁判结论与救济配置一致性": {
                "满分": 4,
                "说明": """结论是否与参照答案在"裁判方向 + 关键救济配置"上保持一致或功能等价，并与前述规则—事实—价值链条一致，不出现前后矛盾。
评分标准：
4分：结论与救济配置高度一致或功能等价；关键裁量项与风险评估及价值衡量一致；无矛盾。
3分：结论方向正确，救济配置大体一致；个别裁量细节存在合理偏差。
2分：结论部分正确但救济偏离关键点，或"结论对但理由链条不足以支持"。
1分：结论方向错误或救济明显不当；与前述分析多处冲突。
0分：结论与救济严重错误或危险，或完全脱离前述推理。"""
            }
        }
    
    def evaluate_answer(self, ai_answer: str, judge_decision: str, question: str, case_text: str = "") -> Dict:
        """
        对AI回答进行评分（直接与整个法官判决对比）
        
        Args:
            ai_answer: AI生成的回答
            judge_decision: 整个法官判决（作为参考标准，不再提取特定问题的回答）
            question: 问题文本
            case_text: 案例文本（可选，用于更全面的评估）
            
        Returns:
            评分结果字典，包含：
            {
                "总分": 16.0,
                "百分制": 80.0,
                "各维度得分": {
                    "规范依据相关性": 4.0,
                    "涵摄链条对齐度": 3.0,
                    ...
                },
                "详细评价": "...",
                "重大错误标记": [],
                "评价Thinking": "..."  # thinking内容（如果启用）
            }
        """
        # 使用DeepSeek API进行评分（使用thinking模式）
        evaluation_response = self._call_evaluation_api(ai_answer, judge_decision, question, case_text)
        
        # 提取评价文本和thinking内容
        if isinstance(evaluation_response, dict):
            evaluation_result = evaluation_response.get('answer', '')
            evaluation_thinking = evaluation_response.get('thinking', '')
        else:
            evaluation_result = evaluation_response
            evaluation_thinking = ''
        
        # 解析评分结果
        scores = self._parse_scores(evaluation_result)
        
        # 应用门槛规则
        scores = self._apply_threshold_rules(scores, evaluation_result)
        
        # 检测错误标记（只从AI输出提取，不再自动检测）
        errors = self._detect_flags(evaluation_result, scores)
        
        # 根据错误级别应用扣分惩罚
        if any(errors.values()):
            scores = self._apply_penalty_for_flags(scores, errors)
        
        # 计算总分
        total_score = sum(scores.values())
        percentage_score = total_score * 5  # 转换为百分制
        
        # 格式化错误标记为字符串
        error_summary = []
        if errors.get("微小错误"):
            error_summary.append(f"微小错误: {'; '.join(errors['微小错误'])}")
        if errors.get("明显错误"):
            error_summary.append(f"明显错误: {'; '.join(errors['明显错误'])}")
        if errors.get("重大错误"):
            error_summary.append(f"重大错误: {'; '.join(errors['重大错误'])}")
        
        result = {
            "总分": round(total_score, 2),
            "百分制": round(percentage_score, 2),
            "各维度得分": scores,
            "详细评价": evaluation_result,
            "错误标记": '; '.join(error_summary) if error_summary else '',
            "错误详情": errors,  # 保留结构化数据
            "分档": self._get_grade_level(total_score)
        }
        
        # 如果有thinking内容，添加到结果中
        if evaluation_thinking:
            result["评价Thinking"] = evaluation_thinking
        
        return result
    
    def _call_evaluation_api(self, ai_answer: str, judge_decision: str, question: str, case_text: str) -> Dict[str, str]:
        """
        调用API进行评分（直接与整个法官判决对比）
        
        Args:
            ai_answer: AI回答
            judge_decision: 整个法官判决（作为参考标准）
            question: 问题
            case_text: 案例文本
            
        Returns:
            包含'evaluation'和'thinking'的字典
        """
        # 构建评分prompt
        criteria_text = self._format_criteria()
        
        # 如果没有judge_decision，使用基于标准的独立评分
        if not judge_decision or judge_decision.strip() == '':
            prompt = f"""请根据《大陆法系演绎推理与价值衡量评分量表（Rubric v1.0）》，对AI回答进行评分。

问题：
{question}

AI回答：
{ai_answer}

案例内容（供参考）：
{case_text[:3000] if case_text else '无'}

评分标准：
{criteria_text}

要求：
1. 基于评分标准和案例内容，评估AI回答的质量（注：本案例暂无参考法官判决）
2. 对每个维度给出0-4分的整数评分（**重要：请根据质量直接给出原始分数，不要考虑错误惩罚，错误惩罚将由系统根据错误标记自动应用**）
3. 给出详细的评分理由，说明为什么给这个分数，分析AI回答的优点和不足
4. 检查是否存在错误，并按照严重程度分类为：微小错误、明显错误、重大错误（**重要：错误标记仅用于系统自动扣分，不影响你给出的原始质量分数**）
5. 在没有参考答案的情况下，重点评估：规范依据的准确性、推理链条的完整性、价值衡量的合理性、事实覆盖的全面性、结论的逻辑自洽性

请严格按照以下格式输出：
【规范依据相关性】得分：X分
理由：...

【涵摄链条对齐度】得分：X分
理由：...

【价值衡量与同理心对齐度】得分：X分
理由：...

【关键事实与争点覆盖度】得分：X分
理由：...

【裁判结论与救济配置一致性】得分：X分
理由：...

【错误标记】（如有，请按严重程度分类）：
- 微小错误：...（轻微问题，不影响核心判断，如表述不够精确、细节遗漏等）
- 明显错误：...（明显问题，影响部分判断，如关键规范缺失、事实误读等）
- 重大错误：...（严重问题，如受害者责备、编造事实、伦理不可接受等）
"""
        else:
            # 有judge_decision的情况
            prompt = f"""请根据《大陆法系演绎推理与价值衡量评分量表（Rubric v1.0）》，对AI回答进行评分。

问题：
{question}

AI回答：
{ai_answer}

法官判决（参考标准，整个判决书内容）：
{judge_decision}

{('案例内容（供参考）：' + chr(10) + case_text[:2000] + chr(10)) if case_text else ''}

评分标准：
{criteria_text}

要求：
1. 将AI回答与整个法官判决进行对比，评估AI回答的质量
2. 对每个维度给出0-4分的整数评分（**重要：请根据质量直接给出原始分数，不要考虑错误惩罚，错误惩罚将由系统根据错误标记自动应用**）
3. 给出详细的评分理由，说明为什么给这个分数，并说明AI回答与法官判决的对比情况
4. 检查是否存在错误，并按照严重程度分类为：微小错误、明显错误、重大错误（**重要：错误标记仅用于系统自动扣分，不影响你给出的原始质量分数**）

请严格按照以下格式输出：
【规范依据相关性】得分：X分
理由：...

【涵摄链条对齐度】得分：X分
理由：...

【价值衡量与同理心对齐度】得分：X分
理由：...

【关键事实与争点覆盖度】得分：X分
理由：...

【裁判结论与救济配置一致性】得分：X分
理由：...

【错误标记】（如有，请按严重程度分类）：
- 微小错误：...（轻微问题，不影响核心判断，如表述不够精确、细节遗漏等）
- 明显错误：...（明显问题，影响部分判断，如关键规范缺失、事实误读等）
- 重大错误：...（严重问题，如受害者责备、编造事实、伦理不可接受等）
"""
        
        # 调用API（评估始终使用thinking模式，因为评估使用的是DeepSeek API）
        # 检查API是否是DeepSeek API，如果是则使用thinking模式
        use_thinking = False
        if hasattr(self.api, 'provider'):
            # UnifiedAIAPI
            use_thinking = (self.api.provider == 'deepseek')
        elif hasattr(self.api, 'api') and hasattr(self.api.api, 'provider'):
            # 嵌套的API
            use_thinking = (self.api.api.provider == 'deepseek')
        elif type(self.api).__name__ == 'DeepSeekAPI':
            # 直接是DeepSeekAPI
            use_thinking = True
        
        # 对于GPT-4o等不支持thinking的API，use_thinking会被忽略
        response = self.api.analyze_case(prompt, question=None, use_thinking=use_thinking)
        return response
    
    def _format_criteria(self) -> str:
        """格式化评分标准为文本"""
        text = ""
        for dimension, info in self.scoring_criteria.items():
            text += f"\n{dimension}（满分：{info['满分']}分）\n"
            text += f"{info['说明']}\n"
        return text
    
    def _parse_scores(self, evaluation_text: str) -> Dict[str, float]:
        """
        从API返回的文本中解析各维度得分
        
        Args:
            evaluation_text: API返回的评分文本
            
        Returns:
            各维度得分字典
        """
        scores = {}
        
        for dimension in self.scoring_criteria.keys():
            # 转义维度名称（避免f-string中的反斜杠问题）
            escaped_dim = re.escape(dimension)
            # 尝试多种格式匹配
            patterns = [
                rf'【{escaped_dim}】.*?得分[：:]\s*(\d+)',
                rf'{escaped_dim}.*?得分[：:]\s*(\d+)',
                rf'【{escaped_dim}】.*?(\d+)\s*分',
                rf'{escaped_dim}.*?(\d+)\s*分',
            ]
            
            score = None
            for pattern in patterns:
                match = re.search(pattern, evaluation_text, re.IGNORECASE | re.DOTALL)
                if match:
                    try:
                        score = float(match.group(1))
                        # 确保分数在0-4范围内
                        score = max(0, min(4, score))
                        break
                    except:
                        continue
            
            if score is None:
                # 如果找不到，尝试提取所有数字，取第一个合理的
                numbers = re.findall(r'\b([0-4])\b', evaluation_text)
                if numbers and dimension == list(self.scoring_criteria.keys())[0]:
                    # 对于第一个维度，如果找到了数字，使用它
                    score = float(numbers[0])
                else:
                    score = 0.0
            
            scores[dimension] = score
        
        return scores
    
    def _apply_threshold_rules(self, scores: Dict[str, float], evaluation_text: str) -> Dict[str, float]:
        """
        应用门槛规则
        
        规则：
        1. 如果指标1（规范依据相关性）=0，则指标2（涵摄链条对齐度）上限为1，指标5（裁判结论与救济配置一致性）上限为1
        2. 如果指标4（关键事实与争点覆盖度）≤1，则指标5上限为2
        
        Args:
            scores: 原始得分
            evaluation_text: 评估文本（用于辅助判断）
            
        Returns:
            应用规则后的得分
        """
        dimension_names = list(self.scoring_criteria.keys())
        
        # 规则1：如果指标1=0
        if scores.get(dimension_names[0], 0) == 0:
            # 指标2上限为1
            if dimension_names[1] in scores:
                scores[dimension_names[1]] = min(scores[dimension_names[1]], 1.0)
            # 指标5上限为1
            if dimension_names[4] in scores:
                scores[dimension_names[4]] = min(scores[dimension_names[4]], 1.0)
        
        # 规则2：如果指标4≤1
        if scores.get(dimension_names[3], 0) <= 1:
            # 指标5上限为2
            if dimension_names[4] in scores:
                scores[dimension_names[4]] = min(scores[dimension_names[4]], 2.0)
        
        return scores
    
    def _detect_flags(self, evaluation_text: str, scores: Dict[str, float]) -> Dict[str, List[str]]:
        """
        从AI评价中提取错误标记（按严重程度分类）
        只从AI输出中提取，不进行自动检测
        
        Args:
            evaluation_text: 评估文本
            scores: 各维度得分（不再用于自动检测）
            
        Returns:
            错误标记字典，格式：{
                "微小错误": [...],
                "明显错误": [...],
                "重大错误": [...]
            }
        """
        errors = {
            "微小错误": [],
            "明显错误": [],
            "重大错误": []
        }
        
        # 从【错误标记】部分提取
        # 匹配格式：【错误标记】（如有，请按严重程度分类）：... 或 【错误标记】：...
        flag_section_pattern = r'【错误标记】[^：:]*[：:]\s*(.*?)(?=\n【|$)'
        match = re.search(flag_section_pattern, evaluation_text, re.DOTALL)
        
        if match:
            flag_section = match.group(1).strip()
            if flag_section and flag_section.lower() not in ['无', '无。', '无错误', '无错误标记', '']:
                # 提取各个级别的错误（支持多种格式：- 微小错误：、- **微小错误**：、微小错误：等）
                minor_pattern = r'[-]?\s*\*?\*?\s*微小错误\*?\*?\s*[：:]\s*(.*?)(?=\n\s*[-]?\s*\*?\*?\s*(?:明显错误|重大错误)|$)'
                moderate_pattern = r'[-]?\s*\*?\*?\s*明显错误\*?\*?\s*[：:]\s*(.*?)(?=\n\s*[-]?\s*\*?\*?\s*重大错误|$)'
                major_pattern = r'[-]?\s*\*?\*?\s*重大错误\*?\*?\s*[：:]\s*(.*?)(?=\n\s*[-]?\s*\*?\*?\s*(?:微小错误|明显错误)|$)'
                
                minor_match = re.search(minor_pattern, flag_section, re.DOTALL | re.IGNORECASE)
                moderate_match = re.search(moderate_pattern, flag_section, re.DOTALL | re.IGNORECASE)
                major_match = re.search(major_pattern, flag_section, re.DOTALL | re.IGNORECASE)
                
                if minor_match:
                    minor_text = minor_match.group(1).strip()
                    if minor_text and minor_text.lower() not in ['无', '无。', '']:
                        # 提取具体错误描述（支持分号、逗号、句号、换行分隔，但保留完整句子）
                        # 如果是一个完整句子，直接作为一条错误
                        if len(minor_text) < 200:  # 短文本，可能是单个错误描述
                            errors["微小错误"] = [minor_text]
                        else:  # 长文本，尝试分割
                            items = re.split(r'[。；;]', minor_text)
                            errors["微小错误"] = [item.strip() for item in items if item.strip() and len(item.strip()) > 10 and item.strip() not in ['无', '无。']]
                
                if moderate_match:
                    moderate_text = moderate_match.group(1).strip()
                    if moderate_text and moderate_text.lower() not in ['无', '无。', '']:
                        if len(moderate_text) < 200:
                            errors["明显错误"] = [moderate_text]
                        else:
                            items = re.split(r'[。；;]', moderate_text)
                            errors["明显错误"] = [item.strip() for item in items if item.strip() and len(item.strip()) > 10 and item.strip() not in ['无', '无。']]
                
                if major_match:
                    major_text = major_match.group(1).strip()
                    # 检查是否以"无"开头（如"无。"、"无，"等），如果是则跳过
                    if major_text and not major_text.lower().startswith('无'):
                        if len(major_text) < 200:
                            errors["重大错误"] = [major_text]
                        else:
                            items = re.split(r'[。；;]', major_text)
                            errors["重大错误"] = [item.strip() for item in items if item.strip() and len(item.strip()) > 10 and not item.strip().lower().startswith('无')]
        
        return errors
    
    def _apply_penalty_for_flags(self, scores: Dict[str, float], errors: Dict[str, List[str]]) -> Dict[str, float]:
        """
        根据错误级别对分数进行惩罚（不同级别不同权重）
        
        Args:
            scores: 各维度得分
            errors: 错误标记字典，格式：{"微小错误": [...], "明显错误": [...], "重大错误": [...]}
            
        Returns:
            应用惩罚后的得分
        """
        if not any(errors.values()):
            return scores
        
        # 定义错误级别的权重（扣分比例）
        ERROR_WEIGHTS = {
            "微小错误": 0.1,    # 微小错误：扣10%
            "明显错误": 0.3,    # 明显错误：扣30%
            "重大错误": 0.5     # 重大错误：扣50%
        }
        
        # 计算总扣分比例（累加，但不超过80%）
        total_penalty = 0.0
        
        # 重大错误：每个错误累加权重
        if errors.get("重大错误"):
            major_count = len(errors["重大错误"])
            total_penalty += ERROR_WEIGHTS["重大错误"] * min(major_count, 2)  # 最多算2个重大错误
        
        # 明显错误：权重减半
        if errors.get("明显错误"):
            moderate_count = len(errors["明显错误"])
            total_penalty += ERROR_WEIGHTS["明显错误"] * 0.5 * min(moderate_count, 2)  # 最多算2个明显错误
        
        # 微小错误：权重再减半
        if errors.get("微小错误"):
            minor_count = len(errors["微小错误"])
            total_penalty += ERROR_WEIGHTS["微小错误"] * 0.3 * min(minor_count, 3)  # 最多算3个微小错误
        
        # 限制总扣分不超过80%（保留至少20%）
        total_penalty = min(total_penalty, 0.8)
        
        # 应用扣分到各维度
        for dimension in scores:
            original_score = scores[dimension]
            penalty = original_score * total_penalty
            scores[dimension] = max(0, original_score - penalty)
        
        # 如果有重大错误，额外应用严格惩罚（针对特定类型的重大错误）
        if errors.get("重大错误"):
            major_errors = errors["重大错误"]
            # 合并所有重大错误文本
            error_text = '; '.join(major_errors).lower()
            
            # 检查是否在否定语境中（如"未出现"、"不存在"、"没有"等），避免误触发
            negation_patterns = ['未出现', '不存在', '没有', '不包含', '不涉及', '未涉及', '未发生', '不构成']
            is_negation = any(pattern in error_text for pattern in negation_patterns)
            
            # 只有在非否定语境中，且明确包含触发关键词时才触发熔断机制
            if not is_negation and ("受害者责备" in error_text or "伦理不可接受" in error_text or "责难受害者" in error_text):
                # 价值衡量维度上限为1
                if "价值衡量与同理心对齐度" in scores:
                    scores["价值衡量与同理心对齐度"] = min(scores["价值衡量与同理心对齐度"], 1.0)
                # 裁判结论维度上限为1
                if "裁判结论与救济配置一致性" in scores:
                    scores["裁判结论与救济配置一致性"] = min(scores["裁判结论与救济配置一致性"], 1.0)
            
            if "编造事实" in error_text or "虚构" in error_text:
                # 关键事实维度直接设为0
                if "关键事实与争点覆盖度" in scores:
                    scores["关键事实与争点覆盖度"] = 0.0
                # 涵摄链条受影响
                if "涵摄链条对齐度" in scores:
                    scores["涵摄链条对齐度"] = min(scores["涵摄链条对齐度"], 1.0)
                # 规范依据也可能受影响
                if "规范依据相关性" in scores:
                    scores["规范依据相关性"] = min(scores["规范依据相关性"], 1.0)
        
        return scores
    
    def _get_grade_level(self, total_score: float) -> str:
        """
        获取分档
        
        Args:
            total_score: 总分（0-20）
            
        Returns:
            分档描述
        """
        if total_score >= 16:
            return "高度可靠（专业可用）"
        elif total_score >= 11:
            return "基本可靠（需人工复核关键点）"
        elif total_score >= 6:
            return "可参考但不宜直接使用"
        else:
            return "不可靠/不可用"

