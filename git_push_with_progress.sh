#!/bin/bash
# Git Push with Progress and Time Estimation
# 带进度显示和时间估计的Git Push脚本

set -e

# 颜色定义
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# 日志文件
LOG_DIR="logs"
mkdir -p "$LOG_DIR"
LOG_FILE="$LOG_DIR/git_push_$(date +%Y%m%d_%H%M%S).log"

# 日志函数
log() {
    local message="$1"
    local timestamp=$(date '+%Y-%m-%d %H:%M:%S')
    echo "[$timestamp] $message" | tee -a "$LOG_FILE"
}

log_info() {
    log "INFO: $1"
    echo -e "${BLUE}INFO:${NC} $1"
}

log_success() {
    log "SUCCESS: $1"
    echo -e "${GREEN}SUCCESS:${NC} $1"
}

log_error() {
    log "ERROR: $1"
    echo -e "${RED}ERROR:${NC} $1" >&2
}

log_warning() {
    log "WARNING: $1"
    echo -e "${YELLOW}WARNING:${NC} $1"
}

# 显示帮助信息
show_help() {
    cat << EOF
用法: $0 [选项] [远程仓库] [分支]

选项:
    -h, --help          显示帮助信息
    -v, --verbose       显示详细输出
    -f, --force         强制推送（使用 --force）
    --dry-run           仅显示将要执行的操作，不实际推送
    --no-verify         跳过pre-push钩子

示例:
    $0                    # 推送到默认远程和当前分支
    $0 origin main        # 推送到origin的main分支
    $0 -f origin main     # 强制推送到origin的main分支
    $0 --dry-run          # 预览推送操作

EOF
}

# 解析参数
DRY_RUN=false
FORCE=false
VERBOSE=false
NO_VERIFY=false
REMOTE=""
BRANCH=""

while [[ $# -gt 0 ]]; do
    case $1 in
        -h|--help)
            show_help
            exit 0
            ;;
        -v|--verbose)
            VERBOSE=true
            shift
            ;;
        -f|--force)
            FORCE=true
            shift
            ;;
        --dry-run)
            DRY_RUN=true
            shift
            ;;
        --no-verify)
            NO_VERIFY=true
            shift
            ;;
        -*)
            log_error "未知选项: $1"
            show_help
            exit 1
            ;;
        *)
            if [[ -z "$REMOTE" ]]; then
                REMOTE="$1"
            elif [[ -z "$BRANCH" ]]; then
                BRANCH="$1"
            else
                log_error "参数过多: $1"
                show_help
                exit 1
            fi
            shift
            ;;
    esac
done

# 获取当前分支和远程信息
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "")
if [[ -z "$CURRENT_BRANCH" ]]; then
    log_error "不在Git仓库中"
    exit 1
fi

# 如果没有指定远程，使用默认远程
if [[ -z "$REMOTE" ]]; then
    REMOTE=$(git remote | head -n1)
    if [[ -z "$REMOTE" ]]; then
        log_error "未找到远程仓库"
        exit 1
    fi
fi

# 如果没有指定分支，使用当前分支
if [[ -z "$BRANCH" ]]; then
    BRANCH="$CURRENT_BRANCH"
fi

log_info "=========================================="
log_info "Git Push with Progress"
log_info "=========================================="
log_info "日志文件: $LOG_FILE"
log_info "远程仓库: $REMOTE"
log_info "分支: $BRANCH"
log_info "当前分支: $CURRENT_BRANCH"

# 检查是否有未提交的更改
if [[ -n "$(git status --porcelain)" ]]; then
    log_warning "工作目录有未提交的更改"
    git status --short | while read line; do
        log_warning "  $line"
    done
    read -p "是否继续推送? (y/N): " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        log_info "操作已取消"
        exit 0
    fi
fi

# 检查是否有未推送的提交
LOCAL_COMMITS=$(git rev-list ${REMOTE}/${BRANCH}..HEAD 2>/dev/null | wc -l | tr -d ' ')
if [[ "$LOCAL_COMMITS" -eq 0 ]]; then
    log_warning "没有需要推送的提交"
    exit 0
fi

log_info "本地有 $LOCAL_COMMITS 个未推送的提交"

# 显示将要推送的提交
log_info "将要推送的提交:"
git log ${REMOTE}/${BRANCH}..HEAD --oneline --decorate | head -10 | while read line; do
    log_info "  $line"
done
if [[ "$LOCAL_COMMITS" -gt 10 ]]; then
    log_info "  ... 还有 $((LOCAL_COMMITS - 10)) 个提交"
fi

# 估算推送大小
log_info "计算推送大小..."
PUSH_SIZE=$(git count-objects -vH | grep "size-pack" | awk '{print $2}')
log_info "推送大小: $PUSH_SIZE"

# 估算推送时间（基于大小，粗略估计）
# 假设平均速度: 1MB/s (可以根据实际情况调整)
SIZE_MB=$(echo "$PUSH_SIZE" | sed 's/[^0-9.]//g')
if [[ -n "$SIZE_MB" ]]; then
    ESTIMATED_SECONDS=$(echo "$SIZE_MB * 1" | bc 2>/dev/null || echo "5")
    ESTIMATED_MINUTES=$(echo "scale=1; $ESTIMATED_SECONDS / 60" | bc 2>/dev/null || echo "0.1")
    log_info "预计推送时间: ${ESTIMATED_MINUTES} 分钟"
else
    log_info "预计推送时间: 约 5-30 秒（取决于网络速度）"
fi

# Dry run模式
if [[ "$DRY_RUN" == true ]]; then
    log_info "=========================================="
    log_info "DRY RUN - 预览模式"
    log_info "=========================================="
    log_info "将要执行的命令:"
    if [[ "$FORCE" == true ]]; then
        log_info "  git push --force $REMOTE $BRANCH"
    else
        log_info "  git push $REMOTE $BRANCH"
    fi
    exit 0
fi

# 执行推送
log_info "=========================================="
log_info "开始推送..."
log_info "=========================================="

START_TIME=$(date +%s)

# 构建git push命令
PUSH_CMD="git push"
if [[ "$FORCE" == true ]]; then
    PUSH_CMD="$PUSH_CMD --force"
fi
if [[ "$NO_VERIFY" == true ]]; then
    PUSH_CMD="$PUSH_CMD --no-verify"
fi
if [[ "$VERBOSE" == true ]]; then
    PUSH_CMD="$PUSH_CMD --verbose"
fi
PUSH_CMD="$PUSH_CMD $REMOTE $BRANCH"

log_info "执行命令: $PUSH_CMD"

# 执行推送并捕获输出
if $PUSH_CMD 2>&1 | tee -a "$LOG_FILE"; then
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    
    log_success "=========================================="
    log_success "推送成功完成！"
    log_success "=========================================="
    log_success "实际耗时: ${ELAPSED} 秒"
    log_success "日志已保存到: $LOG_FILE"
    
    # 显示推送后的状态
    log_info "当前状态:"
    git log --oneline -1 | while read line; do
        log_info "  最新提交: $line"
    done
    
    exit 0
else
    END_TIME=$(date +%s)
    ELAPSED=$((END_TIME - START_TIME))
    
    log_error "=========================================="
    log_error "推送失败！"
    log_error "=========================================="
    log_error "耗时: ${ELAPSED} 秒"
    log_error "请查看日志文件: $LOG_FILE"
    exit 1
fi
