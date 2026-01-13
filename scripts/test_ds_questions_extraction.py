#!/usr/bin/env python3
"""
测试从DeepSeek结果文件提取问题的功能
"""
import pandas as pd
import os
import sys

def test_extract_questions():
    """测试提取问题功能"""
    ds_file = 'data/108个案例_新标准评估_完整版_最终版.xlsx'
    test_case_id = 'case_20251230_134952_90'
    
    if not os.path.exists(ds_file):
        print(f"错误: 文件不存在: {ds_file}")
        return False
    
    print(f"读取文件: {ds_file}")
    try:
        # 读取文件
        ds_df = pd.read_excel(ds_file, engine='openpyxl')
        print(f"✓ 读取成功，共 {len(ds_df)} 行数据")
        print(f"列名: {list(ds_df.columns)[:10]}...")
        
        # 检查必要的列
        required_cols = ['案例ID', '问题', '问题编号']
        missing_cols = [col for col in required_cols if col not in ds_df.columns]
        if missing_cols:
            print(f"✗ 缺少必要的列: {missing_cols}")
            return False
        
        # 筛选DeepSeek数据（如果有"使用的模型"列）
        if '使用的模型' in ds_df.columns:
            ds_df_filtered = ds_df[ds_df['使用的模型'] == 'DeepSeek']
            if len(ds_df_filtered) > 0:
                ds_df = ds_df_filtered
                print(f"✓ 筛选DeepSeek数据后: {len(ds_df)} 行")
            else:
                print(f"⚠️ 未找到DeepSeek数据，使用全部数据")
        
        # 提取测试案例的问题
        case_data = ds_df[ds_df['案例ID'] == test_case_id]
        if len(case_data) == 0:
            print(f"✗ 未找到案例: {test_case_id}")
            print(f"可用的案例ID示例: {ds_df['案例ID'].unique()[:5]}")
            return False
        
        print(f"\n✓ 找到案例 {test_case_id}，共 {len(case_data)} 条记录")
        
        # 按问题编号排序
        case_data = case_data.sort_values('问题编号')
        questions = case_data['问题'].tolist()
        
        print(f"\n提取到 {len(questions)} 个问题:")
        for i, q in enumerate(questions[:5], 1):
            print(f"  问题{i}: {q[:80]}...")
        
        # 检查脱敏数据
        first_row = case_data.iloc[0]
        masked_title = first_row.get('案例标题（脱敏）', '')
        print(f"\n脱敏标题: {masked_title[:50]}..." if masked_title else "\n未找到脱敏标题")
        
        print("\n✓ 测试成功！")
        return True
        
    except Exception as e:
        print(f"✗ 错误: {str(e)}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == '__main__':
    success = test_extract_questions()
    sys.exit(0 if success else 1)
