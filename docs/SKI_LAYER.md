# Snow & skiing map layer

Both `/map/simple?ski=1` and `/map?ski=1` consume `GET /api/ski` and the same map terrain asset. The simple map has a prominent layer switch, searchable resort comparison, a scrollable resort detail dialog, and a save-to-stops action. The advanced map integrates the same reports into its Conditions layer, selection sheet and trip plan. Ski ticket prices are unknown and explicitly excluded from the advanced plan's estimated total.

The map covers 15 Utah resorts. Resort labels show new snow for the past 24 hours. Runs, lift lines and their names can be toggled separately. Run colors indicate mapped difficulty, including gray for unknown ratings; they do not indicate that terrain is open. Tapping a line displays its name, mapped difficulty/type, length, OSM source and map date. An unambiguous nearby name match adds the current resort-reported lift/run status and a fresh wait when available. Source timestamps and an older-report label accompany operational data.

## Connected sources

All requests for conditions run server-side. No new key is required for development.

| Source | Connected fields | Notes |
| --- | --- | --- |
| [Open-Meteo forecast API](https://open-meteo.com/en/docs) | Rolling 24-hour modeled snowfall, next 24/48-hour snowfall, modeled snow cover, base-area temperature and wind at all 15 resorts | One batch request with explicit resort elevations. Snowfall cm → inches; snow cover m → inches. A complete hourly window is required; missing values do not become zero. Model output is labeled separately from resort reports. Attribution: Open-Meteo, CC BY 4.0. |
| [Alta](https://www.alta.com/lift-terrain-status) | Official snow/base report, lift status and open-run totals | Decode the page's `window.Alta` JSON without evaluating JavaScript. Local report times use America/Denver. |
| [Snowbird](https://www.snowbird.com/the-mountain/mountain-report/lift-trail-report/) | Individual lifts, runs, grooming and published lift waits | Public resort feeds: `https://api.snowbird.com/api/v1/dor/drupal/lifts` and `/api/v1/dor/drupal/trails`. `waitTimeSecs` has its own `waitTimeUpdated` timestamp. |
| [Brighton](https://www.brightonresort.com/conditions) | Individual lift status | Resort's public Boyne feed: `https://apim-marketing-001.azure-api.net/FeedService/v1/Feed/Facilities/Areas/Lifts/All?resortName=br`. Undocumented wait units are not interpreted. |
| [Solitude](https://www.solitudemountain.com/mountain-and-village/conditions-and-maps) and [Deer Valley](https://www.deervalley.com/explore-the-mountain/mountain-report) | Official snow/base reports, lift/run status, grooming and run totals | Their MtnPowder feeds: `https://mtnpowder.com/feed?resortId=65` and `?resortId=49`. Keep the source's actual report timestamp. |
| [Park City Mountain](https://www.parkcitymountain.com/the-mountain/mountain-conditions/terrain-and-lift-status.aspx) | Individual lifts and published waits in minutes | Decode ISO-dated `FR.TerrainStatusFeed` JSON. Skip the legacy numeric-status feed; an empty off-season board remains unknown. |
| [OpenStreetMap](https://www.openstreetmap.org/copyright), through [Overpass](https://wiki.openstreetmap.org/wiki/Overpass_API) | 2,246 run segments and 204 ski lifts in the shipped geography extract | Derived database under ODbL-1.0. Attribution appears in both UI and map control. The extract's source data date is June 1, 2026 and is shown in feature details. It is geography, not real-time opening status. |

The other nine resorts retain pins, forecasts, terrain and official report links when no operations adapter exists. They show unavailable operational values. There are no synthetic wait times, crowds, snow reports, ticket prices or opening-hour assumptions. Eagle Point's catalog description includes its dated [official 2026–27 season-closure notice](https://www.eaglepointresort.com/eagle-point-resort-announces-2026-27-winter-season-closure-following-cottonwood-fire/).

## Freshness and failure handling

- Reports are fetched concurrently, with an eight-second total request deadline per source. Browsers refresh every five minutes while the ski layer is active and the page is visible. The refresh button uses the same cached endpoint.
- Server responses are cached in memory for five minutes, and overlapping requests are coalesced. After an upstream failure, retry is delayed one minute. A last successful response may be retained for at most one hour, preserving its original timestamp and showing it as stale.
- Waits are accepted only for open lifts, from successful fresh feeds, with a published timestamp at most 15 minutes old. Zero is a valid observed wait; missing or closed-lift waits are not zero. The UI also checks timestamps before rendering and hides waits after a refresh failure.
- Busyness describes the median of the reporting lift lines: under 5 minutes is quiet, 5 to under 15 is moderate, and 15+ is busy. Each detail says how many of the open lifts supplied waits. This is not an occupancy percentage or resort attendance estimate.
- Resort snow reports older than 36 hours are marked stale. Fresh model output is preferred for the headline when a fresh resort snow report is absent. Weather-model cover is never labeled a measured resort base.
- Malformed or unavailable sources fail independently. Resort pins, geography and official links remain available when conditions are missing.

## Keys and service plans

Public Open-Meteo service access is intended for non-commercial development. For a commercial deployment, use an appropriate [Open-Meteo service plan](https://open-meteo.com/en/pricing) and set `OPEN_METEO_API_KEY` on the server; the adapter then uses the customer endpoint. The key is never included in browser responses. Resort feed access and formats can change, so retain the direct official links. No status.live, paid snow-report subscription, or new account is required by this implementation.

## Refreshing mapped geography

Run `python scripts/update_ski_terrain.py` manually with a descriptive `NWS_USER_AGENT` contact, at most daily. The script downloads OSM downhill ways and ski lift aerialways, excludes abandoned/disused ways, zip lines and non-ski aerialways, and writes `public/assets/ski-terrain.geojson` with source date and license metadata. `--endpoint` selects an Overpass instance; `--input path/to/response.json` converts a previously downloaded response offline. Incomplete responses do not overwrite the asset. No Overpass requests occur during map use.

Validation: `python -m pytest -q` and `node --test tests/ski-layer.test.cjs`.
