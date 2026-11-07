import asyncio
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path

from fastapi.testclient import TestClient
import httpx
import pytest

from app import app
from backend.ski import service
from backend.ski.catalog import RESORTS

NOW = datetime(2026, 11, 7, 17, 30, tzinfo=timezone.utc)
BY_ID = {resort["id"]: resort for resort in RESORTS}


@pytest.fixture(autouse=True)
def clean_ski_cache(monkeypatch):
    monkeypatch.setattr(service, "_cache", {})
    monkeypatch.setattr(service, "_pending", {})
    monkeypatch.setattr(service, "_retry_after", {})
    monkeypatch.setattr(service, "_now", lambda: NOW)


def snowbird_lift(name="Tram", status="open", seconds=0, updated=None):
    return {"name": name, "status": status, "waitTime": {
        "waitTimeSecs": seconds, "waitTimeUpdated": (updated or NOW).isoformat(),
    }}


def forecast_data():
    anchor = NOW.replace(minute=0)
    return {
        "hourly_units": {"snowfall": "cm", "snow_depth": "m", "temperature_2m": "°F", "wind_speed_10m": "mp/h"},
        "hourly": {
            "time": [int((anchor + timedelta(hours=i)).timestamp()) for i in range(-24, 49)],
            "snowfall": [2.54] * 73, "snow_depth": [.254] * 73,
            "temperature_2m": [-5] * 73, "wind_speed_10m": [12] * 73,
        },
    }


@pytest.mark.parametrize("value", [None, "--", "N/A", "1-3", "NaN", float("inf"), -1, True])
def test_missing_and_invalid_measurements_stay_missing(value):
    assert service.number(value) is None


def test_zero_wait_is_valid_only_on_an_open_lift_with_a_fresh_timestamp():
    rows = [snowbird_lift(), snowbird_lift("Closed", "closed", 120),
            snowbird_lift("Stale", updated=NOW - timedelta(minutes=16)),
            snowbird_lift("Future", updated=NOW + timedelta(hours=1))]
    op = service.parse_operations(BY_ID["snowbird"], service.Feed(rows, NOW), NOW)
    assert op.reporting_lifts == 1
    assert op.wait_minutes == 0
    assert [lift.wait_minutes for lift in op.lifts] == [0, None, None, None]
    assert service.busyness(op).level == "quiet"
    assert "1 of 3" in service.busyness(op).description


def test_closed_resort_is_not_a_zero_minute_wait_or_a_quiet_crowd():
    op = service.parse_operations(BY_ID["snowbird"], service.Feed([snowbird_lift(status="closed")], NOW), NOW)
    assert op.wait_minutes is None
    assert op.wait_status == "no_open_lifts"
    assert service.busyness(op).level == "no_open_lifts"


def test_partial_wait_coverage_is_described_and_busyness_uses_median():
    rows = [snowbird_lift("A", seconds=600), snowbird_lift("B", seconds=1200), snowbird_lift("C", seconds=None)]
    op = service.parse_operations(BY_ID["snowbird"], service.Feed(rows, NOW), NOW)
    assert op.wait_minutes == 15
    assert op.max_wait_minutes == 20
    assert service.busyness(op).level == "busy"
    assert "2 of 3" in service.busyness(op).description


def test_outage_cached_lifts_never_supply_live_wait_times():
    op = service.parse_operations(BY_ID["snowbird"], service.Feed([snowbird_lift(seconds=420)], NOW, stale=True), NOW)
    assert op.source.status == "stale"
    assert op.wait_minutes is None
    assert op.wait_status == "stale"
    assert service.busyness(op).level == "unknown"


def test_park_city_uses_iso_feed_and_not_the_legacy_numeric_status_feed():
    legacy = {"Date": "/Date(123)/", "Lifts": [{"Name": "Old", "Status": 1}]}
    current = {"Date": NOW.isoformat(), "Lifts": [{"Name": "Payday", "Status": "Open", "WaitTimeInMinutes": 7}]}
    html = f"<script>FR.TerrainStatusFeed = {json.dumps(legacy)};</script><script>FR.TerrainStatusFeed = {json.dumps(current)};</script>"
    payload = service._embedded_json(html, "FR.TerrainStatusFeed")
    op = service.parse_operations(BY_ID["pcm"], service.Feed(payload, NOW), NOW)
    assert op.lifts[0].name == "Payday"
    assert op.wait_minutes == 7


def test_an_empty_offseason_board_stays_unknown():
    op = service.parse_operations(BY_ID["pcm"], service.Feed({"Date": NOW.isoformat(), "Lifts": []}, NOW), NOW)
    assert op.lifts_open is None and op.lifts_total is None
    assert service.busyness(op).level == "unknown"


def test_old_source_report_does_not_become_fresh_when_retrieved_again():
    data = {"Date": (NOW - timedelta(days=2)).isoformat(), "Lifts": [{"Name": "Payday", "Status": "Open", "WaitTimeInMinutes": 7}]}
    op = service.parse_operations(BY_ID["pcm"], service.Feed(data, NOW), NOW)
    assert op.source.status == "stale" and op.wait_minutes is None


def test_reported_snow_preserves_zero_and_utah_time_zone():
    report = service.parse_snow_report(BY_ID["alta"], service.Feed({"conditions": {
        "dated_at": "2026-11-07 09:00:00", "last24": "0.0", "base_depth": "18",
    }}, NOW), NOW)
    assert report.snowfall_24h_in == 0 and report.base_depth_in == 18
    assert report.source.reported_at == datetime(2026, 11, 7, 16, tzinfo=timezone.utc)


