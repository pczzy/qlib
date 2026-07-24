#!/usr/bin/env bash
set -euo pipefail

ROOT=/data0/zhangpeng6/qlib/reproduction
PID_FILE="$ROOT/pipeline.pid"
CRON_MARKER="# qlib-reproduction-managed"

start() {
  if [ -s "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
    echo "pipeline already running pid=$(cat "$PID_FILE")"
    exit 75
  fi
  setsid "$ROOT/run_pipeline.sh" </dev/null \
    >>"$ROOT/logs/pipeline-launcher.log" 2>&1 &
  echo $! >"$PID_FILE"
  echo "started pid=$!"
}

case "${1:-status}" in
  start) start ;;
  stop)
    if [ -s "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      kill "$(cat "$PID_FILE")"
      echo "stop signal sent to pid=$(cat "$PID_FILE")"
    else
      echo "pipeline is not running"
    fi
    ;;
  status)
    if [ -s "$PID_FILE" ] && kill -0 "$(cat "$PID_FILE")" 2>/dev/null; then
      echo "running pid=$(cat "$PID_FILE")"
    else
      echo "not running"
    fi
    [ -f "$ROOT/state/pipeline-state.json" ] && cat "$ROOT/state/pipeline-state.json"
    [ -f "$ROOT/state/data-fetch-state.json" ] && cat "$ROOT/state/data-fetch-state.json"
    ;;
  logs)
    latest="$(find "$ROOT/logs/pipeline" -type f -name '*.jsonl' 2>/dev/null | sort | tail -n1)"
    [ -n "$latest" ] && tail -n "${2:-50}" "$latest" || echo "no pipeline log"
    ;;
  results)
    readlink -f "$ROOT/analysis/latest" 2>/dev/null || echo "no result"
    ;;
  retry)
    rm -f "$ROOT/status/"*.status
    rm -f "$ROOT/state/training-failed-baseline.txt"
    start
    ;;
  enable)
    current="$(crontab -l 2>/dev/null | grep -vF "$CRON_MARKER" || true)"
    { printf '%s\n' "$current"; printf '*/10 * * * * %s start %s\n' "$ROOT/reproctl.sh" "$CRON_MARKER"; } |
      sed '/^$/d' | crontab -
    echo "enabled: every 10 minutes checks GitHub baseline, then direct Sina increments; unchanged dates skip training"
    ;;
  disable)
    crontab -l 2>/dev/null | grep -vF "$CRON_MARKER" | crontab - || true
    echo "managed schedule disabled"
    ;;
  *)
    echo "usage: $0 {start|stop|status|logs [N]|results|retry|enable|disable}" >&2
    exit 64
    ;;
esac
