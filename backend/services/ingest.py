"""Ingestion jobs. Provider-specific parsing stays in backend/providers; this module owns
the Supabase write lifecycle (ingest_runs bookkeeping, idempotent upserts)."""

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone
from typing import Any

from postgrest.types import ReturnMethod

from backend.config import settings
from backend.intelligence.features import STATION_READING_SCHEMA_VERSION
from backend.intelligence.model import current_model_identity, predict_comfort
from backend.intelligence.service import comfort_features_from_readings
from backend.models import Advisory, AirStationReading, Coordinate, LocalEvent, PlaceRecord
from backend.providers.events import fetch_ticketmaster_events
from backend.providers.osm import fetch_pois
from backend.providers.ridb import fetch_facilities
from backend.providers.ugrc import fetch_trailheads
from backend.providers.udot import fetch_advisories
from backend.providers.uta import fetch_transit_alerts
from backend.providers.utah_daq import STATIONS_BY_ID, fetch_all_station_readings
from backend.supabase_client import get_service_client

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500
SNAPSHOT_CONFLICT = "entity_type,entity_id,prediction_time,feature_schema_version"
PREDICTION_CONFLICT = "entity_type,entity_id,prediction_time,horizon_hours,model_name,model_version"
PLACE_CONFLICT = "source_id,external_id"
ADVISORY_CONFLICT = "source_id,external_id"
EVENT_CONFLICT = "source_id,external_id"

# Places sources, keyed by their `sources.slug`. Each fetcher returns statewide records.
PLACE_SOURCES: dict[str, Callable[[], Awaitable[list[PlaceRecord]]]] = {
    "ugrc": fetch_trailheads,
    "ridb": fetch_facilities,
    "osm": fetch_pois,
}
# Sources that cannot run without a key: slug -> (setting value getter, env var name).
_REQUIRED_KEYS = {"ridb": (lambda: settings.ridb_api_key, "RIDB_API_KEY")}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def run_ingest(source: str) -> dict:
    if source == "utah-daq":
        return await ingest_utah_daq()
    if source in PLACE_SOURCES:
        return await ingest_places(source)
    if source == "udot":
        return await ingest_advisories("udot", fetch_advisories)
    if source == "uta":
        return await ingest_advisories("uta", fetch_transit_alerts)
    if source == "events":
        return await ingest_events()
    return {"source": source, "status": "unsupported", "message": "No configured ingestion adapter for this source."}


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


async def _upsert(client, table: str, rows: list[dict[str, Any]], on_conflict: str) -> int:
    """Insert or update rows by their conflict key. Returns how many were sent."""
    for start in range(0, len(rows), CHUNK_SIZE):
        chunk = rows[start:start + CHUNK_SIZE]
        await _execute(lambda: client.table(table).upsert(chunk, on_conflict=on_conflict, returning=ReturnMethod.minimal))
    return len(rows)


async def _source_id(client, slug: str) -> str | None:
    try:
        source = await _execute(lambda: client.table("sources").select("id").eq("slug", slug).limit(1))
    except Exception as error:
        logger.warning("Could not look up source %s: %s", slug, error)
        return None
    return source.data[0]["id"] if source.data else None


async def _start_run(client, slug: str, source_id: str | None = None) -> str | None:
    try:
        if source_id is None:
            source_id = await _source_id(client, slug)
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


def place_rows(records: list[PlaceRecord], source_id: str, fetched_at: datetime) -> list[dict[str, Any]]:
    """``places`` rows for one source, one per external_id.

    Postgres refuses an upsert batch that touches the same conflict key twice, so a
    record repeated upstream keeps only its last occurrence.
    """
    rows: dict[str, dict[str, Any]] = {}
    for record in records:
        lat, lng = round(record.coordinate.lat, 6), round(record.coordinate.lng, 6)
        rows[record.external_id] = {
            "source_id": source_id,
            "external_id": record.external_id,
            "name": record.name,
            "category": record.category,
            "description": record.description or None,
            "address": record.address,
            "website_url": record.website_url,
            "source_url": record.source_url,
            "tags": record.tags,
            "latitude": lat,
            "longitude": lng,
            # EWKT text: PostgREST casts it into the PostGIS geography column.
            "location": f"SRID=4326;POINT({lng} {lat})",
            "raw_payload": record.raw_payload,
            "updated_at": fetched_at.isoformat(),
        }
    return list(rows.values())


