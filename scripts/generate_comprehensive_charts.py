"""
生成综合专业图表，展示案例评估结果
包括：优化的雷达图、箱线图、柱状图、热力图、散点图等
"""
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import glob
import os
from datetime import datetime
import sys

# 尝试导入seaborn（可选）
try:
    import seaborn as sns
    HAS_SEABORN = True
except ImportError:
    HAS_SEABORN = False

# 设置中文字体和样式
# 优先使用系统可用的中文字体
import matplotlib.font_manager as fm

# 查找可用的中文字体
def find_chinese_font():
    """查找系统可用的中文字体"""
    chinese_fonts = ['PingFang SC', 'STHeiti', 'Heiti TC', 'Arial Unicode MS', 'SimHei', 'Microsoft YaHei']
    available_fonts = [f.name for f in fm.fontManager.ttflist]
    
    # 按优先级查找
    for font in chinese_fonts:
        if font in available_fonts:
            try:
                # 验证字体是否可用
                font_path = fm.findfont(fm.FontProperties(family=font))
                if font_path and 'PingFang' in font_path or 'STHeiti' in font_path or 'Arial Unicode' in font_path:
                    return font
            except:
                continue
    
    # 如果找不到，尝试查找任何包含中文关键字的字体
    for font_name in available_fonts:
        if any(keyword in font_name for keyword in ['PingFang', 'Heiti', 'STHeiti']):
            return font_name
    
    return None

chinese_font = find_chinese_font()
if chinese_font:
    plt.rcParams['font.sans-serif'] = [chinese_font] + ['Arial Unicode MS', 'SimHei', 'STHeiti', 'PingFang SC', 'DejaVu Sans']
    print(f"使用字体: {chinese_font}")
else:
    plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'STHeiti', 'PingFang SC', 'DejaVu Sans']
    print("Warning: Chinese font not found, may display as squares")

plt.rcParams['axes.unicode_minus'] = False

# 清除matplotlib字体缓存（如果需要）
try:
    fm._rebuild()
except:
    pass

# 设置样式
try:
    plt.style.use('seaborn-v0_8-whitegrid')
except:
    try:
        plt.style.use('seaborn-whitegrid')
    except:
        plt.style.use('default')


def load_data(excel_file):
    """加载数据"""
    if not os.path.exists(excel_file):
        print(f"Error: File not found {excel_file}")
        return None
    
    df = pd.read_excel(excel_file)
    
    # 检查必要的列
    required_cols = [
        '规范依据相关性_得分',
        '涵摄链条对齐度_得分',
        '价值衡量与同理心对齐度_得分',
        '关键事实与争点覆盖度_得分',
        '裁判结论与救济配置一致性_得分'
    ]
    
    missing_cols = [col for col in required_cols if col not in df.columns]
    if missing_cols:
        print(f"Error: Missing required columns: {missing_cols}")
        return None
    
    return df


