"""
修复有问题的案例：重新处理脱敏和问题生成
"""
import pandas as pd
import os
from datetime import datetime
import concurrent.futures
from utils.data_masking import DataMaskerAPI
from utils.ai_api import ai_api
from utils.process_cleanup import setup_signal_handlers, SafeThreadPoolExecutor
from config import MAX_CONCURRENT_WORKERS
import threading

# 设置信号处理器，确保中断时正确清理
setup_signal_handlers()


def process_single_case(case_data: dict, index: int, total: int) -> dict:
    """
    处理单个案例：脱敏 + 生成问题（并行处理）
    """
    import threading
    thread_id = threading.current_thread().name
    case_title = case_data.get('案例标题', '')
    print(f"[线程-{thread_id}] [{index+1}/{total}] 开始处理: {case_title[:30]}...")
    
    result = case_data.copy()
    
    try:
        # 1. 脱敏处理
        print(f"[线程-{thread_id}] [{index+1}/{total}] → 进行脱敏处理...")
        masker = DataMaskerAPI()
        
        case_dict = {
            'title': str(case_data.get('案例标题', '')),
            'case_text': str(case_data.get('案例内容', '')),
            'judge_decision': str(case_data.get('法官判决', ''))
        }
        
        masked_case = masker.mask_case_with_api(case_dict)
        
        result['案例标题（脱敏）'] = masked_case.get('title_masked', '')
        result['案例内容（脱敏）'] = masked_case.get('case_text_masked', '')
        result['法官判决（脱敏）'] = masked_case.get('judge_decision_masked', '')
        
        print(f"[线程-{thread_id}] [{index+1}/{total}] ✓ 脱敏完成")
        
        # 2. 生成问题
        print(f"[线程-{thread_id}] [{index+1}/{total}] → 生成5个问题...")
        
        masked_case_text = masked_case.get('case_text_masked', '')
        if not masked_case_text:
            masked_case_text = case_dict['case_text']
            print(f"[线程-{thread_id}] [{index+1}/{total}] ⚠️ 脱敏内容为空，使用原始内容生成问题")
        
        questions = ai_api.generate_questions(masked_case_text, num_questions=5)
        
        for i, question in enumerate(questions, 1):
            result[f'问题{i}'] = question
        
        print(f"[线程-{thread_id}] [{index+1}/{total}] ✓ 问题生成完成（共{len(questions)}个）")
        print(f"[线程-{thread_id}] [{index+1}/{total}] ✓ 完成: {case_title[:30]}...")
        
    except Exception as e:
        print(f"[线程-{thread_id}] [{index+1}/{total}] ✗ 处理失败: {case_title[:30]}... - {str(e)}")
        result['处理错误'] = str(e)
    
    return result


