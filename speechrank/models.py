"""Seven protocol-defined, train-only-fit Poisson error-density candidates."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import Any

from .features import FEATURE_NAMES, FEATURE_VERSION

RANDOM_STATE = 20261017


def get_candidates() -> list[dict[str, Any]]:
    candidates = [
        {"candidate_id": f"poisson-alpha-{alpha:g}", "kind": "poisson", "params": {"alpha": alpha, "max_iter": 1000, "tol": 1e-4}}
        for alpha in (0.1, 1.0, 10.0)
    ]
    candidates.extend({
        "candidate_id": f"hgb-leaves-{leaves}-l2-{penalty}", "kind": "hgb_poisson",
        "params": {"loss": "poisson", "max_iter": 100, "learning_rate": 0.05,
                   "early_stopping": False, "min_samples_leaf": 20,
                   "max_leaf_nodes": leaves, "l2_regularization": penalty, "random_state": RANDOM_STATE},
    } for leaves in (3, 7) for penalty in (1, 10))
    return candidates


def _candidate(candidate: Mapping[str, Any] | str) -> dict[str, Any]:
    choices = get_candidates()
    if isinstance(candidate, str):
        matches = [value for value in choices if value["candidate_id"] == candidate]
    else:
        matches = [value for value in choices if value == candidate]
    if len(matches) != 1:
        raise ValueError("candidate is not in the frozen seven-candidate grid")
    return matches[0]


def build_estimator(candidate: Mapping[str, Any] | str):
    """Construct an unfitted pipeline; import optional sklearn only on demand."""
    from sklearn.ensemble import HistGradientBoostingRegressor
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import PoissonRegressor
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler

    specification = _candidate(candidate)
    model = (PoissonRegressor(**specification["params"]) if specification["kind"] == "poisson"
             else HistGradientBoostingRegressor(**specification["params"]))
    return Pipeline([
        ("imputer", SimpleImputer(strategy="median", add_indicator=True, keep_empty_features=True)),
        ("scale", StandardScaler()), ("model", model),
    ])


def _matrix(feature_rows: Sequence[Mapping[str, Any]], arm: str) -> list[list[float]]:
    if arm not in FEATURE_NAMES:
        raise ValueError("unknown feature arm")
    names = FEATURE_NAMES[arm]
    output = []
    for row in feature_rows:
        if set(row) != set(names):
            raise ValueError("feature row does not exactly match the selected allowlist")
        vector = []
        for name in names:
            value = row[name]
            if value is None:
                vector.append(float("nan"))
            elif isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("features must be finite numbers or None")
            else:
                vector.append(float(value))
        output.append(vector)
    return output


def fit_model(feature_rows: Sequence[Mapping[str, Any]], errors: Sequence[int], durations_s: Sequence[float],
              candidate: Mapping[str, Any] | str, arm: str = "legacy"):
    """Fit errors/second using duration sample weights, on training rows only.

    Gold-derived counts enter solely through this explicit training API. Fitted
    preprocessing and estimator are returned as one artifact; predict never fits.
    """
    if not feature_rows or not (len(feature_rows) == len(errors) == len(durations_s)):
        raise ValueError("training feature/count/duration arrays must be nonempty and aligned")
    if any(isinstance(value, bool) or not isinstance(value, int) or value < 0 for value in errors):
        raise ValueError("training errors must be nonnegative integers")
    if not sum(errors):
        raise ValueError("Poisson training requires at least one observed error")
    if any(isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value <= 0 for value in durations_s):
        raise ValueError("training durations must be positive finite numbers")
    matrix = _matrix(feature_rows, arm)
    estimator = build_estimator(candidate)
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=4):
        estimator.fit(matrix, [count / duration for count, duration in zip(errors, durations_s)],
                      model__sample_weight=list(durations_s))
    estimator.speechrank_feature_arm_ = arm
    estimator.speechrank_feature_version_ = FEATURE_VERSION
    estimator.speechrank_feature_names_ = list(FEATURE_NAMES[arm])
    estimator.speechrank_candidate_ = _candidate(candidate)
    estimator.speechrank_training_rows_ = len(feature_rows)
    estimator.speechrank_training_audio_s_ = sum(durations_s)
    estimator.speechrank_training_errors_ = sum(errors)
    return estimator


def predict_density(model, feature_rows: Sequence[Mapping[str, Any]], arm: str = "legacy") -> list[float]:
    if getattr(model, "speechrank_feature_arm_", None) != arm or getattr(model, "speechrank_feature_version_", None) != FEATURE_VERSION:
        raise ValueError("model feature arm/version does not match prediction input")
    if not feature_rows:
        return []
    from threadpoolctl import threadpool_limits
    with threadpool_limits(limits=4):
        predictions = [float(value) for value in model.predict(_matrix(feature_rows, arm))]
    if any(not math.isfinite(value) or value < 0 for value in predictions):
        raise ValueError("model produced invalid error density")
    return predictions


def model_card(model) -> dict[str, Any]:
    """JSON-safe training metadata; callers persist this alongside joblib bytes."""
    import sklearn

    if not hasattr(model, "speechrank_feature_version_"):
        raise ValueError("model was not fitted through speechrank.fit_model")
    return {
        "schema_version": "speechrank-model-card-v1", "feature_version": model.speechrank_feature_version_,
        "feature_arm": model.speechrank_feature_arm_, "feature_names": model.speechrank_feature_names_,
        "candidate": model.speechrank_candidate_, "sklearn_version": sklearn.__version__,
        "estimator_parameters": model.named_steps["model"].get_params(deep=False),
        "iterations_run": int(model.named_steps["model"].n_iter_),
        "transformed_feature_count": int(model.named_steps["model"].n_features_in_),
        "imputer_indicator_feature_indices": [int(i) for i in model.named_steps["imputer"].indicator_.features_],
        "training_rows": model.speechrank_training_rows_, "training_audio_s": model.speechrank_training_audio_s_,
        "training_errors": model.speechrank_training_errors_,
        "target": "gold_edit_errors / audio_duration_s", "sample_weight": "audio_duration_s",
        "preprocessing": {"imputer": "training median", "missing_indicators": True,
                          "keep_empty_features": True, "scaler": "training mean and standard deviation"},
        "prediction": "nonnegative expected edit errors per audio second; not a calibrated probability",
    }
