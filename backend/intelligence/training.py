"""Leakage-aware training for the Outdoor Comfort nowcast (offline only, needs pandas).

Values available at time ``t`` predict the comfort band of the 24-hour PM2.5 average
at ``t + horizon``. Folds are chronological with a purge gap, so no training label
overlaps the validation or test periods. A model is only returned if it beats the
persistence baseline ("the current band will still hold") on the final test period.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from backend.intelligence.features import (
    CAUTION_MAX_PM25,
    FEATURE_SCHEMA_VERSION,
    GOOD_MAX_PM25,
    MODEL_COLUMNS,
    UTAH_STANDARD_OFFSET,
    aqi_band,
)
from backend.intelligence.portable import LEAF, PORTABLE_FORMAT
from backend.intelligence.portable import predict_proba as portable_predict_proba

REQUIRED_COLUMNS = {"observed_at", "station_id", "pm25_24hr_avg", "temp_f", "humidity", "wind_mph", "elevation_m"}
# These columns must exist but may hold gaps; the model routes NaN natively instead of
# imputing. Trend inputs are missing after record gaps, and EPA AQS history has no wind
# for the DAQ stations (DAQ snapshots do).
OPTIONAL_FEATURES = {"pm25_hourly", "pm25_trend_3h", "temp_f", "humidity", "wind_mph"}
MIN_ROWS = 200
MIN_FOLD_ROWS = 25
CLASSES = ("good", "caution", "poor")


class TrainingRefused(RuntimeError):
    """Raised instead of producing a demo-quality model."""


def load_history(paths: Iterable[Path]) -> pd.DataFrame:
    """Concatenate history CSVs. When two files hold the same station-hour, the earlier path wins,
    so pass quality-assured history before live snapshots."""
    frames = [pd.read_csv(path) for path in paths]
    if not frames:
        raise TrainingRefused("No input files.")
    history = pd.concat(frames, ignore_index=True)
    history["observed_at"] = pd.to_datetime(history["observed_at"], utc=True, format="ISO8601").dt.floor("h")
    return history.drop_duplicates(["station_id", "observed_at"], keep="first").reset_index(drop=True)


def _shifted(keyed: pd.DataFrame, hours: int, name: str) -> pd.DataFrame:
    """Row at time t carries the 24-hour average observed at t + hours."""
    shifted = keyed.assign(observed_at=keyed["observed_at"] - pd.Timedelta(hours=hours))
    return shifted.rename(columns={"pm25_24hr_avg": name})


def make_nowcast_training_frame(frame: pd.DataFrame, horizon_hours: int) -> pd.DataFrame:
    missing = sorted(REQUIRED_COLUMNS - set(frame.columns))
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    data = frame.copy()
    data["observed_at"] = pd.to_datetime(data["observed_at"], utc=True, format="ISO8601").dt.floor("h")
    data["station_id"] = data["station_id"].astype(str)
    if "precip_probability" not in data.columns:
        data["precip_probability"] = 0.0
    if "pm25" not in data.columns:
        data["pm25"] = float("nan")
    numeric = ["pm25", "pm25_24hr_avg", "temp_f", "humidity", "wind_mph", "elevation_m", "precip_probability"]
    data[numeric] = data[numeric].apply(pd.to_numeric, errors="coerce")
    data["precip_probability"] = data["precip_probability"].fillna(0)
    data["pm25_hourly"] = data["pm25"].clip(lower=0)
    data = data.drop_duplicates(["station_id", "observed_at"], keep="first")

    # Join on exact timestamps rather than shifting rows, so gaps in the record never
    # pair a feature with the wrong hour.
    keys = ["station_id", "observed_at"]
    keyed = data[[*keys, "pm25_24hr_avg"]].dropna()
    data = data.merge(_shifted(keyed, horizon_hours, "target_pm25"), on=keys, how="left")
    data = data.merge(_shifted(keyed, -3, "pm25_24hr_avg_3h_ago"), on=keys, how="left")
    data["pm25_trend_3h"] = data["pm25_24hr_avg"] - data["pm25_24hr_avg_3h_ago"]
    data["target_class"] = data["target_pm25"].map(aqi_band)

    local = data["observed_at"] + UTAH_STANDARD_OFFSET
    data["hour"] = local.dt.hour
    data["month"] = local.dt.month
    data["weekday"] = local.dt.weekday

    required = [column for column in MODEL_COLUMNS if column not in OPTIONAL_FEATURES]
    data = data.dropna(subset=[*required, "target_class"])
    return data.sort_values(["observed_at", "station_id"]).reset_index(drop=True)


def chronological_split(frame: pd.DataFrame, horizon_hours: int) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """60/20/20 by time. Train and validation each end ``horizon_hours`` early (a purge gap),
    so their labels never come from the following fold's period."""
    times = frame["observed_at"]
    train_end, valid_end = times.quantile(0.6), times.quantile(0.8)
    gap = pd.Timedelta(hours=horizon_hours)
    train = frame[times <= train_end - gap]
    valid = frame[(times > train_end) & (times <= valid_end - gap)]
    test = frame[times > valid_end]
    return train, valid, test


