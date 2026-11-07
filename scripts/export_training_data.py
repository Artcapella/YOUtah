#!/usr/bin/env python3
"""Export Utah DAQ station history in the training CSV format.

    --source supabase  station-hour snapshots collected by the utah-daq ingest job
                       (POST /api/admin/ingest or the Vercel cron). Needs SUPABASE_SECRET_KEY.
    --source daq       the live air.utah.gov feeds (the last ~10-15 days), no database needed.

Example:

    python scripts/export_training_data.py --source supabase --out data/daq_snapshots.csv
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from backend.intelligence.features import STATION_READING_SCHEMA_VERSION  # noqa: E402
from backend.providers.utah_daq import STATIONS_BY_ID, fetch_all_station_readings  # noqa: E402

COLUMNS = ["observed_at", "station_id", "pm25", "pm25_24hr_avg", "temp_f", "humidity", "wind_mph", "elevation_m", "source", "available_at"]
PAGE_SIZE = 1000


def from_supabase() -> pd.DataFrame:
    from backend.supabase_client import get_service_client

    client = get_service_client()
    if client is None:
        raise SystemExit("SUPABASE_URL and SUPABASE_SECRET_KEY must be set (see .env.example).")
    rows, start = [], 0
    while True:
        page = (
            client.table("ml_feature_snapshots")
            .select("entity_id, prediction_time, available_at, features")
            .eq("entity_type", "station")
            .eq("feature_schema_version", STATION_READING_SCHEMA_VERSION)
            .order("id")
            .range(start, start + PAGE_SIZE - 1)
            .execute()
        ).data
        rows.extend(
            {**row["features"], "station_id": row["entity_id"], "observed_at": row["prediction_time"],
             "available_at": row["available_at"], "source": "supabase-snapshot"}
            for row in page
        )
        if len(page) < PAGE_SIZE:
            return pd.DataFrame(rows)
        start += PAGE_SIZE


def from_daq() -> pd.DataFrame:
    results = asyncio.run(fetch_all_station_readings())
    rows = []
    for station_id, readings in results.items():
        if isinstance(readings, Exception):
            print(f"  skipped {station_id}: {readings}")
            continue
        elevation = STATIONS_BY_ID[station_id].elevation_m
        rows.extend({**reading.model_dump(mode="json"), "elevation_m": elevation, "source": "daq-live"} for reading in readings)
    return pd.DataFrame(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--source", choices=["supabase", "daq"], default="supabase")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()

    frame = from_supabase() if args.source == "supabase" else from_daq()
    if frame.empty:
        raise SystemExit("No station readings found. Run the utah-daq ingest first.")
    for column in COLUMNS:
        if column not in frame.columns:
            frame[column] = None
    frame = frame[COLUMNS].sort_values(["station_id", "observed_at"])
    args.out.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.out, index=False)
    print(f"Wrote {len(frame):,} station-hours ({frame['observed_at'].min()} to {frame['observed_at'].max()}) to {args.out}")


if __name__ == "__main__":
    main()
