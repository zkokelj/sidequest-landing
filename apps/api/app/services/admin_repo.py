"""
Admin write operations on the events catalog.

Used by /api/admin/events* and (later) the scraper. The scraper_upsert
helper enforces the locked=true guarantee documented in 0001_init.sql:
locked rows are immutable from the scraper's perspective.

Service-role only — RLS is bypassed; require_admin enforces auth at the
router layer.
"""

from __future__ import annotations

from datetime import datetime, timezone
from functools import lru_cache
from threading import RLock
from typing import Any, Literal, Protocol


ImportOutcome = Literal["inserted", "updated", "skipped_locked", "skipped_conflict"]

from app.config import Settings, get_settings


_EVENT_COLUMNS = (
    "id,conference_id,title,description,starts_at,ends_at,venue,tags,url,"
    "capacity,attendees,cover_url,tint_color,source,is_manual,locked,"
    "updated_by,updated_at,created_at"
)


class EventsAdminRepo(Protocol):
    def list_events(
        self,
        *,
        conference_id: str | None = None,
        locked: bool | None = None,
        is_manual: bool | None = None,
    ) -> list[dict[str, Any]]: ...

    def get_event(self, event_id: str) -> dict[str, Any] | None: ...

    def create_event(
        self, *, fields: dict[str, Any], updated_by: str
    ) -> dict[str, Any]: ...

    def update_event(
        self,
        event_id: str,
        *,
        patch: dict[str, Any],
        updated_by: str,
    ) -> dict[str, Any] | None: ...

    def delete_event(self, event_id: str) -> bool: ...

    def set_lock(
        self, event_id: str, *, locked: bool, updated_by: str
    ) -> dict[str, Any] | None: ...

    def scraper_upsert(self, event: dict[str, Any]) -> bool:
        """Apply a scraper-style upsert.

        Returns:
            True  — row inserted or updated
            False — row exists and locked=true; scraper must not touch it
        """
        ...

    def import_event(
        self,
        event: dict[str, Any],
        *,
        updated_by: str,
        on_conflict: Literal["upsert", "skip"],
    ) -> ImportOutcome:
        """Apply one bulk-import row. New rows are inserted as
        is_manual=True, locked=True (admin-authored). Existing locked rows
        are never overwritten — admin must unlock them via the events UI
        first. Existing unlocked rows are overwritten when on_conflict is
        'upsert', skipped when 'skip'."""
        ...

    def upsert_conference(self, fields: dict[str, Any]) -> dict[str, Any]: ...


# ---------- in-memory backend (parallel to catalog seed for offline/dev) ----------


