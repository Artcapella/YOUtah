"""Ingestion jobs. Provider-specific parsing stays in backend/providers; this module owns
the Supabase write lifecycle (ingest_runs bookkeeping, idempotent upserts)."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from backend.intelligence.features import STATION_READING_SCHEMA_VERSION
from backend.intelligence.model import current_model_identity, predict_comfort
from backend.intelligence.service import comfort_features_from_readings
from backend.models import AirStationReading
from backend.providers.utah_daq import STATIONS_BY_ID, fetch_all_station_readings
from backend.supabase_client import get_service_client

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500
SNAPSHOT_CONFLICT = "entity_type,entity_id,prediction_time,feature_schema_version"
PREDICTION_CONFLICT = "entity_type,entity_id,prediction_time,horizon_hours,model_name,model_version"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run_ingest(source: str) -> dict:
    if source == "utah-daq":
        return await ingest_utah_daq()
    return {
        "source": source,
        "status": "accepted",
        "message": "Ingestion contract is ready; connect this source adapter and Supabase write in the next increment.",
        "accepted_at": _utcnow().isoformat(),
    }


def snapshot_rows(readings: list[AirStationReading], available_at: datetime) -> list[dict[str, Any]]:
    """One ml_feature_snapshots row per station-hour. ``available_at`` is when this project
    first saw the value; the upsert keeps the first one, so training joins stay leakage-safe."""
    rows = []
    for reading in readings:
        station = STATIONS_BY_ID[reading.station_id]
        rows.append({
            "entity_id": reading.station_id,
            "entity_type": "station",
            "prediction_time": reading.observed_at.isoformat(),
            "available_at": available_at.isoformat(),
            "feature_schema_version": STATION_READING_SCHEMA_VERSION,
            "features": {
                **reading.model_dump(mode="json", exclude={"station_id", "observed_at"}),
                "elevation_m": station.elevation_m,
                "lat": station.coordinate.lat,
                "lng": station.coordinate.lng,
            },
        })
    return rows


def prediction_row(station_id: str, readings: list[AirStationReading], now: datetime) -> dict[str, Any] | None:
    """Score a station's newest reading so predictions can later be compared with what happened."""
    features = comfort_features_from_readings(STATIONS_BY_ID[station_id], readings)
    if features is None:
        return None
    prediction = predict_comfort(features)
    model_name, model_version, horizon_hours = current_model_identity()
    return {
        "entity_id": station_id,
        "entity_type": "station",
        "prediction_time": features.observed_at.isoformat(),
        "horizon_hours": horizon_hours,
        "model_name": model_name,
        "model_version": model_version,
        "prediction_kind": prediction.model_kind,
        "label": prediction.label,
        "confidence": prediction.confidence,
        "probabilities": prediction.probabilities,
        "explanations": prediction.reasons,
        "source_freshness": {"air_quality_minutes": int((now - features.observed_at).total_seconds() // 60)},
    }


async def _execute(build) -> Any:
    return await asyncio.to_thread(lambda: build().execute())


async def _insert_new(client, table: str, rows: list[dict[str, Any]], on_conflict: str) -> int:
    """Insert rows, skipping ones already stored. Returns how many were new."""
    written = 0
    for start in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[start:start + CHUNK_SIZE]
        response = await _execute(lambda: client.table(table).upsert(chunk, on_conflict=on_conflict, ignore_duplicates=True))
        written += len(response.data or [])
    return written


async def _start_run(client, slug: str) -> str | None:
    try:
        source = await _execute(lambda: client.table("sources").select("id").eq("slug", slug).limit(1))
        source_id = source.data[0]["id"] if source.data else None
        run = await _execute(lambda: client.table("ingest_runs").insert({"source_id": source_id, "status": "started"}))
        return run.data[0]["id"]
    except Exception as error:
        logger.warning("Could not record ingest run start for %s: %s", slug, error)
        return None


async def _finish_run(client, run_id: str | None, **fields: Any) -> None:
    if run_id is None:
        return
    try:
        await _execute(lambda: client.table("ingest_runs").update({**fields, "completed_at": _utcnow().isoformat()}).eq("id", run_id))
    except Exception as error:
        logger.warning("Could not record ingest run %s completion: %s", run_id, error)


async def ingest_utah_daq() -> dict:
    client = get_service_client()
    if client is None:
        return {"source": "utah-daq", "status": "skipped", "message": "SUPABASE_URL and SUPABASE_SECRET_KEY are required for ingestion."}

    run_id = await _start_run(client, "utah-daq")
    fetched_at = _utcnow()
    results = await fetch_all_station_readings()
    readings = {station_id: result for station_id, result in results.items() if not isinstance(result, Exception)}
    failed = {station_id: str(result)[:200] for station_id, result in results.items() if isinstance(result, Exception)}
    snapshots = [row for station_readings in readings.values() for row in snapshot_rows(station_readings, fetched_at)]
    predictions = [row for station_id, rows in readings.items() if (row := prediction_row(station_id, rows, fetched_at))]
    metadata = {"stations_ok": sorted(station_id for station_id, rows in readings.items() if rows), "stations_failed": failed}

    try:
        snapshots_written = await _insert_new(client, "ml_feature_snapshots", snapshots, SNAPSHOT_CONFLICT)
        predictions_written = await _insert_new(client, "ml_predictions", predictions, PREDICTION_CONFLICT)
    except Exception as error:
        logger.exception("Utah DAQ ingest failed")
        await _finish_run(client, run_id, status="failed", records_seen=len(snapshots), error_message=str(error)[:500], metadata=metadata)
        return {"source": "utah-daq", "status": "failed", "records_seen": len(snapshots), "error": str(error)[:500], **metadata}

    metadata["predictions_written"] = predictions_written
    await _finish_run(client, run_id, status="succeeded", records_seen=len(snapshots), records_written=snapshots_written, metadata=metadata)
    return {
        "source": "utah-daq",
        "status": "succeeded",
        "records_seen": len(snapshots),
        "snapshots_written": snapshots_written,
        "predictions_written": predictions_written,
        **metadata,
    }
