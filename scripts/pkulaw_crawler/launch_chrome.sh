#!/bin/zsh
set -euo pipefail

exec "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/pkulaw-cdp-profile \
  --no-first-run \
  --no-default-browser-check \
  https://www.pkulaw.com/case
