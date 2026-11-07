from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

from backend.intelligence.features import aqi_band
from backend.intelligence.schemas import ComfortFeatures, ComfortPrediction, EventSignal, PulseEstimate

FALLBACK_DISCLAIMER = "Estimate only. Check official Utah DAQ, NWS, and road/trail advisories before leaving."


def _haversine_km(lat_a: float, lng_a: float, lat_b: float, lng_b: float) -> float:
    radius_km = 6371.0
    d_lat = math.radians(lat_b - lat_a)
    d_lng = math.radians(lng_b - lng_a)
    a = math.sin(d_lat / 2) ** 2 + math.cos(math.radians(lat_a)) * math.cos(math.radians(lat_b)) * math.sin(d_lng / 2) ** 2
    return radius_km * 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))


# Fixed, normalized probabilities for the rules fallback, by label.
_FALLBACK_PROBABILITIES = {
    "good": {"good": 0.72, "caution": 0.2, "poor": 0.08},
    "caution": {"good": 0.08, "caution": 0.7, "poor": 0.22},
    "poor": {"good": 0.06, "caution": 0.22, "poor": 0.72},
}


def fallback_comfort(features: ComfortFeatures, horizon_hours: int = 3) -> ComfortPrediction:
    """Transparent fallback used only when no validated artifact is deployed."""
    band = aqi_band(features.pm25_24hr_avg)
    reasons: list[str] = []
    risk = {"good": 0, "caution": 1, "poor": 2}[band]
    if features.wind_mph >= 10:
        risk = max(0, risk - 1)
        reasons.append("stronger wind can help disperse particulate pollution")
    elif features.wind_mph <= 3:
        risk = min(2, risk + 1)
        reasons.append("very light wind can allow pollution to linger")
    if features.precip_probability >= 60:
        reasons.append("precipitation may affect outdoor comfort")
    if features.elevation_m >= 1700:
        reasons.append("higher elevation may be above some valley pollution")
    label = ("good", "caution", "poor")[risk]
    probabilities = dict(_FALLBACK_PROBABILITIES[label])
    if not reasons:
        reasons.append("latest PM2.5 conditions are the primary signal")
    return ComfortPrediction(
        label=label,
        probabilities=probabilities,
        confidence=max(probabilities.values()),
        model_kind="conservative_fallback",
        horizon_hours=horizon_hours,
        reasons=reasons[:3],
        disclaimer=FALLBACK_DISCLAIMER,
    )


def local_pulse(
    lat: float,
    lng: float,
    when: datetime,
    events: list[EventSignal],
    comfort: ComfortPrediction,
    radius_km: float = 5,
    events_available: bool = True,
) -> PulseEstimate:
    """An explainable ranking, deliberately not a crowd/attendance estimate.

    ``events_available=False`` means no real event feed is connected; the score then
    reflects outdoor conditions only and says so, rather than implying "no events".
    """
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    window_end = when + timedelta(hours=4)
    nearby = [
        event for event in events
        if when <= event.starts_at <= window_end
        and _haversine_km(lat, lng, event.lat, event.lng) <= radius_km
    ]
    event_points = min(45, len(nearby) * 9)
    free_points = min(10, sum(event.is_free for event in nearby) * 3)
    culture_points = min(10, len({event.category.lower() for event in nearby}) * 3)
    comfort_points = {"good": 25, "caution": 14, "poor": 4}[comfort.label]
    score = min(100, 10 + event_points + free_points + culture_points + comfort_points)
    drivers = [f"{len(nearby)} event{'s' if len(nearby) != 1 else ''} within {radius_km:g} km in the next four hours"]
    if comfort.label == "good":
        drivers.append("predicted outdoor conditions are favorable")
    elif comfort.label == "poor":
        drivers.append("outdoor conditions reduce the area score")
    if free_points:
        drivers.append("nearby free events improve budget friendliness")
    if not events_available:
        drivers[0] = "no live event feed is connected yet, so this reflects outdoor conditions only"
    elif not nearby:
        drivers[0] = "no nearby events were found in the next four hours"
    return PulseEstimate(score=score, nearby_event_count=len(nearby), drivers=drivers[:3])
