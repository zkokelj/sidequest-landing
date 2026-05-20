from __future__ import annotations

import hashlib
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.deps import CurrentUser, require_admin
from app.config import get_settings
from app.models.schemas import (
    AdminConferenceUpsert,
    AdminEventCreate,
    AdminEventOut,
    AdminEventUpdate,
    BulkEventsImportRequest,
    BulkEventsImportResponse,
    BulkImportError,
    ConferenceOut,
    LockRequest,
    SchedulerSettingsOut,
    SchedulerSettingsUpdate,
    ScrapeRunResult,
    ScrapeSourceCreate,
    ScrapeSourceOut,
    ScrapeSourceUpdate,
)
from app.scraper.luma_runner import SourceScrapeStats, run_for_source
from app.services.admin_repo import EventsAdminRepo, get_events_admin_repo
from app.services.catalog import CatalogRepo, get_catalog_repo
from app.services.scheduler_settings_repo import (
    SchedulerSettingsRepo,
    get_scheduler_settings_repo,
)
from app.services.scrape_sources_repo import (
    ScrapeSourcesRepo,
    get_scrape_sources_repo,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/conferences", response_model=list[ConferenceOut])
def list_all_conferences(
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[CatalogRepo, Depends(get_catalog_repo)],
) -> list[ConferenceOut]:
    """List ALL conferences (active + inactive). Public /api/conferences stays active-only."""
    return repo.list_conferences(include_inactive=True)


def _to_out(row: dict) -> AdminEventOut:
    return AdminEventOut.model_validate(row)


@router.get("/events", response_model=list[AdminEventOut])
def list_events(
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[EventsAdminRepo, Depends(get_events_admin_repo)],
    conference_id: str | None = None,
    locked: bool | None = None,
    is_manual: bool | None = None,
) -> list[AdminEventOut]:
    rows = repo.list_events(
        conference_id=conference_id, locked=locked, is_manual=is_manual
    )
    return [_to_out(r) for r in rows]


@router.post("/events", response_model=AdminEventOut, status_code=status.HTTP_201_CREATED)
def create_event(
    body: AdminEventCreate,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[EventsAdminRepo, Depends(get_events_admin_repo)],
) -> AdminEventOut:
    fields = body.model_dump()
    # Datetimes need to be serializable for supabase-py — convert to ISO strings.
    fields["starts_at"] = fields["starts_at"].isoformat()
    fields["ends_at"] = fields["ends_at"].isoformat()
    if repo.get_event(body.id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"event '{body.id}' already exists",
        )
    row = repo.create_event(fields=fields, updated_by=admin.id)
    return _to_out(row)


@router.patch("/events/{event_id}", response_model=AdminEventOut)
def update_event(
    event_id: str,
    body: AdminEventUpdate,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[EventsAdminRepo, Depends(get_events_admin_repo)],
) -> AdminEventOut:
    patch = {k: v for k, v in body.model_dump(exclude_unset=True).items() if v is not None}
    # Serialize datetimes if present
    for k in ("starts_at", "ends_at"):
        if k in patch and hasattr(patch[k], "isoformat"):
            patch[k] = patch[k].isoformat()
    if not patch:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="empty patch",
        )
    row = repo.update_event(event_id, patch=patch, updated_by=admin.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"event '{event_id}' not found",
        )
    return _to_out(row)


@router.delete("/events/{event_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_event(
    event_id: str,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[EventsAdminRepo, Depends(get_events_admin_repo)],
) -> None:
    if not repo.delete_event(event_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"event '{event_id}' not found",
        )


def _derive_event_id(conference_id: str, title: str, starts_at_iso: str) -> str:
    """Stable hash so re-importing the same JSON updates rather than duplicates."""
    digest = hashlib.sha1(
        f"{title}|{starts_at_iso}".encode("utf-8"), usedforsecurity=False
    ).hexdigest()[:16]
    return f"import:{conference_id}:{digest}"


