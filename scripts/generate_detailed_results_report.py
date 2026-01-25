#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
生成7个模型的详细评估结果报告（包含模型背景、Token使用等详细信息）
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

def extract_tokens_from_log(log_file):
    """从日志文件中提取token使用数据"""
    tokens_data = {
        'input_tokens': [],
        'output_tokens': [],
        'total_tokens': []
    }
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                # 匹配 [Token使用] 输入: xxx, 输出: xxx, 总计: xxx
                match = re.search(r'\[Token使用\]\s*输入:\s*(\d+),\s*输出:\s*(\d+),\s*总计:\s*(\d+)', line)
                if match:
                    tokens_data['input_tokens'].append(int(match.group(1)))
                    tokens_data['output_tokens'].append(int(match.group(2)))
                    tokens_data['total_tokens'].append(int(match.group(3)))
    except:
        pass
    
    return tokens_data

def get_model_info():
    """获取模型背景信息"""
    return {
        'GPT-4o': {
            'model_type': '聊天型',
            'reasoning': False,
            'provider': 'OpenAI',
            'release_date': '2024年5月',
            'description': 'GPT-4优化版本，速度快，成本较低'
        },
        'GPT-o3': {
            'model_type': '推理型',
            'reasoning': True,
            'provider': 'OpenAI',
            'release_date': '2025年4月',
            'description': '专门优化的推理模型，擅长复杂推理任务'
        },
        'GPT-5': {
            'model_type': '聊天型',
            'reasoning': False,
            'provider': 'OpenAI',
            'release_date': '2025年8月',
            'description': 'GPT系列最新版本，综合能力强'
        },
        'DeepSeek': {
            'model_type': '聊天型（非thinking模式）',
            'reasoning': False,
            'provider': 'DeepSeek',
            'release_date': '2024年',
            'description': 'DeepSeek标准聊天模型，性价比高'
        },
        'DeepSeek-Thinking': {
            'model_type': '推理型（thinking模式）',
            'reasoning': True,
            'provider': 'DeepSeek',
            'release_date': '2024年',
            'description': 'DeepSeek-Reasoner，thinking模式，推理能力强'
        },
        'Gemini': {
            'model_type': '聊天型',
            'reasoning': False,
            'provider': 'Google',
            'release_date': '2024年',
            'description': 'Gemini 2.5 Flash，快速响应'
        },
        'Claude': {
            'model_type': '聊天型',
            'reasoning': False,
            'provider': 'Anthropic',
            'release_date': '2025年5月',
            'description': 'Claude Opus 4，高质量输出'
        },
        'Qwen-Max': {
            'model_type': '聊天型',
            'reasoning': False,
            'provider': '阿里云',
            'release_date': '2024年',
            'description': '通义千问旗舰模型，性能卓越'
        }
    }

