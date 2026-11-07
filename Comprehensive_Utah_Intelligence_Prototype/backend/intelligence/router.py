from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from backend.intelligence.model import model_status, predict_comfort
from backend.intelligence.providers import fetch_comfort_features, fetch_ticketmaster_events
from backend.intelligence.schemas import AreaBrief, ComfortFeatures, FeedbackEvent
from backend.intelligence.scoring import local_pulse

router = APIRouter(prefix="/api/intelligence", tags=["Utah intelligence"])


@router.get("/model-status")
async def get_model_status() -> dict:
    return model_status()


@router.post("/comfort")
async def score_comfort(features: ComfortFeatures):
    """Score feature values supplied by an existing ingestion/data pipeline."""
    return predict_comfort(features)


@router.get("/brief", response_model=AreaBrief)
async def get_area_brief(
    lat: float = Query(default=40.7608, ge=-90, le=90),
    lng: float = Query(default=-111.8910, ge=-180, le=180),
) -> AreaBrief:
    try:
        features, sources = await fetch_comfort_features(lat, lng)
    except Exception as exc:
        raise HTTPException(status_code=502, detail="Live air/weather sources are temporarily unavailable") from exc
    comfort = predict_comfort(features)
    try:
        events = await fetch_ticketmaster_events(lat, lng)
    except Exception:
        events = []
    now = datetime.now(timezone.utc)
    return AreaBrief(
        generated_at=now,
        coordinates={"lat": lat, "lng": lng},
        outdoor_comfort=comfort,
        local_pulse=local_pulse(lat, lng, now, events, comfort),
        data_freshness={"air_quality_minutes": 0, "weather_minutes": 0},
        source_urls=sources,
    )


@router.post("/feedback", status_code=202)
async def record_feedback(event: FeedbackEvent) -> dict:
    """Validation boundary; replace with a server-side Supabase write in integration."""
    return {
        "accepted": True,
        "entity_id": event.entity_id,
        "message": "Validated. Wire this route to a service-role analytics_events insert before production.",
    }
