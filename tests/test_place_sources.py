"""Offline tests for the places connectors (ArcGIS/UGRC, RIDB, Overpass, Google Places) and their ingest."""

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from backend import repository
from backend.config import settings
from backend.models import Coordinate, Place
from backend.providers import google_places, osm, ridb, ugrc
from backend.providers.arcgis import query_features
from backend.providers.http import UpstreamError, request_json
from backend.services import ingest
from backend.services.dashboard import place_source_health


def _client(handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


# --- shared HTTP ---------------------------------------------------------------------------------

def test_request_json_retries_transient_status_then_succeeds() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        return httpx.Response(503) if len(calls) == 1 else httpx.Response(200, json={"ok": True})

    async def run():
        async with _client(handler) as client:
            return await request_json(client, "GET", "https://example.test/x", label="Test", backoff_seconds=0)

    assert asyncio.run(run()) == {"ok": True}
    assert len(calls) == 2


def test_request_json_error_never_contains_query_string_keys() -> None:
    async def run():
        async with _client(lambda request: httpx.Response(401, text="bad key")) as client:
            await request_json(client, "GET", "https://example.test/x", label="Test", params={"apikey": "SECRET123"})

    with pytest.raises(UpstreamError) as error:
        asyncio.run(run())
    assert "401" in str(error.value)
    assert "SECRET123" not in str(error.value)


# --- ArcGIS / UGRC -------------------------------------------------------------------------------

def _trailhead(object_id: int, trailhead_id: str | None = "OV1", name: str | None = "Ensign Peak", use: str = "OVPrimaryUseHIKE") -> dict:
    return {
        "attributes": {"OBJECTID": object_id, "TrailheadID": trailhead_id, "PrimaryName": name, "Comments": use,
                       "Features": None, "PrimaryMaintenance": None, "SeasonalRestriction": None, "InfoURL": None},
        "geometry": {"x": -111.8907, "y": 40.7901},
    }


def test_query_features_follows_pagination_and_requests_wgs84() -> None:
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        offset = int(request.url.params["resultOffset"])
        if offset == 0:
            return httpx.Response(200, json={"features": [_trailhead(1), _trailhead(2)], "exceededTransferLimit": True})
        return httpx.Response(200, json={"features": [_trailhead(3)]})

    async def run():
        async with _client(handler) as client:
            return await query_features("https://example.test/FeatureServer/0/", client=client, page_size=2)

    features = asyncio.run(run())
    assert [feature["attributes"]["OBJECTID"] for feature in features] == [1, 2, 3]
    assert [params["resultOffset"] for params in seen] == ["0", "2"]
    assert seen[0]["outSR"] == "4326"


def test_query_features_raises_on_arcgis_error_envelope() -> None:
    async def run():
        async with _client(lambda request: httpx.Response(200, json={"error": {"code": 400, "message": "Invalid query"}})) as client:
            await query_features("https://example.test/FeatureServer/0", client=client)

    with pytest.raises(UpstreamError, match="Invalid query"):
        asyncio.run(run())


def test_trailhead_record_maps_primary_use_and_point() -> None:
    record = ugrc.trailhead_record(_trailhead(1, use="OVPrimaryUseMTB"))
    assert record is not None
    assert record.external_id == "OV1"
    assert record.category == "trailhead"
    assert record.description == "Mountain biking trailhead."
    assert record.tags == ["trailhead", "mountain biking"]
    assert (record.coordinate.lat, record.coordinate.lng) == (40.7901, -111.8907)


def test_trailhead_without_stable_id_or_name_is_skipped() -> None:
    assert ugrc.trailhead_record(_trailhead(1, trailhead_id=None)) is None
    assert ugrc.trailhead_record(_trailhead(1, name="  ")) is None


# --- Recreation.gov RIDB -------------------------------------------------------------------------

FACILITY = {
    "FacilityID": "232305",
    "FacilityName": "SPRUCES CAMPGROUND",
    "FacilityTypeDescription": "Campground",
    "FacilityDescription": "<h2>Overview</h2><p>Spruces sits in <b>Big Cottonwood Canyon</b> &amp; fills fast.</p>",
    "FacilityLatitude": 40.6413,
    "FacilityLongitude": -111.6403,
    "Reservable": True,
    "Enabled": True,
}


def test_facility_record_strips_html_and_links_campground() -> None:
    record = ridb.facility_record(FACILITY)
    assert record is not None
    assert record.name == "Spruces Campground"
    assert record.description == "Overview Spruces sits in Big Cottonwood Canyon & fills fast."
    assert record.source_url == "https://www.recreation.gov/camping/campgrounds/232305"
    assert record.category == "park"
    assert record.tags == ["campground", "reservable"]


def test_facility_without_location_or_disabled_is_skipped() -> None:
    assert ridb.facility_record({**FACILITY, "FacilityLatitude": 0, "FacilityLongitude": 0}) is None
    assert ridb.facility_record({**FACILITY, "Enabled": False}) is None


def test_long_facility_description_is_cut_at_a_word() -> None:
    record = ridb.facility_record({**FACILITY, "FacilityDescription": "word " * 200})
    assert record is not None
    assert len(record.description) <= ridb.DESCRIPTION_CHARS + 1
    assert record.description.endswith("word…")


def test_fetch_facilities_paginates_and_sends_key_as_header(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ridb_api_key", "RIDBKEY")
    offsets = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert "apikey" not in request.url.params
        offset = int(request.url.params["offset"])
        offsets.append(offset)
        page = [{**FACILITY, "FacilityID": str(offset + index)} for index in range(2 if offset == 0 else 1)]
        return httpx.Response(200, json={"RECDATA": page, "METADATA": {"RESULTS": {"CURRENT_COUNT": len(page), "TOTAL_COUNT": 3}}})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler), headers={"apikey": "RIDBKEY"}) as client:
            return await ridb.fetch_facilities("UT", client)

    records = asyncio.run(run())
    assert offsets == [0, 2]
    assert [record.external_id for record in records] == ["0", "1", "2"]


