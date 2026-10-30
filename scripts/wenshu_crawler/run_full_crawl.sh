#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="${WENSHU_PYTHON:-${ROOT}/.venv/bin/python}"
CRAWLER="${ROOT}/scripts/wenshu_crawler/crawl_single_fixed.py"
OUTPUT_DIR="${ROOT}/data/wenshu_raw"
OUTPUT_FILE="${OUTPUT_DIR}/家庭暴力_判决书_2023-2025.jsonl"
CRAWL_LOG="${OUTPUT_DIR}/crawl_家庭暴力_判决书_2023-2025_single.log"
SUPERVISOR_LOG="${OUTPUT_DIR}/supervisor_判决书_single.log"
CHROME_LOG="${OUTPUT_DIR}/chrome_cdp.log"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROFILE="${WENSHU_PROFILE:-${HOME}/.wenshu-chrome-profile-safe}"
PORT="${WENSHU_PORT:-9222}"
MAX_NEW="${WENSHU_MAX_NEW:-0}"

mkdir -p "${OUTPUT_DIR}"

if [[ ! -x "${PYTHON}" ]]; then
  printf '%s\n' "Python environment not found: ${PYTHON}" >&2
  exit 2
fi

if ! curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
  "${CHROME}" --remote-debugging-port="${PORT}" --user-data-dir="${PROFILE}" \
    "https://wenshu.court.gov.cn/" >>"${CHROME_LOG}" 2>&1 &
  ready=0
  for _ in $(seq 1 30); do
    if curl -fsS --max-time 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
      ready=1
      break
    fi
    sleep 1
  done
  if [[ "${ready}" -ne 1 ]]; then
    printf '%s Chrome CDP failed to start; stopped without retry\n' \
      "$(date '+%F %T')" >>"${SUPERVISOR_LOG}"
    exit 2
  fi
  printf '%s dedicated Chrome started; log in, then rerun this script\n' \
    "$(date '+%F %T')" >>"${SUPERVISOR_LOG}"
  printf '%s\n' "专用 Chrome 已启动。请完成登录后重新运行本脚本。"
  exit 4
fi

printf '%s starting safe crawl; existing=%s; max_new=%s\n' \
  "$(date '+%F %T')" "$(wc -l < "${OUTPUT_FILE}" 2>/dev/null || printf 0)" \
  "${MAX_NEW}" >>"${SUPERVISOR_LOG}"

set +e
caffeinate -dimsu "${PYTHON}" -u "${CRAWLER}" \
  --keyword 家庭暴力 \
  --start-date 2023-01-01 \
  --end-date 2025-12-31 \
  --document-type 判决书 \
  --out "${OUTPUT_FILE}" \
  --min-delay 12 \
  --max-delay 20 \
  --batch-size 30 \
  --batch-pause-min 600 \
  --batch-pause-max 900 \
  --query-pause-min 120 \
  --query-pause-max 240 \
  --max-new "${MAX_NEW}" >>"${CRAWL_LOG}" 2>&1
status=$?
set -e

case "${status}" in
  0)
    printf '%s crawl completed or reached max_new\n' "$(date '+%F %T')" >>"${SUPERVISOR_LOG}"
    ;;
  3)
    printf '%s safety verification detected; stopped without retry\n' \
      "$(date '+%F %T')" >>"${SUPERVISOR_LOG}"
    ;;
  *)
    printf '%s crawler stopped with status=%s; stopped without retry\n' \
      "$(date '+%F %T')" "${status}" >>"${SUPERVISOR_LOG}"
    ;;
esac

exit "${status}"
