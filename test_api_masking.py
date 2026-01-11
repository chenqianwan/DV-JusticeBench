"""
测试使用DeepSeek API进行脱敏
并行处理10个案例，查看效果
"""
import os
import sys
from datetime import datetime
import concurrent.futures
from utils.case_manager import case_manager
from utils.data_masking import DataMaskerAPI
from utils.excel_export import excel_exporter
from utils.process_cleanup import setup_signal_handlers, SafeThreadPoolExecutor
from config import MAX_CONCURRENT_WORKERS

# 设置信号处理器，确保中断时正确清理
setup_signal_handlers()

def mask_single_case(case: dict, masker: DataMaskerAPI) -> dict:
    """对单个案例进行API脱敏"""
    try:
        print(f"正在处理: {case.get('title', '未知案例')[:50]}...")
        masked_case = masker.mask_case_with_api(case)
        print(f"✓ 完成: {case.get('title', '未知案例')[:50]}...")
        return masked_case
    except Exception as e:
        print(f"✗ 失败: {case.get('title', '未知案例')[:50]}... 错误: {str(e)}")
        return case

def main():
    """主函数"""
    print("=" * 60)
    print("使用DeepSeek API进行案例脱敏测试")
    print("=" * 60)
    
    # 获取所有案例
    all_cases = case_manager.get_all_cases()
    print(f"\n总案例数: {len(all_cases)}")
    
    # 只处理前10个案例
    test_cases = all_cases[:10]
    print(f"测试案例数: {len(test_cases)}")
    
    # 初始化API脱敏工具
    masker = DataMaskerAPI()
    
    # 并行处理
    print("\n" + "=" * 60)
    print("开始并行脱敏处理...")
    print("=" * 60)
    
    masked_cases = []
    max_workers = min(MAX_CONCURRENT_WORKERS, len(test_cases))
    print(f"使用 {max_workers} 个并发线程...\n")
    
    with SafeThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_case = {
            executor.submit(mask_single_case, case, masker): case 
            for case in test_cases
        }
        
        for future in concurrent.futures.as_completed(future_to_case):
            case = future_to_case[future]
            try:
                masked_case = future.result()
                masked_cases.append(masked_case)
            except Exception as e:
                print(f"✗ 处理失败: {case.get('title', '未知案例')} - {str(e)}")
                masked_cases.append(case)
    
    # 准备Excel数据（只包含脱敏列，不包含原始列）
    print("\n" + "=" * 60)
    print("准备Excel数据...")
    print("=" * 60)
    
    data = []
    for case in masked_cases:
        judge_decision = case.get('judge_decision_masked', '')
        if judge_decision == 'nan' or (isinstance(judge_decision, float) and pd.isna(judge_decision)):
            judge_decision = ''
        
        row = {
            '案例ID': case.get('id', ''),
            '案例标题': case.get('title', ''),
            '案例标题（脱敏）': case.get('title_masked', ''),
            '案例内容（脱敏）': case.get('case_text_masked', ''),
            '法官判决（脱敏）': judge_decision,
            '案例日期': case.get('case_date', ''),
            '创建时间': case.get('created_at', '')
        }
        data.append(row)
    
    # 导出到Excel
    import pandas as pd
    df = pd.DataFrame(data)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'data/results/案例列表_API脱敏测试_{timestamp}.xlsx'
    os.makedirs('data/results', exist_ok=True)
    
    with pd.ExcelWriter(output_file, engine='openpyxl') as writer:
        df.to_excel(writer, sheet_name='案例列表', index=False)
        
        # 调整列宽
        from openpyxl.utils import get_column_letter
        worksheet = writer.sheets['案例列表']
        for idx, col in enumerate(df.columns):
            max_length = max(
                df[col].astype(str).map(len).max(),
                len(col)
            )
            adjusted_width = min(max_length + 2, 100)
            column_letter = get_column_letter(idx + 1)
            worksheet.column_dimensions[column_letter].width = adjusted_width
    
    print(f"\n✓ Excel文件已生成: {output_file}")
    print(f"  包含 {len(masked_cases)} 个案例（仅脱敏列）")
    
    # 显示前3个案例的对比
    print("\n" + "=" * 60)
    print("前3个案例的脱敏效果预览:")
    print("=" * 60)
    for i, case in enumerate(masked_cases[:3], 1):
        print(f"\n案例 {i}: {case.get('title', '未知')[:50]}")
        print(f"原始内容长度: {len(str(case.get('case_text', '')))} 字符")
        print(f"脱敏内容长度: {len(str(case.get('case_text_masked', '')))} 字符")
        print(f"脱敏内容预览（前200字符）:")
        print(f"  {str(case.get('case_text_masked', ''))[:200]}...")

if __name__ == '__main__':
    import pandas as pd
    main()

