#!/usr/bin/env python3
"""
测试脚本：为 case_20260103_155150_3 生成20个问题，使用GPT-o3回答
复用 process_cases.py 的逻辑
支持并行处理和实时日志输出
"""
import pandas as pd
import os
import glob
import json
import sys
import threading
import traceback
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from utils.ai_api import UnifiedAIAPI
from utils.data_masking import DataMaskerAPI
from utils.unified_model_api import UnifiedModelAPI

# 线程安全的打印锁
print_lock = threading.Lock()

def safe_print(*args, **kwargs):
    """线程安全的打印函数"""
    with print_lock:
        print(*args, **kwargs)
        sys.stdout.flush()

def load_case_data(case_id):
    """从现有数据中加载案例"""
    # 方法1: 从cases.json加载
    cases_json_file = 'data/cases/cases.json'
    if os.path.exists(cases_json_file):
        try:
            with open(cases_json_file, 'r', encoding='utf-8') as f:
                cases_data = json.load(f)
            
            # 查找案例
            if isinstance(cases_data, list):
                for case in cases_data:
                    if case.get('case_id') == case_id:
                        return {
                            'case_id': case_id,
                            'title': case.get('title', ''),
                            'content': case.get('case_text', case.get('content', '')),
                            'judge_decision': case.get('judge_decision', '')
                        }
            elif isinstance(cases_data, dict):
                if case_id in cases_data:
                    case = cases_data[case_id]
                    return {
                        'case_id': case_id,
                        'title': case.get('title', ''),
                        'content': case.get('case_text', case.get('content', '')),
                        'judge_decision': case.get('judge_decision', '')
                    }
        except Exception as e:
            print(f"从cases.json加载失败: {e}")
    
    # 方法2: 从现有结果文件中提取（但只有脱敏后的内容）
    possible_files = [
        'data/results_20260111_151734/DEEPSEEK_20个案例评估_20260111_144155.xlsx',
        'data/results_20260111_151734/GPT-o3_20个案例评估_20260111_144315.xlsx',
    ]
    
    for file_path in possible_files:
        if os.path.exists(file_path):
            try:
                df = pd.read_excel(file_path, engine='openpyxl')
                case_data = df[df['案例ID'] == case_id]
                if len(case_data) > 0:
                    case_title = case_data['案例标题'].iloc[0]
                    # 注意：这里只能获取脱敏后的内容，但可以用于测试
                    masked_content = case_data.get('案例内容（脱敏）', pd.Series([''])).iloc[0] if '案例内容（脱敏）' in case_data.columns else ''
                    masked_judge = case_data.get('法官判决（脱敏）', pd.Series([''])).iloc[0] if '法官判决（脱敏）' in case_data.columns else ''
                    
                    if masked_content:
                        return {
                            'case_id': case_id,
                            'title': case_title,
                            'content': masked_content,  # 注意：这是脱敏后的
                            'judge_decision': masked_judge,
                            'is_masked': True
                        }
            except Exception as e:
                print(f"从{file_path}加载失败: {e}")
    
    return None