@router.post("/events/bulk", response_model=BulkEventsImportResponse)
def bulk_import_events(
    body: BulkEventsImportRequest,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[EventsAdminRepo, Depends(get_events_admin_repo)],
    catalog: Annotated[CatalogRepo, Depends(get_catalog_repo)],
    dry_run: bool = False,
) -> BulkEventsImportResponse:
    """Bulk-import events for one conference from a JSON payload.

    Per-row validation: an error on one row never blocks the others.
    Locked existing rows are always skipped (admin must unlock first).
    Stable IDs let agents emit deterministic JSON and re-run safely.
    """
    if catalog.get_conference(body.conference_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"conference '{body.conference_id}' not found",
        )

    inserted = updated = skipped_locked = skipped_conflict = 0
    errors: list[BulkImportError] = []
    seen_ids: set[str] = set()

    for i, ev in enumerate(body.events):
        try:
            if ev.ends_at <= ev.starts_at:
                raise ValueError("ends_at must be after starts_at")

            starts_iso = ev.starts_at.isoformat()
            ends_iso = ev.ends_at.isoformat()
            event_id = ev.id or _derive_event_id(body.conference_id, ev.title, starts_iso)

            if event_id in seen_ids:
                raise ValueError(f"duplicate id within payload: '{event_id}'")
            seen_ids.add(event_id)

            fields = {
                "id": event_id,
                "conference_id": body.conference_id,
                "title": ev.title,
                "description": ev.description,
                "starts_at": starts_iso,
                "ends_at": ends_iso,
                "venue": ev.venue,
                "tags": ev.tags,
                "url": ev.url,
                "capacity": ev.capacity,
                "attendees": ev.attendees,
            }

            if dry_run:
                existing = repo.get_event(event_id)
                if existing is None:
                    outcome = "inserted"
                elif existing.get("locked"):
                    outcome = "skipped_locked"
                elif body.on_conflict == "skip":
                    outcome = "skipped_conflict"
                else:
                    outcome = "updated"
            else:
                outcome = repo.import_event(
                    fields, updated_by=admin.id, on_conflict=body.on_conflict
                )

            if outcome == "inserted":
                inserted += 1
            elif outcome == "updated":
                updated += 1
            elif outcome == "skipped_locked":
                skipped_locked += 1
            elif outcome == "skipped_conflict":
                skipped_conflict += 1
        except Exception as exc:
            errors.append(
                BulkImportError(index=i, id=ev.id, message=str(exc)[:300])
            )

    logger.info(
        "admin.bulk_import conference=%s dry_run=%s inserted=%d updated=%d "
        "skipped_locked=%d skipped_conflict=%d errors=%d",
        body.conference_id,
        dry_run,
        inserted,
        updated,
        skipped_locked,
        skipped_conflict,
        len(errors),
    )

    return BulkEventsImportResponse(
        dry_run=dry_run,
        inserted=inserted,
        updated=updated,
        skipped_locked=skipped_locked,
        skipped_conflict=skipped_conflict,
        errors=errors,
    )


@router.post("/events/{event_id}/lock", response_model=AdminEventOut)
def set_event_lock(
    event_id: str,
    body: LockRequest,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[EventsAdminRepo, Depends(get_events_admin_repo)],
) -> AdminEventOut:
    row = repo.set_lock(event_id, locked=body.locked, updated_by=admin.id)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"event '{event_id}' not found",
        )
    return _to_out(row)


@router.post("/conferences", status_code=status.HTTP_200_OK)
def upsert_conference(
    body: AdminConferenceUpsert,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[EventsAdminRepo, Depends(get_events_admin_repo)],
) -> dict:
    fields = body.model_dump()
    for k in ("start_date", "end_date"):
        if fields.get(k) is not None:
            fields[k] = fields[k].isoformat()
    if fields.get("days") is None:
        fields.pop("days", None)
    return repo.upsert_conference(fields)


# ============================================================================
# Scrape sources
# ============================================================================


def _source_out(row: dict) -> ScrapeSourceOut:
    return ScrapeSourceOut.model_validate(row)


@router.get(
    "/conferences/{conference_id}/sources",
    response_model=list[ScrapeSourceOut],
)
def list_sources(
    conference_id: str,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[ScrapeSourcesRepo, Depends(get_scrape_sources_repo)],
) -> list[ScrapeSourceOut]:
    return [_source_out(r) for r in repo.list_for_conference(conference_id)]


@router.post(
    "/conferences/{conference_id}/sources",
    response_model=ScrapeSourceOut,
    status_code=status.HTTP_201_CREATED,
)
def add_source(
    conference_id: str,
    body: ScrapeSourceCreate,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[ScrapeSourcesRepo, Depends(get_scrape_sources_repo)],
) -> ScrapeSourceOut:
    url = body.url.strip()
    if not url:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="url is required",
        )
    row = repo.create(
        conference_id=conference_id,
        url=url,
        source_type=body.source_type,
        enabled=body.enabled,
        scrape_interval_minutes=body.scrape_interval_minutes,
    )
    return _source_out(row)


