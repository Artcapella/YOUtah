# Comprehensive Utah

A Vercel-ready **FastAPI/Python** application with a Supabase-backed discovery dashboard for Utah newcomers. The starter makes the state legible through live conditions, events, outdoor access, community places, and local stories—not just the usual tourism highlights.

## What is included

- Responsive dashboard shell with an interactive MapLibre map, filters, featured events, Utah stories, and source-status UI.
- A full-screen map hub at **`/map`** (`public/map.html`) with MapTiler 3D terrain and contours, layers, trip planning, and an inversion view. It hydrates normalized places, events and DAQ inversion estimates from `/api/map-data`; unsupported layers remain visibly offline samples. On Vercel the CDN serves it through the `/map` rewrite in `vercel.json`; locally `app.py` serves it. Its MapTiler key is a browser key: restrict its allowed origins in the MapTiler dashboard.
- A focused **`/map/simple`** alternative for everyday place finding: a north-up, 2D MapTiler street map with search, category filters, source-backed pins, and an optional location control. The advanced 3D map remains at `/map`.
- Python FastAPI API, Pydantic domain models, and a working NWS server-side adapter with mock-safe fallbacks.
- A deterministic **Escape the Inversion** recommendation route that is ready to receive Utah DAQ, UDOT, UTA, elevation, and trailhead data.
- Supabase/Postgres schema, Row Level Security, PostGIS geometry columns, provenance, ingestion-run logging, and seed data.
- Provider-adapter contracts so data work can progress independently from frontend work.
- **Utah intelligence** (`backend/intelligence/`): a trainable Outdoor Comfort nowcast and an explainable Local Pulse score, with a leakage-aware training pipeline. See [Utah intelligence](#utah-intelligence).
- Vercel configuration and environment-variable documentation.

## Run locally

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env.local
uvicorn app:app --reload
```

The UI works without any API keys using transparent demo data. Add a `NEXT_PUBLIC_MAPTILER_KEY` for a production basemap. Until then the map uses MapLibre's demo style for development only.

## Connect Supabase

1. Create a Supabase project.
2. Copy `.env.example` to `.env.local` and fill in `SUPABASE_URL`, `SUPABASE_PUBLISHABLE_KEY` (`sb_publishable_…`) and `SUPABASE_SECRET_KEY` (`sb_secret_…`) from **Project Settings → API Keys**. The legacy names `NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY` and `SUPABASE_SERVICE_ROLE_KEY` are still accepted.
3. Install the CLI and link it: `npx supabase login && npx supabase link --project-ref <project-ref>`.
4. Push the versioned schema: `npx supabase db push`.
5. Start the app and check `GET /api/health`: `"supabase_status": "ok"` means the schema is live and readable. `schema_missing` means step 4 hasn't run.
6. In Vercel, add every variable from `.env.example` under **Project Settings → Environment Variables**. The application deliberately exposes no secret keys to the browser.

How the backend uses Supabase (`backend/supabase_client.py`, `backend/repository.py`):

- **Reads** use the publishable key, so they see only what the RLS policies expose publicly.
- **Writes** (ingestion) use the secret key, which bypasses RLS. It is server-only and never returned to the browser.
- The dashboard serves ingested rows for each lane (places, events, road advisories, transit advisories) and falls back to the provider adapter when a lane's table is empty or Supabase is unreachable. Lanes served from the database show as `cached` in the source drawer.
- The new `sb_` keys need a recent `supabase-py` (the pinned 2.33.0 works). 2.13 and earlier reject them with `Invalid API key`.

Schema changes belong in `supabase/migrations/`. Create a migration with:

```bash
npx supabase migration new describe_change
```

Commit the migration with the feature branch. This keeps the shared database reproducible while the team works in parallel.

## AI guide and RAG

The map now opens first and includes an **Ask YOUtah** guide plus a compact taste quiz. The quiz saves preference-only data (interests, pace, group, budget, access needs, and a voluntary home-base label) in the visitor's browser cookie; this project does not persist a profile or an account identifier.

The guide has a useful source-backed fallback with no model key. Once OpenAI is configured, its production path is:

```text
map + cookie persona
  → /api/guide/answer (server only)
  → Supabase hybrid retrieval (keyword + pgvector + persona + proximity + popularity)
  → OpenAI Responses API, optionally with live web search
  → answer + source links returned to the browser
```

`supabase/migrations/20261107050000_guide_rag.sql` creates `guide_documents`, a derived search index. It mirrors the authoritative `places`, `events`, `advisories`, and `stories` tables; it does not replace them. Every indexed document carries its source URL and freshness timestamp. The retrieval RPC is service-role-only, so clients cannot dump the full index or embedding data.

1. Push migrations: `npx supabase db push`.
2. In Vercel, add a newly created **rotated** `OPENAI_API_KEY` only under Project Settings → Environment Variables (Production and Preview). Do not place it in a `NEXT_PUBLIC_*` variable or commit it.
3. Leave `OPENAI_MODEL=gpt-6-astra` and `OPENAI_EMBEDDING_MODEL=text-embedding-3-small` unless you also migrate the `vector(1536)` column for a different embedding dimensionality.
4. Add `CRON_SECRET`; the hourly `/api/cron/refresh-guide` job rebuilds derived documents and embeds a bounded batch of changed rows. Existing ingest crons keep live sources fresher than that where supported.

The optional “Include live web sources” toggle uses the OpenAI Responses API web-search tool, not a separate search-provider key. The existing provider keys remain optional: MapTiler improves tiles; Ticketmaster, UDOT, RIDB, Google Places, and similar connectors each need their own account/key to move from their built-in fallback to live data.

## Deploy to Vercel

1. Push the repository to GitHub.
2. Import it in Vercel; the platform detects `app.py` and FastAPI automatically.
3. Add the environment variables for Production and Preview.
4. Deploy. Every pull request receives a preview URL; `main` is the judge-facing build.

## Data-provider ownership

| Module | Owner scope | Current state |
| --- | --- | --- |
| `backend/providers/nws.py` | Weather, forecasts, alerts | Live adapter (display + numeric model inputs) + fallback |
| `backend/providers/utah_daq.py` | AQI and inversion ceiling | Live: 14 county stations, nearest-station AQI (EPA 2024 breakpoints). Inversion cap is still a placeholder |
| `backend/providers/udot.py` | Roads, canyon access, traffic | Live UDOT alerts API when `UDOT_API_KEY` is set |
| `backend/providers/uta.py` | GTFS-RT transit / ski bus | Live public GTFS-Realtime service-alert feed |
| `backend/providers/events.py` | Ticketmaster, Eventbrite, campus/library events | Ticketmaster live when `TICKETMASTER_API_KEY` is set; demo values otherwise |
| `backend/providers/ugrc.py` | Trails, places, cultural layers | Live: ~560 statewide trailheads (ArcGIS, no key) for the `ugrc` ingest. Dashboard fallback is still demo |
| `backend/providers/ridb.py` | Campgrounds and recreation facilities | Live with `RIDB_API_KEY` for the `ridb` ingest |
| `backend/providers/osm.py` | Parks, viewpoints, museums, art, libraries (OpenStreetMap via Overpass) | Live for the `osm` ingest. Needs a descriptive `NWS_USER_AGENT` |
| `backend/providers/google_places.py` | Google Places search, ratings, reviews | Adapter only (`GOOGLE_MAPS_API_KEY`). Billed per call and not cacheable, so no route uses it yet |
| `backend/providers/arcgis.py`, `http.py` | Shared: any ArcGIS feature layer (UGRC, UDOT); retrying JSON client for ingestion | Used by the adapters above |

All providers return normalized Pydantic domain models from `backend/models.py`; the frontend never consumes provider-specific JSON.

**Places ingestion.** `ugrc`, `ridb` and `osm` each fetch statewide and upsert into `places` by `(source_id, external_id)`. Run one with `POST /api/admin/ingest {"source": "osm"}`, or all of them through the weekly cron (`GET /api/cron/ingest-places`, in `vercel.json`). The dashboard reads the nearest few places per category around its point through the `nearby_places` SQL function. Apply `supabase/migrations/*_place_sources.sql` first.

## Utah intelligence

Two intentionally distinct products, served under `/api/intelligence` and shown in the hero card:

1. **Outdoor Comfort**: a three-class (`good` / `caution` / `poor`) nowcast of the 24-hour PM2.5 band at the nearest Utah DAQ station, `horizon_hours` (default 3) ahead. A trained model is served only when an artifact has passed the training gate. Otherwise responses say `conservative_fallback` and `/api/intelligence/model-status` says why.
2. **Local Pulse**: an explainable 0–100 appeal score from nearby events (Supabase events, then live Ticketmaster) and predicted comfort. It is never an attendance count, and demo events are never scored.

| Endpoint | Purpose |
| --- | --- |
| `GET /api/intelligence/brief?lat=&lng=` | Comfort + Local Pulse for a point, with the station used, data freshness, and source links. |
| `POST /api/intelligence/comfort` | Score a supplied feature vector (see `ComfortFeatures`). |
| `POST /api/intelligence/feedback` | Privacy-safe interaction (`detail_opened`, …) stored server-side in `analytics_events`. The browser never writes the table. Place-card clicks send these. |
| `GET /api/intelligence/model-status` | Model version, metrics, training window, or the fallback reason. |

### Training pipeline

Training is offline. Requests never retrain. The deployed artifact is portable JSON (`backend/intelligence/artifacts/outdoor_comfort.json`) evaluated in pure Python. scikit-learn is a training-only dependency because it would grow the Vercel bundle from about 52 MB to about 327 MB.

```bash
pip install -r requirements-ml.txt

# 1. Quality-assured PM2.5, temperature and humidity for the DAQ stations from EPA AQS
#    (no key; ~150 MB per year, cached in data/raw). DAQ sends no hourly wind to AQS, so wind
#    is left missing there and is learned from the ingested DAQ snapshots instead.
python scripts/backfill_aqs_history.py --years 2024 2025 2026 --out data/utah_air_history.csv

# 2. Snapshots this project collected (needs the migration + ingest below). Optional until they accumulate.
python scripts/export_training_data.py --source supabase --out data/daq_snapshots.csv

# 3. Train. QA'd history first: it wins when both files hold the same station-hour.
python scripts/train_outdoor_comfort.py --input data/utah_air_history.csv --input data/daq_snapshots.csv

pytest
```

The trainer joins targets on exact timestamps, splits chronologically (60/20/20) with a purge gap, and calibrates on the validation fold. It refuses to write an artifact unless all of the following hold:

- At least 200 rows and two target classes.
- The model beats the persistence baseline on test-period accuracy without losing macro-F1.
- The exported JSON reproduces scikit-learn's probabilities exactly.

Commit the `.json` (and its `.metrics.json`) to deploy it.

**Collecting training data.** `POST /api/admin/ingest` with `{"source": "utah-daq"}` and the `x-ingest-secret` header, or the daily Vercel Cron in `vercel.json` (`GET /api/cron/ingest`, `Authorization: Bearer $CRON_SECRET`). Each run:

- Writes new station-hour readings to `ml_feature_snapshots`, keeping the first-seen `available_at`.
- Logs one prediction per station to `ml_predictions`, for later comparison with what happened.
- Records the run in `ingest_runs`.

The DAQ feed keeps 10+ days of history, so a daily schedule leaves no gaps. Apply `supabase/migrations/*_intelligence.sql` first (`npx supabase db push`).

Product rules: keep the official-source links and disclaimers. Present comfort as guidance, not a safety clearance. Comfort bands (≤12 / ≤35.4 µg/m³) are deliberately simpler than the official AQI shown beside them.

## Source and safety rules

- Fetch providers through server routes, not the browser, so rate limits and secret keys remain controlled.
- Preserve every source URL, provider ID, and update timestamp. The source drawer is part of the product story.
- Treat inversion height as an estimate and do not frame it as a safety clearance.
- Keep public read access on curated/live data; use the service role only in server-side ingestion.
- Store raw source payloads only if needed for debugging and attribution. Never put tokens or personal data there.

## Next build targets

1. ~~Utah DAQ and Ticketmaster adapters~~ Done. Remaining: UDOT, UTA, UGRC, and a DAQ ceilometer source for the real inversion cap.
2. ~~Protected ingest route and schedule~~ Done for `utah-daq` (snapshots, predictions, `ingest_runs`). Remaining: the other sources.
3. ~~Replace demo items with database reads in `backend/services/dashboard.py`.~~ Done: the dashboard reads from Supabase and falls back to providers. Remaining: weather and air snapshots from `observations`.
4. Add source filters, language selection, and a citation drawer before expanding to a chatbot.
