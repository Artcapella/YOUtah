# Data-source integration contract

Each provider adapter is responsible for four things: fetch, validate, normalize, and return source provenance. UI components and Supabase tables consume the normalized Pydantic models only.

| Adapter | Endpoint / input | Normalized output | Required secret |
| --- | --- | --- | --- |
| `nws.py` | NWS `/points` → hourly forecast | `WeatherSnapshot` | None |
| `utah_daq.py` | Utah DAQ air API + ceilometer | `AirSnapshot`, `observations` | None or provider key if required |
| `udot.py` | UDOT Traffic API | `Advisory`, road observations | `UDOT_API_KEY` |
| `uta.py` | GTFS static / GTFS-RT | `Advisory`, route/vehicle observations | None for documented feeds |
| `ugrc.py` | Trailhead, trails, library, open-source place services | `Place` | None / API key for geocoding |
| `events.py` | Ticketmaster, Eventbrite, Campus Connect ICS, library feeds | `LocalEvent` | provider keys as needed |

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