@router.patch("/sources/{source_id}", response_model=ScrapeSourceOut)
def update_source(
    source_id: str,
    body: ScrapeSourceUpdate,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[ScrapeSourcesRepo, Depends(get_scrape_sources_repo)],
) -> ScrapeSourceOut:
    # Use model_fields_set so we can distinguish "omitted" from "explicit null"
    # (needed to clear scrape_interval_minutes back to NULL → manual-only).
    sent = body.model_fields_set
    kwargs: dict[str, Any] = {}
    if "url" in sent and body.url is not None:
        kwargs["url"] = body.url.strip()
    if "enabled" in sent and body.enabled is not None:
        kwargs["enabled"] = body.enabled
    if "scrape_interval_minutes" in sent:
        kwargs["scrape_interval_minutes"] = body.scrape_interval_minutes
    row = repo.update(source_id, **kwargs)
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"source '{source_id}' not found",
        )
    return _source_out(row)


@router.delete("/sources/{source_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_source(
    source_id: str,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[ScrapeSourcesRepo, Depends(get_scrape_sources_repo)],
) -> None:
    if not repo.delete(source_id):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"source '{source_id}' not found",
        )


@router.post(
    "/conferences/{conference_id}/scrape",
    response_model=ScrapeRunResult,
)
def trigger_scrape(
    conference_id: str,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    sources_repo: Annotated[ScrapeSourcesRepo, Depends(get_scrape_sources_repo)],
    events_repo: Annotated[EventsAdminRepo, Depends(get_events_admin_repo)],
) -> ScrapeRunResult:
    """Run every enabled Luma source on this conference and upsert events.

    Per-source failures (network error, bad calendar URL, etc.) are caught
    and recorded against the source's last_scrape_status; one bad source
    doesn't fail the whole run. Per-event failures inside a source are
    counted but not surfaced individually — see server logs.
    """
    sources = [s for s in sources_repo.list_for_conference(conference_id) if s["enabled"]]
    if not sources:
        return ScrapeRunResult(
            ok=True,
            message="No enabled scrape sources for this conference.",
            sources_attempted=0,
            sources_failed=0,
            events_added=0,
            events_updated=0,
        )

    total = SourceScrapeStats()
    failures: list[str] = []

    for source in sources:
        url = source["url"]
        source_id = source["id"]
        try:
            stats = run_for_source(
                conference_id=conference_id,
                source_url=url,
                events_repo=events_repo,
            )
        except Exception as exc:
            logger.exception("admin.trigger_scrape source=%s failed", url)
            failures.append(f"{url}: {exc}")
            sources_repo.record_scrape(
                source_id,
                status="error",
                error=str(exc)[:500],
            )
            continue

        sources_repo.record_scrape(
            source_id,
            status="ok",
            events_added=stats.events_added,
            events_updated=stats.events_updated,
        )
        total.merge(stats)

    failed = len(failures)
    if failed == 0:
        message = (
            f"Scraped {len(sources)} source(s): "
            f"added {total.events_added}, updated {total.events_updated}, "
            f"skipped (locked) {total.events_skipped_locked}, "
            f"failed events {total.events_failed}."
        )
    else:
        message = (
            f"Scraped {len(sources)} source(s); {failed} failed. "
            f"Added {total.events_added}, updated {total.events_updated}. "
            f"First failure: {failures[0]}"
        )

    return ScrapeRunResult(
        ok=failed == 0,
        message=message,
        sources_attempted=len(sources),
        sources_failed=failed,
        events_added=total.events_added,
        events_updated=total.events_updated,
        events_failed=total.events_failed,
        failed_events=[
            {
                "api_id": fe.api_id,
                "reason": fe.reason,
                "detail": fe.detail,
                "url": fe.url,
                "title": fe.title,
            }
            for fe in total.failed_events
        ],
    )


# ============================================================================
# Scheduler on/off
# ============================================================================


@router.get("/scheduler", response_model=SchedulerSettingsOut)
def get_scheduler(
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[SchedulerSettingsRepo, Depends(get_scheduler_settings_repo)],
) -> SchedulerSettingsOut:
    return SchedulerSettingsOut(
        enabled=repo.get_enabled(),
        tick_seconds=get_settings().scraper_scheduler_tick_seconds,
    )


@router.put("/scheduler", response_model=SchedulerSettingsOut)
def update_scheduler(
    body: SchedulerSettingsUpdate,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[SchedulerSettingsRepo, Depends(get_scheduler_settings_repo)],
) -> SchedulerSettingsOut:
    new_enabled = repo.set_enabled(body.enabled, updated_by=admin.id)
    logger.info(
        "scheduler.toggled enabled=%s by=%s", new_enabled, admin.id
    )
    return SchedulerSettingsOut(
        enabled=new_enabled,
        tick_seconds=get_settings().scraper_scheduler_tick_seconds,
    )
