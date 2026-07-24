#!/usr/bin/env python3
"""Rebuild selection rankings with scale-neutral, shrunk model weights.

Qlib model scores are not calibrated to a common scale.  Averaging their raw
values gives high-variance models more influence than their declared weight.
This post-processor ranks every model cross-sectionally per prediction day,
filters weak recorders using the configured metric thresholds, and combines
the ranks with weights shrunk halfway toward equal weight.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
import yaml


METRIC_NAMES = {
    "ic": "IC",
    "icir": "ICIR",
    "rankic": "Rank IC",
    "rankicir": "Rank ICIR",
}
FILTER_COLUMNS = ("STD5", "STD20", "STD60", "ROC10", "ROC20", "ROC60")


def configured_thresholds(config: dict) -> dict[str, float]:
    thresholds = {}
    for item in config.get("rec_filter") or []:
        if len(item) != 1:
            raise ValueError(f"invalid recorder filter: {item!r}")
        key, value = next(iter(item.items()))
        normalized = str(key).lower().replace(" ", "")
        if normalized not in METRIC_NAMES:
            raise ValueError(f"unknown recorder metric: {key}")
        thresholds[METRIC_NAMES[normalized]] = float(value)
    return thresholds


def eligible_models(
    total: pd.DataFrame, metrics: pd.DataFrame, thresholds: dict[str, float]
) -> pd.DataFrame:
    available = total[["exp_name", "rid"]].drop_duplicates()
    joined = available.merge(
        metrics,
        left_on=["exp_name", "rid"],
        right_on=["experiment", "recorder_id"],
        how="left",
        validate="one_to_one",
    )
    missing = joined["Rank IC"].isna()
    if missing.any():
        missing_ids = joined.loc[missing, "rid"].tolist()
        raise ValueError(f"metrics missing for recorders: {missing_ids}")
    mask = pd.Series(True, index=joined.index)
    for metric, threshold in thresholds.items():
        mask &= pd.to_numeric(joined[metric], errors="coerce") > threshold
    selected = joined.loc[mask].copy()
    if selected.empty:
        raise ValueError("recorder thresholds rejected every available model")
    return selected


def shrunk_rank_ic_weights(
    selected: pd.DataFrame, shrinkage: float = 0.5
) -> pd.Series:
    """Blend positive Rank IC weights with equal weight to reduce estimation risk."""
    if not 0 <= shrinkage <= 1:
        raise ValueError("shrinkage must be between zero and one")
    signal = pd.to_numeric(selected.set_index("rid")["Rank IC"], errors="raise").clip(
        lower=0
    )
    equal = pd.Series(1.0 / len(signal), index=signal.index)
    metric_weight = signal / signal.sum() if signal.sum() > 0 else equal
    weights = shrinkage * metric_weight + (1 - shrinkage) * equal
    return weights / weights.sum()


def rank_normalized_scores(total: pd.DataFrame, weights: pd.Series) -> pd.DataFrame:
    frame = total[total["rid"].isin(weights.index)].copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"])
    frame["normalized_score"] = (
        frame.groupby(["datetime", "rid"], sort=False)["score"]
        .rank(method="average", pct=True)
        .sub(0.5)
    )
    frame["weight"] = frame["rid"].map(weights)
    aggregate = (
        frame.groupby(["datetime", "instrument"], sort=False)
        .apply(
            lambda group: pd.Series(
                {
                    "avg_score": np.average(
                        group["normalized_score"], weights=group["weight"]
                    ),
                    "raw_avg_score": np.average(
                        group["score"], weights=group["weight"]
                    ),
                    "pos_ratio": float((group["score"] > 0).mean()),
                }
            ),
            include_groups=False,
        )
        .reset_index()
    )
    return frame, aggregate


def robust_filter(frame: pd.DataFrame) -> pd.DataFrame:
    missing = sorted(set(FILTER_COLUMNS) - set(frame.columns))
    if missing:
        raise ValueError(f"selection file misses filter columns: {missing}")
    mask = (
        (frame["STD5"] < 0.06)
        & (frame["STD20"] < 0.10)
        & (frame["STD60"] < 0.05)
        & (frame["STD5"] < frame["STD60"] * 2)
        & (frame["ROC10"] > 0.80)
        & (frame["ROC20"] > 0.80)
        & (frame["ROC60"] > 0.80)
        & (frame["ROC20"] < 1.30)
    )
    return frame.loc[mask].copy()


def atomic_csv(frame: pd.DataFrame, path: Path) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    frame.to_csv(temporary, index=False, encoding="utf-8-sig")
    os.replace(temporary, path)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--selection-dir", type=Path, required=True)
    parser.add_argument("--metrics", type=Path, required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--shrinkage", type=float, default=0.5)
    args = parser.parse_args()

    total_path = args.selection_dir / "total.csv"
    total = pd.read_csv(total_path)
    total = total.loc[:, ~total.columns.str.startswith("Unnamed:")]
    metrics = pd.read_csv(args.metrics)
    config = yaml.safe_load(args.config.read_text(encoding="utf-8")) or {}
    thresholds = configured_thresholds(config)
    selected = eligible_models(total, metrics, thresholds)
    weights = shrunk_rank_ic_weights(selected, args.shrinkage)
    normalized_total, aggregate = rank_normalized_scores(total, weights)

    dates = sorted(str(day.date()) for day in aggregate["datetime"].unique())
    for date in dates:
        ret_path = args.selection_dir / f"{date}_ret.csv"
        if not ret_path.is_file():
            raise FileNotFoundError(ret_path)
        existing = pd.read_csv(ret_path)
        existing = existing.loc[:, ~existing.columns.str.startswith("Unnamed:")]
        existing = existing.drop(
            columns=["avg_score", "raw_avg_score", "pos_ratio"], errors="ignore"
        )
        scores = aggregate[aggregate["datetime"] == pd.Timestamp(date)].drop(
            columns="datetime"
        )
        rebuilt = scores.merge(
            existing, on="instrument", how="left", validate="one_to_one"
        ).sort_values("avg_score", ascending=False)
        atomic_csv(rebuilt, ret_path)
        atomic_csv(
            robust_filter(rebuilt).reset_index(drop=True),
            args.selection_dir / f"{date}_filter_ret.csv",
        )

    atomic_csv(normalized_total, total_path)
    metadata = {
        "method": "cross_sectional_percentile_rank",
        "score_range": [-0.5, 0.5],
        "weight_metric": "Rank IC",
        "weight_shrinkage_to_metric": args.shrinkage,
        "weight_shrinkage_to_equal": 1 - args.shrinkage,
        "thresholds": thresholds,
        "selected_model_count": len(weights),
        "weights": {rid: float(weight) for rid, weight in weights.items()},
        "dates": dates,
    }
    (args.selection_dir / "ensemble_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(metadata, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
