"""The Outdoor Comfort feature contract, shared by training and serving.

Serving imports only this module (no pandas), so the deployed API stays light.
Training-frame construction lives in ``backend.intelligence.training``.
"""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

# Bump when MODEL_COLUMNS or their meaning changes; stored in every artifact and snapshot.
FEATURE_SCHEMA_VERSION = "outdoor-comfort-v2"
# Raw station-hour readings ingested into ml_feature_snapshots.
STATION_READING_SCHEMA_VERSION = "daq-station-hourly-v1"

MODEL_COLUMNS = [
    "pm25_24hr_avg",
    "temp_f",
    "humidity",
    "wind_mph",
    "elevation_m",
    "precip_probability",
    "hour",
    "month",
    "weekday",
    "pm25_hourly",
    "pm25_trend_3h",
]

# Calendar features use Utah local standard time in both training and serving.
UTAH_STANDARD_OFFSET = timedelta(hours=-7)

GOOD_MAX_PM25 = 12.0
CAUTION_MAX_PM25 = 35.4


def aqi_band(pm25: float | None) -> str | None:
    """Conservative simplified bands for an outdoor-comfort experience.

    This is deliberately not a replacement for official AQI calculations. The
    product retains the official source and displays its safety disclaimer.
    """
    if pm25 is None or not math.isfinite(float(pm25)):
        return None
    if pm25 <= GOOD_MAX_PM25:
        return "good"
    if pm25 <= CAUTION_MAX_PM25:
        return "caution"
    return "poor"


def utah_local(observed_at: datetime) -> datetime:
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=timezone.utc)
    return observed_at.astimezone(timezone(UTAH_STANDARD_OFFSET))


def _optional(value) -> float:
    return float("nan") if value is None else float(value)


def feature_row(values: dict, observed_at: datetime) -> dict[str, float | int]:
    local = utah_local(observed_at)
    return {
        "pm25_24hr_avg": float(values["pm25_24hr_avg"]),
        "temp_f": float(values["temp_f"]),
        "humidity": float(values["humidity"]),
        "wind_mph": float(values["wind_mph"]),
        "elevation_m": float(values.get("elevation_m", 1300)),
        "precip_probability": float(values.get("precip_probability") or 0),
        "hour": local.hour,
        "month": local.month,
        "weekday": local.weekday(),
        # NaN, not a guess: the gradient-boosted model routes missing values natively.
        "pm25_hourly": _optional(values.get("pm25_hourly")),
        "pm25_trend_3h": _optional(values.get("pm25_trend_3h")),
    }
