"""
批量评估脚本：读取Excel，生成AI回答，进行评分
"""
import pandas as pd
import os
from datetime import datetime
import concurrent.futures
from utils.ai_api import ai_api
from utils.evaluator import AnswerEvaluator
from utils.process_cleanup import setup_signal_handlers, SafeThreadPoolExecutor
from config import MAX_CONCURRENT_WORKERS

# 设置信号处理器，确保中断时正确清理
setup_signal_handlers()


def process_single_question(case_data: dict, question_num: int, index: int, total: int) -> dict:
    """
    处理单个问题：生成AI回答 + 评分
    
    Args:
        case_data: 案例数据字典
        question_num: 问题编号（1-5）
        index: 当前问题索引
        total: 总问题数
        
    Returns:
        处理结果字典
    """
    import threading
    thread_id = threading.current_thread().name
    
    question_col = f'问题{question_num}'
    
    question = str(case_data.get(question_col, '')).strip()
    judge_decision_masked = str(case_data.get('法官判决（脱敏）', '')).strip()
    case_text_masked = str(case_data.get('案例内容（脱敏）', '')).strip()
    
    if not question or question == 'nan':
        return None
    
    # 如果没有脱敏的法官判决，尝试使用未脱敏的
    if not judge_decision_masked or judge_decision_masked == 'nan':
        judge_decision_masked = str(case_data.get('法官判决', '')).strip()
    
    case_title = case_data.get('案例标题', '')[:30]
    print(f"[线程-{thread_id}] [{index+1}/{total}] 处理: {case_title}... - 问题{question_num}")
    
    result = {
        '案例ID': case_data.get('案例ID', ''),
        '案例标题': case_data.get('案例标题', ''),
        '问题编号': question_num,
        '问题': question
    }
    
    try:
        # 1. 生成AI回答
        print(f"[线程-{thread_id}] [{index+1}/{total}] → 生成AI回答...")
        ai_answer = ai_api.analyze_case(case_text_masked, question=question)
        result['AI回答'] = ai_answer
        print(f"[线程-{thread_id}] [{index+1}/{total}] ✓ AI回答生成完成（长度：{len(ai_answer)}字符）")
        
        # 2. 进行评分（直接与整个法官判决对比）
        print(f"[线程-{thread_id}] [{index+1}/{total}] → 进行评分（与整个法官判决对比）...")
        evaluator = AnswerEvaluator()
        evaluation = evaluator.evaluate_answer(
            ai_answer=ai_answer,
            judge_decision=judge_decision_masked,  # 使用整个法官判决作为对比标准
            question=question,
            case_text=case_text_masked
        )
        
        result['总分'] = evaluation['总分']
        result['百分制'] = evaluation['百分制']
        result['分档'] = evaluation['分档']
        
        # 添加各维度得分
        for dimension, score in evaluation['各维度得分'].items():
            result[f'{dimension}_得分'] = score
        
        result['详细评价'] = evaluation['详细评价']
        
        # 错误标记（按级别分类）
        error_mark = evaluation.get('错误标记', '')
        error_details = evaluation.get('错误详情', {})
        result['错误标记'] = error_mark
        # 保留错误详情（如果需要）
        if error_details:
            if error_details.get('微小错误'):
                result['微小错误'] = '; '.join(error_details['微小错误'])
            if error_details.get('明显错误'):
                result['明显错误'] = '; '.join(error_details['明显错误'])
            if error_details.get('重大错误'):
                result['重大错误'] = '; '.join(error_details['重大错误'])
        
        print(f"[线程-{thread_id}] [{index+1}/{total}] ✓ 评分完成（总分：{evaluation['总分']}/20，百分制：{evaluation['百分制']}）")
        
    except Exception as e:
        print(f"[线程-{thread_id}] [{index+1}/{total}] ✗ 处理失败: {str(e)}")
        result['处理错误'] = str(e)
    
    return result


