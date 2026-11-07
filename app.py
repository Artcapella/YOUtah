import hmac
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from backend import repository
from backend.config import settings
from backend.intelligence.model import model_status
from backend.intelligence.router import router as intelligence_router
from backend.models import Coordinate, IngestRequest
from backend.services.dashboard import build_dashboard, build_inversion_recommendations
from backend.services.ingest import run_ingest

ROOT_DIR = Path(__file__).resolve().parent
PUBLIC_DIR = ROOT_DIR / "public"

app = FastAPI(
    title="Comprehensive Utah API",
    version="0.1.0",
    description="Source-aware Utah discovery data for the Comprehensive Utah dashboard.",
)

# This is useful for local development. In Vercel the frontend and backend share an origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
async def homepage() -> FileResponse:
    return FileResponse(PUBLIC_DIR / "index.html")


@app.get("/api/health")
async def health() -> dict:
    return {
        "status": "ok",
        "environment": settings.environment,
        "supabase_configured": settings.supabase_configured,
        # ok | not_configured | schema_missing | error | unreachable
        "supabase_status": await repository.check_connection(),
        # trained_nowcast | fallback (see /api/intelligence/model-status for the reason)
        "intelligence_model": model_status()["status"],
    }


@app.get("/api/config")
async def public_config() -> dict:
    """Only return explicitly browser-safe configuration."""
    return {"maptilerKey": settings.maptiler_key or None}


@app.get("/api/dashboard")
async def dashboard(
    lat: float = Query(default=40.7608, ge=-90, le=90),
    lng: float = Query(default=-111.8910, ge=-180, le=180),
) -> dict:
    return (await build_dashboard(Coordinate(lat=lat, lng=lng))).model_dump(mode="json")


@app.get("/api/recommendations/escape-inversion")
async def escape_inversion(
    lat: float = Query(default=40.7608, ge=-90, le=90),
    lng: float = Query(default=-111.8910, ge=-180, le=180),
) -> dict:
    return (await build_inversion_recommendations(Coordinate(lat=lat, lng=lng))).model_dump(mode="json")


@app.post("/api/admin/ingest")
async def ingest(
    request: IngestRequest,
    x_ingest_secret: str | None = Header(default=None),
) -> dict:
    """Protected manual ingest hook. Attach this to a cron job after provider adapters are completed."""
    if not _secret_matches(x_ingest_secret, settings.ingest_secret):
        raise HTTPException(status_code=401, detail="Invalid ingestion credential")
    return await run_ingest(request.source)


@app.get("/api/cron/ingest", include_in_schema=False)
async def scheduled_ingest(authorization: str | None = Header(default=None)) -> dict:
    """Vercel Cron target (see vercel.json). Collects Utah DAQ snapshots for model training.

    The DAQ feed keeps 10+ days of hourly history, so even a once-daily schedule is gap-free.
    """
    token = authorization.removeprefix("Bearer ") if authorization and authorization.startswith("Bearer ") else None
    if not _secret_matches(token, settings.cron_secret):
        raise HTTPException(status_code=401, detail="Invalid cron credential")
    return await run_ingest("utah-daq")


def _secret_matches(provided: str | None, expected: str) -> bool:
    return bool(expected and provided and hmac.compare_digest(provided.encode(), expected.encode()))


app.include_router(intelligence_router)


app.mount("/assets", StaticFiles(directory=PUBLIC_DIR / "assets"), name="assets")
