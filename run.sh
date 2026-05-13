#!/usr/bin/env bash
# Start the web UI locally and open it in your default browser.
#
#   ./run.sh
#
# Requires Python 3.10+ and dependencies (`pip install -r requirements.txt`).
# Port: FANTASY_BUMPS_PORT (default 5050)

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
APP="$DIR/app.py"
PORT="${FANTASY_BUMPS_PORT:-5050}"
export FANTASY_BUMPS_PORT="$PORT"
URL="http://127.0.0.1:${PORT}"

PY="${FANTASY_BUMPS_PYTHON:-}"
if [[ -z "$PY" ]]; then
  if command -v python3 >/dev/null 2>&1; then
    PY=python3
  elif command -v python >/dev/null 2>&1; then
    PY=python
  else
    echo "Install Python 3, or set FANTASY_BUMPS_PYTHON to your interpreter path."
    exit 1
  fi
fi

if [[ ! -f "$APP" ]]; then
  echo "Missing app.py in $DIR"
  exit 1
fi

open_browser_when_ready() {
  sleep 1.75
  if command -v open >/dev/null 2>&1; then
    open "$URL"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$URL"
  elif command -v explorer.exe >/dev/null 2>&1; then
    explorer.exe "$URL" || true
  else
    printf '\nOpen in your browser: %s\n' "$URL"
  fi
}

open_browser_when_ready &
exec "$PY" "$APP"