def test_fetch_facilities_requires_key(monkeypatch) -> None:
    monkeypatch.setattr(settings, "ridb_api_key", "")
    with pytest.raises(RuntimeError, match="RIDB_API_KEY"):
        asyncio.run(ridb.fetch_facilities())


# --- OpenStreetMap / Overpass --------------------------------------------------------------------

def test_overpass_query_is_statewide_by_default_and_point_scoped_on_request() -> None:
    statewide = osm.build_query()
    assert 'area["ISO3166-2"="US-UT"]' in statewide and "(area.utah)" in statewide
    assert '["name"]' in statewide and "out center tags;" in statewide
    nearby = osm.build_query(around=(Coordinate(lat=40.76, lng=-111.89), 5000))
    assert "(around:5000,40.76,-111.89)" in nearby and "area" not in nearby


def test_element_record_handles_nodes_and_way_centers() -> None:
    node = osm.element_record({"type": "node", "id": 7, "lat": 40.7368, "lon": -111.8632,
                               "tags": {"name": "The Whale", "tourism": "artwork", "website": "https://example.org"}})
    assert node is not None
    assert (node.category, node.external_id, node.website_url) == ("art", "node/7", "https://example.org")
    assert node.source_url == "https://www.openstreetmap.org/node/7"

    way = osm.element_record({"type": "way", "id": 9, "center": {"lat": 40.75, "lon": -111.87},
                              "tags": {"name": "Liberty Park", "leisure": "park", "addr:street": "900 S", "addr:city": "Salt Lake City"}})
    assert way is not None
    assert (way.category, way.description, way.address) == ("park", "Park", "900 S, Salt Lake City")


def test_unnamed_or_unmapped_elements_are_skipped() -> None:
    assert osm.element_record({"type": "node", "id": 1, "lat": 40, "lon": -111, "tags": {"leisure": "park"}}) is None
    assert osm.element_record({"type": "node", "id": 2, "lat": 40, "lon": -111, "tags": {"name": "Inn", "tourism": "hotel"}}) is None


def test_overpass_406_explains_the_user_agent_fix() -> None:
    async def run():
        async with _client(lambda request: httpx.Response(406, text="Not Acceptable")) as client:
            await osm.fetch_pois(client=client)

    with pytest.raises(UpstreamError, match="NWS_USER_AGENT"):
        asyncio.run(run())


# --- Google Places -------------------------------------------------------------------------------

def test_google_place_parsing_keeps_review_attribution() -> None:
    place = google_places.parse_place({
        "id": "ChIJ123",
        "displayName": {"text": "Red Butte Garden", "languageCode": "en"},
        "location": {"latitude": 40.7659, "longitude": -111.8244},
        "rating": 4.8,
        "userRatingCount": 5120,
        "googleMapsUri": "https://maps.google.com/?cid=1",
        "reviews": [{"rating": 5, "text": {"text": "Lovely."}, "relativePublishTimeDescription": "a week ago",
                     "publishTime": "2026-10-30T18:00:00Z", "authorAttribution": {"displayName": "A. Visitor", "uri": "https://maps.google.com/u/1"}}],
    })
    assert (place.name, place.rating, place.user_rating_count) == ("Red Butte Garden", 4.8, 5120)
    assert place.reviews[0].author_name == "A. Visitor"
    assert place.reviews[0].published_at == datetime(2026, 10, 30, 18, tzinfo=timezone.utc)


def test_google_places_without_key_makes_no_call(monkeypatch) -> None:
    monkeypatch.setattr(settings, "google_maps_api_key", "")
    assert asyncio.run(google_places.search_text("coffee")) == []
    assert asyncio.run(google_places.place_details("ChIJ123")) is None


# --- ingest --------------------------------------------------------------------------------------

def test_place_rows_dedupe_by_external_id_and_write_ewkt() -> None:
    first = ugrc.trailhead_record(_trailhead(1))
    renamed = ugrc.trailhead_record(_trailhead(2, name="Ensign Peak Trailhead"))
    rows = ingest.place_rows([first, renamed], "src-1", datetime(2026, 11, 7, tzinfo=timezone.utc))
    assert len(rows) == 1
    assert rows[0]["name"] == "Ensign Peak Trailhead"
    assert rows[0]["location"] == "SRID=4326;POINT(-111.8907 40.7901)"
    assert rows[0]["source_id"] == "src-1"


