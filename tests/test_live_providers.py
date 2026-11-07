from fastapi.testclient import TestClient

from backend.models import AirStationReading
from backend.providers.udot import parse_advisories
from backend.providers.utah_daq import STATIONS_BY_ID, estimate_inversion_height_m


def test_udot_alerts_are_normalized_without_demo_fallback() -> None:
    advisories = parse_advisories({"Alerts": [{
        "Id": 42, "Message": "SR-210 restriction", "Notes": "Chains required", "HighImportance": True,
    }]})
    assert len(advisories) == 1
    assert advisories[0].id == "udot-42"
    assert advisories[0].severity == "high"
    assert advisories[0].body == "Chains required"


def test_inversion_estimate_changes_with_local_wind_and_pm25() -> None:
    station = STATIONS_BY_ID["slc"]
    stagnant = AirStationReading(station_id="slc", observed_at="2026-11-07T00:00:00Z", pm25_24hr_avg=45, wind_mph=1)
    mixed = AirStationReading(station_id="slc", observed_at="2026-11-07T00:00:00Z", pm25_24hr_avg=5, wind_mph=18)
    assert estimate_inversion_height_m(station, stagnant) > estimate_inversion_height_m(station, mixed)
    assert estimate_inversion_height_m(station, mixed) > station.elevation_m


def test_map_data_is_a_public_api_contract(monkeypatch) -> None:
    async def dashboard(_):
        from backend.seed_data import demo_air, demo_events, demo_places, demo_sources, utcnow
        from backend.models import DashboardResponse
        return DashboardResponse(generated_at=utcnow(), weather=__import__("backend.seed_data", fromlist=["demo_weather"]).demo_weather(),
                                 air=demo_air(), places=demo_places(), events=demo_events(), advisories=[], sources=demo_sources())
    monkeypatch.setattr("app.build_dashboard", dashboard)
    from app import app
    response = TestClient(app).get("/api/map-data")
    assert response.status_code == 200
    assert set(response.json()) == {"generated_at", "air", "sources", "places", "events", "advisories"}
