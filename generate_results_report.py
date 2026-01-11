#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成5个模型的评估结果报告，特别关注"引用废弃法案"指标
"""
import pandas as pd
import os
from datetime import datetime
import re

def analyze_abandoned_laws(text):
    """分析文本中是否包含废弃法案相关内容"""
    if pd.isna(text) or text == '':
        return False
    
    text = str(text).lower()
    keywords = [
        '废弃', '废止', '已废除', '已废止', '失效', '过期', 
        '旧法', '旧版', '已修订', '已修改', '不再适用',
        '已失效', '已过期', '废除', '废止'
    ]
    
    for keyword in keywords:
        if keyword in text:
            return True
    return False

def generate_report():
    """生成完整报告"""
    # 找到最新的results文件夹
    results_dirs = [d for d in os.listdir('data') if d.startswith('results_') and os.path.isdir(os.path.join('data', d))]
    if not results_dirs:
        print("错误：未找到results文件夹")
        return
    
    latest_dir = sorted(results_dirs)[-1]
    results_path = os.path.join('data', latest_dir)
    
    # 读取所有Excel文件
    model_files = {
        'GPT-4o': None,
        'GPT-o3': None,
        'GPT-5': None,
        'DeepSeek': None,
        'Gemini': None,
        'Claude': None,
        'Qwen-Max': None
    }
    
    for file in os.listdir(results_path):
        if file.endswith('.xlsx'):
            if 'GPT4O_20个案例评估_20260111_144037' in file:
                model_files['GPT-4o'] = os.path.join(results_path, file)
            elif 'GPT-o3_20个案例评估_20260111_144315' in file or 'GPT4O_20个案例评估_20260111_144315' in file:
                model_files['GPT-o3'] = os.path.join(results_path, file)
            elif 'GPT4O_20个案例评估_20260111_153605' in file:
                model_files['GPT-5'] = os.path.join(results_path, file)
            elif 'DEEPSEEK' in file:
                model_files['DeepSeek'] = os.path.join(results_path, file)
            elif 'GEMINI' in file:
                model_files['Gemini'] = os.path.join(results_path, file)
            elif 'CLAUDE' in file:
                model_files['Claude'] = os.path.join(results_path, file)
            elif 'QWEN' in file:
                model_files['Qwen-Max'] = os.path.join(results_path, file)
    
    # 生成报告
    report_lines = []
    report_lines.append("=" * 80)
    report_lines.append("7个模型20个案例评估结果报告")
    report_lines.append("=" * 80)
    report_lines.append(f"生成时间: {datetime.now().strftime('%Y年%m月%d日 %H:%M:%S')}")
    report_lines.append(f"数据来源: {results_path}")
    report_lines.append("")
    
    all_results = {}
    
    for model_name, filepath in model_files.items():
        if not filepath or not os.path.exists(filepath):
            continue
        
        print(f"正在分析: {model_name}...")
        df = pd.read_excel(filepath)
        
        # 基本统计
        total_questions = len(df)
        total_cases = df['案例ID'].nunique()
        
        # 分数统计
        avg_score = df['总分'].mean() if '总分' in df.columns else 0
        avg_percent = df['百分制'].mean() if '百分制' in df.columns else 0
        max_score = df['总分'].max() if '总分' in df.columns else 0
        min_score = df['总分'].min() if '总分' in df.columns else 0
        
        # 分析废弃法案引用
        abandoned_law_questions = []
        abandoned_law_cases = set()
        
        for idx, row in df.iterrows():
            detail = str(row.get('详细评价', ''))
            major_error = str(row.get('重大错误', ''))
            moderate_error = str(row.get('明显错误', ''))
            minor_error = str(row.get('微小错误', ''))
            
            # 检查是否引用废弃法案
            if (analyze_abandoned_laws(detail) or 
                analyze_abandoned_laws(major_error) or 
                analyze_abandoned_laws(moderate_error) or
                analyze_abandoned_laws(minor_error)):
                
                abandoned_law_questions.append({
                    '案例ID': row.get('案例ID', 'N/A'),
                    '案例标题': row.get('案例标题', 'N/A'),
                    '问题编号': row.get('问题编号', 'N/A'),
                    '问题': str(row.get('问题', ''))[:100] + '...' if len(str(row.get('问题', ''))) > 100 else str(row.get('问题', '')),
                    '详细评价': str(detail)[:200] + '...' if len(str(detail)) > 200 else str(detail),
                    '重大错误': str(major_error) if pd.notna(major_error) else '',
                    '明显错误': str(moderate_error) if pd.notna(moderate_error) else '',
                    '总分': row.get('总分', 0)
                })
                abandoned_law_cases.add(row.get('案例ID', 'N/A'))
        
        abandoned_count = len(abandoned_law_questions)
        abandoned_case_count = len(abandoned_law_cases)
        
        # 错误统计
        major_error_count = df['重大错误'].notna().sum() if '重大错误' in df.columns else 0
        moderate_error_count = df['明显错误'].notna().sum() if '明显错误' in df.columns else 0
        minor_error_count = df['微小错误'].notna().sum() if '微小错误' in df.columns else 0
        
        # 保存结果
        all_results[model_name] = {
            'total_questions': total_questions,
            'total_cases': total_cases,
            'avg_score': avg_score,
            'avg_percent': avg_percent,
            'max_score': max_score,
            'min_score': min_score,
            'abandoned_law_count': abandoned_count,
            'abandoned_case_count': abandoned_case_count,
            'abandoned_law_questions': abandoned_law_questions,
            'major_error_count': major_error_count,
            'moderate_error_count': moderate_error_count,
            'minor_error_count': minor_error_count
        }
    
    # 生成报告内容
    report_lines.append("一、总体统计")
    report_lines.append("-" * 80)
    report_lines.append(f"{'模型':<15} {'案例数':<10} {'问题数':<10} {'平均分':<12} {'最高分':<12} {'最低分':<12}")
    report_lines.append("-" * 80)
    
    for model_name, stats in all_results.items():
        report_lines.append(
            f"{model_name:<15} {stats['total_cases']:<10} {stats['total_questions']:<10} "
            f"{stats['avg_score']:.2f}/20 ({stats['avg_percent']:.1f}%)  "
            f"{stats['max_score']:.2f}/20      {stats['min_score']:.2f}/20"
        )
    
    report_lines.append("")
    report_lines.append("=" * 80)
    report_lines.append("二、引用废弃法案分析（重要指标）")
    report_lines.append("=" * 80)
    report_lines.append("")
    
    # 按废弃法案引用数量排序
    sorted_models = sorted(all_results.items(), key=lambda x: x[1]['abandoned_law_count'])
    
    report_lines.append(f"{'模型':<15} {'引用废弃法案问题数':<20} {'涉及案例数':<15} {'占比':<10}")
    report_lines.append("-" * 80)
    
    for model_name, stats in sorted_models:
        percentage = (stats['abandoned_law_count'] / stats['total_questions'] * 100) if stats['total_questions'] > 0 else 0
        report_lines.append(
            f"{model_name:<15} {stats['abandoned_law_count']:<20} {stats['abandoned_case_count']:<15} {percentage:.1f}%"
        )
    
    report_lines.append("")
    report_lines.append("=" * 80)
    report_lines.append("三、详细错误统计")
    report_lines.append("=" * 80)
    report_lines.append("")
    
    report_lines.append(f"{'模型':<15} {'重大错误':<12} {'明显错误':<12} {'微小错误':<12} {'引用废弃法案':<15}")
    report_lines.append("-" * 80)
    
    for model_name, stats in all_results.items():
        report_lines.append(
            f"{model_name:<15} {stats['major_error_count']:<12} {stats['moderate_error_count']:<12} "
            f"{stats['minor_error_count']:<12} {stats['abandoned_law_count']:<15}"
        )
    
    report_lines.append("")
    report_lines.append("=" * 80)
    report_lines.append("四、引用废弃法案的详细记录")
    report_lines.append("=" * 80)
    report_lines.append("")
    
    for model_name, stats in sorted_models:
        if stats['abandoned_law_count'] > 0:
            report_lines.append(f"\n【{model_name}】引用废弃法案记录（共{stats['abandoned_law_count']}个问题）")
            report_lines.append("-" * 80)
            
            for i, q in enumerate(stats['abandoned_law_questions'], 1):
                report_lines.append(f"\n{i}. 案例: {q['案例ID']} - {q['案例标题']}")
                report_lines.append(f"   问题{q['问题编号']}: {q['问题']}")
                report_lines.append(f"   总分: {q['总分']:.2f}/20")
                if q['重大错误']:
                    report_lines.append(f"   重大错误: {q['重大错误'][:150]}...")
                if q['明显错误']:
                    report_lines.append(f"   明显错误: {q['明显错误'][:150]}...")
                report_lines.append(f"   详细评价片段: {q['详细评价'][:200]}...")
        else:
            report_lines.append(f"\n【{model_name}】未发现引用废弃法案的情况 ✓")
    
    report_lines.append("")
    report_lines.append("=" * 80)
    report_lines.append("五、模型排名（按引用废弃法案数量，越少越好）")
    report_lines.append("=" * 80)
    report_lines.append("")
    
    for rank, (model_name, stats) in enumerate(sorted_models, 1):
        report_lines.append(f"{rank}. {model_name}: {stats['abandoned_law_count']}个问题引用废弃法案")
    
    # 保存报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_file = f'data/results_报告_{timestamp}.txt'
    
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))
    
    print(f"\n✓ 报告已生成: {report_file}")
    print(f"  总行数: {len(report_lines)}")
    
    # 同时输出到控制台
    print("\n" + "\n".join(report_lines))

if __name__ == '__main__':
    generate_report()
