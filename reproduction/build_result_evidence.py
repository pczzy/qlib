#!/usr/bin/env python3
"""Create compact, reproducible evidence tables from selection outputs."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

ROOT = Path("/data0/zhangpeng6/qlib/reproduction")
state = json.loads((ROOT / "state/pipeline-state.json").read_text(encoding="utf-8"))
selection = Path(state["selection_dir"])
out = ROOT / "evidence/results"
out.mkdir(parents=True, exist_ok=True)

metrics = pd.read_csv(ROOT / "evidence/final-audit/model_metrics.csv")
metrics.to_csv(out / "all_25_model_metrics.csv", index=False)

total = pd.read_csv(selection / "total.csv")
selected = total[["exp_name", "rid", "weight"]].drop_duplicates()
selected = selected.merge(
    metrics[
        [
            "experiment",
            "recorder_id",
            "algorithm",
            "train_start",
            "train_end",
            "IC",
            "ICIR",
            "Rank IC",
            "Rank ICIR",
        ]
    ],
    left_on=["exp_name", "rid"],
    right_on=["experiment", "recorder_id"],
    how="left",
    validate="one_to_one",
).drop(columns=["experiment", "recorder_id"])
selected.to_csv(out / "selected_models_and_weights.csv", index=False)

latest = state["latest_data_date"]
columns = [
    "instrument",
    "code",
    "name",
    "avg_score",
    "pos_ratio",
    "STD5",
    "STD20",
    "STD60",
    "ROC10",
    "ROC20",
    "ROC60",
    "real_label",
]
unfiltered = pd.read_csv(selection / f"{latest}_ret.csv")
filtered = pd.read_csv(selection / f"{latest}_filter_ret.csv")
unfiltered[columns].head(10).to_csv(out / "top10_unfiltered.csv", index=False)
filtered[columns].head(10).to_csv(out / "top10_filtered.csv", index=False)

summary = {
    "selection_dir": str(selection),
    "prediction_date": latest,
    "current_data_archive_sha256": state["archive_sha256"],
    "model_data_archive_sha256": state["model_data_archive_sha256"],
    "model_train_end": state["model_train_end"],
    "model_test_end": state["model_test_end"],
    "model_uses_current_archive": (
        state["model_data_archive_sha256"] == state["archive_sha256"]
    ),
    "selected_model_count": len(selected),
    "selected_weight_sum": float(selected["weight"].sum()),
    "unfiltered_rows": len(unfiltered),
    "filtered_rows": len(filtered),
    "real_label_non_null": int(unfiltered["real_label"].notna().sum()),
    "data_fetch": json.loads(
        (ROOT / "state/data-fetch-state.json").read_text(encoding="utf-8")
    ),
    "files": {p.name: str(p) for p in sorted(out.iterdir())},
}
(out / "summary.json").write_text(
    json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
)
print(json.dumps(summary, ensure_ascii=False, indent=2))
