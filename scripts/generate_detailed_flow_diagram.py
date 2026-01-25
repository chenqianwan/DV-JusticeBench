"""
生成详细的数据处理流程图（更专业、更详细）
"""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, ConnectionPatch, Circle
import numpy as np

# 设置中文字体和样式
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'STHeiti', 'PingFang SC']
plt.rcParams['axes.unicode_minus'] = False
plt.style.use('seaborn-v0_8-whitegrid')


def create_detailed_flow_diagram():
    """创建详细的数据流程图"""
    fig = plt.figure(figsize=(20, 14))
    ax = fig.add_subplot(111)
    ax.set_xlim(0, 20)
    ax.set_ylim(-2, 14)  # 整体下移，从0-16改为-2-14
    ax.axis('off')
    
    # 专业配色方案
    colors = {
        'input': '#E3F2FD',      # 浅蓝色
        'process': '#FFF3E0',     # 浅橙色
        'api': '#F3E5F5',        # 浅紫色
        'output': '#E8F5E9',     # 浅绿色
        'storage': '#FCE4EC',    # 浅粉色
        'parallel': '#E1F5FE'    # 浅青色 - 并发处理
    }
    
    # 定义节点
    nodes = {}
    
    # === 第一阶段：数据输入 ===（整体下移2个单位）
    y = 13
    nodes['原始文档'] = {'pos': (2, y), 'size': (2, 0.7), 'color': colors['input'], 
                        'text': '原始案例文档\n(.docx文件)\nstatic/cases/'}
    nodes['Excel模板'] = {'pos': (6, y), 'size': (2, 0.7), 'color': colors['input'],
                         'text': 'Excel模板\n(可选输入)'}
    
    # === 第二阶段：文档处理 ===
    y = 11
    nodes['读取文档'] = {'pos': (4, y), 'size': (2.5, 0.7), 'color': colors['process'],
                        'text': 'DocReader\n读取.docx文件\n提取文本内容'}
    
    # === 第三阶段：信息提取 ===
    y = 9
    nodes['提取案例'] = {'pos': (2, y), 'size': (2.2, 0.7), 'color': colors['process'],
                        'text': '提取案例内容\n(案件事实、争议焦点)'}
    nodes['提取判决'] = {'pos': (6, y), 'size': (2.2, 0.7), 'color': colors['process'],
                        'text': '提取法官判决\n(本院认为+判决如下)'}
    nodes['提取日期'] = {'pos': (10, y), 'size': (2.2, 0.7), 'color': colors['process'],
                        'text': '提取案例日期\n(支持中文数字格式)'}
    
    # === 第四阶段：数据存储 ===
    y = 7
    nodes['存储案例'] = {'pos': (6, y), 'size': (2.5, 0.7), 'color': colors['storage'],
                        'text': 'CaseManager\n存储到cases.json\n生成案例ID和时间戳'}
    
    # === 第五阶段：数据脱敏 ===
    y = 5.5
    nodes['脱敏处理'] = {'pos': (6, y), 'size': (2.5, 0.7), 'color': colors['api'],
                        'text': 'DataMaskerAPI\nDeepSeek API脱敏\n(地名、人名、日期、案件编号)'}
    
    # === 第六阶段：问题生成 ===
    y = 4
    nodes['生成问题'] = {'pos': (6, y), 'size': (2.5, 0.7), 'color': colors['api'],
                        'text': '生成5个法律问题\nDeepSeek API\n(基于案例内容和判决)'}
    
    # === 第七阶段：并发处理（每个问题）===
    y = 2.5
    nodes['并发处理'] = {'pos': (6, y), 'size': (3, 0.8), 'color': colors['parallel'],
                        'text': 'ThreadPoolExecutor\n并发处理所有问题\n(默认50并发)'}
    
    # === 第八阶段：AI回答生成 ===
    y = 1
    nodes['生成回答'] = {'pos': (2, y), 'size': (2.5, 0.7), 'color': colors['api'],
                        'text': '生成AI回答\nDeepSeek-Reasoner\n(Thinking模式)'}
    
    # === 第九阶段：评估评分 ===
    y = 1
    nodes['评估评分'] = {'pos': (10, y), 'size': (2.5, 0.7), 'color': colors['api'],
                        'text': '评估AI回答\n5个维度评分\n(0-4分/维度)'}
    
    # === 第十阶段：错误处理 ===
    y = -0.5
    nodes['提取错误'] = {'pos': (2, y), 'size': (2.2, 0.7), 'color': colors['process'],
                        'text': '提取错误标记\n(微小/明显/重大)\n从AI评价中提取'}
    nodes['应用扣分'] = {'pos': (6, y), 'size': (2.2, 0.7), 'color': colors['process'],
                        'text': '应用扣分惩罚\n微小:10% 明显:30%\n重大:50%'}
    nodes['计算总分'] = {'pos': (10, y), 'size': (2.2, 0.7), 'color': colors['process'],
                        'text': '计算最终分数\n(各维度得分之和)\n转换为百分制'}
    
    # === 最终输出 ===
    y = -1.7
    nodes['导出Excel'] = {'pos': (6, y), 'size': (2.5, 0.7), 'color': colors['output'],
                         'text': '导出Excel结果\n包含所有维度得分\n错误标记、详细评价'}
    
    # 绘制节点
    for name, node in nodes.items():
        x, y = node['pos']
        w, h = node['size']
        
        # 绘制圆角矩形
        box = FancyBboxPatch(
            (x - w/2, y - h/2), w, h,
            boxstyle="round,pad=0.08",
            facecolor=node['color'],
            edgecolor='#2C3E50',
            linewidth=2,
            zorder=2
        )
        ax.add_patch(box)
        
        # 添加文本
        lines = node['text'].split('\n')
        for i, line in enumerate(lines):
            ax.text(x, y - h/2 + 0.25 + (len(lines)-1-i)*0.2, line,
                    ha='center', va='center',
                    fontsize=8.5, fontweight='bold' if i == 0 else 'normal',
                    zorder=3)
    
    # 辅助函数：获取节点边界位置
    def get_bottom(pos, size):
        """获取节点底部位置"""
        return (pos[0], pos[1] - size[1]/2)
    
    def get_top(pos, size):
        """获取节点顶部位置"""
        return (pos[0], pos[1] + size[1]/2)
    
    def get_left(pos, size):
        """获取节点左侧位置"""
        return (pos[0] - size[0]/2, pos[1])
    
    def get_right(pos, size):
        """获取节点右侧位置"""
        return (pos[0] + size[0]/2, pos[1])
    
    # 绘制箭头（主要流程，确保从上到下正确流动）
    main_flow = [
        # 输入到读取（从底部指向顶部）
        (get_bottom(nodes['原始文档']['pos'], nodes['原始文档']['size']), 
         get_top(nodes['读取文档']['pos'], nodes['读取文档']['size'])),
        (get_bottom(nodes['Excel模板']['pos'], nodes['Excel模板']['size']), 
         get_top(nodes['读取文档']['pos'], nodes['读取文档']['size'])),
        # 读取到提取（从底部指向顶部）
        (get_bottom(nodes['读取文档']['pos'], nodes['读取文档']['size']), 
         get_top(nodes['提取案例']['pos'], nodes['提取案例']['size'])),
        (get_bottom(nodes['读取文档']['pos'], nodes['读取文档']['size']), 
         get_top(nodes['提取判决']['pos'], nodes['提取判决']['size'])),
        (get_bottom(nodes['读取文档']['pos'], nodes['读取文档']['size']), 
         get_top(nodes['提取日期']['pos'], nodes['提取日期']['size'])),
        # 提取到存储（从底部指向顶部）
        (get_bottom(nodes['提取案例']['pos'], nodes['提取案例']['size']), 
         get_top(nodes['存储案例']['pos'], nodes['存储案例']['size'])),
        (get_bottom(nodes['提取判决']['pos'], nodes['提取判决']['size']), 
         get_top(nodes['存储案例']['pos'], nodes['存储案例']['size'])),
        (get_bottom(nodes['提取日期']['pos'], nodes['提取日期']['size']), 
         get_top(nodes['存储案例']['pos'], nodes['存储案例']['size'])),
        # 存储到脱敏
        (get_bottom(nodes['存储案例']['pos'], nodes['存储案例']['size']), 
         get_top(nodes['脱敏处理']['pos'], nodes['脱敏处理']['size'])),
        # 脱敏到生成问题
        (get_bottom(nodes['脱敏处理']['pos'], nodes['脱敏处理']['size']), 
         get_top(nodes['生成问题']['pos'], nodes['生成问题']['size'])),
        # 生成问题到并发处理
        (get_bottom(nodes['生成问题']['pos'], nodes['生成问题']['size']), 
         get_top(nodes['并发处理']['pos'], nodes['并发处理']['size'])),
        # 并发处理到生成回答和评估（从底部指向顶部）
        (get_bottom(nodes['并发处理']['pos'], nodes['并发处理']['size']), 
         get_top(nodes['生成回答']['pos'], nodes['生成回答']['size'])),
        (get_bottom(nodes['并发处理']['pos'], nodes['并发处理']['size']), 
         get_top(nodes['评估评分']['pos'], nodes['评估评分']['size'])),
        # 生成回答到评估（从右侧指向左侧）
        (get_right(nodes['生成回答']['pos'], nodes['生成回答']['size']), 
         get_left(nodes['评估评分']['pos'], nodes['评估评分']['size'])),
        # 评估到错误处理（从底部指向顶部）
        (get_bottom(nodes['评估评分']['pos'], nodes['评估评分']['size']), 
         get_top(nodes['提取错误']['pos'], nodes['提取错误']['size'])),
        # 错误处理流程
        (get_bottom(nodes['提取错误']['pos'], nodes['提取错误']['size']), 
         get_top(nodes['应用扣分']['pos'], nodes['应用扣分']['size'])),
        (get_bottom(nodes['应用扣分']['pos'], nodes['应用扣分']['size']), 
         get_top(nodes['计算总分']['pos'], nodes['计算总分']['size'])),
        # 计算总分到导出
        (get_bottom(nodes['计算总分']['pos'], nodes['计算总分']['size']), 
         get_top(nodes['导出Excel']['pos'], nodes['导出Excel']['size'])),
        # 评估评分也可以直接到计算总分（如果没有错误，从右侧指向左侧）
        (get_right(nodes['评估评分']['pos'], nodes['评估评分']['size']), 
         get_left(nodes['计算总分']['pos'], nodes['计算总分']['size'])),
    ]
    
    for start, end in main_flow:
        arrow = FancyArrowPatch(
            start, end,
            arrowstyle='->',
            mutation_scale=35,  # 增大箭头大小
            linewidth=3.5,  # 增加线条粗细
            color='#1A1A1A',  # 使用更深的黑色
            zorder=1,
            connectionstyle='arc3,rad=0.15',
            alpha=0.95  # 增加不透明度
        )
        ax.add_patch(arrow)
    
    # 添加标题（下移）
    title_box = FancyBboxPatch(
        (6, 13.5), 8, 0.6,
        boxstyle="round,pad=0.1",
        facecolor='#2C3E50',
        edgecolor='#2C3E50',
        linewidth=2,
        zorder=4
    )
    ax.add_patch(title_box)
    ax.text(10, 13.8, '法律AI研究平台 - 数据处理完整流程', 
            ha='center', va='center',
            fontsize=18, fontweight='bold', color='white',
            zorder=5)
    
    # 添加阶段标签（下移）
    stages = [
        (2, 13.3, '阶段1: 数据输入'),
        (4, 11.3, '阶段2: 文档读取'),
        (6, 9.3, '阶段3: 信息提取'),
        (6, 7.3, '阶段4: 数据存储'),
        (6, 5.8, '阶段5: 数据脱敏'),
        (6, 4.3, '阶段6: 问题生成'),
        (6, 2.8, '阶段7: 并发处理'),
        (6, 1.3, '阶段8: AI分析'),
        (6, -0.2, '阶段9: 评分与扣分'),
        (6, -1.4, '阶段10: 结果导出'),
    ]
    
    for x, y, text in stages:
        ax.text(x, y, text, ha='center', va='bottom',
                fontsize=7, style='italic', color='#7F8C8D',
                bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='#BDC3C7', linewidth=0.5))
    
    # 添加技术说明
    tech_note = """
技术要点：
• 并发处理：ThreadPoolExecutor (50并发)
• API调用：DeepSeek-Reasoner (Thinking模式)
• 错误分级：微小/明显/重大 (不同权重扣分)
• 评分维度：5个维度 (0-4分/维度，总分20分)
• 数据格式：Excel导出 (包含所有详细信息)
    """
    
    note_box = FancyBboxPatch(
        (13, 8), 6, 6,
        boxstyle="round,pad=0.3",
        facecolor='#ECF0F1',
        edgecolor='#95A5A6',
        linewidth=1.5,
        zorder=2
    )
    ax.add_patch(note_box)
    ax.text(16, 11, '技术要点', ha='center', va='top',
            fontsize=12, fontweight='bold', color='#2C3E50')
    ax.text(16, 8.5, tech_note.strip(), ha='left', va='top',
            fontsize=9, color='#34495E',
            bbox=dict(boxstyle='round,pad=0.2', facecolor='white', edgecolor='none', alpha=0.8))
    
    # 添加图例
    legend_elements = [
        mpatches.Patch(facecolor=colors['input'], edgecolor='#2C3E50', label='输入数据'),
        mpatches.Patch(facecolor=colors['process'], edgecolor='#2C3E50', label='数据处理'),
        mpatches.Patch(facecolor=colors['api'], edgecolor='#2C3E50', label='API调用'),
        mpatches.Patch(facecolor=colors['storage'], edgecolor='#2C3E50', label='数据存储'),
        mpatches.Patch(facecolor=colors['parallel'], edgecolor='#2C3E50', label='并发处理'),
        mpatches.Patch(facecolor=colors['output'], edgecolor='#2C3E50', label='最终输出'),
    ]
    ax.legend(handles=legend_elements, loc='upper left', fontsize=9, 
              framealpha=0.95, edgecolor='#2C3E50', facecolor='white')
    
    plt.tight_layout()
    return fig


def main():
    """主函数"""
    print('=' * 80)
    print('生成详细的数据处理流程图')
    print('=' * 80)
    print()
    
    fig = create_detailed_flow_diagram()
    
    # 保存图片
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'data/数据处理详细流程_{timestamp}.png'
    
    plt.savefig(output_file, dpi=300, bbox_inches='tight', facecolor='white', edgecolor='none')
    print(f"✓ 详细流程图已保存到: {output_file}")
    
    plt.close()
    print("✓ 完成！")


if __name__ == '__main__':
    main()

