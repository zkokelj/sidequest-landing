"""
LLM-driven extraction of "who is at this conference" linked to specific events.

Input: a conference id.
Process:
  1. Load events + already-known suggestions (Luma-scraped + seed).
  2. Ask the LLM to (a) associate known people with the events that mention them,
     and (b) propose new people it spots in event descriptions/hosts that aren't
     already in the suggestions list.
  3. Validate everything against the candidate sets (drop hallucinated event_ids
     or suggestion_ids; drop empty names).
  4. Upsert new people into conference_suggestions with source='llm'.
  5. Bulk-insert (event_id, suggestion_id) links into event_suggestions with
     source='llm'.

Returns a GenerationStats dataclass for the router to surface to the admin UI.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from app.models.schemas import EventOut
from app.services.event_suggestions_repo import EventSuggestionsRepo
from app.services.llm import LLMClient, LLMResult
from app.services.suggestions_repo import SuggestionsRepo

SYSTEM_PROMPT = """You are SideQuest, an extractor that maps people to conference events.

You are given:
  - A list of `events` for one conference (id, title, description, tags).
  - A list of `known_people` already curated for this conference (id, name, role).

Your job: return strict JSON with two arrays.

{
  "associations": [
    { "suggestion_id": "<id from known_people>",
      "event_ids":     ["<event id>", ...],
      "confidence":    0.0-1.0 }
  ],
  "new_people": [
    { "name":       "<full name as written>",
      "role":       "<short role/affiliation, e.g. 'Founder, Acme'>",
      "event_ids":  ["<event id>", ...],
      "confidence": 0.0-1.0 }
  ]
}

