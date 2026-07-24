#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/data0/zhangpeng6/qlib/reproduction
ARCHIVE=/data0/zhangpeng6/rsync/qlib_bin.tar.gz
PY="$ROOT/venv/bin/python"
CONFIG="$ROOT/run-config.yaml"
STATE="$ROOT/state/pipeline-state.json"
LOCK="$ROOT/pipeline.lock"
LOG_DIR="$ROOT/logs/pipeline"
RUN_ID="$(date -u +%Y%m%dT%H%M%SZ)"
LOG="$LOG_DIR/$RUN_ID.jsonl"
RAW_LOG="$LOG_DIR/$RUN_ID.command.log"
TRAIN_FAILURE_MARKER="$ROOT/state/training-failed-baseline.txt"
MIN_FREE_KB=$((10 * 1024 * 1024))

mkdir -p "$LOG_DIR" "$ROOT/state" "$ROOT/evidence/final-audit"
exec 9>"$LOCK"
if ! flock -n 9; then
  printf '{"utc":"%s","level":"error","event":"lock_busy","lock":"%s"}\n' \
    "$(date -u +%FT%TZ)" "$LOCK" >&2
  exit 75
fi

json_log() {
  local level="$1" event="$2" detail="${3:-}"
  printf '{"utc":"%s","level":"%s","event":"%s","detail":"%s"}\n' \
    "$(date -u +%FT%TZ)" "$level" "$event" \
    "$(printf '%s' "$detail" | tr '\n' ' ' | sed 's/\\/\\\\/g; s/"/\\"/g')" | tee -a "$LOG"
}
fail() {
  json_log error failure "$1"
  exit "${2:-1}"
}
trap 'rc=$?; json_log error unexpected_exit "line=$LINENO rc=$rc"; exit "$rc"' ERR

json_log info data_fetch_started "checking latest investment_data release"
fetch_rc=0
"$ROOT/fetch_latest_data.sh" >>"$RAW_LOG" 2>&1 || fetch_rc=$?
if [ "$fetch_rc" -eq 10 ]; then
  fetch_release="$("$PY" "$ROOT/pipeline_helpers.py" read "$ROOT/state/data-fetch-state.json" release_tag)"
  json_log warning data_asset_pending \
    "release=$fetch_release qlib_bin.tar.gz not ready; continuing with active provider and Sina"
elif [ "$fetch_rc" -ne 0 ]; then
  if [ -s "$ROOT/data/calendars/day.txt" ] && [ -r "$ARCHIVE" ]; then
    json_log warning data_fetch_failed \
      "rc=$fetch_rc active_provider_retained=true continuing_with_sina=true"
  else
    fail "data_fetch_failed rc=$fetch_rc and no usable local baseline" 76
  fi
fi
fetch_status="$("$PY" "$ROOT/pipeline_helpers.py" read "$ROOT/state/data-fetch-state.json" status)"
fetch_release="$("$PY" "$ROOT/pipeline_helpers.py" read "$ROOT/state/data-fetch-state.json" release_tag)"
json_log info data_fetch_complete "status=$fetch_status release=$fetch_release"

[ -r "$ARCHIVE" ] || fail "archive_not_readable:$ARCHIVE" 66
[ -x "$PY" ] || fail "venv_python_missing:$PY" 69
free_kb="$(df -Pk "$ROOT" | awk 'NR==2 {print $4}')"
[ "$free_kb" -ge "$MIN_FREE_KB" ] || fail "disk_free_kb=$free_kb minimum=$MIN_FREE_KB" 70

sha="$(sha256sum "$ARCHIVE" | awk '{print $1}')"
mtime="$(stat -c '%y' "$ARCHIVE")"
old_sha="$("$PY" "$ROOT/pipeline_helpers.py" read "$STATE" archive_sha256)"
old_model_data_sha="$("$PY" "$ROOT/pipeline_helpers.py" read "$STATE" model_data_archive_sha256)"
old_latest="$("$PY" "$ROOT/pipeline_helpers.py" read "$STATE" latest_data_date)"
archive_changed=false
[ "$sha" = "$old_sha" ] || archive_changed=true
json_log info archive_checked \
  "sha256=$sha archive_changed=$archive_changed state_latest=$old_latest free_kb=$free_kb"

