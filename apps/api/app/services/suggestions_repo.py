"""Storage for conference_suggestions.

Two consumers today: the seed migration (writes once, source='seed') and the
Luma scraper (continuously adds source='luma' rows from event hosts and
featured_guests).

Re-scrape idempotence is by PK collision — the scraper-generated id is
`luma:<ascii-slug>` so the same human in two different events upserts once.
We deliberately do NOT dedupe against seed rows here; if both a seed entry
'stani' and a scraped 'luma:stani-kulechov' exist, that's fine — they're
separate sources and a display-time join can collapse them later.
"""

from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from threading import RLock
from typing import Any, Protocol

from app.config import Settings, get_settings


def _slugify(value: str) -> str:
    """Stable id input. ASCII-folded + lowercased so casing/accent variants
    of the same name produce the same id and collide on the PK."""
    folded = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", folded.lower()).strip("-")


class SuggestionsRepo(Protocol):
    def list_for_conference(self, conference_id: str) -> list[dict[str, Any]]: ...
    def upsert_luma_person(
        self,
        *,
        conference_id: str,
        name: str,
        role: str | None,
        source_event_id: str | None = None,
    ) -> bool: ...
    def upsert_llm_person(
        self,
        *,
        conference_id: str,
        name: str,
        role: str | None,
    ) -> str | None:
        """Upsert a person discovered by the LLM. Returns the row id on
        success (whether it was inserted or already existed), or None if the
        name was empty / unslugifiable. Ids are `llm:<slug>` and collide
        with prior LLM rows for the same human across runs."""
        ...

    def upsert_manual_person(
        self,
        *,
        conference_id: str,
        name: str,
        role: str | None,
    ) -> str | None:
        """Upsert an admin-created person. Id pattern `manual:<slug>`.
        Returns the id, or None if name is empty/unslugifiable."""
        ...

    def get_by_id(
        self, suggestion_id: str
    ) -> dict[str, Any] | None: ...

    def update_fields(
        self,
        suggestion_id: str,
        *,
        name: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any] | None:
        """Patch name/role. Pass None to leave unchanged; pass empty-string to
        explicit-clear (role only — name is required). Returns the updated row
        or None if the id doesn't exist."""
        ...

    def delete_for_conference(
        self,
        conference_id: str,
        *,
        source: str | None = None,
    ) -> int:
        """Bulk delete. `source` filters to rows with that source (e.g. 'llm'),
        or None to delete every row for the conference. FK cascade clears
        related event_suggestions rows. Returns count deleted."""
        ...


# ---------- in-memory ----------