def plot_optimized_radar_chart(df, output_dir='data'):
    """优化的雷达图：显示总体平均、分档平均、高分/低分案例"""
    # 中文列名（用于从DataFrame读取数据）
    dimension_names_cn = [
        '规范依据相关性',
        '涵摄链条对齐度',
        '价值衡量与同理心对齐度',
        '关键事实与争点覆盖度',
        '裁判结论与救济配置一致性'
    ]
    # 英文显示标签
    dimensions = [
        'Normative Basis\nRelevance',
        'Subsumption Chain\nAlignment',
        'Value Judgment &\nEmpathy Alignment',
        'Key Facts &\nDispute Coverage',
        'Judgment &\nRelief Consistency'
    ]
    dimension_cols = [f'{dim}_得分' for dim in dimension_names_cn]
    
    # 计算总体平均
    overall_avg = [df[col].mean() for col in dimension_cols]
    
    # 按分档计算平均
    if '分档' in df.columns:
        grade_avg = {}
        for grade in df['分档'].unique():
            if pd.notna(grade):
                grade_df = df[df['分档'] == grade]
                grade_avg[grade] = [grade_df[col].mean() for col in dimension_cols]
    
    # 设置雷达图参数
    num_dimensions = len(dimensions)
    angles = np.linspace(0, 2 * np.pi, num_dimensions, endpoint=False).tolist()
    angles += angles[:1]
    
    # 创建图形
    fig, ax = plt.subplots(figsize=(12, 10), subplot_kw=dict(projection='polar'))
    
    # 绘制总体平均
    overall_avg_plot = overall_avg + overall_avg[:1]
    ax.plot(angles, overall_avg_plot, 'o-', linewidth=3, label='Overall Average', color='#2E86AB', alpha=0.9)
    ax.fill(angles, overall_avg_plot, alpha=0.2, color='#2E86AB')
    
    # 在数据点上添加数值标签（带背景框，更清晰）
    for angle, value in zip(angles[:-1], overall_avg):
        ax.text(angle, value + 0.2, f'{value:.2f}', ha='center', va='bottom', 
               fontsize=11, fontweight='bold', color='#2E86AB',
               bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.9, edgecolor='#2E86AB', linewidth=1.5))
    
    # 绘制各分档平均
    colors = ['#A23B72', '#F18F01', '#C73E1D', '#6A994E']
    if '分档' in df.columns and grade_avg:
        for i, (grade, scores) in enumerate(grade_avg.items()):
            if len(scores) == num_dimensions:
                scores_plot = scores + scores[:1]
                ax.plot(angles, scores_plot, 'o-', linewidth=2, label=f'{grade}', 
                       color=colors[i % len(colors)], alpha=0.7, linestyle='--')
                # 在数据点上添加数值标签（带背景框，更清晰）
                for angle, value in zip(angles[:-1], scores):
                    ax.text(angle, value + 0.2, f'{value:.2f}', ha='center', va='bottom', 
                           fontsize=10, fontweight='bold', color=colors[i % len(colors)],
                           bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.9, 
                                   edgecolor=colors[i % len(colors)], linewidth=1.2))
    
    # 设置角度标签
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(dimensions, fontsize=11)
    
    # 设置y轴
    ax.set_ylim(0, 4)
    ax.set_yticks([0, 1, 2, 3, 4])
    ax.set_yticklabels(['0', '1', '2', '3', '4'], fontsize=9)
    ax.grid(True, linestyle='--', alpha=0.5)
    
    ax.set_title('Case Evaluation Dimension Score Comparison\n(Overall & Grade Average)', size=16, fontweight='bold', pad=20)
    ax.legend(loc='upper right', bbox_to_anchor=(1.3, 1.1), fontsize=10)
    
    plt.tight_layout()
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'{output_dir}/Optimized_Radar_Chart_{timestamp}.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Optimized radar chart saved to: {output_file}")
    plt.close()
    
    return output_file


def plot_dimension_comparison(df, output_dir='data'):
    """各维度得分对比柱状图"""
    # 中文列名（用于从DataFrame读取数据）
    dimension_names_cn = [
        '规范依据相关性',
        '涵摄链条对齐度',
        '价值衡量与同理心对齐度',
        '关键事实与争点覆盖度',
        '裁判结论与救济配置一致性'
    ]
    # 英文显示标签
    dimensions = [
        'Normative Basis\nRelevance',
        'Subsumption Chain\nAlignment',
        'Value Judgment &\nEmpathy Alignment',
        'Key Facts &\nDispute Coverage',
        'Judgment &\nRelief Consistency'
    ]
    dimension_cols = [f'{dim}_得分' for dim in dimension_names_cn]
    
    # 计算各维度统计
    dim_stats = {
        'Mean': [df[col].mean() for col in dimension_cols],
        'Median': [df[col].median() for col in dimension_cols],
        'Max': [df[col].max() for col in dimension_cols],
        'Min': [df[col].min() for col in dimension_cols]
    }
    
    fig, ax = plt.subplots(figsize=(14, 8))
    
    x = np.arange(len(dimensions))
    width = 0.2
    
    colors = ['#2E86AB', '#A23B72', '#F18F01', '#6A994E']
    bars_list = []
    for i, (stat_name, values) in enumerate(dim_stats.items()):
        bars = ax.bar(x + i * width, values, width, label=stat_name, color=colors[i % len(colors)], alpha=0.8)
        bars_list.append(bars)
        # 在柱子上方添加数值标签（带背景框，更清晰）
        for j, (bar, val) in enumerate(zip(bars, values)):
            height = bar.get_height()
            ax.text(bar.get_x() + bar.get_width()/2., height + 0.08,
                   f'{val:.2f}', ha='center', va='bottom', fontsize=10, fontweight='bold',
                   bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.9, edgecolor='gray', linewidth=1))
    
    ax.set_xlabel('Evaluation Dimension', fontsize=12, fontweight='bold')
    ax.set_ylabel('Score', fontsize=12, fontweight='bold')
    ax.set_title('Dimension Score Statistics Comparison', size=16, fontweight='bold', pad=20)
    ax.set_xticks(x + width * 1.5)
    ax.set_xticklabels(dimensions, rotation=15, ha='right', fontsize=10)
    ax.legend(fontsize=10)
    ax.grid(True, alpha=0.3, axis='y')
    ax.set_ylim(0, 4.8)
    
    plt.tight_layout()
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'{output_dir}/Dimension_Score_Comparison_{timestamp}.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Dimension score comparison chart saved to: {output_file}")
    plt.close()
    
    return output_file


