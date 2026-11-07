from pathlib import Path

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from datetime import date

from backend.providers.events import fetch_events
from backend.services.event_recommendations import recommend_events
from backend.config import settings
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
    if not settings.ingest_secret or x_ingest_secret != settings.ingest_secret:
        raise HTTPException(status_code=401, detail="Invalid ingestion credential")
    return await run_ingest(request.source)


@app.get("/api/events/recommendations")
async def event_recommendations(
        category: str = "any",
        start_date: date | None = None,
        end_date: date | None = None,
):
    events = await fetch_events(
        category=category,
        start_date=start_date.isoformat() if start_date else None,
        end_date=end_date.isoformat() if end_date else None,
    )

    return {
        "recommendations": recommend_events(
            events,
            category=category,
            start_date=start_date,
            end_date=end_date,
        )
    }


app.mount("/assets", StaticFiles(directory=PUBLIC_DIR / "assets"), name="assets")
