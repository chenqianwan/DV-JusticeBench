#!/bin/bash
# 运行4个额外案例，结果汇总到16个案例的文件中

# 4个额外案例（从108个案例中选择，排除已处理的16个）
# 已处理的16个：90-99, 155150_4,5,6,7,105,106
# 选择4个可用的案例
ADDITIONAL_CASE_IDS="case_20251229_221520_0 case_20251229_232906_1 case_20251230_101232_5 case_20251230_101232_6"

# DeepSeek的108个案例结果文件
DS_FILE="data/108个案例_新标准评估_完整版_最终版.xlsx"

# 切换到项目目录
cd /Users/chenlong/WorkSpace/huangyidan

echo "=========================================="
echo "运行4个额外案例，结果汇总到16个案例的文件"
echo "=========================================="
echo "案例列表: $ADDITIONAL_CASE_IDS"
echo "DeepSeek文件: $DS_FILE"
echo "模型: DeepSeek, GPT-4o, Claude, Gemini, Qwen"
echo "=========================================="
echo ""

# 检查DeepSeek文件是否存在
if [ ! -f "$DS_FILE" ]; then
    echo "错误: DeepSeek文件不存在: $DS_FILE"
    exit 1
fi

# 定义日志目录
LOG_DIR="logs/$(date +%Y%m%d_%H%M%S)_4cases"
mkdir -p "$LOG_DIR"

echo "日志目录: $LOG_DIR"
echo ""

# 并行运行各个模型
echo "开始并行运行所有模型..."

# 1. DeepSeek
echo "[1/5] 启动 DeepSeek..."
python3 process_cases.py \
  --model deepseek \
  --use_ds_questions "$DS_FILE" \
  --case_ids $ADDITIONAL_CASE_IDS \
  --standalone \
  > "$LOG_DIR/deepseek.log" 2>&1 &
DEEPSEEK_PID=$!
echo "  DeepSeek PID: $DEEPSEEK_PID"

# 2. GPT-4o
echo "[2/5] 启动 GPT-4o..."
python3 process_cases.py \
  --model gpt4o \
  --use_ds_questions "$DS_FILE" \
  --case_ids $ADDITIONAL_CASE_IDS \
  --standalone \
  > "$LOG_DIR/gpt4o.log" 2>&1 &
GPT4O_PID=$!
echo "  GPT-4o PID: $GPT4O_PID"

# 3. Claude
echo "[3/5] 启动 Claude..."
python3 process_cases.py \
  --model claude \
  --use_ds_questions "$DS_FILE" \
  --case_ids $ADDITIONAL_CASE_IDS \
  --standalone \
  > "$LOG_DIR/claude.log" 2>&1 &
CLAUDE_PID=$!
echo "  Claude PID: $CLAUDE_PID"

# 4. Gemini
echo "[4/5] 启动 Gemini..."
python3 process_cases.py \
  --model gemini \
  --use_ds_questions "$DS_FILE" \
  --case_ids $ADDITIONAL_CASE_IDS \
  --standalone \
  > "$LOG_DIR/gemini.log" 2>&1 &
GEMINI_PID=$!
echo "  Gemini PID: $GEMINI_PID"

# 5. Qwen
echo "[5/5] 启动 Qwen..."
python3 process_cases.py \
  --model qwen \
  --use_ds_questions "$DS_FILE" \
  --case_ids $ADDITIONAL_CASE_IDS \
  --standalone \
  > "$LOG_DIR/qwen.log" 2>&1 &
QWEN_PID=$!
echo "  Qwen PID: $QWEN_PID"

echo ""
echo "=========================================="
echo "所有模型已启动，正在并行运行..."
echo "=========================================="
echo ""
echo "日志文件: $LOG_DIR/"
echo "结果将汇总到16个案例的文件中（使用相同的文件名）"
echo ""
echo "等待所有进程完成..."

# 等待所有进程完成
wait $DEEPSEEK_PID && echo "[1/5] DeepSeek 完成" || echo "[1/5] DeepSeek 失败"
wait $GPT4O_PID && echo "[2/5] GPT-4o 完成" || echo "[2/5] GPT-4o 失败"
wait $CLAUDE_PID && echo "[3/5] Claude 完成" || echo "[3/5] Claude 失败"
wait $GEMINI_PID && echo "[4/5] Gemini 完成" || echo "[4/5] Gemini 失败"
wait $QWEN_PID && echo "[5/5] Qwen 完成" || echo "[5/5] Qwen 失败"

echo ""
echo "=========================================="
echo "所有模型运行完成"
echo "=========================================="
