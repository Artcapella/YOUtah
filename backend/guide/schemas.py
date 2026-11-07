"""Public request/response shapes for the map guide.

The persona deliberately contains preference-only data. It is kept in the visitor's
browser cookie and is not persisted by this API.
"""

from typing import Literal

from pydantic import BaseModel, Field


class GuidePersona(BaseModel):
    """A compact, optional set of trip preferences supplied by the browser."""

    interests: list[str] = Field(default_factory=list, max_length=10)
    pace: Literal["easy", "balanced", "full"] | None = None
    companions: Literal["solo", "couple", "friends", "family", "group"] | None = None
    budget: Literal["free", "low", "moderate", "flexible"] | None = None
    access_needs: list[str] = Field(default_factory=list, max_length=6)
    visitor_type: Literal["local", "visitor", "newcomer"] | None = None
    home_base: str | None = Field(default=None, max_length=100)

    def compact(self) -> dict[str, object]:
        """Return only meaningful values for prompts and retrieval scoring."""
        return {key: value for key, value in self.model_dump().items() if value not in (None, [], "")}


class GuideSearchRequest(BaseModel):
    query: str = Field(min_length=1, max_length=600)
    lat: float = Field(default=40.7608, ge=36, le=43)
    lng: float = Field(default=-111.8910, ge=-114.2, le=-109.0)
    persona: GuidePersona | None = None


class GuideAnswerRequest(GuideSearchRequest):
    use_live_web: bool = False


class GuideCitation(BaseModel):
    id: str
    title: str
    url: str | None = None
    source_name: str
    kind: Literal["place", "event", "advisory", "story", "web", "other"] = "other"
    freshness: str | None = None


class GuideSearchResult(BaseModel):
    id: str
    entity_type: Literal["place", "event", "advisory", "story", "other"]
    entity_id: str | None = None
    title: str
    excerpt: str
    tags: list[str] = Field(default_factory=list)
    latitude: float | None = None
    longitude: float | None = None
    score: float = 0
    citation: GuideCitation


class GuideSearchResponse(BaseModel):
    results: list[GuideSearchResult]
    retrieval: Literal["hybrid", "keyword", "fallback"]


class GuideAnswerResponse(BaseModel):
    answer: str
    citations: list[GuideCitation]
    retrieval: Literal["hybrid", "keyword", "fallback"]
    agent: Literal["openai", "local_fallback"]
    persona_nudge: str | None = None
    live_web_used: bool = False