class InMemoryEventsAdminRepo:
    def __init__(self) -> None:
        self._rows: dict[str, dict[str, Any]] = {}
        self._lock = RLock()

    def _filter_rows(
        self,
        *,
        conference_id: str | None,
        locked: bool | None,
        is_manual: bool | None,
    ) -> list[dict[str, Any]]:
        out = []
        for r in self._rows.values():
            if conference_id is not None and r.get("conference_id") != conference_id:
                continue
            if locked is not None and bool(r.get("locked")) != locked:
                continue
            if is_manual is not None and bool(r.get("is_manual")) != is_manual:
                continue
            out.append(dict(r))
        out.sort(key=lambda r: r.get("starts_at") or "")
        return out

    def list_events(
        self,
        *,
        conference_id: str | None = None,
        locked: bool | None = None,
        is_manual: bool | None = None,
    ) -> list[dict[str, Any]]:
        with self._lock:
            return self._filter_rows(
                conference_id=conference_id, locked=locked, is_manual=is_manual
            )

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._rows.get(event_id)
            return dict(row) if row else None

    def create_event(
        self, *, fields: dict[str, Any], updated_by: str
    ) -> dict[str, Any]:
        with self._lock:
            now = datetime.now(timezone.utc)
            row = {
                **fields,
                "is_manual": True,
                "locked": True,
                "updated_by": updated_by,
                "created_at": now,
                "updated_at": now,
            }
            self._rows[fields["id"]] = row
            return dict(row)

    def update_event(
        self, event_id: str, *, patch: dict[str, Any], updated_by: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._rows.get(event_id)
            if row is None:
                return None
            row.update(patch)
            row["locked"] = True
            row["updated_by"] = updated_by
            row["updated_at"] = datetime.now(timezone.utc)
            return dict(row)

    def delete_event(self, event_id: str) -> bool:
        with self._lock:
            return self._rows.pop(event_id, None) is not None

    def set_lock(
        self, event_id: str, *, locked: bool, updated_by: str
    ) -> dict[str, Any] | None:
        with self._lock:
            row = self._rows.get(event_id)
            if row is None:
                return None
            row["locked"] = locked
            row["updated_by"] = updated_by
            row["updated_at"] = datetime.now(timezone.utc)
            return dict(row)

    def scraper_upsert(self, event: dict[str, Any]) -> bool:
        with self._lock:
            existing = self._rows.get(event["id"])
            if existing and existing.get("locked"):
                return False
            now = datetime.now(timezone.utc)
            if existing is None:
                self._rows[event["id"]] = {
                    **event,
                    "is_manual": False,
                    "locked": False,
                    "updated_by": None,
                    "created_at": now,
                    "updated_at": now,
                }
            else:
                existing.update(event)
                existing["updated_at"] = now
            return True

    def import_event(
        self,
        event: dict[str, Any],
        *,
        updated_by: str,
        on_conflict: Literal["upsert", "skip"],
    ) -> ImportOutcome:
        # Bulk imports stay unlocked so re-running the same JSON (e.g. a
        # scraper agent re-emitting a corrected file) updates cleanly. Admin
        # can lock individual rows via /events/{id}/lock to protect them.
        with self._lock:
            existing = self._rows.get(event["id"])
            now = datetime.now(timezone.utc)
            if existing is None:
                self._rows[event["id"]] = {
                    **event,
                    "is_manual": True,
                    "locked": False,
                    "updated_by": updated_by,
                    "created_at": now,
                    "updated_at": now,
                }
                return "inserted"
            if existing.get("locked"):
                return "skipped_locked"
            if on_conflict == "skip":
                return "skipped_conflict"
            existing.update(event)
            existing["is_manual"] = True
            existing["updated_by"] = updated_by
            existing["updated_at"] = now
            return "updated"

    def upsert_conference(self, fields: dict[str, Any]) -> dict[str, Any]:
        # InMemory backend doesn't model conferences separately; pretend it worked.
        return dict(fields)


# ---------- supabase backend ----------


class SupabaseEventsAdminRepo:
    def __init__(self, settings: Settings) -> None:
        from supabase import create_client

        self._client = create_client(settings.supabase_url, settings.supabase_service_key)

    def list_events(
        self,
        *,
        conference_id: str | None = None,
        locked: bool | None = None,
        is_manual: bool | None = None,
    ) -> list[dict[str, Any]]:
        q = self._client.table("events").select(_EVENT_COLUMNS)
        if conference_id is not None:
            q = q.eq("conference_id", conference_id)
        if locked is not None:
            q = q.eq("locked", locked)
        if is_manual is not None:
            q = q.eq("is_manual", is_manual)
        return q.order("starts_at").execute().data or []

    def get_event(self, event_id: str) -> dict[str, Any] | None:
        rows = (
            self._client.table("events")
            .select(_EVENT_COLUMNS)
            .eq("id", event_id)
            .limit(1)
            .execute()
            .data
        ) or []
        return rows[0] if rows else None

    def create_event(
        self, *, fields: dict[str, Any], updated_by: str
    ) -> dict[str, Any]:
        payload = {
            **fields,
            "is_manual": True,
            "locked": True,
            "updated_by": updated_by,
            "source": fields.get("source") or "manual",
        }
        res = self._client.table("events").insert(payload).execute()
        return (res.data or [payload])[0]

    def update_event(
        self, event_id: str, *, patch: dict[str, Any], updated_by: str
    ) -> dict[str, Any] | None:
        payload = {**patch, "locked": True, "updated_by": updated_by}
        res = (
            self._client.table("events").update(payload).eq("id", event_id).execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    def delete_event(self, event_id: str) -> bool:
        res = self._client.table("events").delete().eq("id", event_id).execute()
        return bool(res.data)

    def set_lock(
        self, event_id: str, *, locked: bool, updated_by: str
    ) -> dict[str, Any] | None:
        res = (
            self._client.table("events")
            .update({"locked": locked, "updated_by": updated_by})
            .eq("id", event_id)
            .execute()
        )
        rows = res.data or []
        return rows[0] if rows else None

    def scraper_upsert(self, event: dict[str, Any]) -> bool:
        # Two-step to enforce locked=true skip. The scraper isn't in this codepath
        # yet — when it lands it should call this (or a Postgres RPC) and never
        # write to `events` directly.
        existing = self.get_event(event["id"])
        if existing and existing.get("locked"):
            return False
        if existing is None:
            payload = {
                **event,
                "is_manual": False,
                "locked": False,
                "source": event.get("source") or "scraper",
            }
            self._client.table("events").insert(payload).execute()
        else:
            self._client.table("events").update(event).eq("id", event["id"]).eq(
                "locked", False
            ).execute()
        return True

    def import_event(
        self,
        event: dict[str, Any],
        *,
        updated_by: str,
        on_conflict: Literal["upsert", "skip"],
    ) -> ImportOutcome:
        existing = self.get_event(event["id"])
        if existing is None:
            payload = {
                **event,
                "is_manual": True,
                "locked": False,
                "updated_by": updated_by,
                "source": event.get("source") or "bulk_import",
            }
            self._client.table("events").insert(payload).execute()
            return "inserted"
        if existing.get("locked"):
            return "skipped_locked"
        if on_conflict == "skip":
            return "skipped_conflict"
        payload = {**event, "is_manual": True, "updated_by": updated_by}
        self._client.table("events").update(payload).eq("id", event["id"]).execute()
        return "updated"

    def upsert_conference(self, fields: dict[str, Any]) -> dict[str, Any]:
        from datetime import date, timedelta

        days = fields.pop("days", None)
        res = self._client.table("conferences").upsert(fields, on_conflict="id").execute()
        row = (res.data or [fields])[0]

        # Resolve effective date range (request fields take priority over the
        # stored row, in case admin only changed dates).
        start_raw = fields.get("start_date") or row.get("start_date")
        end_raw = fields.get("end_date") or row.get("end_date")

        def _parse(v: Any) -> date | None:
            if isinstance(v, date):
                return v
            if isinstance(v, str) and v:
                try:
                    return date.fromisoformat(v)
                except ValueError:
                    return None
            return None

        sd, ed = _parse(start_raw), _parse(end_raw)

        # Date range present → regenerate conference_days from the range. The
        # picker shows exactly these dates, all enabled by default. Admin can
        # toggle individual days off via the `days[]` overrides; existing
        # toggles are preserved across edits.
        if sd and ed and sd <= ed:
            existing = (
                self._client.table("conference_days")
                .select("day_num,enabled")
                .eq("conference_id", fields["id"])
                .execute()
                .data
                or []
            )
            existing_by_num = {r["day_num"]: r["enabled"] for r in existing}

            admin_overrides: dict[int, bool] = {}
            for d in days or []:
                admin_overrides[d["day_num"]] = bool(d.get("enabled", True))

            generated: list[dict[str, Any]] = []
            valid_nums: set[int] = set()
            current = sd
            while current <= ed:
                day_num = current.day
                valid_nums.add(day_num)
                enabled = admin_overrides.get(day_num)
                if enabled is None:
                    enabled = existing_by_num.get(day_num, True)
                generated.append(
                    {
                        "conference_id": fields["id"],
                        "day_num": day_num,
                        "dow": current.strftime("%a"),
                        "date": current.isoformat(),
                        "enabled": bool(enabled),
                    }
                )
                current += timedelta(days=1)

            if generated:
                self._client.table("conference_days").upsert(
                    generated, on_conflict="conference_id,day_num"
                ).execute()

            # Drop day rows outside the new range so picker stays in sync.
            for r in existing:
                if r["day_num"] not in valid_nums:
                    self._client.table("conference_days").delete().eq(
                        "conference_id", fields["id"]
                    ).eq("day_num", r["day_num"]).execute()

        elif days is not None:
            # Legacy path: no date range — accept admin's explicit days as-is.
            payload = [
                {
                    "conference_id": fields["id"],
                    "day_num": d["day_num"],
                    "dow": d["dow"],
                    "date": d["date"].isoformat() if hasattr(d.get("date"), "isoformat") else d.get("date"),
                    "enabled": bool(d.get("enabled", True)),
                }
                for d in days
            ]
            if payload:
                self._client.table("conference_days").upsert(
                    payload, on_conflict="conference_id,day_num"
                ).execute()
        return row


# ---------- FastAPI dependency ----------


@lru_cache
def _build_repo() -> EventsAdminRepo:
    settings = get_settings()
    if settings.supabase_url and settings.supabase_service_key:
        return SupabaseEventsAdminRepo(settings)
    return InMemoryEventsAdminRepo()


def get_events_admin_repo() -> EventsAdminRepo:
    return _build_repo()
