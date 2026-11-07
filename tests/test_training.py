import math
from datetime import datetime, timezone

import pandas as pd
import pytest

from backend.config import settings
from backend.intelligence.features import MODEL_COLUMNS
from backend.intelligence.model import load_model, model_status, predict_comfort, write_artifact
from backend.intelligence.schemas import ComfortFeatures
from backend.intelligence.training import (
    TrainingRefused,
    check_portable_parity,
    chronological_split,
    evaluate,
    export_portable,
    make_nowcast_training_frame,
    train_outdoor_comfort,
)


def history(hours: int = 24 * 60, stations=("slc", "ln"), amplitude: float = 8.0) -> pd.DataFrame:
    """A 24-hour average oscillating across the good/caution boundary twice a day, so
    "the band will persist" is wrong near every crossing but the trend predicts it."""
    start = pd.Timestamp("2026-01-01T00:00:00Z")
    rows = []
    for offset, station in enumerate(stations):
        for hour in range(hours):
            avg = 12 + amplitude * math.sin(2 * math.pi * (hour + offset * 5) / 24)
            rows.append({
                "observed_at": (start + pd.Timedelta(hours=hour)).isoformat(),
                "station_id": station,
                "pm25": max(0.0, avg + 2 * math.cos(2 * math.pi * hour / 24)),
                "pm25_24hr_avg": avg,
                "temp_f": 30 + (hour % 24),
                "humidity": 60,
                "wind_mph": 2 + (hour % 5),
                "elevation_m": 1300 + offset * 100,
            })
    return pd.DataFrame(rows)


def test_target_comes_from_exactly_horizon_hours_later_even_across_gaps() -> None:
    raw = history(hours=48, stations=("slc",))
    raw = raw.drop(index=[10, 11])  # a two-hour gap must not shift later targets
    frame = make_nowcast_training_frame(raw, horizon_hours=3)
    by_time = raw.assign(observed_at=pd.to_datetime(raw["observed_at"], utc=True)).set_index("observed_at")["pm25_24hr_avg"]
    for _, row in frame.iterrows():
        target_time = row["observed_at"] + pd.Timedelta(hours=3)
        assert row["target_pm25"] == pytest.approx(by_time[target_time])
    # Rows whose target hour is missing are dropped rather than paired with the wrong hour.
    assert pd.Timestamp("2026-01-01T07:00:00Z") not in set(frame["observed_at"])


def test_split_purges_labels_that_would_leak_into_the_next_fold() -> None:
    frame = make_nowcast_training_frame(history(), horizon_hours=3)
    train, valid, test = chronological_split(frame, horizon_hours=3)
    assert train["observed_at"].max() + pd.Timedelta(hours=3) < valid["observed_at"].min()
    assert valid["observed_at"].max() + pd.Timedelta(hours=3) < test["observed_at"].min()


def test_history_without_wind_still_trains() -> None:
    """EPA AQS has no wind for the DAQ stations; those rows stay, with wind missing."""
    frame = make_nowcast_training_frame(history().assign(wind_mph=float("nan")), horizon_hours=3)
    assert len(frame) > 2000
    assert frame["wind_mph"].isna().all()
    assert train_outdoor_comfort(frame, 3)["metrics"]["passed_gate"] is True


def test_macro_f1_compares_model_and_baseline_on_the_same_classes() -> None:
    import numpy as np

    truth = pd.Series(["good"] * 8 + ["caution"] * 2)
    # The model is right everywhere except one stray "poor" (absent from the test period).
    probabilities = np.array([[0, 1, 0]] * 7 + [[0, 0, 1]] + [[1, 0, 0]] * 2, dtype=float)
    metrics = evaluate(truth, probabilities, ["caution", "good", "poor"], persistence=truth)
    assert metrics["persistence_macro_f1"] == 1.0
    assert metrics["macro_f1"] > 0.9  # not dragged to ~0.63 by a zero-score "poor" class


def test_refuses_single_class_history() -> None:
    clean = history(amplitude=0).assign(pm25_24hr_avg=5.0)
    with pytest.raises(TrainingRefused, match="two target classes"):
        train_outdoor_comfort(make_nowcast_training_frame(clean, 3), 3)


def test_refuses_tiny_history() -> None:
    with pytest.raises(TrainingRefused, match="at least 200"):
        train_outdoor_comfort(make_nowcast_training_frame(history(hours=50, stations=("slc",)), 3), 3)


