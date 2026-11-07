from __future__ import annotations

from datetime import datetime
import math

import pandas as pd

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
]


def aqi_band(pm25: float) -> str | None:
    """Conservative simplified bands for an outdoor-comfort experience.

    This is deliberately not a replacement for official AQI calculations. The
    product retains the official source and displays its safety disclaimer.
    """
    if not math.isfinite(float(pm25)):
        return None
    if pm25 <= 12.0:
        return "good"
    if pm25 <= 35.4:
        return "caution"
    return "poor"


def feature_row(values: dict, observed_at: datetime) -> dict[str, float | int]:
    return {
        "pm25_24hr_avg": float(values["pm25_24hr_avg"]),
        "temp_f": float(values["temp_f"]),
        "humidity": float(values["humidity"]),
        "wind_mph": float(values["wind_mph"]),
        "elevation_m": float(values.get("elevation_m", 1300)),
        "precip_probability": float(values.get("precip_probability", 0)),
        "hour": observed_at.hour,
        "month": observed_at.month,
        "weekday": observed_at.weekday(),
    }


def make_nowcast_training_frame(frame: pd.DataFrame, horizon_hours: int) -> pd.DataFrame:
    required = {
        "observed_at", "station_id", "pm25_24hr_avg", "temp_f", "humidity",
        "wind_mph", "elevation_m",
    }
    missing = sorted(required - set(frame.columns))
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}")

    data = frame.copy()
    data["observed_at"] = pd.to_datetime(data["observed_at"], utc=True)
    if "precip_probability" not in data.columns:
        data["precip_probability"] = 0
    data["precip_probability"] = data["precip_probability"].fillna(0)
    data = data.sort_values(["station_id", "observed_at"])
    # Hourly observations predict the band horizon rows later within each station.
    data["target_pm25"] = data.groupby("station_id")["pm25_24hr_avg"].shift(-horizon_hours)
    data["target_class"] = data["target_pm25"].map(aqi_band)
    data["hour"] = data["observed_at"].dt.hour
    data["month"] = data["observed_at"].dt.month
    data["weekday"] = data["observed_at"].dt.weekday
    return data.dropna(subset=[*MODEL_COLUMNS, "target_class"]).reset_index(drop=True)
