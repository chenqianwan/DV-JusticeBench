"""
分析为什么某些行缺失AI回答
"""
import pandas as pd
import os

def analyze_missing_ai_answers():
    """分析缺失AI回答的原因"""
    
    # 读取原始文件
    original_file = 'data/108个案例_新标准评估_完整版_20260107_150532.xlsx'
    if not os.path.exists(original_file):
        print(f"错误：找不到文件 {original_file}")
        return
    
    df = pd.read_excel(original_file)
    
    # 找出缺少AI回答的行
    missing_ai_rows = []
    for idx, row in df.iterrows():
        ai_answer = row.get('AI回答', '')
        if pd.isna(ai_answer) or str(ai_answer) == '':
            missing_ai_rows.append({
                'index': idx,
                'case_id': row.get('案例ID', 'N/A'),
                'question_num': row.get('问题编号', 'N/A'),
                'row': row
            })
    
    print("=" * 80)
    print("缺失AI回答的行分析")
    print("=" * 80)
    print(f"\n发现 {len(missing_ai_rows)} 行缺少AI回答\n")
    
    # 分析这些行的特征
    print("详细分析:")
    print("-" * 80)
    
    for row_info in missing_ai_rows:
        idx = row_info['index']
        case_id = row_info['case_id']
        q_num = row_info['question_num']
        row = row_info['row']
        
        print(f"\n行 {idx+1}: {case_id} - 问题 {q_num}")
        print(f"  案例标题: {row.get('案例标题', 'N/A')}")
        
        # 检查关键字段
        ai_answer = row.get('AI回答', '')
        ai_thinking = row.get('AI回答Thinking', '')
        question = row.get('问题', '')
        total_score = row.get('总分', 'N/A')
        detailed_eval = row.get('详细评价', '')
        error = row.get('处理错误', '')
        
        print(f"\n  数据状态:")
        print(f"    AI回答: {'有' if pd.notna(ai_answer) and str(ai_answer) != '' else '无'}")
        print(f"    AI回答Thinking: {'有' if pd.notna(ai_thinking) and str(ai_thinking) != '' else '无'}")
        if pd.notna(ai_thinking) and str(ai_thinking) != '':
            thinking_str = str(ai_thinking)
            print(f"      Thinking长度: {len(thinking_str)} 字符")
            # 检查thinking中是否包含可能的答案
            if len(thinking_str) > 100:
                print(f"      Thinking前100字符: {thinking_str[:100]}...")
        print(f"    问题: {'有' if pd.notna(question) and str(question) != '' else '无'}")
        print(f"    总分: {total_score}")
        print(f"    详细评价: {'有' if pd.notna(detailed_eval) and str(detailed_eval) != '' else '无'}")
        print(f"    处理错误: {error if pd.notna(error) and str(error) != '' else '无'}")
        
        # 检查该案例的其他问题
        case_rows = df[df['案例ID'] == case_id]
        print(f"\n  该案例的其他问题状态:")
        for i, case_row in case_rows.iterrows():
            other_q_num = case_row.get('问题编号', 'N/A')
            other_ai = case_row.get('AI回答', '')
            has_ai = pd.notna(other_ai) and str(other_ai) != ''
            print(f"    问题 {other_q_num}: AI回答={'有' if has_ai else '无'}")
        
        # 分析可能的原因
        print(f"\n  可能的原因分析:")
        if pd.notna(ai_thinking) and str(ai_thinking) != '':
            print(f"    ✓ 有AI回答Thinking，说明API调用成功")
            print(f"    ⚠️ 但AI回答为空，可能是API返回的content字段为空")
            print(f"    ⚠️ 可能原因：DeepSeek-Reasoner在某些情况下将答案放在reasoning_content中")
        if pd.notna(total_score) and total_score != 0:
            print(f"    ✓ 有总分，说明评估成功")
            print(f"    ⚠️ 评估时可能使用了thinking内容或其他数据源")
        if pd.notna(detailed_eval) and str(detailed_eval) != '':
            print(f"    ✓ 有详细评价，说明评估API调用成功")
    
    # 统计模式
    print("\n" + "=" * 80)
    print("统计模式分析")
    print("=" * 80)
    
    has_thinking_count = 0
    has_score_count = 0
    has_eval_count = 0
    
    for row_info in missing_ai_rows:
        row = row_info['row']
        if pd.notna(row.get('AI回答Thinking')) and str(row.get('AI回答Thinking', '')) != '':
            has_thinking_count += 1
        if pd.notna(row.get('总分')) and row.get('总分', 0) != 0:
            has_score_count += 1
        if pd.notna(row.get('详细评价')) and str(row.get('详细评价', '')) != '':
            has_eval_count += 1
    
    print(f"\n缺失AI回答的行中:")
    print(f"  有AI回答Thinking: {has_thinking_count}/{len(missing_ai_rows)} ({has_thinking_count/len(missing_ai_rows)*100:.1f}%)")
    print(f"  有总分: {has_score_count}/{len(missing_ai_rows)} ({has_score_count/len(missing_ai_rows)*100:.1f}%)")
    print(f"  有详细评价: {has_eval_count}/{len(missing_ai_rows)} ({has_eval_count/len(missing_ai_rows)*100:.1f}%)")
    
    # 检查这些案例是否来自process_missing_10_cases.py处理的案例
    print("\n" + "=" * 80)
    print("检查案例来源")
    print("=" * 80)
    
    missing_case_ids_from_script = [
        'case_20260103_155150_0',
        'case_20260103_155150_1',
        'case_20260103_155150_2',
        'case_20260103_155150_3',
        'case_20260103_155150_4',
        'case_20260103_155150_5',
        'case_20260103_155150_6',
        'case_20260103_155150_7',
        'case_20260103_155150_105',
        'case_20260103_155150_106',
    ]
    
    missing_case_ids_in_data = set()
    for row_info in missing_ai_rows:
        case_id = row_info['case_id']
        missing_case_ids_in_data.add(case_id)
    
    print(f"\n缺失AI回答的案例ID:")
    for case_id in sorted(missing_case_ids_in_data):
        is_from_script = case_id in missing_case_ids_from_script
        print(f"  {case_id} {'(来自process_missing_10_cases.py)' if is_from_script else ''}")
    
    # 结论
    print("\n" + "=" * 80)
    print("结论")
    print("=" * 80)
    
    if has_thinking_count == len(missing_ai_rows):
        print("\n✓ 所有缺失AI回答的行都有AI回答Thinking")
        print("  这表明：")
        print("  1. API调用成功，返回了thinking内容")
        print("  2. 但API返回的content字段可能为空字符串")
        print("  3. 可能原因：DeepSeek-Reasoner在某些响应中将答案放在了reasoning_content中")
        print("  4. 或者API响应格式变化，content字段为空")
        print("\n  根本原因分析：")
        print("  - 在process_missing_10_cases.py中，代码逻辑是：")
        print("    ai_answer = ai_response.get('answer', '')")
        print("    result['AI回答'] = ai_answer")
        print("  - 如果API返回的answer字段为空字符串（不是None），")
        print("    那么result['AI回答']会被设置为空字符串")
        print("  - 但thinking内容被正确保存了")
        print("\n  建议：")
        print("  - 检查DeepSeek API的响应格式")
        print("  - 如果content为空但reasoning_content有内容，可能需要从reasoning_content中提取答案")
        print("  - 或者修改代码逻辑，当content为空时，尝试从thinking中提取答案部分")
    
    if has_score_count > 0:
        print(f"\n✓ {has_score_count}行有总分，说明评估时使用了其他数据源（可能是thinking内容）")
    
    print("\n" + "=" * 80)

if __name__ == '__main__':
    analyze_missing_ai_answers()