class _FakeSupabase:
    """Just enough of the supabase-py builder chain for ingest_places."""

    def __init__(self, source_ids: dict[str, str]):
        self.source_ids = source_ids
        self.upserts: list[tuple[str, list[dict], str]] = []
        self.runs: list[dict] = []

    def table(self, name: str):
        fake = self

        class Query:
            def __init__(self):
                self.filters = {}
                self.result = []

            def select(self, *_):
                return self

            def eq(self, column, value):
                self.filters[column] = value
                return self

            def limit(self, *_):
                return self

            def insert(self, row):
                fake.runs.append({**row})
                self.result = [{"id": f"run-{len(fake.runs)}"}]
                return self

            def update(self, fields):
                fake.runs[-1].update(fields)
                return self

            def upsert(self, rows, on_conflict, **_):
                fake.upserts.append((name, rows, on_conflict))
                return self

            def execute(self):
                if name == "sources":
                    slug = self.filters.get("slug")
                    return SimpleNamespace(data=[{"id": fake.source_ids[slug]}] if slug in fake.source_ids else [])
                return SimpleNamespace(data=self.result)

        return Query()


def test_ingest_places_upserts_by_source_and_records_the_run(monkeypatch) -> None:
    fake = _FakeSupabase({"ugrc": "ugrc-id"})
    monkeypatch.setattr(ingest, "get_service_client", lambda: fake)

    async def trailheads():
        return [ugrc.trailhead_record(_trailhead(1)), ugrc.trailhead_record(_trailhead(2, trailhead_id="OV2"))]

    monkeypatch.setitem(ingest.PLACE_SOURCES, "ugrc", trailheads)
    result = asyncio.run(ingest.ingest_places("ugrc"))
    assert result["status"] == "succeeded" and result["records_written"] == 2
    table, rows, conflict = fake.upserts[0]
    assert (table, conflict) == ("places", "source_id,external_id")
    assert {row["source_id"] for row in rows} == {"ugrc-id"}
    assert fake.runs[0]["status"] == "succeeded"


def test_ingest_places_records_upstream_failure(monkeypatch) -> None:
    fake = _FakeSupabase({"osm": "osm-id"})
    monkeypatch.setattr(ingest, "get_service_client", lambda: fake)

    async def broken():
        raise UpstreamError("Overpass: 504")

    monkeypatch.setitem(ingest.PLACE_SOURCES, "osm", broken)
    result = asyncio.run(ingest.ingest_places("osm"))
    assert result["status"] == "failed" and "504" in result["error"]
    assert fake.runs[0]["status"] == "failed"
    assert not fake.upserts


def test_ingest_places_skips_ridb_without_key_and_fails_without_sources_row(monkeypatch) -> None:
    monkeypatch.setattr(ingest, "get_service_client", lambda: _FakeSupabase({}))
    monkeypatch.setattr(settings, "ridb_api_key", "")
    assert asyncio.run(ingest.ingest_places("ridb"))["status"] == "skipped"
    result = asyncio.run(ingest.ingest_places("ugrc"))
    assert result["status"] == "failed" and "db push" in result["error"]


def test_ingest_without_supabase_is_skipped() -> None:
    assert asyncio.run(ingest.run_ingest("osm"))["status"] == "skipped"


# --- dashboard read path -------------------------------------------------------------------------

def test_load_places_calls_nearby_places_and_maps_attribution(monkeypatch) -> None:
    calls = []
    row = {"id": "6f1c", "name": "Liberty Park", "category": "park", "description": None, "source_url": "https://www.openstreetmap.org/way/9",
           "website_url": None, "tags": ["park"], "latitude": 40.745, "longitude": -111.874, "elevation_m": None,
           "updated_at": "2026-11-07T04:00:00+00:00", "source_name": "OpenStreetMap contributors", "distance_m": 1800.0}

    class Client:
        def rpc(self, function, params):
            calls.append((function, params))
            return SimpleNamespace(execute=lambda: SimpleNamespace(data=[row]))

    monkeypatch.setattr(repository, "get_public_client", lambda: Client())
    places = asyncio.run(repository.load_places(Coordinate(lat=40.76, lng=-111.89)))
    assert calls == [("nearby_places", {"lat": 40.76, "lng": -111.89, "radius_m": 40000.0, "per_category": 8})]
    assert places[0].source == "OpenStreetMap contributors" and places[0].description == ""

    drawer = place_source_health(places)
    assert [(entry.id, entry.status, entry.href) for entry in drawer] == [("osm", "cached", "https://www.openstreetmap.org/copyright")]


def test_place_source_health_ignores_sources_with_fixed_drawer_entries() -> None:
    place = Place(id="1", source="Utah Geospatial Resource Center", name="Ensign Peak", category="trailhead", description="",
                  coordinate=Coordinate(lat=40.79, lng=-111.89), updated_at=datetime.now(timezone.utc))
    assert place_source_health([place]) == []