def generate_report():
    """生成完整报告"""
    # 找到最新的results文件夹
    results_dirs = [d for d in os.listdir('data') if d.startswith('results_') and os.path.isdir(os.path.join('data', d))]
    if not results_dirs:
        print("错误：未找到results文件夹")
        return
    
    latest_dir = sorted(results_dirs)[-1]
    results_path = os.path.join('data', latest_dir)
    
    # 模型日志文件映射
    models_logs = {
        'GPT-4o': 'logs/gpt4o_20cases.log',
        'GPT-o3': 'logs/gpt_o3_20cases.log',
        'GPT-5': 'logs/gpt5_20cases.log',
        'DeepSeek': 'logs/deepseek_no_thinking_20cases.log',
        'Gemini': 'logs/gemini_20cases.log',
        'Claude': 'logs/claude_20cases.log',
        'Qwen-Max': 'logs/qwen_max_20cases.log'
    }
    
    # 读取所有Excel文件
    model_files = {
        'GPT-4o': None,
        'GPT-o3': None,
        'GPT-5': None,
        'DeepSeek': None,
        'DeepSeek-Thinking': None,
        'Gemini': None,
        'Claude': None,
        'Qwen-Max': None
    }
    
    for file in os.listdir(results_path):
        if file.endswith('.xlsx'):
            if 'GPT4O_20个案例评估_20260111_144037' in file:
                model_files['GPT-4o'] = os.path.join(results_path, file)
            elif (
                'GPT4O_20个案例评估' in file
                and model_files['GPT-4o'] is None
                and '20260111_144315' not in file
                and '20260111_153605' not in file
            ):
                model_files['GPT-4o'] = os.path.join(results_path, file)
            elif 'GPT-o3_20个案例评估_20260111_144315' in file or 'GPT4O_20个案例评估_20260111_144315' in file:
                model_files['GPT-o3'] = os.path.join(results_path, file)
            elif 'GPT5_20个案例评估_20260111_153605' in file or 'GPT4O_20个案例评估_20260111_153605' in file:
                model_files['GPT-5'] = os.path.join(results_path, file)
            elif 'DEEPSEEK_THINKING' in file or 'THINKING' in file:
                model_files['DeepSeek-Thinking'] = os.path.join(results_path, file)
            elif 'DEEPSEEK' in file and 'THINKING' not in file:
                model_files['DeepSeek'] = os.path.join(results_path, file)
            elif 'GEMINI' in file:
                model_files['Gemini'] = os.path.join(results_path, file)
            elif 'CLAUDE' in file:
                model_files['Claude'] = os.path.join(results_path, file)
            elif 'QWEN' in file:
                model_files['Qwen-Max'] = os.path.join(results_path, file)
    
    # 获取模型背景信息
    model_info = get_model_info()
    
    # 生成报告
    report_lines = []
    report_lines.append("=" * 100)
    report_lines.append("8个模型20个案例评估结果详细报告")
    report_lines.append("=" * 100)
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
        
        # 计算AI回答长度统计（排除空回答）
        if 'AI回答' in df.columns:
            # 过滤掉空回答（nan、空字符串、或'nan'字符串）
            valid_answers = df['AI回答'].copy()
            valid_answers = valid_answers[
                valid_answers.notna() & 
                (valid_answers.astype(str).str.strip() != '') & 
                (valid_answers.astype(str).str.lower() != 'nan')
            ]
            
            if len(valid_answers) > 0:
                answer_lengths = valid_answers.astype(str).str.len()
                avg_answer_length = answer_lengths.mean()
                max_answer_length = answer_lengths.max()
                min_answer_length = answer_lengths.min()
                empty_count = len(df) - len(valid_answers)
            else:
                avg_answer_length = max_answer_length = min_answer_length = 0
                empty_count = len(df)
        else:
            avg_answer_length = max_answer_length = min_answer_length = 0
            empty_count = 0
        
        # 从日志提取Token使用数据
        log_file = models_logs.get(model_name)
        token_stats = {'avg_input': 0, 'avg_output': 0, 'avg_total': 0, 'total_calls': 0}
        if log_file and os.path.exists(log_file):
            tokens_data = extract_tokens_from_log(log_file)
            if tokens_data['input_tokens']:
                token_stats['avg_input'] = sum(tokens_data['input_tokens']) / len(tokens_data['input_tokens'])
                token_stats['avg_output'] = sum(tokens_data['output_tokens']) / len(tokens_data['output_tokens'])
                token_stats['avg_total'] = sum(tokens_data['total_tokens']) / len(tokens_data['total_tokens'])
                token_stats['total_calls'] = len(tokens_data['input_tokens'])
        
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
            'avg_answer_length': avg_answer_length,
            'max_answer_length': max_answer_length,
            'min_answer_length': min_answer_length,
            'empty_answer_count': empty_count,
            'token_stats': token_stats,
            'abandoned_law_count': abandoned_count,
            'abandoned_case_count': abandoned_case_count,
            'abandoned_law_questions': abandoned_law_questions,
            'major_error_count': major_error_count,
            'moderate_error_count': moderate_error_count,
            'minor_error_count': minor_error_count,
            'model_info': model_info.get(model_name, {})
        }
    
    # ========== 一、模型背景信息表 ==========
    report_lines.append("一、模型背景信息")
    report_lines.append("=" * 100)
    report_lines.append("")
    report_lines.append(f"{'模型':<15} {'类型':<20} {'是否推理型':<12} {'提供商':<15} {'发布时间':<15} {'特点描述':<30}")
    report_lines.append("-" * 100)
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in all_results:
            info = all_results[model_name]['model_info']
            reasoning_str = '是' if info.get('reasoning', False) else '否'
            report_lines.append(
                f"{model_name:<15} {info.get('model_type', 'N/A'):<20} {reasoning_str:<12} "
                f"{info.get('provider', 'N/A'):<15} {info.get('release_date', 'N/A'):<15} "
                f"{info.get('description', 'N/A'):<30}"
            )
    
    report_lines.append("")
    
    # ========== 二、综合性能对比表 ==========
    report_lines.append("二、综合性能对比")
    report_lines.append("=" * 100)
    report_lines.append("")
    report_lines.append(f"{'模型':<15} {'案例数':<8} {'问题数':<8} {'平均分':<15} {'最高分':<12} {'最低分':<12} {'引用废弃法案':<15}")
    report_lines.append("-" * 100)
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in all_results:
            stats = all_results[model_name]
            report_lines.append(
                f"{model_name:<15} {stats['total_cases']:<8} {stats['total_questions']:<8} "
                f"{stats['avg_score']:.2f}/20 ({stats['avg_percent']:.1f}%)  "
                f"{stats['max_score']:.2f}/20      {stats['min_score']:.2f}/20      "
                f"{stats['abandoned_law_count']:<15}"
            )
    
    report_lines.append("")
    
    # ========== 三、Token使用统计表 ==========
    report_lines.append("三、Token使用统计（仅统计步骤3：AI回答生成）")
    report_lines.append("=" * 100)
    report_lines.append("")
    report_lines.append(f"{'模型':<15} {'平均输入Token':<18} {'平均输出Token':<18} {'平均总计Token':<18} {'API调用次数':<15}")
    report_lines.append("-" * 100)
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in all_results:
            token_stats = all_results[model_name]['token_stats']
            if token_stats['total_calls'] > 0:
                report_lines.append(
                    f"{model_name:<15} {token_stats['avg_input']:>15.0f}  {token_stats['avg_output']:>15.0f}  "
                    f"{token_stats['avg_total']:>15.0f}  {token_stats['total_calls']:>15}"
                )
            else:
                report_lines.append(f"{model_name:<15} {'数据不可用':<18} {'数据不可用':<18} {'数据不可用':<18} {'数据不可用':<15}")
    
    report_lines.append("")
    
    # ========== 四、回答长度统计表 ==========
    report_lines.append("四、AI回答长度统计（仅统计有效回答，排除API调用失败的空回答）")
    report_lines.append("=" * 100)
    report_lines.append("")
    report_lines.append(f"{'模型':<15} {'平均长度(字符)':<18} {'最大长度(字符)':<18} {'最小长度(字符)':<18} {'空回答数':<12}")
    report_lines.append("-" * 100)
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in all_results:
            stats = all_results[model_name]
            empty_count = stats.get('empty_answer_count', 0)
            if stats['avg_answer_length'] > 0:
                report_lines.append(
                    f"{model_name:<15} {stats['avg_answer_length']:>15.0f}  {stats['max_answer_length']:>15.0f}  "
                    f"{stats['min_answer_length']:>15.0f}  {empty_count:>10}"
                )
            else:
                report_lines.append(
                    f"{model_name:<15} {'全部为空':<18} {'全部为空':<18} {'全部为空':<18}  {empty_count:>10}"
                )
    
    report_lines.append("")
    report_lines.append("说明：空回答通常由API调用失败导致（如超时、content字段缺失等），已从统计中排除。")
    report_lines.append("")
    
    # ========== 五、引用废弃法案分析（重要指标）==========
    report_lines.append("五、引用废弃法案分析（重要指标）")
    report_lines.append("=" * 100)
    report_lines.append("")
    
    # 按废弃法案引用数量排序
    sorted_models = sorted(all_results.items(), key=lambda x: x[1]['abandoned_law_count'])
    
    report_lines.append(f"{'模型':<15} {'引用废弃法案问题数':<22} {'涉及案例数':<15} {'占比':<10} {'排名':<8}")
    report_lines.append("-" * 100)
    
    for rank, (model_name, stats) in enumerate(sorted_models, 1):
        percentage = (stats['abandoned_law_count'] / stats['total_questions'] * 100) if stats['total_questions'] > 0 else 0
        report_lines.append(
            f"{model_name:<15} {stats['abandoned_law_count']:<22} {stats['abandoned_case_count']:<15} "
            f"{percentage:>6.1f}%    {rank:>3}"
        )
    
    report_lines.append("")
    
    # ========== 六、详细错误统计表 ==========
    report_lines.append("六、详细错误统计")
    report_lines.append("=" * 100)
    report_lines.append("")
    
    report_lines.append(f"{'模型':<15} {'重大错误':<12} {'明显错误':<12} {'微小错误':<12} {'引用废弃法案':<15}")
    report_lines.append("-" * 100)
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in all_results:
            stats = all_results[model_name]
            report_lines.append(
                f"{model_name:<15} {stats['major_error_count']:<12} {stats['moderate_error_count']:<12} "
                f"{stats['minor_error_count']:<12} {stats['abandoned_law_count']:<15}"
            )
    
    report_lines.append("")
    
    # ========== 七、综合排名表 ==========
    report_lines.append("七、综合排名")
    report_lines.append("=" * 100)
    report_lines.append("")
    
    # 按平均分排名
    sorted_by_score = sorted(all_results.items(), key=lambda x: x[1]['avg_score'], reverse=True)
    report_lines.append("7.1 按平均分排名（越高越好）")
    report_lines.append("-" * 100)
    report_lines.append(f"{'排名':<6} {'模型':<15} {'平均分':<15} {'百分制':<12}")
    report_lines.append("-" * 100)
    for rank, (model_name, stats) in enumerate(sorted_by_score, 1):
        report_lines.append(f"{rank:<6} {model_name:<15} {stats['avg_score']:.2f}/20        {stats['avg_percent']:.1f}%")
    
    report_lines.append("")
    
    # 按引用废弃法案排名
    report_lines.append("7.2 按引用废弃法案数量排名（越少越好）")
    report_lines.append("-" * 100)
    report_lines.append(f"{'排名':<6} {'模型':<15} {'引用废弃法案问题数':<22} {'占比':<10}")
    report_lines.append("-" * 100)
    for rank, (model_name, stats) in enumerate(sorted_models, 1):
        percentage = (stats['abandoned_law_count'] / stats['total_questions'] * 100) if stats['total_questions'] > 0 else 0
        report_lines.append(f"{rank:<6} {model_name:<15} {stats['abandoned_law_count']:<22} {percentage:>6.1f}%")
    
    report_lines.append("")
    
    # ========== 八、引用废弃法案的详细记录 ==========
    report_lines.append("八、引用废弃法案的详细记录")
    report_lines.append("=" * 100)
    report_lines.append("")
    
    for model_name, stats in sorted_models:
        if stats['abandoned_law_count'] > 0:
            report_lines.append(f"\n【{model_name}】引用废弃法案记录（共{stats['abandoned_law_count']}个问题）")
            report_lines.append("-" * 100)
            
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
    
    # ========== 九、数据分析与洞察 ==========
    report_lines.append("九、数据分析与洞察")
    report_lines.append("=" * 100)
    report_lines.append("")
    
    # 1. 整体表现分析
    report_lines.append("9.1 整体表现分析")
    report_lines.append("-" * 100)
    
    sorted_by_score = sorted(all_results.items(), key=lambda x: x[1]['avg_score'], reverse=True)
    top_model = sorted_by_score[0]
    bottom_model = sorted_by_score[-1]
    
    report_lines.append(f"• 最佳表现模型：{top_model[0]}，平均分 {top_model[1]['avg_score']:.2f}/20 ({top_model[1]['avg_percent']:.1f}%)")
    report_lines.append(f"• 最低表现模型：{bottom_model[0]}，平均分 {bottom_model[1]['avg_score']:.2f}/20 ({bottom_model[1]['avg_percent']:.1f}%)")
    report_lines.append(f"• 平均分差距：{top_model[1]['avg_score'] - bottom_model[1]['avg_score']:.2f}分（{top_model[1]['avg_percent'] - bottom_model[1]['avg_percent']:.1f}个百分点）")
    
    # 计算所有模型的平均分
    all_avg_scores = [stats['avg_score'] for stats in all_results.values()]
    overall_avg = sum(all_avg_scores) / len(all_avg_scores) if all_avg_scores else 0
    report_lines.append(f"• 所有模型平均分：{overall_avg:.2f}/20 ({overall_avg/20*100:.1f}%)")
    
    # 高于平均分的模型
    above_avg = [name for name, stats in all_results.items() if stats['avg_score'] > overall_avg]
    report_lines.append(f"• 高于平均分的模型：{', '.join(above_avg)}（共{len(above_avg)}个）")
    report_lines.append("")
    
    # 2. 推理型模型 vs 非推理型模型
    report_lines.append("9.2 推理型模型 vs 非推理型模型对比")
    report_lines.append("-" * 100)
    
    reasoning_models = []
    non_reasoning_models = []
    
    for model_name, stats in all_results.items():
        if stats['model_info'].get('reasoning', False):
            reasoning_models.append((model_name, stats))
        else:
            non_reasoning_models.append((model_name, stats))
    
    if reasoning_models:
        reasoning_avg = sum(s['avg_score'] for _, s in reasoning_models) / len(reasoning_models)
        reasoning_names = [n for n, _ in reasoning_models]
        report_lines.append(f"• 推理型模型（{len(reasoning_models)}个）：{', '.join(reasoning_names)}")
        report_lines.append(f"  平均分：{reasoning_avg:.2f}/20 ({reasoning_avg/20*100:.1f}%)")
    
    if non_reasoning_models:
        non_reasoning_avg = sum(s['avg_score'] for _, s in non_reasoning_models) / len(non_reasoning_models)
        non_reasoning_names = [n for n, _ in non_reasoning_models]
        report_lines.append(f"• 非推理型模型（{len(non_reasoning_models)}个）：{', '.join(non_reasoning_names)}")
        report_lines.append(f"  平均分：{non_reasoning_avg:.2f}/20 ({non_reasoning_avg/20*100:.1f}%)")
    
    if reasoning_models and non_reasoning_models:
        diff = reasoning_avg - non_reasoning_avg
        if diff > 0:
            report_lines.append(f"• 推理型模型平均分比非推理型模型高 {diff:.2f}分（{diff/20*100:.1f}个百分点）")
        else:
            report_lines.append(f"• 非推理型模型平均分比推理型模型高 {abs(diff):.2f}分（{abs(diff)/20*100:.1f}个百分点）")
    report_lines.append("")
    
    # 3. DeepSeek Thinking vs Non-Thinking 对比
    report_lines.append("9.3 DeepSeek Thinking模式 vs 非Thinking模式对比")
    report_lines.append("-" * 100)
    
    if 'DeepSeek' in all_results and 'DeepSeek-Thinking' in all_results:
        ds_normal = all_results['DeepSeek']
        ds_thinking = all_results['DeepSeek-Thinking']
        
        score_diff = ds_thinking['avg_score'] - ds_normal['avg_score']
        abandoned_diff = ds_normal['abandoned_law_count'] - ds_thinking['abandoned_law_count']
        
        report_lines.append(f"• 平均分：Thinking模式 ({ds_thinking['avg_score']:.2f}/20) vs 非Thinking模式 ({ds_normal['avg_score']:.2f}/20)")
        if score_diff > 0:
            report_lines.append(f"  Thinking模式平均分高 {score_diff:.2f}分（{score_diff/20*100:.1f}个百分点）")
        else:
            report_lines.append(f"  非Thinking模式平均分高 {abs(score_diff):.2f}分（{abs(score_diff)/20*100:.1f}个百分点）")
        
        report_lines.append(f"• 引用废弃法案：Thinking模式 ({ds_thinking['abandoned_law_count']}个) vs 非Thinking模式 ({ds_normal['abandoned_law_count']}个)")
        if abandoned_diff > 0:
            report_lines.append(f"  Thinking模式引用废弃法案少 {abandoned_diff}个，表现更优")
        elif abandoned_diff < 0:
            report_lines.append(f"  非Thinking模式引用废弃法案少 {abs(abandoned_diff)}个，表现更优")
        else:
            report_lines.append(f"  两者在引用废弃法案方面表现相同")
        
        report_lines.append(f"• 错误统计：")
        report_lines.append(f"  Thinking模式 - 重大错误：{ds_thinking['major_error_count']}，明显错误：{ds_thinking['moderate_error_count']}，微小错误：{ds_thinking['minor_error_count']}")
        report_lines.append(f"  非Thinking模式 - 重大错误：{ds_normal['major_error_count']}，明显错误：{ds_normal['moderate_error_count']}，微小错误：{ds_normal['minor_error_count']}")
    report_lines.append("")
    
    # 4. 引用废弃法案分析
    report_lines.append("9.4 引用废弃法案深度分析")
    report_lines.append("-" * 100)
    
    sorted_models = sorted(all_results.items(), key=lambda x: x[1]['abandoned_law_count'])
    perfect_models = [name for name, stats in sorted_models if stats['abandoned_law_count'] == 0]
    worst_model = sorted_models[-1]
    
    report_lines.append(f"• 零引用废弃法案的模型：{', '.join(perfect_models)}（共{len(perfect_models)}个）")
    report_lines.append(f"• 引用废弃法案最多的模型：{worst_model[0]}，共{worst_model[1]['abandoned_law_count']}个问题")
    report_lines.append(f"• 引用废弃法案占比：")
    
    for model_name, stats in sorted_models:
        percentage = (stats['abandoned_law_count'] / stats['total_questions'] * 100) if stats['total_questions'] > 0 else 0
        if stats['abandoned_law_count'] > 0:
            report_lines.append(f"  {model_name}: {stats['abandoned_law_count']}个问题（{percentage:.1f}%）")
    
    report_lines.append("")
    
    # 5. API调用稳定性分析
    report_lines.append("9.5 API调用稳定性分析")
    report_lines.append("-" * 100)
    
    unstable_models = []
    for model_name, stats in all_results.items():
        empty_count = stats.get('empty_answer_count', 0)
        if empty_count > 0:
            failure_rate = (empty_count / stats['total_questions'] * 100) if stats['total_questions'] > 0 else 0
            unstable_models.append((model_name, empty_count, failure_rate))
    
    if unstable_models:
        report_lines.append("• API调用失败的模型：")
        for model_name, count, rate in sorted(unstable_models, key=lambda x: x[2], reverse=True):
            report_lines.append(f"  {model_name}: {count}个空回答（失败率 {rate:.1f}%）")
            if rate > 50:
                report_lines.append(f"    ⚠️ 警告：失败率超过50%，可能存在严重的API稳定性问题")
            elif rate > 20:
                report_lines.append(f"    ⚠️ 注意：失败率较高，建议检查API配置或网络连接")
    else:
        report_lines.append("• 所有模型API调用均成功，无空回答")
    
    stable_models = [name for name, stats in all_results.items() if stats.get('empty_answer_count', 0) == 0]
    if stable_models:
        report_lines.append(f"• API调用完全稳定的模型：{', '.join(stable_models)}（共{len(stable_models)}个）")
    report_lines.append("")
    
    # 6. Token使用效率分析
    report_lines.append("9.6 Token使用效率分析")
    report_lines.append("-" * 100)
    
    models_with_tokens = []
    for model_name, stats in all_results.items():
        token_stats = stats.get('token_stats', {})
        if token_stats.get('total_calls', 0) > 0:
            avg_output = token_stats.get('avg_output', 0)
            avg_total = token_stats.get('avg_total', 0)
            avg_score = stats['avg_score']
            # 计算效率：平均分 / 平均总token数（分数越高、token越少越好）
            if avg_total > 0:
                efficiency = avg_score / avg_total * 1000  # 放大1000倍便于比较
                models_with_tokens.append((model_name, avg_output, avg_total, avg_score, efficiency))
    
    if models_with_tokens:
        # 按效率排序
        models_with_tokens.sort(key=lambda x: x[4], reverse=True)
        report_lines.append("• Token使用效率排名（综合考虑得分和token消耗）：")
        for rank, (name, output, total, score, eff) in enumerate(models_with_tokens, 1):
            report_lines.append(f"  {rank}. {name}: 平均输出{output:.0f}tokens，总计{total:.0f}tokens，得分{score:.2f}/20，效率指数{eff:.3f}")
        
        most_efficient = models_with_tokens[0]
        least_efficient = models_with_tokens[-1]
        report_lines.append(f"• 最高效模型：{most_efficient[0]}（效率指数{most_efficient[4]:.3f}）")
        report_lines.append(f"• 最低效模型：{least_efficient[0]}（效率指数{least_efficient[4]:.3f}）")
    else:
        report_lines.append("• Token使用数据不可用")
    report_lines.append("")
    
    # 7. 综合建议
    report_lines.append("9.7 综合建议与结论")
    report_lines.append("-" * 100)
    
    # 找出最佳模型（综合考虑多个指标）
    best_overall = None
    best_score = -1
    
    for model_name, stats in all_results.items():
        # 综合评分：平均分权重70%，引用废弃法案权重30%（越少越好）
        abandoned_penalty = stats['abandoned_law_count'] * 0.5  # 每个废弃法案扣0.5分
        empty_penalty = stats.get('empty_answer_count', 0) * 0.3  # 每个空回答扣0.3分
        comprehensive_score = stats['avg_score'] - abandoned_penalty - empty_penalty
        
        if comprehensive_score > best_score:
            best_score = comprehensive_score
            best_overall = model_name
    
    if best_overall:
        report_lines.append(f"• 综合最佳模型：{best_overall}")
        report_lines.append(f"  - 平均分：{all_results[best_overall]['avg_score']:.2f}/20")
        report_lines.append(f"  - 引用废弃法案：{all_results[best_overall]['abandoned_law_count']}个")
        report_lines.append(f"  - API稳定性：{all_results[best_overall].get('empty_answer_count', 0)}个空回答")
    
    report_lines.append("")
    report_lines.append("• 主要发现：")
    
    # 找出关键发现
    if len(perfect_models) > 0:
        report_lines.append(f"  1. {len(perfect_models)}个模型在引用废弃法案方面表现完美（零引用）")
    
    if reasoning_models and non_reasoning_models:
        if reasoning_avg > non_reasoning_avg:
            report_lines.append(f"  2. 推理型模型整体表现优于非推理型模型")
        else:
            report_lines.append(f"  2. 非推理型模型整体表现优于推理型模型")
    
    if 'DeepSeek-Thinking' in all_results and 'DeepSeek' in all_results:
        if all_results['DeepSeek-Thinking']['avg_score'] > all_results['DeepSeek']['avg_score']:
            report_lines.append(f"  3. DeepSeek Thinking模式在平均分和引用废弃法案方面均优于非Thinking模式")
    
    if unstable_models:
        worst_unstable = max(unstable_models, key=lambda x: x[2])
        report_lines.append(f"  4. {worst_unstable[0]}的API调用稳定性需要关注（失败率{worst_unstable[2]:.1f}%）")
    
    report_lines.append("")
    report_lines.append("• 使用建议：")
    report_lines.append("  - 对于准确性要求高的场景，推荐使用DeepSeek-Thinking或DeepSeek（非Thinking）")
    report_lines.append("  - 对于需要避免引用废弃法案的场景，推荐使用DeepSeek-Thinking或Gemini")
    report_lines.append("  - 对于API稳定性要求高的场景，避免使用GPT-5（失败率过高）")
    report_lines.append("  - 推理型模型（GPT-o3、DeepSeek-Thinking）在复杂法律分析任务中表现更优")
    
    report_lines.append("")
    report_lines.append("=" * 100)
    report_lines.append("报告结束")
    report_lines.append("=" * 100)
    
    # 保存报告
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    report_file = f'data/results_详细报告_{timestamp}.txt'
    
    with open(report_file, 'w', encoding='utf-8') as f:
        f.write('\n'.join(report_lines))
    
    print(f"\n✓ 详细报告已生成: {report_file}")
    print(f"  总行数: {len(report_lines)}")
    
    # 同时输出到控制台（前100行）
    print("\n" + "\n".join(report_lines[:100]))
    if len(report_lines) > 100:
        print(f"\n... (共{len(report_lines)}行，完整内容请查看文件)")

if __name__ == '__main__':
    generate_report()
