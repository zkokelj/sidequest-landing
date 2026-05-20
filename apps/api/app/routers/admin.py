from __future__ import annotations

import hashlib
import logging
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status

from app.config import get_settings
from app.deps import CurrentUser, require_admin
from app.models.schemas import (
    AdminConferenceUpsert,
    AdminEventCreate,
    AdminEventOut,
    AdminEventUpdate,
    AdminSuggestionOut,
    AdminSuggestionPatch,
    BulkDeleteEventsResult,
    BulkDeleteSuggestionsResult,
    BulkEventsImportRequest,
    BulkEventsImportResponse,
    BulkImportError,
    ConferenceOut,
    EventPersonAttach,
    EventPersonOut,
    GeneratePeopleResult,
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
from app.services.event_suggestions_repo import (
    EventSuggestionsRepo,
    get_event_suggestions_repo,
)
from app.services.llm import LLMClient, get_llm_client
from app.services.people_generator import generate_people_for_conference
from app.services.scheduler_settings_repo import (
    SchedulerSettingsRepo,
    get_scheduler_settings_repo,
)
from app.services.scrape_sources_repo import (
    ScrapeSourcesRepo,
    get_scrape_sources_repo,
)
from app.services.suggestions_repo import SuggestionsRepo, get_suggestions_repo

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
        f"{title}|{starts_at_iso}".encode(), usedforsecurity=False
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


@router.delete(
    "/conferences/{conference_id}/events",
    response_model=BulkDeleteEventsResult,
)
def delete_all_conference_events(
    conference_id: str,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    repo: Annotated[EventsAdminRepo, Depends(get_events_admin_repo)],
    catalog: Annotated[CatalogRepo, Depends(get_catalog_repo)],
    include_locked: bool = False,
) -> BulkDeleteEventsResult:
    """Delete every event under a conference.

    Locked rows are preserved by default — admin must unlock them first, or
    pass include_locked=true to wipe everything. Returns counts so the UI
    can show 'deleted N, skipped M locked'.
    """
    if catalog.get_conference(conference_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"conference '{conference_id}' not found",
        )
    deleted, skipped_locked = repo.delete_events_for_conference(
        conference_id, include_locked=include_locked
    )
    logger.info(
        "admin.delete_all_conference_events conference=%s include_locked=%s "
        "deleted=%d skipped_locked=%d by=%s",
        conference_id,
        include_locked,
        deleted,
        skipped_locked,
        admin.id,
    )
    return BulkDeleteEventsResult(deleted=deleted, skipped_locked=skipped_locked)


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
    suggestions_repo: Annotated[SuggestionsRepo, Depends(get_suggestions_repo)],
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
                suggestions_repo=suggestions_repo,
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
# LLM-driven people extraction
# ============================================================================


@router.post(
    "/conferences/{conference_id}/generate-people",
    response_model=GeneratePeopleResult,
)
async def generate_people(
    conference_id: str,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    catalog: Annotated[CatalogRepo, Depends(get_catalog_repo)],
    suggestions_repo: Annotated[SuggestionsRepo, Depends(get_suggestions_repo)],
    event_suggestions_repo: Annotated[
        EventSuggestionsRepo, Depends(get_event_suggestions_repo)
    ],
    llm: Annotated[LLMClient, Depends(get_llm_client)],
    model: str | None = None,
) -> GeneratePeopleResult:
    """Run the LLM over this conference's events + known suggestions, persist
    associations and any new people it discovers. Idempotent — re-running
    refreshes confidence on existing (event, person) links rather than
    duplicating."""
    if catalog.get_conference(conference_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"conference '{conference_id}' not found",
        )
    events = catalog.list_events(conference_id)
    known_people = [
        row
        for row in suggestions_repo.list_for_conference(conference_id)
        if row.get("kind") == "people"
    ]
    stats = await generate_people_for_conference(
        conference_id=conference_id,
        events=events,
        known_people=known_people,
        llm=llm,
        suggestions_repo=suggestions_repo,
        event_suggestions_repo=event_suggestions_repo,
        model=model,
    )
    ok = not stats.errors
    if stats.errors:
        message = f"Generation failed: {stats.errors[0]}"
    elif not events:
        message = "No events for this conference — nothing to generate."
    else:
        message = (
            f"Added {stats.associations_added} associations across "
            f"{stats.events_considered} events. "
            f"Created {stats.new_people_created} new people. "
            f"Rejected {stats.rejected_hallucinations} hallucinated rows."
        )
    logger.info(
        "admin.generate_people conference=%s tokens=%d new=%d associations=%d "
        "rejected=%d by=%s",
        conference_id,
        stats.tokens_used,
        stats.new_people_created,
        stats.associations_added,
        stats.rejected_hallucinations,
        admin.id,
    )
    return GeneratePeopleResult(
        ok=ok,
        message=message,
        events_considered=stats.events_considered,
        known_people_considered=stats.known_people_considered,
        new_people_created=stats.new_people_created,
        associations_added=stats.associations_added,
        rejected_hallucinations=stats.rejected_hallucinations,
        tokens_used=stats.tokens_used,
        model=stats.model or None,
        errors=stats.errors,
    )


# ============================================================================
# Event-people: list/attach/detach (manual admin curation)
# ============================================================================


# Sources we allow callers to delete by — keep tight so a typo doesn't
# accidentally wipe rows. 'all' is the magic value meaning "any source".
_DELETABLE_SOURCES = {"llm", "manual", "luma", "seed", "all"}


@router.delete(
    "/conferences/{conference_id}/suggestions",
    response_model=BulkDeleteSuggestionsResult,
)
def bulk_delete_conference_suggestions(
    conference_id: str,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    catalog: Annotated[CatalogRepo, Depends(get_catalog_repo)],
    suggestions_repo: Annotated[SuggestionsRepo, Depends(get_suggestions_repo)],
    source: str = "llm",
) -> BulkDeleteSuggestionsResult:
    """Wipe people for a conference. `source` defaults to 'llm' (the common
    "delete and regenerate" workflow). Pass `source=all` to delete every row
    regardless of source — including manually-curated entries.

    FK cascade on event_suggestions automatically removes any event links
    pointing at deleted suggestion rows. There is no soft-delete or undo.
    """
    if catalog.get_conference(conference_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"conference '{conference_id}' not found",
        )
    if source not in _DELETABLE_SOURCES:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"source must be one of {sorted(_DELETABLE_SOURCES)}",
        )
    filter_source = None if source == "all" else source
    deleted = suggestions_repo.delete_for_conference(
        conference_id, source=filter_source
    )
    logger.info(
        "admin.bulk_delete_suggestions conference=%s source=%s deleted=%d by=%s",
        conference_id,
        source,
        deleted,
        admin.id,
    )
    return BulkDeleteSuggestionsResult(deleted=deleted)


