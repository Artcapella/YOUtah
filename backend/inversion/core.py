"""Escape the Inversion: pure logic, no I/O and no heavy dependencies.

1. estimate_lid: find the smog lid from station (elevation, temperature) pairs.
2. judge_trailheads: GO / CAUTION / SKIP per trailhead from the lid + valley PM2.5.
3. smog_cells: Gaussian-kernel interpolation of PM2.5 monitors, cut off above the lid by terrain.
4. daily_outlook: 7-day inversion risk from forecast hours.
"""
from __future__ import annotations

import math
from collections import defaultdict
from statistics import median

MIN_STATIONS = 3
MIN_WARMING_C = 2.0   # a layer must be this much warmer than the valley floor to count as an inversion
BIN_M = 100
LID_MARGIN_M = 100    # trailheads must sit this far above the lid to count as "above"

# EPA PM2.5 AQI breakpoints (µg/m³, 2024): 0 Good / 1 Moderate / 2 USG / 3 Unhealthy+
PM_BREAKS = [9.0, 35.4, 55.4]
PM_NAMES = ["Good", "Moderate", "Unhealthy for sensitive groups", "Unhealthy"]
VERDICTS = ["GO", "CAUTION", "SKIP"]


# ---------- 1. lid ----------

def estimate_lid(stations: list[dict]) -> dict:
    """stations: [{elevation_m, air_temp_c, ...}] -> {has_inversion, lid_m, valley_temp_c, lid_temp_c, reason}."""
    rows = [s for s in stations if s.get("elevation_m") is not None and s.get("air_temp_c") is not None]
    if len(rows) < MIN_STATIONS:
        return dict(has_inversion=False, lid_m=None, valley_temp_c=None, lid_temp_c=None,
                    reason="not enough stations")

    bins: dict[int, list] = defaultdict(list)
    for s in rows:
        bins[int(s["elevation_m"] // BIN_M)].append(s)
    profile = {b: median(x["air_temp_c"] for x in v) for b, v in bins.items()}
    valley_bin = min(profile)
    lid_bin = max(profile, key=profile.get)
    valley_t, lid_t = profile[valley_bin], profile[lid_bin]

    if lid_bin == valley_bin or lid_t - valley_t < MIN_WARMING_C:
        return dict(has_inversion=False, lid_m=None, valley_temp_c=round(valley_t, 1), lid_temp_c=None,
                    reason="temperature drops with height (normal)")

    lid_m = round(sum(s["elevation_m"] for s in bins[lid_bin]) / len(bins[lid_bin]))
    return dict(has_inversion=True, lid_m=lid_m, valley_temp_c=round(valley_t, 1), lid_temp_c=round(lid_t, 1),
                reason=f"~{lid_m} m is {lid_t - valley_t:.1f}°C warmer than the valley")


# ---------- 2. trailheads ----------

def pm_category(pm25: float | None) -> int:
    if pm25 is None:
        return 1  # unknown -> treat as Moderate (conservative)
    return sum(pm25 > b for b in PM_BREAKS)


def km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    a = (math.sin((p2 - p1) / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(math.radians(lon2 - lon1) / 2) ** 2)
    return 6371 * 2 * math.asin(math.sqrt(a))


def nearby(items: list[dict], lat: float, lon: float, radius_km: float, min_count: int = 0) -> list[dict]:
    """Items within radius_km of (lat, lon); if fewer than min_count, the nearest min_count instead."""
    ranked = sorted(items, key=lambda t: km(lat, lon, t["lat"], t["lon"]))
    inside = [t for t in ranked if km(lat, lon, t["lat"], t["lon"]) <= radius_km]
    return inside if len(inside) >= min_count else ranked[:min_count]


def judge_trailhead(elevation_m: float, pm25: float | None, lid: dict) -> tuple[str, str]:
    above = lid["has_inversion"] and elevation_m >= lid["lid_m"] + LID_MARGIN_M
    if above:
        return "GO", "above the smog lid, clean air"
    cat = pm_category(pm25)
    return VERDICTS[{0: 0, 1: 0, 2: 1, 3: 2}[cat]], f"valley air: {PM_NAMES[cat]}"


def judge_trailheads(trailheads: list[dict], lid: dict, pm25: float | None,
                     lat: float, lon: float) -> list[dict]:
    """GO first, then nearest. Each item gains verdict, why, distance_km."""
    out = []
    for t in trailheads:
        v, why = judge_trailhead(t["elevation_m"], pm25, lid)
        out.append({**t, "elevation_m": round(t["elevation_m"]), "verdict": v, "why": why,
                    "distance_km": round(km(lat, lon, t["lat"], t["lon"]), 1)})
    order = {v: i for i, v in enumerate(VERDICTS)}
    return sorted(out, key=lambda r: (order[r["verdict"]], r["distance_km"]))


def headline(lid: dict, pm25: float | None) -> dict:
    """Big one-line answer + tone for the page banner."""
    cat = pm_category(pm25)
    if pm25 is None and not lid["has_inversion"]:
        detail = ("Too few weather stations nearby to check for an inversion either."
                  if lid.get("reason") == "not enough stations"
                  else "Nearby weather stations show no inversion, but air quality isn't measured here.")
        return dict(tone="info", title="No air monitor near here.", body=f"{detail} Trailheads are listed by distance.")
    if lid["has_inversion"] and cat >= 2:
        return dict(tone="bad", title=f"Smog is trapped below ~{lid['lid_m']:,} m. Go up to breathe clean air.",
                    body=f"Inversion is trapping smog below ~{lid['lid_m']} m. Escape above it.")
    if lid["has_inversion"]:
        return dict(tone="warn", title="An inversion is forming.",
                    body=f"A warm layer sits at ~{lid['lid_m']} m. Air is OK now but may get worse over the next days.")
    if cat >= 2:
        return dict(tone="warn", title="Air is bad, but not from an inversion.",
                    body="Could be wildfire smoke. Going higher may not help.")
    if cat == 1:
        return dict(tone="good", title="No inversion. Air is moderate.",
                    body="Fine for most people; sensitive groups should take it easy.")
    return dict(tone="good", title="Clean air. Great day to hike anywhere.", body="Good mixing, no inversion.")


# ---------- 3. smog map ----------

def _flat_km(lat1, lon1, lat2, lon2):
    return math.hypot((lat1 - lat2) * 111.0, (lon1 - lon2) * 111.0 * math.cos(math.radians(40.7)))


def gaussian_pm(lat: float, lon: float, monitors: list[dict], sigma_km: float = 5.0) -> tuple[float, float]:
    """Gaussian-kernel weighted PM2.5 at one point, plus distance to the nearest monitor (km).
    Distances are shifted by the nearest one so weights never underflow far from every monitor."""
    d = [_flat_km(lat, lon, m["lat"], m["lon"]) for m in monitors]
    dmin = min(d)
    w = [math.exp(-0.5 * (x * x - dmin * dmin) / sigma_km ** 2) for x in d]
    return sum(wi * m["pm25"] for wi, m in zip(w, monitors)) / sum(w), dmin


def smog_cells(grid: list[dict], monitors: list[dict], lid_m: float | None,
               clean_pm: float = 3.0, fade_m: float = 120.0, sigma_km: float = 8.0) -> list[dict]:
    """grid: [{lat, lon, elevation_m}] -> same + pm25, nearest_monitor_km.
    With an inversion, cells above the lid fade to clean air; without one, air is mixed."""
    out = []
    for c in grid:
        pm, dmin = gaussian_pm(c["lat"], c["lon"], monitors, sigma_km)
        if lid_m is not None:
            under = 1 / (1 + math.exp((c["elevation_m"] - lid_m) / (fade_m / 4)))
            pm = clean_pm + (pm - clean_pm) * under
        out.append({**c, "pm25": round(pm, 1), "nearest_monitor_km": round(dmin, 1)})
    return out


# ---------- 4. 7-day outlook ----------

def daily_outlook(hours: list[dict], ground_m: float) -> list[dict]:
    """hours: [{time 'YYYY-MM-DDTHH:MM', temperature_2m, temperature_850hPa, boundary_layer_height}].
    Morning inversion = 850 hPa (~1.5 km) warmer than the ground. Afternoon mixing height ~ lid."""
    days: dict[str, list] = defaultdict(list)
    for h in hours:
        days[h["time"][:10]].append(h)
    out = []
    for i, (date, hs) in enumerate(sorted(days.items())):
        def hour(h):
            return int(h["time"][11:13])
        morning = [h["temperature_850hPa"] - h["temperature_2m"] for h in hs
                   if 4 <= hour(h) <= 9 and h["temperature_850hPa"] is not None and h["temperature_2m"] is not None]
        afternoon = [h["boundary_layer_height"] for h in hs
                     if 11 <= hour(h) <= 17 and h["boundary_layer_height"] is not None]
        if not morning or not afternoon:
            continue
        inv_c, mix_m = max(morning), max(afternoon)
        if inv_c >= 2 and mix_m < 600:
            risk = "High"
        elif inv_c >= 2 or mix_m < 800:
            risk = "Medium"
        else:
            risk = "Low"
        out.append(dict(date=date, morning_inversion_c=round(inv_c, 1), afternoon_mixing_m=round(mix_m),
                        lid_elevation_m=int(round(ground_m + mix_m, -1)), risk=risk,
                        confidence="forecast" if i < 3 else "trend"))
    return out
