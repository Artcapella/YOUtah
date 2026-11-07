"""Hybrid retrieval and response generation for the YOUtah guide.

All model traffic stays in this server module.  Browser clients receive only the
answer and source URLs, never OpenAI or Supabase secret credentials.  The guide is
also deliberately useful before a vector index or an OpenAI key has been
configured: it falls back to the project's normalized public data.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable

import httpx

from backend.config import settings
from backend.guide.links import normalize_source_url
from backend.guide.planning import UTAH_TIME, local_answer, requested_day, utah_now
from backend.guide.prompts import GUIDE_INSTRUCTIONS
from backend.guide.schemas import (
    GuideAnswerRequest,
    GuideAnswerResponse,
    GuideCitation,
    GuidePersona,
    GuideSearchRequest,
    GuideSearchResponse,
    GuideSearchResult,
)
from backend.models import Coordinate
from backend.services.dashboard import build_dashboard
from backend.supabase_client import get_service_client

logger = logging.getLogger(__name__)

OPENAI_BASE_URL = "https://api.openai.com/v1"
MAX_RETRIEVED_DOCUMENTS = 8
MAX_CONTEXT_CHARS = 1_100
MAX_EMBED_BATCH = 48

# A small, explicit vocabulary makes local fallback useful while the vector index
# warms up. The database is still the authority when its hybrid search is present.
INTEREST_TERMS = {
    "outdoors": {"trail", "hike", "camp", "ski", "climb", "bike", "park", "view", "outdoor"},
    "food": {"food", "restaurant", "coffee", "bakery", "dining", "brewery"},
    "culture": {"museum", "art", "music", "history", "theater", "culture"},
    "community": {"community", "library", "meetup", "market", "volunteer", "neighborhood"},
    "family": {"family", "kid", "children", "playground", "free"},
    "winter": {"snow", "ski", "winter", "sled", "inversion"},
    "wellness": {"walk", "yoga", "wellness", "quiet", "accessible"},
}


@dataclass(slots=True)
class RetrievedDocument:
    document_key: str
    entity_type: str
    entity_id: str | None
    title: str
    content: str
    source_name: str
    source_url: str | None
    tags: list[str]
    latitude: float | None
    longitude: float | None
    freshness: str | None
    score: float = 0.0
    starts_at: datetime | None = None
    is_demo: bool = False

    def citation(self, label: str | None = None) -> GuideCitation:
        kind = self.entity_type if self.entity_type in {"place", "event", "advisory", "story"} else "other"
        return GuideCitation(
            id=self.document_key,
            label=label,
            title=self.title,
            url=normalize_source_url(self.source_url, title=self.title, entity_id=self.entity_id),
            source_name=self.source_name,
            kind=kind,
            freshness=self.freshness,
        )

    def result(self) -> GuideSearchResult:
        excerpt = re.sub(r"\s+", " ", self.content).strip()
        return GuideSearchResult(
            id=self.document_key,
            entity_type=self.entity_type if self.entity_type in {"place", "event", "advisory", "story"} else "other",
            entity_id=self.entity_id,
            title=self.title,
            excerpt=excerpt[:280] + ("…" if len(excerpt) > 280 else ""),
            tags=self.tags,
            latitude=self.latitude,
            longitude=self.longitude,
            score=round(self.score, 4),
            citation=self.citation(),
        )


def guide_status() -> dict[str, object]:
    """Safe configuration state for the UI and deployment diagnostics."""
    return {
        "status": "ready" if settings.openai_api_key else "fallback_ready",
        "openai_configured": bool(settings.openai_api_key),
        "live_web_available": bool(settings.openai_api_key and settings.openai_enable_web_search),
        "supabase_write_configured": bool(get_service_client()),
    }


def _clean_query(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def _query_terms(text: str) -> set[str]:
    stopwords = {"the", "and", "for", "with", "that", "want", "then", "will", "not", "can", "you", "our", "fun", "plan"}
    return {term.lower() for term in re.findall(r"[a-zA-Z]{3,}", text)} - stopwords


def _parse_timestamp(value: Any) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        # Never interpret an upstream naive date in the execution machine's timezone.
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTAH_TIME)
    except (TypeError, ValueError):
        return None


def _persona_terms(persona: GuidePersona | None) -> set[str]:
    if not persona:
        return set()
    terms = set(persona.interests)
    terms.update(persona.access_needs)
    if persona.companions:
        terms.add(persona.companions)
    if persona.budget:
        terms.add(persona.budget)
    return {str(term).lower() for term in terms}


def _vector_literal(values: Iterable[float]) -> str:
    """Format a pgvector literal without ever interpolating untrusted text."""
    return "[" + ",".join(f"{float(value):.8g}" for value in values) + "]"


def _to_document(row: dict[str, Any]) -> RetrievedDocument:
    score = row.get("score") or row.get("rank") or 0
    metadata = row.get("metadata") or {}
    starts = re.search(r"\bStarts:\s*(\d{4}-\d\d-\d\d[ T][^\n]+)", str(row.get("content") or ""))
    return RetrievedDocument(
        document_key=str(row.get("document_key") or row.get("id")),
        entity_type=str(row.get("entity_type") or "other"),
        entity_id=str(row["entity_id"]) if row.get("entity_id") else None,
        title=str(row.get("title") or "Untitled source"),
        content=str(row.get("content") or ""),
        source_name=str(row.get("source_name") or "Curated"),
        source_url=row.get("source_url"),
        tags=[str(tag) for tag in (row.get("tags") or [])],
        latitude=float(row["latitude"]) if row.get("latitude") is not None else None,
        longitude=float(row["longitude"]) if row.get("longitude") is not None else None,
        freshness=str(row["freshness_at"]) if row.get("freshness_at") else None,
        score=float(score),
        starts_at=_parse_timestamp(metadata.get("starts_at") or (starts.group(1).strip().rstrip(".") if starts else None)),
        is_demo=metadata.get("is_demo") is True,
    )


async def _create_embeddings(texts: list[str]) -> list[list[float]] | None:
    """Create embeddings only from server-side configuration; fail closed to keyword search."""
    if not settings.openai_api_key or not texts:
        return None
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(18.0)) as client:
            response = await client.post(
                f"{OPENAI_BASE_URL}/embeddings",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json={"model": settings.openai_embedding_model, "input": texts},
            )
            response.raise_for_status()
            payload = response.json()
        return [item["embedding"] for item in sorted(payload.get("data", []), key=lambda item: item["index"])]
    except (httpx.HTTPError, KeyError, TypeError, ValueError) as error:
        # Never include API headers, request bodies, or credentials in a log message.
        logger.info("Guide embeddings unavailable; using keyword retrieval (%s)", type(error).__name__)
        return None


async def _search_hybrid_index(
    query: str, lat: float, lng: float, persona: GuidePersona | None
) -> tuple[list[RetrievedDocument], str]:
    """Search the Supabase RPC when the RAG migration and service key are live."""
    client = get_service_client()
    if client is None:
        return [], "fallback"

    embeddings = await _create_embeddings([query])
    params: dict[str, object] = {
        "query_text": query,
        "query_embedding": _vector_literal(embeddings[0]) if embeddings else None,
        "match_count": MAX_RETRIEVED_DOCUMENTS,
        "interest_tags": sorted(_persona_terms(persona)),
        "origin_lat": lat,
        "origin_lng": lng,
    }
    try:
        response = await asyncio.to_thread(lambda: client.rpc("search_guide_documents", params).execute())
    except Exception as error:
        # During a rolling deploy the API may arrive before the migration. The UI still works.
        logger.info("Guide index unavailable; using normalized-data fallback (%s)", type(error).__name__)
        return [], "fallback"
    documents = [_to_document(row) for row in (response.data or [])]
    return documents, "hybrid" if embeddings else "keyword"


def _local_score(document: RetrievedDocument, query: str, persona: GuidePersona | None) -> float:
    terms = _query_terms(query)
    haystack = f"{document.title} {document.content} {' '.join(document.tags)}".lower()
    words = _query_terms(haystack)
    lexical = len(terms.intersection(words))
    # A multi-stop request should surface a real trail or lunch candidate even
    # when a more generic place repeats words such as 'local' or 'morning'.
    if {"hike", "hiking", "trail"}.intersection(terms) and "trailhead" in document.tags:
        lexical += 3
    if {"lunch", "restaurant", "restraunt", "food"}.intersection(terms) and "food" in document.tags:
        lexical += 3
    interest = 0.0
    for interest_name in _persona_terms(persona):
        synonyms = INTEREST_TERMS.get(interest_name, {interest_name})
        if synonyms.intersection(set(document.tags)) or any(term in haystack for term in synonyms):
            interest += 0.7
    if document.entity_type == "advisory" and {"road", "weather", "condition", "closure"}.intersection(terms):
        lexical += 2
    if document.entity_type == "event" and {"event", "tonight", "weekend", "music", "community"}.intersection(terms):
        lexical += 1.5
    return lexical + interest


async def _fallback_documents(query: str, lat: float, lng: float, persona: GuidePersona | None) -> list[RetrievedDocument]:
    """Turn the existing normalized dashboard contract into source-aware RAG context."""
    try:
        dashboard = await build_dashboard(Coordinate(lat=lat, lng=lng))
        places, events, advisories = dashboard.places, dashboard.events, dashboard.advisories
    except Exception as error:
        # A blocked upstream must not turn a Vercel preview or local demo into a
        # blank agent. These transparent sample records retain named source links.
        logger.warning("Guide fallback data unavailable; using demo sources (%s)", type(error).__name__)
        from backend.seed_data import demo_advisories, demo_events, demo_places

        places, events, advisories = demo_places(), demo_events(), demo_advisories()

    documents: list[RetrievedDocument] = []
    for place in places:
        documents.append(
            RetrievedDocument(
                document_key=f"place:{place.id}",
                entity_type="place",
                entity_id=place.id,
                title=place.name,
                content=place.description,
                source_name=place.source,
                source_url=place.source_url,
                tags=[place.category, *place.tags],
                latitude=place.coordinate.lat,
                longitude=place.coordinate.lng,
                freshness=place.updated_at.isoformat(),
            )
        )
    for event in events:
        documents.append(
            RetrievedDocument(
                document_key=f"event:{event.id}",
                entity_type="event",
                entity_id=event.id,
                title=event.title,
                content=f"{event.category}. {event.venue}. {event.price_label}.",
                source_name=event.source,
                source_url=event.source_url,
                tags=[event.category, "event"],
                latitude=event.coordinate.lat,
                longitude=event.coordinate.lng,
                # The event date is a schedule, not the time its source was checked.
                freshness=None,
                starts_at=event.starts_at,
            )
        )
    for advisory in advisories:
        documents.append(
            RetrievedDocument(
                document_key=f"advisory:{advisory.id}",
                entity_type="advisory",
                entity_id=advisory.id,
                title=advisory.title,
                content=advisory.body,
                source_name=advisory.source,
                source_url=advisory.source_url,
                tags=[advisory.kind, advisory.severity, "conditions"],
                latitude=None,
                longitude=None,
                freshness=None,
            )
        )
    for document in documents:
        document.score = _local_score(document, query, persona)
    return sorted(documents, key=lambda document: document.score, reverse=True)[:MAX_RETRIEVED_DOCUMENTS]


async def retrieve(query: str, lat: float, lng: float, persona: GuidePersona | None) -> tuple[list[RetrievedDocument], str]:
    """Prefer persisted hybrid RAG; transparently fall back to live normalized data."""
    documents, mode = await _search_hybrid_index(query, lat, lng, persona)
    if documents:
        return documents, mode
    return await _fallback_documents(query, lat, lng, persona), "fallback"


def _prepare_documents(documents: list[RetrievedDocument], question: str) -> list[RetrievedDocument]:
    """Keep samples and off-date events out of factual itinerary context."""
    target, conflict = requested_day(question)
    legacy_sample_titles = {"community creative workshop", "campus community event", "live music tonight"}
    prepared = []
    for document in documents:
        document.source_url = normalize_source_url(document.source_url, title=document.title, entity_id=document.entity_id)
        if document.entity_type == "event":
            sample = (
                document.is_demo or document.source_name.lower().startswith("demo")
                or document.title.lower().startswith("sample:")
                or document.title.lower() in legacy_sample_titles
            )
            if sample:
                # A genuine organizer calendar is still useful, but sample names
                # and fabricated relative start times must never reach the model.
                if document.source_url:
                    calendar_name = re.sub(r" (?:event )?calendar$", "", document.source_name.removeprefix("Demo — "))
                    prepared.append(RetrievedDocument(
                        document_key=f"calendar:{document.entity_id}", entity_type="other", entity_id=None,
                        title=f"{calendar_name} events calendar",
                        content="Browse the organizer’s calendar for current dates. No event on the requested date has been confirmed.",
                        source_name=calendar_name, source_url=document.source_url,
                        tags=["event-calendar"], latitude=document.latitude, longitude=document.longitude,
                        freshness=None, score=document.score,
                    ))
                continue
            if document.starts_at:
                start = document.starts_at.astimezone(UTAH_TIME)
                if start < utah_now() or (target and not conflict and start.date() != target):
                    continue
        prepared.append(document)
    return prepared


def _persona_nudge(persona: GuidePersona | None) -> str | None:
    if not persona or len(persona.compact()) < 3:
        return "Want ideas that feel more like you? Add your interests whenever you like."
    return None


def _context_block(documents: list[RetrievedDocument]) -> str:
    if not documents:
        return "No verified local documents were available for this question."
    blocks = []
    for index, document in enumerate(documents, start=1):
        excerpt = re.sub(r"\s+", " ", document.content).strip()[:MAX_CONTEXT_CHARS]
        blocks.append(
            f"[S{index}] title={document.title}\n"
            f"source={document.source_name}\n"
            f"url={document.source_url or 'not provided'}\n"
            f"tags={', '.join(document.tags)}\n"
            f"scheduled_start={document.starts_at.astimezone(UTAH_TIME).isoformat() if document.starts_at else 'not confirmed'}\n"
            f"content={excerpt}"
        )
    return "\n\n".join(blocks)


def _response_text(payload: dict[str, Any]) -> str:
    """Read either the convenient field or the canonical Responses output array."""
    if isinstance(payload.get("output_text"), str) and payload["output_text"].strip():
        return payload["output_text"].strip()
    fragments: list[str] = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") in {"output_text", "text"} and content.get("text"):
                fragments.append(str(content["text"]))
    return "\n".join(fragments).strip()


def _response_with_citations(payload: dict[str, Any]) -> tuple[str, list[GuideCitation]]:
    """Map Responses URL annotations to explicit clickable W-labels in our UI."""
    citations: dict[str, GuideCitation] = {}
    fragments = []
    for item in payload.get("output", []):
        if item.get("type") != "message":
            continue
        for content in item.get("content", []):
            if content.get("type") not in {"output_text", "text"}:
                continue
            text = str(content.get("text") or "")
            edits: dict[tuple[int, int], list[str]] = {}
            trailing = []
            for annotation in content.get("annotations", []):
                details = annotation.get("url_citation") or annotation
                if annotation.get("type") != "url_citation":
                    continue
                url = normalize_source_url(details.get("url"))
                if not url:
                    continue
                if url not in citations:
                    label = f"W{len(citations) + 1}"
                    citations[url] = GuideCitation(
                        id=f"web:{label}", label=label, title=str(details.get("title") or "Web source"),
                        url=url, source_name="Live web result", kind="web",
                    )
                marker = f"[{citations[url].label}]"
                start, end = details.get("start_index"), details.get("end_index")
                if isinstance(start, int) and isinstance(end, int) and 0 <= start <= end <= len(text):
                    span = text[start:end]
                    # Some API versions annotate the claim; keep that prose. Only
                    # replace actual citation syntax. Otherwise append its label.
                    citation_syntax = bool(re.fullmatch(r"(?:\ue200[^\ue201]*\ue201|【[^】]*】|\[[^\]]*\](?:\([^\n]*\))?)", span))
                    position = (start, end) if citation_syntax else (end, end)
                    edits.setdefault(position, []).append(marker)
                else:
                    trailing.append(marker)
            for (start, end), markers in sorted(edits.items(), reverse=True):
                text = text[:start] + " " + " ".join(dict.fromkeys(markers)) + text[end:]
            text = re.sub(r"\ue200[^\ue201]*\ue201", "", text)
            if trailing:
                text += " " + " ".join(dict.fromkeys(trailing))
            fragments.append(text.strip())
    return "\n\n".join(fragments).strip() or _response_text(payload), list(citations.values())


def _used_citations(answer: str, candidates: list[GuideCitation]) -> list[GuideCitation]:
    """Keep original labels even when two cited records share one source URL."""
    labels = set(re.findall(r"\[([SW]\d+)\]", answer))
    linked_urls = {normalize_source_url(url) for url in re.findall(r"\]\((https?://[^\s)]+)\)", answer)}
    return [citation for citation in candidates if citation.label in labels or (citation.url and citation.url in linked_urls)]


async def _generate_answer(
    request: GuideAnswerRequest, documents: list[RetrievedDocument]
) -> tuple[str | None, list[GuideCitation], bool]:
    """Call Responses on the server, allowing web search only when the visitor opts in."""
    if not settings.openai_api_key:
        return None, [], False

    persona = request.persona.compact() if request.persona else {}
    input_text = (
        f"Current Utah date and time: {utah_now().isoformat()} (America/Denver)\n"
        f"Visitor question: {request.query}\n"
        f"Map center: {request.lat:.4f}, {request.lng:.4f}\n"
        f"Preference-only persona: {persona or 'not completed'}\n\n"
        f"Retrieved source pack:\n{_context_block(documents)}"
    )
    payload: dict[str, Any] = {
        "model": settings.openai_model,
        "instructions": GUIDE_INSTRUCTIONS,
        "input": input_text,
        "max_output_tokens": 1800,
        "store": False,
    }
    use_web = bool(request.use_live_web and settings.openai_enable_web_search)
    if use_web:
        payload["tools"] = [{"type": "web_search"}]
    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0)) as client:
            response = await client.post(
                f"{OPENAI_BASE_URL}/responses",
                headers={"Authorization": f"Bearer {settings.openai_api_key}"},
                json=payload,
            )
            response.raise_for_status()
            result = response.json()
        answer, citations = _response_with_citations(result)
        web_used = any(item.get("type") == "web_search_call" for item in result.get("output", []))
        # Partial responses often end mid-link or mid-itinerary. Use the complete
        # local reply rather than display broken Markdown or unsupported stops.
        if result.get("status") == "incomplete":
            return None, [], web_used
        return answer or None, citations, web_used
    except (httpx.HTTPError, TypeError, ValueError) as error:
        logger.info("Guide response unavailable; returning source-backed fallback (%s)", type(error).__name__)
        return None, [], False


def _local_answer(question: str, documents: list[RetrievedDocument]) -> str:
    return local_answer(question, documents)


async def search_guide(request: GuideSearchRequest) -> GuideSearchResponse:
    query = _clean_query(request.query)
    documents, retrieval = await retrieve(query, request.lat, request.lng, request.persona)
    return GuideSearchResponse(results=[document.result() for document in documents], retrieval=retrieval)


async def answer_guide(request: GuideAnswerRequest) -> GuideAnswerResponse:
    request.query = _clean_query(request.query)
    documents, retrieval = await retrieve(request.query, request.lat, request.lng, request.persona)
    documents = _prepare_documents(documents, request.query)
    generated, web_citations, live_web_used = await _generate_answer(request, documents)
    answer = generated or _local_answer(request.query, documents)
    citations = [document.citation(f"S{index}") for index, document in enumerate(documents, 1)]
    citations = _used_citations(answer, [*citations, *web_citations])
    # An unsupported model label should not become a dangling link in the UI.
    valid_labels = {citation.label for citation in citations}
    answer = re.sub(r"\[([SW]\d+)\]", lambda match: match.group(0) if match.group(1) in valid_labels else "", answer)
    return GuideAnswerResponse(
        answer=answer,
        citations=citations,
        retrieval=retrieval,
        agent="openai" if generated else "local_fallback",
        persona_nudge=_persona_nudge(request.persona),
        live_web_used=live_web_used,
    )


async def refresh_guide_index() -> dict[str, object]:
    """Mirror normalized data into RAG documents and embed changed rows in bounded batches."""
    client = get_service_client()
    if client is None:
        return {"status": "skipped", "reason": "supabase_write_not_configured"}
    try:
        refresh = await asyncio.to_thread(lambda: client.rpc("refresh_guide_documents").execute())
    except Exception as error:
        logger.warning("Guide refresh failed (%s)", type(error).__name__)
        return {"status": "failed", "reason": "guide_schema_unavailable"}

    result: dict[str, object] = {"status": "succeeded", "refresh": refresh.data or {}}
    if not settings.openai_api_key:
        result["embeddings"] = "skipped_openai_not_configured"
        return result
    try:
        pending = await asyncio.to_thread(
            lambda: client.table("guide_documents")
            .select("document_key,title,content")
            .is_("embedding", "null")
            .limit(MAX_EMBED_BATCH)
            .execute()
        )
        rows = pending.data or []
        texts = [f"{row['title']}\n{row['content'][:6000]}" for row in rows]
        vectors = await _create_embeddings(texts)
        if vectors is None:
            result["embeddings"] = "deferred"
            return result
        for row, vector in zip(rows, vectors, strict=True):
            await asyncio.to_thread(
                lambda row=row, vector=vector: client.table("guide_documents")
                .update({"embedding": _vector_literal(vector)})
                .eq("document_key", row["document_key"])
                .execute()
            )
        result["embeddings"] = len(vectors)
    except Exception as error:
        logger.warning("Guide embedding refresh deferred (%s)", type(error).__name__)
        result["embeddings"] = "deferred"
    return result