class InMemorySuggestionsRepo:
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def list_for_conference(self, conference_id: str) -> list[dict[str, Any]]:
        with self._lock:
            return [
                dict(r) for r in self._rows.values()
                if r["conference_id"] == conference_id
            ]

    def upsert_luma_person(
        self,
        *,
        conference_id: str,
        name: str,
        role: str | None,
        source_event_id: str | None = None,
    ) -> bool:
        normalized = (name or "").strip()
        if not normalized:
            return False
        slug = _slugify(normalized)
        if not slug:
            return False
        row_id = f"luma:{slug}"
        with self._lock:
            # PK-collision dedup. If a scrape from event A already wrote
            # this person, keep the first role text — UI consistency over
            # whichever event ran the scraper last.
            if row_id in self._rows:
                return False
            self._rows[row_id] = {
                "id": row_id,
                "conference_id": conference_id,
                "kind": "people",
                "name": normalized,
                "role": (role or "").strip() or None,
                "source": "luma",
                "meta": {"source_event_id": source_event_id} if source_event_id else {},
            }
            return True

    def upsert_llm_person(
        self,
        *,
        conference_id: str,
        name: str,
        role: str | None,
    ) -> str | None:
        return self._upsert_with_prefix(
            conference_id=conference_id, name=name, role=role, prefix="llm"
        )

    def upsert_manual_person(
        self,
        *,
        conference_id: str,
        name: str,
        role: str | None,
    ) -> str | None:
        return self._upsert_with_prefix(
            conference_id=conference_id, name=name, role=role, prefix="manual"
        )

    def get_by_id(self, suggestion_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._rows.get(suggestion_id)
            return dict(row) if row else None

    def _upsert_with_prefix(
        self,
        *,
        conference_id: str,
        name: str,
        role: str | None,
        prefix: str,
    ) -> str | None:
        normalized = (name or "").strip()
        if not normalized:
            return None
        slug = _slugify(normalized)
        if not slug:
            return None
        row_id = f"{prefix}:{slug}"
        with self._lock:
            if row_id not in self._rows:
                self._rows[row_id] = {
                    "id": row_id,
                    "conference_id": conference_id,
                    "kind": "people",
                    "name": normalized,
                    "role": (role or "").strip() or None,
                    "source": prefix,
                    "meta": {},
                }
            return row_id

    def update_fields(
        self,
        suggestion_id: str,
        *,
        name: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._rows.get(suggestion_id)
            if row is None:
                return None
            if name is not None:
                stripped = name.strip()
                if not stripped:
                    # name is required — empty string is invalid. Caller
                    # should validate; we silently ignore here.
                    pass
                else:
                    row["name"] = stripped
            if role is not None:
                # Empty string clears the field; non-empty replaces it.
                row["role"] = role.strip() or None
            return dict(row)

    def delete_for_conference(
        self,
        conference_id: str,
        *,
        source: str | None = None,
    ) -> int:
        with self._lock:
            to_remove = [
                rid
                for rid, r in self._rows.items()
                if r.get("conference_id") == conference_id
                and (source is None or r.get("source") == source)
            ]
            for rid in to_remove:
                del self._rows[rid]
            return len(to_remove)


# ---------- supabase ----------


class SupabaseSuggestionsRepo:
    def __init__(self, settings: Settings) -> None:
        from supabase import create_client

        self._client = create_client(settings.supabase_url, settings.supabase_service_key)

    def list_for_conference(self, conference_id: str) -> list[dict[str, Any]]:
        res = (
            self._client.table("conference_suggestions")
            .select("id,conference_id,kind,name,role,source,meta")
            .eq("conference_id", conference_id)
            .execute()
        )
        return res.data or []

    def upsert_luma_person(
        self,
        *,
        conference_id: str,
        name: str,
        role: str | None,
        source_event_id: str | None = None,
    ) -> bool:
        normalized = (name or "").strip()
        if not normalized:
            return False
        slug = _slugify(normalized)
        if not slug:
            return False
        payload = {
            "id": f"luma:{slug}",
            "conference_id": conference_id,
            "kind": "people",
            "name": normalized,
            "role": (role or "").strip() or None,
            "source": "luma",
            "meta": {"source_event_id": source_event_id} if source_event_id else {},
        }
        # ignore_duplicates on the PK so re-scrapes are no-ops and the first
        # role text wins. supabase-py returns no inserted-vs-skipped signal
        # so we return True optimistically.
        self._client.table("conference_suggestions").upsert(
            payload,
            on_conflict="id",
            ignore_duplicates=True,
        ).execute()
        return True

    def upsert_llm_person(
        self,
        *,
        conference_id: str,
        name: str,
        role: str | None,
    ) -> str | None:
        return self._upsert_with_prefix(
            conference_id=conference_id, name=name, role=role, prefix="llm"
        )

    def upsert_manual_person(
        self,
        *,
        conference_id: str,
        name: str,
        role: str | None,
    ) -> str | None:
        return self._upsert_with_prefix(
            conference_id=conference_id, name=name, role=role, prefix="manual"
        )

    def get_by_id(self, suggestion_id: str) -> dict[str, Any] | None:
        rows = (
            self._client.table("conference_suggestions")
            .select("id,conference_id,kind,name,role,source,meta")
            .eq("id", suggestion_id)
            .limit(1)
            .execute()
            .data
        ) or []
        return rows[0] if rows else None

    def _upsert_with_prefix(
        self,
        *,
        conference_id: str,
        name: str,
        role: str | None,
        prefix: str,
    ) -> str | None:
        normalized = (name or "").strip()
        if not normalized:
            return None
        slug = _slugify(normalized)
        if not slug:
            return None
        row_id = f"{prefix}:{slug}"
        payload = {
            "id": row_id,
            "conference_id": conference_id,
            "kind": "people",
            "name": normalized,
            "role": (role or "").strip() or None,
            "source": prefix,
            "meta": {},
        }
        self._client.table("conference_suggestions").upsert(
            payload,
            on_conflict="id",
            ignore_duplicates=True,
        ).execute()
        return row_id

    def update_fields(
        self,
        suggestion_id: str,
        *,
        name: str | None = None,
        role: str | None = None,
    ) -> dict[str, Any] | None:
        patch: dict[str, Any] = {}
        if name is not None and name.strip():
            patch["name"] = name.strip()
        if role is not None:
            patch["role"] = role.strip() or None
        if not patch:
            # Nothing to change — return current row so the caller can echo.
            return self.get_by_id(suggestion_id)
        res = (
            self._client.table("conference_suggestions")
            .update(patch)
            .eq("id", suggestion_id)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    def delete_for_conference(
        self,
        conference_id: str,
        *,
        source: str | None = None,
    ) -> int:
        q = (
            self._client.table("conference_suggestions")
            .delete()
            .eq("conference_id", conference_id)
        )
        if source is not None:
            q = q.eq("source", source)
        res = q.execute()
        return len(res.data or [])


# ---------- factory ----------


@lru_cache
def _build_repo() -> SuggestionsRepo:
    settings = get_settings()
    if settings.supabase_url and settings.supabase_service_key:
        return SupabaseSuggestionsRepo(settings)
    return InMemorySuggestionsRepo()


def get_suggestions_repo() -> SuggestionsRepo:
    return _build_repo()
