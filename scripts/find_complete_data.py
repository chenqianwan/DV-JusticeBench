"""
查找包含这10个案例完整数据的文件
"""
import pandas as pd
import os
from datetime import datetime

target_case_ids = [
    'case_20260103_155150_0',
    'case_20260103_155150_1',
    'case_20260103_155150_2',
    'case_20260103_155150_3',
    'case_20260103_155150_4',
    'case_20260103_155150_5',
    'case_20260103_155150_6',
    'case_20260103_155150_7',
    'case_20260103_155150_105',
    'case_20260103_155150_106',
]

print("查找包含这10个案例完整数据的文件...")
print("=" * 80)

found_files = []

for root, dirs, files in os.walk('data'):
    for f in files:
        if f.endswith('.xlsx'):
            filepath = os.path.join(root, f)
            try:
                df = pd.read_excel(filepath)
                if '案例ID' in df.columns:
                    file_case_ids = set(df['案例ID'].unique())
                    target_in_file = [cid for cid in target_case_ids if cid in file_case_ids]
                    
                    if len(target_in_file) > 0:
                        target_rows = df[df['案例ID'].isin(target_case_ids)]
                        
                        # 检查数据完整性
                        dim_cols = ['规范依据相关性_得分', '涵摄链条对齐度_得分', 
                                   '价值衡量与同理心对齐度_得分', '关键事实与争点覆盖度_得分',
                                   '裁判结论与救济配置一致性_得分']
                        
                        dim_non_zero = sum((target_rows[col] != 0).sum() for col in dim_cols if col in df.columns)
                        eval_filled = 0
                        if '详细评价' in df.columns:
                            eval_filled = ((target_rows['详细评价'].astype(str) != '') & 
                                          (target_rows['详细评价'].astype(str) != 'nan') & 
                                          (target_rows['详细评价'].astype(str) != 'None')).sum()
                        
                        if dim_non_zero > 0 or eval_filled > 0:
                            mtime = os.path.getmtime(filepath)
                            found_files.append({
                                'name': f,
                                'path': filepath,
                                'mtime': mtime,
                                'target_count': len(target_in_file),
                                'dim_non_zero': dim_non_zero,
                                'eval_filled': eval_filled,
                                'total_rows': len(target_rows)
                            })
            except Exception as e:
                continue

# 按修改时间排序
found_files.sort(key=lambda x: x['mtime'], reverse=True)

if found_files:
    print(f"\n找到 {len(found_files)} 个包含这10个案例数据的文件:\n")
    for i, file_info in enumerate(found_files[:15], 1):
        date_str = datetime.fromtimestamp(file_info['mtime']).strftime('%Y-%m-%d %H:%M:%S')
        print(f"{i}. {file_info['name']}")
        print(f"   路径: {file_info['path']}")
        print(f"   修改时间: {date_str}")
        print(f"   包含案例数: {file_info['target_count']}/10")
        print(f"   总行数: {file_info['total_rows']}")
        print(f"   各维度得分非0行数: {file_info['dim_non_zero']}")
        print(f"   详细评价非空行数: {file_info['eval_filled']}")
        print()
    
    # 找出数据最完整的文件（包含所有10个案例且数据完整）
    complete_files = [f for f in found_files if f['target_count'] == 10]
    if complete_files:
        best_file = max(complete_files, key=lambda x: x['dim_non_zero'] + x['eval_filled'])
        print(f"\n✓ 数据最完整的文件（包含所有10个案例）:")
        print(f"  文件名: {best_file['name']}")
        print(f"  路径: {best_file['path']}")
        print(f"  各维度得分非0行数: {best_file['dim_non_zero']}")
        print(f"  详细评价非空行数: {best_file['eval_filled']}")
    else:
        print("\n⚠️ 未找到包含所有10个案例的文件")
else:
    print("\n✗ 未找到包含这10个案例数据的文件")

