from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import app
from backend.config import settings
from backend.providers.utah_daq import STATIONS_BY_ID
from backend.services.ingest import prediction_row, snapshot_rows
from backend.models import AirStationReading


@pytest.fixture(autouse=True)
def _no_artifact(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "outdoor_comfort_model_path", str(tmp_path / "absent.joblib"))


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def readings(now: datetime, hours: int = 6) -> list[AirStationReading]:
    return [
        AirStationReading(station_id="slc", observed_at=now - timedelta(hours=h), pm25=20.0, pm25_24hr_avg=18.0 - h * 0.5,
                          temp_f=35, humidity=70, wind_mph=1.5)
        for h in range(hours)
    ]


def test_comfort_endpoint_scores_supplied_features(client) -> None:
    response = client.post("/api/intelligence/comfort", json={
        "pm25_24hr_avg": 48, "temp_f": 30, "humidity": 80, "wind_mph": 1, "observed_at": "2026-01-10T15:00:00Z",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["label"] == "poor"
    assert body["model_kind"] == "conservative_fallback"


def test_model_status_explains_fallback(client) -> None:
    body = client.get("/api/intelligence/model-status").json()
    assert body["status"] == "fallback"
    assert body["reason"]


def test_feedback_is_validated_and_not_stored_without_service_key(client) -> None:
    ok = client.post("/api/intelligence/feedback", json={
        "entity_id": "red-butte", "entity_type": "place", "event_type": "detail_opened", "occurred_at": "2026-11-06T20:00:00Z",
    })
    assert ok.status_code == 202
    assert ok.json() == {"accepted": True, "stored": False, "entity_id": "red-butte"}
    bad = client.post("/api/intelligence/feedback", json={
        "entity_id": "x", "entity_type": "user", "event_type": "detail_opened", "occurred_at": "2026-11-06T20:00:00Z",
    })
    assert bad.status_code == 422


def test_brief_uses_nearest_station_and_never_scores_demo_events(client, monkeypatch) -> None:
    now = datetime.now(timezone.utc).replace(minute=0, second=0, microsecond=0)

    async def nearest(point, **_):
        return STATIONS_BY_ID["slc"], 3.2, readings(now)

    async def no_weather(point):
        raise RuntimeError("NWS down")

    monkeypatch.setattr("backend.intelligence.service.fetch_nearest_readings", nearest)
    monkeypatch.setattr("backend.intelligence.service.fetch_hourly_conditions", no_weather)
    monkeypatch.setattr(settings, "ticketmaster_api_key", "")

    body = client.get("/api/intelligence/brief?lat=40.7608&lng=-111.8910").json()
    assert body["air_station"]["id"] == "slc"
    assert body["outdoor_comfort"]["model_kind"] == "conservative_fallback"
    assert body["outdoor_comfort"]["label"] == "poor"  # caution band + very light wind
    assert body["events_source"] == "not_configured"
    assert body["local_pulse"]["nearby_event_count"] == 0
    assert "no live event feed" in body["local_pulse"]["drivers"][0]
    assert body["data_freshness"]["weather_minutes"] is None


def test_brief_returns_502_when_no_station_has_data(client, monkeypatch) -> None:
    async def nothing(point, **_):
        raise LookupError("no data")

    monkeypatch.setattr("backend.intelligence.service.fetch_nearest_readings", nothing)
    assert client.get("/api/intelligence/brief").status_code == 502


def test_cron_route_requires_bearer_secret(client, monkeypatch) -> None:
    monkeypatch.setattr(settings, "cron_secret", "s3cret")
    assert client.get("/api/cron/ingest").status_code == 401
    assert client.get("/api/cron/ingest", headers={"Authorization": "Bearer wrong"}).status_code == 401
    skipped = client.get("/api/cron/ingest", headers={"Authorization": "Bearer s3cret"}).json()
    assert skipped["status"] == "skipped"  # no Supabase service client in tests


def test_ingest_rows_carry_lineage() -> None:
    now = datetime(2026, 11, 7, 3, tzinfo=timezone.utc)
    rows = snapshot_rows(readings(now, hours=2), available_at=now)
    assert rows[0]["entity_type"] == "station"
    assert rows[0]["prediction_time"] == now.isoformat()
    assert rows[0]["available_at"] == now.isoformat()
    assert rows[0]["features"]["elevation_m"] == STATIONS_BY_ID["slc"].elevation_m

    prediction = prediction_row("slc", readings(now), now + timedelta(minutes=50))
    assert prediction["prediction_kind"] == "conservative_fallback"
    assert prediction["model_version"] == "rules-v1"
    assert prediction["source_freshness"] == {"air_quality_minutes": 50}
