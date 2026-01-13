"""
生成Gemini vs GPT-4o vs DeepSeek的对比图表
使用英文标签避免中文显示问题
"""
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import os
from datetime import datetime

# 设置matplotlib使用英文，避免中文显示问题
plt.rcParams['font.family'] = 'Arial'
plt.rcParams['axes.unicode_minus'] = False

# 设置样式
try:
    plt.style.use('seaborn-v0_8-whitegrid')
except:
    try:
        plt.style.use('seaborn-whitegrid')
    except:
        plt.style.use('default')

# 定义维度（英文）
DIMENSIONS = {
    '规范依据相关性_得分': 'Normative Basis\nRelevance',
    '涵摄链条对齐度_得分': 'Subsumption Chain\nAlignment',
    '价值衡量与同理心对齐度_得分': 'Value & Empathy\nAlignment',
    '关键事实与争点覆盖度_得分': 'Key Facts &\nDispute Coverage',
    '裁判结论与救济配置一致性_得分': 'Judgment &\nRelief Consistency'
}

def load_data(file_path):
    """加载数据"""
    if not os.path.exists(file_path):
        print(f"Warning: File not found {file_path}")
        return None
    return pd.read_excel(file_path)

def plot_average_score_comparison(ds_df, gemini_df, gpt4o_df, output_dir='data'):
    """平均总分对比柱状图"""
    fig, ax = plt.subplots(figsize=(10, 6))
    
    models = ['DeepSeek', 'Gemini 2.5 Flash', 'GPT-4o']
    scores = [
        ds_df['总分'].mean() if ds_df is not None else 0,
        gemini_df['总分'].mean() if gemini_df is not None else 0,
        gpt4o_df['总分'].mean() if gpt4o_df is not None else 0
    ]
    
    colors = ['#2E86AB', '#6A994E', '#A23B72']
    bars = ax.bar(models, scores, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)
    
    # 添加数值标签
    for bar, score in zip(bars, scores):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height + 0.3,
               f'{score:.2f}', ha='center', va='bottom', fontsize=14, fontweight='bold')
    
    ax.set_ylabel('Average Total Score', fontsize=12, fontweight='bold')
    ax.set_title('Average Total Score Comparison (5 Cases)', fontsize=14, fontweight='bold')
    ax.set_ylim(0, max(scores) * 1.2)
    ax.grid(axis='y', alpha=0.3)
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'comparison_avg_score_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def plot_dimension_comparison(ds_df, gemini_df, gpt4o_df, output_dir='data'):
    """各维度平均得分对比"""
    fig, ax = plt.subplots(figsize=(14, 8))
    
    x = np.arange(len(DIMENSIONS))
    width = 0.25
    
    # 计算各模型各维度的平均分
    ds_scores = []
    gemini_scores = []
    gpt4o_scores = []
    
    for dim_col in DIMENSIONS.keys():
        ds_scores.append(ds_df[dim_col].mean() if ds_df is not None else 0)
        gemini_scores.append(gemini_df[dim_col].mean() if gemini_df is not None else 0)
        gpt4o_scores.append(gpt4o_df[dim_col].mean() if gpt4o_df is not None else 0)
    
    bars1 = ax.bar(x - width, ds_scores, width, label='DeepSeek', color='#2E86AB', alpha=0.8)
    bars2 = ax.bar(x, gemini_scores, width, label='Gemini 2.5 Flash', color='#6A994E', alpha=0.8)
    bars3 = ax.bar(x + width, gpt4o_scores, width, label='GPT-4o', color='#A23B72', alpha=0.8)
    
    # 添加数值标签
    for bars in [bars1, bars2, bars3]:
        for bar in bars:
            height = bar.get_height()
            if height > 0:
                ax.text(bar.get_x() + bar.get_width()/2., height + 0.05,
                       f'{height:.2f}', ha='center', va='bottom', fontsize=9, fontweight='bold')
    
    ax.set_xlabel('Evaluation Dimension', fontsize=12, fontweight='bold')
    ax.set_ylabel('Average Score', fontsize=12, fontweight='bold')
    ax.set_title('Dimension Score Comparison (5 Cases)', fontsize=14, fontweight='bold')
    ax.set_xticks(x)
    ax.set_xticklabels([DIMENSIONS[dim] for dim in DIMENSIONS.keys()], fontsize=10)
    ax.legend(fontsize=11, loc='upper right')
    ax.set_ylim(0, 4.5)
    ax.grid(axis='y', alpha=0.3)
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'comparison_dimensions_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def plot_score_distribution(ds_df, gemini_df, gpt4o_df, output_dir='data'):
    """总分分布箱线图"""
    fig, ax = plt.subplots(figsize=(10, 6))
    
    data = []
    labels = []
    
    if ds_df is not None:
        data.append(ds_df['总分'].dropna())
        labels.append('DeepSeek')
    if gemini_df is not None:
        data.append(gemini_df['总分'].dropna())
        labels.append('Gemini 2.5 Flash')
    if gpt4o_df is not None:
        data.append(gpt4o_df['总分'].dropna())
        labels.append('GPT-4o')
    
    bp = ax.boxplot(data, tick_labels=labels, patch_artist=True, showmeans=True, meanline=True)
    
    colors = ['#2E86AB', '#6A994E', '#A23B72']
    for patch, color in zip(bp['boxes'], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    
    ax.set_ylabel('Total Score', fontsize=12, fontweight='bold')
    ax.set_title('Total Score Distribution (Box Plot)', fontsize=14, fontweight='bold')
    ax.grid(axis='y', alpha=0.3)
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'comparison_distribution_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def plot_percentage_comparison(ds_df, gemini_df, gpt4o_df, output_dir='data'):
    """百分制得分对比"""
    fig, ax = plt.subplots(figsize=(10, 6))
    
    models = ['DeepSeek', 'Gemini 2.5 Flash', 'GPT-4o']
    scores = [
        ds_df['百分制'].mean() if ds_df is not None else 0,
        gemini_df['百分制'].mean() if gemini_df is not None else 0,
        gpt4o_df['百分制'].mean() if gpt4o_df is not None else 0
    ]
    
    colors = ['#2E86AB', '#6A994E', '#A23B72']
    bars = ax.bar(models, scores, color=colors, alpha=0.8, edgecolor='black', linewidth=1.5)
    
    # 添加数值标签和百分比
    for bar, score in zip(bars, scores):
        height = bar.get_height()
        ax.text(bar.get_x() + bar.get_width()/2., height + 1,
               f'{score:.2f}%', ha='center', va='bottom', fontsize=14, fontweight='bold')
    
    ax.set_ylabel('Average Percentage Score', fontsize=12, fontweight='bold')
    ax.set_title('Average Percentage Score Comparison (5 Cases)', fontsize=14, fontweight='bold')
    ax.set_ylim(0, 100)
    ax.grid(axis='y', alpha=0.3)
    
    plt.tight_layout()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(output_dir, f'comparison_percentage_{timestamp}.png')
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    plt.close()
    
    return output_file

def main():
    # 加载数据
    ds_file = 'data/DEEPSEEK_5个案例评估_20260110_083956.xlsx'
    gemini_file = 'data/GEMINI_5个案例评估_20260110_084759.xlsx'
    gpt4o_file = 'data/GPT4O_5个案例评估_20260110_084652.xlsx'
    
    ds_df = load_data(ds_file)
    gemini_df = load_data(gemini_file)
    gpt4o_df = load_data(gpt4o_file)
    
    if ds_df is None or gemini_df is None or gpt4o_df is None:
        print("Error: Some data files are missing")
        return
    
    print("Generating comparison charts...")
    
    # 生成图表
    files = []
    files.append(plot_average_score_comparison(ds_df, gemini_df, gpt4o_df))
    files.append(plot_dimension_comparison(ds_df, gemini_df, gpt4o_df))
    files.append(plot_score_distribution(ds_df, gemini_df, gpt4o_df))
    files.append(plot_percentage_comparison(ds_df, gemini_df, gpt4o_df))
    
    print("\nGenerated charts:")
    for f in files:
        print(f"  ✓ {f}")

if __name__ == '__main__':
    main()

