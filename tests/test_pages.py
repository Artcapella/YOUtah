import json
from pathlib import Path

from fastapi.testclient import TestClient

from app import app

ROOT = Path(__file__).resolve().parents[1]


def test_map_page_is_served_locally() -> None:
    response = TestClient(app).get("/map")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert 'id="map"' in response.text


def test_vercel_rewrites_map_to_static_page() -> None:
    config = json.loads((ROOT / "vercel.json").read_text(encoding="utf-8"))
    rewrite = {"source": "/map", "destination": "/map.html"}
    assert rewrite in config.get("rewrites", [])
    assert (ROOT / "public" / "map.html").is_file()
