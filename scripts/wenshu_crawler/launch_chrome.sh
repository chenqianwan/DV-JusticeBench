#!/usr/bin/env bash
# 启动一个带 CDP 调试端口的 Chrome，供 crawl.py 接管。
# Chrome 136+ 禁止在默认 profile 目录上开调试端口，所以这里用一份独立 profile，
# 首次运行会把默认 profile 的登录态（Cookie / Local Storage）复制过来。
set -euo pipefail

PORT="${WENSHU_PORT:-9222}"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
DST="${WENSHU_PROFILE:-$HOME/.wenshu-chrome-profile-safe}"

if curl -s --max-time 2 "http://127.0.0.1:$PORT/json/version" >/dev/null 2>&1; then
  echo "调试端口 $PORT 已经就绪，直接跑 crawl.py 即可。"
  exit 0
fi

if [ ! -d "$DST" ]; then
  echo "首次运行：创建独立 profile $DST"
  mkdir -p "$DST"
  echo "为避免混用旧账号登录态，不复制默认 Chrome 的 Cookie；请在弹出的窗口登录新账号。"
fi

echo "启动调试 Chrome（端口 ${PORT}，profile: ${DST}）…"
"$CHROME" --remote-debugging-port="${PORT}" --user-data-dir="${DST}" \
  "https://wenshu.court.gov.cn/" >/dev/null 2>&1 &

for _ in $(seq 1 30); do
  if curl -s --max-time 2 "http://127.0.0.1:${PORT}/json/version" >/dev/null 2>&1; then
    echo "调试端口就绪。确认页面已登录后，运行：python3 crawl.py"
    exit 0
  fi
  sleep 1
done

echo "等待调试端口超时，请检查 Chrome 是否正常启动。" >&2
exit 1
