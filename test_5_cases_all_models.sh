#!/bin/bash
# 测试5个案例 - 所有模型并行运行（除了gpt-5）
# 所有模型使用相同的问题，输出到同一个Excel文件的不同tab
# 使用方法: ./test_5_cases_all_models.sh
# 默认处理前5个案例

cd /Users/chenlong/WorkSpace/huangyidan

# 获取前5个案例ID
CASE_IDS=$(python3 -c "import json; cases = json.load(open('data/cases/cases.json', 'r', encoding='utf-8')); print(' '.join(list(cases.keys())[:5]))")
DS_FILE="data/108个案例_新标准评估_完整版_最终版.xlsx"
LOG_DIR="logs/$(date +%Y%m%d_%H%M%S)"

mkdir -p "$LOG_DIR"

echo "=========================================="
echo "测试5个案例 - 所有模型并行运行（除gpt-5）"
echo "=========================================="
echo "案例ID: $CASE_IDS"
echo "DeepSeek文件: $DS_FILE"
echo "模型: DeepSeek, DeepSeek-NoThinking, GPT-4o, Claude, Gemini, Qwen"
echo "所有模型将使用相同的问题，输出到同一个Excel文件的不同tab"
echo "日志目录: $LOG_DIR"
echo "=========================================="
echo ""

# 检查DeepSeek文件是否存在
if [ ! -f "$DS_FILE" ]; then
    echo "错误: DeepSeek结果文件不存在: $DS_FILE"
    exit 1
fi

# 并行运行各个模型
echo "开始并行运行所有模型..."
echo ""

# 1. DeepSeek (thinking模式)
echo "[1/6] 启动 DeepSeek (thinking模式)..."
python3 process_cases.py \
  --model deepseek \
  --use_ds_questions "$DS_FILE" \
  --case_ids $CASE_IDS \
  --standalone \
  > "$LOG_DIR/deepseek.log" 2>&1 &
DEEPSEEK_PID=$!
echo "  DeepSeek PID: $DEEPSEEK_PID"
sleep 1

# 2. DeepSeek (非thinking模式)
echo "[2/6] 启动 DeepSeek (非thinking模式)..."
python3 process_cases.py \
  --model deepseek \
  --no-thinking \
  --use_ds_questions "$DS_FILE" \
  --case_ids $CASE_IDS \
  --standalone \
  > "$LOG_DIR/deepseek_no_thinking.log" 2>&1 &
DEEPSEEK_NO_THINKING_PID=$!
echo "  DeepSeek-NoThinking PID: $DEEPSEEK_NO_THINKING_PID"
sleep 1

# 3. GPT-4o
echo "[3/6] 启动 GPT-4o..."
python3 process_cases.py \
  --model gpt4o \
  --use_ds_questions "$DS_FILE" \
  --case_ids $CASE_IDS \
  --standalone \
  > "$LOG_DIR/gpt4o.log" 2>&1 &
GPT4O_PID=$!
echo "  GPT-4o PID: $GPT4O_PID"
sleep 1

# 4. Claude
echo "[4/6] 启动 Claude..."
python3 process_cases.py \
  --model claude \
  --use_ds_questions "$DS_FILE" \
  --case_ids $CASE_IDS \
  --standalone \
  > "$LOG_DIR/claude.log" 2>&1 &
CLAUDE_PID=$!
echo "  Claude PID: $CLAUDE_PID"
sleep 1

# 5. Gemini
echo "[5/6] 启动 Gemini..."
python3 process_cases.py \
  --model gemini \
  --use_ds_questions "$DS_FILE" \
  --case_ids $CASE_IDS \
  --standalone \
  > "$LOG_DIR/gemini.log" 2>&1 &
GEMINI_PID=$!
echo "  Gemini PID: $GEMINI_PID"
sleep 1

# 6. Qwen
echo "[6/6] 启动 Qwen..."
python3 process_cases.py \
  --model qwen \
  --use_ds_questions "$DS_FILE" \
  --case_ids $CASE_IDS \
  --standalone \
  > "$LOG_DIR/qwen.log" 2>&1 &
QWEN_PID=$!
echo "  Qwen PID: $QWEN_PID"

