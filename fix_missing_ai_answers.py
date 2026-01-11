"""
修复缺少AI回答的行：重新生成AI回答并评估
"""
import pandas as pd
import os
import sys
import json
from datetime import datetime
import concurrent.futures
from utils.ai_api import ai_api
from utils.evaluator import AnswerEvaluator
from utils.data_masking import DataMaskerAPI
from config import MAX_CONCURRENT_WORKERS
from utils.process_cleanup import setup_signal_handlers, SafeThreadPoolExecutor
import glob


def find_rows_without_ai_answer(df):
    """找出缺少AI回答的行"""
    missing_rows = []
    
    for idx, row in df.iterrows():
        ai_answer = row.get('AI回答', '')
        if pd.isna(ai_answer) or ai_answer == '':
            missing_rows.append({
                'index': idx,
                'row_data': row.to_dict()
            })
    
    return missing_rows


def fix_single_row_without_ai_answer(row_info, cases_data, row_index, total_rows):
    """
    修复缺少AI回答的行：重新生成AI回答并评估
    """
    row = row_info['row_data']
    case_id = row.get('案例ID', '')
    question = row.get('问题', '')
    
    print(f"[{row_index}/{total_rows}] 修复案例: {case_id} - 问题 {row.get('问题编号', 'N/A')}", flush=True)
    print(f"  缺少AI回答，将重新生成并评估", flush=True)
    
    result = row.copy()
    
    try:
        # 检查必要的列
        if not question or pd.isna(question) or question == '':
            print(f"  ⚠️ 缺少问题，跳过", flush=True)
            result['处理错误'] = "缺少问题"
            return result
        
        if not case_id or case_id == '':
            print(f"  ⚠️ 缺少案例ID，跳过", flush=True)
            result['处理错误'] = "缺少案例ID"
            return result
        
        # 从cases.json获取原始案例数据
        if case_id not in cases_data:
            print(f"  ⚠️ 案例 {case_id} 不在cases.json中，跳过", flush=True)
            result['处理错误'] = f"案例 {case_id} 不在cases.json中"
            return result
        
        case_data = cases_data[case_id]
        case_text = case_data.get('case_text', case_data.get('content', ''))
        judge_decision = case_data.get('judge_decision', '')
        
        if not judge_decision:
            print(f"  ⚠️ 案例 {case_id} 缺少法官判决数据，跳过", flush=True)
            result['处理错误'] = "缺少法官判决数据"
            return result
        
        # 对案例进行脱敏
        masker = DataMaskerAPI()
        case_dict = {
            'title': case_data.get('title', ''),
            'case_text': case_text,
            'judge_decision': judge_decision
        }
        
        masked_case = masker.mask_case_with_api(case_dict)
        masked_content = masked_case.get('case_text_masked', case_text)
        masked_judge = masked_case.get('judge_decision_masked', judge_decision)
        masked_title = masked_case.get('title_masked', '')
        
        # 更新脱敏标题（如果缺失）
        if '案例标题（脱敏）' not in result or pd.isna(result.get('案例标题（脱敏）')) or result.get('案例标题（脱敏）') == '':
            result['案例标题（脱敏）'] = masked_title
        
        # 重新生成AI回答（使用thinking模式）
        print(f"  → 生成AI回答（Thinking模式）...", flush=True)
        ai_response = ai_api.analyze_case(masked_content, question=question, use_thinking=True)
        
        if isinstance(ai_response, dict):
            ai_answer = ai_response.get('answer', '')
            ai_thinking = ai_response.get('thinking', '')
        else:
            ai_answer = ai_response
            ai_thinking = ''
        
        result['AI回答'] = ai_answer
        if ai_thinking:
            result['AI回答Thinking'] = ai_thinking
        
        print(f"  ✓ AI回答生成完成（长度：{len(ai_answer)}字符）", flush=True)
        
        # 重新评估
        print(f"  → 进行评估...", flush=True)
        evaluator = AnswerEvaluator()
        evaluation = evaluator.evaluate_answer(
            ai_answer=ai_answer,
            judge_decision=masked_judge,
            question=question,
            case_text=masked_content if masked_content else ''
        )
        
        # 填充所有评估结果
        result['总分'] = evaluation.get('总分', 0)
        result['百分制'] = evaluation.get('百分制', 0)
        result['分档'] = evaluation.get('分档', '')
        
        # 填充各维度得分
        dimension_columns = [
            '规范依据相关性_得分',
            '涵摄链条对齐度_得分',
            '价值衡量与同理心对齐度_得分',
            '关键事实与争点覆盖度_得分',
            '裁判结论与救济配置一致性_得分'
        ]
        
        dimension_scores = evaluation.get('各维度得分', {})
        for col in dimension_columns:
            dimension_name = col.replace('_得分', '')
            score = dimension_scores.get(dimension_name, 0)
            result[col] = score
        
        # 填充详细评价
        result['详细评价'] = evaluation.get('详细评价', '')
        
        # 填充评价Thinking（如果存在）
        if '评价Thinking' in evaluation:
            result['评价Thinking'] = evaluation.get('评价Thinking', '')
        
        # 更新错误标记相关列
        error_mark = evaluation.get('错误标记', '')
        error_details = evaluation.get('错误详情', {})
        
        result['错误标记'] = error_mark
        
        if error_details:
            if error_details.get('微小错误'):
                result['微小错误'] = '; '.join(error_details['微小错误'])
            if error_details.get('明显错误'):
                result['明显错误'] = '; '.join(error_details['明显错误'])
            if error_details.get('重大错误'):
                result['重大错误'] = '; '.join(error_details['重大错误'])
        
        result['处理错误'] = ''
        print(f"  ✓ 修复完成（总分：{result['总分']:.2f}/20）", flush=True)
        
        return result
        
    except Exception as e:
        print(f"  ✗ 修复失败: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        result['处理错误'] = f"修复失败: {str(e)}"
        return result


def find_latest_fixed_file():
    """查找最新的修复文件（优先查找修复熔断bug的文件）"""
    # 优先查找修复熔断bug的文件
    pattern1 = 'data/[[]标注102个[]]*修复熔断bug*.xlsx'
    files1 = glob.glob(pattern1)
    if files1:
        latest_file = max(files1, key=os.path.getmtime)
        return latest_file
    
    # 如果没有，查找其他修复文件
    pattern2 = 'data/*修复*.xlsx'
    files2 = glob.glob(pattern2)
    if files2:
        latest_file = max(files2, key=os.path.getmtime)
        return latest_file
    
    return None


def main():
    """主函数"""
    setup_signal_handlers()
    
    print('=' * 80, flush=True)
    print('修复缺少AI回答的行', flush=True)
    print('=' * 80, flush=True)
    print(flush=True)
    
    # 读取cases.json
    cases_json_file = 'data/cases/cases.json'
    if not os.path.exists(cases_json_file):
        print(f"错误：找不到文件 {cases_json_file}", flush=True)
        return
    
    print(f"读取案例数据: {cases_json_file}", flush=True)
    with open(cases_json_file, 'r', encoding='utf-8') as f:
        cases_data = json.load(f)
    print(f"加载了 {len(cases_data)} 个案例", flush=True)
    print(flush=True)
    
    # 查找最新的修复文件
    excel_file = find_latest_fixed_file()
    
    if not excel_file:
        print("错误：找不到修复文件", flush=True)
        return
    
    print(f"找到文件: {excel_file}", flush=True)
    print(flush=True)
    
    # 读取Excel文件
    print("读取Excel文件...", flush=True)
    df = pd.read_excel(excel_file)
    print(f"总行数: {len(df)}", flush=True)
    print(flush=True)
    
    # 找出缺少AI回答的行
    print("检查缺少AI回答的行...", flush=True)
    missing_rows = find_rows_without_ai_answer(df)
    
    if not missing_rows:
        print("✓ 所有行都有AI回答，无需修复", flush=True)
        return
    
    print(f"发现 {len(missing_rows)} 行缺少AI回答", flush=True)
    print(flush=True)
    
    # 显示详情
    print("缺少AI回答的行详情:", flush=True)
    for row_info in missing_rows:
        row = row_info['row_data']
        print(f"  行 {row_info['index']+1}: {row.get('案例ID', 'N/A')} - 问题 {row.get('问题编号', 'N/A')}", flush=True)
    print(flush=True)
    
    # 确认是否继续
    print("=" * 80, flush=True)
    print(f"将修复 {len(missing_rows)} 行数据", flush=True)
    print(f"这将需要 {len(missing_rows)} 次AI回答生成 + {len(missing_rows)} 次评估API调用", flush=True)
    print("=" * 80, flush=True)
    print(flush=True)
    
    # 并行修复
    print(f"使用 {MAX_CONCURRENT_WORKERS} 个并发线程修复数据...", flush=True)
    print(flush=True)
    
    fixed_results = []
    completed_count = 0
    
    with SafeThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_WORKERS, len(missing_rows))) as executor:
        future_to_row = {
            executor.submit(fix_single_row_without_ai_answer, row_info, cases_data, i+1, len(missing_rows)): (i, row_info)
            for i, row_info in enumerate(missing_rows)
        }
        
        for future in concurrent.futures.as_completed(future_to_row):
            index, row_info = future_to_row[future]
            try:
                fixed_result = future.result()
                fixed_results.append({
                    'original_index': row_info['index'],
                    'fixed_data': fixed_result
                })
                completed_count += 1
                print(f"[总体进度] {completed_count}/{len(missing_rows)} 行已修复", flush=True)
            except Exception as e:
                completed_count += 1
                print(f"✗ 行 {row_info['index']} 修复异常: {str(e)}", flush=True)
    
    print(flush=True)
    print("=" * 80, flush=True)
    print("更新DataFrame...", flush=True)
    print("=" * 80, flush=True)
    
    # 更新DataFrame
    for fix_info in fixed_results:
        original_idx = fix_info['original_index']
        fixed_data = fix_info['fixed_data']
        
        for col, value in fixed_data.items():
            if col in df.columns:
                df.at[original_idx, col] = value
            else:
                if col not in df.columns:
                    df[col] = None
                df.at[original_idx, col] = value
    
    # 重新排列列的顺序
    columns_order = [
        '案例ID', '案例标题', '案例标题（脱敏）', '问题编号', '问题', 
        'AI回答', 'AI回答Thinking',
        '总分', '百分制', '分档',
        '规范依据相关性_得分', '涵摄链条对齐度_得分',
        '价值衡量与同理心对齐度_得分', '关键事实与争点覆盖度_得分',
        '裁判结论与救济配置一致性_得分',
        '错误标记', '微小错误', '明显错误', '重大错误',
        '详细评价', '评价Thinking', '处理错误'
    ]
    
    final_columns = [col for col in columns_order if col in df.columns]
    other_columns = [col for col in df.columns if col not in final_columns]
    final_columns.extend(other_columns)
    
    df = df[final_columns]
    
    # 保存修复后的文件
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    base_name = os.path.basename(excel_file)
    name_without_ext = os.path.splitext(base_name)[0]
    output_file = f'data/{name_without_ext}_完整修复_{timestamp}.xlsx'
    
    print(flush=True)
    print("=" * 80, flush=True)
    print("保存修复后的文件...", flush=True)
    print("=" * 80, flush=True)
    print(f"保存到: {output_file}", flush=True)
    
    df.to_excel(output_file, index=False)
    print("✓ 保存完成！", flush=True)
    print(flush=True)
    
    # 统计信息
    print("=" * 80, flush=True)
    print("修复统计:", flush=True)
    print("=" * 80, flush=True)
    print(f"总行数: {len(df)}", flush=True)
    print(f"修复行数: {len(fixed_results)}", flush=True)
    print(f"总案例数: {df['案例ID'].nunique() if '案例ID' in df.columns else 0}", flush=True)
    print(flush=True)
    
    # 检查是否还有缺失
    remaining_missing = find_rows_without_ai_answer(df)
    if remaining_missing:
        print(f"⚠️ 仍有 {len(remaining_missing)} 行缺少AI回答", flush=True)
    else:
        print("✓ 所有行都有AI回答", flush=True)
    
    # 检查各维度得分是否完整
    required_cols = [
        '规范依据相关性_得分',
        '涵摄链条对齐度_得分',
        '价值衡量与同理心对齐度_得分',
        '关键事实与争点覆盖度_得分',
        '裁判结论与救济配置一致性_得分'
    ]
    
    missing_dimension_rows = []
    for idx, row in df.iterrows():
        for col in required_cols:
            if col not in df.columns or pd.isna(row.get(col)) or row.get(col) == '' or row.get(col) == 0:
                missing_dimension_rows.append(idx)
                break
    
    if missing_dimension_rows:
        print(f"⚠️ 仍有 {len(set(missing_dimension_rows))} 行存在缺失的维度得分", flush=True)
    else:
        print("✓ 所有行的维度得分都完整", flush=True)
    
    print(flush=True)
    print("=" * 80, flush=True)
    print("✓ 修复完成！", flush=True)
    print("=" * 80, flush=True)


if __name__ == '__main__':
    main()

