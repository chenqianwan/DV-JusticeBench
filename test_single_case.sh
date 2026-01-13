#!/bin/bash
# 测试单个案例并实时显示日志

cd /Users/chenlong/WorkSpace/huangyidan

CASE_ID="case_20251230_134952_90"
DS_FILE="data/108个案例_新标准评估_完整版_最终版.xlsx"
LOG_FILE="logs/test_single_case_$(date +%Y%m%d_%H%M%S).log"

mkdir -p logs

echo "=========================================="
echo "测试单个案例: $CASE_ID"
echo "使用DeepSeek的108个案例结果文件中的问题"
echo "日志文件: $LOG_FILE"
echo "=========================================="
echo ""

# 运行脚本并实时显示输出
python3 process_cases.py \
  --model gpt4o \
  --use_ds_questions "$DS_FILE" \
  --case_ids "$CASE_ID" \
  --standalone 2>&1 | tee "$LOG_FILE"

echo ""
echo "=========================================="
echo "测试完成"
echo "日志已保存到: $LOG_FILE"
echo "=========================================="
