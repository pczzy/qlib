#!/usr/bin/env bash
set -uo pipefail

ROOT=/data0/zhangpeng6/qlib/reproduction
PY="$ROOT/venv/bin/python"
ROLL="$ROOT/qlibAssistant/roll/roll.py"
CONFIG="$ROOT/run-config.yaml"
STATUS_DIR="$ROOT/status"
LOG_DIR="$ROOT/logs/training"
LOCK_FILE="$ROOT/train.lock"

mkdir -p "$STATUS_DIR" "$LOG_DIR"
exec 9>"$LOCK_FILE"
if ! flock -n 9; then
  echo "another training process holds $LOCK_FILE" >&2
  exit 75
fi

overall=0
for model in XGBoost Linear DoubleEnsemble LightGBM CatBoost; do
  status_file="$STATUS_DIR/$model.status"
  log_file="$LOG_DIR/$model.log"
  if grep -q '^exit_status=0$' "$status_file" 2>/dev/null; then
    echo "skip previously successful model=$model"
    continue
  fi
  started=$(date -u +%FT%TZ)
  {
    echo "model=$model"
    echo "started_utc=$started"
    echo "command=$PY $ROLL --config_path=$CONFIG --model_name=$model train start_custom"
  } > "$status_file"
  "$PY" "$ROLL" \
    --config_path="$CONFIG" \
    --model_name="$model" \
    train start_custom >> "$log_file" 2>&1
  rc=$?
  {
    echo "finished_utc=$(date -u +%FT%TZ)"
    echo "exit_status=$rc"
  } >> "$status_file"
  if [ "$rc" -ne 0 ]; then
    overall=1
  fi
done
exit "$overall"