if $archive_changed; then
  tar -tzf "$ARCHIVE" >/dev/null || fail archive_integrity_failed 65
  stage="$ROOT/data.stage.$RUN_ID"
  rm -rf "$stage"
  mkdir -p "$stage"
  tar -xzf "$ARCHIVE" --strip-components=1 -C "$stage"
  [ -s "$stage/calendars/day.txt" ] || fail staged_calendar_missing 65
  [ -s "$stage/instruments/csi300.txt" ] || fail staged_csi300_missing 65
  latest="$(tail -n 1 "$stage/calendars/day.txt" | tr -d '\r')"
  backup="$ROOT/data.previous.$RUN_ID"
  if [ -d "$ROOT/data" ]; then
    mv "$ROOT/data" "$backup"
  fi
  mv "$stage" "$ROOT/data"
  json_log info data_activated "latest=$latest backup=$backup"
else
  [ -s "$ROOT/data/calendars/day.txt" ] || fail active_calendar_missing 65
  latest="$(tail -n 1 "$ROOT/data/calendars/day.txt" | tr -d '\r')"
fi

# GitHub remains the baseline.  After activating it (if newer), ask Sina for
# every exchange date beyond the active calendar.  Exit 10 means no new date.
sina_changed=false
sina_stage="$ROOT/data.sina-stage.$RUN_ID"
sina_report="$ROOT/evidence/sina-auto-$RUN_ID.json"
sina_rc=0
json_log info sina_probe_started "provider_latest=$latest direct_no_proxy=true"
env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy \
  NO_PROXY='*' no_proxy='*' \
  "$PY" "$ROOT/sina_incremental_update.py" --auto \
    --provider "$ROOT/data" --stage "$sina_stage" --report "$sina_report" \
    >>"$RAW_LOG" 2>&1 || sina_rc=$?
if [ "$sina_rc" -eq 0 ]; then
  [ -s "$sina_stage/calendars/day.txt" ] || fail sina_stage_calendar_missing 77
  sina_latest="$(tail -n 1 "$sina_stage/calendars/day.txt" | tr -d '\r')"
  [[ "$sina_latest" > "$latest" ]] || fail "sina_stage_not_newer current=$latest staged=$sina_latest" 77
  sina_backup="$ROOT/data.previous.sina.$RUN_ID"
  mv "$ROOT/data" "$sina_backup"
  mv "$sina_stage" "$ROOT/data"
  latest="$sina_latest"
  sina_changed=true
  json_log info sina_data_activated "latest=$latest backup=$sina_backup report=$sina_report"
elif [ "$sina_rc" -eq 10 ]; then
  json_log info sina_no_update "latest=$latest report=$sina_report"
else
  [ ! -e "$sina_stage" ] || rm -rf "$sina_stage"
  json_log warning sina_update_failed "rc=$sina_rc active_data_retained=true report=$sina_report"
  fail "sina_update_incomplete rc=$sina_rc; training_and_prediction_blocked=true report=$sina_report" 77
fi

local_changed=false
[ "$latest" = "$old_latest" ] || local_changed=true
changed=false
if $archive_changed || $sina_changed || $local_changed; then
  changed=true
fi
json_log info data_change_resolved \
  "archive_changed=$archive_changed sina_changed=$sina_changed local_changed=$local_changed latest=$latest state_latest=$old_latest changed=$changed"
"$PY" "$ROOT/pipeline_helpers.py" set-predict-date "$CONFIG" "$latest"

audit_rc=0
"$PY" "$ROOT/verify_models.py" \
  --provider "$ROOT/data" --mlruns "$ROOT/mlruns" --prefix REPRO_ \
  --output-dir "$ROOT/evidence/final-audit" >>"$RAW_LOG" 2>&1 || audit_rc=$?
read -r model_train_end model_test_end < <("$PY" -c \
  'import pandas as p,sys; d=p.read_csv(sys.argv[1]); v=d[d.valid.astype(str).str.lower().eq("true")]; print(v["train_end"].max(), v["test_end"].max()) if len(v) else print("", "")' \
  "$ROOT/evidence/final-audit/model_metrics.csv" 2>/dev/null || true)

train_required=false
if [ "$audit_rc" -ne 0 ]; then
  train_required=true
elif $sina_changed; then
  # A newly activated Sina provider must be paired with models trained from
  # that exact, fully verified snapshot.  A model carrying the same end date
  # may have been produced before the provider passed its completeness gate.
  train_required=true
elif $changed && [[ "$latest" > "$model_test_end" ]]; then
  train_required=true
