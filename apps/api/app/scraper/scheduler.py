"""Background scheduler — drives auto-scrape per source.

A single asyncio task ticks on a fixed interval. The first thing each tick
does is consult `scheduler_settings.enabled` (singleton row, flipped from
the admin UI). When disabled, the tick is a cheap DB read and a return.
When enabled, the tick asks the sources repo for "due" rows (interval
elapsed since last_scraped_at) and runs the Luma runner against each one.

On/off control:
    The DB flag is authoritative — no env var gates whether the task runs.
    Boot the API anywhere; admins flip the toggle in the UI. Latency
    between flipping and effect is at most `tick_seconds` (default 60s).

Single-process model:
    We rely on an in-process `asyncio.Lock` to prevent concurrent ticks
    overlapping inside one worker. We do NOT use a database advisory lock,
    so running with >1 uvicorn worker (or two API processes pointed at the
    same DB) can cause each due source to be scraped N times per tick.
    Upserts are idempotent (deterministic IDs), so the only consequence is
    duplicate Luma API calls — but if/when this service scales horizontally,
    swap the in-process lock for a Postgres advisory lock or a lease row.

Failure isolation: a per-source exception is logged but does NOT kill the
tick loop. Per-event failures are already absorbed by `run_for_source`.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from app.scraper.luma_runner import run_for_source
from app.services.admin_repo import EventsAdminRepo
from app.services.scheduler_settings_repo import SchedulerSettingsRepo
from app.services.scrape_sources_repo import ScrapeSourcesRepo
from app.services.suggestions_repo import SuggestionsRepo

logger = logging.getLogger(__name__)


class Scheduler:
    def __init__(
        self,
        *,
        sources_repo: ScrapeSourcesRepo,
        events_repo: EventsAdminRepo,
        settings_repo: SchedulerSettingsRepo,
        suggestions_repo: SuggestionsRepo | None = None,
        tick_seconds: int = 60,
    ) -> None:
        self._sources_repo = sources_repo
        self._events_repo = events_repo
        self._settings_repo = settings_repo
        self._suggestions_repo = suggestions_repo
        self._tick_seconds = tick_seconds
        self._task: asyncio.Task[None] | None = None
        self._tick_lock = asyncio.Lock()
        self._stop_event = asyncio.Event()

    async def start(self) -> None:
        if self._task is not None:
            return
        self._stop_event.clear()
        self._task = asyncio.create_task(self._run(), name="scraper-scheduler")
        logger.info("scheduler.start tick_seconds=%d", self._tick_seconds)

    async def stop(self) -> None:
        if self._task is None:
            return
        self._stop_event.set()
        try:
            await asyncio.wait_for(self._task, timeout=10.0)
        except asyncio.TimeoutError:
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        finally:
            self._task = None
            logger.info("scheduler.stop")

    async def _run(self) -> None:
        # Stagger the first tick slightly so multiple workers (if ever) don't
        # all hit the DB at the same second.
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=2.0)
            return  # stop requested before the first tick
        except asyncio.TimeoutError:
            pass

        while not self._stop_event.is_set():
            try:
                await self.tick_once()
            except Exception:
                logger.exception("scheduler.tick_failed")
            try:
                await asyncio.wait_for(
                    self._stop_event.wait(), timeout=self._tick_seconds
                )
                return  # stop requested
            except asyncio.TimeoutError:
                continue  # next tick

    async def tick_once(self) -> dict[str, int]:
        """Process all currently-due sources. Public so tests can drive it
        without owning the timer. Returns a small stats dict."""
        if self._tick_lock.locked():
            logger.info("scheduler.tick_skipped reason=already_running")
            return {"due": 0, "ran": 0, "failed_sources": 0}

        async with self._tick_lock:
            try:
                enabled = await asyncio.to_thread(self._settings_repo.get_enabled)
            except Exception:
                logger.exception("scheduler.settings_read_failed")
                return {"due": 0, "ran": 0, "failed_sources": 0}

            if not enabled:
                logger.debug("scheduler.tick_skipped reason=disabled")
                return {"due": 0, "ran": 0, "failed_sources": 0}

            due_rows = await asyncio.to_thread(self._sources_repo.list_due)
            ran = 0
            failed = 0

            for source in due_rows:
                try:
                    stats = await asyncio.to_thread(
                        run_for_source,
                        conference_id=source["conference_id"],
                        source_url=source["url"],
                        events_repo=self._events_repo,
                        suggestions_repo=self._suggestions_repo,
                    )
                    await asyncio.to_thread(
                        self._sources_repo.record_scrape,
                        source["id"],
                        status="ok",
                        events_added=stats.events_added,
                        events_updated=stats.events_updated,
                    )
                    ran += 1
                    logger.info(
                        "scheduler.source_ok id=%s url=%s added=%d updated=%d failed=%d",
                        source["id"],
                        source["url"],
                        stats.events_added,
                        stats.events_updated,
                        stats.events_failed,
                    )
                except Exception as exc:
                    failed += 1
                    logger.exception(
                        "scheduler.source_failed id=%s url=%s",
                        source["id"],
                        source["url"],
                    )
                    try:
                        await asyncio.to_thread(
                            self._sources_repo.record_scrape,
                            source["id"],
                            status="error",
                            error=str(exc)[:500],
                        )
                    except Exception:
                        logger.exception(
                            "scheduler.record_error_failed id=%s", source["id"]
                        )

            logger.info(
                "scheduler.tick_done due=%d ran=%d failed_sources=%d",
                len(due_rows),
                ran,
                failed,
            )
            return {"due": len(due_rows), "ran": ran, "failed_sources": failed}


def build_scheduler(
    settings: Any,
    *,
    sources_repo: ScrapeSourcesRepo,
    events_repo: EventsAdminRepo,
    settings_repo: SchedulerSettingsRepo,
    suggestions_repo: SuggestionsRepo | None = None,
) -> Scheduler:
    return Scheduler(
        sources_repo=sources_repo,
        events_repo=events_repo,
        settings_repo=settings_repo,
        suggestions_repo=suggestions_repo,
        tick_seconds=settings.scraper_scheduler_tick_seconds,
    )
