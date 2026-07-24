#!/usr/bin/env bash
set -euo pipefail
ROOT=/data0/zhangpeng6/qlib/reproduction/dashboard
PID_FILE="$ROOT/dashboard.pid"
LOG="$ROOT/dashboard.log"
PORT="${PORT:-8765}"
AUTH_FILE="$ROOT/.dashboard.env"

if [ ! -r "$AUTH_FILE" ]; then
  echo "missing dashboard credentials: $AUTH_FILE" >&2
  exit 1
fi
set -a
# shellcheck disable=SC1090
source "$AUTH_FILE"
set +a

if [ -s "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
  echo "dashboard already running pid=$(cat "$PID_FILE") port=$PORT"
  exit 0
fi
setsid /data0/zhangpeng6/qlib/reproduction/venv/bin/python \
  "$ROOT/server.py" --host 0.0.0.0 --port "$PORT" </dev/null >>"$LOG" 2>&1 &
echo $! >"$PID_FILE"
echo "dashboard started pid=$! port=$PORT"
