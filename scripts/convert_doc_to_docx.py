"""
将.doc文件批量转换为.docx文件
使用macOS的textutil工具
"""
import os
import subprocess
import sys
from pathlib import Path


def convert_doc_to_docx(doc_path: str, output_dir: str = None) -> str:
    """
    将单个.doc文件转换为.docx
    
    Args:
        doc_path: .doc文件路径
        output_dir: 输出目录，如果为None则输出到原文件同目录
        
    Returns:
        转换后的.docx文件路径
    """
    if not doc_path.lower().endswith('.doc'):
        raise ValueError("文件必须是.doc格式")
    
    if not os.path.exists(doc_path):
        raise FileNotFoundError(f"文件不存在: {doc_path}")
    
    # 确定输出路径
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)
        base_name = os.path.basename(doc_path)
        docx_path = os.path.join(output_dir, base_name.replace('.doc', '.docx'))
    else:
        docx_path = doc_path.replace('.doc', '.docx')
    
    # 使用textutil转换（macOS自带工具）
    # 注意：textutil不能直接转docx，需要先转rtf，然后手动处理
    # 或者使用LibreOffice（如果已安装）
    
    # 方法1: 尝试使用LibreOffice（如果可用）
    try:
        result = subprocess.run(
            ['soffice', '--headless', '--convert-to', 'docx', '--outdir', os.path.dirname(docx_path) or '.', doc_path],
            capture_output=True,
            timeout=60
        )
        if result.returncode == 0:
            return docx_path
    except FileNotFoundError:
        pass
    
    # 方法2: 使用textutil转换为rtf，然后提示用户手动转换
    # 或者提示用户使用Microsoft Word或在线工具转换
    print(f"⚠️  无法自动转换 {doc_path}")
    print("   建议使用以下方法之一：")
    print("   1. 使用Microsoft Word打开并另存为.docx格式")
    print("   2. 使用在线转换工具（如 https://convertio.co/zh/doc-docx/）")
    print("   3. 安装LibreOffice: brew install --cask libreoffice")
    
    return None


def batch_convert(doc_dir: str, output_dir: str = None):
    """
    批量转换目录下的所有.doc文件
    
    Args:
        doc_dir: 包含.doc文件的目录
        output_dir: 输出目录
    """
    doc_files = [f for f in os.listdir(doc_dir) if f.endswith('.doc')]
    
    if not doc_files:
        print(f"在 {doc_dir} 中未找到.doc文件")
        return
    
    print(f"找到 {len(doc_files)} 个.doc文件")
    
    converted = 0
    failed = 0
    
    for doc_file in doc_files:
        doc_path = os.path.join(doc_dir, doc_file)
        try:
            docx_path = convert_doc_to_docx(doc_path, output_dir)
            if docx_path:
                print(f"✓ 已转换: {doc_file} -> {os.path.basename(docx_path)}")
                converted += 1
            else:
                failed += 1
        except Exception as e:
            print(f"✗ 转换失败 {doc_file}: {str(e)}")
            failed += 1
    
    print(f"\n转换完成: 成功 {converted} 个, 失败 {failed} 个")


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print("用法: python convert_doc_to_docx.py <doc文件或目录> [输出目录]")
        sys.exit(1)
    
    input_path = sys.argv[1]
    output_dir = sys.argv[2] if len(sys.argv) > 2 else None
    
    if os.path.isfile(input_path):
        convert_doc_to_docx(input_path, output_dir)
    elif os.path.isdir(input_path):
        batch_convert(input_path, output_dir)
    else:
        print(f"错误: {input_path} 不是有效的文件或目录")


