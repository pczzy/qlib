#!/usr/bin/env python3
"""Build historical prediction review statistics for the local dashboard."""
from __future__ import annotations

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd
import qlib
from qlib.constant import REG_CN
from qlib.data import D

ROOT = Path("/data0/zhangpeng6/qlib/reproduction")
ANALYSIS = ROOT / "analysis"
OUTPUT = ROOT / "evidence/results/review_stats.json"
TOP_NS = (10, 20, 30, 50)
KINDS = {"filtered": "filter_ret", "unfiltered": "ret"}
LABEL_EXPR = "Ref($close, -2)/Ref($close, -1) - 1"


def selection_snapshots() -> list[dict]:
    """Return one immutable selection snapshot per prediction date."""
    by_date: dict[str, dict] = {}
    for directory in sorted(ANALYSIS.glob("selection_*")):
        match = re.fullmatch(
            r"selection_(\d{8})_(\d{2})_(\d{2})_(\d{2})", directory.name
        )
        if not match:
            continue
        for ret_file in directory.glob("????-??-??_ret.csv"):
            date = ret_file.name[:10]
            filter_file = directory / f"{date}_filter_ret.csv"
            if not filter_file.is_file():
                continue
            # Keep the first successful snapshot, so a later rerun cannot rewrite
            # the historical prediction used for review.
            by_date.setdefault(
                date,
                {
                    "date": date,
                    "selection": directory.name,
                    "ret": ret_file,
                    "filter_ret": filter_file,
                },
            )
    return [by_date[date] for date in sorted(by_date)]


def safe_float(value):
    return None if pd.isna(value) or not np.isfinite(value) else float(value)


def main() -> None:
    snapshots = selection_snapshots()
    qlib.init(provider_uri=str(ROOT / "data"), region=REG_CN)
    calendar = [str(pd.Timestamp(x).date()) for x in D.calendar(freq="day")]
    calendar_index = {date: index for index, date in enumerate(calendar)}

    instruments = sorted(
        {
            instrument
            for snapshot in snapshots
            for kind in KINDS.values()
            for instrument in pd.read_csv(snapshot[kind], usecols=["instrument"])[
                "instrument"
            ].dropna()
        }
    )
    dates = [snapshot["date"] for snapshot in snapshots]
    labels = pd.DataFrame()
    benchmark = pd.DataFrame()
    if instruments and dates:
        labels = D.features(
            instruments,
            [LABEL_EXPR],
            start_time=min(dates),
            end_time=max(dates),
            freq="day",
        )
        labels.columns = ["real_label"]
        benchmark = D.features(
            ["SH000300"],
            [LABEL_EXPR],
            start_time=min(dates),
            end_time=max(dates),
            freq="day",
        )
        benchmark.columns = ["benchmark_return"]

    label_map = labels["real_label"].to_dict() if not labels.empty else {}
    benchmark_map = benchmark["benchmark_return"].to_dict() if not benchmark.empty else {}
    rows = []
    for snapshot in snapshots:
        date = snapshot["date"]
        idx = calendar_index.get(date)
        outcome_date = calendar[idx + 2] if idx is not None and idx + 2 < len(calendar) else None
        for kind, file_key in KINDS.items():
            frame = pd.read_csv(snapshot[file_key])
            frame = frame[frame["avg_score"] > 0].sort_values(
                "avg_score", ascending=False
            )
            frame["real_label"] = frame["instrument"].map(
                lambda instrument: label_map.get((instrument, pd.Timestamp(date)))
            )
            for top_n in TOP_NS:
                picked = frame.head(top_n)
                realized = picked["real_label"].dropna()
                average = realized.mean() if len(realized) else np.nan
                stock_win_rate = (realized > 0).mean() if len(realized) else np.nan
                benchmark_return = benchmark_map.get(("SH000300", pd.Timestamp(date)), np.nan)
                rows.append(
                    {
                        "date": date,
                        "outcome_date": outcome_date,
                        "status": "realized" if len(realized) else "pending",
                        "kind": kind,
                        "top_n": top_n,
                        "selection": snapshot["selection"],
                        "selected_count": int(len(picked)),
                        "realized_count": int(len(realized)),
                        "stock_win_rate": safe_float(stock_win_rate),
                        "average_return": safe_float(average),
                        "benchmark_return": safe_float(benchmark_return),
                        "excess_return": safe_float(average - benchmark_return),
                    }
                )

    details = pd.DataFrame(rows)
    aggregates = []
    if not details.empty:
        for (kind, top_n), group in details.groupby(["kind", "top_n"], sort=True):
            realized = group[group["status"] == "realized"]
            paired = realized.dropna(
                subset=["benchmark_return", "excess_return"]
            )
            aggregates.append(
                {
                    "kind": kind,
                    "top_n": int(top_n),
                    "prediction_days": int(len(group)),
                    "realized_days": int(len(realized)),
                    "pending_days": int((group["status"] == "pending").sum()),
                    "benchmark_realized_days": int(len(paired)),
                    "stock_win_rate": safe_float(
                        np.average(
                            realized["stock_win_rate"],
                            weights=realized["realized_count"],
                        )
                        if len(realized) and realized["realized_count"].sum()
                        else np.nan
                    ),
                    "day_win_rate": safe_float(
                        (realized["average_return"] > 0).mean()
                        if len(realized)
                        else np.nan
                    ),
                    "average_return": safe_float(realized["average_return"].mean()),
                    "average_benchmark_return": safe_float(
                        paired["benchmark_return"].mean()
                        if len(paired) == len(realized)
                        else np.nan
                    ),
                    "average_excess_return": safe_float(
                        paired["excess_return"].mean()
                        if len(paired) == len(realized)
                        else np.nan
                    ),
                    "cumulative_return": safe_float(
                        (1 + realized["average_return"]).prod() - 1
                        if len(realized)
                        else np.nan
                    ),
                }
            )

    payload = {
        "generated_at": pd.Timestamp.now(tz="UTC").isoformat(),
        "label_definition": "预测日后第1个交易日收盘至第2个交易日收盘的收益率",
        "available_prediction_dates": dates,
        "aggregates": aggregates,
        "daily": rows,
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({"output": str(OUTPUT), "rows": len(rows)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
