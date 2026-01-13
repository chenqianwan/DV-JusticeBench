#!/bin/bash
# 并行运行5个模型，每个处理最后20个案例

# 最后20个案例的ID列表
CASE_IDS="case_20251230_134952_90 case_20251230_134952_91 case_20251230_134952_92 case_20251230_134952_93 case_20251230_134952_94 case_20251230_134952_95 case_20251230_134952_96 case_20251230_134952_97 case_20251230_134952_98 case_20251230_134952_99 case_20260103_155150_0 case_20260103_155150_1 case_20260103_155150_105 case_20260103_155150_106 case_20260103_155150_2 case_20260103_155150_3 case_20260103_155150_4 case_20260103_155150_5 case_20260103_155150_6 case_20260103_155150_7"

# 切换到项目目录
cd /Users/chenlong/WorkSpace/huangyidan

echo "=========================================="
echo "开始并行运行5个模型，每个处理20个案例"
echo "=========================================="
echo ""

# 1. GPT-4o
echo "启动: GPT-4o"
python3 process_cases.py --model gpt4o --gpt-model gpt-4o --case_ids $CASE_IDS --standalone > logs/gpt4o_20cases.log 2>&1 &
GPT4O_PID=$!
echo "  PID: $GPT4O_PID"

# 2. GPT o3-2025-04-16 (推理模型)
echo "启动: GPT o3-2025-04-16"
python3 process_cases.py --model gpt4o --gpt-model o3-2025-04-16 --case_ids $CASE_IDS --standalone > logs/gpt_o3_20cases.log 2>&1 &
GPT_O3_PID=$!
echo "  PID: $GPT_O3_PID"

# 3. DeepSeek (非thinking模式)
echo "启动: DeepSeek (非thinking模式)"
python3 process_cases.py --model deepseek --no-thinking --case_ids $CASE_IDS --standalone > logs/deepseek_no_thinking_20cases.log 2>&1 &
DEEPSEEK_PID=$!
echo "  PID: $DEEPSEEK_PID"

# 4. Gemini 2.5 Flash
echo "启动: Gemini 2.5 Flash"
python3 process_cases.py --model gemini --case_ids $CASE_IDS --standalone > logs/gemini_20cases.log 2>&1 &
GEMINI_PID=$!
echo "  PID: $GEMINI_PID"

# 5. Claude Opus 4
echo "启动: Claude Opus 4"
python3 process_cases.py --model claude --case_ids $CASE_IDS --standalone > logs/claude_20cases.log 2>&1 &
CLAUDE_PID=$!
echo "  PID: $CLAUDE_PID"

echo ""
echo "=========================================="
echo "所有任务已启动，PID列表:"
echo "  GPT-4o:        $GPT4O_PID"
echo "  GPT o3:        $GPT_O3_PID"
echo "  DeepSeek:      $DEEPSEEK_PID"
echo "  Gemini:        $GEMINI_PID"
echo "  Claude:        $CLAUDE_PID"
echo "=========================================="
echo ""
echo "日志文件位置:"
echo "  logs/gpt4o_20cases.log"
echo "  logs/gpt_o3_20cases.log"
echo "  logs/deepseek_no_thinking_20cases.log"
echo "  logs/gemini_20cases.log"
echo "  logs/claude_20cases.log"
echo ""
echo "使用以下命令查看进度:"
echo "  tail -f logs/gpt4o_20cases.log"
echo "  tail -f logs/gpt_o3_20cases.log"
echo "  tail -f logs/deepseek_no_thinking_20cases.log"
echo "  tail -f logs/gemini_20cases.log"
echo "  tail -f logs/claude_20cases.log"
echo ""
echo "使用以下命令检查进程状态:"
echo "  ps -p $GPT4O_PID,$GPT_O3_PID,$DEEPSEEK_PID,$GEMINI_PID,$CLAUDE_PID"
