#!/bin/bash
# 并行运行GPT-5和Qwen-Max，每个处理最后20个案例

# 最后20个案例的ID列表
CASE_IDS="case_20251230_134952_90 case_20251230_134952_91 case_20251230_134952_92 case_20251230_134952_93 case_20251230_134952_94 case_20251230_134952_95 case_20251230_134952_96 case_20251230_134952_97 case_20251230_134952_98 case_20251230_134952_99 case_20260103_155150_0 case_20260103_155150_1 case_20260103_155150_105 case_20260103_155150_106 case_20260103_155150_2 case_20260103_155150_3 case_20260103_155150_4 case_20260103_155150_5 case_20260103_155150_6 case_20260103_155150_7"

# 切换到项目目录
cd /Users/chenlong/WorkSpace/huangyidan

echo "=========================================="
echo "开始并行运行GPT-5和Qwen-Max，每个处理20个案例"
echo "=========================================="
echo ""

# 1. GPT-5
echo "启动: GPT-5"
python3 process_cases.py --model gpt4o --gpt-model gpt-5 --case_ids $CASE_IDS --standalone > logs/gpt5_20cases.log 2>&1 &
GPT5_PID=$!
echo "  PID: $GPT5_PID"

# 2. Qwen-Max
echo "启动: Qwen-Max"
python3 process_cases.py --model qwen --qwen-model qwen-max --case_ids $CASE_IDS --standalone > logs/qwen_max_20cases.log 2>&1 &
QWEN_PID=$!
echo "  PID: $QWEN_PID"

echo ""
echo "=========================================="
echo "所有任务已启动，PID列表:"
echo "  GPT-5:         $GPT5_PID"
echo "  Qwen-Max:      $QWEN_PID"
echo "=========================================="
echo ""
echo "日志文件位置:"
echo "  logs/gpt5_20cases.log"
echo "  logs/qwen_max_20cases.log"
echo ""
echo "使用以下命令查看进度:"
echo "  tail -f logs/gpt5_20cases.log"
echo "  tail -f logs/qwen_max_20cases.log"
echo ""
echo "使用以下命令检查进程状态:"
echo "  ps -p $GPT5_PID,$QWEN_PID"