def test_full_mountain_report_keeps_run_status_and_deduplicates_lifts():
    lift = {"Name": "Moonbeam", "StatusEnglish": "open"}
    data = {"LastUpdate": NOW.isoformat(), "SnowReport": {"TotalOpenTrails": 0, "TotalTrails": 82},
            "MountainAreas": [{"Lifts": [lift], "Trails": [{"Name": "Easy Street", "StatusEnglish": "closed", "Grooming": "No"}]}, {"Lifts": [lift]}]}
    op = service.parse_operations(BY_ID["solitude"], service.Feed(data, NOW), NOW)
    assert op.lifts_total == 1 and op.trails_open == 0
    assert op.runs[0].status == "closed" and op.runs[0].groomed is False


def test_snow_uses_complete_rolling_hours_and_correct_unit_conversions():
    data = forecast_data()
    forecast = service.parse_forecast(data, service.Feed([data], NOW), NOW)
    assert forecast.modeled is True
    assert forecast.snowfall_24h_in == 24
    assert forecast.snowfall_next_24h_in == 24
    assert forecast.snowfall_next_48h_in == 48
    assert forecast.snow_depth_in == 10
    assert forecast.temperature_f == -5 and forecast.wind_mph == 12


def test_missing_snow_hours_are_not_reported_as_zero():
    data = forecast_data()
    data["hourly"]["snowfall"][10] = None
    forecast = service.parse_forecast(data, service.Feed([data], NOW), NOW)
    assert forecast.snowfall_24h_in is None
    assert forecast.snowfall_next_24h_in == 24
    data["hourly_units"]["snowfall"] = "inch"
    assert service.parse_forecast(data, service.Feed([data], NOW), NOW).snowfall_next_24h_in is None


def test_public_api_retains_resort_pins_when_every_upstream_is_down(monkeypatch):
    async def unavailable(*args, **kwargs):
        return service.Feed()
    monkeypatch.setattr(service, "fetch_feed", unavailable)
    monkeypatch.setattr(service.settings, "open_meteo_api_key", "private-ski-key")
    response = TestClient(app).get("/api/ski")
    assert response.status_code == 200
    data = response.json()
    assert len(data["resorts"]) == len(RESORTS)
    assert "private-ski-key" not in response.text
    assert all(r["operations"]["wait_minutes"] is None and r["busyness"]["level"] == "unknown" for r in data["resorts"])
    assert all(r["coordinate"]["lat"] and r["website"].startswith("https://") for r in data["resorts"])


def test_one_malformed_resort_cannot_break_the_other_resorts(monkeypatch):
    async def fetch(key, *args, **kwargs):
        if key == "solitude":
            return service.Feed({"MountainAreas": [None]}, NOW)
        if key == "snowbird":
            return service.Feed([snowbird_lift(seconds=600)], NOW)
        return service.Feed()
    monkeypatch.setattr(service, "fetch_feed", fetch)
    data = asyncio.run(service.ski_conditions())
    by_id = {r.id: r for r in data.resorts}
    assert by_id["solitude"].operations.source.status == "unavailable"
    assert by_id["snowbird"].operations.wait_minutes == 10


def test_request_cache_coalesces_calls_and_keeps_original_freshness(monkeypatch):
    calls = []
    async def request(*args):
        calls.append(args)
        await asyncio.sleep(0)
        feed = service.Feed([snowbird_lift()], NOW)
        service._cache["snowbird"] = feed
        return feed
    monkeypatch.setattr(service, "_request_feed", request)
    async def run():
        first, second = await asyncio.gather(service.fetch_feed("snowbird", "https://example.test"), service.fetch_feed("snowbird", "https://example.test"))
        third = await service.fetch_feed("snowbird", "https://example.test")
        assert first.checked_at == second.checked_at == third.checked_at == NOW
        assert third.cached
    asyncio.run(run())
    assert len(calls) == 1


def test_http_failure_uses_old_payload_with_its_old_timestamp_and_backs_off(monkeypatch):
    old = NOW - timedelta(minutes=7)
    service._cache["snowbird"] = service.Feed([snowbird_lift()], old)
    class BrokenClient:
        async def __aenter__(self): return self
        async def __aexit__(self, *args): pass
        async def get(self, *args, **kwargs): raise httpx.ConnectError("offline")
    monkeypatch.setattr(service.httpx, "AsyncClient", lambda **kwargs: BrokenClient())
    feed = asyncio.run(service.fetch_feed("snowbird", "https://example.test"))
    assert feed.stale and feed.checked_at == old
    assert "snowbird" in service._retry_after
    service._cache["snowbird"].checked_at = NOW - timedelta(hours=2)
    assert asyncio.run(service.fetch_feed("snowbird", "https://example.test")).data is None


def test_shipped_map_terrain_contains_real_named_lifts_and_runs_near_every_resort():
    terrain = json.loads((Path(__file__).resolve().parents[1] / "public/assets/ski-terrain.geojson").read_text(encoding="utf-8"))
    assert terrain["metadata"]["license"] == "ODbL-1.0"
    assert len(terrain["features"]) > 2000
    assert any(f["properties"]["kind"] == "lift" and "Collins" in f["properties"]["name"] for f in terrain["features"])
    points = []
    for feature in terrain["features"]:
        assert feature["properties"]["osm_id"]
        assert feature["properties"].get("lift_type") not in ("zip_line", "mining_tram", "station")
        coords = feature["geometry"]["coordinates"]
        points.extend(coords[0] if feature["geometry"]["type"] == "Polygon" else coords)
    for resort in RESORTS:
        assert any(abs(lng - resort["lng"]) < .08 and abs(lat - resort["lat"]) < .08 for lng, lat in points), resort["name"]
