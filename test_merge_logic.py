"""
测试合并逻辑，验证不会丢失原有数据
"""
import pandas as pd
import os

def test_merge_logic():
    """测试合并逻辑"""
    
    # 读取原始文件（应该有完整数据）
    original_file = 'data/108个案例_新标准评估_完整版_20260107_150532.xlsx'
    if not os.path.exists(original_file):
        print(f"错误：找不到原始文件 {original_file}")
        return
    
    print("=" * 80)
    print("测试合并逻辑")
    print("=" * 80)
    print()
    
    # 读取原始文件
    print(f"读取原始文件: {original_file}")
    original_df = pd.read_excel(original_file)
    print(f"原始文件包含 {len(original_df)} 行，{len(original_df.columns)} 列")
    
    # 检查原始文件中的AI回答
    if 'AI回答' in original_df.columns:
        original_ai_count = original_df['AI回答'].astype(str)
        original_ai_filled = ((original_ai_count != '') & (original_ai_count != 'nan') & (original_ai_count != 'None')).sum()
        print(f"原始文件中非空AI回答数量: {original_ai_filled}/{len(original_df)}")
    
    # 读取修复后的文件（如果有）
    fixed_files = [
        'data/108个案例_新标准评估_完整版_20260107_150532_修复_20260108_205952.xlsx',
        'data/108个案例_新标准评估_完整版_20260107_150532_修复_20260108_205952_完整修复_20260108_211402.xlsx'
    ]
    
    for fixed_file in fixed_files:
        if os.path.exists(fixed_file):
            print()
            print(f"检查修复后的文件: {fixed_file}")
            fixed_df = pd.read_excel(fixed_file)
            print(f"修复后文件包含 {len(fixed_df)} 行，{len(fixed_df.columns)} 列")
            
            # 检查修复后文件中的AI回答
            if 'AI回答' in fixed_df.columns:
                fixed_ai_count = fixed_df['AI回答'].astype(str)
                fixed_ai_filled = ((fixed_ai_count != '') & (fixed_ai_count != 'nan') & (fixed_ai_count != 'None')).sum()
                print(f"修复后文件中非空AI回答数量: {fixed_ai_filled}/{len(fixed_df)}")
                
                # 比较原始文件和修复后文件
                if len(fixed_df) >= len(original_df):
                    # 检查前N行（原始数据的行数）是否一致
                    original_rows = original_df.head(len(original_df))
                    fixed_rows = fixed_df.head(len(original_df))
                    
                    # 检查AI回答是否一致
                    if 'AI回答' in original_df.columns and 'AI回答' in fixed_df.columns:
                        original_ai = original_rows['AI回答'].astype(str).fillna('')
                        fixed_ai = fixed_rows['AI回答'].astype(str).fillna('')
                        
                        # 找出不一致的行
                        mismatches = []
                        for idx in range(len(original_rows)):
                            orig_val = original_ai.iloc[idx]
                            fixed_val = fixed_ai.iloc[idx]
                            
                            # 如果原始有值但修复后为空，说明数据丢失
                            if orig_val != '' and orig_val != 'nan' and orig_val != 'None':
                                if fixed_val == '' or fixed_val == 'nan' or fixed_val == 'None':
                                    mismatches.append({
                                        'index': idx,
                                        'case_id': original_rows.iloc[idx].get('案例ID', 'N/A'),
                                        'question_num': original_rows.iloc[idx].get('问题编号', 'N/A'),
                                        'original': orig_val[:50] if len(orig_val) > 50 else orig_val,
                                        'fixed': fixed_val
                                    })
                        
                        if mismatches:
                            print(f"\n⚠️ 发现 {len(mismatches)} 行数据不一致（原始有AI回答，修复后为空）:")
                            for mismatch in mismatches[:10]:  # 只显示前10个
                                print(f"  行 {mismatch['index']+1}: {mismatch['case_id']} - 问题 {mismatch['question_num']}")
                                print(f"    原始: {mismatch['original']}...")
                                print(f"    修复后: {mismatch['fixed']}")
                            if len(mismatches) > 10:
                                print(f"  ... 还有 {len(mismatches) - 10} 行")
                        else:
                            print("✓ 原始数据的AI回答在修复后文件中完全保留")
    
    print()
    print("=" * 80)
    print("测试完成")
    print("=" * 80)

if __name__ == '__main__':
    test_merge_logic()

