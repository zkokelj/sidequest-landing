"""
Schedule merge logic — single source of truth for combining:
  - The active user_curation's schedule (LLM-curated CuratedItems)
  - The user_event_pins overlay (pinned=true adds; pinned=false hides)
  - Full event details from the catalog

Pure function; no I/O. Tested directly in tests/test_pin_overlay.py.
"""

from __future__ import annotations

from typing import Any

from app.models.schemas import EventOut, ScheduleItem


_DEFAULT_RATIONALE_PINNED = "Added by you."


def _to_schedule_item(
    event: EventOut,
    *,
    rationale: str,
    priority: str,
) -> ScheduleItem:
    return ScheduleItem(
        id=event.id,
        conference_id=event.conference_id,
        title=event.title,
        description=event.description,
        start=event.start,
        end=event.end,
        venue=event.venue,
        tags=list(event.tags),
        url=event.url,
        capacity=event.capacity,
        attendees=event.attendees,
        cover_url=event.cover_url,
        tint_color=event.tint_color,
        rationale=rationale,
        priority=priority,
        inSchedule=True,
    )


def merge_schedule(
    *,
    curated: list[dict[str, Any]],
    pins: list[dict[str, Any]],
    events: list[EventOut],
) -> list[ScheduleItem]:
    """Apply pin overlay on top of the curated schedule.

    Args:
        curated: list of CuratedItem dicts from user_curations.schedule
        pins:    rows from user_event_pins, each with event_id + pinned bool
        events:  full event catalog for the active conference

    Returns: ScheduleItem list, sorted by start.

    Rules:
      - For each curated item, include the enriched event unless the user
        has an explicit pinned=false override for that event_id.
      - For each pinned=true event NOT already in the curated set, append it
        with a default rationale and priority='must'.
      - Events referenced by curation that are no longer in the catalog
        are silently skipped.
    """
    by_id = {e.id: e for e in events}
    pin_by_id: dict[str, bool] = {p["event_id"]: bool(p["pinned"]) for p in pins}

    seen: set[str] = set()
    out: list[ScheduleItem] = []

    for item in curated:
        event_id = item.get("event_id")
        if not isinstance(event_id, str):
            continue
        if pin_by_id.get(event_id) is False:
            continue  # user explicitly hid it
        ev = by_id.get(event_id)
        if ev is None:
            continue
        out.append(
            _to_schedule_item(
                ev,
                rationale=str(item.get("rationale", "")),
                priority=str(item.get("priority", "should")),
            )
        )
        seen.add(event_id)

    for event_id, pinned in pin_by_id.items():
        if not pinned or event_id in seen:
            continue
        ev = by_id.get(event_id)
        if ev is None:
            continue
        out.append(
            _to_schedule_item(
                ev,
                rationale=_DEFAULT_RATIONALE_PINNED,
                priority="must",
            )
        )

    out.sort(key=lambda s: s.start)
    return out


def pinned_events(
    pins: list[dict[str, Any]], events: list[EventOut]
) -> list[EventOut]:
    """Return events the user has actively pinned (pinned=True only)."""
    by_id = {e.id: e for e in events}
    out = [by_id[p["event_id"]] for p in pins if p.get("pinned") and p["event_id"] in by_id]
    out.sort(key=lambda e: e.start)
    return out
