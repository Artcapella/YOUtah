"""Serve the Outdoor Comfort nowcast, or say exactly why the rules fallback is in use.

The deployed artifact is plain JSON evaluated by ``backend.intelligence.portable``,
so serving needs no ML libraries and never unpickles anything.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from backend.config import settings
from backend.intelligence.features import FEATURE_SCHEMA_VERSION, aqi_band, feature_row
from backend.intelligence.portable import PORTABLE_FORMAT
from backend.intelligence.portable import predict_proba as portable_predict_proba
from backend.intelligence.schemas import ComfortFeatures, ComfortPrediction
from backend.intelligence.scoring import fallback_comfort

logger = logging.getLogger(__name__)

ROOT_DIR = Path(__file__).resolve().parents[2]
MODEL_NAME = "outdoor_comfort"
FALLBACK_VERSION = "rules-v1"
TRAINED_DISCLAIMER = (
    "Model-based estimate, not a health or safety guarantee. "
    "Check official Utah DAQ, NWS, and road/trail advisories."
)


@dataclass(frozen=True)
class ModelLoad:
    artifact: dict | None
    reason: str | None = None


def artifact_path() -> Path:
    path = Path(settings.outdoor_comfort_model_path)
    return path if path.is_absolute() else ROOT_DIR / path


@lru_cache(maxsize=1)
def load_model() -> ModelLoad:
    if not settings.intelligence_enable_trained_model:
        return ModelLoad(None, "The trained model is disabled by INTELLIGENCE_ENABLE_TRAINED_MODEL.")
    path = artifact_path()
    if not path.exists():
        return ModelLoad(None, "No validated model artifact is deployed.")
    try:
        artifact = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        logger.exception("Could not load Outdoor Comfort artifact at %s", path)
        return ModelLoad(None, "The model artifact could not be loaded.")

    if (artifact.get("model") or {}).get("format") != PORTABLE_FORMAT:
        return ModelLoad(None, f"Artifact model format is not {PORTABLE_FORMAT}; retrain it.")
    if artifact.get("feature_schema_version") != FEATURE_SCHEMA_VERSION:
        return ModelLoad(None, f"Artifact uses feature schema {artifact.get('feature_schema_version')}, the API serves {FEATURE_SCHEMA_VERSION}; retrain it.")
    if not artifact.get("metrics", {}).get("passed_gate"):
        return ModelLoad(None, "The artifact did not pass the training gate.")
    return ModelLoad(artifact)


def model_status() -> dict:
    loaded = load_model()
    if loaded.artifact is None:
        return {"status": "fallback", "model_version": FALLBACK_VERSION, "reason": loaded.reason}
    artifact = loaded.artifact
    return {
        "status": "trained_nowcast",
        "model_version": artifact["model_version"],
        "horizon_hours": artifact["horizon_hours"],
        "feature_schema_version": artifact["feature_schema_version"],
        "trained_at": artifact.get("trained_at"),
        "training_window": artifact.get("training_window"),
        "metrics": artifact.get("metrics", {}),
    }


def predict_comfort(features: ComfortFeatures) -> ComfortPrediction:
    artifact = load_model().artifact
    if artifact is None:
        return fallback_comfort(features)
    row = feature_row(features.model_dump(), features.observed_at)
    probabilities = portable_predict_proba(artifact["model"], [float(row[column]) for column in artifact["feature_columns"]])
    by_label = {label: round(prob, 4) for label, prob in zip(artifact["model"]["classes"], probabilities)}
    for label in ("good", "caution", "poor"):
        by_label.setdefault(label, 0.0)
    label = max(by_label, key=by_label.get)
    return ComfortPrediction(
        label=label,
        probabilities=by_label,
        confidence=by_label[label],
        model_kind="trained_nowcast",
        model_version=artifact["model_version"],
        horizon_hours=int(artifact["horizon_hours"]),
        reasons=_reasons(features, label),
        disclaimer=TRAINED_DISCLAIMER,
    )


def current_model_identity() -> tuple[str, str, int]:
    """(model_name, model_version, horizon_hours) for prediction lineage rows."""
    artifact = load_model().artifact
    if artifact is None:
        return MODEL_NAME, FALLBACK_VERSION, 3
    return MODEL_NAME, artifact["model_version"], int(artifact["horizon_hours"])


def _reasons(features: ComfortFeatures, label: str) -> list[str]:
    reasons: list[str] = []
    if features.pm25_24hr_avg > 35.4:
        reasons.append("elevated recent PM2.5 is the largest concern")
    elif features.pm25_24hr_avg <= 12:
        reasons.append("recent PM2.5 is in the lowest prototype comfort band")
    trend = features.pm25_trend_3h
    if trend is not None and math.isfinite(trend) and abs(trend) >= 1:
        trend_reason = f"PM2.5 has been {'rising' if trend > 0 else 'falling'} over the last three hours"
        # When the forecast leaves the current band, the trend is the headline, not the current level.
        if label != aqi_band(features.pm25_24hr_avg):
            reasons.insert(0, trend_reason)
        else:
            reasons.append(trend_reason)
    if features.wind_mph >= 10:
        reasons.append("current wind supports pollutant dispersion")
    elif features.wind_mph <= 3:
        reasons.append("light wind increases inversion risk")
    if features.elevation_m >= 1700:
        reasons.append("higher elevation may offer better conditions than the valley")
    if not reasons:
        reasons.append(f"the model estimates {label} outdoor conditions from current air and weather inputs")
    return reasons[:3]


def write_artifact(path: Path, artifact: dict) -> None:
    """Write the deployable JSON artifact and a human-readable ``.metrics.json`` beside it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, separators=(",", ":")) + "\n", encoding="utf-8")
    summary = {key: value for key, value in artifact.items() if key != "model"}
    path.with_suffix(".metrics.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
