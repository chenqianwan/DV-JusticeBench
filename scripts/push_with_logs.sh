#!/bin/bash
# Git push with detailed logging

cd /Users/chenlong/WorkSpace/huangyidan

echo "=========================================="
echo "Starting Git Push with Detailed Logging"
echo "=========================================="
echo ""
echo "Repository: $(pwd)"
echo "Remote: $(git remote get-url origin)"
echo "Branch: main"
echo "Commits to push:"
git log origin/main..main --oneline 2>/dev/null || git log --oneline -5
echo ""
echo "=========================================="
echo "Starting push..."
echo "=========================================="
echo ""

# Set git to show progress
export GIT_PROGRESS_DELAY=0
export GIT_TRACE=1
export GIT_CURL_VERBOSE=1

# Push with verbose output
git push -u origin main --progress 2>&1 | tee /tmp/git_push_log.txt

echo ""
echo "=========================================="
echo "Push completed. Checking status..."
echo "=========================================="

# Check if push was successful
if git ls-remote --heads origin main | grep -q main; then
    echo "✓ Push successful! Remote main branch exists."
    echo ""
    echo "Remote commit:"
    git ls-remote origin main
else
    echo "✗ Push may have failed. Check the log above for errors."
fi

echo ""
echo "Full log saved to: /tmp/git_push_log.txt"
