#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import pandas as pd

f = 'data/results_20260112_unified_e8fd22b9/20个案例_统一评估结果_108cases.xlsx'
x = pd.ExcelFile(f)
print('所有sheet:', x.sheet_names)
print()

for s in x.sheet_names:
    df = pd.read_excel(f, sheet_name=s)
    case_count = df['案例ID'].nunique() if '案例ID' in df.columns else 'N/A'
    print(f'{s}: {len(df)}行, {case_count}个案例')
