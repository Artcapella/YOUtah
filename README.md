# Comprehensive Utah

A Vercel-ready **FastAPI/Python** application with a Supabase-backed discovery dashboard for Utah newcomers. The starter makes the state legible through live conditions, events, outdoor access, community places, and local stories—not just the usual tourism highlights.

## What is included

- Responsive dashboard shell with an interactive MapLibre map, filters, featured events, Utah stories, and source-status UI.
- Python FastAPI API, Pydantic domain models, and a working NWS server-side adapter with mock-safe fallbacks.
- A deterministic **Escape the Inversion** recommendation route that is ready to receive Utah DAQ, UDOT, UTA, elevation, and trailhead data.
- Supabase/Postgres schema, Row Level Security, PostGIS geometry columns, provenance, ingestion-run logging, and seed data.
- Provider-adapter contracts so data work can progress independently from frontend work.
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
2. Install the CLI and link it: `npx supabase login && npx supabase link --project-ref <project-ref>`.
3. Push the versioned schema: `npx supabase db push`.
4. Copy the project URL and anon key into `.env.local`; add the service-role key only to Vercel/server environments.
5. In Vercel, add every variable from `.env.example` under **Project Settings → Environment Variables**. The application deliberately exposes no secret keys to the browser.

Schema changes belong in `supabase/migrations/`. Create a migration with:

```bash
npx supabase migration new describe_change
```

Commit the migration with the feature branch. This keeps the shared database reproducible while the team works in parallel.

## Deploy to Vercel

1. Push the repository to GitHub.
2. Import it in Vercel; the platform detects `app.py` and FastAPI automatically.
3. Add the environment variables for Production and Preview.
4. Deploy. Every pull request receives a preview URL; `main` is the judge-facing build.

## Data-provider ownership

| Module | Owner scope | Current state |
| --- | --- | --- |
| `backend/providers/nws.py` | Weather, forecasts, alerts | Live adapter + fallback |
| `backend/providers/utah_daq.py` | AQI and inversion ceiling | Typed adapter shell + demo values |
| `backend/providers/udot.py` | Roads, canyon access, traffic | Typed adapter shell + demo values |
| `backend/providers/uta.py` | GTFS-RT transit / ski bus | Typed adapter shell + demo values |
| `backend/providers/events.py` | Ticketmaster, Eventbrite, campus/library events | Typed adapter shell + demo values |
| `backend/providers/ugrc.py` | Trails, places, cultural layers | Typed adapter shell + demo values |

All providers return normalized Pydantic domain models from `backend/models.py`; the frontend never consumes provider-specific JSON.

## Source and safety rules

- Fetch providers through server routes, not the browser, so rate limits and secret keys remain controlled.
- Preserve every source URL, provider ID, and update timestamp. The source drawer is part of the product story.
- Treat inversion height as an estimate and do not frame it as a safety clearance.
- Keep public read access on curated/live data; use the service role only in server-side ingestion.
- Store raw source payloads only if needed for debugging and attribution. Never put tokens or personal data there.

## Next build targets

1. Implement the Utah DAQ, UDOT, UTA, UGRC, and Ticketmaster adapters.
2. Add a protected ingest route or scheduled function that writes normalized snapshots and `ingest_runs`.
3. Replace demo items with database reads in `backend/services/dashboard.py`.
4. Add source filters, language selection, and a citation drawer before expanding to a chatbot.