def plot_score_distribution(df, output_dir='data'):
    """分数分布箱线图"""
    # 中文列名（用于从DataFrame读取数据）
    dimension_names_cn = [
        '规范依据相关性',
        '涵摄链条对齐度',
        '价值衡量与同理心对齐度',
        '关键事实与争点覆盖度',
        '裁判结论与救济配置一致性'
    ]
    # 英文显示标签
    dimensions = [
        'Normative Basis\nRelevance',
        'Subsumption Chain\nAlignment',
        'Value Judgment &\nEmpathy Alignment',
        'Key Facts &\nDispute Coverage',
        'Judgment &\nRelief Consistency'
    ]
    dimension_cols = [f'{dim}_得分' for dim in dimension_names_cn]
    
    # 准备数据
    data_for_box = [df[col].dropna() for col in dimension_cols]
    
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    
    # 左图：各维度得分箱线图
    bp = axes[0].boxplot(data_for_box, labels=dimensions, patch_artist=True, 
                        showmeans=True, meanline=True)
    
    # 设置箱线图颜色
    colors = ['#2E86AB', '#A23B72', '#F18F01', '#6A994E', '#C73E1D']
    for patch, color in zip(bp['boxes'], colors):
        patch.set_facecolor(color)
        patch.set_alpha(0.7)
    
    # 添加统计值文本（均值、中位数、最大值、最小值）
    for i, (data, dim) in enumerate(zip(data_for_box, dimensions)):
        mean_val = data.mean()
        median_val = data.median()
        max_val = data.max()
        min_val = data.min()
        # 在箱线图上方添加统计信息（更大更清晰）
        stats_text = f'Mean:{mean_val:.2f}\nMedian:{median_val:.2f}\nMax:{max_val:.2f}\nMin:{min_val:.2f}'
        axes[0].text(i+1, 4.3, stats_text, ha='center', va='top', fontsize=9, fontweight='bold',
                    bbox=dict(boxstyle='round,pad=0.4', facecolor='white', alpha=0.95, 
                            edgecolor=colors[i], linewidth=2))
    
    axes[0].set_ylabel('Score', fontsize=12, fontweight='bold')
    axes[0].set_title('Dimension Score Distribution (Box Plot)', size=14, fontweight='bold')
    axes[0].grid(True, alpha=0.3, axis='y')
    axes[0].set_ylim(-0.5, 5.0)
    axes[0].tick_params(axis='x', rotation=15)
    
    # 右图：总分分布直方图
    if '总分' in df.columns:
        n, bins, patches = axes[1].hist(df['总分'].dropna(), bins=30, color='#2E86AB', alpha=0.7, edgecolor='black')
        mean_val = df['总分'].mean()
        median_val = df['总分'].median()
        std_val = df['总分'].std()
        max_val = df['总分'].max()
        min_val = df['总分'].min()
        
        axes[1].axvline(mean_val, color='red', linestyle='--', linewidth=2, label=f'Mean: {mean_val:.2f}')
        axes[1].axvline(median_val, color='green', linestyle='--', linewidth=2, label=f'Median: {median_val:.2f}')
        
        # 添加统计信息文本框（更大更清晰）
        stats_text = f'Statistics:\nMean: {mean_val:.2f}\nMedian: {median_val:.2f}\nStd Dev: {std_val:.2f}\nMax: {max_val:.2f}\nMin: {min_val:.2f}'
        axes[1].text(0.98, 0.98, stats_text, transform=axes[1].transAxes,
                    fontsize=11, fontweight='bold', verticalalignment='top', horizontalalignment='right',
                    bbox=dict(boxstyle='round,pad=0.5', facecolor='wheat', alpha=0.95, edgecolor='black', linewidth=2))
        
        axes[1].set_xlabel('Total Score', fontsize=12, fontweight='bold')
        axes[1].set_ylabel('Frequency', fontsize=12, fontweight='bold')
        axes[1].set_title('Total Score Distribution Histogram', size=14, fontweight='bold')
        axes[1].legend(fontsize=10)
        axes[1].grid(True, alpha=0.3, axis='y')
    
    plt.tight_layout()
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'{output_dir}/Score_Distribution_{timestamp}.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Score distribution chart saved to: {output_file}")
    plt.close()
    
    return output_file


