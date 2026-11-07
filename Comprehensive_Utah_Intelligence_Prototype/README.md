# Comprehensive Utahn Intelligence Prototype

An additive FastAPI module for the existing **Comprehensive Utah FastAPI
Starter**. It introduces two intentionally distinct products:

1. **Outdoor Comfort** — a trained, calibrated, three-class nowcast when a
   validated model artifact is available; otherwise a clearly marked,
   conservative rules fallback.
2. **Local Pulse** — an explainable estimate of how appealing/active an area is
   right now. It is not advertised as attendance or foot traffic.

The package is designed to be copied into the starter repository. It does not
replace existing provider modules or the dashboard endpoint.

## What is included

* `backend/intelligence/`: FastAPI routes, schemas, feature builders, scoring,
  data adapters, and model loading.
* `scripts/train_outdoor_comfort.py`: reproducible training/evaluation command
  using real historical station observations.
* `supabase/migrations/`: timestamped feature/prediction/engagement tables.
* `tests/`: fast unit tests for deterministic scoring behavior.

## Add it to the starter repository

Copy the `backend/intelligence`, `scripts`, `tests`, and migration files into
the existing starter. Then make these three changes.

### 1. Add packages

Append `requirements.intelligence.txt` to the project's `requirements.txt`:

```bash
cat requirements.intelligence.txt >> requirements.txt
```

### 2. Mount the router

In `app.py`, add:

```python
from backend.intelligence.router import router as intelligence_router

app.include_router(intelligence_router)
```

### 3. Configure environment

Add the optional values from `.env.intelligence.example` to `.env.local` and
your deployment environment. The service works without a Ticketmaster key and
will return an empty event list rather than fail.

Then apply the migration:

```bash
npx supabase db push
```

## Endpoints

| Endpoint | Purpose |
| --- | --- |
| `GET /api/intelligence/brief` | Comfort prediction plus explainable Local Pulse for a latitude/longitude. |
| `POST /api/intelligence/comfort` | Score an explicitly supplied feature vector; ideal for a job that already has API data. |
| `POST /api/intelligence/feedback` | Validates a privacy-safe aggregate interaction payload for later ingestion. |
| `GET /api/intelligence/model-status` | Makes model/fallback state visible to the UI and judges. |

Try it locally:

```bash
uvicorn app:app --reload
curl 'http://127.0.0.1:8000/api/intelligence/brief?lat=40.7608&lng=-111.8910'
```

## Train the Outdoor Comfort model

The training command expects a CSV of **historical station readings**, one row
per station observation. It creates a nowcast: values available at time `t`
predict the AQI band at `t + horizon`. It will refuse to train on a tiny or
single-class dataset.

Required columns:

```text
observed_at,station_id,pm25_24hr_avg,temp_f,humidity,wind_mph,elevation_m
```

Optional: `precip_probability`. `observed_at` must be ISO-8601 and the input
must be ordered or orderable by station/time. The Utah DAQ current CSV feed
uses equivalent PM2.5, temperature, relative-humidity, and wind measurements;
use quality-assured historical data for training.

```bash
python scripts/train_outdoor_comfort.py \
  --input data/utah_air_history.csv \
  --out backend/intelligence/artifacts/outdoor_comfort.joblib \
  --horizon-hours 3
```

The script writes `outdoor_comfort.metrics.json` next to the artifact. Only
deploy an artifact that beats the persistence baseline on its final chronological
test period.

## Important product rules

* `model_kind: trained_nowcast` is returned only when a model artifact passed
  the training gate. Otherwise the response says `conservative_fallback`.
* Keep `available_at` with API snapshots once the team implements persistence.
  Training joins must use only values available before prediction time.
* Never display Local Pulse as an attendance count. It becomes a supervised
  engagement model only after real, aggregate product outcome data exists.
* “Escape the Inversion” remains guidance. Preserve DAQ, NWS, and UDOT source
  links and show a safety disclaimer.

## Handoff notes for the coding agent

* Replace the feedback route's validation-only response with a Supabase
  service-role insert into `analytics_events`; do not grant the browser direct
  write access to that table.
* Replace the optional Ticketmaster adapter with your canonical event query if
  one already exists. It is included only to make this package runnable alone.
* Use the existing NWS adapter for production weather if it exposes numeric
  hourly fields. The included adapter is intentionally self-contained.
* Schedule API snapshot collection first; train offline, then serve an
  immutable versioned artifact. Do not retrain inside a Vercel request.
