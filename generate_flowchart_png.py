#!/usr/bin/env python3
"""
从Markdown文件中提取Mermaid图表并生成PNG图片
"""
import re
import os
import subprocess
from pathlib import Path

def extract_mermaid_blocks(markdown_file):
    """从Markdown文件中提取所有Mermaid代码块"""
    with open(markdown_file, 'r', encoding='utf-8') as f:
        content = f.read()
    
    # 匹配mermaid代码块
    pattern = r'```mermaid\n(.*?)```'
    matches = re.findall(pattern, content, re.DOTALL)
    
    return matches

def get_chart_titles(markdown_file):
    """获取每个图表的标题"""
    with open(markdown_file, 'r', encoding='utf-8') as f:
        lines = f.readlines()
    
    titles = []
    current_title = None
    
    for i, line in enumerate(lines):
        # 查找标题（## 或 ### 开头的行）
        if line.startswith('##') and ('流程图' in line or '数据流' in line):
            current_title = line.strip().replace('#', '').strip()
        elif line.startswith('###') and ('流程' in line or '数据流' in line):
            current_title = line.strip().replace('#', '').strip()
        elif '```mermaid' in line:
            if current_title:
                titles.append(current_title)
                current_title = None  # 重置，避免重复使用
            else:
                titles.append(f'图表{len(titles) + 1}')
    
    return titles

def generate_png(mermaid_code, output_file, title=None):
    """使用mmdc生成PNG图片"""
    # 创建临时mermaid文件
    temp_mmd = output_file.replace('.png', '.mmd')
    with open(temp_mmd, 'w', encoding='utf-8') as f:
        f.write(mermaid_code)
    
    try:
        # 使用mmdc生成PNG
        cmd = ['mmdc', '-i', temp_mmd, '-o', output_file, '-w', '2400', '-H', '1800', '-b', 'white']
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        
        if result.returncode == 0:
            print(f"✓ 生成成功: {output_file}")
            if title:
                print(f"  标题: {title}")
            return True
        else:
            print(f"✗ 生成失败: {output_file}")
            print(f"  错误: {result.stderr}")
            return False
    except subprocess.TimeoutExpired:
        print(f"✗ 生成超时: {output_file}")
        return False
    except Exception as e:
        print(f"✗ 生成错误: {output_file}")
        print(f"  错误: {str(e)}")
        return False
    finally:
        # 清理临时文件
        if os.path.exists(temp_mmd):
            os.remove(temp_mmd)

def main():
    markdown_file = 'web功能流程图.md'
    output_dir = 'flowcharts_png'
    
    # 创建输出目录
    os.makedirs(output_dir, exist_ok=True)
    
    # 提取mermaid代码块
    mermaid_blocks = extract_mermaid_blocks(markdown_file)
    titles = get_chart_titles(markdown_file)
    
    print(f"找到 {len(mermaid_blocks)} 个Mermaid图表\n")
    
    # 为每个图表生成PNG
    for i, (code, title) in enumerate(zip(mermaid_blocks, titles), 1):
        # 清理标题作为文件名
        safe_title = re.sub(r'[^\w\s-]', '', title).strip()
        safe_title = re.sub(r'[-\s]+', '_', safe_title)
        
        output_file = os.path.join(output_dir, f"{i:02d}_{safe_title}.png")
        
        print(f"[{i}/{len(mermaid_blocks)}] 正在生成: {title}")
        generate_png(code, output_file, title)
        print()
    
    print(f"\n所有PNG图片已生成到目录: {output_dir}/")
    print(f"共生成 {len(mermaid_blocks)} 个PNG文件")

if __name__ == '__main__':
    main()

