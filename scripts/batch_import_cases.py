#!/usr/bin/env python3
"""
批量导入static/cases目录下的所有案例文件到系统
"""
import os
from utils.doc_reader import DocReader
from utils.case_manager import case_manager

def main():
    cases_dir = 'static/cases'
    
    if not os.path.exists(cases_dir):
        print(f"错误：目录 {cases_dir} 不存在")
        return
    
    # 读取所有docx文件
    docx_files = [f for f in os.listdir(cases_dir) if f.endswith('.docx')]
    
    if not docx_files:
        print(f"未找到任何.docx文件")
        return
    
    print(f"找到 {len(docx_files)} 个.docx文件")
    print("=" * 60)
    
    reader = DocReader()
    imported_count = 0
    skipped_count = 0
    error_count = 0
    
    for filename in docx_files:
        filepath = os.path.join(cases_dir, filename)
        try:
            # 解析案例
            case = reader.parse_case_from_doc(filepath)
            
            # 检查是否已存在（根据标题）
            existing_cases = case_manager.get_all_cases()
            title_exists = any(c.get('title') == case['title'] for c in existing_cases)
            
            if title_exists:
                print(f"⏭ 跳过（已存在）: {filename[:50]}...")
                skipped_count += 1
                continue
            
            # 导入案例
            case_id = case_manager.add_case(
                title=case['title'],
                case_text=case['case_text'],
                judge_decision=case.get('judge_decision', ''),
                case_date=case.get('case_date', ''),
                metadata={'source_file': filename}
            )
            
            imported_count += 1
            print(f"✓ [{imported_count}/{len(docx_files)}] 已导入: {case['title'][:50]}...")
            
        except Exception as e:
            error_count += 1
            print(f"✗ 导入失败 {filename}: {str(e)}")
    
    print("=" * 60)
    print(f"导入完成！")
    print(f"  成功导入: {imported_count} 个")
    print(f"  跳过（已存在）: {skipped_count} 个")
    print(f"  失败: {error_count} 个")
    print(f"  总计: {len(docx_files)} 个文件")
    
    # 显示最终统计
    all_cases = case_manager.get_all_cases()
    print(f"\n系统中共有案例: {len(all_cases)} 个")

if __name__ == '__main__':
    main()


