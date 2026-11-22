#!/bin/bash
# Start the mock model and one app on a throwaway home, for driving the UI by hand or with Playwright.
# Usage: testkit/dev_app.sh PORT HOME   (stops with: pkill -f "ui --port PORT")
PORT="${1:-8790}"; HOME_DIR="${2:-/tmp/mp-dev-home}"; R="$(cd "$(dirname "$0")/.." && pwd)"
pgrep -f "mock_llm.py 8911" >/dev/null || (cd "$R" && nohup python3 testkit/mock_llm.py 8911 /tmp/mock.jsonl > /tmp/mock.log 2>&1 &)
mkdir -p "$HOME_DIR"
[ -f "$HOME_DIR/meshpool.config" ] || printf 'MESHPOOL_BASE_URL=http://127.0.0.1:8911/v1\nMESHPOOL_API_KEY=test-key-1234\n' > "$HOME_DIR/meshpool.config"
(cd "$R/app" && MESHPOOL_HOME="$HOME_DIR" HOME="$HOME_DIR/user" MH_BROWSER_HEADLESS=1 MH_LEARN=0 nohup python3 -m meshpool_harness ui --port "$PORT" --no-browser > "/tmp/app-$PORT.log" 2>&1 &)
for i in $(seq 1 40); do curl -s "http://127.0.0.1:$PORT/" >/dev/null && echo "up on $PORT" && exit 0; sleep 0.5; done
echo "failed"; tail -5 "/tmp/app-$PORT.log"; exit 1
