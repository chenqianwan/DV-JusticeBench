#!/bin/bash
# 检查Git Push状态和日志

echo "=========================================="
echo "Git Push 状态检查"
echo "=========================================="
echo ""

# 检查是否有git push进程正在运行
echo "1. 检查正在运行的Git Push进程:"
echo "----------------------------------------"
GIT_PUSH_PIDS=$(ps aux | grep -E "git.*push|git-push" | grep -v grep | awk '{print $2}')
if [[ -n "$GIT_PUSH_PIDS" ]]; then
    echo "发现正在运行的Git Push进程:"
    ps aux | grep -E "git.*push|git-push" | grep -v grep
    echo ""
    echo "进程详情:"
    for pid in $GIT_PUSH_PIDS; do
        echo "  PID: $pid"
        if [[ -d "/proc/$pid" ]]; then
            echo "    启动时间: $(ps -p $pid -o lstart= 2>/dev/null || echo 'N/A')"
            echo "    运行时间: $(ps -p $pid -o etime= 2>/dev/null || echo 'N/A')"
        fi
    done
else
    echo "✓ 没有发现正在运行的Git Push进程"
fi
echo ""

# 检查最近的git push日志
echo "2. 检查最近的Git Push日志:"
echo "----------------------------------------"
if [[ -d "logs" ]]; then
    PUSH_LOGS=$(find logs/ -name "*git*push*" -o -name "*push*git*" 2>/dev/null | sort -r | head -5)
    if [[ -n "$PUSH_LOGS" ]]; then
        echo "找到以下Push日志文件:"
        for log in $PUSH_LOGS; do
            echo "  $log"
            echo "    最后修改: $(stat -f "%Sm" -t "%Y-%m-%d %H:%M:%S" "$log" 2>/dev/null || echo 'N/A')"
            echo "    文件大小: $(du -h "$log" | cut -f1)"
            echo "    最后几行:"
            tail -3 "$log" 2>/dev/null | sed 's/^/      /'
            echo ""
        done
    else
        echo "✓ 没有找到Push日志文件"
    fi
else
    echo "✓ logs目录不存在"
fi
echo ""

# 检查git状态
echo "3. Git仓库状态:"
echo "----------------------------------------"
cd /Users/chenlong/WorkSpace/huangyidan 2>/dev/null || exit 1

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null)
if [[ -n "$CURRENT_BRANCH" ]]; then
    echo "当前分支: $CURRENT_BRANCH"
    
    # 检查是否有未推送的提交
    REMOTE=$(git remote | head -n1)
    if [[ -n "$REMOTE" ]]; then
        echo "远程仓库: $REMOTE"
        
        # 获取远程分支信息
        git fetch "$REMOTE" "$CURRENT_BRANCH" 2>/dev/null
        
        LOCAL_COMMITS=$(git rev-list ${REMOTE}/${CURRENT_BRANCH}..HEAD 2>/dev/null | wc -l | tr -d ' ')
        if [[ "$LOCAL_COMMITS" -gt 0 ]]; then
            echo "⚠️  有 $LOCAL_COMMITS 个未推送的提交"
            echo "未推送的提交:"
            git log ${REMOTE}/${CURRENT_BRANCH}..HEAD --oneline | head -5 | sed 's/^/  /'
        else
            echo "✓ 所有提交都已推送"
        fi
    else
        echo "⚠️  未配置远程仓库"
    fi
    
    # 检查工作目录状态
    if [[ -n "$(git status --porcelain)" ]]; then
        echo ""
        echo "⚠️  工作目录有未提交的更改:"
        git status --short | sed 's/^/  /'
    else
        echo "✓ 工作目录干净"
    fi
else
    echo "⚠️  不在Git仓库中"
fi
echo ""

# 检查最近的git操作
echo "4. 最近的Git操作记录:"
echo "----------------------------------------"
if [[ -d ".git" ]]; then
    echo "最近的提交:"
    git log --oneline --decorate -5 | sed 's/^/  /'
    echo ""
    echo "最近的reflog (最后10条):"
    git reflog -10 2>/dev/null | sed 's/^/  /' || echo "  (无reflog记录)"
else
    echo "⚠️  不是Git仓库"
fi
echo ""

# 检查是否有后台任务
echo "5. 检查后台任务:"
echo "----------------------------------------"
JOBS=$(jobs -l 2>/dev/null)
if [[ -n "$JOBS" ]]; then
    echo "当前shell的后台任务:"
    echo "$JOBS"
else
    echo "✓ 当前shell没有后台任务"
fi
echo ""

echo "=========================================="
echo "检查完成"
echo "=========================================="
