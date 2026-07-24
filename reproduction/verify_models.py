#!/usr/bin/env python3
"""Audit the from-scratch qlibAssistant training matrix.

Exit 0 only when each expected model class has exactly one valid recorder for
each of the five generated training windows.  The CSV/JSON outputs are also
the authoritative metric inventory used by the final report.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import qlib
from dateutil.relativedelta import relativedelta
from qlib.config import C
from qlib.constant import REG_CN
from qlib.workflow import R

from model_provenance import build_provenance


EXPECTED_MODELS = {
    "XGBModel": "XGBoost",
    "LinearModel": "Linear",
    "DEnsembleModel": "DoubleEnsemble",
    "LGBModel": "LightGBM",
    "CatBoostModel": "CatBoost",
}
REQUIRED_ARTIFACTS = {"params.pkl", "sig_analysis", "task", "training_provenance", "training_provenance.json"}


def expected_segments(provider: Path) -> dict[str, dict[str, tuple[str, str]]]:
    """Return the five 9:2:1 windows anchored to the active data calendar."""
    calendar = provider / "calendars" / "day.txt"
    dates = [line.strip() for line in calendar.read_text().splitlines() if line.strip()]
    if not dates:
        raise ValueError(f"empty calendar: {calendar}")
    t3 = datetime.strptime(dates[-1], "%Y-%m-%d")
    result = {}
    for years in range(1, 6):
        test_months = years
        valid_months = 2 * years
        train_months = 9 * years
        t2 = t3 - relativedelta(months=test_months)
        t1 = t2 - relativedelta(months=valid_months)
        t0 = t1 - relativedelta(months=train_months)
        segments = {
            "train": (t0.strftime("%Y-%m-%d"), (t1 - timedelta(days=1)).strftime("%Y-%m-%d")),
            "valid": (t1.strftime("%Y-%m-%d"), (t2 - timedelta(days=1)).strftime("%Y-%m-%d")),
            "test": (t2.strftime("%Y-%m-%d"), t3.strftime("%Y-%m-%d")),
        }
        result[segments["train"][0]] = segments
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--provider", required=True)
    parser.add_argument("--mlruns", required=True)
    parser.add_argument("--prefix", default="REPRO_")
    parser.add_argument("--output-dir", required=True)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    expected = expected_segments(Path(args.provider))
    expected_train_starts = set(expected)
    exp_manager = C["exp_manager"]
    exp_manager["kwargs"]["uri"] = "file:" + str(Path(args.mlruns).resolve())
    qlib.init(provider_uri=args.provider, region=REG_CN, exp_manager=exp_manager)

    rows: list[dict] = []
    problems: list[str] = []
    seen_pairs: Counter[tuple[str, str]] = Counter()
    matching_experiments = 0
    total_recorders = 0

    for exp_name in sorted(R.list_experiments()):
        if not exp_name.startswith(args.prefix):
            continue
        matching_experiments += 1
        exp = R.get_exp(experiment_name=exp_name)
        for rid in exp.list_recorders():
            total_recorders += 1
            rec = exp.get_recorder(recorder_id=rid)
            # Old cohorts are intentionally retained in MLflow.  They are not
            # part of the current audit and must not make a daily roll fail.
            try:
                candidate_task = rec.load_object("task")
                candidate_segments = candidate_task["dataset"]["kwargs"]["segments"]
                candidate_model = candidate_task["model"]["class"]
                candidate_train_start = str(candidate_segments["train"][0])
                normalized_segments = {
                    key: tuple(str(value) for value in candidate_segments[key])
                    for key in ("train", "valid", "test")
                }
                if (
                    candidate_model not in EXPECTED_MODELS
                    or candidate_train_start not in expected
                    or normalized_segments != expected[candidate_train_start]
                ):
                    continue
                candidate_provenance = rec.load_object("training_provenance")
                expected_provenance = build_provenance(candidate_task, args.provider)
                if candidate_provenance.get("recorder_fingerprint_sha256") != expected_provenance["recorder_fingerprint_sha256"]:
                    continue
            except Exception:
                continue
            row = {
                "experiment": exp_name,
                "recorder_id": rid,
                "status": rec.status,
                "model_class": "",
                "algorithm": "",
                "train_start": "",
                "train_end": "",
                "valid_start": "",
                "valid_end": "",
                "test_start": "",
                "test_end": "",
                "training_data_sha256": "",
                "github_archive_sha256": "",
                "sina_manifest_sha256": "",
                "feature_config_sha256": "",
                "model_config_sha256": "",
                "code_sha256": "",
                "recorder_fingerprint_sha256": "",
                "training_started_utc": "",
                "training_finished_utc": "",
                "IC": "",
                "ICIR": "",
                "Rank IC": "",
                "Rank ICIR": "",
                "prediction_rows": "",
                "prediction_nan": "",
                "valid": False,
                "error": "",
            }
            try:
                artifacts = set(rec.list_artifacts())
                missing = REQUIRED_ARTIFACTS - artifacts
                if missing:
                    raise ValueError(f"missing artifacts: {sorted(missing)}")
                task = rec.load_object("task")
                model = rec.load_object("params.pkl")
                if model is None:
                    raise ValueError("params.pkl deserialized to None")
                model_class = task["model"]["class"]
                segments = task["dataset"]["kwargs"]["segments"]
                provenance = rec.load_object("training_provenance")
                expected_provenance = build_provenance(task, args.provider)
                if provenance.get("recorder_fingerprint_sha256") != expected_provenance["recorder_fingerprint_sha256"]:
                    raise ValueError("recorder provenance fingerprint mismatch")
                row.update(
                    model_class=model_class,
                    algorithm=EXPECTED_MODELS.get(model_class, "UNKNOWN"),
                    train_start=str(segments["train"][0]),
                    train_end=str(segments["train"][1]),
                    valid_start=str(segments["valid"][0]),
                    valid_end=str(segments["valid"][1]),
                    test_start=str(segments["test"][0]),
                    test_end=str(segments["test"][1]),
                    training_data_sha256=provenance["training_data_sha256"],
                    github_archive_sha256=provenance["github_archive_sha256"],
                    sina_manifest_sha256=provenance["sina_manifest_sha256"],
                    feature_config_sha256=provenance["feature_config_sha256"],
                    model_config_sha256=provenance["model_config_sha256"],
                    code_sha256=provenance["code_sha256"],
                    recorder_fingerprint_sha256=provenance["recorder_fingerprint_sha256"],
                    training_started_utc=provenance["training_started_utc"],
                    training_finished_utc=provenance["training_finished_utc"],
                )
                ic = rec.load_object("sig_analysis/ic.pkl")
                ric = rec.load_object("sig_analysis/ric.pkl")
                pred = rec.load_object("pred.pkl")
                if len(ic) == 0 or len(ric) == 0 or len(pred) == 0:
                    raise ValueError("empty prediction or signal analysis")
                metrics = {
                    "IC": float(ic.mean()),
                    "ICIR": float(ic.mean() / ic.std()),
                    "Rank IC": float(ric.mean()),
                    "Rank ICIR": float(ric.mean() / ric.std()),
                }
                if not all(math.isfinite(v) for v in metrics.values()):
                    raise ValueError(f"non-finite metric: {metrics}")
                row.update(metrics)
                row["prediction_rows"] = int(len(pred))
                row["prediction_nan"] = int(np.asarray(pred.isna()).sum())
                if row["prediction_nan"] >= row["prediction_rows"]:
                    raise ValueError("prediction is entirely NaN")
                if model_class not in EXPECTED_MODELS:
                    raise ValueError(f"unexpected model class {model_class}")
                if row["train_start"] not in expected_train_starts:
                    raise ValueError(f"unexpected train start {row['train_start']}")
                seen_pairs[(model_class, row["train_start"])] += 1
                row["valid"] = True
            except Exception as exc:
                row["error"] = f"{type(exc).__name__}: {exc}"
                problems.append(f"{exp_name}/{rid}: {row['error']}")
            rows.append(row)

    expected_pairs = {
        (model_class, train_start)
        for model_class in EXPECTED_MODELS
        for train_start in expected_train_starts
    }
    for pair in sorted(expected_pairs):
        count = seen_pairs[pair]
        if count != 1:
            problems.append(f"expected exactly one valid recorder for {pair}, found {count}")
    for pair, count in seen_pairs.items():
        if pair not in expected_pairs:
            problems.append(f"unexpected valid recorder pair {pair} count={count}")

    fieldnames = list(rows[0]) if rows else [
        "experiment", "recorder_id", "valid", "error"
    ]
    with (output_dir / "model_metrics.csv").open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    summary = {
        "matching_experiments": matching_experiments,
        "total_recorders": total_recorders,
        "current_cohort_recorders": len(rows),
        "data_end": next(iter(expected.values()))["test"][1],
        "valid_recorders": sum(bool(row["valid"]) for row in rows),
        "expected_recorders": 25,
        "problems": problems,
        "success": not problems and len(rows) == 25,
    }
    (output_dir / "model_audit.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
