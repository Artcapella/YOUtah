from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from backend.intelligence.model import model_status, predict_comfort
from backend.intelligence.schemas import AreaBrief, ComfortFeatures, ComfortPrediction, FeedbackEvent
from backend.intelligence.service import build_area_brief, store_feedback

router = APIRouter(prefix="/api/intelligence", tags=["Utah intelligence"])


@router.get("/model-status")
async def get_model_status() -> dict:
    return model_status()


@router.post("/comfort", response_model=ComfortPrediction)
async def score_comfort(features: ComfortFeatures) -> ComfortPrediction:
    """Score feature values supplied by an existing ingestion/data pipeline."""
    return predict_comfort(features)


@router.get("/brief", response_model=AreaBrief)
async def get_area_brief(
    lat: float = Query(default=40.7608, ge=-90, le=90),
    lng: float = Query(default=-111.8910, ge=-180, le=180),
) -> AreaBrief:
    try:
        return await build_area_brief(lat, lng)
    except LookupError as exc:
        raise HTTPException(status_code=502, detail="Live air-quality sources are temporarily unavailable") from exc


@router.post("/feedback", status_code=202)
async def record_feedback(event: FeedbackEvent) -> dict:
    """Validate a privacy-safe aggregate interaction and store it server-side for later training."""
    return {"accepted": True, "stored": await store_feedback(event), "entity_id": event.entity_id}
