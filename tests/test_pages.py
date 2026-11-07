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


def test_home_page_links_to_the_map_from_nav_and_map_section() -> None:
    html = TestClient(app).get("/").text
    assert '<a class="nav-map" href="/map">Map</a>' in html
    assert 'class="button primary map-open" href="/map"' in html


def test_map_nav_link_stays_visible_on_narrow_screens() -> None:
    css = (ROOT / "public" / "assets" / "styles.css").read_text(encoding="utf-8")
    assert ".nav-links { display:none; }" not in css
    assert ".nav-links a:not(.nav-map) { display:none; }" in css