async def ingest_places(slug: str) -> dict:
    """Fetch one places source statewide and upsert it into ``places`` by (source_id, external_id).

    Rows that disappear upstream are kept; nothing is deleted.
    """
    fetch = PLACE_SOURCES[slug]
    client = get_service_client()
    if client is None:
        return {"source": slug, "status": "skipped", "message": "SUPABASE_URL and SUPABASE_SECRET_KEY are required for ingestion."}
    if slug in _REQUIRED_KEYS:
        key, env_name = _REQUIRED_KEYS[slug]
        if not key():
            return {"source": slug, "status": "skipped", "message": f"{env_name} is required for this source."}

    source_id = await _source_id(client, slug)
    if source_id is None:
        return {
            "source": slug,
            "status": "failed",
            "error": f"No `sources` row with slug '{slug}'. Apply supabase/migrations first (npx supabase db push).",
        }

    run_id = await _start_run(client, slug, source_id)
    fetched_at = _utcnow()
    records: list[PlaceRecord] = []
    try:
        records = await fetch()
        written = await _upsert(client, "places", place_rows(records, source_id, fetched_at), PLACE_CONFLICT)
    except Exception as error:
        logger.exception("Places ingest for %s failed", slug)
        await _finish_run(client, run_id, status="failed", records_seen=len(records), error_message=str(error)[:500])
        return {"source": slug, "status": "failed", "records_seen": len(records), "error": str(error)[:500]}

    await _finish_run(client, run_id, status="succeeded", records_seen=len(records), records_written=written)
    return {"source": slug, "status": "succeeded", "records_seen": len(records), "records_written": written}


async def ingest_all_places() -> dict:
    """Every places source in turn (scheduled weekly). One source failing does not stop the others."""
    results = [await ingest_places(slug) for slug in PLACE_SOURCES]
    failed = [result["source"] for result in results if result["status"] == "failed"]
    return {"source": "places", "status": "failed" if failed else "succeeded", "failed": failed, "results": results}


def advisory_rows(records: list[Advisory], source_id: str, fetched_at: datetime) -> list[dict[str, Any]]:
    return [{
        "source_id": source_id, "external_id": record.id, "kind": record.kind, "severity": record.severity,
        "title": record.title, "body": record.body, "source_url": record.source_url,
        "raw_payload": record.model_dump(mode="json"), "updated_at": fetched_at.isoformat(),
    } for record in records]


async def ingest_advisories(slug: str, fetch: Callable[[], Awaitable[list[Advisory]]]) -> dict:
    """Persist UDOT/UTA current alerts. Upsert makes frequent cron runs idempotent."""
    client = get_service_client()
    if client is None:
        return {"source": slug, "status": "skipped", "message": "SUPABASE_URL and SUPABASE_SECRET_KEY are required for ingestion."}
    source_id = await _source_id(client, slug)
    if source_id is None:
        return {"source": slug, "status": "failed", "error": f"No sources row for '{slug}'. Apply migrations first."}
    run_id = await _start_run(client, slug, source_id)
    records: list[Advisory] = []
    try:
        records = await fetch()
        written = await _upsert(client, "advisories", advisory_rows(records, source_id, _utcnow()), ADVISORY_CONFLICT)
    except Exception as error:
        await _finish_run(client, run_id, status="failed", records_seen=len(records), error_message=str(error)[:500])
        return {"source": slug, "status": "failed", "records_seen": len(records), "error": str(error)[:500]}
    await _finish_run(client, run_id, status="succeeded", records_seen=len(records), records_written=written)
    return {"source": slug, "status": "succeeded", "records_seen": len(records), "records_written": written}


def event_rows(records: list[LocalEvent], source_id: str, fetched_at: datetime) -> list[dict[str, Any]]:
    return [{
        "source_id": source_id, "external_id": record.id, "title": record.title, "category": record.category,
        "starts_at": record.starts_at.isoformat(), "is_free": record.is_free, "ticket_url": record.source_url,
        "source_url": record.source_url, "latitude": record.coordinate.lat, "longitude": record.coordinate.lng,
        "raw_payload": record.model_dump(mode="json"), "updated_at": fetched_at.isoformat(),
    } for record in records]


async def ingest_events(point: Coordinate = Coordinate(lat=40.7608, lng=-111.8910)) -> dict:
    client = get_service_client()
    if client is None:
        return {"source": "events", "status": "skipped", "message": "SUPABASE_URL and SUPABASE_SECRET_KEY are required for ingestion."}
    if not settings.ticketmaster_api_key:
        return {"source": "events", "status": "skipped", "message": "TICKETMASTER_API_KEY is required for event ingestion."}
    source_id = await _source_id(client, "ticketmaster")
    if source_id is None:
        return {"source": "events", "status": "failed", "error": "No sources row for 'ticketmaster'. Apply migrations first."}
    run_id = await _start_run(client, "ticketmaster", source_id)
    records: list[LocalEvent] = []
    try:
        records = await fetch_ticketmaster_events(point)
        written = await _upsert(client, "events", event_rows(records, source_id, _utcnow()), EVENT_CONFLICT)
    except Exception as error:
        await _finish_run(client, run_id, status="failed", records_seen=len(records), error_message=str(error)[:500])
        return {"source": "events", "status": "failed", "records_seen": len(records), "error": str(error)[:500]}
    await _finish_run(client, run_id, status="succeeded", records_seen=len(records), records_written=written)
    return {"source": "events", "status": "succeeded", "records_seen": len(records), "records_written": written}
