"""
统一案例处理脚本 - 支持选择不同模型
步骤1/4: 脱敏处理 → DeepSeek API
步骤2/4: 生成问题 → DeepSeek API
步骤3/4: 生成AI回答 → 选择的模型（deepseek/gpt4o/gemini）
步骤4/4: 评估 → DeepSeek API

使用方法:
    # 使用默认模型（DeepSeek）处理所有案例
    python process_cases.py --all
    
    # 使用GPT-4o模型处理前5个案例
    python process_cases.py --model gpt4o --num_cases 5
    
    # 使用Gemini模型处理指定案例
    python process_cases.py --model gemini --case_ids case_001 case_002
    
    # 使用DeepSeek模型处理所有案例（默认）
    python process_cases.py --model deepseek --all
"""
import pandas as pd
import os
import time
import sys
import argparse
from datetime import datetime
import concurrent.futures
from utils.ai_api import UnifiedAIAPI
from utils.evaluator import AnswerEvaluator
from utils.data_masking import DataMaskerAPI
from utils.unified_model_api import UnifiedModelAPI
from config import MAX_CONCURRENT_WORKERS
from utils.process_cleanup import setup_signal_handlers, SafeThreadPoolExecutor
import json
import glob

# 文件锁支持（用于并发写入Excel文件）
try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False  # Windows系统不支持fcntl


