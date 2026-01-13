#!/bin/bash
# 重试4个网络问题较多的模型（GPT-4o, Claude, Gemini, Qwen）

CASE_IDS="case_20251229_221520_0 case_20251229_232906_1 case_20251230_101232_5 case_20251230_101232_6"

DS_FILE="data/108个案例_新标准评估_完整版_最终版.xlsx"

cd /Users/chenlong/WorkSpace/huangyidan

LOG_DIR="logs/$(date +%Y%m%d_%H%M%S)_retry_4models"
mkdir -p "$LOG_DIR"

echo "开始重试4个模型（4个案例）..."
echo "日志目录: $LOG_DIR"

# 并行运行4个模型
python3 process_cases.py --model gpt4o --use_ds_questions "$DS_FILE" --case_ids $CASE_IDS --standalone > "$LOG_DIR/gpt4o.log" 2>&1 &
python3 process_cases.py --model claude --use_ds_questions "$DS_FILE" --case_ids $CASE_IDS --standalone > "$LOG_DIR/claude.log" 2>&1 &
python3 process_cases.py --model gemini --use_ds_questions "$DS_FILE" --case_ids $CASE_IDS --standalone > "$LOG_DIR/gemini.log" 2>&1 &
python3 process_cases.py --model qwen --use_ds_questions "$DS_FILE" --case_ids $CASE_IDS --standalone > "$LOG_DIR/qwen.log" 2>&1 &

# 等待所有任务完成
wait

echo "所有模型处理完成！"
echo "日志位置: $LOG_DIR/"
