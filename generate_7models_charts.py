#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate comparison charts for 7 models evaluation results
All labels in English to avoid Chinese display issues
"""
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import os
from datetime import datetime
import re

# Set matplotlib to use English fonts, avoid Chinese display issues
plt.rcParams['font.family'] = 'Arial'
plt.rcParams['axes.unicode_minus'] = False

# Set style
try:
    plt.style.use('seaborn-v0_8-whitegrid')
except:
    try:
        plt.style.use('seaborn-whitegrid')
    except:
        plt.style.use('default')

# Model colors
MODEL_COLORS = {
    'GPT-4o': '#A23B72',
    'GPT-o3': '#F18F01',
    'GPT-5': '#C73E1D',
    'DeepSeek': '#2E86AB',
    'DeepSeek-Thinking': '#1A5F7A',
    'Gemini': '#6A994E',
    'Claude': '#A23B72',
    'Qwen-Max': '#2E86AB'
}

def extract_tokens_from_log(log_file):
    """Extract token usage data from log file"""
    tokens_data = {
        'input_tokens': [],
        'output_tokens': [],
        'total_tokens': []
    }
    
    try:
        with open(log_file, 'r', encoding='utf-8') as f:
            for line in f:
                match = re.search(r'\[Token使用\]\s*输入:\s*(\d+),\s*输出:\s*(\d+),\s*总计:\s*(\d+)', line)
                if match:
                    tokens_data['input_tokens'].append(int(match.group(1)))
                    tokens_data['output_tokens'].append(int(match.group(2)))
                    tokens_data['total_tokens'].append(int(match.group(3)))
    except:
        pass
    
    return tokens_data

def analyze_abandoned_laws(text):
    """Check if text contains abandoned law references"""
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

def load_model_data(results_path):
    """Load all model data from results folder"""
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
            elif 'DEEPSEEK_THINKING' in file or ('THINKING' in file and 'DEEPSEEK' in file):
                model_files['DeepSeek-Thinking'] = os.path.join(results_path, file)
            elif 'DEEPSEEK' in file and 'THINKING' not in file:
                model_files['DeepSeek'] = os.path.join(results_path, file)
            elif 'GEMINI' in file:
                model_files['Gemini'] = os.path.join(results_path, file)
            elif 'CLAUDE' in file:
                model_files['Claude'] = os.path.join(results_path, file)
            elif 'QWEN' in file:
                model_files['Qwen-Max'] = os.path.join(results_path, file)
    
    # Load dataframes
    model_data = {}
    for model_name, filepath in model_files.items():
        if filepath and os.path.exists(filepath):
            try:
                df = pd.read_excel(filepath)
                model_data[model_name] = df
                print(f"  Loaded {model_name}: {len(df)} rows")
            except Exception as e:
                print(f"  Error loading {model_name}: {e}")
        else:
            print(f"  Warning: {model_name} file not found")
    
    return model_data

def plot_average_score_comparison(model_data, output_dir):
    """Average total score comparison bar chart"""
    fig, ax = plt.subplots(figsize=(14, 8))
    
    models = []
    scores = []
    colors = []
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in model_data:
            df = model_data[model_name]
            avg_score = df['总分'].mean() if '总分' in df.columns else 0
            models.append(model_name)
            scores.append(avg_score)
            colors.append(MODEL_COLORS.get(model_name, '#808080'))
    
    bars = ax.bar(models, scores, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)
    
    # Add value labels
    for bar, score in zip(bars, scores):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height + max(scores) * 0.02,
               f'{score:.2f}', ha='center', va='bottom', fontsize=11, fontweight='bold')
    
    ax.set_ylabel('Average Total Score (out of 20)', fontsize=12, fontweight='bold')
    ax.set_title('Average Total Score Comparison (20 Cases)', fontsize=14, fontweight='bold')
    ax.set_ylim(0, max(scores) * 1.15)
    ax.grid(axis='y', alpha=0.3)
    plt.xticks(rotation=15, ha='right')
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'chart_avg_score_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def plot_percentage_comparison(model_data, output_dir):
    """Percentage score comparison bar chart"""
    fig, ax = plt.subplots(figsize=(14, 8))
    
    models = []
    scores = []
    colors = []
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in model_data:
            df = model_data[model_name]
            avg_percent = df['百分制'].mean() if '百分制' in df.columns else 0
            models.append(model_name)
            scores.append(avg_percent)
            colors.append(MODEL_COLORS.get(model_name, '#808080'))
    
    bars = ax.bar(models, scores, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)
    
    # Add value labels
    for bar, score in zip(bars, scores):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height + 2,
               f'{score:.1f}%', ha='center', va='bottom', fontsize=11, fontweight='bold')
    
    ax.set_ylabel('Average Percentage Score (%)', fontsize=12, fontweight='bold')
    ax.set_title('Average Percentage Score Comparison (20 Cases)', fontsize=14, fontweight='bold')
    ax.set_ylim(0, 100)
    ax.grid(axis='y', alpha=0.3)
    plt.xticks(rotation=15, ha='right')
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'chart_percentage_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def plot_abandoned_laws_comparison(model_data, output_dir):
    """Abandoned laws reference comparison"""
    fig, ax = plt.subplots(figsize=(14, 8))
    
    models = []
    counts = []
    colors = []
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in model_data:
            df = model_data[model_name]
            abandoned_count = 0
            
            for idx, row in df.iterrows():
                detail = str(row.get('详细评价', ''))
                major_error = str(row.get('重大错误', ''))
                moderate_error = str(row.get('明显错误', ''))
                minor_error = str(row.get('微小错误', ''))
                
                if (analyze_abandoned_laws(detail) or 
                    analyze_abandoned_laws(major_error) or 
                    analyze_abandoned_laws(moderate_error) or
                    analyze_abandoned_laws(minor_error)):
                    abandoned_count += 1
            
            models.append(model_name)
            counts.append(abandoned_count)
            colors.append(MODEL_COLORS.get(model_name, '#808080'))
    
    # Sort by count (ascending - lower is better)
    sorted_data = sorted(zip(models, counts, colors), key=lambda x: x[1])
    models, counts, colors = zip(*sorted_data) if sorted_data else ([], [], [])
    
    bars = ax.barh(models, counts, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)
    
    # Add value labels
    for bar, count in zip(bars, counts):
        width = bar.get_width()
        ax.text(width + max(counts) * 0.02, bar.get_y() + bar.get_height()/2.,
               f'{int(count)}', ha='left', va='center', fontsize=11, fontweight='bold')
    
    ax.set_xlabel('Number of Questions with Abandoned Law References', fontsize=12, fontweight='bold')
    ax.set_title('Abandoned Law References Comparison (Lower is Better)', fontsize=14, fontweight='bold')
    ax.set_xlim(0, max(counts) * 1.2 if counts else 15)
    ax.grid(axis='x', alpha=0.3)
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'chart_abandoned_laws_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def plot_token_usage_comparison(model_data, output_dir):
    """Token usage comparison"""
    models_logs = {
        'GPT-4o': 'logs/gpt4o_20cases.log',
        'GPT-o3': 'logs/gpt_o3_20cases.log',
        'GPT-5': 'logs/gpt5_20cases.log',
        'DeepSeek': 'logs/deepseek_no_thinking_20cases.log',
        'Gemini': 'logs/gemini_20cases.log',
        'Claude': 'logs/claude_20cases.log',
        'Qwen-Max': 'logs/qwen_max_20cases.log'
    }
    
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    
    models = []
    avg_input = []
    avg_output = []
    colors = []
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in model_data:
            log_file = models_logs.get(model_name)
            if log_file and os.path.exists(log_file):
                tokens_data = extract_tokens_from_log(log_file)
                if tokens_data['input_tokens']:
                    models.append(model_name)
                    avg_input.append(sum(tokens_data['input_tokens']) / len(tokens_data['input_tokens']))
                    avg_output.append(sum(tokens_data['output_tokens']) / len(tokens_data['output_tokens']))
                    colors.append(MODEL_COLORS.get(model_name, '#808080'))
    
    if models:
        x = np.arange(len(models))
        width = 0.35
        
        bars1 = ax1.bar(x - width/2, avg_input, width, label='Input Tokens', color='#2E86AB', alpha=0.8)
        bars2 = ax1.bar(x + width/2, avg_output, width, label='Output Tokens', color='#F18F01', alpha=0.8)
        
        ax1.set_ylabel('Average Tokens', fontsize=12, fontweight='bold')
        ax1.set_title('Average Token Usage per API Call', fontsize=14, fontweight='bold')
        ax1.set_xticks(x)
        ax1.set_xticklabels(models, rotation=15, ha='right')
        ax1.legend(fontsize=11)
        ax1.grid(axis='y', alpha=0.3)
        
        # Add value labels
        for bars in [bars1, bars2]:
            for bar in bars:
                height = bar.get_height()
                ax1.text(bar.get_x() + bar.get_width()/2., height + max(avg_input + avg_output) * 0.01,
                        f'{int(height)}', ha='center', va='bottom', fontsize=9, fontweight='bold')
        
        # Total tokens comparison
        total_tokens = [i + o for i, o in zip(avg_input, avg_output)]
        bars = ax2.bar(models, total_tokens, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)
        
        for bar, total in zip(bars, total_tokens):
            height = bar.get_height()
            ax2.text(bar.get_x() + bar.get_width()/2., height + max(total_tokens) * 0.02,
                    f'{int(total)}', ha='center', va='bottom', fontsize=11, fontweight='bold')
        
        ax2.set_ylabel('Average Total Tokens', fontsize=12, fontweight='bold')
        ax2.set_title('Average Total Token Usage per API Call', fontsize=14, fontweight='bold')
        ax2.set_ylim(0, max(total_tokens) * 1.15)
        ax2.grid(axis='y', alpha=0.3)
        plt.setp(ax2.xaxis.get_majorticklabels(), rotation=15, ha='right')
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'chart_token_usage_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def plot_error_comparison(model_data, output_dir):
    """Error statistics comparison"""
    fig, ax = plt.subplots(figsize=(14, 8))
    
    models = []
    major_errors = []
    moderate_errors = []
    minor_errors = []
    colors = []
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in model_data:
            df = model_data[model_name]
            models.append(model_name)
            major_errors.append(df['重大错误'].notna().sum() if '重大错误' in df.columns else 0)
            moderate_errors.append(df['明显错误'].notna().sum() if '明显错误' in df.columns else 0)
            minor_errors.append(df['微小错误'].notna().sum() if '微小错误' in df.columns else 0)
            colors.append(MODEL_COLORS.get(model_name, '#808080'))
    
    x = np.arange(len(models))
    width = 0.25
    
    bars1 = ax.bar(x - width, major_errors, width, label='Major Errors', color='#C73E1D', alpha=0.8)
    bars2 = ax.bar(x, moderate_errors, width, label='Moderate Errors', color='#F18F01', alpha=0.8)
    bars3 = ax.bar(x + width, minor_errors, width, label='Minor Errors', color='#6A994E', alpha=0.8)
    
    # Add value labels
    for bars in [bars1, bars2, bars3]:
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.text(bar.get_x() + bar.get_width()/2., height + max(major_errors + moderate_errors + minor_errors) * 0.01,
                       f'{int(height)}', ha='center', va='bottom', fontsize=9, fontweight='bold')
    
    ax.set_ylabel('Number of Errors', fontsize=12, fontweight='bold')
    ax.set_title('Error Statistics Comparison (20 Cases)', fontsize=14, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels(models, rotation=15, ha='right')
    ax.legend(fontsize=11)
    ax.grid(axis='y', alpha=0.3)
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'chart_errors_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def plot_score_distribution(model_data, output_dir):
    """Score distribution box plot"""
    fig, ax = plt.subplots(figsize=(14, 8))
    
    data = []
    labels = []
    colors_list = []
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in model_data:
            df = model_data[model_name]
            if '总分' in df.columns:
                data.append(df['总分'].dropna())
                labels.append(model_name)
                colors_list.append(MODEL_COLORS.get(model_name, '#808080'))
    
    if data:
        bp = ax.boxplot(data, tick_labels=labels, patch_artist=True, showmeans=True, meanline=True)
        
        for patch, color in zip(bp['boxes'], colors_list):
            patch.set_facecolor(color)
            patch.set_alpha(0.7)
        
        ax.set_ylabel('Total Score (out of 20)', fontsize=12, fontweight='bold')
        ax.set_title('Score Distribution Comparison (Box Plot)', fontsize=14, fontweight='bold')
        ax.grid(axis='y', alpha=0.3)
        plt.xticks(rotation=15, ha='right')
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'chart_distribution_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def plot_heatmap_dimensions(model_data, output_dir):
    """Heatmap: Models × Evaluation Dimensions"""
    # Dimension names (Chinese column names)
    dimension_names_cn = [
        '规范依据相关性',
        '涵摄链条对齐度',
        '价值衡量与同理心对齐度',
        '关键事实与争点覆盖度',
        '裁判结论与救济配置一致性'
    ]
    # English display labels
    dimensions_en = [
        'Normative Basis\nRelevance',
        'Subsumption Chain\nAlignment',
        'Value & Empathy\nAlignment',
        'Key Facts &\nDispute Coverage',
        'Judgment &\nRelief Consistency'
    ]
    dimension_cols = [f'{dim}_得分' for dim in dimension_names_cn]
    
    # Prepare data matrix: models × dimensions
    models = []
    heatmap_data = []
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in model_data:
            df = model_data[model_name]
            row_data = []
            for col in dimension_cols:
                if col in df.columns:
                    avg_score = df[col].mean()
                    row_data.append(avg_score)
                else:
                    row_data.append(0)
            
            if any(row_data):  # Only add if there's data
                models.append(model_name)
                heatmap_data.append(row_data)
    
    if not heatmap_data:
        return None
    
    # Convert to numpy array and transpose (dimensions × models)
    heatmap_array = np.array(heatmap_data).T
    
    # Create heatmap
    fig, ax = plt.subplots(figsize=(14, 8))
    
    # Use seaborn if available, otherwise use matplotlib
    try:
        import seaborn as sns
        sns.heatmap(heatmap_array, 
                   xticklabels=models,
                   yticklabels=dimensions_en,
                   annot=True, 
                   fmt='.2f',
                   cmap='YlOrRd',
                   cbar_kws={'label': 'Average Score'},
                   linewidths=0.5,
                   linecolor='gray',
                   annot_kws={'size': 10, 'weight': 'bold'})
    except ImportError:
        # Fallback to matplotlib
        im = ax.imshow(heatmap_array, cmap='YlOrRd', aspect='auto', interpolation='nearest')
        ax.set_xticks(range(len(models)))
        ax.set_yticks(range(len(dimensions_en)))
        ax.set_xticklabels(models, rotation=15, ha='right', fontsize=10)
        ax.set_yticklabels(dimensions_en, fontsize=10)
        
        # Add value annotations
        for i in range(len(dimensions_en)):
            for j in range(len(models)):
                val = heatmap_array[i, j]
                text_color = 'white' if val > 2.5 else 'black'
                ax.text(j, i, f'{val:.2f}',
                       ha="center", va="center", color=text_color, fontsize=10, fontweight='bold',
                       bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.7, edgecolor='none'))
        
        # Add colorbar
        cbar = plt.colorbar(im, ax=ax)
        cbar.set_label('Average Score', fontsize=11, fontweight='bold')
    
    ax.set_xlabel('Models', fontsize=12, fontweight='bold')
    ax.set_ylabel('Evaluation Dimensions', fontsize=12, fontweight='bold')
    ax.set_title('Model Performance Heatmap: Average Scores by Dimension (20 Cases)', 
                fontsize=14, fontweight='bold', pad=20)
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'chart_heatmap_dimensions_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def plot_heatmap_metrics(model_data, output_dir):
    """Heatmap: Models × Performance Metrics"""
    # Prepare metrics data
    models = []
    metrics_data = []
    metric_names = [
        'Avg Score',
        'Percentage',
        'Max Score',
        'Min Score',
        'Major Errors',
        'Moderate Errors',
        'Minor Errors',
        'Abandoned Laws'
    ]
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in model_data:
            df = model_data[model_name]
            row_data = []
            
            # Average score (normalized to 0-4 scale for better visualization)
            avg_score = df['总分'].mean() if '总分' in df.columns else 0
            row_data.append(avg_score / 5.0)  # Normalize to 0-4
            
            # Percentage (normalized to 0-4 scale)
            avg_percent = df['百分制'].mean() if '百分制' in df.columns else 0
            row_data.append(avg_percent / 25.0)  # Normalize to 0-4
            
            # Max score (normalized)
            max_score = df['总分'].max() if '总分' in df.columns else 0
            row_data.append(max_score / 5.0)
            
            # Min score (normalized)
            min_score = df['总分'].min() if '总分' in df.columns else 0
            row_data.append(min_score / 5.0)
            
            # Error counts (normalized by total questions)
            total_questions = len(df)
            major_errors = df['重大错误'].notna().sum() if '重大错误' in df.columns else 0
            moderate_errors = df['明显错误'].notna().sum() if '明显错误' in df.columns else 0
            minor_errors = df['微小错误'].notna().sum() if '微小错误' in df.columns else 0
            
            row_data.append(major_errors / total_questions * 4 if total_questions > 0 else 0)
            row_data.append(moderate_errors / total_questions * 4 if total_questions > 0 else 0)
            row_data.append(minor_errors / total_questions * 4 if total_questions > 0 else 0)
            
            # Abandoned laws count
            abandoned_count = 0
            for idx, row in df.iterrows():
                detail = str(row.get('详细评价', ''))
                major_error = str(row.get('重大错误', ''))
                moderate_error = str(row.get('明显错误', ''))
                minor_error = str(row.get('微小错误', ''))
                
                if (analyze_abandoned_laws(detail) or 
                    analyze_abandoned_laws(major_error) or 
                    analyze_abandoned_laws(moderate_error) or
                    analyze_abandoned_laws(minor_error)):
                    abandoned_count += 1
            
            row_data.append(abandoned_count / total_questions * 4 if total_questions > 0 else 0)
            
            models.append(model_name)
            metrics_data.append(row_data)
    
    if not metrics_data:
        return None
    
    # Convert to numpy array and transpose (metrics × models)
    heatmap_array = np.array(metrics_data).T
    
    # Create heatmap
    fig, ax = plt.subplots(figsize=(14, 8))
    
    try:
        import seaborn as sns
        sns.heatmap(heatmap_array, 
                   xticklabels=models,
                   yticklabels=metric_names,
                   annot=True, 
                   fmt='.2f',
                   cmap='RdYlGn_r',  # Reversed: green is better for scores, red for errors
                   cbar_kws={'label': 'Normalized Score (0-4)'},
                   linewidths=0.5,
                   linecolor='gray',
                   annot_kws={'size': 9, 'weight': 'bold'})
    except ImportError:
        # Fallback to matplotlib
        im = ax.imshow(heatmap_array, cmap='RdYlGn_r', aspect='auto', interpolation='nearest')
        ax.set_xticks(range(len(models)))
        ax.set_yticks(range(len(metric_names)))
        ax.set_xticklabels(models, rotation=15, ha='right', fontsize=10)
        ax.set_yticklabels(metric_names, fontsize=10)
        
        # Add value annotations
        for i in range(len(metric_names)):
            for j in range(len(models)):
                val = heatmap_array[i, j]
                text_color = 'white' if val < 2.0 else 'black'
                ax.text(j, i, f'{val:.2f}',
                       ha="center", va="center", color=text_color, fontsize=9, fontweight='bold',
                       bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.7, edgecolor='none'))
        
        cbar = plt.colorbar(im, ax=ax)
        cbar.set_label('Normalized Score (0-4)', fontsize=11, fontweight='bold')
    
    ax.set_xlabel('Models', fontsize=12, fontweight='bold')
    ax.set_ylabel('Performance Metrics', fontsize=12, fontweight='bold')
    ax.set_title('Model Performance Heatmap: Comprehensive Metrics Comparison (20 Cases)', 
                fontsize=14, fontweight='bold', pad=20)
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'chart_heatmap_metrics_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def plot_ranking_comparison(model_data, output_dir):
    """Ranking comparison - average score vs abandoned laws"""
    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 6))
    
    # Average score ranking
    models_score = []
    scores = []
    colors_score = []
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in model_data:
            df = model_data[model_name]
            avg_score = df['总分'].mean() if '总分' in df.columns else 0
            models_score.append(model_name)
            scores.append(avg_score)
            colors_score.append(MODEL_COLORS.get(model_name, '#808080'))
    
    # Sort by score (descending)
    sorted_score = sorted(zip(models_score, scores, colors_score), key=lambda x: x[1], reverse=True)
    models_score, scores, colors_score = zip(*sorted_score) if sorted_score else ([], [], [])
    
    bars1 = ax1.barh(models_score, scores, color=colors_score, alpha=0.8, edgecolor='black', linewidth=1.5)
    for bar, score in zip(bars1, scores):
        width = bar.get_width()
        ax1.text(width + max(scores) * 0.02, bar.get_y() + bar.get_height()/2.,
                f'{score:.2f}', ha='left', va='center', fontsize=11, fontweight='bold')
    
    ax1.set_xlabel('Average Total Score (out of 20)', fontsize=12, fontweight='bold')
    ax1.set_title('Ranking by Average Score (Higher is Better)', fontsize=14, fontweight='bold')
    ax1.set_xlim(0, max(scores) * 1.2 if scores else 20)
    ax1.grid(axis='x', alpha=0.3)
    
    # Abandoned laws ranking
    models_abandoned = []
    counts = []
    colors_abandoned = []
    
    for model_name in ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'DeepSeek-Thinking', 'Gemini', 'Claude', 'Qwen-Max']:
        if model_name in model_data:
            df = model_data[model_name]
            abandoned_count = 0
            
            for idx, row in df.iterrows():
                detail = str(row.get('详细评价', ''))
                major_error = str(row.get('重大错误', ''))
                moderate_error = str(row.get('明显错误', ''))
                minor_error = str(row.get('微小错误', ''))
                
                if (analyze_abandoned_laws(detail) or 
                    analyze_abandoned_laws(major_error) or 
                    analyze_abandoned_laws(moderate_error) or
                    analyze_abandoned_laws(minor_error)):
                    abandoned_count += 1
            
            models_abandoned.append(model_name)
            counts.append(abandoned_count)
            colors_abandoned.append(MODEL_COLORS.get(model_name, '#808080'))
    
    # Sort by count (ascending - lower is better)
    sorted_abandoned = sorted(zip(models_abandoned, counts, colors_abandoned), key=lambda x: x[1])
    models_abandoned, counts, colors_abandoned = zip(*sorted_abandoned) if sorted_abandoned else ([], [], [])
    
    bars2 = ax2.barh(models_abandoned, counts, color=colors_abandoned, alpha=0.8, edgecolor='black', linewidth=1.5)
    for bar, count in zip(bars2, counts):
        width = bar.get_width()
        ax2.text(width + max(counts) * 0.02 if counts else 1, bar.get_y() + bar.get_height()/2.,
                f'{int(count)}', ha='left', va='center', fontsize=11, fontweight='bold')
    
    ax2.set_xlabel('Number of Questions with Abandoned Law References', fontsize=12, fontweight='bold')
    ax2.set_title('Ranking by Abandoned Law References (Lower is Better)', fontsize=14, fontweight='bold')
    ax2.set_xlim(0, max(counts) * 1.2 if counts else 15)
    ax2.grid(axis='x', alpha=0.3)
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'chart_ranking_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def main():
    # Find latest results folder
    results_dirs = [d for d in os.listdir('data') if d.startswith('results_') and os.path.isdir(os.path.join('data', d))]
    if not results_dirs:
        print("Error: No results folder found")
        return
    
    latest_dir = sorted(results_dirs)[-1]
    results_path = os.path.join('data', latest_dir)
    
    print(f"Loading data from: {results_path}")
    model_data = load_model_data(results_path)
    
    if not model_data:
        print("Error: No model data loaded")
        return
    
    print(f"\nGenerating charts for {len(model_data)} models...")
    
    charts = []
    
    try:
        print("1. Generating average score comparison...")
        charts.append(plot_average_score_comparison(model_data, results_path))
    except Exception as e:
        print(f"   Error: {e}")
    
    try:
        print("2. Generating percentage comparison...")
        charts.append(plot_percentage_comparison(model_data, results_path))
    except Exception as e:
        print(f"   Error: {e}")
    
    try:
        print("3. Generating abandoned laws comparison...")
        charts.append(plot_abandoned_laws_comparison(model_data, results_path))
    except Exception as e:
        print(f"   Error: {e}")
    
    try:
        print("4. Generating token usage comparison...")
        charts.append(plot_token_usage_comparison(model_data, results_path))
    except Exception as e:
        print(f"   Error: {e}")
    
    try:
        print("5. Generating error comparison...")
        charts.append(plot_error_comparison(model_data, results_path))
    except Exception as e:
        print(f"   Error: {e}")
    
    try:
        print("6. Generating score distribution...")
        charts.append(plot_score_distribution(model_data, results_path))
    except Exception as e:
        print(f"   Error: {e}")
    
    try:
        print("7. Generating ranking comparison...")
        charts.append(plot_ranking_comparison(model_data, results_path))
    except Exception as e:
        print(f"   Error: {e}")
    
    try:
        print("8. Generating dimension heatmap...")
        charts.append(plot_heatmap_dimensions(model_data, results_path))
    except Exception as e:
        print(f"   Error: {e}")
    
    try:
        print("9. Generating metrics heatmap...")
        charts.append(plot_heatmap_metrics(model_data, results_path))
    except Exception as e:
        print(f"   Error: {e}")
    
    print(f"\n{'='*80}")
    print(f"✓ Generated {len(charts)} chart files")
    print(f"{'='*80}\n")
    print("Generated charts:")
    for i, chart in enumerate(charts, 1):
        print(f"  {i}. {os.path.basename(chart)}")

if __name__ == '__main__':
    main()