fi
json_log info training_gate \
  "audit_rc=$audit_rc data_latest=$latest model_train_end=$model_train_end model_test_end=$model_test_end train_required=$train_required"

if $train_required; then
  training_baseline_key="$({
    printf '%s\n%s\n' "$sha" "$latest"
    [ ! -s "$ROOT/data/sina_incremental.json" ] || sha256sum "$ROOT/data/sina_incremental.json"
    sha256sum "$ROOT/model_provenance.py" "$ROOT/verify_models.py" "$ROOT/train_all.sh" "$ROOT/qlibAssistant/roll/"*.py
  } | sha256sum | awk '{print $1}')"
  failed_baseline="$(cat "$TRAIN_FAILURE_MARKER" 2>/dev/null || true)"
  if [ "$failed_baseline" = "$training_baseline_key" ]; then
    fail "training_circuit_open baseline=$training_baseline_key; use reproctl.sh retry after diagnosis" 78
  fi
  rm -f "$ROOT/status/"*.status
  json_log warning resume_scan "training required; all algorithms rescanned, existing train windows will be skipped"
  if ! "$ROOT/train_all.sh" >>"$RAW_LOG" 2>&1; then
    printf '%s\n' "$training_baseline_key" >"$TRAIN_FAILURE_MARKER"
    fail training_failed 71
  fi
  if ! "$PY" "$ROOT/verify_models.py" \
    --provider "$ROOT/data" --mlruns "$ROOT/mlruns" --prefix REPRO_ \
    --output-dir "$ROOT/evidence/final-audit" >>"$RAW_LOG" 2>&1; then
    printf '%s\n' "$training_baseline_key" >"$TRAIN_FAILURE_MARKER"
    fail final_model_audit_failed 72
  fi
  rm -f "$TRAIN_FAILURE_MARKER"
  read -r model_train_end model_test_end < <("$PY" -c \
    'import pandas as p,sys; d=p.read_csv(sys.argv[1]); v=d[d.valid.astype(str).str.lower().eq("true")]; print(v["train_end"].max(), v["test_end"].max())' \
    "$ROOT/evidence/final-audit/model_metrics.csv")
  model_data_sha="$sha"
  if [ -s "$ROOT/data/sina_incremental.json" ]; then
    sina_manifest_sha="$(sha256sum "$ROOT/data/sina_incremental.json" | awk '{print $1}')"
    model_data_sha="${sha}+sina:${sina_manifest_sha}"
  fi
else
  model_data_sha="$old_model_data_sha"
  [ -n "$model_data_sha" ] || model_data_sha="$old_sha"
  json_log info training_skipped \
    "model cohort retained; current archive may still refresh prediction and selection"
fi

old_selection="$("$PY" "$ROOT/pipeline_helpers.py" read "$STATE" selection_dir)"
selection_dir="$old_selection"
if $changed || [ ! -f "$old_selection/total.csv" ]; then
  (
    cd "$ROOT/qlibAssistant/roll"
    "$PY" ./roll.py --config_path="$CONFIG" model selection
  ) >>"$RAW_LOG" 2>&1 || fail selection_failed 73
  selection_dir="$(find "$ROOT/analysis" -maxdepth 1 -type d -name 'selection_*' | sort | tail -n 1)"
  [ -s "$selection_dir/total.csv" ] || fail selection_total_missing 74
  [ -s "$selection_dir/${latest}_ret.csv" ] || fail selection_ret_missing 74
  [ -s "$selection_dir/${latest}_filter_ret.csv" ] || fail selection_filter_missing 74
  json_log info selection_complete "$selection_dir"
else
  json_log info selection_skipped "existing_result=$selection_dir"
fi

"$PY" "$ROOT/pipeline_helpers.py" write-state "$STATE" \
  --sha256 "$sha" --mtime "$mtime" --latest-date "$latest" \
  --model-train-end "$model_train_end" --model-test-end "$model_test_end" \
  --model-data-sha256 "$model_data_sha" --selection-dir "$selection_dir"
ln -sfn "$selection_dir" "$ROOT/analysis/latest"
"$PY" "$ROOT/build_result_evidence.py" >>"$RAW_LOG" 2>&1 ||
  fail report_evidence_failed 75
"$PY" "$ROOT/build_review_stats.py" >>"$RAW_LOG" 2>&1 ||
  fail review_evidence_failed 75
json_log info report_complete "$ROOT/evidence/results"
json_log info pipeline_success "state=$STATE results=$selection_dir"
