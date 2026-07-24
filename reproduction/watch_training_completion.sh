#!/usr/bin/env bash
set -uo pipefail

ROOT=/data0/zhangpeng6/qlib/reproduction
PIPELINE_PID="${1:?pipeline pid required}"
LOG="$ROOT/logs/training-completion-watch.log"

printf '%s watch_started pipeline_pid=%s\n' "$(date -u +%FT%TZ)" "$PIPELINE_PID" >>"$LOG"
while kill -0 "$PIPELINE_PID" 2>/dev/null; do
  sleep 60
done

if "$ROOT/venv/bin/python" - "$ROOT/evidence/final-audit/model_audit.json" <<'PY'
import json, sys
audit = json.load(open(sys.argv[1]))
raise SystemExit(0 if audit.get("success") and audit.get("valid_recorders") == 25 else 1)
PY
then
  "$ROOT/reproctl.sh" enable >>"$LOG" 2>&1
  printf '%s audit_success cron_enabled=true\n' "$(date -u +%FT%TZ)" >>"$LOG"
else
  printf '%s audit_failed cron_enabled=false\n' "$(date -u +%FT%TZ)" >>"$LOG"
fi
