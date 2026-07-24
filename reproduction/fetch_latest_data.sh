#!/usr/bin/env bash
set -Eeuo pipefail

ROOT=/data0/zhangpeng6/qlib/reproduction
DEST=/data0/zhangpeng6/rsync/qlib_bin.tar.gz
STATE="$ROOT/state/data-fetch-state.json"
LOCK="$ROOT/data-fetch.lock"
PY="$ROOT/venv/bin/python"
API=https://api.github.com/repos/chenditc/investment_data/releases/latest
PROXY=http://10.81.97.61:3128
META=
META_CHECK=
PART=

mkdir -p "$ROOT/state" "$(dirname "$DEST")"
exec 8>"$LOCK"
if ! flock -n 8; then
  echo "data fetch already running" >&2
  exit 75
fi

record() {
  "$PY" "$ROOT/pipeline_helpers.py" write-fetch-state "$STATE" \
    --summary "$ROOT/evidence/results/summary.json" \
    --status "$1" --message "${2:-}" \
    --release-tag "${release_tag:-}" --published-at "${published_at:-}" \
    --asset-id "${asset_id:-}" --asset-url "${asset_url:-}" \
    --remote-sha256 "${remote_sha:-}" --local-sha256 "${local_sha:-}" \
    --asset-size "${asset_size:-0}" --downloaded-bytes "${downloaded_bytes:-0}"
}
cleanup() {
  [ -z "${META:-}" ] || rm -f "$META"
  [ -z "${META_CHECK:-}" ] || rm -f "$META_CHECK"
}
failed() {
  rc=$?
  downloaded_bytes=0
  [ -z "${PART:-}" ] || downloaded_bytes="$(stat -c %s "$PART" 2>/dev/null || echo 0)"
  record failed "line=$1 rc=$rc"
  cleanup
  exit "$rc"
}
trap 'failed $LINENO' ERR
trap cleanup EXIT

release_tag=
published_at=
asset_id=
asset_url=
remote_sha=
local_sha=
asset_size=0
downloaded_bytes=0
record checking "querying latest GitHub release"

META="$(mktemp "$ROOT/state/latest-release.XXXXXX.json")"
# Proxy variables exist only in these curl processes, never in training.
env http_proxy="$PROXY" https_proxy="$PROXY" HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY" \
  curl --fail --silent --show-error --location \
    --retry 3 --retry-all-errors --retry-delay 5 \
    --connect-timeout 15 --max-time 120 \
    -H 'Accept: application/vnd.github+json' \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    "$API" -o "$META"

IFS=$'\t' read -r release_tag published_at asset_id asset_size remote_sha asset_url < <(
  "$PY" "$ROOT/pipeline_helpers.py" release-asset "$META" qlib_bin.tar.gz
)
if [ -z "$asset_url" ]; then
  record waiting_for_asset "release exists; qlib_bin.tar.gz is still being published"
  exit 10
fi
if [ -z "$remote_sha" ]; then
  record waiting_for_asset "asset exists; GitHub sha256 digest is not ready"
  exit 10
fi

if [ -r "$DEST" ]; then
  local_sha="$(sha256sum "$DEST" | awk '{print $1}')"
fi
if [ "$local_sha" = "$remote_sha" ]; then
  downloaded_bytes="$(stat -c %s "$DEST")"
  record already_downloaded "remote sha256 matches verified local archive"
  exit 0
fi

PART="$DEST.part.$asset_id"
downloaded_bytes="$(stat -c %s "$PART" 2>/dev/null || echo 0)"
record downloading "new release found; download started"
env http_proxy="$PROXY" https_proxy="$PROXY" HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY" \
  curl --fail --show-error --location \
    --retry 5 --retry-all-errors --retry-delay 10 \
    --connect-timeout 20 --max-time 7200 \
    --continue-at - "$asset_url" -o "$PART"
downloaded_bytes="$(stat -c %s "$PART")"
[ "$downloaded_bytes" -eq "$asset_size" ]
local_sha="$(sha256sum "$PART" | awk '{print $1}')"
[ "$local_sha" = "$remote_sha" ]
tar -tzf "$PART" >/dev/null

# A newer release may appear during a large download. Re-check before activation
# so training never starts from an asset that is no longer the latest one.
META_CHECK="$(mktemp "$ROOT/state/latest-release-recheck.XXXXXX.json")"
env http_proxy="$PROXY" https_proxy="$PROXY" HTTP_PROXY="$PROXY" HTTPS_PROXY="$PROXY" \
  curl --fail --silent --show-error --location \
    --retry 3 --retry-all-errors --retry-delay 5 \
    --connect-timeout 15 --max-time 120 \
    -H 'Accept: application/vnd.github+json' \
    -H 'X-GitHub-Api-Version: 2022-11-28' \
    "$API" -o "$META_CHECK"
newest_asset_id="$("$PY" "$ROOT/pipeline_helpers.py" release-asset \
  "$META_CHECK" qlib_bin.tar.gz | cut -f3)"
if [ "$newest_asset_id" != "$asset_id" ]; then
  record superseded "newer release appeared during download; retrying latest asset"
  exit 75
fi

chmod 0644 "$PART"
mv -f "$PART" "$DEST"
PART=
record ready "download verified and atomically activated"
