"""Drives a single Luma `scrape_sources` entry end-to-end.

The admin trigger endpoint iterates enabled sources and calls
:func:`run_for_source` once per source. Per-event errors are absorbed so
one bad event doesn't kill the whole run; the scraper itself raising
(network failure, bad slug, calendar 404) propagates and is recorded as
the source's last_scrape_status = "error" by the caller.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from app.scraper.sources.luma import LUMA_WEB_BASE, LumaScraper, normalize_event
from app.services.admin_repo import EventsAdminRepo
from app.services.suggestions_repo import SuggestionsRepo

logger = logging.getLogger(__name__)


@dataclass
class FailedEvent:
    """A Luma entry that didn't make it into the DB. `api_id` may be None
    if the entry was malformed enough to lack one. `url` + `title` are
    best-effort — useful for admins to inspect/recreate the event manually."""
    api_id: str | None
    reason: str  # 'missing_required' | 'exception'
    detail: str | None = None
    url: str | None = None
    title: str | None = None


def _entry_url(event: dict[str, Any]) -> str | None:
    """Best-effort URL for the admin failure list.

    Native Luma events store a bare slug in `event.url`; external (bookmarked)
    events store a full http(s) URL. We need to keep the two apart — prefixing
    `https://lu.ma/` onto an already-absolute URL gave us the wrong-looking
    `https://lu.ma/https://www.rsv.pizza/...` links the admin UI showed.
    """
    raw = event.get("url")
    if not raw:
        return None
    if "://" in raw:
        return raw
    return f"{LUMA_WEB_BASE}/{raw}"


def _capture_people(
    details: dict[str, Any],
    *,
    conference_id: str,
    source_event_id: str,
    suggestions_repo: SuggestionsRepo,
) -> int:
    """Lift Luma hosts + featured_guests into conference_suggestions.

    Returns the number of new rows actually inserted (repeat names from
    other events upsert no-ops, so the count reflects net-new people).
    """
    added = 0
    for host in details.get("hosts") or []:
        name = (host.get("name") or "").strip()
        if not name:
            continue
        role = (host.get("bio_short") or "").strip() or None
        if suggestions_repo.upsert_luma_person(
            conference_id=conference_id,
            name=name,
            role=role,
            source_event_id=source_event_id,
        ):
            added += 1
    for guest in details.get("featured_guests") or []:
        name = (guest.get("name") or "").strip()
        if not name:
            continue
        role = (guest.get("bio_short") or "").strip() or None
        if suggestions_repo.upsert_luma_person(
            conference_id=conference_id,
            name=name,
            role=role,
            source_event_id=source_event_id,
        ):
            added += 1
    return added


@dataclass
class SourceScrapeStats:
    events_added: int = 0
    events_updated: int = 0
    events_skipped_locked: int = 0
    events_failed: int = 0
    suggestions_added: int = 0
    failed_events: list[FailedEvent] = field(default_factory=list)

    def merge(self, other: SourceScrapeStats) -> None:
        self.events_added += other.events_added
        self.events_updated += other.events_updated
        self.events_skipped_locked += other.events_skipped_locked
        self.events_failed += other.events_failed
        self.suggestions_added += other.suggestions_added
        self.failed_events.extend(other.failed_events)


def run_for_source(
    *,
    conference_id: str,
    source_url: str,
    events_repo: EventsAdminRepo,
    suggestions_repo: SuggestionsRepo | None = None,
    scraper: LumaScraper | None = None,
    fetch_details: bool = True,
    max_pages: int | None = None,
) -> SourceScrapeStats:
    """Scrape one Luma source and upsert events into `events_repo`.

    Args:
        conference_id: SideQuest conference to attach events to.
        source_url: Full Luma URL (or bare slug). Stored as `events.source`.
        events_repo: Repo to upsert into. Tests inject the in-memory backend.
        suggestions_repo: Optional. When provided, hosts + featured_guests
            from each event's /event/get response are upserted into
            conference_suggestions with source='luma'. Requires
            fetch_details=True to have anything to lift.
        scraper: Optional pre-built scraper (used by tests with MockTransport).
            When None, a fresh LumaScraper is built and disposed.
        fetch_details: When True (default), also call `/event/get` for each
            entry to populate description/capacity/attendees. Costs N extra
            HTTP requests but the calendar listing alone doesn't carry rich
            data — callers can disable for cheap "what's new?" sweeps.
        max_pages: Cap on calendar pagination (None = walk to end).

    Returns:
        Per-source stats. Caller is responsible for recording these via
        `ScrapeSourcesRepo.record_scrape`.

    Raises:
        Anything raised by the scraper itself (httpx.HTTPError, ValueError
        from missing calendar id, etc.). Per-event failures are caught
        internally and counted in `events_failed`.
    """
    owns_scraper = scraper is None
    s = scraper or LumaScraper()
    try:
        entries = s.scrape_calendar(source_url, max_pages=max_pages)
        details_map = s.fetch_all_details(entries) if fetch_details else {}
    finally:
        if owns_scraper:
            s.close()

    stats = SourceScrapeStats()
    for entry in entries:
        event = entry.get("event") or {}
        event_api_id = event.get("api_id")
        # Fall back to the envelope `calev-…` id for external (non-Luma) entries.
        api_id = event_api_id or entry.get("api_id")
        url = _entry_url(event)
        title = event.get("name")
        try:
            details = details_map.get(event_api_id) if event_api_id else None
            row = normalize_event(
                entry,
                conference_id=conference_id,
                source=source_url,
                details=details,
            )
            if row is None:
                stats.events_failed += 1
                missing = [k for k in ("name", "start_at") if not event.get(k)]
                if not api_id:
                    missing.insert(0, "api_id")
                stats.failed_events.append(
                    FailedEvent(
                        api_id=api_id,
                        reason="missing_required",
                        detail=f"missing fields: {', '.join(missing)}" if missing else None,
                        url=url,
                        title=title,
                    )
                )
                continue

            existed = events_repo.get_event(row["id"]) is not None
            upserted = events_repo.scraper_upsert(row)
            if not upserted:
                # locked=true — scraper contract requires skip
                stats.events_skipped_locked += 1
            elif existed:
                stats.events_updated += 1
            else:
                stats.events_added += 1

            # Suggestions are best-effort — a failure here must not break
            # the event upsert that already succeeded above.
            if suggestions_repo is not None and details:
                try:
                    stats.suggestions_added += _capture_people(
                        details,
                        conference_id=conference_id,
                        source_event_id=row["id"],
                        suggestions_repo=suggestions_repo,
                    )
                except Exception:
                    logger.exception(
                        "luma_runner.suggestions_failed event_id=%s", row["id"]
                    )
        except Exception as exc:
            logger.exception("luma_runner.event_failed source_url=%s", source_url)
            stats.events_failed += 1
            stats.failed_events.append(
                FailedEvent(
                    api_id=api_id,
                    reason="exception",
                    detail=str(exc)[:200],
                    url=url,
                    title=title,
                )
            )

    logger.info(
        "luma_runner.done source_url=%s added=%d updated=%d skipped_locked=%d "
        "failed=%d suggestions_added=%d",
        source_url,
        stats.events_added,
        stats.events_updated,
        stats.events_skipped_locked,
        stats.events_failed,
        stats.suggestions_added,
    )
    return stats