def plot_grade_distribution(df, output_dir='data'):
    """分档分布图"""
    if '分档' not in df.columns:
        return None
    
    fig, axes = plt.subplots(1, 2, figsize=(16, 6))
    
    # 左图：分档分布饼图
    grade_counts = df['分档'].value_counts()
    colors_pie = ['#6A994E', '#F18F01', '#A23B72', '#C73E1D', '#2E86AB']
    
    # 自定义autopct函数，同时显示百分比和数量
    def make_autopct(values):
        def my_autopct(pct):
            total = sum(values)
            val = int(round(pct*total/100.0))
            return f'{pct:.1f}%\n({val}个)'
        return my_autopct
    
    axes[0].pie(grade_counts.values, labels=grade_counts.index, 
               autopct=make_autopct(grade_counts.values),
               colors=colors_pie[:len(grade_counts)], startangle=90, 
               textprops={'fontsize': 10, 'fontweight': 'bold'})
    axes[0].set_title('Grade Distribution (Pie Chart)', size=14, fontweight='bold')
    
    # 右图：分档分布柱状图
    grade_counts_sorted = grade_counts.sort_index()
    bars = axes[1].bar(range(len(grade_counts_sorted)), grade_counts_sorted.values,
                       color=colors_pie[:len(grade_counts_sorted)], alpha=0.8, edgecolor='black')
    axes[1].set_xticks(range(len(grade_counts_sorted)))
    axes[1].set_xticklabels(grade_counts_sorted.index, rotation=15, ha='right', fontsize=10)
    axes[1].set_ylabel('Count', fontsize=12, fontweight='bold')
    axes[1].set_title('Grade Distribution (Bar Chart)', size=14, fontweight='bold')
    axes[1].grid(True, alpha=0.3, axis='y')
    
    # 添加数值标签（带背景框，更清晰）
    for bar in bars:
        height = bar.get_height()
        axes[1].text(bar.get_x() + bar.get_width()/2., height + max(grade_counts_sorted.values) * 0.02,
                    f'{int(height)}', ha='center', va='bottom', fontsize=12, fontweight='bold',
                    bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.9, edgecolor='black', linewidth=1.5))
    
    plt.tight_layout()
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'{output_dir}/Grade_Distribution_{timestamp}.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Grade distribution chart saved to: {output_file}")
    plt.close()
    
    return output_file


