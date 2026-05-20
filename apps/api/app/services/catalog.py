"""
Catalog repository: read access for conferences, days, and events.

Two backends:
- SupabaseCatalogRepo — uses supabase-py service-role client against Postgres.
- InMemoryCatalogRepo — mirrors the seed migration; selected automatically when
  Supabase env vars are missing. Lets the API run locally without a live DB.

`get_catalog_repo` is the FastAPI dependency. Tests can override it.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Protocol

from app.config import Settings, get_settings
from app.models.schemas import ConferenceDayOut, ConferenceOut, EventOut
from app.services import seed_data


class CatalogRepo(Protocol):
    def list_conferences(self, *, include_inactive: bool = False) -> list[ConferenceOut]: ...
    def get_conference(self, conference_id: str) -> ConferenceOut | None: ...
    def list_events(self, conference_id: str) -> list[EventOut]: ...


# ---------- in-memory backend ----------


class InMemoryCatalogRepo:
    def _days_for(self, conference_id: str) -> list[ConferenceDayOut]:
        return [
            ConferenceDayOut(**d)
            for d in seed_data.CONFERENCE_DAYS
            if d["conference_id"] == conference_id
        ]

    def list_conferences(self, *, include_inactive: bool = False) -> list[ConferenceOut]:
        return [
            ConferenceOut(**{**c, "days": self._days_for(c["id"])})
            for c in seed_data.CONFERENCES
            if include_inactive or c.get("is_active", True)
        ]

    def get_conference(self, conference_id: str) -> ConferenceOut | None:
        for c in seed_data.CONFERENCES:
            if c["id"] == conference_id:
                return ConferenceOut(**{**c, "days": self._days_for(conference_id)})
        return None

    def list_events(self, conference_id: str) -> list[EventOut]:
        return [
            EventOut(
                id=e["id"],
                conference_id=e["conference_id"],
                title=e["title"],
                description=e.get("description"),
                start=e["starts_at"],
                end=e["ends_at"],
                venue=e.get("venue"),
                tags=list(e.get("tags", [])),
                url=e.get("url"),
                capacity=e.get("capacity"),
                attendees=e.get("attendees"),
                cover_url=e.get("cover_url"),
                tint_color=e.get("tint_color"),
            )
            for e in seed_data.EVENTS
            if e["conference_id"] == conference_id
        ]


# ---------- supabase backend ----------


class SupabaseCatalogRepo:
    """Uses supabase-py with the service-role key. RLS is bypassed."""

    def __init__(self, settings: Settings) -> None:
        from supabase import create_client  # imported lazily so tests don't need it

        self._client = create_client(settings.supabase_url, settings.supabase_service_key)

    def list_conferences(self, *, include_inactive: bool = False) -> list[ConferenceOut]:
        q = self._client.table("conferences").select("*")
        if not include_inactive:
            q = q.eq("is_active", True)
        confs = q.execute().data or []
        days = self._client.table("conference_days").select("*").execute().data or []
        by_conf: dict[str, list[ConferenceDayOut]] = {}
        for d in days:
            by_conf.setdefault(d["conference_id"], []).append(ConferenceDayOut(**d))
        return [ConferenceOut(**{**c, "days": by_conf.get(c["id"], [])}) for c in confs]

    def get_conference(self, conference_id: str) -> ConferenceOut | None:
        # supabase-py's maybe_single().execute() returns None on no-match in
        # current versions — using limit(1) is more portable.
        res = (
            self._client.table("conferences")
            .select("*")
            .eq("id", conference_id)
            .limit(1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            return None
        row = rows[0]
        days = (
            self._client.table("conference_days")
            .select("*")
            .eq("conference_id", conference_id)
            .order("day_num")
            .execute()
            .data
            or []
        )
        return ConferenceOut(**{**row, "days": [ConferenceDayOut(**d) for d in days]})

    def list_events(self, conference_id: str) -> list[EventOut]:
        rows = (
            self._client.table("events")
            .select(
                "id,conference_id,title,description,starts_at,ends_at,venue,tags,url,"
                "capacity,attendees,cover_url,tint_color"
            )
            .eq("conference_id", conference_id)
            .order("starts_at")
            .execute()
            .data
            or []
        )
        return [
            EventOut(
                id=r["id"],
                conference_id=r["conference_id"],
                title=r["title"],
                description=r.get("description"),
                start=r["starts_at"],
                end=r["ends_at"],
                venue=r.get("venue"),
                tags=list(r.get("tags") or []),
                url=r.get("url"),
                capacity=r.get("capacity"),
                attendees=r.get("attendees"),
                cover_url=r.get("cover_url"),
                tint_color=r.get("tint_color"),
            )
            for r in rows
        ]


# ---------- FastAPI dependency ----------


@lru_cache
def _build_repo() -> CatalogRepo:
    settings = get_settings()
    if settings.supabase_url and settings.supabase_service_key:
        return SupabaseCatalogRepo(settings)
    return InMemoryCatalogRepo()


def get_catalog_repo() -> CatalogRepo:
    return _build_repo()