def persistence_labels(frame: pd.DataFrame) -> pd.Series:
    return frame["pm25_24hr_avg"].map(
        lambda value: "good" if value <= GOOD_MAX_PM25 else "caution" if value <= CAUTION_MAX_PM25 else "poor"
    )


def evaluate(y_true: pd.Series, probabilities, classes, persistence: pd.Series) -> dict:
    from sklearn.metrics import accuracy_score, f1_score, log_loss

    classes = list(classes)
    prediction = [classes[index] for index in probabilities.argmax(axis=1)]
    per_class = f1_score(y_true, prediction, labels=classes, average=None, zero_division=0)
    # Macro-F1 for both model and baseline over the classes that occur in the test period, so a
    # stray prediction of an absent class costs recall rather than adding a zero-score class to
    # one side's average only.
    present = sorted(set(y_true))
    return {
        "macro_f1": round(float(f1_score(y_true, prediction, labels=present, average="macro", zero_division=0)), 4),
        "accuracy": round(float(accuracy_score(y_true, prediction)), 4),
        "log_loss": round(float(log_loss(y_true, probabilities, labels=classes)), 4),
        "f1_by_class": {label: round(float(score), 4) for label, score in zip(classes, per_class)},
        "persistence_accuracy": round(float(accuracy_score(y_true, persistence)), 4),
        "persistence_macro_f1": round(float(f1_score(y_true, persistence, labels=present, average="macro", zero_division=0)), 4),
        "class_counts": {label: int(count) for label, count in y_true.value_counts().items()},
        "rows": int(len(y_true)),
    }


def _matrix(frame: pd.DataFrame):
    return frame[MODEL_COLUMNS].to_numpy(dtype=float)


def _export_ensemble(model) -> dict:
    nodes_of = [[predictor.nodes for predictor in iteration] for iteration in model._predictors]
    if any(nodes["is_categorical"].any() for iteration in nodes_of for nodes in iteration):
        raise TrainingRefused("Categorical splits are not supported by the portable model format.")
    return {
        "baseline": [float(value) for value in model._baseline_prediction.ravel()],
        "trees": [
            [
                {
                    "f": nodes["feature_idx"].tolist(),
                    "t": nodes["num_threshold"].tolist(),
                    "m": nodes["missing_go_to_left"].astype(int).tolist(),
                    "l": [LEAF if is_leaf else int(left) for is_leaf, left in zip(nodes["is_leaf"], nodes["left"])],
                    "r": nodes["right"].tolist(),
                    "v": nodes["value"].tolist(),
                }
                for nodes in iteration
            ]
            for iteration in nodes_of
        ],
    }


def export_portable(fitted) -> dict:
    """Export a fitted HistGradientBoostingClassifier, or a sigmoid CalibratedClassifierCV around
    one, to the JSON format evaluated by ``backend.intelligence.portable``."""
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.preprocessing import LabelEncoder

    classes = [str(label) for label in fitted.classes_]
    if not isinstance(fitted, CalibratedClassifierCV):
        return {"format": PORTABLE_FORMAT, "classes": classes, "ensembles": [_export_ensemble(fitted)], "calibration": None}

    ensembles: list[dict] = []
    index_of: dict[int, int] = {}
    members = []
    for member in fitted.calibrated_classifiers_:
        base = getattr(member.estimator, "estimator", member.estimator)  # unwrap FrozenEstimator
        if id(base) not in index_of:
            index_of[id(base)] = len(ensembles)
            ensembles.append(_export_ensemble(base))
        if any(type(calibrator).__name__ != "_SigmoidCalibration" for calibrator in member.calibrators):
            raise TrainingRefused("Only sigmoid calibration is supported by the portable model format.")
        members.append({
            "ensemble": index_of[id(base)],
            "class_indices": [int(index) for index in LabelEncoder().fit(member.classes).transform(base.classes_)],
            "a": [float(calibrator.a_) for calibrator in member.calibrators],
            "b": [float(calibrator.b_) for calibrator in member.calibrators],
        })
    return {"format": PORTABLE_FORMAT, "classes": classes, "ensembles": ensembles, "calibration": members}


