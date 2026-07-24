import sys
from pathlib import Path

import pandas as pd
import pytest


ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from robust_ensemble import (
    configured_thresholds,
    eligible_models,
    rank_normalized_scores,
    shrunk_rank_ic_weights,
)


def test_thresholds_and_shrunk_weights():
    config = {
        "rec_filter": [
            {"ic": 0.001},
            {"rankicir": 0.1},
        ]
    }
    total = pd.DataFrame(
        {
            "exp_name": ["exp", "exp"],
            "rid": ["strong", "weak"],
        }
    )
    metrics = pd.DataFrame(
        {
            "experiment": ["exp", "exp"],
            "recorder_id": ["strong", "weak"],
            "IC": [0.02, 0.02],
            "ICIR": [0.2, 0.2],
            "Rank IC": [0.03, 0.01],
            "Rank ICIR": [0.2, 0.08],
        }
    )
    selected = eligible_models(total, metrics, configured_thresholds(config))
    assert selected["rid"].tolist() == ["strong"]
    weights = shrunk_rank_ic_weights(selected)
    assert weights.to_dict() == {"strong": 1.0}


def test_rank_normalization_removes_score_scale():
    total = pd.DataFrame(
        {
            "datetime": ["2026-01-01"] * 6,
            "instrument": ["A", "B", "C"] * 2,
            "rid": ["small"] * 3 + ["large"] * 3,
            "score": [0.01, 0.02, 0.03, 100, 200, 300],
        }
    )
    weights = pd.Series({"small": 0.5, "large": 0.5})
    normalized, aggregate = rank_normalized_scores(total, weights)
    assert normalized.groupby("rid")["normalized_score"].max().to_dict() == {
        "large": pytest.approx(0.5),
        "small": pytest.approx(0.5),
    }
    scores = aggregate.set_index("instrument")["avg_score"]
    assert scores["A"] < scores["B"] < scores["C"]
