#!/bin/bash

# 删除旧数据文件的脚本
# 此脚本用于清理旧版本数据文件

cd /Users/chenlong/WorkSpace/huangyidan

echo "开始删除旧数据文件..."

# 删除旧结果文件夹
echo "删除旧结果文件夹..."
rm -rf data/results_20260111_151734
rm -rf data/results_20260112_105927

# 删除旧的图表文件夹
echo "删除旧的图表文件夹..."
rm -rf "data/Case_Evaluation_Charts_English_20260107_144901"
rm -rf "data/案例评估图表_20260107_142949"
rm -rf "data/案例评估图表_优化版_20260107_143610"
rm -rf "data/案例评估图表_带数据标识_20260107_143333"
rm -rf "data/模型对比图表_20260110_084906"
rm -rf "data/所有模型5个案例评估结果_20260110_181657"

# 删除旧的Excel文件
echo "删除旧的Excel文件..."
rm -f "data/5个案例_新标准评估_20260105_120559.xlsx"
rm -f "data/GPT-o3_case_20260103_155150_3_20个问题测试_20260111_233756.xlsx"
rm -f "data/GPT4O_5个案例评估_20260109_232015_清理后.xlsx"
rm -f "data/GPT4O_5个案例评估_20260110_173319.xlsx"
rm -f "data/GPT4O_5个案例评估_20260110_181309.xlsx"
rm -f "data/Dimension_Score_Comparison_20260107_144745.png"
rm -f "data/1个案例_统一评估结果_108cases.xlsx.lock"

echo "删除完成！"
