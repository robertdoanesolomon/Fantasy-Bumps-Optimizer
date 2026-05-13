#!/bin/bash
# Web UI: conda env Python, no `conda activate` required.
# Usage:
#   ./run_web.sh              foreground (logs in this terminal); fails if port busy
#   ./run_web.sh start | on   background on 127.0.0.1:5050 (log: fantasy_bumps_web.log)
#   ./run_web.sh stop | off   stop whatever is listening on 5050
#   ./run_web.sh restart      stop then start (background)
#   ./run_web.sh status       show whether server is listening
#   ./run_web.sh toggle       stop if running, else start (background)

set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="${FANTASY_BUMPS_PYTHON:-/usr/local/Caskroom/miniconda/base/envs/fantasy_bumps/bin/python}"
APP="$DIR/app.py"
PORT="${FANTASY_BUMPS_PORT:-5050}"
LOG="$DIR/fantasy_bumps_web.log"

listening_pids() {
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true
}

stop_server() {
  local pids
  pids="$(listening_pids)"
  if [[ -z "$pids" ]]; then
    echo "Nothing listening on 127.0.0.1:${PORT} (already off)."
    return 0
  fi
  echo "$pids" | xargs kill 2>/dev/null || true
  sleep 0.5
  pids="$(listening_pids)"
  if [[ -n "$pids" ]]; then
    echo "$pids" | xargs kill -9 2>/dev/null || true
  fi
  sleep 0.2
  if [[ -n "$(listening_pids)" ]]; then
    echo "Could not free port ${PORT}."
    return 1
  fi
  echo "Stopped (port ${PORT} free)."
}

start_background() {
  if [[ -n "$(listening_pids)" ]]; then
    echo "Port ${PORT} already in use. Run: $0 stop   (or: $0 toggle)"
    return 1
  fi
  nohup env FANTASY_BUMPS_PORT="$PORT" "$PY" "$APP" >>"$LOG" 2>&1 &
  disown "$!" 2>/dev/null || true
  sleep 0.4
  if [[ -z "$(listening_pids)" ]]; then
    echo "Failed to start. Last log lines:"
    tail -n 20 "$LOG" 2>/dev/null || true
    return 1
  fi
  echo "Running in background — http://127.0.0.1:${PORT}"
  echo "Log: $LOG   Stop: $0 stop"
}

cmd="${1:-fg}"
case "$cmd" in
  start|on)
    start_background
    ;;
  stop|off)
    stop_server
    ;;
  restart)
    stop_server || true
    start_background
    ;;
  status)
    pids="$(listening_pids)"
    if [[ -n "$pids" ]]; then
      echo "On  — http://127.0.0.1:${PORT}  (PID(s): $(echo "$pids" | tr '\n' ' '))"
    else
      echo "Off — nothing on port ${PORT}"
    fi
    ;;
  toggle)
    if [[ -n "$(listening_pids)" ]]; then
      stop_server
    else
      start_background
    fi
    ;;
  fg|foreground)
    if [[ -n "$(listening_pids)" ]]; then
      echo "Port ${PORT} already in use (PID(s): $(listening_pids | tr '\n' ' '))."
      echo "Stop it with:  $0 stop"
      exit 1
    fi
    export FANTASY_BUMPS_PORT="$PORT"
    exec "$PY" "$APP"
    ;;
  -h|--help|help)
    sed -n '2,10p' "$0" | sed 's/^# //' | sed 's/^#//'
    ;;
  *)
    echo "Unknown: $cmd  (try: $0 --help)"
    exit 1
    ;;
esac