def plot_heatmap(df, output_dir='data'):
    """热力图：案例×维度得分（显示前30个案例）"""
    # 中文列名（用于从DataFrame读取数据）
    dimension_names_cn = [
        '规范依据相关性',
        '涵摄链条对齐度',
        '价值衡量与同理心对齐度',
        '关键事实与争点覆盖度',
        '裁判结论与救济配置一致性'
    ]
    # 英文显示标签
    dimensions = [
        'Normative Basis\nRelevance',
        'Subsumption Chain\nAlignment',
        'Value Judgment &\nEmpathy Alignment',
        'Key Facts &\nDispute Coverage',
        'Judgment &\nRelief Consistency'
    ]
    dimension_cols = [f'{dim}_得分' for dim in dimension_names_cn]
    
    # 按案例分组计算平均分
    if '案例ID' in df.columns:
        case_avg = df.groupby('案例ID')[dimension_cols].mean()
        
        # 只显示前30个案例（按总分排序）
        if '总分' in df.columns:
            case_total = df.groupby('案例ID')['总分'].mean()
            top_cases = case_total.nlargest(30).index
            case_avg = case_avg.loc[top_cases]
        
        # 获取案例标题
        case_titles = {}
        if '案例标题' in df.columns:
            for case_id in case_avg.index:
                title = df[df['案例ID'] == case_id]['案例标题'].iloc[0] if len(df[df['案例ID'] == case_id]) > 0 else case_id
                case_titles[case_id] = title[:30] + '...' if len(title) > 30 else title
        
        fig, ax = plt.subplots(figsize=(14, max(10, len(case_avg) * 0.3)))
        
        # 创建热力图
        if HAS_SEABORN:
            sns.heatmap(case_avg.T, annot=True, fmt='.2f', cmap='YlOrRd', 
                       cbar_kws={'label': 'Score'}, ax=ax, linewidths=0.5, linecolor='gray')
        else:
            # 使用matplotlib的imshow创建热力图
            im = ax.imshow(case_avg.T.values, cmap='YlOrRd', aspect='auto', interpolation='nearest')
            ax.set_xticks(range(len(case_avg)))
            ax.set_yticks(range(len(dimensions)))
            ax.set_xticklabels([case_titles.get(cid, cid) for cid in case_avg.index] if case_titles else case_avg.index, 
                              rotation=45, ha='right', fontsize=8)
            ax.set_yticklabels(dimensions, fontsize=10)
            
            # 添加数值标注（更大更清晰）
            for i in range(len(dimensions)):
                for j in range(len(case_avg)):
                    val = case_avg.T.values[i, j]
                    # 根据背景颜色选择文字颜色（深色背景用白色，浅色背景用黑色）
                    text_color = 'white' if val > 2.5 else 'black'
                    ax.text(j, i, f'{val:.2f}',
                           ha="center", va="center", color=text_color, fontsize=9, fontweight='bold',
                           bbox=dict(boxstyle='round,pad=0.2', facecolor='white', alpha=0.7, edgecolor='none'))
            
            # 添加颜色条
            cbar = plt.colorbar(im, ax=ax)
            cbar.set_label('Score', fontsize=10)
        
        # 设置标签
        ax.set_yticklabels(dimensions, rotation=0, fontsize=10)
        if case_titles:
            ax.set_xticklabels([case_titles.get(cid, cid) for cid in case_avg.index], 
                              rotation=45, ha='right', fontsize=8)
        else:
            ax.set_xticklabels(case_avg.index, rotation=45, ha='right', fontsize=8)
        
        ax.set_title('Case × Dimension Score Heatmap (Top 30 Cases)', size=14, fontweight='bold', pad=20)
        
        plt.tight_layout()
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f'{output_dir}/Heatmap_{timestamp}.png'
        plt.savefig(output_file, dpi=300, bbox_inches='tight')
        print(f"✓ Heatmap saved to: {output_file}")
        plt.close()
        
        return output_file
    
    return None


def plot_scatter_matrix(df, output_dir='data'):
    """散点图矩阵：各维度之间的关系"""
    # 中文列名（用于从DataFrame读取数据）
    dimension_names_cn = [
        '规范依据相关性',
        '涵摄链条对齐度',
        '价值衡量与同理心对齐度',
        '关键事实与争点覆盖度',
        '裁判结论与救济配置一致性'
    ]
    # 英文显示标签
    dimensions = [
        'Normative Basis\nRelevance',
        'Subsumption Chain\nAlignment',
        'Value Judgment &\nEmpathy Alignment',
        'Key Facts &\nDispute Coverage',
        'Judgment &\nRelief Consistency'
    ]
    dimension_cols = [f'{dim}_得分' for dim in dimension_names_cn]
    
    # 准备数据
    plot_data = df[dimension_cols].dropna()
    
    if len(plot_data) > 1000:
        # 如果数据太多，随机采样
        plot_data = plot_data.sample(n=1000, random_state=42)
    
    # 创建散点图矩阵
    fig = plt.figure(figsize=(16, 16))
    gs = fig.add_gridspec(len(dimensions), len(dimensions), hspace=0.3, wspace=0.3)
    
    for i in range(len(dimensions)):
        for j in range(len(dimensions)):
            ax = fig.add_subplot(gs[i, j])
            
            if i == j:
                # 对角线：直方图
                ax.hist(plot_data[dimension_cols[i]], bins=20, color='#2E86AB', alpha=0.7, edgecolor='black')
                ax.set_ylabel('Frequency', fontsize=9)
            else:
                # 非对角线：散点图
                ax.scatter(plot_data[dimension_cols[j]], plot_data[dimension_cols[i]], 
                          alpha=0.3, s=10, color='#2E86AB')
                # 添加趋势线
                if len(plot_data) > 2:
                    z = np.polyfit(plot_data[dimension_cols[j]], plot_data[dimension_cols[i]], 1)
                    p = np.poly1d(z)
                    x_line = np.linspace(plot_data[dimension_cols[j]].min(), 
                                       plot_data[dimension_cols[j]].max(), 100)
                    ax.plot(x_line, p(x_line), "r--", alpha=0.5, linewidth=1)
            
            # 设置标签
            if i == len(dimensions) - 1:
                ax.set_xlabel(dimensions[j][:10], fontsize=9)
            if j == 0:
                ax.set_ylabel(dimensions[i][:10], fontsize=9)
            
            ax.tick_params(labelsize=7)
            ax.grid(True, alpha=0.3)
    
    fig.suptitle('Dimension Score Relationship Scatter Matrix', size=16, fontweight='bold', y=0.995)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'{output_dir}/Scatter_Matrix_{timestamp}.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Scatter matrix saved to: {output_file}")
    plt.close()
    
    return output_file


