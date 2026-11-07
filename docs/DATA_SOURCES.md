# Data-source integration contract

Each provider adapter is responsible for four things: fetch, validate, normalize, and return source provenance. UI components and Supabase tables consume the normalized Pydantic models only.

| Adapter | Endpoint / input | Normalized output | Required secret |
| --- | --- | --- | --- |
| `nws.py` | NWS `/points` → hourly forecast | `WeatherSnapshot`, `HourlyConditions` | None |
| `utah_daq.py` | `air.utah.gov/csvFeed.php?id=<station>` (tab-separated, newest first, `-9999` = missing, times UTC-7, wind mph) | `AirSnapshot`, `AirStationReading` → `ml_feature_snapshots` | None |
| `udot.py` | UDOT Traffic API | `Advisory`, road observations | `UDOT_API_KEY` |
| `uta.py` | GTFS static / GTFS-RT | `Advisory`, route/vehicle observations | None for documented feeds |
| `ugrc.py` | Trailhead, trails, library, open-source place services | `Place` | None / API key for geocoding |
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

## Escape the Inversion inputs

- Utah DAQ current AQI/PM2.5: valley condition.
- Utah DAQ ceilometer and/or mountain observations: estimated cap height.
- UGRC trailheads: trail locations.
- USGS elevation point query: elevation at each trailhead.
- UDOT: canyon road closures and weather conditions.
- UTA GTFS-RT: transit access and disruptions.

Expose the result as an estimate with timestamp and official-source links, not a safety guarantee.
