from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path

import joblib
import pandas as pd

from backend.intelligence.features import MODEL_COLUMNS, feature_row
from backend.intelligence.schemas import ComfortFeatures, ComfortPrediction
from backend.intelligence.scoring import fallback_comfort


@lru_cache(maxsize=1)
def load_model() -> dict | None:
    path = Path(os.getenv("OUTDOOR_COMFORT_MODEL_PATH", "backend/intelligence/artifacts/outdoor_comfort.joblib"))
    enabled = os.getenv("INTELLIGENCE_ENABLE_TRAINED_MODEL", "true").lower() == "true"
    if not enabled or not path.exists():
        return None
    return joblib.load(path)


def model_status() -> dict:
    artifact = load_model()
    if artifact is None:
        return {"status": "fallback", "reason": "No validated model artifact is deployed."}
    return {
        "status": "trained_nowcast",
        "horizon_hours": artifact["horizon_hours"],
        "metrics": artifact.get("metrics", {}),
        "trained_at": artifact.get("trained_at"),
    }


def predict_comfort(features: ComfortFeatures) -> ComfortPrediction:
    artifact = load_model()
    if artifact is None:
        return fallback_comfort(features)
    row = feature_row(features.model_dump(), features.observed_at)
    probabilities = artifact["model"].predict_proba(pd.DataFrame([row])[MODEL_COLUMNS])[0]
    labels = artifact["model"].classes_.tolist()
    by_label = {label: round(float(prob), 4) for label, prob in zip(labels, probabilities)}
    for label in ("good", "caution", "poor"):
        by_label.setdefault(label, 0.0)
    label = max(by_label, key=by_label.get)
    reasons = _reasons(features, label)
    return ComfortPrediction(
        label=label,
        probabilities=by_label,
        confidence=by_label[label],
        model_kind="trained_nowcast",
        horizon_hours=int(artifact["horizon_hours"]),
        reasons=reasons,
        disclaimer="Model-based estimate, not a health or safety guarantee. Check official Utah DAQ, NWS, and road/trail advisories.",
    )


def _reasons(features: ComfortFeatures, label: str) -> list[str]:
    reasons: list[str] = []
    if features.pm25_24hr_avg > 35.4:
        reasons.append("elevated recent PM2.5 is the largest concern")
    elif features.pm25_24hr_avg <= 12:
        reasons.append("recent PM2.5 is in the lowest prototype comfort band")
    if features.wind_mph >= 10:
        reasons.append("forecast wind supports pollutant dispersion")
    elif features.wind_mph <= 3:
        reasons.append("light wind increases inversion risk")
    if features.elevation_m >= 1700:
        reasons.append("higher elevation may offer better conditions than the valley")
    if not reasons:
        reasons.append(f"the model estimates {label} outdoor conditions from current air and weather inputs")
    return reasons[:3]


def write_metrics(path: Path, artifact: dict) -> None:
    metrics_path = path.with_suffix(".metrics.json")
    metrics_path.write_text(json.dumps(artifact.get("metrics", {}), indent=2) + "\n")
