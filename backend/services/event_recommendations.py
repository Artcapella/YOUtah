
from datetime import date

from backend.models import LocalEvent


def filter_events(
    events: list[LocalEvent],
    category: str = "any",
    start_date: date | None = None,
    end_date: date | None = None,
    max_budget: float | None = None,
) -> list[LocalEvent]:
    """Filter events according to user preferences."""

    filtered = []

    for event in events:
        # Filter by category
        if category.lower() != "any":
            if event.category.lower() != category.lower():
                continue

        # Filter by event date
        event_date = event.starts_at.date()

        if start_date and event_date < start_date:
            continue

        if end_date and event_date > end_date:
            continue

        # Budget filtering will be added when we have
        # numeric price data from Ticketmaster.
        filtered.append(event)

    return filtered


def calculate_event_score(
    event: LocalEvent,
    preferred_category: str = "any",
) -> int:
    """Return a basic recommendation score from 0 to 100."""

    score = 50

    # Reward matching the user's interests.
    if preferred_category.lower() != "any":
        if event.category.lower() == preferred_category.lower():
            score += 30

    # Free events are generally more accessible.
    if event.is_free:
        score += 10

    return min(score, 100)


def recommend_events(
    events: list[LocalEvent],
    category: str = "any",
    start_date: date | None = None,
    end_date: date | None = None,
    max_budget: float | None = None,
) -> list[dict]:
    """Filter, score, and sort events."""

    filtered = filter_events(
        events,
        category=category,
        start_date=start_date,
        end_date=end_date,
        max_budget=max_budget,
    )

    recommendations = []

    for event in filtered:
        score = calculate_event_score(event, category)

        recommendations.append({
            "event": event.model_dump(mode="json"),
            "score": score,
            "reason": (
                "Matches your preferred category"
                if category != "any"
                else "Upcoming event in your area"
            ),
        })

    recommendations.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    return recommendations
