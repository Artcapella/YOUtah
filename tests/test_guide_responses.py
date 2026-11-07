"""Behavior regressions for the guide's family itinerary and source handling."""

import asyncio
import re
from datetime import datetime, timezone

import pytest

from backend.guide import planning, service
from backend.guide.schemas import GuideAnswerRequest, GuideCitation
from backend.guide.service import RetrievedDocument


FAMILY_REQUEST = (
    "Plan a fun saturday for my family tomorrow. We want to start with a hike "
    "on a trail that is not busy in the morning, then get lunch at a cheap "
    "restraunt, and end the evening at a local event"
)


@pytest.fixture(autouse=True)
def utah_friday(monkeypatch):
    friday = datetime(2026, 11, 6, 12, tzinfo=planning.UTAH_TIME)
    monkeypatch.setattr(planning, "utah_now", lambda: friday)
    monkeypatch.setattr(service, "utah_now", lambda: friday)
    return friday


def document(key, title, *, kind="place", content="A place to explore.", tags=None, starts=None, source="Local organizer"):
    return RetrievedDocument(
        document_key=key,
        entity_type=kind,
        entity_id=key.split(":", 1)[-1],
        title=title,
        content=content,
        source_name=source,
        source_url="https://www.slc.gov/parks/",
        tags=tags or [],
        latitude=40.76,
        longitude=-111.89,
        freshness=None,
        score=2,
        starts_at=starts,
    )


def test_family_fallback_is_a_readable_itinerary_without_invented_stops(monkeypatch):
    documents = [
        document("place:trail", "Foothill Trail", content="A foothill walk with city views.\nTags: trailhead viewpoint sunset walk", tags=["trailhead"]),
        document("event:library-workshop", "Community creative workshop", kind="event", source="Salt Lake City Public Library", starts=datetime(2026, 11, 7, 18, tzinfo=planning.UTAH_TIME)),
        document("place:art", "Neighborhood art", tags=["art"]),
    ]

    async def retrieve(*_args):
        return documents, "fallback"

    async def unavailable(*_args):
        return None, [], False

    monkeypatch.setattr(service, "retrieve", retrieve)
    monkeypatch.setattr(service, "_generate_answer", unavailable)
    response = asyncio.run(service.answer_guide(GuideAnswerRequest(query=FAMILY_REQUEST)))

    assert "### Morning" in response.answer
    assert "### Lunch" in response.answer
    assert "### Evening" in response.answer
    assert "Saturday, November 7" in response.answer
    assert "**Foothill Trail**" in response.answer
    assert "don’t have crowd counts" in response.answer
    assert "don’t have a restaurant with current menu prices" in response.answer
    assert "don’t have a confirmed evening event" in response.answer
    assert "not a confirmed event" in response.answer
    assert "Community creative workshop" not in response.answer
    assert "2026-11-07T" not in response.answer
    assert "trailhead viewpoint sunset walk" not in response.answer
    assert "Neighborhood art" not in response.answer
    labels = set(re.findall(r"\[([SW]\d+)\]", response.answer))
    assert {citation.label for citation in response.citations} == labels
    assert all(citation.title != "Neighborhood art" for citation in response.citations)


def test_event_dates_use_utah_day_and_samples_become_calendars():
    same_utah_evening = document(
        "event:real", "Neighborhood concert", kind="event",
        # Sunday UTC is still Saturday evening in Utah.
        starts=datetime(2026, 11, 8, 1, tzinfo=timezone.utc),
    )
    docs = [
        document("event:past", "Last week's concert", kind="event", starts=datetime(2026, 11, 5, 18, tzinfo=planning.UTAH_TIME)),
        document("event:sunday", "Sunday concert", kind="event", starts=datetime(2026, 11, 8, 18, tzinfo=planning.UTAH_TIME)),
        same_utah_evening,
        document("event:library-workshop", "Sample: workshop", kind="event", source="Demo — Salt Lake City Public Library calendar", starts=datetime(2026, 11, 7, 19, tzinfo=planning.UTAH_TIME)),
    ]

    prepared = service._prepare_documents(docs, FAMILY_REQUEST)

    assert [doc.title for doc in prepared if doc.entity_type == "event"] == ["Neighborhood concert"]
    calendars = [doc for doc in prepared if "event-calendar" in doc.tags]
    assert len(calendars) == 1
    assert calendars[0].starts_at is None
    assert "Sample:" not in calendars[0].title
    answer = planning.local_answer(FAMILY_REQUEST, prepared)
    assert "Saturday, November 7 at 6:00 PM Mountain time" in answer
    assert "Sunday concert" not in answer


def test_undated_event_is_never_presented_as_tomorrows_evening_booking():
    unknown = document("event:undated", "Undated concert", kind="event", content="An event without a confirmed schedule.")
    answer = planning.local_answer(FAMILY_REQUEST, [unknown])

    assert "Undated concert" not in answer
    assert "don’t have a confirmed evening event" in answer


def test_contradictory_tomorrow_and_weekday_requests_are_clarified():
    answer = planning.local_answer("Plan my family Sunday tomorrow", [document("place:trail", "Trail", tags=["trailhead"])])

    assert "two different dates" in answer
    assert "Saturday, November 7" in answer
    assert "Which day" in answer


def test_only_used_citations_are_returned_without_losing_same_url_labels():
    shared_url = "https://www.slc.gov/parks/"
    candidates = [
        GuideCitation(id=f"source:{index}", label=f"S{index}", title=f"Source {index}", url=shared_url, source_name="Salt Lake City")
        for index in range(1, 5)
    ]

    used = service._used_citations("Start here [S2], then try this [S4].", candidates)

    assert [citation.label for citation in used] == ["S2", "S4"]
    assert [citation.title for citation in used] == ["Source 2", "Source 4"]


def test_responses_web_annotations_become_clickable_stable_labels():
    text = "Try the library calendar for an event."
    payload = {
        "output": [{
            "type": "message",
            "content": [{
                "type": "output_text",
                "text": text,
                "annotations": [{
                    "type": "url_citation",
                    "start_index": 8,
                    "end_index": 24,
                    "title": "Library events",
                    "url": "https://events.slcpl.org/",
                }],
            }],
        }],
    }

    answer, citations = service._response_with_citations(payload)

    assert "[W1]" in answer
    assert len(citations) == 1
    assert citations[0].label == "W1"
    assert citations[0].url == "https://events.slcpl.org/"
    assert citations[0].title == "Library events"
