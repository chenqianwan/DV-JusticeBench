"""
补齐案例ID和创建时间
"""
import pandas as pd
import re
from datetime import datetime
import os
import uuid


def generate_case_id(index: int, timestamp: datetime = None) -> str:
    """
    生成案例ID
    
    Args:
        index: 案例索引
        timestamp: 时间戳，如果不提供则使用当前时间
        
    Returns:
        案例ID字符串
    """
    if timestamp is None:
        timestamp = datetime.now()
    return f"case_{timestamp.strftime('%Y%m%d_%H%M%S')}_{index}"


def generate_created_at(case_date: str = None, timestamp: datetime = None) -> str:
    """
    生成创建时间
    
    Args:
        case_date: 案例日期（YYYY-MM-DD格式），如果提供则使用该日期
        timestamp: 时间戳，如果不提供则使用当前时间
        
    Returns:
        ISO格式的时间字符串
    """
    if case_date and pd.notna(case_date) and case_date != '':
        try:
            # 尝试解析案例日期
            case_date_obj = datetime.strptime(str(case_date), '%Y-%m-%d')
            # 使用案例日期的中午时间（12:00:00）
            return case_date_obj.replace(hour=12, minute=0, second=0).isoformat()
        except:
            pass
    
    if timestamp is None:
        timestamp = datetime.now()
    return timestamp.isoformat()


def fill_case_id_and_time(excel_file: str, output_file: str = None):
    """
    补齐案例ID和创建时间
    
    Args:
        excel_file: 输入的Excel文件路径
        output_file: 输出的Excel文件路径，如果不提供则生成新文件
    """
    print('=' * 80)
    print('补齐案例ID和创建时间')
    print('=' * 80)
    print()
    
    # 读取Excel文件
    if not os.path.exists(excel_file):
        print(f"错误：找不到文件 {excel_file}")
        return
    
    print(f"读取文件: {excel_file}")
    df = pd.read_excel(excel_file)
    print(f"总行数: {len(df)}")
    print()
    
    # 检查必要的列
    required_columns = ['案例ID', '创建时间']
    missing_columns = [col for col in required_columns if col not in df.columns]
    if missing_columns:
        print(f"错误：缺少必要的列: {missing_columns}")
        return
    
    # 统计需要补齐的行数
    id_missing = df['案例ID'].isna() | (df['案例ID'] == '')
    time_missing = df['创建时间'].isna() | (df['创建时间'] == '')
    print(f"需要补齐案例ID的行数: {id_missing.sum()}")
    print(f"需要补齐创建时间的行数: {time_missing.sum()}")
    print()
    
    # 补齐案例ID和创建时间
    filled_id_count = 0
    filled_time_count = 0
    
    print("开始补齐...")
    print('-' * 80)
    
    # 使用当前时间作为基准，为每个缺失的行生成唯一的时间戳
    base_time = datetime.now()
    
    for idx, row in df.iterrows():
        updated = False
        
        # 补齐案例ID
        if id_missing.iloc[idx]:
            # 使用索引和当前时间生成唯一ID
            case_id = generate_case_id(idx, base_time)
            df.at[idx, '案例ID'] = case_id
            filled_id_count += 1
            updated = True
        
        # 补齐创建时间
        if time_missing.iloc[idx]:
            # 尝试使用案例日期，如果没有则使用当前时间
            case_date = row.get('案例日期', '')
            created_at = generate_created_at(case_date, base_time)
            df.at[idx, '创建时间'] = created_at
            filled_time_count += 1
            updated = True
        
        if updated:
            case_title = str(row.get('案例标题', ''))[:30]
            case_id = df.at[idx, '案例ID']
            created_at = df.at[idx, '创建时间']
            print(f"✓ [{idx+1}] {case_title}...")
            print(f"   案例ID: {case_id}")
            print(f"   创建时间: {created_at}")
    
    print()
    print('-' * 80)
    print(f"完成！")
    print(f"  成功补齐案例ID: {filled_id_count} 个")
    print(f"  成功补齐创建时间: {filled_time_count} 个")
    print()
    
    # 保存文件
    if output_file is None:
        # 生成新文件名
        base_name = os.path.splitext(excel_file)[0]
        ext = os.path.splitext(excel_file)[1]
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        output_file = f"{base_name}_ID时间补齐_{timestamp}{ext}"
    
    print(f"保存到: {output_file}")
    df.to_excel(output_file, index=False)
    print("✓ 保存完成！")
    print()
    
    # 显示统计信息
    print('=' * 80)
    print('统计信息:')
    print('=' * 80)
    total_rows = len(df)
    has_id = df['案例ID'].notna() & (df['案例ID'] != '')
    has_time = df['创建时间'].notna() & (df['创建时间'] != '')
    print(f"总行数: {total_rows}")
    print(f"有案例ID: {has_id.sum()} 个 ({has_id.sum() / total_rows * 100:.1f}%)")
    print(f"有创建时间: {has_time.sum()} 个 ({has_time.sum() / total_rows * 100:.1f}%)")
    
    # 显示前5行的示例
    print()
    print('前5行示例:')
    print('-' * 80)
    for idx in range(min(5, len(df))):
        row = df.iloc[idx]
        print(f"[{idx+1}] {str(row['案例标题'])[:30]}...")
        print(f"    案例ID: {row['案例ID']}")
        print(f"    创建时间: {row['创建时间']}")
        print()


if __name__ == '__main__':
    excel_file = 'data/1.3号案例内容提取_v3_日期补齐_20260103_154847.xlsx'
    fill_case_id_and_time(excel_file)


