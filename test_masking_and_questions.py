"""
测试脚本：对前10个案例进行脱敏和问题生成
"""
import pandas as pd
import os
from datetime import datetime
import concurrent.futures
from utils.data_masking import DataMaskerAPI
from utils.ai_api import ai_api
from utils.process_cleanup import setup_signal_handlers, SafeThreadPoolExecutor
from config import MAX_CONCURRENT_WORKERS

# 设置信号处理器，确保中断时正确清理
setup_signal_handlers()


def process_single_case(case_data: dict, index: int, total: int) -> dict:
    """
    处理单个案例：脱敏 + 生成问题（并行处理）
    
    Args:
        case_data: 案例数据字典
        index: 案例索引
        total: 总案例数
        
    Returns:
        处理后的案例数据字典
    """
    import threading
    thread_id = threading.current_thread().name
    case_title = case_data.get('案例标题', '')
    print(f"[线程-{thread_id}] [{index+1}/{total}] 开始处理: {case_title[:30]}...")
    
    result = case_data.copy()
    
    try:
        # 1. 脱敏处理（每个线程创建独立的API实例，确保并行）
        print(f"[线程-{thread_id}] [{index+1}/{total}] → 进行脱敏处理...")
        masker = DataMaskerAPI()  # 每个线程独立实例，支持并行
        
        # 构建案例字典（用于脱敏API）
        case_dict = {
            'title': str(case_data.get('案例标题', '')),
            'case_text': str(case_data.get('案例内容', '')),
            'judge_decision': str(case_data.get('法官判决', ''))
        }
        
        # 执行脱敏（API调用，支持并发）
        masked_case = masker.mask_case_with_api(case_dict)
        
        # 将脱敏结果添加到结果字典
        result['案例标题（脱敏）'] = masked_case.get('title_masked', '')
        result['案例内容（脱敏）'] = masked_case.get('case_text_masked', '')
        result['法官判决（脱敏）'] = masked_case.get('judge_decision_masked', '')
        
        print(f"[线程-{thread_id}] [{index+1}/{total}] ✓ 脱敏完成")
        
        # 2. 生成问题（使用脱敏后的案例内容）
        print(f"[线程-{thread_id}] [{index+1}/{total}] → 生成5个问题...")
        # 使用统一的API接口，支持切换提供商
        
        # 使用脱敏后的案例内容生成问题
        masked_case_text = masked_case.get('case_text_masked', '')
        if not masked_case_text:
            # 如果脱敏失败，使用原始内容
            masked_case_text = case_dict['case_text']
            print(f"[线程-{thread_id}] [{index+1}/{total}] ⚠️ 脱敏内容为空，使用原始内容生成问题")
        
        questions = ai_api.generate_questions(masked_case_text, num_questions=5)
        
        # 将问题添加到结果字典
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
    print('批量处理：剩余案例脱敏和问题生成')
    print('=' * 80)
    print()
    
    # 读取Excel文件
    excel_file = 'data/1.3号案例内容提取_v3_日期补齐_20260103_154847_ID时间补齐_20260103_155150.xlsx'
    
    if not os.path.exists(excel_file):
        print(f"错误：找不到文件 {excel_file}")
        return
    
    print(f"读取文件: {excel_file}")
    df = pd.read_excel(excel_file)
    print(f"总行数: {len(df)}")
    print()
    
    # 处理所有剩余的案例（从第11个开始，即索引10开始）
    remaining_cases = df.iloc[10:].copy()  # 跳过前10个，处理剩余的
    print(f"总案例数: {len(df)}")
    print(f"已处理: 10 个")
    print(f"将处理剩余 {len(remaining_cases)} 个案例")
    print()
    
    # 准备案例数据列表
    cases_data = []
    for idx, row in remaining_cases.iterrows():
        case_dict = {
            '案例ID': row.get('案例ID', ''),
            '案例标题': row.get('案例标题', ''),
            '案例内容': row.get('案例内容', ''),
            '法官判决': row.get('法官判决', ''),
            '案例日期': row.get('案例日期', ''),
            '创建时间': row.get('创建时间', '')
        }
        cases_data.append(case_dict)
    
    # 并发处理案例（确保真正并行）
    print("开始并发处理...")
    print('-' * 80)
    
    results = []
    # 使用配置的最大并发数，但不超过案例数量
    max_workers = min(MAX_CONCURRENT_WORKERS, len(cases_data))
    print(f"使用 {max_workers} 个并发线程（完全并行处理）")
    print(f"注意：每个案例的处理（脱敏+问题生成）将在独立线程中并行执行")
    print()
    
    with SafeThreadPoolExecutor(max_workers=max_workers) as executor:
        # 提交所有任务
        total_cases = len(cases_data)
        future_to_index = {
            executor.submit(process_single_case, case_data, idx, total_cases): idx 
            for idx, case_data in enumerate(cases_data)
        }
        
        # 收集结果
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            try:
                result = future.result()
                results.append((index, result))
            except Exception as e:
                print(f"✗ 案例 {index+1} 处理异常: {str(e)}")
                # 添加错误结果
                error_result = cases_data[index].copy()
                error_result['处理错误'] = str(e)
                results.append((index, error_result))
    
    # 按原始顺序排序
    results.sort(key=lambda x: x[0])
    
    # 构建结果DataFrame
    result_rows = []
    for index, result in results:
        result_rows.append(result)
    
    result_df = pd.DataFrame(result_rows)
    
    # 重新排列列的顺序，使其更易读
    columns_order = [
        '案例ID', '案例标题', '案例标题（脱敏）',
        '案例内容', '案例内容（脱敏）',
        '法官判决', '法官判决（脱敏）',
        '案例日期', '创建时间',
        '问题1', '问题2', '问题3', '问题4', '问题5'
    ]
    
    # 只包含存在的列
    final_columns = [col for col in columns_order if col in result_df.columns]
    # 添加其他列（如处理错误）
    other_columns = [col for col in result_df.columns if col not in final_columns]
    final_columns.extend(other_columns)
    
    result_df = result_df[final_columns]
    
    # 保存结果
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'data/剩余案例_脱敏和问题生成_{timestamp}.xlsx'
    
    print()
    print('=' * 80)
    print('保存结果...')
    print('=' * 80)
    print(f"保存到: {output_file}")
    
    result_df.to_excel(output_file, index=False)
    print("✓ 保存完成！")
    print()
    
    # 显示统计信息
    print('=' * 80)
    print('处理统计:')
    print('=' * 80)
    print(f"总案例数: {len(result_df)}")
    
    # 统计脱敏成功数
    has_masked_title = result_df['案例标题（脱敏）'].notna() & (result_df['案例标题（脱敏）'] != '')
    has_masked_content = result_df['案例内容（脱敏）'].notna() & (result_df['案例内容（脱敏）'] != '')
    has_masked_judge = result_df['法官判决（脱敏）'].notna() & (result_df['法官判决（脱敏）'] != '')
    
    print(f"脱敏成功:")
    print(f"  标题脱敏: {has_masked_title.sum()}/{len(result_df)}")
    print(f"  内容脱敏: {has_masked_content.sum()}/{len(result_df)}")
    print(f"  判决脱敏: {has_masked_judge.sum()}/{len(result_df)}")
    
    # 统计问题生成成功数
    has_questions = result_df['问题1'].notna() & (result_df['问题1'] != '')
    print(f"问题生成成功: {has_questions.sum()}/{len(result_df)}")
    
    # 显示错误
    if '处理错误' in result_df.columns:
        errors = result_df[result_df['处理错误'].notna()]
        if len(errors) > 0:
            print(f"处理错误: {len(errors)} 个")
            for idx, row in errors.iterrows():
                print(f"  - [{idx+1}] {row.get('案例标题', '')[:30]}...: {row['处理错误']}")
    
    print()
    print('=' * 80)
    print('✓ 处理完成！')
    print('=' * 80)


if __name__ == '__main__':
    main()

