#!/usr/bin/env python3
"""Small state/config helpers for the qlib reproduction pipeline."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import yaml


def main() -> None:
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)

    read = sub.add_parser("read")
    read.add_argument("state")
    read.add_argument("key")

    config = sub.add_parser("set-predict-date")
    config.add_argument("config")
    config.add_argument("date")

    state = sub.add_parser("write-state")
    state.add_argument("state")
    state.add_argument("--sha256", required=True)
    state.add_argument("--mtime", required=True)
    state.add_argument("--latest-date", required=True)
    state.add_argument("--model-train-end", required=True)
    state.add_argument("--model-test-end", required=True)
    state.add_argument("--model-data-sha256", required=True)
    state.add_argument("--selection-dir", required=True)

    release = sub.add_parser("release-asset")
    release.add_argument("metadata")
    release.add_argument("asset_name")

    fetch = sub.add_parser("write-fetch-state")
    fetch.add_argument("state")
    fetch.add_argument("--summary", default="")
    fetch.add_argument("--status", required=True)
    fetch.add_argument("--message", default="")
    fetch.add_argument("--release-tag", default="")
    fetch.add_argument("--published-at", default="")
    fetch.add_argument("--asset-id", default="")
    fetch.add_argument("--asset-url", default="")
    fetch.add_argument("--remote-sha256", default="")
    fetch.add_argument("--local-sha256", default="")
    fetch.add_argument("--asset-size", type=int, default=0)
    fetch.add_argument("--downloaded-bytes", type=int, default=0)

    args = parser.parse_args()
    if args.command == "read":
        path = Path(args.state)
        if not path.exists():
            return
        value = json.loads(path.read_text(encoding="utf-8")).get(args.key, "")
        print(value if value is not None else "")
    elif args.command == "set-predict-date":
        path = Path(args.config)
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data["predict_dates"] = [{"start": args.date, "end": args.date}]
        path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )
    elif args.command == "release-asset":
        data = json.loads(Path(args.metadata).read_text(encoding="utf-8"))
        asset = next(
            (item for item in data.get("assets", []) if item.get("name") == args.asset_name),
            {},
        )
        digest = asset.get("digest") or ""
        if digest.startswith("sha256:"):
            digest = digest.removeprefix("sha256:")
        values = (
            data.get("tag_name", ""),
            data.get("published_at", ""),
            str(asset.get("id", "")),
            str(asset.get("size", 0)),
            digest,
            asset.get("browser_download_url", ""),
        )
        print("\t".join(values))
    elif args.command == "write-fetch-state":
        payload = {
            "status": args.status,
            "message": args.message,
            "release_tag": args.release_tag,
            "published_at": args.published_at,
            "asset_id": args.asset_id,
            "asset_url": args.asset_url,
            "remote_sha256": args.remote_sha256,
            "local_sha256": args.local_sha256,
            "asset_size": args.asset_size,
            "downloaded_bytes": args.downloaded_bytes,
            "progress_percent": round(
                args.downloaded_bytes * 100 / args.asset_size, 2
            )
            if args.asset_size
            else 0.0,
            "updated_utc": datetime.now(timezone.utc).isoformat(),
        }
        path = Path(args.state)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        if args.summary:
            summary_path = Path(args.summary)
            if summary_path.exists():
                summary = json.loads(summary_path.read_text(encoding="utf-8"))
                summary["data_fetch"] = payload
                summary_tmp = summary_path.with_suffix(".tmp")
                summary_tmp.write_text(
                    json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                summary_tmp.replace(summary_path)
    else:
        payload = {
            "archive_sha256": args.sha256,
            "archive_mtime": args.mtime,
            "latest_data_date": args.latest_date,
            "model_train_end": args.model_train_end,
            "model_test_end": args.model_test_end,
            "model_data_archive_sha256": args.model_data_sha256,
            "selection_dir": args.selection_dir,
            "last_success_utc": datetime.now(timezone.utc).isoformat(),
            "success": True,
        }
        path = Path(args.state)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)


if __name__ == "__main__":
    main()
