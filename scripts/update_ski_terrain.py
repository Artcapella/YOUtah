"""Build the small Utah ski-terrain asset from OSM, never from invented lines.

Run manually (at most daily): python scripts/update_ski_terrain.py
An existing Overpass response can be converted offline with --input <path>.
The resulting derived OSM database is distributed under ODbL with its provenance.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sys
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from backend.config import settings

QUERY = '[out:json][timeout:60];(way["piste:type"="downhill"](37,-113.5,42.05,-111.0);way["aerialway"](37,-113.5,42.05,-111.0););out tags geom;'
SKI_LIFTS = {"chair_lift", "gondola", "mixed_lift", "cable_car", "drag_lift", "t-bar", "j-bar", "platter", "rope_tow", "magic_carpet"}


def length_m(coords: list) -> int:
    total = 0.0
    for a, b in zip(coords, coords[1:]):
        lat1, lat2 = math.radians(a[1]), math.radians(b[1])
        dlat, dlon = lat2 - lat1, math.radians(b[0] - a[0])
        total += 12742000 * math.asin(min(1, math.sqrt(math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2)))
    return round(total)


def convert(payload: dict) -> dict:
    if payload.get("remark") or not payload.get("elements"):
        raise ValueError("Incomplete Overpass response; existing terrain asset was preserved")
    features = []
    for element in payload["elements"]:
        tags = element.get("tags", {})
        lift_type = tags.get("aerialway")
        is_lift = lift_type in SKI_LIFTS
        if not is_lift and tags.get("piste:type") != "downhill":
            continue
        # Remove abandoned/disused ways; OSM is geography, never a real-time operation report.
        if tags.get("abandoned") == "yes" or tags.get("disused") == "yes":
            continue
        coords = [[round(p["lon"], 6), round(p["lat"], 6)] for p in element.get("geometry", []) if p and "lon" in p and "lat" in p]
        if len(coords) < 2 or any(not (-113.5 <= x <= -111 and 37 <= y <= 42.05) for x, y in coords):
            continue
        area = not is_lift and tags.get("area") == "yes" and len(coords) >= 4 and coords[0] == coords[-1]
        properties = {
            "osm_id": element["id"], "kind": "lift" if is_lift else "run",
            "name": tags.get("name") or tags.get("piste:name") or "",
            "difficulty": tags.get("piste:difficulty", "unknown"),
            "lift_type": lift_type if is_lift else "", "grooming": tags.get("piste:grooming", ""),
            "lit": tags.get("lit", tags.get("piste:lit", "unknown")),
            "length_m": length_m(coords) if not area else None,
        }
        features.append({"type": "Feature", "id": element["id"], "properties": properties,
                         "geometry": {"type": "Polygon" if area else "LineString", "coordinates": [coords] if area else coords}})
    if not features:
        raise ValueError("No ski terrain found; existing asset was preserved")
    return {"type": "FeatureCollection", "metadata": {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "osm_data_at": payload.get("osm3s", {}).get("timestamp_osm_base"),
        "source": "OpenStreetMap via Overpass API", "source_url": "https://www.openstreetmap.org/",
        "license": "ODbL-1.0", "license_url": "https://opendatacommons.org/licenses/odbl/1-0/",
        "note": "Geography only. Not a live opening, grooming, avalanche or navigation report.",
    }, "features": features}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, help="Convert an already downloaded Overpass JSON response")
    parser.add_argument("--endpoint", default="https://overpass-api.de/api/interpreter")
    args = parser.parse_args()
    if args.input:
        payload = json.loads(args.input.read_text(encoding="utf-8"))
    else:
        request = urllib.request.Request(args.endpoint, data=QUERY.encode(), headers={
            "User-Agent": settings.nws_user_agent, "Content-Type": "text/plain", "Accept": "application/json",
        })
        with urllib.request.urlopen(request, timeout=70) as response:
            payload = json.load(response)
    collection = convert(payload)
    output = ROOT / "public" / "assets" / "ski-terrain.geojson"
    output.write_text(json.dumps(collection, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    counts = {kind: sum(f["properties"]["kind"] == kind for f in collection["features"]) for kind in ("run", "lift")}
    print(f"Wrote {counts['run']} run segments and {counts['lift']} lifts to {output}")


if __name__ == "__main__":
    main()
