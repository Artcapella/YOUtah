from pathlib import Path

from fastapi.testclient import TestClient

from app import app
from backend.guide.schemas import GuideAnswerResponse, GuideCitation, GuideSearchResponse, GuideSearchResult


def test_guide_answer_route_returns_citations_without_calling_a_model(monkeypatch) -> None:
    async def fake_answer(_request):
        return GuideAnswerResponse(
            answer="Try Liberty Park [S1].",
            citations=[
                GuideCitation(
                    id="place:liberty",
                    title="Liberty Park",
                    url="https://www.slc.gov/parks/parks-division/liberty-park/",
                    source_name="Salt Lake City",
                    kind="place",
                )
            ],
            retrieval="fallback",
            agent="local_fallback",
        )

    monkeypatch.setattr("app.answer_guide", fake_answer)
    response = TestClient(app).post("/api/guide/answer", json={"query": "Where can I take a walk?"})

    assert response.status_code == 200
    body = response.json()
    assert body["answer"] == "Try Liberty Park [S1]."
    assert body["citations"][0]["url"].startswith("https://")
    assert body["agent"] == "local_fallback"


def test_guide_search_route_exposes_ranked_source_metadata(monkeypatch) -> None:
    async def fake_search(_request):
        citation = GuideCitation(
            id="place:liberty",
            title="Liberty Park",
            url="https://www.slc.gov/parks/parks-division/liberty-park/",
            source_name="Salt Lake City",
            kind="place",
        )
        return GuideSearchResponse(
            retrieval="keyword",
            results=[
                GuideSearchResult(
                    id="place:liberty",
                    entity_type="place",
                    entity_id="liberty",
                    title="Liberty Park",
                    excerpt="A central Salt Lake City park.",
                    tags=["park", "outdoors"],
                    latitude=40.745,
                    longitude=-111.874,
                    score=0.81,
                    citation=citation,
                )
            ],
        )

    monkeypatch.setattr("app.search_guide", fake_search)
    response = TestClient(app).post("/api/guide/search", json={"query": "an easy outdoor walk"})

    assert response.status_code == 200
    assert response.json()["results"][0]["citation"]["source_name"] == "Salt Lake City"


def test_map_guide_keeps_persona_in_a_browser_cookie() -> None:
    script = (Path(__file__).resolve().parents[1] / "public" / "assets" / "simple-map.js").read_text(encoding="utf-8")
    assert 'const PERSONA_COOKIE = "youtah_persona"' in script
    assert "SameSite=Lax" in script
    assert "/api/guide/answer" in script
