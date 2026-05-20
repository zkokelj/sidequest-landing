"""Storage for the event_suggestions join table.

A single row pins one person/company/speaker (`conference_suggestions.id`) to
one event. Three write sources today: 'llm' from the generate-people endpoint,
'manual' from the admin events form (phase 2), 'luma' reserved for later.

Inserts go through `upsert_links` which calls Postgres ON CONFLICT to refresh
source/confidence rather than duplicate — important because the LLM endpoint
is re-runnable.
"""

from __future__ import annotations

from datetime import UTC
from functools import lru_cache
from threading import RLock
from typing import Any, Literal, Protocol

from app.config import Settings, get_settings

LinkSource = Literal["llm", "manual", "luma"]


class EventSuggestionsRepo(Protocol):
    def upsert_links(
        self,
        rows: list[dict[str, Any]],
    ) -> int:
        """Bulk upsert. Each row must have event_id, suggestion_id, source,
        and optionally confidence. Returns count of rows successfully written
        (insert or update; the underlying DB does not distinguish)."""
        ...

    def list_for_event(self, event_id: str) -> list[dict[str, Any]]: ...

    def list_for_conference(
        self, conference_id: str
    ) -> list[dict[str, Any]]:
        """All links for events in a conference. Used by the per-conference
        suggestions read endpoint in phase 3."""
        ...

    def delete_link(self, event_id: str, suggestion_id: str) -> bool:
        """Detach one person from one event. Returns True if a row was
        removed, False if no link existed."""
        ...


# ---------- in-memory ----------


class InMemoryEventSuggestionsRepo:
    def __init__(self) -> None:
        # key: (event_id, suggestion_id)
        self._rows: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = RLock()

    def upsert_links(self, rows: list[dict[str, Any]]) -> int:
        written = 0
        from datetime import datetime

        now = datetime.now(UTC)
        with self._lock:
            for r in rows:
                event_id = r.get("event_id")
                suggestion_id = r.get("suggestion_id")
                source = r.get("source")
                if not event_id or not suggestion_id or source not in {
                    "llm",
                    "manual",
                    "luma",
                }:
                    continue
                key = (event_id, suggestion_id)
                existing = self._rows.get(key)
                self._rows[key] = {
                    "event_id": event_id,
                    "suggestion_id": suggestion_id,
                    "source": source,
                    "confidence": r.get("confidence"),
                    "created_at": existing["created_at"] if existing else now,
                }
                written += 1
        return written

    def list_for_event(self, event_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(r)
                for (eid, _), r in self._rows.items()
                if eid == event_id
            ]

    def list_for_conference(
        self, conference_id: str
    ) -> list[dict[str, Any]]:
        # In-memory has no conference link; tests that need this should
        # spin up SupabaseEventSuggestionsRepo (or extend the mock).
        with self._lock:
            return [dict(r) for r in self._rows.values()]

    def delete_link(self, event_id: str, suggestion_id: str) -> bool:
        with self._lock:
            return self._rows.pop((event_id, suggestion_id), None) is not None


# ---------- supabase ----------


class SupabaseEventSuggestionsRepo:
    def __init__(self, settings: Settings) -> None:
        from supabase import create_client

        self._client = create_client(settings.supabase_url, settings.supabase_service_key)

    def upsert_links(self, rows: list[dict[str, Any]]) -> int:
        if not rows:
            return 0
        clean: list[dict[str, Any]] = []
        for r in rows:
            event_id = r.get("event_id")
            suggestion_id = r.get("suggestion_id")
            source = r.get("source")
            if not event_id or not suggestion_id or source not in {
                "llm",
                "manual",
                "luma",
            }:
                continue
            clean.append(
                {
                    "event_id": event_id,
                    "suggestion_id": suggestion_id,
                    "source": source,
                    "confidence": r.get("confidence"),
                }
            )
        if not clean:
            return 0
        # on_conflict on the composite PK lets re-runs refresh source/confidence
        # rather than fail. ignore_duplicates=False so updates take effect.
        self._client.table("event_suggestions").upsert(
            clean,
            on_conflict="event_id,suggestion_id",
        ).execute()
        return len(clean)

    def list_for_event(self, event_id: str) -> list[dict[str, Any]]:
        res = (
            self._client.table("event_suggestions")
            .select("event_id,suggestion_id,source,confidence,created_at")
            .eq("event_id", event_id)
            .execute()
        )
        return res.data or []

    def list_for_conference(
        self, conference_id: str
    ) -> list[dict[str, Any]]:
        # Join via events.conference_id. supabase-py's inner-join filter syntax:
        # select fields including events!inner(conference_id) and filter on it.
        res = (
            self._client.table("event_suggestions")
            .select(
                "event_id,suggestion_id,source,confidence,created_at,"
                "events!inner(conference_id)"
            )
            .eq("events.conference_id", conference_id)
            .execute()
        )
        rows = res.data or []
        # Strip the joined column so callers see a flat shape.
        for r in rows:
            r.pop("events", None)
        return rows

    def delete_link(self, event_id: str, suggestion_id: str) -> bool:
        res = (
            self._client.table("event_suggestions")
            .delete()
            .eq("event_id", event_id)
            .eq("suggestion_id", suggestion_id)
            .execute()
        )
        return bool(res.data)


# ---------- FastAPI dependency ----------


@lru_cache
def _build_repo() -> EventSuggestionsRepo:
    settings = get_settings()
    if settings.supabase_url and settings.supabase_service_key:
        return SupabaseEventSuggestionsRepo(settings)
    return InMemoryEventSuggestionsRepo()


def get_event_suggestions_repo() -> EventSuggestionsRepo:
    return _build_repo()
