#!/usr/bin/env python3
"""Build quality-assured training history for the Utah DAQ stations from EPA AQS.

EPA publishes yearly, quality-assured hourly files (no API key) at
https://aqs.epa.gov/aqsweb/airdata/download_files.html. Each is a national zip, so
this streams them and keeps only Utah rows for the stations in
``backend/providers/utah_daq.py``. Output matches the training contract:

    observed_at,station_id,pm25,pm25_24hr_avg,temp_f,humidity,wind_mph,elevation_m,source

Example (downloads about 150 MB per year into --cache; reruns reuse it):

    python scripts/backfill_aqs_history.py --years 2024 2025 2026 --out data/utah_air_history.csv
"""

from __future__ import annotations

import argparse
import csv
import io
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx  # noqa: E402
import pandas as pd  # noqa: E402

from backend.providers.utah_daq import STATIONS  # noqa: E402

AQS_URL = "https://aqs.epa.gov/aqsweb/airdata/hourly_{kind}_{year}.zip"
# Utah DAQ does not submit hourly wind to AQS for these stations (checked against the
# 2024 WIND file), so wind_mph is left empty here. The model treats it as missing, and
# station wind enters training through the DAQ snapshots the ingest job collects.
KINDS = ("88101", "TEMP", "RH_DP")
# AQS parameter code -> (output column, priority). Lower priority wins when a site reports both.
PARAMETERS = {
    "88101": ("pm25", 0),       # PM2.5 FRM/FEM mass
    "62101": ("temp_f", 0),     # outdoor temperature
    "62201": ("humidity", 0),   # relative humidity
    "61101": ("wind_mph", 0),   # wind speed, scalar (if a site ever reports it)
    "61103": ("wind_mph", 1),   # wind speed, resultant
}
SITE_TO_STATION = {station.aqs_site: station for station in STATIONS}
# Lines start with "State","County","Site" codes, e.g. "49","035","3006", so a prefix test skips everything else.
SITE_PREFIXES = tuple(f'"{site[:2]}","{site[2:5]}","{site[5:]}",' for site in SITE_TO_STATION)
# The DAQ feed's 24-hour average is a trailing mean; require 18 of 24 hours (EPA's 75% rule).
MIN_HOURS_FOR_24H = 18


def convert(value: float, units: str, column: str) -> float:
    units = units.lower()
    if column == "wind_mph":
        if "knot" in units:
            return value * 1.150779
        if "meters" in units:
            return value * 2.236936
    if column == "temp_f" and "centigrade" in units:
        return value * 9 / 5 + 32
    return value


def download(kind: str, year: int, cache: Path) -> Path:
    path = cache / f"hourly_{kind}_{year}.zip"
    if path.exists() and zipfile.is_zipfile(path):
        return path
    cache.mkdir(parents=True, exist_ok=True)
    partial = path.with_suffix(".part")
    url = AQS_URL.format(kind=kind, year=year)
    print(f"  downloading {url}", flush=True)
    with httpx.stream("GET", url, timeout=httpx.Timeout(60.0, read=300.0), follow_redirects=True) as response:
        response.raise_for_status()
        with partial.open("wb") as handle:
            for chunk in response.iter_bytes(1 << 20):
                handle.write(chunk)
    partial.replace(path)
    return path


def station_rows(path: Path) -> pd.DataFrame:
    """Long-format rows for our stations only, without parsing the rest of the national file."""
    with zipfile.ZipFile(path) as archive:
        member = archive.namelist()[0]
        with archive.open(member) as raw:
            lines = io.TextIOWrapper(raw, encoding="utf-8", errors="replace")
            header = next(lines)
            matches = [line for line in lines if line.startswith(SITE_PREFIXES)]
    rows = []
    for record in csv.DictReader([header, *matches]):
        site = f"{record['State Code']}{record['County Code']}{record['Site Num']}"
        parameter = PARAMETERS.get(record["Parameter Code"])
        if site not in SITE_TO_STATION or parameter is None or not record["Sample Measurement"]:
            continue
        column, priority = parameter
        rows.append((
            SITE_TO_STATION[site].id,
            f"{record['Date GMT']}T{record['Time GMT']}:00Z",
            column,
            priority,
            convert(float(record["Sample Measurement"]), record["Units of Measure"], column),
        ))
    return pd.DataFrame(rows, columns=["station_id", "observed_at", "column", "priority", "value"])


def build_history(long: pd.DataFrame) -> pd.DataFrame:
    long = long.copy()
    long["observed_at"] = pd.to_datetime(long["observed_at"], utc=True)
    # Keep the preferred parameter per column, then average co-located instruments (POCs).
    best = long.groupby(["station_id", "observed_at", "column"])["priority"].transform("min")
    long = long[long["priority"] == best]
    wide = long.pivot_table(index=["station_id", "observed_at"], columns="column", values="value", aggfunc="mean").reset_index()
    wide.columns.name = None
    for column in ("pm25", "temp_f", "humidity", "wind_mph"):
        if column not in wide.columns:
            wide[column] = float("nan")

    frames = []
    for station_id, group in wide.groupby("station_id"):
        series = group.set_index("observed_at").sort_index()
        series["pm25_24hr_avg"] = series["pm25"].rolling("24h", min_periods=MIN_HOURS_FOR_24H).mean().round(2)
        series["station_id"] = station_id
        frames.append(series.reset_index())
    history = pd.concat(frames, ignore_index=True)
    elevations = {station.id: station.elevation_m for station in STATIONS}
    history["elevation_m"] = history["station_id"].map(elevations)
    history["source"] = "epa-aqs"
    history["observed_at"] = history["observed_at"].dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    columns = ["observed_at", "station_id", "pm25", "pm25_24hr_avg", "temp_f", "humidity", "wind_mph", "elevation_m", "source"]
    return history[columns].sort_values(["station_id", "observed_at"]).reset_index(drop=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--years", type=int, nargs="+", required=True)
    parser.add_argument("--out", type=Path, default=ROOT / "data" / "utah_air_history.csv")
    parser.add_argument("--cache", type=Path, default=ROOT / "data" / "raw", help="where the EPA zips are kept")
    args = parser.parse_args()

    parts: list[pd.DataFrame] = []
    for year in args.years:
        for kind in KINDS:
            path = download(kind, year, args.cache)
            found = station_rows(path)
            print(f"  {path.name}: {len(found):,} station rows", flush=True)
            parts.append(found)
    long = pd.concat(parts, ignore_index=True)
    if long.empty:
        raise SystemExit("No rows for the DAQ stations were found for those years.")

    history = build_history(long)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    history.to_csv(args.out, index=False)
    print(f"Wrote {len(history):,} station-hours to {args.out}")
    print(history.groupby("station_id")["observed_at"].agg(["min", "max", "count"]).to_string())


if __name__ == "__main__":
    main()