def check_portable_parity(fitted, portable: dict, matrix, max_rows: int = 2000, tolerance: float = 1e-9) -> float:
    """Max absolute probability difference between scikit-learn and the portable evaluator.
    Checks every row with a missing value (up to a quarter of the budget) plus a fixed random sample."""
    import numpy as np

    missing = np.flatnonzero(np.isnan(matrix).any(axis=1))[: max_rows // 4]
    rest = np.setdiff1d(np.arange(len(matrix)), missing)
    sampled = np.random.default_rng(42).choice(rest, size=min(len(rest), max_rows - len(missing)), replace=False)
    rows = matrix[np.concatenate([missing, sampled])]
    expected = fitted.predict_proba(rows)
    worst = max(
        max(abs(got - want) for got, want in zip(portable_predict_proba(portable, row.tolist()), wanted))
        for row, wanted in zip(rows, expected)
    )
    if worst > tolerance:
        raise TrainingRefused(f"Portable export does not reproduce scikit-learn (max difference {worst:.2e}).")
    return worst


def train_outdoor_comfort(frame: pd.DataFrame, horizon_hours: int) -> dict:
    """Fit, calibrate on the validation fold, and gate on the test fold. Returns the artifact dict."""
    import sklearn
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.frozen import FrozenEstimator
    from sklearn.metrics import log_loss

    if len(frame) < MIN_ROWS:
        raise TrainingRefused(f"Need at least {MIN_ROWS} usable historical rows (have {len(frame)}); refusing to train a demo-quality model.")
    if frame["target_class"].nunique() < 2:
        raise TrainingRefused(
            f"Need at least two target classes (have only {sorted(frame['target_class'].unique())}); "
            "choose a longer or more varied historical period, e.g. one that includes a winter inversion season."
        )

    train, valid, test = chronological_split(frame, horizon_hours)
    if min(len(train), len(valid), len(test)) < MIN_FOLD_ROWS:
        raise TrainingRefused("Chronological folds are too small; provide more history.")
    if train["target_class"].nunique() < 2:
        raise TrainingRefused("The training period contains a single class; provide more varied history.")

    model = HistGradientBoostingClassifier(
        learning_rate=0.06,
        max_leaf_nodes=20,
        l2_regularization=1.0,
        random_state=42,
    )
    model.fit(_matrix(train), train["target_class"].to_numpy())
    classes = list(model.classes_)
    unseen = set(test["target_class"]) - set(classes)
    if unseen:
        raise TrainingRefused(f"The test period contains classes the training period never saw: {sorted(unseen)}.")

    # Calibrate probabilities on the validation fold; keep calibration only if it helps there.
    final_model, calibration = model, "none"
    if set(valid["target_class"]) == set(classes):
        try:
            calibrated = CalibratedClassifierCV(FrozenEstimator(model), method="sigmoid").fit(_matrix(valid), valid["target_class"])
            raw_loss = log_loss(valid["target_class"], model.predict_proba(_matrix(valid)), labels=classes)
            calibrated_loss = log_loss(valid["target_class"], calibrated.predict_proba(_matrix(valid)), labels=classes)
            if calibrated_loss < raw_loss:
                final_model, calibration = calibrated, "sigmoid"
        except ValueError:
            calibration = "skipped"

    metrics = evaluate(
        test["target_class"],
        final_model.predict_proba(_matrix(test)),
        final_model.classes_,
        persistence_labels(test),
    )
    metrics.update(
        train_rows=int(len(train)),
        validation_rows=int(len(valid)),
        calibration=calibration,
        passes_persistence_baseline=metrics["accuracy"] > metrics["persistence_accuracy"],
    )
    # Accuracy alone rewards always predicting the majority "good" class, so the
    # model must also match persistence on macro-F1 (the minority classes).
    metrics["passed_gate"] = metrics["passes_persistence_baseline"] and metrics["macro_f1"] >= metrics["persistence_macro_f1"]
    if not metrics["passed_gate"]:
        raise TrainingRefused(f"Model did not beat the persistence baseline on the test period: {metrics}")

    portable = export_portable(final_model)
    metrics["portable_max_abs_diff"] = check_portable_parity(final_model, portable, _matrix(test))

    trained_at = datetime.now(timezone.utc)
    return {
        "model": portable,
        "model_version": f"oc-h{horizon_hours}-{trained_at:%Y%m%d%H%M}",
        "horizon_hours": horizon_hours,
        "trained_at": trained_at.isoformat(),
        "feature_schema_version": FEATURE_SCHEMA_VERSION,
        "feature_columns": list(MODEL_COLUMNS),
        "training_window": {
            "start": frame["observed_at"].min().isoformat(),
            "end": frame["observed_at"].max().isoformat(),
            "stations": sorted(frame["station_id"].unique().tolist()),
            "test_start": test["observed_at"].min().isoformat(),
        },
        "versions": {"scikit-learn": sklearn.__version__, "pandas": pd.__version__},
        "metrics": metrics,
    }
