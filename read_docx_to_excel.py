"""
读取DOCX文件并生成Excel模板（支持.docx格式，处理更可靠）
"""
import os
import sys
from utils.doc_reader import DocReader
from utils.excel_export import ExcelExporter
import pandas as pd


def main():
    """主函数"""
    # 设置路径
    cases_dir = os.path.join(os.path.dirname(__file__), 'static', 'cases')
    output_dir = os.path.join(os.path.dirname(__file__), 'data')
    os.makedirs(output_dir, exist_ok=True)
    
    print("=" * 60)
    print("开始读取文档文件...")
    print("=" * 60)
    
    # 读取文档文件
    reader = DocReader()
    
    # 优先读取.docx文件（如果存在）
    docx_files = [f for f in os.listdir(cases_dir) if f.endswith('.docx')]
    doc_files = [f for f in os.listdir(cases_dir) if f.endswith('.doc')]
    
    if docx_files:
        print(f"\n找到 {len(docx_files)} 个.docx文件（推荐格式）")
        print(f"找到 {len(doc_files)} 个.doc文件")
        print("\n优先处理.docx文件...")
        files_to_process = docx_files[:10]  # 处理前10个
        file_ext = '.docx'
    elif doc_files:
        print(f"\n找到 {len(doc_files)} 个.doc文件")
        print("⚠️  注意：.doc格式可能有编码问题，建议转换为.docx格式")
        print("\n处理前3个.doc文件作为测试...")
        files_to_process = doc_files[:3]
        file_ext = '.doc'
    else:
        print("未找到任何文档文件！")
        return
    
    cases = []
    for filename in files_to_process:
        filepath = os.path.join(cases_dir, filename)
        try:
            case = reader.parse_case_from_doc(filepath)
            cases.append(case)
            print(f"✓ 已读取: {filename}")
        except Exception as e:
            print(f"✗ 读取失败 {filename}: {str(e)}")
    
    if not cases:
        print("未成功读取任何案例文件！")
        return
    
    print(f"\n成功读取 {len(cases)} 个案例")
    
    # 生成Excel模板
    print("\n" + "=" * 60)
    print("生成Excel模板...")
    print("=" * 60)
    
    # 准备Excel数据
    excel_data = []
    for case in cases:
        excel_data.append({
            '案例标题': case['title'],
            '案例内容': case['case_text'],
            '法官判决': case['judge_decision'],
            '案例日期': case['case_date']
        })
    
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
    
    # 生成Excel文件
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'案例模板_{timestamp}.xlsx')
    
    df = pd.DataFrame(excel_data)
    df.to_excel(output_file, index=False, engine='openpyxl')
    
    print(f"\n✓ Excel模板已生成: {output_file}")
    print(f"  包含 {len(excel_data)} 个案例")
    
    # 显示预览
    print("\n" + "=" * 60)
    print("预览（前2个案例的标题和内容片段）:")
    print("=" * 60)
    for i, case in enumerate(cases[:2], 1):
        print(f"\n案例 {i}:")
        print(f"  标题: {case['title']}")
        print(f"  内容长度: {len(case['case_text'])} 字符")
        print(f"  判决长度: {len(case['judge_decision'])} 字符")
        print(f"  日期: {case['case_date'] or '未识别'}")
        # 显示内容预览（检查中文）
        preview = case['case_text'][:200]
        chinese_count = sum(1 for c in preview if '\u4e00' <= c <= '\u9fff')
        print(f"  内容预览（前200字符，包含{chinese_count}个中文字符）:")
        print(f"  {preview}...")


if __name__ == '__main__':
    main()

