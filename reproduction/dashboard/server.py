#!/usr/bin/env python3
"""Local read-only dashboard for qlib reproduction results."""
from __future__ import annotations

import csv
import json
import mimetypes
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from dateutil.relativedelta import relativedelta

ROOT = Path("/data0/zhangpeng6/qlib/reproduction")
STATIC = Path(__file__).resolve().parent / "static"
RESULTS = ROOT / "evidence/results"


def read_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def read_csv(path: Path):
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def latest_events():
    logs = sorted((ROOT / "logs/pipeline").glob("*.jsonl"))
    if not logs:
        return []
    events = []
    for line in logs[-1].read_text(encoding="utf-8").splitlines()[-40:]:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events


def live_data_status():
    """Read freshness and expected training windows from live source files."""
    fetch = read_json(ROOT / "state/data-fetch-state.json")
    calendar = ROOT / "data/calendars/day.txt"
    dates = [line.strip() for line in calendar.read_text().splitlines() if line.strip()]
    if not dates:
        raise ValueError(f"empty trading calendar: {calendar}")
    data_end = dates[-1]
    manifest_path = ROOT / "data/sina_incremental.json"
    if manifest_path.exists():
        manifest = read_json(manifest_path)
        github_end = manifest.get("base_latest_date", data_end)
        sina_dates = sorted(
            {
                day
                for day in manifest.get("appended_dates", [])
                if day in dates and day > github_end
            }
        )
    else:
        github_end = data_end
        sina_dates = []
    github_days = sum(day <= github_end for day in dates)
    data_sources = {
        "mode": "github+sina" if sina_dates else "github",
        "github": {
            "start": dates[0],
            "end": github_end,
            "trading_days": github_days,
            "release_tag": fetch.get("release_tag", ""),
        },
        "sina": {
            "dates": sina_dates,
            "start": sina_dates[0] if sina_dates else None,
            "end": sina_dates[-1] if sina_dates else None,
            "trading_days": len(sina_dates),
            "automatic": True,
            "direct_no_proxy": True,
        },
        "calendar_end": data_end,
        "total_trading_days": len(dates),
    }
    t3 = datetime.strptime(data_end, "%Y-%m-%d")
    windows = []
    for years in range(1, 6):
        t2 = t3 - relativedelta(months=years)
        t1 = t2 - relativedelta(months=2 * years)
        t0 = t1 - relativedelta(months=9 * years)
        windows.append(
            {
                "horizon_months": 12 * years,
                "train": [t0.strftime("%Y-%m-%d"), (t1 - timedelta(days=1)).strftime("%Y-%m-%d")],
                "valid": [t1.strftime("%Y-%m-%d"), (t2 - timedelta(days=1)).strftime("%Y-%m-%d")],
                "test": [t2.strftime("%Y-%m-%d"), data_end],
            }
        )
    return {
        "release_tag": fetch.get("release_tag", ""),
        "release_published_at": fetch.get("published_at", ""),
        "release_asset_url": fetch.get("asset_url", ""),
        "fetch_status": fetch.get("status", ""),
        "fetch_updated_utc": fetch.get("updated_utc", ""),
        "calendar_end": data_end,
        "data_sources": data_sources,
        "training_windows": windows,
    }


def dashboard_payload():
    return {
        "summary": read_json(RESULTS / "summary.json"),
        "pipeline": read_json(ROOT / "state/pipeline-state.json"),
        "audit": read_json(ROOT / "evidence/final-audit/model_audit.json"),
        "selected": read_csv(RESULTS / "selected_models_and_weights.csv"),
        "metrics": read_csv(RESULTS / "all_25_model_metrics.csv"),
        "filtered": read_csv(RESULTS / "top10_filtered.csv"),
        "unfiltered": read_csv(RESULTS / "top10_unfiltered.csv"),
        "review": read_json(RESULTS / "review_stats.json"),
        "events": latest_events(),
        "live_data": live_data_status(),
    }


class Handler(BaseHTTPRequestHandler):
    def send_bytes(self, body: bytes, content_type: str, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/api/dashboard":
            try:
                body = json.dumps(
                    dashboard_payload(), ensure_ascii=False, allow_nan=False
                ).encode("utf-8")
                self.send_bytes(body, "application/json; charset=utf-8")
            except Exception as exc:
                body = json.dumps({"error": str(exc)}, ensure_ascii=False).encode()
                self.send_bytes(body, "application/json; charset=utf-8", 500)
            return

        relative = "index.html" if path == "/" else path.lstrip("/")
        target = (STATIC / relative).resolve()
        if STATIC not in target.parents or not target.is_file():
            self.send_bytes(b"Not found", "text/plain; charset=utf-8", 404)
            return
        content_type = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if content_type.startswith("text/") or content_type in {
            "application/javascript",
            "application/json",
        }:
            content_type += "; charset=utf-8"
        self.send_bytes(target.read_bytes(), content_type)

    def log_message(self, fmt, *args):
        print(f"{self.address_string()} - {fmt % args}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Qlib dashboard: http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
