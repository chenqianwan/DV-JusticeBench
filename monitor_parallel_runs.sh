#!/bin/bash
# 监控5个并行运行的模型进度

echo "=========================================="
echo "并行运行监控"
echo "=========================================="
echo ""

# 检查日志文件是否存在
check_log() {
    local log_file=$1
    local model_name=$2
    
    if [ -f "$log_file" ]; then
        local lines=$(wc -l < "$log_file" 2>/dev/null || echo "0")
        local size=$(du -h "$log_file" 2>/dev/null | cut -f1 || echo "0")
        echo "  $model_name:"
        echo "    日志行数: $lines"
        echo "    文件大小: $size"
        
        # 显示最后几行
        echo "    最新日志:"
        tail -3 "$log_file" 2>/dev/null | sed 's/^/      /'
    else
        echo "  $model_name: 日志文件不存在"
    fi
    echo ""
}

check_log "logs/gpt4o_20cases.log" "GPT-4o"
check_log "logs/gpt_o3_20cases.log" "GPT o3-2025-04-16"
check_log "logs/deepseek_no_thinking_20cases.log" "DeepSeek (非thinking)"
check_log "logs/gemini_20cases.log" "Gemini 2.5 Flash"
check_log "logs/claude_20cases.log" "Claude Opus 4"

echo "=========================================="
echo "进程状态:"
echo "=========================================="
ps aux | grep "process_cases.py" | grep -v grep | awk '{print "  PID: "$2" - "$11" "$12" "$13" "$14" "$15}'

echo ""
echo "使用以下命令查看实时日志:"
echo "  tail -f logs/gpt4o_20cases.log"
echo "  tail -f logs/gpt_o3_20cases.log"
echo "  tail -f logs/deepseek_no_thinking_20cases.log"
echo "  tail -f logs/gemini_20cases.log"
echo "  tail -f logs/claude_20cases.log"
