"""Readable planning replies when the language model or live feeds are unavailable."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING
from zoneinfo import ZoneInfo

if TYPE_CHECKING:
    from backend.guide.service import RetrievedDocument

UTAH_TIME = ZoneInfo("America/Denver")
WEEKDAYS = ("monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday")


def utah_now() -> datetime:
    return datetime.now(UTAH_TIME)


def requested_day(question: str, now: datetime | None = None) -> tuple[date | None, bool]:
    """Resolve simple trip dates in Utah time and flag contradictory day names."""
    today = (now or utah_now()).astimezone(UTAH_TIME).date()
    text = question.lower()
    explicit = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", text)
    target = None
    if explicit:
        try:
            target = date.fromisoformat(explicit.group(1))
        except ValueError:
            pass
    if target is None and re.search(r"\btomorrow\b", text):
        target = today + timedelta(days=1)
    elif target is None and re.search(r"\b(today|tonight)\b", text):
        target = today
    weekday = next((index for index, day in enumerate(WEEKDAYS) if re.search(rf"\b{day}\b", text)), None)
    conflict = target is not None and weekday is not None and target.weekday() != weekday
    if target is None and weekday is not None:
        offset = (weekday - today.weekday()) % 7
        target = today + timedelta(days=offset or 7)
    return target, conflict


def day_label(day: date) -> str:
    return f"{day.strftime('%A, %B')} {day.day}"


def is_itinerary(question: str) -> bool:
    return bool(re.search(r"\b(plan|itinerary|day trip|then|end the|start with)\b", question, re.I))


def _prose(document: RetrievedDocument) -> str:
    """Use editorial descriptions, leaving search tags and timestamps in the index."""
    text = re.split(r"\n(?:Tags:|Venue:|Starts:|Ends:)", document.content, maxsplit=1, flags=re.I)[0]
    text = re.sub(r"\s+", " ", text).strip()
    # Older indexed documents may have a synthetic 'Starts' field on one line.
    text = re.split(r"\bStarts\s*:", text, maxsplit=1, flags=re.I)[0].strip()
    if len(text) > 230:
        text = text[:227].rsplit(" ", 1)[0].rstrip(".,;:") + "…"
    return text


def _pick(documents: list[RetrievedDocument], tags: set[str]) -> tuple[int, RetrievedDocument] | None:
    return next(
        ((index, doc) for index, doc in enumerate(documents, 1)
         if doc.entity_type == "place" and tags.intersection(tag.lower() for tag in doc.tags)),
        None,
    )


def _recommend(pick: tuple[int, RetrievedDocument]) -> str:
    index, document = pick
    return f"**{document.title}** — {_prose(document)} [S{index}]"


def local_answer(question: str, documents: list[RetrievedDocument], now: datetime | None = None) -> str:
    """Respect requested stops instead of returning the top four unrelated records."""
    target, conflict = requested_day(question, now)
    if conflict:
        return (
            f"Happy to help plan that! I see two different dates: the date you gave falls on **{day_label(target)}**, "
            "but you also named a different weekday. Which day should I use?"
        )
    if not documents:
        return (
            "I’d love to help you find a good fit. I couldn’t load local listings just now. "
            "Which Utah town are you exploring, and what would make the day fun for you?"
        )

    if not is_itinerary(question):
        picks = [(index, doc) for index, doc in enumerate(documents, 1)
                 if doc.entity_type in {"place", "event", "story"} and doc.score > 0][:3]
        if not picks:
            return "I haven’t found a close match yet. Which Utah town or neighborhood should I focus on?"
        return "Here are a few ideas to explore:\n\n" + "\n\n".join(_recommend(pick) for pick in picks)

    text = question.lower()
    family = bool(re.search(r"\b(family|kids|children)\b", text))
    intro = "That sounds like a lovely family day!" if family else "That sounds like a great day out!"
    when = f" for **{day_label(target)}**" if target else ""
    lines = [f"{intro} Here’s a starting plan{when}, using the area around your map."]

    wants_hike = bool(re.search(r"\b(hike|hiking|trail|walk|outdoors)\b", text))
    wants_food = bool(re.search(r"\b(lunch|food|restaurant|restraunt|dinner|eat|coffee)\b", text))
    wants_event = bool(re.search(r"\b(event|evening|music|show|concert)\b", text))

    if wants_hike:
        lines.append("### Morning · Get outside")
        trail = _pick(documents, {"trailhead", "hiking", "hike", "trail"})
        if trail:
            lines.append(_recommend(trail))
        else:
            lines.append("I haven’t found a trail that fits your request yet. Your town and the kids’ comfortable hiking distance will help narrow it down.")
        if re.search(r"\b(quiet|not busy|less crowded|uncrowded)\b", text):
            if trail and "popular" in trail[1].tags:
                lines.append("This is a popular trail, so I’d look for another option if quiet is the priority. I don’t have crowd counts to compare trails yet.")
            else:
                lines.append("I don’t have crowd counts for these trails, so I can’t promise a quiet morning. Consider an early start.")

    if wants_food:
        lines.append("### Lunch · Keep it easy")
        restaurant = _pick(documents, {"restaurant", "cafe", "food"})
        if restaurant:
            lines.append(_recommend(restaurant))
            if re.search(r"\b(cheap|budget|affordable|low.cost)\b", text):
                lines.append("I don’t have a current menu price for this spot, so I can’t call it a budget pick yet.")
        else:
            lines.append("I don’t have a restaurant with current menu prices to recommend yet. A picnic is an easy backup while we find a lunch spot that fits your budget.")

    if wants_event:
        lines.append("### Evening · Find something local")
        event = next(
            ((index, doc) for index, doc in enumerate(documents, 1)
             if doc.entity_type == "event" and not doc.is_demo and doc.starts_at
             and (not target or doc.starts_at.astimezone(UTAH_TIME).date() == target)
             and doc.starts_at.astimezone(UTAH_TIME).hour >= 16),
            None,
        )
        if event:
            index, doc = event
            starts = doc.starts_at.astimezone(UTAH_TIME)
            clock = starts.strftime("%I:%M %p").lstrip("0")
            lines.append(f"**{doc.title}** — {day_label(starts.date())} at {clock} Mountain time. {_prose(doc)} [S{index}]")
            if family:
                lines.append("Check the listing’s age policy before booking; I haven’t confirmed that it’s suitable for children.")
        else:
            lines.append("I don’t have a confirmed evening event for your date yet.")
            calendar = next(((index, doc) for index, doc in enumerate(documents, 1) if "event-calendar" in doc.tags), None)
            if calendar:
                index, doc = calendar
                lines.append(f"Browse the **{doc.title}** for current listings. This is a calendar to explore, not a confirmed event. [S{index}]")

    if not any((wants_hike, wants_food, wants_event)):
        for index, doc in enumerate(documents, 1):
            if doc.entity_type == "place":
                lines.extend(["### A place to start", _recommend((index, doc))])
                break
    if family:
        lines.append("**One quick question:** How old are the kids, and how far would you like to hike?")
    else:
        lines.append("**One quick question:** Which Utah town or neighborhood should I focus on?")
    return "\n\n".join(lines)
