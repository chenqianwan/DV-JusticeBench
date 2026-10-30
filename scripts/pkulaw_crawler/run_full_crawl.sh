#!/bin/zsh
set -u
unsetopt ERR_EXIT

SCRIPT_DIR="${0:A:h}"
PROJECT_DIR="${SCRIPT_DIR:h:h}"
OUTPUT_FILE="${PROJECT_DIR}/data/pkulaw_raw/pkulaw_family_violence_2023_2025.jsonl"
LOG_FILE="${PROJECT_DIR}/data/pkulaw_raw/pkulaw_full.log"

mkdir -p "${PROJECT_DIR}/data/pkulaw_raw"
cd "${PROJECT_DIR}"

trap 'exit 0' INT TERM

while true; do
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 启动或恢复抓取" >>"${LOG_FILE}"
  if /Users/chenlong/.pyenv/versions/3.11.0/bin/python3 -u \
      scripts/pkulaw_crawler/crawler.py \
      --keyword 家庭暴力 \
      --case-types 刑事案件,民事案件 \
      --years 2023,2024,2025 \
      --document-type 判决书 \
      --page-size 100 \
      --min-delay 8 \
      --max-delay 15 \
      --output "${OUTPUT_FILE}" >>"${LOG_FILE}" 2>&1; then
    status=0
  else
    status=$?
  fi
  if [[ ${status} -eq 0 ]]; then
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] 全部抓取完成" >>"${LOG_FILE}"
    exit 0
  fi
  echo "[$(date '+%Y-%m-%d %H:%M:%S')] 抓取异常退出（${status}），300 秒后自动恢复" >>"${LOG_FILE}"
  sleep 300
done
