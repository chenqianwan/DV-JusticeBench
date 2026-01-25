#!/bin/bash
# 并行运行多个模型（除了gpt-5），使用DeepSeek的108个案例结果文件中的问题
# 所有模型的结果会保存到同一个Excel文件的不同tab中

# 20个案例列表（去掉4个敏感案例后的16个 + 4个新案例）
# 敏感案例（已排除）：case_20260103_155150_0, 1, 2, 3
# 16个没有被拒绝的案例：
CASE_IDS="case_20251230_134952_90 case_20251230_134952_91 case_20251230_134952_92 case_20251230_134952_93 case_20251230_134952_94 case_20251230_134952_95 case_20251230_134952_96 case_20251230_134952_97 case_20251230_134952_98 case_20251230_134952_99 case_20260103_155150_4 case_20260103_155150_5 case_20260103_155150_6 case_20260103_155150_7 case_20260103_155150_105 case_20260103_155150_106"

# DeepSeek的108个案例结果文件
DS_FILE="data/108个案例_新标准评估_完整版_最终版.xlsx"

# 切换到项目目录
cd /Users/chenlong/WorkSpace/huangyidan

echo "=========================================="
echo "并行运行多个模型（使用DeepSeek的问题）"
echo "=========================================="
echo "案例列表: $CASE_IDS"
echo "DeepSeek文件: $DS_FILE"
echo "模型: DeepSeek, GPT-4o, Claude, Gemini, Qwen (排除GPT-5)"
echo "=========================================="
echo ""

# 检查DeepSeek文件是否存在
if [ ! -f "$DS_FILE" ]; then
    echo "错误: DeepSeek文件不存在: $DS_FILE"
    exit 1
fi

# 定义日志目录
LOG_DIR="logs/$(date +%Y%m%d_%H%M%S)"
mkdir -p "$LOG_DIR"

echo "日志目录: $LOG_DIR"
echo ""

# 并行运行各个模型
echo "开始并行运行..."

# 1. DeepSeek
echo "[1/5] 启动 DeepSeek..."
python3 process_cases.py \
  --model deepseek \
  --use_ds_questions "$DS_FILE" \
  --case_ids $CASE_IDS \
  --standalone \
  > "$LOG_DIR/deepseek.log" 2>&1 &
DEEPSEEK_PID=$!
echo "  DeepSeek PID: $DEEPSEEK_PID"

# 2. GPT-4o
echo "[2/5] 启动 GPT-4o..."
python3 process_cases.py \
  --model gpt4o \
  --use_ds_questions "$DS_FILE" \
  --case_ids $CASE_IDS \
  --standalone \
  > "$LOG_DIR/gpt4o.log" 2>&1 &
GPT4O_PID=$!
echo "  GPT-4o PID: $GPT4O_PID"

# 3. Claude
echo "[3/5] 启动 Claude..."
python3 process_cases.py \
  --model claude \
  --use_ds_questions "$DS_FILE" \
  --case_ids $CASE_IDS \
  --standalone \
  > "$LOG_DIR/claude.log" 2>&1 &
CLAUDE_PID=$!
echo "  Claude PID: $CLAUDE_PID"

# 4. Gemini
echo "[4/5] 启动 Gemini..."
python3 process_cases.py \
  --model gemini \
  --use_ds_questions "$DS_FILE" \
  --case_ids $CASE_IDS \
  --standalone \
  > "$LOG_DIR/gemini.log" 2>&1 &
GEMINI_PID=$!
echo "  Gemini PID: $GEMINI_PID"

# 5. Qwen
echo "[5/5] 启动 Qwen..."
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
echo "⚠️  注意: 多个模型会并行写入同一个Excel文件"
echo "   如果出现文件锁定错误，模型会自动重试"
echo "   建议按顺序运行模型，或使用文件锁机制"
echo ""
echo "进程ID:"
echo "  DeepSeek: $DEEPSEEK_PID"
echo "  GPT-4o:   $GPT4O_PID"
echo "  Claude:    $CLAUDE_PID"
echo "  Gemini:   $GEMINI_PID"
echo "  Qwen:     $QWEN_PID"
echo ""
echo "日志文件:"
echo "  DeepSeek: $LOG_DIR/deepseek.log"
echo "  GPT-4o:   $LOG_DIR/gpt4o.log"
echo "  Claude:   $LOG_DIR/claude.log"
echo "  Gemini:   $LOG_DIR/gemini.log"
echo "  Qwen:     $LOG_DIR/qwen.log"
echo ""
echo "实时查看日志:"
echo "  tail -f $LOG_DIR/deepseek.log"
echo "  tail -f $LOG_DIR/gpt4o.log"
echo "  tail -f $LOG_DIR/claude.log"
echo "  tail -f $LOG_DIR/gemini.log"
echo "  tail -f $LOG_DIR/qwen.log"
echo ""
echo "或者同时查看所有日志:"
echo "  tail -f $LOG_DIR/*.log"
echo ""
echo "等待所有进程完成..."

# 等待所有进程完成
wait $DEEPSEEK_PID
DEEPSEEK_EXIT=$?
echo "[1/5] DeepSeek 完成 (退出码: $DEEPSEEK_EXIT)"

wait $GPT4O_PID
GPT4O_EXIT=$?
echo "[2/5] GPT-4o 完成 (退出码: $GPT4O_EXIT)"

wait $CLAUDE_PID
CLAUDE_EXIT=$?
echo "[3/5] Claude 完成 (退出码: $CLAUDE_EXIT)"

wait $GEMINI_PID
GEMINI_EXIT=$?
echo "[4/5] Gemini 完成 (退出码: $GEMINI_EXIT)"

wait $QWEN_PID
QWEN_EXIT=$?
echo "[5/5] Qwen 完成 (退出码: $QWEN_EXIT)"

echo ""
echo "=========================================="
echo "所有模型运行完成"
echo "=========================================="
echo "退出码:"
echo "  DeepSeek: $DEEPSEEK_EXIT"
echo "  GPT-4o:   $GPT4O_EXIT"
echo "  Claude:   $CLAUDE_EXIT"
echo "  Gemini:   $GEMINI_EXIT"
echo "  Qwen:     $QWEN_EXIT"
echo ""
echo "结果文件应该保存在: data/20个案例_统一评估结果_*.xlsx"
echo "每个模型的结果在不同的tab中"
echo ""

# 检查是否有错误
if [ $DEEPSEEK_EXIT -ne 0 ] || [ $GPT4O_EXIT -ne 0 ] || [ $CLAUDE_EXIT -ne 0 ] || [ $GEMINI_EXIT -ne 0 ] || [ $QWEN_EXIT -ne 0 ]; then
    echo "⚠️ 警告: 部分模型运行失败，请检查日志文件"
    exit 1
else
    echo "✓ 所有模型运行成功！"
    exit 0
fi