def plot_case_performance_ranking(df, output_dir='data'):
    """案例表现排名图（前20名和后20名）"""
    if '案例ID' not in df.columns or '总分' not in df.columns:
        return None
    
    # 按案例计算平均总分
    case_scores = df.groupby('案例ID').agg({
        '总分': 'mean',
        '案例标题': 'first'
    }).sort_values('总分', ascending=False)
    
    fig, axes = plt.subplots(1, 2, figsize=(18, 10))
    
    # 左图：前20名
    top20 = case_scores.head(20)
    bars1 = axes[0].barh(range(len(top20)), top20['总分'].values, 
                        color='#6A994E', alpha=0.8, edgecolor='black')
    axes[0].set_yticks(range(len(top20)))
    axes[0].set_yticklabels([title[:40] + '...' if len(title) > 40 else title 
                            for title in top20['案例标题'].values], fontsize=9)
    axes[0].set_xlabel('Average Total Score', fontsize=12, fontweight='bold')
    axes[0].set_title('Top 20 Best Performing Cases', size=14, fontweight='bold')
    axes[0].grid(True, alpha=0.3, axis='x')
    axes[0].invert_yaxis()
    
    # 添加数值标签
    for i, bar in enumerate(bars1):
        width = bar.get_width()
        axes[0].text(width, bar.get_y() + bar.get_height()/2,
                    f'{width:.2f}', ha='left', va='center', fontsize=8, fontweight='bold')
    
    # 右图：后20名
    bottom20 = case_scores.tail(20)
    bars2 = axes[1].barh(range(len(bottom20)), bottom20['总分'].values,
                        color='#C73E1D', alpha=0.8, edgecolor='black')
    axes[1].set_yticks(range(len(bottom20)))
    axes[1].set_yticklabels([title[:40] + '...' if len(title) > 40 else title 
                            for title in bottom20['案例标题'].values], fontsize=9)
    axes[1].set_xlabel('Average Total Score', fontsize=12, fontweight='bold')
    axes[1].set_title('Bottom 20 Worst Performing Cases', size=14, fontweight='bold')
    axes[1].grid(True, alpha=0.3, axis='x')
    axes[1].invert_yaxis()
    
    # 添加数值标签
    for i, bar in enumerate(bars2):
        width = bar.get_width()
        axes[1].text(width, bar.get_y() + bar.get_height()/2,
                    f'{width:.2f}', ha='left', va='center', fontsize=8, fontweight='bold')
    
    plt.tight_layout()
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'{output_dir}/Case_Ranking_{timestamp}.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Case ranking chart saved to: {output_file}")
    plt.close()
    
    return output_file


