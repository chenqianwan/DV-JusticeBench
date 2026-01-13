#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证GPT-4o是否包含在图表数据中"""
import sys
sys.path.insert(0, '/Users/chenlong/WorkSpace/huangyidan')

from generate_7models_charts import load_model_data

results_path = 'data/results_20260112_unified_e8fd22b9'
model_data = load_model_data(results_path)

print("=" * 80)
print("验证GPT-4o是否在图表数据中")
print("=" * 80)
print()
print(f"加载的模型: {list(model_data.keys())}")
print()
print(f"GPT-4o在数据中: {'GPT-4o' in model_data}")
if 'GPT-4o' in model_data:
    df = model_data['GPT-4o']
    print(f"GPT-4o数据行数: {len(df)}")
    print(f"GPT-4o平均分: {df['总分'].mean():.2f}/20" if '总分' in df.columns else "无总分列")
else:
    print("⚠️ GPT-4o未加载！")
print()
print("所有模型的平均分:")
for model_name in sorted(model_data.keys()):
    df = model_data[model_name]
    avg_score = df['总分'].mean() if '总分' in df.columns else 0
    print(f"  {model_name:20s}: {avg_score:.2f}/20")
