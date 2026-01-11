"""
处理Excel模板：生成问题并进行AI分析
直接复用web端的并发处理逻辑
"""
import os
import sys
import pandas as pd
from datetime import datetime
import concurrent.futures
from utils.case_manager import case_manager
from utils.ai_api import ai_api
from utils.excel_export import excel_exporter
from utils.similarity import similarity_calculator
from utils.process_cleanup import setup_signal_handlers, SafeThreadPoolExecutor
from config import MAX_CONCURRENT_WORKERS

# 设置信号处理器，确保中断时正确清理
setup_signal_handlers()


def main():
    """主函数"""
    # 读取最新的Excel模板
    import glob
    template_files = sorted(glob.glob('data/案例模板_*.xlsx'), reverse=True)
    if not template_files:
        print("错误：找不到模板文件")
        print("请先运行 read_docx_to_excel.py 生成模板")
        return
    template_file = template_files[0]
    print(f"使用模板文件: {template_file}")
    
    if not os.path.exists(template_file):
        print(f"错误：找不到模板文件 {template_file}")
        print("请先运行 read_docx_to_excel.py 生成模板")
        return
    
    print("=" * 60)
    print("读取Excel模板...")
    print("=" * 60)
    
    df = pd.read_excel(template_file)
    print(f"✓ 读取到 {len(df)} 个案例")
    
    # 将案例导入到case_manager
    print("\n" + "=" * 60)
    print("导入案例到系统...")
    print("=" * 60)
    
    case_ids = []
    for index, row in df.iterrows():
        case_id = case_manager.add_case(
            title=row['案例标题'],
            case_text=row['案例内容'],
            judge_decision=row.get('法官判决', ''),
            case_date=row.get('案例日期', ''),
            metadata={}
        )
        case_ids.append(case_id)
        print(f"✓ 已导入: {row['案例标题']}")
    
    # 为每个案例生成5个问题（并发处理）
    print("\n" + "=" * 60)
    print("为每个案例生成5个问题（并发处理）...")
    print("=" * 60)
    
    def generate_questions_for_case(case_id):
        """为单个案例生成问题的辅助函数"""
        case = case_manager.get_case(case_id)
        if not case:
            return None
        
        case_text = case.get('case_text', '')
        if not case_text:
            print(f"⚠️  跳过案例 {case.get('title', case_id)}：内容为空")
            return None
        
        try:
            print(f"正在为案例生成问题: {case.get('title', case_id)}...")
            questions = ai_api.generate_questions(case_text, num_questions=5)
            
            result = []
            for question in questions:
                result.append({
                    'case_id': case_id,
                    'case_title': case.get('title', ''),
                    'question': question
                })
            
            print(f"✓ 已生成 {len(questions)} 个问题: {case.get('title', case_id)}")
            return result
        except Exception as e:
            print(f"✗ 生成问题失败 {case.get('title', case_id)}: {str(e)}")
            return None
    
    # 使用线程池并发生成问题
    all_questions = []
    max_workers = min(MAX_CONCURRENT_WORKERS, len(case_ids))
    
    with SafeThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_case = {executor.submit(generate_questions_for_case, case_id): case_id 
                          for case_id in case_ids}
        
        for future in concurrent.futures.as_completed(future_to_case):
            result = future.result()
            if result:
                all_questions.extend(result)
    
    print(f"\n总共生成 {len(all_questions)} 个问题")
    
    # 对每个问题进行AI分析（使用并发，复用web端逻辑）
    print("\n" + "=" * 60)
    print("进行AI分析（并发处理）...")
    print("=" * 60)
    
    def analyze_single_question(q_info):
        """分析单个问题的辅助函数（复用web端逻辑）"""
        case_id = q_info['case_id']
        question = q_info['question']
        case_title = q_info['case_title']
        
        case = case_manager.get_case(case_id)
        if not case:
            return None
        
        case_text = case.get('case_text', '')
        judge_decision = case.get('judge_decision', '')
        
        try:
            # AI分析
            ai_decision = ai_api.analyze_case(case_text, question)
            
            # 如果有法官判决，进行对比
            comparison = ''
            similarity_metrics = {}
            if judge_decision:
                try:
                    comparison = ai_api.compare_decisions(ai_decision, judge_decision)
                    # 计算相似度指标
                    try:
                        similarity_metrics = similarity_calculator.calculate_metrics(
                            ai_decision, judge_decision
                        )
                    except:
                        similarity_metrics = {
                            'overall_similarity': 0.0,
                            'has_judge_decision': True
                        }
                except:
                    pass
            else:
                similarity_metrics = {
                    'overall_similarity': 0.0,
                    'has_judge_decision': False
                }
            
            result = {
                'case_id': case_id,
                'case_title': case_title,
                'case_text': case_text,
                'question': question,
                'ai_decision': ai_decision,
                'judge_decision': judge_decision,
                'comparison': comparison,
                'similarity_metrics': similarity_metrics,
                'created_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            }
            
            print(f"✓ 完成: {question[:50]}...")
            return result
        except Exception as e:
            print(f"✗ 分析失败: {question[:50]}... - {str(e)}")
            return None
    
    # 使用线程池并发分析（复用web端的并发逻辑）
    results = []
    total = len(all_questions)
    max_workers = min(MAX_CONCURRENT_WORKERS, total)
    
    print(f"使用 {max_workers} 个并发线程处理 {total} 个问题...")
    
    with SafeThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_question = {executor.submit(analyze_single_question, q_info): q_info 
                              for q_info in all_questions}
        
        completed = 0
        for future in concurrent.futures.as_completed(future_to_question):
            completed += 1
            result = future.result()
            if result:
                results.append(result)
            print(f"进度: {completed}/{total} ({completed*100//total}%)")
    
    # 导出到Excel
    print("\n" + "=" * 60)
    print("导出结果到Excel...")
    print("=" * 60)
    
    if results:
        output_file = excel_exporter.export_analysis_results(results)
        print(f"\n✓ 完成！结果已导出到: {output_file}")
        print(f"  包含 {len(results)} 条分析结果")
    else:
        print("✗ 没有可导出的结果")


if __name__ == '__main__':
    main()

