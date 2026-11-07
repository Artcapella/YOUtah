# Data-source integration contract

Each provider adapter is responsible for four things: fetch, validate, normalize, and return source provenance. UI components and Supabase tables consume the normalized Pydantic models only.

| Adapter | Endpoint / input | Normalized output | Required secret |
| --- | --- | --- | --- |
| `nws.py` | NWS `/points` → hourly forecast | `WeatherSnapshot`, `HourlyConditions` | None |
| `utah_daq.py` | `air.utah.gov/csvFeed.php?id=<station>` (tab-separated, newest first, `-9999` = missing, times UTC-7, wind mph) | `AirSnapshot`, `AirStationReading` → `ml_feature_snapshots` | None |
| `udot.py` | UDOT Traffic API | `Advisory`, road observations | `UDOT_API_KEY` |
| `uta.py` | GTFS static / GTFS-RT | `Advisory`, route/vehicle observations | None for documented feeds |
| `ugrc.py` | ArcGIS `UtahTrailheads/FeatureServer/0` (~570 points, ~560 with a stable `TrailheadID`) | `PlaceRecord` → `places` (`ugrc` ingest); demo `Place` fallback | None |
| `ridb.py` | Recreation.gov RIDB `/api/v1/facilities?state=UT`, 50 per page, key in the `apikey` header. Descriptions are HTML (stripped); `0,0` means no location | `PlaceRecord` → `places` (`ridb` ingest) | `RIDB_API_KEY` |
| `osm.py` | Overpass API: named parks, viewpoints, museums, art, attractions and libraries, statewide (`ISO3166-2=US-UT`) or around a point | `PlaceRecord` → `places` (`osm` ingest) | None, but a descriptive `NWS_USER_AGENT` (406 otherwise) |
| `google_places.py` | Google Places API (New) `places:searchText` and place details, with field masks | `GooglePlace`, `GoogleReview` (live only, never stored) | `GOOGLE_MAPS_API_KEY` |
| `arcgis.py` | Any ArcGIS feature layer: paginated, `outSR=4326` (Utah layers are stored in Web Mercator), HTTP-200 error envelopes raised | Raw features for an adapter to map | Per layer |
| `http.py` | Shared ingestion client: project User-Agent, retries 429/5xx with backoff (honours `Retry-After`), errors strip query strings so keys never reach logs | — | — |
| `events.py` | Ticketmaster Discovery (live), Eventbrite, Campus Connect ICS, library feeds | `LocalEvent` | `TICKETMASTER_API_KEY` |
| `scripts/backfill_aqs_history.py` | EPA AQS pre-generated hourly files (88101, TEMP, RH_DP), quality-assured. Utah DAQ sends no hourly wind to AQS for these stations | Training CSV | None |

Station metadata (`STATIONS` in `utah_daq.py`) maps each DAQ feed id to its EPA AQS site, so live readings and AQS history describe the same monitor. Coordinates come from the EPA AirNow site file and elevations from the USGS Elevation Point Query Service. The `ed` (Erda) and `hc` (Hurricane) feeds returned 404 in November 2026. Ingest reports them under `stations_failed` and the brief skips them.

## Ingestion procedure

1. Add parsing and Pydantic validation to the relevant adapter.
2. Add an entry to `sources`, including attribution and refresh interval.
3. Record one `ingest_runs` row before calling the provider.
4. Upsert by `(source_id, external_id)` into the matching normalized table.
5. Preserve `source_url`, `raw_payload`, and `updated_at`.
6. Mark the ingest run succeeded or failed. Never hide a source failure from the dashboard status UI.

Places sources follow this procedure through `ingest_places` in `backend/services/ingest.py`. A new places source needs only a fetcher that returns `PlaceRecord`s, an entry in `PLACE_SOURCES` and a `sources` row.

### Places source terms

- **OpenStreetMap** (ODbL): show "© OpenStreetMap contributors" wherever the places appear. The dashboard adds this source to the source drawer whenever it serves OSM rows. Overpass is a shared, rate-limited service, so call it only from scheduled ingestion.
- **Google Places**: Google Maps Platform terms allow storing only `place_id`. Fetch everything else live, show review author attribution and Google attribution, and refresh rather than cache. A public route needs rate limiting first, because every call is billed.
- **UGRC / RIDB**: public data. Keep `source_url` and the `sources` attribution.

## Escape the Inversion inputs

- Utah DAQ current AQI/PM2.5: valley condition.
- Utah DAQ ceilometer and/or mountain observations: estimated cap height.
- UGRC trailheads: trail locations.
- USGS elevation point query: elevation at each trailhead.
- UDOT: canyon road closures and weather conditions.
- UTA GTFS-RT: transit access and disruptions.

Expose the result as an estimate with timestamp and official-source links, not a safety guarantee.