def test_trains_gated_artifact_and_serves_it(tmp_path, monkeypatch) -> None:
    artifact = train_outdoor_comfort(make_nowcast_training_frame(history(), 3), 3)
    metrics = artifact["metrics"]
    assert metrics["passed_gate"] is True
    assert metrics["accuracy"] > metrics["persistence_accuracy"]
    assert metrics["portable_max_abs_diff"] <= 1e-9

    path = tmp_path / "outdoor_comfort.json"
    write_artifact(path, artifact)
    assert path.with_suffix(".metrics.json").exists()
    monkeypatch.setattr(settings, "outdoor_comfort_model_path", str(path))
    load_model.cache_clear()
    assert model_status()["status"] == "trained_nowcast"

    prediction = predict_comfort(ComfortFeatures(
        pm25_24hr_avg=11.5, temp_f=40, humidity=60, wind_mph=2, elevation_m=1300,
        pm25_hourly=16, pm25_trend_3h=2.5, observed_at=datetime(2026, 1, 5, 15, tzinfo=timezone.utc),
    ))
    assert prediction.model_kind == "trained_nowcast"
    assert prediction.model_version == artifact["model_version"]
    assert sum(prediction.probabilities.values()) == pytest.approx(1, abs=0.01)


def test_stale_feature_schema_falls_back_with_reason(tmp_path, monkeypatch) -> None:
    artifact = train_outdoor_comfort(make_nowcast_training_frame(history(), 3), 3)
    artifact["feature_schema_version"] = "outdoor-comfort-v1"
    path = tmp_path / "old.json"
    write_artifact(path, artifact)
    monkeypatch.setattr(settings, "outdoor_comfort_model_path", str(path))
    load_model.cache_clear()
    status = model_status()
    assert status["status"] == "fallback"
    assert "outdoor-comfort-v1" in status["reason"]


@pytest.mark.parametrize("calibrate", [False, True])
@pytest.mark.parametrize("amplitude", [8.0, 30.0])  # two classes, then good/caution/poor
def test_portable_export_reproduces_sklearn(calibrate, amplitude) -> None:
    import numpy as np
    from sklearn.calibration import CalibratedClassifierCV
    from sklearn.ensemble import HistGradientBoostingClassifier
    from sklearn.frozen import FrozenEstimator

    frame = make_nowcast_training_frame(history(amplitude=amplitude), 3)
    matrix = frame[MODEL_COLUMNS].to_numpy(dtype=float)
    matrix[::7, MODEL_COLUMNS.index("pm25_trend_3h")] = np.nan  # exercise missing-value routing
    labels = frame["target_class"].to_numpy()
    fitted = HistGradientBoostingClassifier(max_iter=40, random_state=0).fit(matrix[:2000], labels[:2000])
    if calibrate:
        fitted = CalibratedClassifierCV(FrozenEstimator(fitted), method="sigmoid").fit(matrix[2000:2600], labels[2000:2600])
    assert len(fitted.classes_) == (2 if amplitude < 20 else 3)
    assert check_portable_parity(fitted, export_portable(fitted), matrix[2000:]) <= 1e-9


def test_missing_artifact_reports_fallback(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(settings, "outdoor_comfort_model_path", str(tmp_path / "absent.joblib"))
    status = model_status()
    assert status == {"status": "fallback", "model_version": "rules-v1", "reason": "No validated model artifact is deployed."}


def test_committed_artifact_is_servable_without_ml_libraries(monkeypatch) -> None:
    """Guards the deployed model: it must load, pass its recorded gate, and score a row."""
    from backend.intelligence.model import ROOT_DIR

    path = ROOT_DIR / "backend" / "intelligence" / "artifacts" / "outdoor_comfort.json"
    if not path.exists():
        pytest.skip("no trained artifact committed")
    monkeypatch.setattr(settings, "outdoor_comfort_model_path", str(path))
    load_model.cache_clear()
    status = model_status()
    assert status["status"] == "trained_nowcast", status
    assert status["metrics"]["accuracy"] > status["metrics"]["persistence_accuracy"]
    prediction = predict_comfort(ComfortFeatures(
        pm25_24hr_avg=11.2, pm25_hourly=24, pm25_trend_3h=2.1, temp_f=28, humidity=82, wind_mph=1.2,
        elevation_m=1307, observed_at=datetime(2026, 1, 8, 18, tzinfo=timezone.utc),
    ))
    assert prediction.model_kind == "trained_nowcast"
    assert prediction.label == "caution"
    assert prediction.reasons[0].startswith("PM2.5 has been rising")
