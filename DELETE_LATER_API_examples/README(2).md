# Comprehensive Utah data connectors

Drop `utah_data_connectors.py` into the FastAPI backend (for example, `app/services/`). Copy `.env.example` to `.env`, fill in keys, and load the functions from a scheduled ingestion worker rather than a request handler.

```python
from utah_data_connectors import DataClient, recreation_gov_facilities, weather_forecast

client = DataClient()
facilities = recreation_gov_facilities(client, state="UT")
forecast = weather_forecast(client, 40.7608, -111.8910)
```

## Sources covered

| Source | Function | Auth | Notes |
| --- | --- | --- | --- |
| Recreation.gov RIDB | `recreation_gov_facilities` | `RIDB_API_KEY` | Facilities/campgrounds; paginate before production use. |
| Weather.gov/NWS | `weather_forecast` | None | Supply a genuine user agent/contact before deployment; cache forecasts. |
| Google Maps/Places | `google_places_text_search`, `google_place_reviews` | `GOOGLE_MAPS_API_KEY` | Uses authorized Places API (New), not HTML scraping. Enable billing and the Places API. |
| UDOT Open Data/ArcGIS | `udot_arcgis_features` | Usually none | Pass a verified public FeatureServer layer URL from the UDOT GIS/Open Data catalog. Schemas vary per layer. |
| OpenStreetMap | `overpass_pois` | None | Optional secondary POI source; respect Overpass usage limits and attribution. |

## Recommended ingestion shape

1. Run sources on a schedule (Weather: hourly; road conditions: 5–15 min; facilities/places: daily or weekly).
2. Preserve `source`, source-native ID, retrieval time, raw JSON, and a normalized record. Upsert by `(source, external_id)`.
3. Geocode/normalize into a single `places` table, then create a `place_sources` table for per-source ratings, links, and freshness.
4. Do not treat Google review data as durable owned content: follow current Google Maps Platform terms, attribution, display, refresh, and caching requirements.

The snippet intentionally keeps UDOT generic: its public ArcGIS layer IDs and field names change by dataset. Confirm a layer's metadata and license before wiring it to a production job.