@router.patch(
    "/suggestions/{suggestion_id}",
    response_model=AdminSuggestionOut,
)
def patch_suggestion(
    suggestion_id: str,
    body: AdminSuggestionPatch,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    suggestions_repo: Annotated[SuggestionsRepo, Depends(get_suggestions_repo)],
) -> AdminSuggestionOut:
    """Edit a person's name/role. The id is immutable — even if you rename
    `llm:stani-kulechov` to "Stan Kulechov", the row id stays the same so
    existing event_suggestions links survive."""
    # Validate name when provided — empty/whitespace is invalid.
    if body.name is not None and not body.name.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="name cannot be empty",
        )
    row = suggestions_repo.update_fields(
        suggestion_id,
        name=body.name,
        role=body.role,
    )
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"suggestion '{suggestion_id}' not found",
        )
    logger.info(
        "admin.patch_suggestion id=%s by=%s",
        suggestion_id,
        admin.id,
    )
    return AdminSuggestionOut.model_validate(row)


@router.get(
    "/conferences/{conference_id}/suggestions",
    response_model=list[AdminSuggestionOut],
)
def list_conference_suggestions(
    conference_id: str,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    suggestions_repo: Annotated[SuggestionsRepo, Depends(get_suggestions_repo)],
    catalog: Annotated[CatalogRepo, Depends(get_catalog_repo)],
    kind: str | None = None,
) -> list[AdminSuggestionOut]:
    """All conference_suggestions for a conference. Filter by `kind=people`
    when populating the event-people picker."""
    if catalog.get_conference(conference_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"conference '{conference_id}' not found",
        )
    rows = suggestions_repo.list_for_conference(conference_id)
    if kind is not None:
        rows = [r for r in rows if r.get("kind") == kind]
    return [AdminSuggestionOut.model_validate(r) for r in rows]