Rules:
- Use ONLY event ids from `events`. Never invent event ids.
- Use ONLY suggestion_ids from `known_people`. Never invent suggestion ids.
- A person belongs to an event only if they're plausibly there as a speaker,
  host, featured guest, or central topic — NOT for casual mentions ("inspired
  by Vitalik") or analogies.
- `new_people` is for humans named explicitly in the event title or description
  (or implied by hosts/featured_guests sections) who do NOT already appear in
  known_people. Match casing/diacritics to the source text. Skip generic group
  names ("the team", "founders").
- `confidence` reflects how confident you are the person is actually there
  for that event. 0.9+ = explicit host/speaker mention; 0.5-0.8 = strong
  contextual signal; below 0.5 = skip the row entirely.
- Empty arrays are fine if the data doesn't support associations.
- Output JSON only. No markdown, no commentary, no code fences."""


@dataclass(slots=True)
class GenerationStats:
    events_considered: int = 0
    known_people_considered: int = 0
    new_people_created: int = 0
    associations_added: int = 0
    rejected_hallucinations: int = 0
    tokens_used: int = 0
    model: str = ""
    errors: list[str] = field(default_factory=list)


def _event_to_payload(e: EventOut) -> dict[str, Any]:
    return {
        "id": e.id,
        "title": e.title,
        "description": e.description,
        "tags": list(e.tags),
    }


def _person_to_payload(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "name": row["name"],
        "role": row.get("role"),
    }


def build_user_message(
    events: list[EventOut],
    known_people: list[dict[str, Any]],
) -> str:
    payload = {
        "events": [_event_to_payload(e) for e in events],
        "known_people": [_person_to_payload(p) for p in known_people],
    }
    return json.dumps(payload, separators=(",", ":"), default=str)


def _strip_fences(raw: str) -> str:
    s = raw.strip()
    if s.startswith("```"):
        first_newline = s.find("\n")
        if first_newline != -1:
            s = s[first_newline + 1 :]
        if s.endswith("```"):
            s = s[:-3]
    return s.strip()


def _coerce_confidence(value: Any) -> float | None:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if v < 0.0 or v > 1.0:
        return None
    return v


def parse_response(
    raw: str,
    *,
    valid_event_ids: set[str],
    valid_suggestion_ids: set[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    """Returns (associations, new_people, rejected_count).

    Each `association` is {suggestion_id, event_ids[], confidence}.
    Each `new_person`   is {name, role, event_ids[], confidence}.
    Hallucinated ids are dropped from event_ids/suggestion_id; rows that end
    up with no valid event_ids are dropped entirely and counted in rejected.
    """
    cleaned = _strip_fences(raw)
    try:
        data = json.loads(cleaned)
    except json.JSONDecodeError:
        return [], [], 0
    if not isinstance(data, dict):
        return [], [], 0

    rejected = 0
    associations: list[dict[str, Any]] = []
    for item in data.get("associations") or []:
        if not isinstance(item, dict):
            rejected += 1
            continue
        sid = item.get("suggestion_id")
        if not isinstance(sid, str) or sid not in valid_suggestion_ids:
            rejected += 1
            continue
        raw_events = item.get("event_ids") or []
        if not isinstance(raw_events, list):
            rejected += 1
            continue
        kept = [
            eid for eid in raw_events
            if isinstance(eid, str) and eid in valid_event_ids
        ]
        if not kept:
            rejected += 1
            continue
        associations.append(
            {
                "suggestion_id": sid,
                "event_ids": kept,
                "confidence": _coerce_confidence(item.get("confidence")),
            }
        )

    new_people: list[dict[str, Any]] = []
    for item in data.get("new_people") or []:
        if not isinstance(item, dict):
            rejected += 1
            continue
        name = item.get("name")
        if not isinstance(name, str) or not name.strip():
            rejected += 1
            continue
        raw_events = item.get("event_ids") or []
        if not isinstance(raw_events, list):
            rejected += 1
            continue
        kept = [
            eid for eid in raw_events
            if isinstance(eid, str) and eid in valid_event_ids
        ]
        if not kept:
            rejected += 1
            continue
        role = item.get("role")
        new_people.append(
            {
                "name": name.strip(),
                "role": role.strip() if isinstance(role, str) and role.strip() else None,
                "event_ids": kept,
                "confidence": _coerce_confidence(item.get("confidence")),
            }
        )

    return associations, new_people, rejected


async def generate_people_for_conference(
    *,
    conference_id: str,
    events: list[EventOut],
    known_people: list[dict[str, Any]],
    llm: LLMClient,
    suggestions_repo: SuggestionsRepo,
    event_suggestions_repo: EventSuggestionsRepo,
    model: str | None = None,
) -> GenerationStats:
    stats = GenerationStats(
        events_considered=len(events),
        known_people_considered=len(known_people),
    )
    if not events:
        # Nothing to extract from — return empty stats rather than calling the
        # LLM with an empty corpus.
        return stats

    user_msg = build_user_message(events, known_people)
    try:
        result: LLMResult = await llm.complete_json(
            SYSTEM_PROMPT, user_msg, model=model
        )
    except Exception as exc:
        stats.errors.append(f"llm_call_failed: {exc}")
        return stats
    stats.tokens_used = result.tokens_used
    stats.model = result.model

    valid_event_ids = {e.id for e in events}
    valid_suggestion_ids = {p["id"] for p in known_people}
    associations, new_people, rejected = parse_response(
        result.content,
        valid_event_ids=valid_event_ids,
        valid_suggestion_ids=valid_suggestion_ids,
    )
    stats.rejected_hallucinations = rejected

    links: list[dict[str, Any]] = []

    for assoc in associations:
        for event_id in assoc["event_ids"]:
            links.append(
                {
                    "event_id": event_id,
                    "suggestion_id": assoc["suggestion_id"],
                    "source": "llm",
                    "confidence": assoc.get("confidence"),
                }
            )

    for person in new_people:
        new_id = suggestions_repo.upsert_llm_person(
            conference_id=conference_id,
            name=person["name"],
            role=person.get("role"),
        )
        if not new_id:
            stats.rejected_hallucinations += 1
            continue
        stats.new_people_created += 1
        for event_id in person["event_ids"]:
            links.append(
                {
                    "event_id": event_id,
                    "suggestion_id": new_id,
                    "source": "llm",
                    "confidence": person.get("confidence"),
                }
            )

    if links:
        stats.associations_added = event_suggestions_repo.upsert_links(links)

    return stats
