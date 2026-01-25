"""
DeepSeek API封装模块
提供API调用、错误处理和重试机制
"""
import requests
import json
import time
import threading
from collections import deque
from typing import Dict, Optional, List
from config import DEEPSEEK_API_KEY, DEEPSEEK_API_URL, DEEPSEEK_MAX_RPM, DEEPSEEK_MAX_RPS


class DeepSeekAPI:
    """DeepSeek API客户端"""
    
    def __init__(self, api_key: str = None):
        """
        初始化API客户端
        
        Args:
            api_key: DeepSeek API密钥，如果不提供则从config读取
        """
        self.api_key = api_key or DEEPSEEK_API_KEY
        self.api_url = DEEPSEEK_API_URL
        self.max_retries = 3
        self.retry_delay = 1  # 秒
        
        # 速率限制控制（支持高并发）
        self.request_times = deque(maxlen=3000)  # 记录最近请求时间
        self.min_interval = 0.1  # 最小请求间隔（秒）
        self.rate_limit_lock = threading.Lock()
        
        # 从配置读取速率限制
        self.max_rpm = DEEPSEEK_MAX_RPM
        self.max_rps = DEEPSEEK_MAX_RPS
    
    def _rate_limit_check(self):
        """检查并控制请求速率（支持高并发）"""
        with self.rate_limit_lock:
            now = time.time()
            
            # 移除60秒前的记录
            while self.request_times and now - self.request_times[0] > 60:
                self.request_times.popleft()
            
            # 检查每分钟请求数限制
            if len(self.request_times) >= self.max_rpm:
                wait_time = 60 - (now - self.request_times[0])
                if wait_time > 0:
                    if wait_time > 1.0:  # 只对较长的等待显示提示
                        print(f"[速率限制] 达到每分钟请求上限，等待 {wait_time:.1f} 秒...", flush=True)
                    time.sleep(wait_time)
                    now = time.time()
            
            # 检查每秒请求数限制
            recent_requests = [t for t in self.request_times if now - t < 1.0]
            if len(recent_requests) >= self.max_rps:
                wait_time = 1.0 - (now - recent_requests[0])
                if wait_time > 0:
                    if wait_time > 0.5:  # 只对较长的等待显示提示
                        print(f"[速率限制] 达到每秒请求上限，等待 {wait_time:.1f} 秒...", flush=True)
                    time.sleep(wait_time)
                    now = time.time()
            
            # 确保最小间隔（通常很短，不显示提示）
            if self.request_times:
                last_time = self.request_times[-1]
                if now - last_time < self.min_interval:
                    time.sleep(self.min_interval - (now - last_time))
            
            self.request_times.append(time.time())
        
    def _make_request(self, messages: List[Dict], temperature: float = 0.7, max_tokens: int = 2000, auto_retry_on_truncate: bool = True, use_thinking: bool = False) -> Optional[Dict]:
        """
        发送API请求（带重试机制和速率限制）
        
        Args:
            messages: 消息列表，格式为 [{"role": "user", "content": "..."}]
            temperature: 温度参数，控制随机性
            max_tokens: 最大token数
            auto_retry_on_truncate: 如果响应被截断，是否自动增加max_tokens重试
            use_thinking: 是否使用thinking模式（使用deepseek-r1模型）
            
        Returns:
            API响应字典，失败返回None
        """
        if not self.api_key:
            raise ValueError("DeepSeek API密钥未配置，请在.env文件中设置DEEPSEEK_API_KEY")
        
        original_max_tokens = max_tokens
        current_max_tokens = max_tokens
        
        # 选择模型
        model = 'deepseek-reasoner' if use_thinking else 'deepseek-chat'
        
        for retry_round in range(2):  # 最多重试2轮（原始请求 + 1次补救）
            # 速率限制检查
            self._rate_limit_check()
            
            headers = {
                'Content-Type': 'application/json',
                'Authorization': f'Bearer {self.api_key}'
            }
            
            payload = {
                'model': model,
                'messages': messages,
                'temperature': temperature,
                'max_tokens': current_max_tokens
            }
            
            # 如果使用thinking模式，可能需要添加额外参数
            # 注意：DeepSeek-R1的thinking内容可能在响应的不同位置
            # 先不添加reasoning_effort，看看API是否支持
            
            for attempt in range(self.max_retries):
                try:
                    response = requests.post(
                        self.api_url,
                        headers=headers,
                        json=payload,
                        timeout=180  # 增加到180秒，适应长文本分析
                    )
                    
                    # 处理429错误（速率限制）
                    if response.status_code == 429:
                        retry_after = int(response.headers.get('Retry-After', 60))
                        print(f"[速率限制] API返回429错误，等待 {retry_after} 秒后重试...", flush=True)
                        time.sleep(retry_after)
                        # 重新检查速率限制
                        self._rate_limit_check()
                        continue
                    
                    response.raise_for_status()
                    result = response.json()
                    
                    # 检查响应是否完整（finish_reason）
                    truncated = False
                    if 'choices' in result and len(result['choices']) > 0:
                        choice = result['choices'][0]
                        finish_reason = choice.get('finish_reason', '')
                        if finish_reason == 'length':
                            truncated = True
                            if auto_retry_on_truncate and retry_round == 0:
                                # 增加max_tokens并重试
                                current_max_tokens = min(current_max_tokens * 2, 16000)  # 最多增加到16000
                                print(f"[自动补救] 响应被截断，增加max_tokens到{current_max_tokens}并重新生成...", flush=True)
                                break  # 跳出内层循环，进入下一轮重试
                            else:
                                print(f"[警告] 响应因token限制被截断（max_tokens={current_max_tokens}）", flush=True)
                        elif finish_reason == 'content_filter':
                            print(f"[警告] 响应被内容过滤器截断", flush=True)
                        elif finish_reason not in ['stop', '']:
                            print(f"[警告] 响应完成原因: {finish_reason}", flush=True)
                    
                    # 如果响应完整或已经重试过，返回结果
                    if not truncated or retry_round > 0:
                        # 记录token使用情况（如果API返回）
                        if 'usage' in result:
                            usage = result['usage']
                            input_tokens = usage.get('prompt_tokens', 0)
                            output_tokens = usage.get('completion_tokens', 0)
                            total_tokens = usage.get('total_tokens', 0)
                            # thinking模式可能有额外的reasoning tokens
                            reasoning_tokens = usage.get('reasoning_tokens', 0)
                            if reasoning_tokens > 0:
                                print(f"[Token使用] 输入: {input_tokens}, 输出: {output_tokens}, 推理: {reasoning_tokens}, 总计: {total_tokens}", flush=True)
                            else:
                                print(f"[Token使用] 输入: {input_tokens}, 输出: {output_tokens}, 总计: {total_tokens}", flush=True)
                            
                            # 记录到token统计器
                            try:
                                from utils.token_tracker import token_tracker
                                api_type = 'thinking' if use_thinking else 'normal'
                                token_tracker.record_usage(usage, api_type=api_type)
                            except Exception as e:
                                # 如果导入失败，不影响主流程
                                pass
                        
                        return result
                    
                except requests.exceptions.RequestException as e:
                    if attempt < self.max_retries - 1:
                        wait_time = self.retry_delay * (attempt + 1)
                        print(f"[API重试] 请求失败，{wait_time}秒后重试... (尝试 {attempt + 1}/{self.max_retries})", flush=True)
                        time.sleep(wait_time)
                    else:
                        print(f"API请求最终失败: {str(e)}", flush=True)
                        raise
            
            # 如果是因为截断而重试，但重试后仍然失败，返回最后一次结果
            if truncated and retry_round == 0:
                continue
            else:
                break
        
        return None
    
    def analyze_case(self, case_text: str, question: str = None, use_thinking: bool = True) -> Dict[str, str]:
        """
        分析法律案例
        
        Args:
            case_text: 案例文本
            question: 可选的问题，如果提供则针对问题进行分析
            use_thinking: 是否使用thinking模式
            
        Returns:
            包含'answer'和'thinking'的字典，如果未启用thinking则'thinking'为空字符串
        """
        print(f"[DeepSeek API] 开始分析案例，文本长度: {len(case_text)} 字符", flush=True)
        if question:
            print(f"[DeepSeek API] 分析问题: {question[:50]}...", flush=True)
        if use_thinking:
            print("[DeepSeek API] 使用Thinking模式（deepseek-reasoner）", flush=True)
        
        if question:
            prompt = f"""请作为法律专家分析以下案例，并回答相关问题。

案例内容：
{case_text}

问题：{question}

请提供详细的法律分析，包括：
1. 案件事实梳理
2. 法律适用分析
3. 判决建议
4. 法律依据

请用中文回答。"""
        else:
            prompt = f"""请作为法律专家分析以下案例。

案例内容：
{case_text}

请提供详细的法律分析，包括：
1. 案件事实梳理
2. 法律适用分析
3. 判决建议
4. 法律依据

请用中文回答。"""
        
        messages = [
            {"role": "system", "content": "你是一位专业的法律专家，擅长分析法律案例并提供专业的法律意见。"},
            {"role": "user", "content": prompt}
        ]
        
        print("[DeepSeek API] 正在调用API，请稍候...", flush=True)
        response = self._make_request(messages, temperature=0.3, max_tokens=3000, use_thinking=use_thinking)
        
        if response and 'choices' in response and len(response['choices']) > 0:
            choice = response['choices'][0]
            message = choice.get('message', {})
            
            # 提取thinking内容（如果存在）
            thinking = ''
            if use_thinking:
                # DeepSeek-Reasoner的thinking内容在reasoning_content字段
                thinking = message.get('reasoning_content', '')
                # 如果不在message中，可能在choice的其他位置
                if not thinking:
                    thinking = choice.get('reasoning_content', '')
                # 或者可能在response的其他位置
                if not thinking and 'reasoning_content' in response:
                    thinking = response.get('reasoning_content', '')
            
            # 提取最终答案
            answer = message.get('content', '')
            
            # 如果content为空，自动重试（最多3次）
            if not answer or answer.strip() == '':
                if thinking and thinking.strip():
                    print(f"[DeepSeek API] ⚠️ 警告：content为空，但reasoning_content有内容（{len(thinking)}字符）", flush=True)
                    print(f"[DeepSeek API] 开始自动重试机制...", flush=True)
                
                # 自动重试机制（最多3次）
                max_retries = 3
                for retry_count in range(1, max_retries + 1):
                    print(f"[DeepSeek API] 第{retry_count}次重试（共{max_retries}次）...", flush=True)
                    try:
                        retry_response = self._make_request(messages, temperature=0.3, max_tokens=3000, use_thinking=use_thinking)
                        
                        if retry_response and 'choices' in retry_response and len(retry_response['choices']) > 0:
                            retry_choice = retry_response['choices'][0]
                            retry_message = retry_choice.get('message', {})
                            retry_answer = retry_message.get('content', '')
                            
                            if retry_answer and retry_answer.strip():
                                print(f"[DeepSeek API] ✓ 重试成功，获得答案（{len(retry_answer)}字符）", flush=True)
                                answer = retry_answer
                                
                                # 更新thinking内容（如果重试时也有thinking）
                                if use_thinking:
                                    retry_thinking = retry_message.get('reasoning_content', '')
                                    if retry_thinking:
                                        thinking = retry_thinking
                                break
                            else:
                                print(f"[DeepSeek API] 重试{retry_count}：content仍为空", flush=True)
                                if retry_count < max_retries:
                                    import time
                                    time.sleep(2)  # 等待2秒后重试
                        else:
                            print(f"[DeepSeek API] 重试{retry_count}：API响应格式错误", flush=True)
                            if retry_count < max_retries:
                                import time
                                time.sleep(2)
                    except Exception as retry_e:
                        print(f"[DeepSeek API] 重试{retry_count}失败: {str(retry_e)}", flush=True)
                        if retry_count < max_retries:
                            import time
                            time.sleep(2)
                
                # 如果所有重试都失败，抛出异常
                if not answer or answer.strip() == '':
                    error_msg = f"API返回content为空，重试{max_retries}次后仍失败"
                    print(f"[DeepSeek API] ✗ {error_msg}", flush=True)
                    if thinking and thinking.strip():
                        print(f"[DeepSeek API] 详细信息：thinking内容长度={len(thinking)}字符", flush=True)
                    raise Exception(error_msg)
            
            print(f"[DeepSeek API] 分析完成，答案长度: {len(answer)} 字符", flush=True)
            if thinking:
                print(f"[DeepSeek API] Thinking内容长度: {len(thinking)} 字符", flush=True)
            
            return {
                'answer': answer,
                'thinking': thinking
            }
        else:
            raise Exception("API响应格式错误或为空")
    
    def generate_questions(self, case_text: str, num_questions: int = 10) -> List[str]:
        """
        基于案例生成测试问题
        
        Args:
            case_text: 案例文本
            num_questions: 要生成的问题数量
            
        Returns:
            问题列表
        """
        prompt = f"""请根据本案文本中的争议焦点、裁判理由与法律法条原理，提炼并输出{num_questions}个可供法律AI回答的法律争议问题（涵盖是否构成家暴、危险性/持续性、证据评价、受害者责任归因、公序良俗/关系语境），偏向法律分析和价值判断，不要事实问题

案例内容：
{case_text}

请生成{num_questions}个问题，每个问题一行，用中文回答。只输出问题，不要编号或其他说明。"""
        
        messages = [
            {"role": "system", "content": "你是一位法律教育专家，擅长基于案例生成法律争议问题，这些问题侧重于法律分析和价值判断。"},
            {"role": "user", "content": prompt}
        ]
        
        response = self._make_request(messages, temperature=0.7, max_tokens=2000)
        
        if response and 'choices' in response and len(response['choices']) > 0:
            content = response['choices'][0]['message']['content']
            # 解析问题列表（按行分割，过滤空行）
            questions = [q.strip() for q in content.split('\n') if q.strip()]
            # 移除可能的编号（如 "1. ", "1、"等）
            questions = [q.split('.', 1)[-1].split('、', 1)[-1].strip() for q in questions]
            return questions[:num_questions]
        else:
            raise Exception("API响应格式错误或为空")
    
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
        # 第一步：基于案例内容和法官判决生成问题
        prompt = f"""请根据以下案例内容和法官判决，提炼并输出{num_questions}个可供法律AI回答的法律争议问题。

要求：
1. 问题应该基于法官判决中的争议焦点、裁判理由和法律法条原理
2. 问题应涵盖：是否构成家暴、危险性/持续性、证据评价、受害者责任归因、公序良俗/关系语境
3. 偏向法律分析和价值判断，不要事实问题
4. 每个问题应该能在法官判决中找到对应的回答

案例内容：
{case_text}

法官判决：
{judge_decision}

请生成{num_questions}个问题，每个问题一行，用中文回答。只输出问题，不要编号或其他说明。"""
        
        messages = [
            {"role": "system", "content": "你是一位法律教育专家，擅长基于案例和判决生成法律争议问题。"},
            {"role": "user", "content": prompt}
        ]
        
        response = self._make_request(messages, temperature=0.7, max_tokens=2000)
        
        if response and 'choices' in response and len(response['choices']) > 0:
            content = response['choices'][0]['message']['content']
            questions = [q.strip() for q in content.split('\n') if q.strip()]
            questions = [q.split('.', 1)[-1].split('、', 1)[-1].strip() for q in questions]
            questions = questions[:num_questions]
            
            # 第二步：为每个问题从法官判决中提取回答
            results = []
            for question in questions:
                judge_answer = self._extract_answer_from_judgment(question, judge_decision)
                results.append({
                    'question': question,
                    'judge_answer': judge_answer
                })
            
            return results
        else:
            raise Exception("API响应格式错误或为空")
    
    def _extract_answer_from_judgment(self, question: str, judge_decision: str) -> str:
        """
        从法官判决中提取对特定问题的回答（确保返回原文片段，不是AI生成的）
        
        Args:
            question: 问题文本
            judge_decision: 法官判决文本
            
        Returns:
            法官判决中相关的回答片段（原文）
        """
        # 第一步：让AI识别相关段落，要求返回原文中的关键句子（完全复制，不能改写）
        prompt = f"""请从以下法官判决中，找出对以下问题最相关的段落。

问题：
{question}

法官判决：
{judge_decision}

要求：
1. 找出法官判决中直接回答该问题的相关段落
2. 如果判决中没有直接回答，找出最相关的法律推理和判断段落
3. **重要：必须完全复制原文中的句子，一个字都不能改，不能重新表述，不能总结**
4. 返回原文中的完整句子或段落，保持原文的标点和格式
5. 如果找不到相关内容，返回"判决中未明确回答此问题"

请只输出原文中的句子或段落，不要添加任何说明、解释或改写。"""
        
        messages = [
            {"role": "system", "content": "你是一位法律分析专家，擅长从判决书中定位特定问题的相关段落。你必须完全复制原文，不能改写或总结。"},
            {"role": "user", "content": prompt}
        ]
        
        response = self._make_request(messages, temperature=0.1, max_tokens=1500)  # 降低temperature确保更准确
        
        if response and 'choices' in response and len(response['choices']) > 0:
            ai_extracted = response['choices'][0]['message']['content'].strip()
            
            # 第二步：验证提取的文本是否在原文中
            # 如果完全匹配，直接返回
            if ai_extracted in judge_decision:
                return ai_extracted
            
            # 如果不完全匹配，尝试找到最相似的原文段落
            # 方法：在原文中搜索包含提取文本中关键词的段落
            return self._find_original_text(ai_extracted, judge_decision)
        else:
            return "无法提取回答"
    
    def _find_original_text(self, ai_extracted: str, judge_decision: str, min_length: int = 20) -> str:
        """
        在原文中找到与AI提取文本最相似的段落
        
        Args:
            ai_extracted: AI提取的文本
            judge_decision: 原文
            min_length: 最小段落长度
            
        Returns:
            原文中最相似的段落
        """
        import re
        
        # 如果AI提取的文本太短，可能不准确
        if len(ai_extracted) < min_length:
            return ai_extracted
        
        # 提取AI文本中的关键词（中文字符和数字）
        keywords = re.findall(r'[\u4e00-\u9fa5]{2,}|\d+', ai_extracted)
        if not keywords:
            return ai_extracted
        
        # 在原文中查找包含这些关键词的段落
        # 按句号、分号、换行符分割原文
        sentences = re.split(r'[。；\n]', judge_decision)
        
        # 计算每个句子与AI提取文本的相似度
        best_match = ""
        best_score = 0
        
        for sentence in sentences:
            if len(sentence.strip()) < min_length:
                continue
            
            # 计算包含的关键词数量
            keyword_count = sum(1 for keyword in keywords if keyword in sentence)
            if keyword_count > 0:
                # 计算相似度：关键词匹配数 / 总关键词数
                score = keyword_count / len(keywords)
                if score > best_score:
                    best_score = score
                    best_match = sentence.strip()
        
        # 如果找到较好的匹配（相似度>0.3），返回该段落
        if best_score > 0.3 and best_match:
            # 尝试返回更完整的段落（包含前后文）
            idx = judge_decision.find(best_match)
            if idx != -1:
                # 返回包含该句子的完整段落（前后各200字）
                start = max(0, idx - 200)
                end = min(len(judge_decision), idx + len(best_match) + 200)
                return judge_decision[start:end].strip()
            return best_match
        
        # 如果找不到匹配，返回AI提取的文本（但标记可能不准确）
        return ai_extracted
    
    def compare_decisions(self, ai_decision: str, judge_decision: str) -> str:
        """
        对比AI判决和法官判决的差异
        
        Args:
            ai_decision: AI生成的判决
            judge_decision: 法官的实际判决
            
        Returns:
            差异分析文本
        """
        prompt = f"""请对比分析以下两个法律判决的差异：

AI判决：
{ai_decision}

法官判决：
{judge_decision}

请从以下角度进行对比分析：
1. 判决结果的一致性
2. 法律依据的差异
3. 推理过程的差异
4. 关键争议点的处理差异
5. 整体评价

请用中文回答。"""
        
        messages = [
            {"role": "system", "content": "你是一位法律分析专家，擅长对比分析不同判决的差异。"},
            {"role": "user", "content": prompt}
        ]
        
        response = self._make_request(messages, temperature=0.5, max_tokens=2500)
        
        if response and 'choices' in response and len(response['choices']) > 0:
            return response['choices'][0]['message']['content']
        else:
            raise Exception("API响应格式错误或为空")


# 创建全局实例
deepseek_api = DeepSeekAPI()

