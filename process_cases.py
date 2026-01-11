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


def process_single_case(case_id, case, case_index, total_cases, model='deepseek', existing_questions_data=None, gpt_model='gpt-4o', qwen_model='qwen-max', use_thinking=True):
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
        
        if False:  # 保留代码结构，但总是执行脱敏
            # 执行脱敏处理
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
            """处理单个问题"""
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
            else:
                model_display_name = {
                    'deepseek': 'DeepSeek',
                    'gpt4o': 'GPT-4o',
                    'gemini': 'Gemini',
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
            
            try:
                # 生成AI回答（步骤3使用选择的模型）
                print(f"  [问题{q_num}/5] 开始生成AI回答...", flush=True)
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
                
                return result
                
            except Exception as e:
                print(f"  [问题{q_num}/5] ✗ 处理失败: {str(e)}", flush=True)
                result['处理错误'] = str(e)
                return result
        
        # 并行处理所有问题
        with SafeThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_WORKERS, len(questions))) as executor:
            futures = [
                executor.submit(process_single_question, q, i+1)
                for i, q in enumerate(questions)
            ]
            
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                    if result:
                        all_results.append(result)
                except Exception as e:
                    print(f"✗ 问题处理异常: {str(e)}", flush=True)
        
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
                        help='使用DeepSeek结果文件中的问题和脱敏数据（指定DeepSeek结果文件路径）')
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
    
    # 加载DeepSeek的问题和脱敏数据（如果指定）
    existing_questions_data = None
    if args.use_ds_questions:
        ds_file = args.use_ds_questions
        if os.path.exists(ds_file):
            print(f"从DeepSeek结果文件加载问题和脱敏数据: {ds_file}", flush=True)
            ds_df = pd.read_excel(ds_file)
            # 如果有"使用的模型"列，只提取DeepSeek处理的结果；否则假设所有数据都是DeepSeek的
            if '使用的模型' in ds_df.columns:
                ds_df = ds_df[ds_df['使用的模型'] == 'DeepSeek']
            # 如果没有DeepSeek数据，假设所有数据都是DeepSeek的（因为步骤1和2都用DeepSeek）
            if len(ds_df) == 0:
                ds_df = pd.read_excel(ds_file)
            
            existing_questions_data = {}
            for case_id in selected_cases.keys():
                case_data = ds_df[ds_df['案例ID'] == case_id]
                if len(case_data) > 0:
                    # 提取问题和脱敏数据
                    # 按问题编号排序，确保顺序一致
                    case_data = case_data.sort_values('问题编号')
                    questions = case_data['问题'].tolist()
                    if len(questions) >= 5:
                        questions = questions[:5]  # 只取前5个问题
                        first_row = case_data.iloc[0]
                        existing_questions_data[case_id] = {
                            'questions': questions,
                            'masked_title': first_row.get('案例标题（脱敏）', ''),
                            # 注意：脱敏内容（case_text_masked和judge_decision_masked）不在Excel中
                            # 但问题保持一致，脱敏会在process_single_case中重新执行（使用相同输入，结果应该一致）
                        }
                        print(f"  ✓ 案例 {case_id}: 找到 {len(questions)} 个问题", flush=True)
                    else:
                        print(f"  ⚠️ 案例 {case_id}: 问题数量不足（{len(questions)}个），将重新生成", flush=True)
                else:
                    print(f"  ⚠️ 案例 {case_id}: 未找到数据，将重新生成", flush=True)
            if existing_questions_data:
                print(f"成功加载 {len(existing_questions_data)} 个案例的问题数据", flush=True)
                print(f"注意：将使用相同的问题，但脱敏数据会重新生成（使用相同输入，结果应一致）", flush=True)
            else:
                print("⚠️ 未找到匹配的问题数据，将重新生成", flush=True)
                existing_questions_data = None
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
                           existing_questions_data=existing_questions_data, gpt_model=gpt_model, qwen_model=qwen_model, use_thinking=use_thinking): (i, case_id)
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
    model_suffix = f'_{model.upper()}' if model != 'deepseek' else ''
    
    if standalone:
        # 独立模式：只保存新处理的案例
        output_file = f'data/{model.upper()}_{len(new_result_df["案例ID"].unique())}个案例评估_{timestamp}.xlsx'
    else:
        # 合并模式：保存所有案例
        output_file = f'data/{len(final_df["案例ID"].unique())}个案例_新标准评估_完整版{model_suffix}_{timestamp}.xlsx'
    
    print(flush=True)
    print('=' * 80, flush=True)
    print('保存结果...', flush=True)
    print('=' * 80, flush=True)
    print(f"累加新结果到现有文件...")
    print(f"累加后总记录数: {len(final_df)} (原有: {len(existing_df) if existing_df is not None else 0}, 新增: {len(new_result_df)})")
    
    final_df.to_excel(output_file, index=False)
    print("✓ 保存完成！", flush=True)
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

