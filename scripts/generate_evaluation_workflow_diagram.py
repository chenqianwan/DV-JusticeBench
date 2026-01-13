#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Generate evaluation workflow diagram for 7 models
All labels in English to avoid Chinese display issues
"""
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, ConnectionPatch
import numpy as np

# Set matplotlib to use English fonts
plt.rcParams['font.family'] = 'Arial'
plt.rcParams['axes.unicode_minus'] = False

def create_evaluation_workflow_diagram():
    """Create evaluation workflow diagram for 7 models"""
    fig = plt.figure(figsize=(20, 14))
    ax = fig.add_subplot(111)
    ax.set_xlim(0, 20)
    ax.set_ylim(-1, 15)
    ax.axis('off')
    
    # Professional color scheme
    colors = {
        'input': '#E3F2FD',      # Light blue - Input
        'process': '#FFF3E0',     # Light orange - Process
        'api': '#F3E5F5',         # Light purple - API
        'output': '#E8F5E9',      # Light green - Output
        'storage': '#FCE4EC',     # Light pink - Storage
        'parallel': '#E1F5FE',    # Light cyan - Parallel
        'model': '#FFE0B2'        # Light amber - Model selection
    }
    
    # Define nodes
    nodes = {}
    
    # === Stage 1: Input ===
    y = 14
    nodes['Input'] = {'pos': (10, y), 'size': (3, 0.8), 'color': colors['input'],
                      'text': 'Input: 20 Legal Cases\n(108 Cases Dataset)'}
    
    # === Stage 2: Data Masking ===
    y = 12.5
    nodes['Masking'] = {'pos': (10, y), 'size': (3, 0.8), 'color': colors['api'],
                       'text': 'Step 1/4: Data Masking\nDeepSeek API\n(Anonymize sensitive info)'}
    
    # === Stage 3: Question Generation ===
    y = 11
    nodes['Questions'] = {'pos': (10, y), 'size': (3, 0.8), 'color': colors['api'],
                         'text': 'Step 2/4: Question Generation\nDeepSeek API\n(5 questions per case)'}
    
    # === Stage 4: Model Selection (Parallel) ===
    y = 9
    nodes['ModelSelection'] = {'pos': (10, y), 'size': (4, 0.8), 'color': colors['parallel'],
                              'text': 'Step 3/4: AI Answer Generation\n7 Models in Parallel'}
    
    # === Stage 5: 7 Models (arranged horizontally) ===
    y = 7
    model_names = ['GPT-4o', 'GPT-o3', 'GPT-5', 'DeepSeek', 'Gemini', 'Claude', 'Qwen-Max']
    model_x_positions = np.linspace(2, 18, 7)
    
    for i, (model_name, x_pos) in enumerate(zip(model_names, model_x_positions)):
        nodes[f'Model_{i}'] = {'pos': (x_pos, y), 'size': (2, 0.7), 'color': colors['model'],
                               'text': f'{model_name}\n(100 questions\nper model)'}
    
    # === Stage 6: Evaluation ===
    y = 5.5
    nodes['Evaluation'] = {'pos': (10, y), 'size': (3, 0.8), 'color': colors['api'],
                         'text': 'Step 4/4: Answer Evaluation\nDeepSeek API\n(5-dimension scoring)'}
    
    # === Stage 7: Evaluation Dimensions ===
    y = 4
    dimensions = [
        'Normative\nBasis',
        'Subsumption\nChain',
        'Value &\nEmpathy',
        'Key Facts &\nCoverage',
        'Judgment &\nRelief'
    ]
    dim_x_positions = np.linspace(2, 18, 5)
    
    for i, (dim, x_pos) in enumerate(zip(dimensions, dim_x_positions)):
        nodes[f'Dim_{i}'] = {'pos': (x_pos, y), 'size': (2.5, 0.6), 'color': colors['process'],
                            'text': dim}
    
    # === Stage 8: Error Analysis ===
    y = 2.5
    nodes['ErrorAnalysis'] = {'pos': (6, y), 'size': (2.5, 0.7), 'color': colors['process'],
                              'text': 'Error Extraction\n(Major/Moderate/Minor)'}
    nodes['Penalty'] = {'pos': (10, y), 'size': (2.5, 0.7), 'color': colors['process'],
                       'text': 'Penalty Application\n(50%/30%/10%)'}
    nodes['ScoreCalc'] = {'pos': (14, y), 'size': (2.5, 0.7), 'color': colors['process'],
                         'text': 'Final Score\nCalculation'}
    
    # === Stage 9: Results ===
    y = 1
    nodes['Results'] = {'pos': (10, y), 'size': (3, 0.8), 'color': colors['output'],
                       'text': 'Results Export\nExcel Files\n(7 models × 20 cases)'}
    
    # === Stage 10: Analysis ===
    y = -0.3
    nodes['Analysis'] = {'pos': (10, y), 'size': (4, 0.8), 'color': colors['output'],
                        'text': 'Comprehensive Analysis\nReports & Charts\n(9 visualization charts)'}
    
    # Helper functions for node positions
    def get_bottom(pos, size):
        return (pos[0], pos[1] - size[1]/2)
    
    def get_top(pos, size):
        return (pos[0], pos[1] + size[1]/2)
    
    def get_left(pos, size):
        return (pos[0] - size[0]/2, pos[1])
    
    def get_right(pos, size):
        return (pos[0] + size[0]/2, pos[1])
    
    # Draw nodes
    for node_name, node_info in nodes.items():
        pos = node_info['pos']
        size = node_info['size']
        color = node_info['color']
        text = node_info['text']
        
        # Create rounded rectangle
        box = FancyBboxPatch(
            (pos[0] - size[0]/2, pos[1] - size[1]/2),
            size[0], size[1],
            boxstyle="round,pad=0.1",
            facecolor=color,
            edgecolor='black',
            linewidth=1.5,
            zorder=2
        )
        ax.add_patch(box)
        
        # Add text
        ax.text(pos[0], pos[1], text,
               ha='center', va='center',
               fontsize=9, fontweight='bold',
               zorder=3)
    
    # Draw arrows - Main flow
    arrow_props = dict(arrowstyle='->', lw=2, color='#333333', zorder=1)
    
    # Input to Masking
    ax.annotate('', xy=get_top(nodes['Masking']['pos'], nodes['Masking']['size']),
               xytext=get_bottom(nodes['Input']['pos'], nodes['Input']['size']),
               arrowprops=arrow_props)
    
    # Masking to Questions
    ax.annotate('', xy=get_top(nodes['Questions']['pos'], nodes['Questions']['size']),
               xytext=get_bottom(nodes['Masking']['pos'], nodes['Masking']['size']),
               arrowprops=arrow_props)
    
    # Questions to Model Selection
    ax.annotate('', xy=get_top(nodes['ModelSelection']['pos'], nodes['ModelSelection']['size']),
               xytext=get_bottom(nodes['Questions']['pos'], nodes['Questions']['size']),
               arrowprops=arrow_props)
    
    # Model Selection to 7 Models (fan out)
    for i in range(7):
        model_node = nodes[f'Model_{i}']
        ax.annotate('', xy=get_top(model_node['pos'], model_node['size']),
                   xytext=(model_node['pos'][0], nodes['ModelSelection']['pos'][1] - nodes['ModelSelection']['size'][1]/2),
                   arrowprops=arrow_props)
    
    # 7 Models to Evaluation (fan in)
    for i in range(7):
        model_node = nodes[f'Model_{i}']
        ax.annotate('', xy=(nodes['Evaluation']['pos'][0], nodes['Evaluation']['pos'][1] + nodes['Evaluation']['size'][1]/2),
                   xytext=get_bottom(model_node['pos'], model_node['size']),
                   arrowprops=arrow_props)
    
    # Evaluation to Dimensions
    for i in range(5):
        dim_node = nodes[f'Dim_{i}']
        ax.annotate('', xy=get_top(dim_node['pos'], dim_node['size']),
                   xytext=get_bottom(nodes['Evaluation']['pos'], nodes['Evaluation']['size']),
                   arrowprops=arrow_props)
    
    # Dimensions to Error Analysis (converge)
    for i in range(5):
        dim_node = nodes[f'Dim_{i}']
        mid_y = (dim_node['pos'][1] - dim_node['size'][1]/2 + nodes['ErrorAnalysis']['pos'][1] + nodes['ErrorAnalysis']['size'][1]/2) / 2
        ax.annotate('', xy=(nodes['ErrorAnalysis']['pos'][0], nodes['ErrorAnalysis']['pos'][1] + nodes['ErrorAnalysis']['size'][1]/2),
                   xytext=(dim_node['pos'][0], dim_node['pos'][1] - dim_node['size'][1]/2),
                   arrowprops=arrow_props)
    
    # Error Analysis to Penalty to ScoreCalc
    ax.annotate('', xy=get_left(nodes['Penalty']['pos'], nodes['Penalty']['size']),
               xytext=get_right(nodes['ErrorAnalysis']['pos'], nodes['ErrorAnalysis']['size']),
               arrowprops=arrow_props)
    
    ax.annotate('', xy=get_left(nodes['ScoreCalc']['pos'], nodes['ScoreCalc']['size']),
               xytext=get_right(nodes['Penalty']['pos'], nodes['Penalty']['size']),
               arrowprops=arrow_props)
    
    # ScoreCalc to Results (converge)
    ax.annotate('', xy=get_top(nodes['Results']['pos'], nodes['Results']['size']),
               xytext=(nodes['ScoreCalc']['pos'][0], nodes['ScoreCalc']['pos'][1] - nodes['ScoreCalc']['size'][1]/2),
               arrowprops=arrow_props)
    
    # Results to Analysis
    ax.annotate('', xy=get_top(nodes['Analysis']['pos'], nodes['Analysis']['size']),
               xytext=get_bottom(nodes['Results']['pos'], nodes['Results']['size']),
               arrowprops=arrow_props)
    
    # Add title
    ax.text(10, 14.8, '7-Model Legal Case Evaluation Workflow', 
           ha='center', va='top',
           fontsize=18, fontweight='bold',
           bbox=dict(boxstyle='round,pad=0.5', facecolor='white', edgecolor='black', linewidth=2))
    
    # Add legend
    legend_elements = [
        mpatches.Patch(facecolor=colors['input'], edgecolor='black', label='Input'),
        mpatches.Patch(facecolor=colors['api'], edgecolor='black', label='API Call'),
        mpatches.Patch(facecolor=colors['parallel'], edgecolor='black', label='Parallel Processing'),
        mpatches.Patch(facecolor=colors['model'], edgecolor='black', label='AI Models'),
        mpatches.Patch(facecolor=colors['process'], edgecolor='black', label='Processing'),
        mpatches.Patch(facecolor=colors['output'], edgecolor='black', label='Output')
    ]
    ax.legend(handles=legend_elements, loc='upper left', fontsize=10, framealpha=0.9)
    
    # Add statistics box
    stats_text = (
        'Statistics:\n'
        '• 7 Models: GPT-4o, GPT-o3, GPT-5, DeepSeek, Gemini, Claude, Qwen-Max\n'
        '• 20 Cases (last 20 from 108-case dataset)\n'
        '• 100 Questions per model (5 questions × 20 cases)\n'
        '• 5 Evaluation Dimensions\n'
        '• 9 Visualization Charts\n'
        '• Parallel Processing (ThreadPoolExecutor)'
    )
    ax.text(1, 1, stats_text,
           ha='left', va='bottom',
           fontsize=9,
           bbox=dict(boxstyle='round,pad=0.5', facecolor='#F5F5F5', edgecolor='gray', linewidth=1))
    
    plt.tight_layout()
    return fig

def main():
    """Generate and save the workflow diagram"""
    print("Generating evaluation workflow diagram...")
    
    fig = create_evaluation_workflow_diagram()
    
    # Save to results folder
    from datetime import datetime
    import os
    
    # Find latest results folder
    results_dirs = [d for d in os.listdir('data') if d.startswith('results_') and os.path.isdir(os.path.join('data', d))]
    if results_dirs:
        latest_dir = sorted(results_dirs)[-1]
        results_path = os.path.join('data', latest_dir)
    else:
        results_path = 'data'
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_file = os.path.join(results_path, f'workflow_diagram_{timestamp}.png')
    
    fig.savefig(output_file, dpi=300, bbox_inches='tight', facecolor='white')
    plt.close()
    
    print(f"✓ Workflow diagram saved to: {output_file}")

if __name__ == '__main__':
    main()
