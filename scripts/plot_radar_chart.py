"""
绘制不同案例在不同维度得分的星形图（雷达图）
"""
import pandas as pd
import matplotlib.pyplot as plt
import numpy as np
import glob
import os
from datetime import datetime
import sys

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'STHeiti', 'PingFang SC']
plt.rcParams['axes.unicode_minus'] = False


def plot_radar_chart(excel_file, output_file=None, group_by_case=True):
    """
    绘制雷达图，展示不同案例在不同维度上的得分
    
    Args:
        excel_file: Excel文件路径
        output_file: 输出图片路径（可选）
        group_by_case: 是否按案例分组（如果为False，则每个问题单独显示）
    """
    # 读取数据
    if not os.path.exists(excel_file):
        print(f"错误：找不到文件 {excel_file}")
        return
    
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
        print(f"错误：缺少必要的列: {missing_cols}")
        return
    
    # 提取维度得分
    dimensions = [
        '规范依据相关性',
        '涵摄链条对齐度',
        '价值衡量与同理心对齐度',
        '关键事实与争点覆盖度',
        '裁判结论与救济配置一致性'
    ]
    
    dimension_cols = [f'{dim}_得分' for dim in dimensions]
    
    # 准备数据
    data = []
    labels = []
    
    if group_by_case and '案例ID' in df.columns:
        # 按案例分组，计算每个案例的平均得分
        print("按案例分组，计算平均得分...")
        case_groups = df.groupby('案例ID')
        
        for case_id, group_df in case_groups:
            # 计算该案例所有问题的平均得分
            avg_scores = []
            for col in dimension_cols:
                scores = group_df[col].fillna(0).astype(float)
                avg_score = scores.mean() if len(scores) > 0 else 0
                avg_scores.append(avg_score)
            
            # 只包含有数据的案例
            if any(s > 0 for s in avg_scores):
                data.append(avg_scores)
                # 获取案例标题
                if '案例标题' in group_df.columns:
                    case_title = group_df['案例标题'].iloc[0] if len(group_df) > 0 else str(case_id)
                else:
                    case_title = str(case_id)
                labels.append(f"{case_title} (平均{len(group_df)}个问题)")
        
        if not data:
            print("警告：按案例分组后没有有效数据，改为按问题显示")
            group_by_case = False
    
    if not group_by_case:
        # 按问题显示（每个问题一行）
        # 获取案例标识列（优先使用案例标题，否则使用案例ID）
        if '案例标题' in df.columns:
            case_labels = df['案例标题'].fillna('未知案例').astype(str)
        elif '案例ID' in df.columns:
            case_labels = df['案例ID'].fillna('未知案例').astype(str)
        else:
            case_labels = [f'案例{i+1}' for i in range(len(df))]
        
        # 如果有问题编号，添加到标签中
        if '问题编号' in df.columns:
            for idx, row in df.iterrows():
                scores = []
                for col in dimension_cols:
                    score = row.get(col, 0)
                    if pd.isna(score):
                        score = 0
                    scores.append(float(score))
                
                # 只包含有数据的行
                if any(s > 0 for s in scores):
                    data.append(scores)
                    case_label = case_labels.iloc[idx] if isinstance(case_labels, pd.Series) else case_labels[idx]
                    q_num = row.get('问题编号', '')
                    if pd.notna(q_num) and q_num != '':
                        labels.append(f"{case_label} - 问题{q_num}")
                    else:
                        labels.append(case_label)
        else:
            for idx, row in df.iterrows():
                scores = []
                for col in dimension_cols:
                    score = row.get(col, 0)
                    if pd.isna(score):
                        score = 0
                    scores.append(float(score))
                
                # 只包含有数据的行
                if any(s > 0 for s in scores):
                    data.append(scores)
                    case_label = case_labels.iloc[idx] if isinstance(case_labels, pd.Series) else case_labels[idx]
                    labels.append(case_label)
    
    if not data:
        print("错误：没有找到有效的数据")
        return
    
    # 设置雷达图参数
    num_dimensions = len(dimensions)
    angles = np.linspace(0, 2 * np.pi, num_dimensions, endpoint=False).tolist()
    angles += angles[:1]  # 闭合图形
    
    # 创建图形
    fig, ax = plt.subplots(figsize=(12, 10), subplot_kw=dict(projection='polar'))
    
    # 设置颜色
    colors = plt.cm.tab10(np.linspace(0, 1, len(data)))
    
    # 绘制每个案例
    for i, (scores, label) in enumerate(zip(data, labels)):
        scores += scores[:1]  # 闭合数据
        
        # 绘制线条
        ax.plot(angles, scores, 'o-', linewidth=2, label=label[:30], color=colors[i], alpha=0.7)
        ax.fill(angles, scores, alpha=0.15, color=colors[i])
    
    # 设置角度标签
    ax.set_xticks(angles[:-1])
    ax.set_xticklabels(dimensions, fontsize=11)
    
    # 设置y轴（分数范围0-4）
    ax.set_ylim(0, 4)
    ax.set_yticks([0, 1, 2, 3, 4])
    ax.set_yticklabels(['0', '1', '2', '3', '4'], fontsize=9)
    ax.grid(True, linestyle='--', alpha=0.5)
    
    # 添加标题
    if group_by_case:
        title = f'案例评估维度得分对比（共{len(data)}个案例，按案例平均）'
    else:
        title = f'案例评估维度得分对比（共{len(data)}条记录）'
    ax.set_title(title, size=14, fontweight='bold', pad=20)
    
    # 添加图例
    ax.legend(loc='upper right', bbox_to_anchor=(1.3, 1.1), fontsize=9)
    
    # 调整布局
    plt.tight_layout()
    
    # 保存图片
    if output_file is None:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_name = os.path.splitext(os.path.basename(excel_file))[0]
        output_file = f'data/{base_name}_雷达图_{timestamp}.png'
    
    plt.savefig(output_file, dpi=300, bbox_inches='tight')
    print(f"✓ 雷达图已保存到: {output_file}")
    
    # 关闭图形以释放内存（不显示，只保存）
    plt.close()


def main():
    """主函数"""
    group_by_case = True
    
    if len(sys.argv) > 1:
        excel_file = sys.argv[1]
        if len(sys.argv) > 2:
            if sys.argv[2].lower() in ['false', '0', 'no', '问题']:
                group_by_case = False
    else:
        # 默认使用最新的评估结果文件
        files = glob.glob('data/*评估*.xlsx')
        if not files:
            print("错误：找不到评估结果文件")
            print("用法: python plot_radar_chart.py <excel_file> [group_by_case]")
            print("  group_by_case: true/案例 (默认) 或 false/问题")
            return
        
        # 按修改时间排序，使用最新的
        files.sort(key=os.path.getmtime, reverse=True)
        excel_file = files[0]
        print(f"使用最新文件: {excel_file}")
    
    plot_radar_chart(excel_file, group_by_case=group_by_case)


if __name__ == '__main__':
    main()

