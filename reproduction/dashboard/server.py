#!/usr/bin/env python3
"""Local read-only dashboard for qlib reproduction results."""
from __future__ import annotations

import csv
import base64
import hashlib
import hmac
import json
import mimetypes
import os
import re
import subprocess
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from dateutil.relativedelta import relativedelta

ROOT = Path("/data0/zhangpeng6/qlib/reproduction")
STATIC = Path(__file__).resolve().parent / "static"
RESULTS = ROOT / "evidence/results"
AUTH_REALM = "Qlib Dashboard"
TRAIN_COMMAND_MARKER = str(ROOT / "qlibAssistant/roll/roll.py")
TRAIN_MODELS = ("XGBoost", "Linear", "DoubleEnsemble", "LightGBM", "CatBoost")


def password_matches(password: str, encoded_hash: str) -> bool:
    """Verify a pbkdf2_sha256$iterations$salt$hex_digest password hash."""
    try:
        algorithm, iterations, salt, expected = encoded_hash.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt.encode(), int(iterations)
        ).hex()
        return hmac.compare_digest(actual, expected)
    except (TypeError, ValueError):
        return False


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


def live_training_resources():
    """Return live qlib training parents with resources summed over their workers."""
    output = subprocess.run(
        ["ps", "-e", "-o", "pid,ppid,etimes,pcpu,pmem,args"],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    ).stdout
    processes = {}
    for line in output.splitlines():
        parts = line.strip().split(None, 5)
        if len(parts) != 6:
            continue
        pid, ppid, elapsed, cpu, memory, command = parts
        try:
            processes[int(pid)] = {
                "pid": int(pid),
                "ppid": int(ppid),
                "elapsed_seconds": int(elapsed),
                "cpu_percent": float(cpu),
                "memory_percent": float(memory),
                "command": command,
            }
        except ValueError:
            continue

    children = {}
    for process in processes.values():
        children.setdefault(process["ppid"], []).append(process["pid"])

    roots = [
        process
        for process in processes.values()
        if TRAIN_COMMAND_MARKER in process["command"]
        and re.search(r"(?:^|\s)train(?:\s|$)", process["command"])
    ]
    result = []
    for root in roots:
        descendant_ids = []
        pending = list(children.get(root["pid"], []))
        while pending:
            child_pid = pending.pop()
            descendant_ids.append(child_pid)
            pending.extend(children.get(child_pid, []))
        family = [root] + [
            processes[pid] for pid in descendant_ids if pid in processes
        ]
        model_match = re.search(
            r"--model_name(?:=|\s+)([^\s]+)", root["command"]
        )
        result.append(
            {
                "pid": root["pid"],
                "model": model_match.group(1) if model_match else "未知模型",
                "elapsed_seconds": root["elapsed_seconds"],
                "cpu_percent": round(sum(item["cpu_percent"] for item in family), 1),
                "memory_percent": round(
                    sum(item["memory_percent"] for item in family), 1
                ),
                "worker_count": len(descendant_ids),
                "command": root["command"],
            }
        )
    return {item["model"]: item for item in result}


def parse_status(path: Path):
    values = {}
    if not path.exists():
        return values
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        key, separator, value = line.partition("=")
        if separator:
            values[key] = value
    return values