def main():
    """主函数"""
    print('=' * 80)
    print('修复有问题的案例')
    print('=' * 80)
    print()
    
    # 读取原始数据文件
    excel_file = 'data/1.3号案例内容提取_v3_日期补齐_20260103_154847_ID时间补齐_20260103_155150.xlsx'
    df_original = pd.read_excel(excel_file)
    
    # 读取已处理的结果文件
    import glob
    result_files = sorted(glob.glob('data/剩余案例_脱敏和问题生成_*.xlsx'), reverse=True)
    if not result_files:
        print("错误：找不到已处理的结果文件")
        return
    
    result_file = result_files[0]
    print(f"读取结果文件: {result_file}")
    df_result = pd.read_excel(result_file)
    print(f"已处理案例数: {len(df_result)}")
    print()
    
    # 找出有问题的案例
    problem_indices = []
    problem_cases_data = []
    
    for idx, row in df_result.iterrows():
        case_id = row.get('案例ID', '')
        case_title = str(row.get('案例标题', ''))
        issues = []
        
        # 检查脱敏
        if pd.isna(row.get('案例标题（脱敏）')) or str(row.get('案例标题（脱敏）', '')).strip() == '':
            issues.append('标题脱敏缺失')
        if pd.isna(row.get('案例内容（脱敏）')) or str(row.get('案例内容（脱敏）', '')).strip() == '':
            issues.append('内容脱敏缺失')
        if pd.isna(row.get('法官判决（脱敏）')) or str(row.get('法官判决（脱敏）', '')).strip() == '':
            issues.append('判决脱敏缺失')
        
        # 检查问题
        question_count = 0
        for i in range(1, 6):
            q_col = f'问题{i}'
            if q_col in df_result.columns and pd.notna(row[q_col]):
                question = str(row[q_col]).strip()
                if question and question != 'nan' and len(question) >= 10:
                    question_count += 1
        
        if question_count < 5 or issues:
            problem_indices.append(idx)
            # 从原始数据中获取案例信息
            original_row = df_original[df_original['案例ID'] == case_id]
            if len(original_row) > 0:
                original_row = original_row.iloc[0]
                case_data = {
                    '案例ID': original_row.get('案例ID', ''),
                    '案例标题': original_row.get('案例标题', ''),
                    '案例内容': original_row.get('案例内容', ''),
                    '法官判决': original_row.get('法官判决', ''),
                    '案例日期': original_row.get('案例日期', ''),
                    '创建时间': original_row.get('创建时间', '')
                }
                problem_cases_data.append((idx, case_data, issues, question_count))
                print(f"发现问题案例 [{idx+1}]: {case_title[:40]}...")
                print(f"  问题: {', '.join(issues)} | 问题数: {question_count}/5")
    
    print()
    print(f"共发现 {len(problem_cases_data)} 个有问题的案例需要重新处理")
    print()
    
    if not problem_cases_data:
        print("✓ 没有发现需要修复的案例")
        return
    
    # 并发处理有问题的案例
    print("开始重新处理有问题的案例...")
    print('-' * 80)
    
    results = []
    max_workers = min(MAX_CONCURRENT_WORKERS, len(problem_cases_data))
    print(f"使用 {max_workers} 个并发线程")
    print()
    
    with SafeThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_info = {
            executor.submit(process_single_case, case_data, idx, len(problem_cases_data)): (original_idx, case_data, issues, question_count)
            for idx, (original_idx, case_data, issues, question_count) in enumerate(problem_cases_data)
        }
        
        for future in concurrent.futures.as_completed(future_to_info):
            original_idx, case_data, issues, question_count = future_to_info[future]
            try:
                result = future.result()
                results.append((original_idx, result))
            except Exception as e:
                print(f"✗ 案例 {original_idx+1} 处理异常: {str(e)}")
                error_result = case_data.copy()
                error_result['处理错误'] = str(e)
                results.append((original_idx, error_result))
    
    # 按原始索引排序
    results.sort(key=lambda x: x[0])
    
    # 更新结果DataFrame
    for original_idx, result in results:
        for col, value in result.items():
            if col in df_result.columns:
                df_result.at[original_idx, col] = value
    
    # 保存修复后的文件
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'data/剩余案例_脱敏和问题生成_修复_{timestamp}.xlsx'
    
    print()
    print('=' * 80)
    print('保存修复后的结果...')
    print('=' * 80)
    print(f"保存到: {output_file}")
    
    df_result.to_excel(output_file, index=False)
    print("✓ 保存完成！")
    print()
    
    # 再次检查
    print('=' * 80)
    print('修复后检查:')
    print('=' * 80)
    
    complete_cases = 0
    for idx, row in df_result.iterrows():
        case_complete = True
        question_count = 0
        
        for i in range(1, 6):
            q_col = f'问题{i}'
            if q_col in df_result.columns and pd.notna(row[q_col]):
                question = str(row[q_col]).strip()
                if question and question != 'nan' and len(question) >= 10:
                    question_count += 1
        
        if question_count < 5:
            case_complete = False
        
        if case_complete:
            complete_cases += 1
    
    print(f"完整案例数: {complete_cases}/{len(df_result)} ({complete_cases/len(df_result)*100:.1f}%)")
    
    if complete_cases == len(df_result):
        print("✓ 所有案例都已修复完成！")
    else:
        print(f"⚠️ 仍有 {len(df_result) - complete_cases} 个案例存在问题")


if __name__ == '__main__':
    main()

