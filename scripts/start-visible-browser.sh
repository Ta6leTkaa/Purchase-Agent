#!/bin/sh
set -eu

profile_dir="$(pwd)/.runtime/visible-browser"
mkdir -p "$profile_dir"

browser_app="/Applications/Google Chrome.app"
if [ ! -d "$browser_app" ]; then
  browser_app="$(find "$HOME/Library/Caches/ms-playwright" -type d \
    -name 'Google Chrome for Testing.app' \
    -print -quit)"
fi
if [ ! -d "$browser_app" ]; then
  echo "Visible Chromium is not installed. Run: python -m playwright install chromium" >&2
  exit 1
fi

open -na "$browser_app" --args \
  --remote-debugging-address=0.0.0.0 \
  --remote-debugging-port=9222 \
  --user-data-dir="$profile_dir" \
  --no-first-run \
  --no-default-browser-check \
  about:blank

echo "Visible agent browser started. Keep this window open."
echo "Set BROWSER_CDP_URL=http://host.docker.internal:9222 in .env and restart api."
