#!/usr/bin/env python3
"""Validate and append Sina daily bars to an existing Qlib binary provider.

Sina requests deliberately ignore proxy environment variables.  The updater first
replays a date already present in Qlib, then builds and verifies a staging copy.
It never edits the active provider in place.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import math
import os
import shutil
import statistics
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import requests
from akshare.stock.cons import hk_js_decode
from py_mini_racer import MiniRacer


FIELDS = ("open", "high", "low", "close", "volume", "amount", "vwap", "factor", "adjclose", "change")
PRICE_FIELDS = ("open", "high", "low", "close")
SINA_HIST = "https://finance.sina.com.cn/realstock/company/{symbol}/hisdata_klc2/klc_kl.js"
SINA_HFQ = "https://finance.sina.com.cn/realstock/company/{symbol}/hfq.js"


@dataclass
class SinaStock:
    bars: dict[str, dict[str, float]]
    hfq_events: list[tuple[str, float]]

    def hfq_at(self, day: str) -> float:
        for event_day, value in sorted(self.hfq_events, reverse=True):
            if day >= event_day:
                return value
        return 1.0


def session() -> requests.Session:
    client = requests.Session()
    client.trust_env = False
    client.headers.update({"User-Agent": "Mozilla/5.0", "Referer": "https://finance.sina.com.cn/"})
    return client


def fetch_stock(symbol: str) -> SinaStock:
    client = session()
    hist = client.get(SINA_HIST.format(symbol=symbol), timeout=20)
    hist.raise_for_status()
    decoder = MiniRacer()
    decoder.eval(hk_js_decode)
    encoded = hist.text.split("=", 1)[1].split(";", 1)[0].replace('"', "")
    decoded = decoder.call("d", encoded)
    bars: dict[str, dict[str, float]] = {}
    for row in decoded:
        day = str(row["date"])[:10]
        if all(key in row for key in ("open", "high", "low", "close", "volume", "amount")):
            bars[day] = {key: float(row[key]) for key in ("open", "high", "low", "close", "volume", "amount")}

    hfq_events: list[tuple[str, float]] = [("1900-01-01", 1.0)]
    factor = client.get(SINA_HFQ.format(symbol=symbol), timeout=20)
    factor.raise_for_status()
    object_start = factor.text.find("{", factor.text.find("=") + 1)
    if object_start >= 0:
        payload, _ = json.JSONDecoder().raw_decode(factor.text[object_start:])
        parsed = [(str(item["d"]), float(item["f"])) for item in payload.get("data", [])]
        if parsed:
            hfq_events = parsed
    return SinaStock(bars=bars, hfq_events=hfq_events)


def current_constituents(provider: Path, day: str) -> list[str]:
    result = set()
    for line in (provider / "instruments/csi300.txt").read_text().splitlines():
        symbol, start, end = line.split("\t")
        if start <= day <= end:
            result.add(symbol.lower())
    if len(result) != 300:
        raise RuntimeError(f"expected 300 CSI300 constituents at {day}, got {len(result)}")
    return sorted(result)


def read_value(provider: Path, symbol: str, field: str, day: str, calendar: list[str]) -> float:
    path = provider / "features" / symbol / f"{field}.day.bin"
    values = np.fromfile(path, dtype="<f4")
    start = int(values[0])
    index = calendar.index(day) - start + 1
    if index < 1 or index >= len(values):
        return math.nan
    return float(values[index])


def generated_values(stock: SinaStock, bar_day: str, calibration_day: str, qlib: dict[str, float]) -> dict[str, float]:
    bar = stock.bars[bar_day]
    calibration_bar = stock.bars[calibration_day]
    hfq_ratio = stock.hfq_at(bar_day) / stock.hfq_at(calibration_day)
    price_factor = qlib["factor"] * hfq_ratio
    volume_scale = qlib["volume"] / calibration_bar["volume"]
    hfq_calibrated_scale = qlib["adjclose"] / (calibration_bar["close"] * stock.hfq_at(calibration_day))
    raw_vwap = bar["amount"] / bar["volume"]
    return {
        "open": bar["open"] * price_factor,
        "high": bar["high"] * price_factor,
        "low": bar["low"] * price_factor,
        "close": bar["close"] * price_factor,
        "volume": bar["volume"] * volume_scale / hfq_ratio,
        "amount": bar["amount"] / 1000.0,
        "vwap": raw_vwap * price_factor,
        "factor": price_factor,
        "adjclose": bar["close"] * stock.hfq_at(bar_day) * hfq_calibrated_scale,
        "change": bar["close"] / calibration_bar["close"] - 1.0,
    }


def relative_error(actual: float, expected: float) -> float:
    if math.isnan(actual) and math.isnan(expected):
        return 0.0
    if math.isnan(actual) or math.isnan(expected):
        return math.inf
    return abs(actual - expected) / max(abs(actual), 1e-12)


def fetch_all(symbols: list[str], workers: int) -> tuple[dict[str, SinaStock], dict[str, str]]:
    stocks: dict[str, SinaStock] = {}
    errors: dict[str, str] = {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {pool.submit(fetch_stock, symbol): symbol for symbol in symbols}
        for future in concurrent.futures.as_completed(futures):
            symbol = futures[future]
            try:
                stocks[symbol] = future.result()
            except Exception as exc:  # network/parser failures are summarized together
                errors[symbol] = f"{type(exc).__name__}: {exc}"
    return stocks, errors


def discover_dates(symbols: list[str], local_latest: str, workers: int) -> tuple[list[str], dict]:
    """Discover exchange dates by consensus, so one suspended stock cannot hide a session."""
    probes = symbols[:5]
    stocks, errors = fetch_all(probes, min(workers, len(probes)))
    counts: dict[str, int] = {}
    latest_by_symbol = {}
    for symbol, stock in stocks.items():
        dates = sorted(day for day in stock.bars if day > local_latest)
        latest_by_symbol[symbol] = max(stock.bars) if stock.bars else None
        for day in dates:
            counts[day] = counts.get(day, 0) + 1
    # At least three of five liquid index constituents must report the session.
    discovered = sorted(day for day, count in counts.items() if count >= 3)
    detail = {
        "probe_symbols": probes,
        "probe_errors": errors,
        "latest_by_symbol": latest_by_symbol,
        "date_votes": counts,
    }
    if len(stocks) < 3:
        raise RuntimeError(f"Sina date discovery has only {len(stocks)}/5 successful probes")
    return discovered, detail


def dynamic_replay_dates(provider: Path, calendar: list[str]) -> tuple[str, str]:
    replay_day = calendar[-1]
    manifest = provider / "sina_incremental.json"
    if manifest.exists():
        payload = json.loads(manifest.read_text())
        base_day = payload.get("base_latest_date")
        if base_day in calendar:
            replay_day = base_day
    replay_index = calendar.index(replay_day)
    if replay_index < 1:
        raise RuntimeError(f"no calibration day before replay day {replay_day}")
    return replay_day, calendar[replay_index - 1]


def validate(provider: Path, stocks: dict[str, SinaStock], symbols: list[str], replay_day: str, calibration_day: str) -> dict:
    calendar = (provider / "calendars/day.txt").read_text().splitlines()
    field_errors: dict[str, list[float]] = {field: [] for field in FIELDS}
    field_details: dict[str, list[tuple[float, str]]] = {field: [] for field in FIELDS}
    missing = []
    compared = 0
    corporate_actions = []
    for symbol in symbols:
        stock = stocks.get(symbol)
        if not stock or replay_day not in stock.bars or calibration_day not in stock.bars:
            # Suspensions are valid only when Qlib also has no close on the replay date.
            actual_close = read_value(provider, symbol, "close", replay_day, calendar)
            if not math.isnan(actual_close):
                missing.append(symbol)
            continue
        qlib_cal = {field: read_value(provider, symbol, field, calibration_day, calendar) for field in FIELDS}
        generated = generated_values(stock, replay_day, calibration_day, qlib_cal)
        if stock.hfq_at(replay_day) != stock.hfq_at(calibration_day):
            corporate_actions.append(symbol)
        for field in FIELDS:
            actual = read_value(provider, symbol, field, replay_day, calendar)
            error = relative_error(actual, generated[field])
            field_errors[field].append(error)
            field_details[field].append((error, symbol))
        compared += 1

    stats = {}
    for field, values in field_errors.items():
        finite = sorted(value for value in values if math.isfinite(value))
        stats[field] = {
            "count": len(values),
            "median_rel_error": statistics.median(finite) if finite else None,
            "p95_rel_error": finite[int(0.95 * (len(finite) - 1))] if finite else None,
            "max_rel_error": max(finite) if finite else None,
            "non_finite": len(values) - len(finite),
            "worst_symbols": [
                {"symbol": symbol.upper(), "relative_error": error}
                for error, symbol in sorted(field_details[field], reverse=True)[:5]
            ],
        }
    # These tolerances permit small vendor rounding differences, but reject a
    # mismatched symbol, unit, adjustment regime, or stale daily bar.
    limits = {
        "open": 0.003,
        "high": 0.003,
        "low": 0.003,
        "close": 0.003,
        "volume": 0.01,
        "amount": 0.003,
        "vwap": 0.003,
        "factor": 0.003,
        "adjclose": 0.005,
        "change": 0.03,
    }
    passed = not missing and compared >= 290
    for field, limit in limits.items():
        passed = passed and stats[field]["non_finite"] == 0 and (stats[field]["p95_rel_error"] or 0) <= limit
    return {
        "passed": passed,
        "replay_day": replay_day,
        "calibration_day": calibration_day,
        "universe": len(symbols),
        "compared": compared,
        "missing_non_suspended": missing,
        "corporate_actions": corporate_actions,
        "limits": limits,
        "fields": stats,
    }


def append_bin(path: Path, new_calendar_indexes: list[int], values: list[float]) -> None:
    old = np.fromfile(path, dtype="<f4")
    start = int(old[0])
    old_last = start + len(old) - 2
    if new_calendar_indexes[0] != old_last + 1:
        raise RuntimeError(f"non-contiguous append for {path}: old_last={old_last}, new={new_calendar_indexes[0]}")
    combined = np.concatenate([old, np.asarray(values, dtype="<f4")])
    temp = path.with_suffix(path.suffix + ".tmp")
    combined.tofile(temp)
    os.replace(temp, path)


def atomic_write_text(path: Path, content: str) -> None:
    """Replace a text file without modifying a hard-linked active-provider inode."""
    temp = path.with_name(path.name + ".tmp")
    temp.write_text(content)
    os.replace(temp, path)


def extend_csi300(stage: Path, old_last: str, new_last: str) -> int:
    path = stage / "instruments/csi300.txt"
    output = []
    changed = 0
    for line in path.read_text().splitlines():
        symbol, start, end = line.split("\t")
        if start <= old_last <= end and end < new_last:
            end = new_last
            changed += 1
        output.append("\t".join((symbol, start, end)))
    atomic_write_text(path, "\n".join(output) + "\n")
    if changed != 300:
        raise RuntimeError(f"expected to extend 300 CSI300 intervals, extended {changed}")
    return changed


def build_stage(provider: Path, stage: Path, stocks: dict[str, SinaStock], symbols: list[str], dates: list[str]) -> dict:
    if stage.exists():
        raise FileExistsError(stage)
    # Untouched files are hard-linked.  Every changed feature is written to a
    # temporary file and replaced, so the active provider is never mutated.
    shutil.copytree(provider, stage, copy_function=os.link)
    calendar_path = stage / "calendars/day.txt"
    calendar = calendar_path.read_text().splitlines()
    old_last = calendar[-1]
    base_latest = old_last
    all_appended_dates = list(dates)
    old_manifest = provider / "sina_incremental.json"
    if old_manifest.exists():
        old_payload = json.loads(old_manifest.read_text())
        base_latest = old_payload.get("base_latest_date", base_latest)
        all_appended_dates = list(old_payload.get("appended_dates", [])) + list(dates)
    if dates[0] <= old_last or dates != sorted(dates):
        raise RuntimeError(f"invalid append dates {dates}; provider ends at {old_last}")
    new_indexes = list(range(len(calendar), len(calendar) + len(dates)))
    calibration_day = old_last
    missing = []
    action_symbols = []

    for symbol in symbols:
        stock = stocks[symbol]
        if calibration_day not in stock.bars:
            raise RuntimeError(f"calibration bar missing for {symbol} on {calibration_day}")
        qlib_cal = {field: read_value(provider, symbol, field, calibration_day, calendar) for field in FIELDS}
        generated_by_day = {}
        previous_day = calibration_day
        previous_close = stock.bars[calibration_day]["close"]
        for target_day in dates:
            if target_day not in stock.bars:
                generated_by_day[target_day] = {field: math.nan for field in FIELDS}
                missing.append((symbol, target_day))
                continue
            values = generated_values(stock, target_day, calibration_day, qlib_cal)
            # change must use the immediately preceding available trading bar.
            values["change"] = stock.bars[target_day]["close"] / previous_close - 1.0
            generated_by_day[target_day] = values
            previous_day = target_day
            previous_close = stock.bars[target_day]["close"]
        if any(stock.hfq_at(day) != stock.hfq_at(calibration_day) for day in dates):
            action_symbols.append(symbol)
        for field in FIELDS:
            append_bin(stage / "features" / symbol / f"{field}.day.bin", new_indexes, [generated_by_day[d][field] for d in dates])

    atomic_write_text(calendar_path, "\n".join(calendar + dates) + "\n")
    extended = extend_csi300(stage, old_last, dates[-1])
    atomic_write_text(
        stage / "sina_incremental.json",
        json.dumps(
            {
                "source": "sina-direct-no-proxy",
                "base_latest_date": base_latest,
                "appended_dates": all_appended_dates,
                "symbols": len(symbols),
                "fields": list(FIELDS),
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
    )
    return {"symbols": len(symbols), "dates": dates, "missing_bars": missing, "corporate_actions": action_symbols, "extended_intervals": extended}


def verify_stage(stage: Path, symbols: list[str], dates: list[str]) -> dict:
    import qlib
    from qlib.data import D

    qlib.init(provider_uri=str(stage), region="cn")
    frame = D.features(
        [symbol.upper() for symbol in symbols],
        [f"${field}" for field in FIELDS],
        start_time=dates[0],
        end_time=dates[-1],
        freq="day",
    )
    expected = len(symbols) * len(dates)
    finite_close = int(frame["$close"].notna().sum())
    finite_rows = int(frame.notna().all(axis=1).sum())
    active_last = D.list_instruments(D.instruments("csi300"), start_time=dates[-1], end_time=dates[-1], as_list=True)
    return {
        "rows": len(frame),
        "expected_rows": expected,
        "finite_close": finite_close,
        "fully_finite_rows": finite_rows,
        "csi300_last_day": len(active_last),
        "passed": len(frame) == expected and finite_close >= expected - 5 and finite_rows >= expected - 5 and len(active_last) == 300,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", type=Path, default=Path(__file__).resolve().parent / "data")
    parser.add_argument("--replay-day", default="2026-07-17")
    parser.add_argument("--calibration-day", default="2026-07-16")
    parser.add_argument("--append", nargs="*", default=[])
    parser.add_argument("--auto", action="store_true", help="discover all Sina dates newer than the provider")
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--workers", type=int, default=12)
    parser.add_argument("--report", type=Path)
    args = parser.parse_args()

    started = time.monotonic()
    calendar = (args.provider / "calendars/day.txt").read_text().splitlines()
    symbols = current_constituents(args.provider, calendar[-1])
    discovery = None
    if args.auto:
        args.append, discovery = discover_dates(symbols, calendar[-1], args.workers)
        if not args.append:
            report = {
                "source": "sina-direct-no-proxy",
                "provider": str(args.provider),
                "status": "no_update",
                "local_latest": calendar[-1],
                "discovery": discovery,
                "passed": True,
                "elapsed_seconds": round(time.monotonic() - started, 3),
            }
            rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
            print(rendered)
            if args.report:
                args.report.parent.mkdir(parents=True, exist_ok=True)
                args.report.write_text(rendered + "\n")
            return 10
        args.replay_day, args.calibration_day = dynamic_replay_dates(args.provider, calendar)
    stocks, fetch_errors = fetch_all(symbols, args.workers)
    report = {
        "source": "sina-direct-no-proxy",
        "provider": str(args.provider),
        "fetch": {"requested": len(symbols), "succeeded": len(stocks), "errors": fetch_errors},
    }
    if discovery is not None:
        report["discovery"] = discovery
    if fetch_errors:
        report["passed"] = False
    else:
        report["validation"] = validate(args.provider, stocks, symbols, args.replay_day, args.calibration_day)
        report["passed"] = report["validation"]["passed"]
        if args.append:
            if not report["passed"]:
                report["update"] = {"skipped": "replay validation failed"}
            else:
                if args.stage is None:
                    parser.error("--stage is required with --append")
                report["update"] = build_stage(args.provider, args.stage, stocks, symbols, args.append)
                report["stage_verification"] = verify_stage(args.stage, symbols, args.append)
                report["passed"] = report["stage_verification"]["passed"]
    report["elapsed_seconds"] = round(time.monotonic() - started, 3)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    print(rendered)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(rendered + "\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