def plot_error_analysis(df, output_dir='data'):
    """错误分析图"""
    if '错误标记' not in df.columns:
        return None
    
    has_errors = df['错误标记'].notna() & (df['错误标记'] != '')
    error_df = df[has_errors].copy()
    
    if len(error_df) == 0:
        return None
    
    fig, axes = plt.subplots(2, 2, figsize=(16, 12))
    
    # 左上：错误类型统计
    error_types = {}
    if '微小错误' in error_df.columns:
        minor_count = error_df['微小错误'].notna().sum()
        if minor_count > 0:
            error_types['Minor Error'] = minor_count
    if '明显错误' in error_df.columns:
        major_count = error_df['明显错误'].notna().sum()
        if major_count > 0:
            error_types['Major Error'] = major_count
    if '重大错误' in error_df.columns:
        critical_count = error_df['重大错误'].notna().sum()
        if critical_count > 0:
            error_types['Critical Error'] = critical_count
    
    if error_types:
        axes[0, 0].bar(error_types.keys(), error_types.values(), 
                      color=['#F18F01', '#A23B72', '#C73E1D'], alpha=0.8, edgecolor='black')
        axes[0, 0].set_ylabel('Count', fontsize=12, fontweight='bold')
        axes[0, 0].set_title('Error Type Distribution', size=14, fontweight='bold')
        axes[0, 0].grid(True, alpha=0.3, axis='y')
        for i, (err_type, count) in enumerate(error_types.items()):
            axes[0, 0].text(i, count, str(count), ha='center', va='bottom', 
                           fontsize=11, fontweight='bold')
    
    # 右上：有错误vs无错误的分数对比
    no_error_df = df[~has_errors]
    if len(no_error_df) > 0 and len(error_df) > 0:
        comparison_data = [no_error_df['总分'].dropna(), error_df['总分'].dropna()]
        bp = axes[0, 1].boxplot(comparison_data, labels=['No Errors', 'With Errors'], 
                               patch_artist=True, showmeans=True)
        for patch in bp['boxes']:
            patch.set_facecolor('#2E86AB')
            patch.set_alpha(0.7)
        
        # 添加统计值（更大更清晰）
        for i, data in enumerate(comparison_data):
            mean_val = data.mean()
            median_val = data.median()
            count = len(data)
            axes[0, 1].text(i+1, data.max() + 1.5, 
                          f'Mean:{mean_val:.2f}\nMedian:{median_val:.2f}\nCount:{count}',
                          ha='center', va='bottom', fontsize=10, fontweight='bold',
                          bbox=dict(boxstyle='round,pad=0.4', facecolor='white', alpha=0.95, 
                                  edgecolor='#2E86AB', linewidth=2))
        
        axes[0, 1].set_ylabel('Total Score', fontsize=12, fontweight='bold')
        axes[0, 1].set_title('Score Comparison: With vs Without Errors', size=14, fontweight='bold')
        axes[0, 1].grid(True, alpha=0.3, axis='y')
        axes[0, 1].set_ylim(0, max([d.max() for d in comparison_data]) + 3)
    
    # 左下：错误率统计
    total = len(df)
    error_count = len(error_df)
    no_error_count = total - error_count
    
    axes[1, 0].pie([no_error_count, error_count], 
                   labels=[f'No Errors\n({no_error_count})', f'With Errors\n({error_count})'],
                   autopct='%1.1f%%', colors=['#6A994E', '#C73E1D'], 
                   startangle=90, textprops={'fontsize': 11})
    axes[1, 0].set_title('Error Rate Statistics', size=14, fontweight='bold')
    
    # 右下：各维度错误率
    # 中文列名（用于从DataFrame读取数据）
    dimension_names_cn = [
        '规范依据相关性',
        '涵摄链条对齐度',
        '价值衡量与同理心对齐度',
        '关键事实与争点覆盖度',
        '裁判结论与救济配置一致性'
    ]
    # 英文显示标签
    dimensions = [
        'Normative Basis\nRelevance',
        'Subsumption Chain\nAlignment',
        'Value Judgment &\nEmpathy Alignment',
        'Key Facts &\nDispute Coverage',
        'Judgment &\nRelief Consistency'
    ]
    dimension_cols = [f'{dim}_得分' for dim in dimension_names_cn]
    
    error_rates = []
    for col in dimension_cols:
        error_in_dim = error_df[col].notna().sum()
        total_in_dim = df[col].notna().sum()
        rate = (error_in_dim / total_in_dim * 100) if total_in_dim > 0 else 0
        error_rates.append(rate)
    
    axes[1, 1].bar(range(len(dimensions)), error_rates, 
                  color='#A23B72', alpha=0.8, edgecolor='black')
    axes[1, 1].set_xticks(range(len(dimensions)))
    axes[1, 1].set_xticklabels([d[:8] for d in dimensions], rotation=15, ha='right', fontsize=9)
    axes[1, 1].set_ylabel('Error Rate (%)', fontsize=12, fontweight='bold')
    axes[1, 1].set_title('Error Rate by Dimension', size=14, fontweight='bold')
    axes[1, 1].grid(True, alpha=0.3, axis='y')
    for i, rate in enumerate(error_rates):
        axes[1, 1].text(i, rate + max(error_rates) * 0.05, f'{rate:.1f}%', ha='center', va='bottom', 
                       fontsize=11, fontweight='bold',
                       bbox=dict(boxstyle='round,pad=0.3', facecolor='white', alpha=0.9, edgecolor='black', linewidth=1.5))
    
    plt.tight_layout()
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'{output_dir}/Error_Analysis_{timestamp}.png'
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ Error analysis chart saved to: {output_file}")
    plt.close()
    
    return output_file


