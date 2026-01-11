"""
读取DOC文件并生成Excel模板
"""
import os
import sys
from utils.doc_reader import DocReader
from utils.excel_export import ExcelExporter


def main():
    """主函数"""
    # 设置路径
    cases_dir = os.path.join(os.path.dirname(__file__), 'static', 'cases')
    output_dir = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(output_dir, exist_ok=True)
    
    print("=" * 60)
    print("开始读取DOC文件...")
    print("=" * 60)
    
    # 读取DOC文件
    reader = DocReader()
    
    # 先读取前3个文件作为测试
    print("\n读取前3个文件作为测试...")
    cases = reader.read_multiple_docs(cases_dir, limit=3)
    
    if not cases:
        print("未找到任何案例文件！")
        return
    
    print(f"\n成功读取 {len(cases)} 个案例")
    
    # 生成Excel模板
    print("\n" + "=" * 60)
    print("生成Excel模板...")
    print("=" * 60)
    
    exporter = ExcelExporter()
    
    # 准备Excel数据
    excel_data = []
    for case in cases:
        excel_data.append({
            '案例标题': case['title'],
            '案例内容': case['case_text'],
            '法官判决': case['judge_decision'],
            '案例日期': case['case_date']
        })
    
    # 生成Excel文件
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'案例模板_{timestamp}.xlsx')
    
    # 清理数据中的非法字符
    import re
    def clean_cell(value):
        if not isinstance(value, str):
            return value
        # 移除控制字符（保留换行符）
        value = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f-\x9f]', '', value)
        return value
    
    for row in excel_data:
        for key in row:
            row[key] = clean_cell(row[key])
    
    df = pd.DataFrame(excel_data)
    df.to_excel(output_file, index=False, engine='openpyxl')
    
    print(f"\n✓ Excel模板已生成: {output_file}")
    print(f"  包含 {len(excel_data)} 个案例")
    
    # 显示预览
    print("\n" + "=" * 60)
    print("预览（前2个案例的标题）:")
    print("=" * 60)
    for i, case in enumerate(cases[:2], 1):
        print(f"\n案例 {i}:")
        print(f"  标题: {case['title']}")
        print(f"  内容长度: {len(case['case_text'])} 字符")
        print(f"  判决长度: {len(case['judge_decision'])} 字符")
        print(f"  日期: {case['case_date'] or '未识别'}")
        print(f"  内容预览: {case['case_text'][:100]}...")


if __name__ == '__main__':
    import pandas as pd
    main()