def main():
    # 去掉4个敏感案例，从最开始的案例列表（90-99）中找4个替代
    # 敏感案例：case_20260103_155150_0, 1, 2, 3（虐待、强奸、杀人、强奸猥亵儿童）
    # 替代案例：从90-99中选择，使用95-98（因为90-94是之前5个案例测试过的）
    sensitive_cases = [
        'case_20260103_155150_0',  # 牟某虐待案
        'case_20260103_155150_1',  # 张某强奸案
        'case_20260103_155150_2',  # 许某某故意杀人案
        'case_20260103_155150_3',  # 任某强奸、猥亵儿童案
    ]
    
    replacement_cases = [
        'case_20251230_134952_95',  # 从最开始的案例列表中选择
        'case_20251230_134952_96',
        'case_20251230_134952_97',
        'case_20251230_134952_98',
    ]
    
    # 要处理的案例列表（替换敏感案例）
    case_ids = replacement_cases
    
    safe_print("=" * 80)
    safe_print(f"测试脚本：为 {len(case_ids)} 个案例生成20个问题，使用GPT-o3回答（并行模式）")
    safe_print(f"已排除敏感案例: {', '.join(sensitive_cases)}")
    safe_print(f"使用替代案例: {', '.join(case_ids)}")
    safe_print("=" * 80)
    
    # 1. 加载案例数据
    safe_print("\n步骤1: 加载案例数据...")
    case = load_case_data(case_id)
    
    if not case:
        safe_print(f"❌ 未找到案例 {case_id} 的数据")
        return
    
    case_title = case.get('title', '')
    case_text = case.get('content', case.get('case_text', ''))
    judge_decision = case.get('judge_decision', '')
    is_masked = case.get('is_masked', False)
    
    safe_print(f"✓ 案例加载成功: {case_title}")
    if is_masked:
        safe_print("⚠️  注意：使用的是脱敏后的内容（将跳过脱敏步骤）")
    
    # 2. 脱敏处理（如果内容未脱敏）
    if not is_masked:
        safe_print("\n步骤2: 脱敏处理...")
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
        safe_print("✓ 脱敏完成")
    else:
        # 如果已经是脱敏后的内容，直接使用
        safe_print("\n步骤2: 跳过脱敏（内容已脱敏）")
        masked_title = case_title
        masked_content = case_text
        masked_judge = judge_decision
    
    # 3. 生成20个问题（使用DeepSeek API）
    safe_print("\n步骤3: 生成20个问题（使用DeepSeek API）...")
    deepseek_api = UnifiedAIAPI(provider='deepseek')
    questions = deepseek_api.generate_questions(masked_content, num_questions=20)
    safe_print(f"✓ 问题生成完成（共{len(questions)}个）")
    
    # 4. 使用GPT-o3并行回答所有问题
    safe_print("\n步骤4: 使用GPT-o3并行回答所有问题...")
    safe_print(f"并行线程数: 20（可同时处理20个问题）")
    
    results = []
    results_lock = threading.Lock()
    empty_count = 0
    empty_count_lock = threading.Lock()
    completed_count = 0
    completed_count_lock = threading.Lock()
    
    # 输出文件路径（提前确定，便于实时保存）
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'data/GPT-o3_{case_id}_20个问题测试_{timestamp}.xlsx'
    
    def save_results():
        """保存当前结果（线程安全）"""
        try:
            with results_lock:
                if len(results) == 0:
                    return
                # 按问题编号排序
                sorted_results = sorted(results, key=lambda x: x['问题编号'])
                df_results = pd.DataFrame(sorted_results)
                df_results.to_excel(output_file, index=False, engine='openpyxl')
                safe_print(f"[保存] 已保存 {len(results)} 条结果到: {output_file}")
        except Exception as e:
            safe_print(f"[保存错误] 保存结果失败: {str(e)}")
            safe_print(f"[保存错误详情] {traceback.format_exc()}")
    
    def process_question(i, question):
        """处理单个问题的函数"""
        nonlocal empty_count, completed_count
        
        safe_print(f"[开始] 问题 {i}/20: {question[:60]}...")
        
        try:
            # 为每个线程创建独立的API实例，避免线程安全问题
            gpt_o3_api = UnifiedModelAPI(model='o3-2025-04-16')
            
            # 生成AI回答（捕获详细错误）
            try:
                ai_answer = gpt_o3_api.analyze_case(masked_content, question=question)
                
                # 验证返回结果
                if ai_answer is None:
                    raise ValueError("API返回None，可能是请求失败或响应格式错误")
                    
            except KeyError as ke:
                # 如果是KeyError，可能是API响应格式问题
                error_detail = f"KeyError: {ke}\n可能是API响应格式异常，缺少必要的字段（如'content'）"
                safe_print(f"[API错误] 问题 {i}/20: {error_detail}")
                safe_print(f"[API错误] 这通常意味着API响应中缺少预期的字段，可能是内容过滤器拦截或响应格式变化")
                raise Exception(f"API响应格式错误 - KeyError: {ke}") from ke
            except AttributeError as ae:
                # AttributeError可能是访问不存在的属性
                error_detail = f"AttributeError: {ae}\n可能是API响应对象结构异常"
                safe_print(f"[API错误] 问题 {i}/20: {error_detail}")
                raise Exception(f"API响应结构错误 - AttributeError: {ae}") from ae
            except Exception as api_e:
                # 包装API错误，添加更多上下文
                error_detail = f"API调用错误: {type(api_e).__name__}: {str(api_e)}"
                safe_print(f"[API错误] 问题 {i}/20: {error_detail}")
                # 如果是requests相关错误，尝试获取更多信息
                if hasattr(api_e, 'response'):
                    try:
                        resp = api_e.response
                        safe_print(f"[API错误] 响应状态码: {getattr(resp, 'status_code', 'N/A')}")
                        safe_print(f"[API错误] 响应内容: {getattr(resp, 'text', 'N/A')[:200]}")
                    except:
                        pass
                raise Exception(f"API调用失败 - {type(api_e).__name__}: {str(api_e)}") from api_e
            
            is_empty = not ai_answer or ai_answer.strip() == '' or ai_answer.lower() == 'nan'
            
            if is_empty:
                with empty_count_lock:
                    empty_count += 1
                safe_print(f"[完成] 问题 {i}/20: ⚠️  空回答")
            else:
                safe_print(f"[完成] 问题 {i}/20: ✓ 回答生成完成（{len(ai_answer)}字符）")
            
            result = {
                '案例ID': case_id,
                '案例标题': case_title,
                '案例标题（脱敏）': masked_title,
                '问题编号': i,
                '问题': question,
                'AI回答': ai_answer if ai_answer else '',
                '使用的模型': 'GPT-o3',
                '是否空回答': '是' if is_empty else '否'
            }
            
            with results_lock:
                results.append(result)
            
            with completed_count_lock:
                completed_count += 1
                safe_print(f"[进度] {completed_count}/20 问题已完成")
            
            # 每次成功完成就保存结果
            save_results()
            
            return result
            
        except Exception as e:
            with empty_count_lock:
                empty_count += 1
            with completed_count_lock:
                completed_count += 1
            
            # 输出详细错误信息
            error_msg = str(e)
            error_type = type(e).__name__
            error_traceback = traceback.format_exc()
            
            # 尝试获取更多错误上下文信息
            error_context = {
                'error_type': error_type,
                'error_message': error_msg,
                'question_number': i,
                'question_preview': question[:100] if question else '',
                'case_id': case_id,
                'traceback': error_traceback
            }
            
            # 如果是KeyError，尝试获取更多信息
            if isinstance(e, KeyError):
                error_context['missing_key'] = str(e)
                error_context['error_detail'] = f"缺少键: {e}"
            
            # 如果是API相关错误，尝试获取响应信息
            if 'API' in error_type or 'request' in error_msg.lower() or 'timeout' in error_msg.lower():
                error_context['error_category'] = 'API错误'
                if hasattr(e, 'response'):
                    try:
                        error_context['response_status'] = getattr(e.response, 'status_code', None)
                        error_context['response_text'] = getattr(e.response, 'text', '')[:500]
                    except:
                        pass
            
            safe_print(f"[错误] 问题 {i}/20: ❌ {error_type}: {error_msg}")
            safe_print(f"[错误上下文] 问题编号: {i}, 案例ID: {case_id}")
            safe_print(f"[错误详情] {error_traceback}")
            
            # 如果traceback太长，只显示关键部分
            traceback_lines = error_traceback.split('\n')
            if len(traceback_lines) > 20:
                safe_print(f"[错误详情-简化] ... (共{len(traceback_lines)}行，显示最后10行)")
                for line in traceback_lines[-10:]:
                    safe_print(f"  {line}")
            
            safe_print(f"[进度] {completed_count}/20 问题已完成")
            
            result = {
                '案例ID': case_id,
                '案例标题': case_title,
                '案例标题（脱敏）': masked_title,
                '问题编号': i,
                '问题': question,
                'AI回答': '',
                '使用的模型': 'GPT-o3',
                '处理错误': f"{error_type}: {error_msg}",
                '错误类型': error_type,
                '错误详情': error_traceback[:1000] if len(error_traceback) > 1000 else error_traceback,
                '错误上下文': json.dumps(error_context, ensure_ascii=False, indent=2)[:500],
                '是否空回答': '是'
            }
            
            with results_lock:
                results.append(result)
            
            # 即使失败也保存结果
            save_results()
            
            return result
    
    # 使用线程池并行处理
    with ThreadPoolExecutor(max_workers=20) as executor:
        # 提交所有任务
        future_to_question = {
            executor.submit(process_question, i+1, question): (i+1, question) 
            for i, question in enumerate(questions)
        }
        
        # 等待所有任务完成（实时显示进度）
        for future in as_completed(future_to_question):
            i, question = future_to_question[future]
            try:
                future.result()  # 获取结果（已在函数内部处理）
            except Exception as e:
                error_traceback = traceback.format_exc()
                safe_print(f"[异常] 问题 {i}/20 处理异常: {type(e).__name__}: {str(e)}")
                safe_print(f"[异常详情] {error_traceback}")
                # 确保异常也被记录
                save_results()
    
    # 按问题编号排序结果
    results.sort(key=lambda x: x['问题编号'])
    
    # 5. 统计结果
    safe_print("\n" + "=" * 80)
    safe_print("统计结果")
    safe_print("=" * 80)
    safe_print(f"总问题数: {len(questions)}")
    safe_print(f"空回答数: {empty_count}")
    safe_print(f"空回答率: {empty_count/len(questions)*100:.1f}%")
    safe_print(f"正常回答数: {len(questions) - empty_count}")
    
    # 6. 最终保存结果（确保所有结果都已保存）
    safe_print("\n步骤5: 最终保存结果...")
    save_results()  # 再次保存确保完整性
    safe_print(f"✓ 最终结果已保存到: {output_file}")
    
    # 7. 显示空回答的问题
    if empty_count > 0:
        safe_print("\n" + "=" * 80)
        safe_print("空回答的问题列表")
        safe_print("=" * 80)
        empty_questions = df_results[df_results['是否空回答'] == '是']
        for idx, row in empty_questions.iterrows():
            safe_print(f"\n问题 {row['问题编号']}: {row['问题']}")
            if '处理错误' in row and pd.notna(row['处理错误']):
                safe_print(f"  错误: {row['处理错误']}")

if __name__ == '__main__':
    main()