def process_single_case(case_id, case, case_index, total_cases, model='deepseek', existing_questions_data=None, unified_data=None, gpt_model='gpt-4o', qwen_model='qwen-max', use_thinking=True):
    """处理单个案例"""
    print('=' * 80, flush=True)
    print(f'[{case_index}/{total_cases}] 处理案例: {case_id}', flush=True)
    print(f'案例标题: {case["title"]}', flush=True)
    print('=' * 80, flush=True)
    print(flush=True)
    
    case_title = case['title']
    case_text = case.get('content', case.get('case_text', ''))
    judge_decision = case.get('judge_decision', '')
    
    if not case_text:
        print(f'⚠️ 案例 {case_id} 没有案例内容，跳过', flush=True)
        return None
    
    all_results = []
    
    try:
        # 优先使用统一数据（如果提供）
        if unified_data and case_id in unified_data:
            # 使用统一问题数据（从DeepSeek结果文件提取）
            unified_case_data = unified_data[case_id]
            questions = unified_case_data.get('questions', [])
            
            if questions:
                # 检查是否已有脱敏内容（从DeepSeek文件提取）
                masked_content = unified_case_data.get('masked_content')
                masked_judge = unified_case_data.get('masked_judge')
                masked_title = unified_case_data.get('masked_title', '')
                
                if masked_content and masked_judge:
                    # 直接使用DeepSeek文件中的脱敏内容，不重新脱敏
                    print(f"[{case_index}/{total_cases}] → 步骤1/4: 使用DeepSeek文件中的脱敏数据（跳过脱敏处理）...", flush=True)
                    print(f"[{case_index}/{total_cases}] ✓ 使用DeepSeek的脱敏数据", flush=True)
                else:
                    # 如果没有脱敏内容，说明DeepSeek文件中没有存储脱敏数据
                    # 使用DeepSeek API重新脱敏，但使用DeepSeek文件中的问题（确保一致性）
                    print(f"[{case_index}/{total_cases}] → 步骤1/4: 脱敏处理（使用DeepSeek API）...", flush=True)
                    masker = DataMaskerAPI()
                    
                    case_dict = {
                        'title': case_title,
                        'case_text': case_text,
                        'judge_decision': judge_decision
                    }
                    
                    masked_case = masker.mask_case_with_api(case_dict)
                    
                    masked_title = masked_case.get('title_masked', '') or masked_title
                    masked_content = masked_case.get('case_text_masked', '')
                    masked_judge = masked_case.get('judge_decision_masked', '')
                    
                    print(f"[{case_index}/{total_cases}] ✓ 脱敏完成", flush=True)
                
                print(f"[{case_index}/{total_cases}] → 步骤2/4: 使用DeepSeek的问题（共{len(questions)}个）...", flush=True)
                print(f"[{case_index}/{total_cases}] ✓ 使用DeepSeek的问题", flush=True)
            else:
                # 如果没有问题，回退到正常流程
                print(f"[{case_index}/{total_cases}] ⚠️ 未找到问题，使用正常流程...", flush=True)
                unified_data = None  # 清除统一数据，使用正常流程
        else:
            # 1. 脱敏处理（为了保持一致性，即使使用现有问题也重新脱敏，但使用相同的问题）
            print(f"[{case_index}/{total_cases}] → 步骤1/4: 脱敏处理...", flush=True)
            masker = DataMaskerAPI()
            
            case_dict = {
                'title': case_title,
                'case_text': case_text,
                'judge_decision': judge_decision
            }
            
            masked_case = masker.mask_case_with_api(case_dict)
            
            masked_title = masked_case.get('title_masked', '')
            masked_content = masked_case.get('case_text_masked', '')
            masked_judge = masked_case.get('judge_decision_masked', '')
            
            print(f"[{case_index}/{total_cases}] ✓ 脱敏完成", flush=True)
            
            print(flush=True)
            
            # 2. 生成问题（使用DeepSeek API）或复用现有问题
            if existing_questions_data and case_id in existing_questions_data:
                # 使用现有问题（从DeepSeek结果中提取）
                print(f"[{case_index}/{total_cases}] → 步骤2/4: 使用现有问题（来自DeepSeek结果）...", flush=True)
                case_questions_data = existing_questions_data[case_id]
                questions = case_questions_data['questions']
                print(f"[{case_index}/{total_cases}] ✓ 使用现有问题（共{len(questions)}个）", flush=True)
            else:
                # 生成新问题
                print(f"[{case_index}/{total_cases}] → 步骤2/4: 生成5个问题...", flush=True)
                deepseek_api = UnifiedAIAPI(provider='deepseek')  # 步骤2使用DeepSeek
                questions = deepseek_api.generate_questions(masked_content, num_questions=5)
                print(f"[{case_index}/{total_cases}] ✓ 问题生成完成（共{len(questions)}个）", flush=True)
        
        print(flush=True)
        
        # 显示问题
        for i, question in enumerate(questions, 1):
            print(f"  问题{i}: {question[:80]}...", flush=True)
        print(flush=True)
        
        # 3. 处理每个问题（生成AI回答并评估）
        print(f"[{case_index}/{total_cases}] → 步骤3/4: 生成AI回答...", flush=True)
        
        def process_single_question(question, q_num):
            """处理单个问题（带失败重试机制）"""
            # 重试配置
            max_retries = 3
            retry_delay = 2  # 秒
            
            # 为每个线程创建独立的API实例，避免锁竞争
            if model == 'gemini':
                thread_ai_api = UnifiedModelAPI(model='gemini-2.5-flash')
            elif model == 'gpt4o':
                thread_ai_api = UnifiedAIAPI(provider='chatgpt', model=gpt_model)
                print(f"  [问题{q_num}/5] 使用GPT模型: {gpt_model}", flush=True)
            elif model == 'claude':
                thread_ai_api = UnifiedModelAPI(model='claude-opus-4-20250514')
                print(f"  [问题{q_num}/5] 使用Claude模型: claude-opus-4-20250514", flush=True)
            elif model == 'qwen':
                thread_ai_api = UnifiedModelAPI(model=qwen_model)
                print(f"  [问题{q_num}/5] 使用Qwen模型: {qwen_model}", flush=True)
            else:
                # 默认使用DeepSeek（支持thinking模式）
                thread_ai_api = UnifiedAIAPI(provider='deepseek')
            
            # 确定模型显示名称
            if model == 'qwen':
                model_display_name = f'Qwen-{qwen_model.split("-")[-1].title()}'
            elif model == 'deepseek':
                # DeepSeek根据是否使用thinking模式显示不同名称
                if use_thinking:
                    model_display_name = 'DeepSeek'
                else:
                    model_display_name = 'DeepSeek-NoThinking'
            else:
                model_display_name = {
                    'gpt4o': 'GPT-4o',
                    'gemini': 'Gemini 2.5 Flash',
                    'claude': 'Claude Opus 4'
                }.get(model, model.upper())
            
            result = {
                '案例ID': case_id,
                '案例标题': case_title,
                '案例标题（脱敏）': masked_title,
                '问题编号': q_num,
                '问题': question,
                '使用的模型': model_display_name,  # 步骤3使用的模型
                '脱敏API': 'DeepSeek',  # 步骤1使用的API
                '问题生成API': 'DeepSeek',  # 步骤2使用的API
                '评估API': 'DeepSeek'  # 步骤4使用的API
            }
            
            # 重试循环
            last_error = None
            for attempt in range(1, max_retries + 1):
                try:
                    # 生成AI回答（步骤3使用选择的模型）
                    if attempt == 1:
                        print(f"  [问题{q_num}/5] 开始生成AI回答...", flush=True)
                    else:
                        print(f"  [问题{q_num}/5] 第{attempt}次重试（共{max_retries}次）...", flush=True)
                    
                    # DeepSeek支持thinking模式，其他模型不支持
                    # 对于Gemini、GPT-4o和Claude，不传递use_thinking参数
                    if model == 'deepseek':
                        ai_response = thread_ai_api.analyze_case(masked_content, question=question, use_thinking=use_thinking)
                    else:
                        # Gemini、GPT-4o和Claude不支持thinking模式，不传递该参数
                        ai_response = thread_ai_api.analyze_case(masked_content, question=question)
                    
                    if isinstance(ai_response, dict):
                        ai_answer = ai_response.get('answer', '')
                        ai_thinking = ai_response.get('thinking', '')
                    else:
                        ai_answer = ai_response
                        ai_thinking = ''
                    
                    # 检查AI回答是否为空
                    if not ai_answer or not ai_answer.strip():
                        error_msg = f"AI回答为空（answer长度={len(ai_answer) if ai_answer else 0}字符）"
                        print(f"  [问题{q_num}/5] ⚠️ {error_msg}", flush=True)
                        if ai_thinking and ai_thinking.strip():
                            print(f"  [问题{q_num}/5] 注意：thinking内容存在（{len(ai_thinking)}字符），但answer为空", flush=True)
                        print(f"  [问题{q_num}/5] 详细信息：案例ID={case_id}, 问题编号={q_num}, 模型={model}", flush=True)
                        raise Exception(error_msg)
                    
                    result['AI回答'] = ai_answer
                    if ai_thinking:
                        result['AI回答Thinking'] = ai_thinking
                    else:
                        result['AI回答Thinking'] = ''
                    
                    print(f"  [问题{q_num}/5] ✓ AI回答生成完成（{len(ai_answer)}字符）", flush=True)
                    
                    # 步骤4/4: 进行评估（使用DeepSeek API）
                    print(f"  [问题{q_num}/5] → 步骤4/4: 开始评估...", flush=True)
                    evaluator = AnswerEvaluator()  # 使用默认的DeepSeek API进行评估
                    evaluation = evaluator.evaluate_answer(
                        ai_answer=ai_answer,
                        judge_decision=masked_judge,
                        question=question,
                        case_text=masked_content
                    )
                    
                    result['总分'] = evaluation['总分']
                    result['百分制'] = evaluation['百分制']
                    result['分档'] = evaluation['分档']
                    
                    # 各维度得分（从'各维度得分'字典中获取）
                    dimension_scores = evaluation.get('各维度得分', {})
                    result['规范依据相关性_得分'] = dimension_scores.get('规范依据相关性', 0)
                    result['涵摄链条对齐度_得分'] = dimension_scores.get('涵摄链条对齐度', 0)
                    result['价值衡量与同理心对齐度_得分'] = dimension_scores.get('价值衡量与同理心对齐度', 0)
                    result['关键事实与争点覆盖度_得分'] = dimension_scores.get('关键事实与争点覆盖度', 0)
                    result['裁判结论与救济配置一致性_得分'] = dimension_scores.get('裁判结论与救济配置一致性', 0)
                    
                    # 错误标记
                    result['错误标记'] = evaluation.get('错误标记', '')
                    # 从错误详情中提取各类型错误
                    error_details = evaluation.get('错误详情', {})
                    result['微小错误'] = '; '.join(error_details.get('微小错误', [])) if error_details.get('微小错误') else ''
                    result['明显错误'] = '; '.join(error_details.get('明显错误', [])) if error_details.get('明显错误') else ''
                    result['重大错误'] = '; '.join(error_details.get('重大错误', [])) if error_details.get('重大错误') else ''
                    
                    # 详细评价
                    result['详细评价'] = evaluation.get('详细评价', '')
                    result['评价Thinking'] = evaluation.get('评价Thinking', '')
                    
                    result['处理错误'] = ''
                    
                    print(f"  [问题{q_num}/5] ✓ 评估完成（总分: {result['总分']:.2f}/20, 百分制: {result['百分制']:.2f}）", flush=True)
                    
                    # 如果重试成功，记录重试信息
                    if attempt > 1:
                        print(f"  [问题{q_num}/5] ✓ 重试成功（第{attempt}次尝试）", flush=True)
                    
                    return result
                    
                except Exception as e:
                    import traceback
                    error_detail = traceback.format_exc()
                    error_msg = str(e)
                    last_error = (error_msg, error_detail)
                    
                    if attempt < max_retries:
                        print(f"  [问题{q_num}/5] ✗ 处理失败（第{attempt}次尝试）: {error_msg}", flush=True)
                        print(f"  [问题{q_num}/5] 等待 {retry_delay} 秒后重试...", flush=True)
                        time.sleep(retry_delay)
                    else:
                        # 最后一次尝试也失败
                        print(f"  [问题{q_num}/5] ✗ 处理失败（已重试{max_retries}次）: {error_msg}", flush=True)
                        print(f"  [问题{q_num}/5] 错误详情:", flush=True)
                        print(f"  {error_detail}", flush=True)
                        print(f"  [问题{q_num}/5] 上下文信息：案例ID={case_id}, 问题编号={q_num}, 模型={model_display_name}", flush=True)
                        
                        # 记录详细错误信息（包含重试信息）
                        error_msg_with_retry = f"{error_msg}（已重试{max_retries}次）"
                        result['处理错误'] = f"{error_msg_with_retry}\n详细堆栈:\n{error_detail}"
                        
                        # 确保AI回答字段有值（即使是错误标记）
                        if 'AI回答' not in result or not result.get('AI回答'):
                            result['AI回答'] = f"[错误：{error_msg_with_retry}]"
                            result['AI回答Thinking'] = ''
                        
                        # 如果评估未完成，设置默认值
                        if '总分' not in result:
                            result['总分'] = 0
                            result['百分制'] = 0
                            result['分档'] = '处理失败'
                            result['详细评价'] = f'处理失败：{error_msg_with_retry}'
                        
                        return result
            
            # 理论上不会到达这里，但为了安全起见
            if last_error:
                error_msg, error_detail = last_error
                result['处理错误'] = f"{error_msg}\n详细堆栈:\n{error_detail}"
                if 'AI回答' not in result or not result.get('AI回答'):
                    result['AI回答'] = f"[错误：{error_msg}]"
                    result['AI回答Thinking'] = ''
                if '总分' not in result:
                    result['总分'] = 0
                    result['百分制'] = 0
                    result['分档'] = '处理失败'
                    result['详细评价'] = f'处理失败：{error_msg}'
            
            return result
        
        # 并行处理所有问题（每个问题独立线程并发处理）
        max_workers = min(MAX_CONCURRENT_WORKERS, len(questions))
        print(f"[{case_index}/{total_cases}] 使用 {max_workers} 个并发线程处理 {len(questions)} 个问题", flush=True)
        
        with SafeThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_question = {
                executor.submit(process_single_question, q, i+1): (i+1, q)
                for i, q in enumerate(questions)
            }
            
            completed_questions = 0
            for future in concurrent.futures.as_completed(future_to_question):
                q_num, question = future_to_question[future]
                try:
                    result = future.result()
                    if result:
                        all_results.append(result)
                        completed_questions += 1
                        print(f"[{case_index}/{total_cases}] 问题进度: {completed_questions}/{len(questions)} 已完成", flush=True)
                except Exception as e:
                    completed_questions += 1
                    print(f"[{case_index}/{total_cases}] ✗ 问题{q_num}处理异常: {str(e)}", flush=True)
                    import traceback
                    traceback.print_exc()
        
        print(f"[{case_index}/{total_cases}] ✓ 所有问题处理完成（共{len(all_results)}个）", flush=True)
        print(flush=True)
        
        return all_results
        
    except Exception as e:
        print(f"✗ 案例 {case_id} 处理失败: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        return None


def find_latest_existing_file():
    """查找最新的现有结果文件"""
    pattern = 'data/*案例*评估*.xlsx'
    files = glob.glob(pattern)
    if not files:
        return None
    latest_file = max(files, key=os.path.getmtime)
    return latest_file


def main():
    setup_signal_handlers()
    
    # 解析命令行参数
    parser = argparse.ArgumentParser(description='统一案例处理脚本 - 支持选择不同模型')
    parser.add_argument('--model', type=str, default='deepseek', choices=['deepseek', 'gpt4o', 'gemini', 'claude', 'qwen'],
                        help='选择模型: deepseek (默认), gpt4o, gemini, claude, 或 qwen')
    parser.add_argument('--num_cases', type=int, default=None,
                        help='处理的案例数量（默认: 处理所有案例或指定案例列表）')
    parser.add_argument('--case_ids', type=str, nargs='+', default=None,
                        help='指定要处理的案例ID列表（例如: --case_ids case_001 case_002）')
    parser.add_argument('--all', action='store_true',
                        help='处理所有案例')
    parser.add_argument('--standalone', action='store_true',
                        help='独立保存，不合并到现有文件')
    parser.add_argument('--use_ds_questions', type=str, default=None,
                        help='使用DeepSeek结果文件中的问题（指定DeepSeek结果文件路径，如108个案例的完整版文件）')
    parser.add_argument('--use_unified_data', type=str, default=None,
                        help='使用统一脱敏和问题数据文件（JSON格式，由prepare_unified_masking_questions.py生成）')
    parser.add_argument('--gpt-model', type=str, default='gpt-4o',
                        help='指定GPT模型名称，如 gpt-4o, gpt-4.1-2025-04-14, gpt-5-chat-latest, o3-2025-04-16 等')
    parser.add_argument('--qwen-model', type=str, default='qwen-max',
                        help='指定Qwen模型名称，如 qwen-turbo, qwen-plus, qwen-max (默认: qwen-max)')
    parser.add_argument('--no-thinking', action='store_true',
                        help='DeepSeek不使用thinking模式（仅对deepseek模型有效）')
    args = parser.parse_args()
    
    model = args.model
    num_cases = args.num_cases
    case_ids_arg = args.case_ids
    process_all = args.all
    standalone = args.standalone
    gpt_model = args.gpt_model
    qwen_model = args.qwen_model
    use_thinking = not args.no_thinking  # 如果指定了--no-thinking，则use_thinking=False
    
    print('=' * 80, flush=True)
    print(f'统一案例处理脚本 - 步骤3使用 {model.upper()} 模型', flush=True)
    print('=' * 80, flush=True)
    if args.use_unified_data:
        print(f'步骤1/4: 脱敏处理 → 使用统一数据（跳过）', flush=True)
        print(f'步骤2/4: 生成问题 → 使用统一数据（跳过）', flush=True)
    elif args.use_ds_questions:
        print(f'步骤1/4: 脱敏处理 → DeepSeek API（使用DeepSeek API重新脱敏）', flush=True)
        print(f'步骤2/4: 生成问题 → 使用DeepSeek结果文件中的问题', flush=True)
    else:
        print(f'步骤1/4: 脱敏处理 → DeepSeek API', flush=True)
        print(f'步骤2/4: 生成问题 → DeepSeek API', flush=True)
    print(f'步骤3/4: 生成AI回答 → {model.upper()} API', flush=True)
    print(f'步骤4/4: 评估 → DeepSeek API', flush=True)
    print('=' * 80, flush=True)
    print(flush=True)
    
    with open('data/cases/cases.json', 'r', encoding='utf-8') as f:
        cases = json.load(f)
    
    # 确定要处理的案例ID列表
    if case_ids_arg:
        # 使用命令行指定的案例ID
        target_case_ids = case_ids_arg
    elif process_all:
        # 处理所有案例
        target_case_ids = list(cases.keys())
    elif num_cases:
        # 处理前N个案例
        target_case_ids = list(cases.keys())[:num_cases]
    else:
        # 默认：处理所有案例
        target_case_ids = list(cases.keys())
    
    # 验证这些案例是否存在
    selected_cases = {}
    for case_id in target_case_ids:
        if case_id in cases:
            selected_cases[case_id] = cases[case_id]
        else:
            print(f"⚠️ 警告: 案例 {case_id} 不在 cases.json 中", flush=True)
    
    if not selected_cases:
        print("错误：没有找到需要处理的案例", flush=True)
        return
    
    print(f"将处理以下 {len(selected_cases)} 个案例:", flush=True)
    for i, (case_id, case) in enumerate(selected_cases.items(), 1):
        print(f"  {i}. {case_id}: {case['title']}", flush=True)
    print(flush=True)
    
    # 加载统一脱敏和问题数据（如果指定，优先级最高）
    unified_data = None
    if args.use_unified_data:
        unified_file = args.use_unified_data
        if os.path.exists(unified_file):
            print(f"加载统一脱敏和问题数据: {unified_file}", flush=True)
            try:
                with open(unified_file, 'r', encoding='utf-8') as f:
                    unified_data = json.load(f)
                print(f"✓ 成功加载 {len(unified_data)} 个案例的统一数据", flush=True)
            except Exception as e:
                print(f"✗ 加载统一数据失败: {str(e)}", flush=True)
                unified_data = None
        else:
            print(f"⚠️ 统一数据文件不存在: {unified_file}，将使用默认流程", flush=True)
    
    # 加载DeepSeek的108个案例结果文件（如果指定，且未使用统一数据）
    # 直接从DeepSeek结果文件中提取问题和脱敏数据
    existing_questions_data = None
    unified_data_from_ds = None  # 从DeepSeek文件提取的统一数据
    if not unified_data and args.use_ds_questions:
        ds_file = args.use_ds_questions
        if os.path.exists(ds_file):
            print(f"从DeepSeek结果文件加载问题和脱敏数据: {ds_file}", flush=True)
            try:
                ds_df = pd.read_excel(ds_file, engine='openpyxl')
                print(f"  读取到 {len(ds_df)} 行数据", flush=True)
                
                # 如果有"使用的模型"列，只提取DeepSeek处理的结果；否则假设所有数据都是DeepSeek的
                if '使用的模型' in ds_df.columns:
                    ds_df_filtered = ds_df[ds_df['使用的模型'] == 'DeepSeek']
                    if len(ds_df_filtered) > 0:
                        ds_df = ds_df_filtered
                        print(f"  筛选DeepSeek数据后: {len(ds_df)} 行", flush=True)
                    else:
                        print(f"  ⚠️ 未找到DeepSeek数据，使用全部数据", flush=True)
                
                # 提取统一数据（包含问题和脱敏内容）
                unified_data_from_ds = {}
                # 先打印所有列名，便于调试
                print(f"  DeepSeek文件包含的列: {list(ds_df.columns)[:20]}...", flush=True)
                
                for case_id in selected_cases.keys():
                    case_data = ds_df[ds_df['案例ID'] == case_id]
                    if len(case_data) > 0:
                        # 按问题编号排序，确保顺序一致
                        case_data = case_data.sort_values('问题编号')
                        questions = case_data['问题'].tolist()
                        if len(questions) >= 5:
                            questions = questions[:5]  # 只取前5个问题
                            first_row = case_data.iloc[0]
                            
                            # 提取脱敏数据（如果存在）
                            masked_title = first_row.get('案例标题（脱敏）', '')
                            
                            # 尝试从可能的列名中提取脱敏内容
                            # 注意：DeepSeek结果文件中可能不存储完整的脱敏内容
                            # 如果找不到，我们需要从原始案例重新脱敏，但使用相同的问题
                            possible_content_cols = ['案例内容（脱敏）', '脱敏内容', '案例文本（脱敏）', '案例内容', 'case_text_masked', '详细评价']
                            possible_judge_cols = ['法官判决（脱敏）', '判决（脱敏）', '法官决定（脱敏）', '法官判决', 'judge_decision_masked']
                            
                            masked_content = None
                            masked_judge = None
                            
                            # 尝试提取脱敏内容
                            for col in possible_content_cols:
                                if col in first_row.index:
                                    value = first_row[col]
                                    if pd.notna(value) and str(value).strip() and len(str(value).strip()) > 50:  # 确保内容足够长
                                        masked_content = str(value).strip()
                                        print(f"  ✓ 从列 '{col}' 提取到脱敏内容（{len(masked_content)}字符）", flush=True)
                                        break
                            
                            # 尝试提取脱敏判决
                            for col in possible_judge_cols:
                                if col in first_row.index:
                                    value = first_row[col]
                                    if pd.notna(value) and str(value).strip():
                                        masked_judge = str(value).strip()
                                        print(f"  ✓ 从列 '{col}' 提取到脱敏判决（{len(masked_judge)}字符）", flush=True)
                                        break
                            
                            # 如果还是找不到，尝试从原始案例数据中获取（但需要重新脱敏）
                            # 由于DeepSeek文件可能不存储脱敏内容，我们标记为None，让后续逻辑处理
                            unified_data_from_ds[case_id] = {
                                'questions': questions,
                                'masked_title': masked_title,
                                'masked_content': masked_content,  # 可能为None，需要重新脱敏
                                'masked_judge': masked_judge,  # 可能为None，需要重新脱敏
                            }
                            
                            if masked_content and masked_judge:
                                print(f"  ✓ 案例 {case_id}: 找到脱敏数据，将直接使用（不重新脱敏）", flush=True)
                            else:
                                print(f"  ⚠️ 案例 {case_id}: DeepSeek文件中未找到脱敏数据，将使用DeepSeek API重新脱敏", flush=True)
                            print(f"  ✓ 案例 {case_id}: 找到 {len(questions)} 个问题", flush=True)
                        else:
                            print(f"  ⚠️ 案例 {case_id}: 问题数量不足（{len(questions)}个），将重新生成", flush=True)
                    else:
                        print(f"  ⚠️ 案例 {case_id}: 未找到数据，将重新生成", flush=True)
                
                if unified_data_from_ds:
                    print(f"✓ 成功从DeepSeek文件加载 {len(unified_data_from_ds)} 个案例的问题数据", flush=True)
                    # 将统一数据赋值给unified_data，这样process_single_case会使用它
                    unified_data = unified_data_from_ds
                else:
                    print("⚠️ 未找到匹配的问题数据，将重新生成", flush=True)
                    unified_data_from_ds = None
            except Exception as e:
                print(f"✗ 读取DeepSeek结果文件失败: {str(e)}", flush=True)
                import traceback
                print(traceback.format_exc(), flush=True)
        else:
            print(f"⚠️ DeepSeek结果文件不存在: {ds_file}，将重新生成问题", flush=True)
    
    # 查找现有的结果文件（仅在非独立模式下）
    existing_df = None
    if not standalone:
        latest_result_file = find_latest_existing_file()
        if latest_result_file and os.path.exists(latest_result_file):
            print(f"找到现有结果文件: {latest_result_file}")
            existing_df = pd.read_excel(latest_result_file)
            print(f"现有文件包含 {len(existing_df)} 条记录，涉及 {existing_df['案例ID'].nunique()} 个案例")
        else:
            print("未找到现有结果文件，将创建新文件。")
    else:
        print("独立模式：结果将单独保存，不合并到现有文件。", flush=True)
    
    all_results = []
    total_cases = len(selected_cases)
    
    print(f"使用 {MAX_CONCURRENT_WORKERS} 个并发线程处理 {total_cases} 个案例", flush=True)
    print(flush=True)
    
    completed_count = 0
    batch_start_time = time.time()
    
    with SafeThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_WORKERS, total_cases)) as executor:
        future_to_case = {
            executor.submit(process_single_case, case_id, case, i+1, total_cases, model=model, 
                           existing_questions_data=existing_questions_data, unified_data=unified_data,
                           gpt_model=gpt_model, qwen_model=qwen_model, use_thinking=use_thinking): (i, case_id)
            for i, (case_id, case) in enumerate(selected_cases.items())
        }
        
        for future in concurrent.futures.as_completed(future_to_case):
            index, case_id = future_to_case[future]
            try:
                results = future.result()
                if results:
                    all_results.extend(results)
                    completed_count += 1
                    progress = (completed_count / total_cases) * 100
                    elapsed = time.time() - batch_start_time
                    avg_time = elapsed / completed_count if completed_count > 0 else 0
                    remaining = (total_cases - completed_count) * avg_time
                    print(f"[总体进度] {completed_count}/{total_cases} 个案例已完成 ({progress:.1f}%)", flush=True)
                    print(f"[总体进度] 已用时间: {elapsed:.1f}秒，预计剩余: {remaining:.1f}秒", flush=True)
                    print(flush=True)
            except Exception as e:
                completed_count += 1
                print(f"✗ 案例{index+1} ({case_id}) 处理异常: {str(e)}", flush=True)
                print(f"[总体进度] {completed_count}/{total_cases} 个案例已完成", flush=True)
                print(flush=True)
    
    if not all_results:
        print("错误：没有生成任何结果", flush=True)
        return
    
    new_result_df = pd.DataFrame(all_results)
    
    # 累加到现有结果
    final_df = new_result_df
    
    # 定义列顺序（在所有情况下都需要）
    columns_order = [
        '案例ID', '案例标题', '案例标题（脱敏）', '问题编号', '问题',
        '使用的模型', '脱敏API', '问题生成API', '评估API',
        'AI回答', 'AI回答Thinking',
        '总分', '百分制', '分档',
        '规范依据相关性_得分', '涵摄链条对齐度_得分',
        '价值衡量与同理心对齐度_得分', '关键事实与争点覆盖度_得分',
        '裁判结论与救济配置一致性_得分',
        '错误标记', '微小错误', '明显错误', '重大错误',
        '详细评价', '评价Thinking', '处理错误'
    ]
    
    if existing_df is not None:
        print(f"合并前检查：原有数据 {len(existing_df)} 行，新数据 {len(new_result_df)} 行", flush=True)
        
        # 确保新数据的DataFrame包含所有必要的列，避免合并时丢失列
        # 获取所有列的并集
        all_columns = set(existing_df.columns) | set(new_result_df.columns)
        
        # 确保两个DataFrame都有相同的列（缺失的列用NaN填充，后续会处理）
        for col in all_columns:
            if col not in new_result_df.columns:
                new_result_df[col] = None
            if col not in existing_df.columns:
                existing_df[col] = None
        
        # 统一数据类型，避免合并时类型不匹配导致数据丢失
        # 对于字符串列，确保都是字符串类型
        string_columns = ['案例ID', '案例标题', '案例标题（脱敏）', '问题', '使用的模型', '脱敏API', '问题生成API', '评估API',
                         'AI回答', 'AI回答Thinking', 
                         '分档', '错误标记', '微小错误', '明显错误', '重大错误', 
                         '详细评价', '评价Thinking', '处理错误']
        
        for col in string_columns:
            if col in all_columns:
                # 将NaN转换为空字符串，避免类型不匹配
                if col in existing_df.columns:
                    # 先填充NaN，再转换为字符串
                    existing_df[col] = existing_df[col].fillna('').astype(str)
                    existing_df[col] = existing_df[col].replace('nan', '').replace('None', '')
                if col in new_result_df.columns:
                    new_result_df[col] = new_result_df[col].fillna('').astype(str)
                    new_result_df[col] = new_result_df[col].replace('nan', '').replace('None', '')
        
        # 对于数值列，确保都是数值类型
        numeric_columns = ['问题编号', '总分', '百分制', 
                          '规范依据相关性_得分', '涵摄链条对齐度_得分',
                          '价值衡量与同理心对齐度_得分', '关键事实与争点覆盖度_得分',
                          '裁判结论与救济配置一致性_得分']
        
        for col in numeric_columns:
            if col in all_columns:
                if col in existing_df.columns:
                    existing_df[col] = pd.to_numeric(existing_df[col], errors='coerce')
                if col in new_result_df.columns:
                    new_result_df[col] = pd.to_numeric(new_result_df[col], errors='coerce')
        
        # 确保列顺序一致
        common_columns = sorted(list(all_columns))
        existing_df = existing_df[common_columns]
        new_result_df = new_result_df[common_columns]
        
        # 合并DataFrame
        final_df = pd.concat([existing_df, new_result_df], ignore_index=True)
        
        # 检查合并后是否有数据丢失
        original_count = len(existing_df)
        new_count = len(new_result_df)
        final_count = len(final_df)
        
        if final_count != original_count + new_count:
            print(f"⚠️ 警告：合并后行数不匹配！原有: {original_count}, 新增: {new_count}, 合并后: {final_count}", flush=True)
        
        # 检查原有数据的AI回答是否被保留
        if 'AI回答' in existing_df.columns:
            # 计算原有数据中非空的AI回答数量
            original_ai_series = existing_df['AI回答'].astype(str)
            original_ai_count = ((original_ai_series != '') & (original_ai_series != 'nan') & (original_ai_series != 'None')).sum()
            
            # 计算合并后原有行中非空的AI回答数量
            final_ai_series = final_df.iloc[:original_count]['AI回答'].astype(str)
            final_ai_count = ((final_ai_series != '') & (final_ai_series != 'nan') & (final_ai_series != 'None')).sum()
            
            if final_ai_count < original_ai_count:
                print(f"⚠️ 警告：原有数据的AI回答可能丢失！原有: {original_ai_count}, 合并后: {final_ai_count}", flush=True)
            else:
                print(f"✓ 原有数据的AI回答已保留：{original_ai_count} 个", flush=True)
    
    # 重新排列列的顺序
    final_columns = [col for col in columns_order if col in final_df.columns]
    other_columns = [col for col in final_df.columns if col not in final_columns]
    final_columns.extend(other_columns)
    
    final_df = final_df[final_columns]
    
    # 最终清理：将字符串列中的'nan'和'None'替换为空字符串
    for col in final_df.columns:
        if final_df[col].dtype == 'object':
            final_df[col] = final_df[col].astype(str).replace('nan', '').replace('None', '')
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # 确定模型显示名称（用于tab名称）
    if model == 'qwen':
        model_display_name = f'Qwen-{qwen_model.split("-")[-1].title()}'
        sheet_name = f'Qwen-{qwen_model.split("-")[-1].title()}'
    elif model == 'deepseek':
        # DeepSeek根据是否使用thinking模式显示不同名称
        if use_thinking:
            model_display_name = 'DeepSeek'
            sheet_name = 'DeepSeek'
        else:
            model_display_name = 'DeepSeek-NoThinking'
            sheet_name = 'DeepSeek-NoThinking'
    else:
        model_display_name = {
            'gpt4o': 'GPT-4o',
            'gemini': 'Gemini 2.5 Flash',
            'claude': 'Claude Opus 4'
        }.get(model, model.upper())
        sheet_name = model_display_name
    
    # 使用统一文件名（如果使用统一数据或DeepSeek文件，使用统一文件名；否则使用原有逻辑）
    if unified_data:
        # 统一评估模式：使用固定文件名，不同模型放在不同tab
        # 尝试从统一数据文件名或DeepSeek文件名中提取时间戳，或使用当前时间戳
        unified_file_timestamp = None
        
        # 优先从统一数据文件名提取
        if args.use_unified_data:
            import re
            match = re.search(r'(\d{8}_\d{6})', args.use_unified_data)
            if match:
                unified_file_timestamp = match.group(1)
        
        # 如果没有，尝试从DeepSeek文件名提取
        if not unified_file_timestamp and args.use_ds_questions:
            import re
            match = re.search(r'(\d{8}_\d{6})', args.use_ds_questions)
            if match:
                unified_file_timestamp = match.group(1)
            # 如果没有时间戳，使用文件名的一部分作为标识
            if not unified_file_timestamp:
                # 使用"108个案例"或类似的关键词
                import re
                match = re.search(r'(\d+)个案例', args.use_ds_questions)
                if match:
                    case_count = match.group(1)
                    unified_file_timestamp = f"{case_count}cases"
        
        if unified_file_timestamp:
            file_timestamp = unified_file_timestamp
        else:
            # 如果没有找到时间戳，使用当前时间戳
            file_timestamp = timestamp
        
        num_cases = len(final_df["案例ID"].unique())
        # 创建results文件夹（带时间戳，如果使用统一数据，所有模型共享同一个文件夹）
        # 尝试从统一数据文件名或DeepSeek文件名中提取时间戳作为文件夹名
        results_timestamp = None
        if args.use_unified_data:
            import re
            match = re.search(r'(\d{8}_\d{6})', args.use_unified_data)
            if match:
                results_timestamp = match.group(1)
        elif args.use_ds_questions:
            import re
            match = re.search(r'(\d{8}_\d{6})', args.use_ds_questions)
            if match:
                results_timestamp = match.group(1)
            else:
                # 如果DeepSeek文件名中没有时间戳，使用固定标识符（基于文件名）
                # 这样所有模型会使用同一个文件夹
                import hashlib
                file_hash = hashlib.md5(args.use_ds_questions.encode()).hexdigest()[:8]
                # 使用今天的日期 + 文件hash，确保同一天使用相同文件时共享文件夹
                today = datetime.now().strftime("%Y%m%d")
                results_timestamp = f"{today}_unified_{file_hash}"
        
        if results_timestamp:
            # 使用统一时间戳创建results文件夹（所有模型共享）
            results_dir = f'data/results_{results_timestamp}'
        else:
            # 使用当前时间戳创建新的results文件夹
            results_dir = f'data/results_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        
        os.makedirs(results_dir, exist_ok=True)
        output_file = f'{results_dir}/{num_cases}个案例_统一评估结果_{file_timestamp}.xlsx'
    elif standalone:
        # 独立模式：如果使用统一数据或DeepSeek问题，所有模型写入同一个文件的不同tab
        if unified_data or args.use_ds_questions:
            # 使用与unified_data相同的逻辑，确保所有模型写入同一个文件
            unified_file_timestamp = None
            
            # 优先从统一数据文件名提取
            if args.use_unified_data:
                import re
                match = re.search(r'(\d{8}_\d{6})', args.use_unified_data)
                if match:
                    unified_file_timestamp = match.group(1)
            
            # 如果没有，尝试从DeepSeek文件名提取
            if not unified_file_timestamp and args.use_ds_questions:
                import re
                match = re.search(r'(\d{8}_\d{6})', args.use_ds_questions)
                if match:
                    unified_file_timestamp = match.group(1)
                # 如果没有时间戳，使用文件名的一部分作为标识
                if not unified_file_timestamp:
                    import re
                    match = re.search(r'(\d+)个案例', args.use_ds_questions)
                    if match:
                        case_count = match.group(1)
                        unified_file_timestamp = f"{case_count}cases"
            
            if unified_file_timestamp:
                file_timestamp = unified_file_timestamp
            else:
                file_timestamp = timestamp
            
            # 使用固定的文件夹名，确保所有模型写入同一个文件夹
            results_timestamp = None
            if args.use_unified_data:
                import re
                match = re.search(r'(\d{8}_\d{6})', args.use_unified_data)
                if match:
                    results_timestamp = match.group(1)
            elif args.use_ds_questions:
                import re
                match = re.search(r'(\d{8}_\d{6})', args.use_ds_questions)
                if match:
                    results_timestamp = match.group(1)
                else:
                    # 使用固定标识符，确保所有模型使用同一个文件夹
                    results_timestamp = 'unified'
            
            if results_timestamp:
                results_dir = f'data/results_{results_timestamp}'
            else:
                results_dir = f'data/results_unified_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
            
            os.makedirs(results_dir, exist_ok=True)
            num_cases = len(new_result_df["案例ID"].unique())
            output_file = f'{results_dir}/{num_cases}个案例_统一评估结果_{file_timestamp}.xlsx'
        else:
            # 没有使用统一数据，每个模型独立保存
            results_dir = f'data/results_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
            os.makedirs(results_dir, exist_ok=True)
            output_file = f'{results_dir}/{model_display_name}_{len(new_result_df["案例ID"].unique())}个案例评估_{timestamp}.xlsx'
    else:
        # 合并模式：保存所有案例
        results_dir = f'data/results_{datetime.now().strftime("%Y%m%d_%H%M%S")}'
        os.makedirs(results_dir, exist_ok=True)
        output_file = f'{results_dir}/{len(final_df["案例ID"].unique())}个案例_新标准评估_完整版_{model_display_name}_{timestamp}.xlsx'
    
    print(flush=True)
    print('=' * 80, flush=True)
    print('保存结果...', flush=True)
    print('=' * 80, flush=True)
    
    # 如果使用统一数据或standalone模式且使用统一数据/DeepSeek问题，保存到多tab Excel文件
    if unified_data or (standalone and (unified_data or args.use_ds_questions)):
        # 检查文件是否已存在，读取所有现有的tab
        existing_sheets = {}
        if os.path.exists(output_file):
            print(f"文件已存在，读取现有文件: {output_file}", flush=True)
            try:
                excel_file = pd.ExcelFile(output_file)
                for sheet in excel_file.sheet_names:
                    existing_sheets[sheet] = pd.read_excel(output_file, sheet_name=sheet)
                print(f"  已读取 {len(existing_sheets)} 个现有tab: {list(existing_sheets.keys())}", flush=True)
            except Exception as e:
                print(f"  ⚠️ 读取现有文件失败: {str(e)}，将创建新文件", flush=True)
                existing_sheets = {}
        
        # 使用ExcelWriter保存多个tab（使用mode='w'重新写入所有sheet，确保所有tab都被保留）
        # 注意：多个进程并行写入时，需要文件锁机制
        print(f"保存到tab: {sheet_name}", flush=True)
        print(f"当前模型记录数: {len(final_df)} (新增: {len(new_result_df)})", flush=True)
        
        # 使用文件锁避免并发写入冲突（如果支持fcntl）
        if HAS_FCNTL:
            lock_file = output_file + '.lock'
            max_retries = 10
            retry_delay = 2  # 秒
            
            for attempt in range(max_retries):
                try:
                    # 尝试获取文件锁
                    with open(lock_file, 'w') as lock:
                        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                        
                        # 重新读取现有文件（可能在等待锁期间被其他进程更新）
                        existing_sheets_updated = {}
                        if os.path.exists(output_file):
                            try:
                                excel_file = pd.ExcelFile(output_file)
                                for sheet in excel_file.sheet_names:
                                    existing_sheets_updated[sheet] = pd.read_excel(output_file, sheet_name=sheet)
                            except Exception as e:
                                print(f"  ⚠️ 重新读取文件失败: {str(e)}", flush=True)
                                existing_sheets_updated = existing_sheets
                        
                        # 写入文件
                        with pd.ExcelWriter(output_file, engine='openpyxl', mode='w') as writer:
                            # 先写入所有现有的tab（除了当前模型的tab）
                            for sheet, df_sheet in existing_sheets_updated.items():
                                if sheet != sheet_name:
                                    df_sheet.to_excel(writer, sheet_name=sheet, index=False)
                                    print(f"  保留tab: {sheet} ({len(df_sheet)} 条记录)", flush=True)
                            
                            # 写入当前模型的数据
                            final_df.to_excel(writer, sheet_name=sheet_name, index=False)
                            print(f"  ✓ 已保存tab: {sheet_name} ({len(final_df)} 条记录)", flush=True)
                        
                        # 释放锁（文件关闭时自动释放）
                        break
                        
                except BlockingIOError:
                    # 文件被锁定，等待后重试
                    if attempt < max_retries - 1:
                        print(f"  ⚠️ 文件被锁定，等待 {retry_delay} 秒后重试 ({attempt + 1}/{max_retries})...", flush=True)
                        time.sleep(retry_delay)
                    else:
                        print(f"  ✗ 无法获取文件锁，已达到最大重试次数", flush=True)
                        raise
                except Exception as e:
                    print(f"  ✗ 保存文件时出错: {str(e)}", flush=True)
                    raise
        else:
            # 不支持文件锁，直接写入（不推荐并行运行）
            print(f"  ⚠️ 警告: 系统不支持文件锁，建议按顺序运行模型", flush=True)
            with pd.ExcelWriter(output_file, engine='openpyxl', mode='w') as writer:
                # 先写入所有现有的tab（除了当前模型的tab）
                for sheet, df_sheet in existing_sheets.items():
                    if sheet != sheet_name:
                        df_sheet.to_excel(writer, sheet_name=sheet, index=False)
                        print(f"  保留tab: {sheet} ({len(df_sheet)} 条记录)", flush=True)
                
                # 写入当前模型的数据
                final_df.to_excel(writer, sheet_name=sheet_name, index=False)
                print(f"  ✓ 已保存tab: {sheet_name} ({len(final_df)} 条记录)", flush=True)
            # 先写入所有现有的tab（除了当前模型的tab，因为会被当前数据替换）
            for sheet, df_sheet in existing_sheets.items():
                if sheet != sheet_name:  # 保留其他模型的tab
                    df_sheet.to_excel(writer, sheet_name=sheet, index=False)
                    print(f"  保留tab: {sheet} ({len(df_sheet)} 条记录)", flush=True)
            
            # 写入当前模型的数据（如果sheet已存在会被替换）
            final_df.to_excel(writer, sheet_name=sheet_name, index=False)
            print(f"  ✓ 已保存tab: {sheet_name} ({len(final_df)} 条记录)", flush=True)
        
        # 计算最终的tab数量
        total_tabs = len(existing_sheets) + (0 if sheet_name in existing_sheets else 1)
        print(f"✓ 所有模型结果已保存到: {output_file}", flush=True)
        print(f"  文件包含 {total_tabs} 个tab: {list(existing_sheets.keys()) + ([sheet_name] if sheet_name not in existing_sheets else [])}", flush=True)
        print(f"  结果目录: {results_dir}", flush=True)
    else:
        # 原有逻辑：单文件单tab
        print(f"累加新结果到现有文件...")
        print(f"累加后总记录数: {len(final_df)} (原有: {len(existing_df) if existing_df is not None else 0}, 新增: {len(new_result_df)})")
        
        final_df.to_excel(output_file, index=False, engine='openpyxl')
        print("✓ 保存完成！", flush=True)
        if 'results_dir' in locals():
            print(f"  结果目录: {results_dir}", flush=True)
    
    print(flush=True)
    
    print('=' * 80, flush=True)
    print('处理统计:', flush=True)
    print('=' * 80, flush=True)
    print(f"总案例数: {len(final_df['案例ID'].unique())}", flush=True)
    print(f"总问题数: {len(final_df)}", flush=True)

    print(flush=True)
    print("本次新增统计:")
    print(f"  新增案例数: {len(selected_cases)}")
    print(f"  新增问题数: {len(new_result_df)}")
    if '总分' in new_result_df.columns:
        avg_score_new = new_result_df['总分'].mean()
        avg_percentage_new = new_result_df['百分制'].mean()
        print(f"  新增平均总分: {avg_score_new:.2f}/20", flush=True)
        print(f"  新增平均百分制: {avg_percentage_new:.2f}", flush=True)
        print(f"  新增最高分: {new_result_df['总分'].max():.2f}/20 ({new_result_df['百分制'].max():.2f}分)", flush=True)
        print(f"  新增最低分: {new_result_df['总分'].min():.2f}/20 ({new_result_df['百分制'].min():.2f}分)", flush=True)

    print(flush=True)
    print("累计统计（所有案例）:")
    if '总分' in final_df.columns:
        avg_score_total = final_df['总分'].mean()
        avg_percentage_total = final_df['百分制'].mean()
        print(f"  累计平均总分: {avg_score_total:.2f}/20", flush=True)
        print(f"  累计平均百分制: {avg_percentage_total:.2f}", flush=True)
        print(f"  累计最高分: {final_df['总分'].max():.2f}/20 ({final_df['百分制'].max():.2f}分)", flush=True)
        print(f"  累计最低分: {final_df['总分'].min():.2f}/20 ({final_df['百分制'].min():.2f}分)", flush=True)
    
    print(flush=True)
    print("本次新增案例详情:")
    for case_id in target_case_ids:
        if case_id in selected_cases:
            case_results = new_result_df[new_result_df['案例ID'] == case_id]
            if len(case_results) > 0:
                case_title = case_results.iloc[0]['案例标题']
                avg_score = case_results['总分'].mean() if '总分' in case_results.columns else 0
                print(f"  {case_title[:40]}...: {len(case_results)}个问题, 平均分: {avg_score:.2f}/20", flush=True)
    
    if '错误标记' in new_result_df.columns:
        has_errors = new_result_df['错误标记'].notna() & (new_result_df['错误标记'] != '')
        error_count = has_errors.sum()
        if error_count > 0:
            print(flush=True)
            print(f"⚠️ 本次新增检测到错误的问题数: {error_count}/{len(new_result_df)}", flush=True)
    
    print(flush=True)
    print('=' * 80, flush=True)
    print('✓ 处理完成！', flush=True)
    print('=' * 80, flush=True)


if __name__ == '__main__':
    main()