def main():
    """主函数"""
    print('=' * 80)
    print('批量评估：生成AI回答并评分')
    print('=' * 80)
    print()
    
    # 读取Excel文件
    excel_file = 'data/测试_问题与法官回答_20260103_230911.xlsx'
    
    if not os.path.exists(excel_file):
        print(f"错误：找不到文件 {excel_file}")
        return
    
    print(f"读取文件: {excel_file}")
    df = pd.read_excel(excel_file)
    print(f"总案例数: {len(df)}")
    print()
    
    # 准备所有问题（每个案例5个问题）
    all_questions = []
    for idx, row in df.iterrows():
        case_dict = row.to_dict()
        for q_num in range(1, 6):
            question = str(case_dict.get(f'问题{q_num}', '')).strip()
            if question and question != 'nan':
                all_questions.append((case_dict, q_num))
    
    print(f"总问题数: {len(all_questions)}")
    print()
    
    # 并发处理所有问题
    print("开始并发处理...")
    print('-' * 80)
    
    results = []
    max_workers = min(MAX_CONCURRENT_WORKERS, len(all_questions))
    print(f"使用 {max_workers} 个并发线程")
    print()
    
    with SafeThreadPoolExecutor(max_workers=max_workers) as executor:
        total = len(all_questions)
        future_to_index = {
            executor.submit(process_single_question, case_data, q_num, idx, total): idx
            for idx, (case_data, q_num) in enumerate(all_questions)
        }
        
        for future in concurrent.futures.as_completed(future_to_index):
            index = future_to_index[future]
            try:
                result = future.result()
                if result:
                    results.append(result)
            except Exception as e:
                print(f"✗ 问题 {index+1} 处理异常: {str(e)}")
    
    # 构建结果DataFrame
    result_df = pd.DataFrame(results)
    
    # 重新排列列的顺序
    columns_order = [
        '案例ID', '案例标题', '问题编号', '问题', '法官回答', 'AI回答',
        '总分', '百分制', '分档',
        '规范依据相关性_得分', '涵摄链条对齐度_得分',
        '价值衡量与同理心对齐度_得分', '关键事实与争点覆盖度_得分',
        '裁判结论与救济配置一致性_得分',
        '错误标记', '微小错误', '明显错误', '重大错误',
        '详细评价', '处理错误'
    ]
    
    # 只包含存在的列
    final_columns = [col for col in columns_order if col in result_df.columns]
    # 添加其他列
    other_columns = [col for col in result_df.columns if col not in final_columns]
    final_columns.extend(other_columns)
    
    result_df = result_df[final_columns]
    
    # 保存结果
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'data/评估结果_{timestamp}.xlsx'
    
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
    print(f"总问题数: {len(result_df)}")
    
    if '总分' in result_df.columns:
        avg_score = result_df['总分'].mean()
        avg_percentage = result_df['百分制'].mean()
        print(f"平均总分: {avg_score:.2f}/20")
        print(f"平均百分制: {avg_percentage:.2f}")
        print(f"最高分: {result_df['总分'].max():.2f}/20 ({result_df['百分制'].max():.2f}分)")
        print(f"最低分: {result_df['总分'].min():.2f}/20 ({result_df['百分制'].min():.2f}分)")
        
        # 分档统计
        if '分档' in result_df.columns:
            print()
            print("分档统计:")
            grade_counts = result_df['分档'].value_counts()
            for grade, count in grade_counts.items():
                print(f"  {grade}: {count}个 ({count/len(result_df)*100:.1f}%)")
        
        # 错误标记统计
        if '错误标记' in result_df.columns:
            flagged = result_df[result_df['错误标记'] != '']
            if len(flagged) > 0:
                print()
                print(f"错误标记: {len(flagged)}个问题")
                # 统计各级别错误
                minor_count = len(flagged[flagged.get('微小错误', '').notna() & (flagged.get('微小错误', '') != '')])
                moderate_count = len(flagged[flagged.get('明显错误', '').notna() & (flagged.get('明显错误', '') != '')])
                major_count = len(flagged[flagged.get('重大错误', '').notna() & (flagged.get('重大错误', '') != '')])
                if minor_count > 0:
                    print(f"  微小错误: {minor_count}个问题")
                if moderate_count > 0:
                    print(f"  明显错误: {moderate_count}个问题")
                if major_count > 0:
                    print(f"  重大错误: {major_count}个问题")
                for flag, count in flag_counts.items():
                    print(f"  {flag}: {count}次")
    
    # 显示错误
    if '处理错误' in result_df.columns:
        errors = result_df[result_df['处理错误'].notna()]
        if len(errors) > 0:
            print()
            print(f"处理错误: {len(errors)} 个")
            for idx, row in errors.iterrows():
                print(f"  - [{idx+1}] {row.get('案例标题', '')[:30]}... - 问题{row.get('问题编号', '')}: {row['处理错误']}")
    
    print()
    print('=' * 80)
    print('✓ 处理完成！')
    print('=' * 80)


if __name__ == '__main__':
    main()

