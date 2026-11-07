"""Escape the Inversion: combines sources + core logic into JSON-ready payloads for the API routes.
Works anywhere in Utah: stations, the valley air monitor and trailheads are picked near the starting point.
The smog shading covers the Wasatch Front only, where there are enough air monitors."""
from __future__ import annotations

from backend.inversion import core, sources

DEMO_PM25 = 48.0       # a smoggy winter-day valley value for the simulated demo
MONITOR_RADIUS_KM = 80  # use the nearest air monitor only if it is this close
TRAILHEAD_RADIUS_KM = 80


async def today(mode: str, lat: float, lon: float) -> dict:
    monitors = await sources.fetch_monitors()
    wasatch_monitors = [m for m in monitors
                        if sources.BBOX[0] - .2 <= m["lon"] <= sources.BBOX[2] + .2
                        and sources.BBOX[1] - .2 <= m["lat"] <= sources.BBOX[3] + .2]

    if mode == "demo":
        stations, source = sources.demo_stations(), "simulated Salt Lake Valley inversion day (real station locations)"
        top = max((m["pm25"] for m in wasatch_monitors), default=1) or 1
        wasatch_monitors = [{**m, "pm25": round(m["pm25"] * DEMO_PM25 / top, 1)} for m in wasatch_monitors]
        pm25, air_from = DEMO_PM25, "simulated"
    else:
        stations, source = await sources.fetch_stations(lat, lon)
        near = core.nearby(monitors, lat, lon, MONITOR_RADIUS_KM)
        if near:
            pm25 = near[0]["pm25"]
            air_from = f"{near[0]['name']} monitor, {core.km(lat, lon, near[0]['lat'], near[0]['lon']):.0f} km away"
        else:
            pm25, air_from = None, "no air monitor within 80 km"

    lid = core.estimate_lid(stations)
    pool = core.nearby(sources.load_trailheads(), lat, lon, TRAILHEAD_RADIUS_KM, min_count=12)
    trailheads = core.judge_trailheads(pool, lid, pm25, lat, lon)
    cells = core.smog_cells(sources.load_grid(), wasatch_monitors,
                            lid["lid_m"] if lid["has_inversion"] else None) if wasatch_monitors else []

    return {
        "mode": mode,
        "source": source,
        "air_from": air_from,
        "headline": core.headline(lid, pm25),
        "pm25": pm25,
        "pm_level": core.PM_NAMES[core.pm_category(pm25)],
        "lid": lid,
        "counts": {v: sum(t["verdict"] == v for t in trailheads) for v in core.VERDICTS},
        "trailheads": trailheads,
        "monitors": wasatch_monitors if mode == "demo" else monitors,
        "stations": [{k: s.get(k) for k in ("stid", "name", "lat", "lon", "elevation_m", "air_temp_c")} for s in stations],
        "smog": {"bbox": sources.BBOX, "grid_n": int(round(len(cells) ** 0.5)), "cells": cells},
        "in_wasatch": sources.in_wasatch(lat, lon),
        "disclaimer": "Inversion height is an estimate for visitors, not an official forecast or a safety clearance.",
    }


async def outlook(lat: float, lon: float) -> list[dict]:
    hours, ground_m = await sources.fetch_forecast_hours(lat, lon)
    return core.daily_outlook(hours, ground_m)