def latest_training_detail(log_path: Path):
    if not log_path.exists():
        return {"task_current": 0, "task_total": 5, "detail": "等待开始"}
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-500:]
    task_matches = [
        re.search(r"Training task (\d+)/(\d+)", line) for line in lines
    ]
    task_matches = [match for match in task_matches if match]
    sub_matches = [
        re.search(r"Training sub-model: \((\d+)/(\d+)\)", line) for line in lines
    ]
    sub_matches = [match for match in sub_matches if match]
    useful = [
        line.strip()
        for line in lines
        if any(
            marker in line
            for marker in (
                "Training task",
                "Training sub-model",
                "Loading data Done",
                "Init data Done",
                "Feature selection",
                "Sample re-weighting",
                "训练完成",
                "退出代码",
            )
        )
    ]
    task_current, task_total = (
        (int(task_matches[-1].group(1)), int(task_matches[-1].group(2)))
        if task_matches
        else (0, 5)
    )
    detail = useful[-1] if useful else "日志已创建，等待训练输出"
    detail = re.sub(r"^.*? - ", "", detail)
    if sub_matches:
        detail += f" · 子模型 {sub_matches[-1].group(1)}/{sub_matches[-1].group(2)}"
    return {
        "task_current": task_current,
        "task_total": task_total,
        "detail": detail,
    }


def training_progress():
    """Build the five-model training report primarily from status and log files."""
    resources = live_training_resources()
    rows = []
    for model in TRAIN_MODELS:
        status = parse_status(ROOT / f"status/{model}.status")
        log_path = ROOT / f"logs/training/{model}.log"
        log = latest_training_detail(log_path)
        resource = resources.get(model)
        exit_status = status.get("exit_status")
        if resource:
            state = "running"
        elif exit_status == "0":
            state = "completed"
        elif exit_status is not None:
            state = "failed"
        elif status:
            state = "interrupted"
        else:
            state = "pending"
        if state == "completed":
            log["task_current"] = log["task_total"]
            log["detail"] = "全部训练任务已完成"
        elif state == "failed":
            log["detail"] = f"训练失败，退出代码 {exit_status}"
        elif state == "pending":
            log["task_current"] = 0
            log["detail"] = "等待本轮训练"
        rows.append(
            {
                "model": model,
                "state": state,
                "started_utc": status.get("started_utc"),
                "finished_utc": status.get("finished_utc"),
                "log_updated_utc": (
                    datetime.utcfromtimestamp(log_path.stat().st_mtime)
                    .replace(microsecond=0)
                    .isoformat()
                    + "Z"
                    if log_path.exists()
                    else None
                ),
                **log,
                "pid": resource["pid"] if resource else None,
                "elapsed_seconds": resource["elapsed_seconds"] if resource else None,
                "cpu_percent": resource["cpu_percent"] if resource else None,
                "memory_percent": resource["memory_percent"] if resource else None,
                "worker_count": resource["worker_count"] if resource else None,
            }
        )
    return {
        "active_count": sum(row["state"] == "running" for row in rows),
        "completed_count": sum(row["state"] == "completed" for row in rows),
        "failed_count": sum(
            row["state"] in {"failed", "interrupted"} for row in rows
        ),
        "models": rows,
        "sampled_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
    }


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
        "training": training_progress(),
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

    def authenticated(self) -> bool:
        username = os.environ["DASHBOARD_USERNAME"]
        password_hash = os.environ["DASHBOARD_PASSWORD_HASH"]
        header = self.headers.get("Authorization", "")
        if not header.startswith("Basic "):
            return False
        try:
            supplied = base64.b64decode(header[6:], validate=True).decode("utf-8")
            supplied_username, supplied_password = supplied.split(":", 1)
        except (ValueError, UnicodeDecodeError):
            return False
        return hmac.compare_digest(supplied_username, username) and password_matches(
            supplied_password, password_hash
        )

    def request_authentication(self):
        body = b"Authentication required"
        self.send_response(401)
        self.send_header("WWW-Authenticate", f'Basic realm="{AUTH_REALM}", charset="UTF-8"')
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if not self.authenticated():
            self.request_authentication()
            return

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
    missing = [
        name
        for name in ("DASHBOARD_USERNAME", "DASHBOARD_PASSWORD_HASH")
        if not os.environ.get(name)
    ]
    if missing:
        parser.error(f"missing required environment variables: {', '.join(missing)}")
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Qlib dashboard: http://{args.host}:{args.port}", flush=True)
    server.serve_forever()
