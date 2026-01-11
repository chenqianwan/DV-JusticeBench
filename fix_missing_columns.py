"""
修复Excel文件中缺失的列数据
从cases.json读取原始案例数据，复用评估逻辑重新评估缺失的行
"""
import pandas as pd
import os
import sys
import json
from datetime import datetime
import concurrent.futures
from utils.evaluator import AnswerEvaluator
from utils.data_masking import DataMaskerAPI
from config import MAX_CONCURRENT_WORKERS
from utils.process_cleanup import setup_signal_handlers, SafeThreadPoolExecutor
import glob


def find_rows_with_missing_columns(df):
    """
    找出缺少各维度得分列的行
    
    Returns:
        list: 需要修复的行索引列表
    """
    required_dimension_columns = [
        '规范依据相关性_得分',
        '涵摄链条对齐度_得分',
        '价值衡量与同理心对齐度_得分',
        '关键事实与争点覆盖度_得分',
        '裁判结论与救济配置一致性_得分'
    ]
    
    missing_rows = []
    
    for idx, row in df.iterrows():
        # 检查是否缺少任何维度得分列
        missing_cols = []
        for col in required_dimension_columns:
            if col not in df.columns:
                missing_cols.append(col)
            elif pd.isna(row.get(col)) or row.get(col) == '' or row.get(col) == 0:
                missing_cols.append(col)
        
        # 检查是否缺少详细评价
        if '详细评价' not in df.columns or pd.isna(row.get('详细评价')) or row.get('详细评价') == '':
            missing_cols.append('详细评价')
        
        if missing_cols:
            missing_rows.append({
                'index': idx,
                'missing_columns': missing_cols,
                'row_data': row.to_dict()
            })
    
    return missing_rows


def fix_single_row(row_info, cases_data, row_index, total_rows):
    """
    修复单行缺失的数据
    从cases.json读取原始案例数据，复用评估逻辑重新评估
    """
    row = row_info['row_data']
    missing_cols = row_info['missing_columns']
    case_id = row.get('案例ID', '')
    
    print(f"[{row_index}/{total_rows}] 修复案例: {case_id} - 问题 {row.get('问题编号', 'N/A')}", flush=True)
    print(f"  缺失列: {', '.join(missing_cols)}", flush=True)
    
    result = row.copy()
    
    try:
        # 检查必要的列是否存在
        required_cols = ['AI回答', '问题', '案例ID']
        missing_required = [col for col in required_cols if col not in row or pd.isna(row.get(col)) or row.get(col) == '']
        
        if missing_required:
            print(f"  ⚠️ 缺少必要列: {', '.join(missing_required)}，跳过", flush=True)
            result['处理错误'] = f"缺少必要列: {', '.join(missing_required)}"
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
        
        # 获取必要的数据
        ai_answer = row.get('AI回答', '')
        question = row.get('问题', '')
        
        # 对案例进行脱敏（复用脱敏逻辑）
        masker = DataMaskerAPI()
        case_dict = {
            'title': case_data.get('title', ''),
            'case_text': case_text,
            'judge_decision': judge_decision
        }
        
        masked_case = masker.mask_case_with_api(case_dict)
        masked_content = masked_case.get('case_text_masked', case_text)
        masked_judge = masked_case.get('judge_decision_masked', judge_decision)
        
        # 重新评估（复用评估逻辑）
        evaluator = AnswerEvaluator()
        evaluation = evaluator.evaluate_answer(
            ai_answer=ai_answer,
            judge_decision=masked_judge,
            question=question,
            case_text=masked_content if masked_content else ''
        )
        
        # 填充各维度得分（如果缺失）
        dimension_columns = [
            '规范依据相关性_得分',
            '涵摄链条对齐度_得分',
            '价值衡量与同理心对齐度_得分',
            '关键事实与争点覆盖度_得分',
            '裁判结论与救济配置一致性_得分'
        ]
        
        dimension_scores = evaluation.get('各维度得分', {})
        for col in dimension_columns:
            if col in missing_cols or col not in result or pd.isna(result.get(col)) or result.get(col) == '':
                dimension_name = col.replace('_得分', '')
                score = dimension_scores.get(dimension_name, 0)
                result[col] = score
                print(f"  ✓ 填充 {col}: {score}", flush=True)
        
        # 填充详细评价（如果缺失）
        if '详细评价' in missing_cols or '详细评价' not in result or pd.isna(result.get('详细评价')) or result.get('详细评价') == '':
            result['详细评价'] = evaluation.get('详细评价', '')
            print(f"  ✓ 填充 详细评价", flush=True)
        
        # 填充评价Thinking（如果存在）
        if '评价Thinking' in evaluation:
            result['评价Thinking'] = evaluation.get('评价Thinking', '')
        
        # 更新错误标记相关列
        error_mark = evaluation.get('错误标记', '')
        error_details = evaluation.get('错误详情', {})
        
        if '错误标记' not in result or pd.isna(result.get('错误标记')) or result.get('错误标记') == '':
            result['错误标记'] = error_mark
        
        if error_details:
            if '微小错误' not in result or pd.isna(result.get('微小错误')) or result.get('微小错误') == '':
                if error_details.get('微小错误'):
                    result['微小错误'] = '; '.join(error_details['微小错误'])
            
            if '明显错误' not in result or pd.isna(result.get('明显错误')) or result.get('明显错误') == '':
                if error_details.get('明显错误'):
                    result['明显错误'] = '; '.join(error_details['明显错误'])
            
            if '重大错误' not in result or pd.isna(result.get('重大错误')) or result.get('重大错误') == '':
                if error_details.get('重大错误'):
                    result['重大错误'] = '; '.join(error_details['重大错误'])
        
        # 更新总分和百分制（如果缺失）
        if '总分' not in result or pd.isna(result.get('总分')) or result.get('总分') == 0:
            result['总分'] = evaluation.get('总分', 0)
        
        if '百分制' not in result or pd.isna(result.get('百分制')) or result.get('百分制') == 0:
            result['百分制'] = evaluation.get('百分制', 0)
        
        if '分档' not in result or pd.isna(result.get('分档')) or result.get('分档') == '':
            result['分档'] = evaluation.get('分档', '')
        
        result['处理错误'] = ''
        print(f"  ✓ 修复完成", flush=True)
        
        return result
        
    except Exception as e:
        print(f"  ✗ 修复失败: {str(e)}", flush=True)
        import traceback
        traceback.print_exc()
        result['处理错误'] = f"修复失败: {str(e)}"
        return result


