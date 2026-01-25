"""
生成从原始案例文档到最终数据的完整流程图
使用matplotlib绘制专业的流程图
"""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, ConnectionPatch
import numpy as np

# 设置中文字体
plt.rcParams['font.sans-serif'] = ['Arial Unicode MS', 'SimHei', 'STHeiti', 'PingFang SC']
plt.rcParams['axes.unicode_minus'] = False

def create_data_flow_diagram():
    """创建数据流程图"""
    fig, ax = plt.subplots(figsize=(16, 12))
    ax.set_xlim(0, 10)
    ax.set_ylim(-1, 13)  # 整体下移，从0-14改为-1-13
    ax.axis('off')
    
    # 定义颜色方案（专业配色）
    colors = {
        'input': '#E3F2FD',      # 浅蓝色 - 输入
        'process': '#FFF3E0',    # 浅橙色 - 处理
        'api': '#F3E5F5',        # 浅紫色 - API调用
        'output': '#E8F5E9',     # 浅绿色 - 输出
        'storage': '#FCE4EC',    # 浅粉色 - 存储
        'decision': '#FFF9C4'    # 浅黄色 - 决策
    }
    
    # 定义节点位置和大小
    nodes = {}
    
    # 第一层：输入（整体下移2个单位）
    y1 = 11
    nodes['原始文档'] = {'pos': (2, y1), 'size': (1.5, 0.6), 'color': colors['input'], 'text': '原始案例文档\n(.docx文件)'}
    nodes['Excel模板'] = {'pos': (5, y1), 'size': (1.5, 0.6), 'color': colors['input'], 'text': 'Excel模板\n(可选)'}
    
    # 第二层：文档读取
    y2 = 9.5
    nodes['读取文档'] = {'pos': (3.5, y2), 'size': (1.8, 0.6), 'color': colors['process'], 'text': '读取文档\n(DocReader)'}
    
    # 第三层：数据提取
    y3 = 8
    nodes['提取信息'] = {'pos': (3.5, y3), 'size': (1.8, 0.6), 'color': colors['process'], 'text': '提取案例信息\n(案例内容、法官判决、日期)'}
    
    # 第四层：数据存储
    y4 = 6.5
    nodes['存储案例'] = {'pos': (3.5, y4), 'size': (1.8, 0.6), 'color': colors['storage'], 'text': '存储到案例管理器\n(cases.json)'}
    
    # 第五层：数据脱敏
    y5 = 5
    nodes['数据脱敏'] = {'pos': (3.5, y5), 'size': (1.8, 0.6), 'color': colors['api'], 'text': '数据脱敏\n(DeepSeek API)'}
    
    # 第六层：问题生成
    y6 = 3.5
    nodes['生成问题'] = {'pos': (1.5, y6), 'size': (1.8, 0.6), 'color': colors['api'], 'text': '生成5个问题\n(DeepSeek API)'}
    
    # 第七层：AI回答
    y7 = 2
    nodes['生成回答'] = {'pos': (1.5, y7), 'size': (1.8, 0.6), 'color': colors['api'], 'text': '生成AI回答\n(Thinking模式)'}
    
    # 第八层：评估
    y8 = 0.5
    nodes['评估回答'] = {'pos': (1.5, y8), 'size': (1.8, 0.6), 'color': colors['api'], 'text': '评估AI回答\n(5个维度评分)'}
    
    # 第九层：错误处理
    y9 = -0.5
    nodes['错误标记'] = {'pos': (5.5, y6), 'size': (1.8, 0.6), 'color': colors['process'], 'text': '提取错误标记\n(微小/明显/重大)'}
    nodes['应用扣分'] = {'pos': (5.5, y5), 'size': (1.8, 0.6), 'color': colors['process'], 'text': '应用扣分惩罚\n(按错误级别)'}
    
    # 第十层：最终输出
    y10 = -1
    nodes['最终结果'] = {'pos': (3.5, y10), 'size': (1.8, 0.6), 'color': colors['output'], 'text': '导出Excel结果\n(包含所有维度得分)'}
    
    # 绘制节点
    for name, node in nodes.items():
        x, y = node['pos']
        w, h = node['size']
        
        # 绘制圆角矩形
        box = FancyBboxPatch(
            (x - w/2, y - h/2), w, h,
            boxstyle="round,pad=0.05",
            facecolor=node['color'],
            edgecolor='#333333',
            linewidth=1.5,
            zorder=2
        )
        ax.add_patch(box)
        
        # 添加文本
        ax.text(x, y, node['text'], 
                ha='center', va='center',
                fontsize=9, fontweight='bold',
                zorder=3)
    
    # 绘制箭头（确保从上到下，从起点底部指向终点顶部）
    def get_bottom(pos, size):
        """获取节点底部位置"""
        return (pos[0], pos[1] - size[1]/2)
    
    def get_top(pos, size):
        """获取节点顶部位置"""
        return (pos[0], pos[1] + size[1]/2)
    
    arrows = [
        # 输入到读取（从底部指向顶部）
        (get_bottom(nodes['原始文档']['pos'], nodes['原始文档']['size']), 
         get_top(nodes['读取文档']['pos'], nodes['读取文档']['size'])),
        (get_bottom(nodes['Excel模板']['pos'], nodes['Excel模板']['size']), 
         get_top(nodes['读取文档']['pos'], nodes['读取文档']['size'])),
        # 读取到提取
        (get_bottom(nodes['读取文档']['pos'], nodes['读取文档']['size']), 
         get_top(nodes['提取信息']['pos'], nodes['提取信息']['size'])),
        # 提取到存储
        (get_bottom(nodes['提取信息']['pos'], nodes['提取信息']['size']), 
         get_top(nodes['存储案例']['pos'], nodes['存储案例']['size'])),
        # 存储到脱敏
        (get_bottom(nodes['存储案例']['pos'], nodes['存储案例']['size']), 
         get_top(nodes['数据脱敏']['pos'], nodes['数据脱敏']['size'])),
        # 脱敏到生成问题
        (get_bottom(nodes['数据脱敏']['pos'], nodes['数据脱敏']['size']), 
         get_top(nodes['生成问题']['pos'], nodes['生成问题']['size'])),
        # 生成问题到生成回答
        (get_bottom(nodes['生成问题']['pos'], nodes['生成问题']['size']), 
         get_top(nodes['生成回答']['pos'], nodes['生成回答']['size'])),
        # 生成回答到评估
        (get_bottom(nodes['生成回答']['pos'], nodes['生成回答']['size']), 
         get_top(nodes['评估回答']['pos'], nodes['评估回答']['size'])),
        # 评估到错误标记（向右）
        ((nodes['评估回答']['pos'][0] + nodes['评估回答']['size'][0]/2, nodes['评估回答']['pos'][1]), 
         (nodes['错误标记']['pos'][0] - nodes['错误标记']['size'][0]/2, nodes['错误标记']['pos'][1])),
        # 错误标记到应用扣分
        (get_bottom(nodes['错误标记']['pos'], nodes['错误标记']['size']), 
         get_top(nodes['应用扣分']['pos'], nodes['应用扣分']['size'])),
        # 应用扣分到最终结果（向左下）
        ((nodes['应用扣分']['pos'][0] - nodes['应用扣分']['size'][0]/2, 
          nodes['应用扣分']['pos'][1] - nodes['应用扣分']['size'][1]/2), 
         (nodes['最终结果']['pos'][0] + nodes['最终结果']['size'][0]/2, 
          nodes['最终结果']['pos'][1] + nodes['最终结果']['size'][1]/2)),
        # 评估到最终结果（直接路径，向左下）
        ((nodes['评估回答']['pos'][0] - nodes['评估回答']['size'][0]/2, 
          nodes['评估回答']['pos'][1] - nodes['评估回答']['size'][1]/2), 
         (nodes['最终结果']['pos'][0] + nodes['最终结果']['size'][0]/2, 
          nodes['最终结果']['pos'][1] + nodes['最终结果']['size'][1]/2)),
    ]
    
    for start, end in arrows:
        arrow = FancyArrowPatch(
            start, end,
            arrowstyle='->',
            mutation_scale=30,  # 增大箭头大小
            linewidth=3,  # 增加线条粗细
            color='#2C3E50',  # 使用更深的颜色
            zorder=1,
            connectionstyle='arc3,rad=0.1',
            alpha=0.9  # 增加不透明度
        )
        ax.add_patch(arrow)
    
    # 添加标题（下移）
    ax.text(5, 12.5, '法律AI研究平台 - 数据处理完整流程', 
            ha='center', va='center',
            fontsize=16, fontweight='bold',
            bbox=dict(boxstyle='round,pad=0.5', facecolor='white', edgecolor='#333', linewidth=2))
    
    # 添加图例
    legend_elements = [
        mpatches.Patch(facecolor=colors['input'], edgecolor='#333', label='输入数据'),
        mpatches.Patch(facecolor=colors['process'], edgecolor='#333', label='数据处理'),
        mpatches.Patch(facecolor=colors['api'], edgecolor='#333', label='API调用'),
        mpatches.Patch(facecolor=colors['storage'], edgecolor='#333', label='数据存储'),
        mpatches.Patch(facecolor=colors['output'], edgecolor='#333', label='最终输出'),
    ]
    ax.legend(handles=legend_elements, loc='upper right', fontsize=9, framealpha=0.9)
    
    # 添加说明文字
    info_text = """
流程说明：
1. 从原始.docx文档或Excel模板读取案例数据
2. 提取案例内容、法官判决、日期等信息
3. 存储到案例管理器
4. 使用DeepSeek API进行数据脱敏
5. 为每个案例生成5个法律争议问题
6. 使用Thinking模式生成AI回答
7. 评估AI回答（5个维度：规范依据、涵摄链条、价值衡量、关键事实、裁判结论）
8. 提取错误标记（微小/明显/重大）并应用扣分惩罚
9. 导出最终Excel结果（包含所有维度得分和错误标记）
    """
    
    ax.text(8, 7, info_text.strip(),
            ha='left', va='top',
            fontsize=8,
            bbox=dict(boxstyle='round,pad=0.5', facecolor='#F5F5F5', edgecolor='#CCCCCC', linewidth=1))
    
    plt.tight_layout()
    return fig


def main():
    """主函数"""
    print('=' * 80)
    print('生成数据处理流程图')
    print('=' * 80)
    print()
    
    fig = create_data_flow_diagram()
    
    # 保存图片
    from datetime import datetime
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = f'data/数据处理完整流程_{timestamp}.png'
    
    plt.savefig(output_file, dpi=300, bbox_inches='tight', facecolor='white')
    print(f"✓ 流程图已保存到: {output_file}")
    
    plt.close()
    print("✓ 完成！")


if __name__ == '__main__':
    main()