echo ""
echo "=========================================="
echo "所有模型已启动，正在并行运行..."
echo "=========================================="
echo ""
echo "进程ID:"
echo "  DeepSeek:           $DEEPSEEK_PID"
echo "  DeepSeek-NoThinking: $DEEPSEEK_NO_THINKING_PID"
echo "  GPT-4o:             $GPT4O_PID"
echo "  Claude:             $CLAUDE_PID"
echo "  Gemini:             $GEMINI_PID"
echo "  Qwen:               $QWEN_PID"
echo ""
echo "日志文件:"
echo "  DeepSeek:           $LOG_DIR/deepseek.log"
echo "  DeepSeek-NoThinking: $LOG_DIR/deepseek_no_thinking.log"
echo "  GPT-4o:             $LOG_DIR/gpt4o.log"
echo "  Claude:             $LOG_DIR/claude.log"
echo "  Gemini:             $LOG_DIR/gemini.log"
echo "  Qwen:               $LOG_DIR/qwen.log"
echo ""
echo "实时查看日志:"
echo "  tail -f $LOG_DIR/deepseek.log"
echo "  tail -f $LOG_DIR/deepseek_no_thinking.log"
echo "  tail -f $LOG_DIR/gpt4o.log"
echo "  tail -f $LOG_DIR/claude.log"
echo "  tail -f $LOG_DIR/gemini.log"
echo "  tail -f $LOG_DIR/qwen.log"
echo ""
echo "或者同时查看所有日志:"
echo "  tail -f $LOG_DIR/*.log"
echo ""
echo "等待所有进程完成..."
echo ""

# 等待所有进程完成
wait $DEEPSEEK_PID
DEEPSEEK_EXIT=$?
echo "[1/6] DeepSeek 完成 (退出码: $DEEPSEEK_EXIT)"

wait $DEEPSEEK_NO_THINKING_PID
DEEPSEEK_NO_THINKING_EXIT=$?
echo "[2/6] DeepSeek-NoThinking 完成 (退出码: $DEEPSEEK_NO_THINKING_EXIT)"

wait $GPT4O_PID
GPT4O_EXIT=$?
echo "[3/6] GPT-4o 完成 (退出码: $GPT4O_EXIT)"

wait $CLAUDE_PID
CLAUDE_EXIT=$?
echo "[4/6] Claude 完成 (退出码: $CLAUDE_EXIT)"

wait $GEMINI_PID
GEMINI_EXIT=$?
echo "[5/6] Gemini 完成 (退出码: $GEMINI_EXIT)"

wait $QWEN_PID
QWEN_EXIT=$?
echo "[6/6] Qwen 完成 (退出码: $QWEN_EXIT)"

echo ""
echo "=========================================="
echo "所有模型运行完成"
echo "=========================================="
echo "退出码:"
echo "  DeepSeek:           $DEEPSEEK_EXIT"
echo "  DeepSeek-NoThinking: $DEEPSEEK_NO_THINKING_EXIT"
echo "  GPT-4o:             $GPT4O_EXIT"
echo "  Claude:             $CLAUDE_EXIT"
echo "  Gemini:             $GEMINI_EXIT"
echo "  Qwen:               $QWEN_EXIT"
echo ""

# 检查是否有错误
if [ $DEEPSEEK_EXIT -ne 0 ] || [ $DEEPSEEK_NO_THINKING_EXIT -ne 0 ] || [ $GPT4O_EXIT -ne 0 ] || [ $CLAUDE_EXIT -ne 0 ] || [ $GEMINI_EXIT -ne 0 ] || [ $QWEN_EXIT -ne 0 ]; then
    echo "⚠️ 警告: 部分模型运行失败，请检查日志文件"
    exit 1
else
    echo "✓ 所有模型运行成功！"
    echo ""
    echo "结果文件应该保存在: data/results_*/5个案例_统一评估结果_*.xlsx"
    echo "每个模型的结果在不同的tab中："
    echo "  - DeepSeek"
    echo "  - DeepSeek-NoThinking"
    echo "  - GPT-4o"
    echo "  - Claude Opus 4"
    echo "  - Gemini 2.5 Flash"
    echo "  - Qwen-Max"
    echo ""
    
    # 查找最新的结果文件
    LATEST_RESULT=$(find data/results_* -name "*统一评估结果*.xlsx" -type f -printf '%T@ %p\n' 2>/dev/null | sort -n | tail -1 | cut -d' ' -f2-)
    if [ -n "$LATEST_RESULT" ]; then
        echo "最新结果文件: $LATEST_RESULT"
    fi
    
    exit 0
fi