def find_latest_excel_file():
    """查找最新的评估结果Excel文件"""
    patterns = [
        'data/*案例*新标准评估*完整版*.xlsx',
        'data/*案例*新标准评估*.xlsx'
    ]
    
    all_files = []
    for pattern in patterns:
        files = glob.glob(pattern)
        all_files.extend(files)
    
    if not all_files:
        return None
    
    latest_file = max(all_files, key=os.path.getmtime)
    return latest_file


def main():
    """主函数"""
    setup_signal_handlers()
    
    print('=' * 80, flush=True)
    print('修复Excel文件中缺失的列数据', flush=True)
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
    
    # 查找Excel文件
    excel_file = find_latest_excel_file()
    
    if not excel_file:
        print("错误：找不到评估结果Excel文件", flush=True)
        print("请确保 data/ 目录下有评估结果文件", flush=True)
        return
    
    print(f"找到文件: {excel_file}", flush=True)
    print(flush=True)
    
    # 读取Excel文件
    print("读取Excel文件...", flush=True)
    df = pd.read_excel(excel_file)
    print(f"总行数: {len(df)}", flush=True)
    print(f"总案例数: {df['案例ID'].nunique() if '案例ID' in df.columns else 0}", flush=True)
    print(flush=True)
    
    # 找出需要修复的行
    print("检查缺失的列...", flush=True)
    missing_rows = find_rows_with_missing_columns(df)
    
    if not missing_rows:
        print("✓ 所有行的数据都完整，无需修复", flush=True)
        return
    
    print(f"发现 {len(missing_rows)} 行需要修复", flush=True)
    print(flush=True)
    
    # 显示缺失情况统计
    missing_cols_count = {}
    for row_info in missing_rows:
        for col in row_info['missing_columns']:
            missing_cols_count[col] = missing_cols_count.get(col, 0) + 1
    
    print("缺失列统计:", flush=True)
    for col, count in sorted(missing_cols_count.items(), key=lambda x: -x[1]):
        print(f"  {col}: {count} 行", flush=True)
    print(flush=True)
    
    # 按案例ID分组显示
    case_missing_count = {}
    for row_info in missing_rows:
        case_id = row_info['row_data'].get('案例ID', 'N/A')
        case_missing_count[case_id] = case_missing_count.get(case_id, 0) + 1
    
    print("缺失数据的案例统计:", flush=True)
    for case_id, count in sorted(case_missing_count.items(), key=lambda x: -x[1]):
        print(f"  {case_id}: {count} 行", flush=True)
    print(flush=True)
    
    # 确认是否继续
    print("=" * 80, flush=True)
    print(f"将修复 {len(missing_rows)} 行数据", flush=True)
    print(f"这将需要 {len(missing_rows)} 次评估API调用（每个调用包含脱敏+评估）", flush=True)
    print("=" * 80, flush=True)
    print(flush=True)
    
    # 并行修复缺失的行
    print(f"使用 {MAX_CONCURRENT_WORKERS} 个并发线程修复数据...", flush=True)
    print(flush=True)
    
    fixed_results = []
    completed_count = 0
    
    with SafeThreadPoolExecutor(max_workers=min(MAX_CONCURRENT_WORKERS, len(missing_rows))) as executor:
        future_to_row = {
            executor.submit(fix_single_row, row_info, cases_data, i+1, len(missing_rows)): (i, row_info)
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
    
    # 更新DataFrame中修复的行
    for fix_info in fixed_results:
        original_idx = fix_info['original_index']
        fixed_data = fix_info['fixed_data']
        
        for col, value in fixed_data.items():
            if col in df.columns:
                df.at[original_idx, col] = value
            else:
                # 如果列不存在，添加新列
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
    output_file = f'data/{name_without_ext}_修复_{timestamp}.xlsx'
    
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
    remaining_missing = find_rows_with_missing_columns(df)
    if remaining_missing:
        print(f"⚠️ 仍有 {len(remaining_missing)} 行存在缺失数据", flush=True)
        print("可能的原因：缺少必要数据（AI回答、问题、案例ID或法官判决）", flush=True)
    else:
        print("✓ 所有数据已完整", flush=True)
    
    print(flush=True)
    print("=" * 80, flush=True)
    print("✓ 修复完成！", flush=True)
    print("=" * 80, flush=True)


if __name__ == '__main__':
    main()