@router.get(
    "/events/{event_id}/people",
    response_model=list[EventPersonOut],
)
def list_event_people(
    event_id: str,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    events_repo: Annotated[EventsAdminRepo, Depends(get_events_admin_repo)],
    suggestions_repo: Annotated[SuggestionsRepo, Depends(get_suggestions_repo)],
    event_suggestions_repo: Annotated[
        EventSuggestionsRepo, Depends(get_event_suggestions_repo)
    ],
) -> list[EventPersonOut]:
    if events_repo.get_event(event_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"event '{event_id}' not found",
        )
    links = event_suggestions_repo.list_for_event(event_id)
    out: list[EventPersonOut] = []
    for link in links:
        person = suggestions_repo.get_by_id(link["suggestion_id"])
        if person is None:
            # Suggestion was deleted underneath us. Skip — the link will be
            # GC'd by the FK cascade on the next conference_suggestions delete.
            continue
        out.append(
            EventPersonOut(
                suggestion_id=person["id"],
                name=person["name"],
                role=person.get("role"),
                person_source=person.get("source"),
                link_source=link["source"],
                confidence=link.get("confidence"),
            )
        )
    return out


@router.post(
    "/events/{event_id}/people",
    response_model=EventPersonOut,
    status_code=status.HTTP_201_CREATED,
)
def attach_event_person(
    event_id: str,
    body: EventPersonAttach,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    events_repo: Annotated[EventsAdminRepo, Depends(get_events_admin_repo)],
    suggestions_repo: Annotated[SuggestionsRepo, Depends(get_suggestions_repo)],
    event_suggestions_repo: Annotated[
        EventSuggestionsRepo, Depends(get_event_suggestions_repo)
    ],
) -> EventPersonOut:
    """Attach a person to an event. Either provide `suggestion_id` (an
    existing person already curated for the conference) or `name` (+ optional
    `role`) to create a new manual person and attach in one call."""
    event = events_repo.get_event(event_id)
    if event is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"event '{event_id}' not found",
        )
    has_id = bool(body.suggestion_id and body.suggestion_id.strip())
    has_name = bool(body.name and body.name.strip())
    if has_id == has_name:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="provide exactly one of suggestion_id or name",
        )

    if has_id:
        suggestion_id = body.suggestion_id.strip()  # type: ignore[union-attr]
        person = suggestions_repo.get_by_id(suggestion_id)
        if person is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"suggestion '{suggestion_id}' not found",
            )
        if person["conference_id"] != event["conference_id"]:
            # Don't allow linking a person to an event in a different
            # conference — the picker should never offer that, but defend
            # against direct API calls anyway.
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="suggestion belongs to a different conference",
            )
    else:
        new_id = suggestions_repo.upsert_manual_person(
            conference_id=event["conference_id"],
            name=body.name or "",  # has_name guard ensures truthy
            role=body.role,
        )
        if not new_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="name produced an empty slug — provide a valid name",
            )
        suggestion_id = new_id
        person = suggestions_repo.get_by_id(new_id)
        if person is None:
            # Extremely unlikely — upsert just succeeded.
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="newly created person could not be loaded",
            )

    written = event_suggestions_repo.upsert_links(
        [
            {
                "event_id": event_id,
                "suggestion_id": suggestion_id,
                "source": "manual",
                "confidence": None,
            }
        ]
    )
    if not written:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="failed to write event link",
        )
    logger.info(
        "admin.attach_event_person event=%s suggestion=%s by=%s",
        event_id,
        suggestion_id,
        admin.id,
    )
    return EventPersonOut(
        suggestion_id=suggestion_id,
        name=person["name"],
        role=person.get("role"),
        person_source=person.get("source"),
        link_source="manual",
        confidence=None,
    )


@router.delete(
    "/events/{event_id}/people/{suggestion_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
def detach_event_person(
    event_id: str,
    suggestion_id: str,
    admin: Annotated[CurrentUser, Depends(require_admin)],
    event_suggestions_repo: Annotated[
        EventSuggestionsRepo, Depends(get_event_suggestions_repo)
    ],
) -> None:
    """Detach a person from an event. Idempotent — returns 204 whether or not
    the link existed (404 felt noisy for an "undo this checkbox" interaction).
    Note: the underlying person row in conference_suggestions is preserved;
    only the (event, person) edge is removed."""
    event_suggestions_repo.delete_link(event_id, suggestion_id)
    logger.info(
        "admin.detach_event_person event=%s suggestion=%s by=%s",
        event_id,
        suggestion_id,
        admin.id,
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