def main():
    """主函数"""
    # 查找最新的评估结果文件
    if len(sys.argv) > 1:
        excel_file = sys.argv[1]
    else:
        files = glob.glob('data/*案例*评估*.xlsx')
        if not files:
            print("Error: Evaluation result file not found")
            print("Usage: python generate_comprehensive_charts.py <excel_file>")
            return
        
        files.sort(key=os.path.getmtime, reverse=True)
        excel_file = files[0]
        print(f"Using latest file: {excel_file}")
    
    # 加载数据
    df = load_data(excel_file)
    if df is None:
        return
    
    print('=' * 80)
    print('Starting to generate comprehensive charts...')
    print('=' * 80)
    print()
    
    output_dir = os.path.dirname(excel_file) if os.path.dirname(excel_file) else 'data'
    
    # 生成各种图表
    charts = []
    
    try:
        print("1. Generating optimized radar chart...")
        chart = plot_optimized_radar_chart(df, output_dir)
        if chart:
            charts.append(chart)
    except Exception as e:
        print(f"   ✗ Radar chart generation failed: {str(e)}")
    
    try:
        print("2. Generating dimension score comparison chart...")
        chart = plot_dimension_comparison(df, output_dir)
        if chart:
            charts.append(chart)
    except Exception as e:
        print(f"   ✗ Dimension comparison chart generation failed: {str(e)}")
    
    try:
        print("3. Generating score distribution chart...")
        chart = plot_score_distribution(df, output_dir)
        if chart:
            charts.append(chart)
    except Exception as e:
        print(f"   ✗ Score distribution chart generation failed: {str(e)}")
    
    try:
        print("4. Generating grade distribution chart...")
        chart = plot_grade_distribution(df, output_dir)
        if chart:
            charts.append(chart)
    except Exception as e:
        print(f"   ✗ Grade distribution chart generation failed: {str(e)}")
    
    try:
        print("5. Generating heatmap...")
        chart = plot_heatmap(df, output_dir)
        if chart:
            charts.append(chart)
    except Exception as e:
        print(f"   ✗ Heatmap generation failed: {str(e)}")
    
    try:
        print("6. Generating case ranking chart...")
        chart = plot_case_performance_ranking(df, output_dir)
        if chart:
            charts.append(chart)
    except Exception as e:
        print(f"   ✗ Case ranking chart generation failed: {str(e)}")
    
    try:
        print("7. Generating error analysis chart...")
        chart = plot_error_analysis(df, output_dir)
        if chart:
            charts.append(chart)
    except Exception as e:
        print(f"   ✗ Error analysis chart generation failed: {str(e)}")
    
    try:
        print("8. Generating scatter matrix...")
        chart = plot_scatter_matrix(df, output_dir)
        if chart:
            charts.append(chart)
    except Exception as e:
        print(f"   ✗ Scatter matrix generation failed: {str(e)}")
    
    print()
    print('=' * 80)
    print(f'✓ Chart generation completed! Generated {len(charts)} chart files')
    print('=' * 80)
    print()
    print('Generated chart files:')
    for i, chart in enumerate(charts, 1):
        print(f'  {i}. {os.path.basename(chart)}')


if __name__ == '__main__':
    main()

