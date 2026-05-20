"""Slice C — in-process scheduler loop.

Exercises tick_once() directly with monkey-patched run_for_source so we
don't depend on a real Luma scraper. Covers:
  - Only due rows are processed.
  - Successful run records ok + counts.
  - Failing run records error + last_error.
  - Re-entrant tick is skipped (asyncio.Lock guard).
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest

from app.scraper.luma_runner import SourceScrapeStats
from app.scraper.scheduler import Scheduler
from app.services.admin_repo import InMemoryEventsAdminRepo
from app.services.scheduler_settings_repo import InMemorySchedulerSettingsRepo
from app.services.scrape_sources_repo import InMemoryScrapeSourcesRepo


def _backdate(repo: InMemoryScrapeSourcesRepo, sid: str, *, when: datetime) -> None:
    repo._rows[sid]["last_scraped_at"] = when  # type: ignore[index]


def _enabled_settings() -> InMemorySchedulerSettingsRepo:
    repo = InMemorySchedulerSettingsRepo()
    repo.set_enabled(True)
    return repo


def _disabled_settings() -> InMemorySchedulerSettingsRepo:
    return InMemorySchedulerSettingsRepo()


@pytest.mark.asyncio
async def test_tick_processes_only_due_sources(monkeypatch) -> None:
    sources = InMemoryScrapeSourcesRepo()
    events = InMemoryEventsAdminRepo()
    now = datetime.now(timezone.utc)

    due = sources.create(
        conference_id="c1",
        url="https://lu.ma/due",
        scrape_interval_minutes=15,
    )  # never scraped → due

    fresh = sources.create(
        conference_id="c1",
        url="https://lu.ma/fresh",
        scrape_interval_minutes=60,
    )
    _backdate(sources, fresh["id"], when=now - timedelta(minutes=10))  # not yet due

    manual = sources.create(
        conference_id="c1",
        url="https://lu.ma/manual",
        scrape_interval_minutes=None,
    )

    calls: list[str] = []

    def fake_run(*, conference_id, source_url, events_repo, suggestions_repo=None):
        calls.append(source_url)
        return SourceScrapeStats(events_added=2, events_updated=0)

    monkeypatch.setattr("app.scraper.scheduler.run_for_source", fake_run)

    sched = Scheduler(
        sources_repo=sources,
        events_repo=events,
        settings_repo=_enabled_settings(),
        tick_seconds=60,
    )
    result = await sched.tick_once()

    assert result == {"due": 1, "ran": 1, "failed_sources": 0}
    assert calls == ["https://lu.ma/due"]

    refreshed = sources.get(due["id"])
    assert refreshed is not None
    assert refreshed["last_status"] == "ok"
    assert refreshed["events_added"] == 2

    # Fresh + manual untouched
    assert sources.get(fresh["id"])["last_status"] is None  # type: ignore[index]
    assert sources.get(manual["id"])["last_status"] is None  # type: ignore[index]


@pytest.mark.asyncio
async def test_tick_records_error_when_runner_raises(monkeypatch) -> None:
    sources = InMemoryScrapeSourcesRepo()
    events = InMemoryEventsAdminRepo()
    row = sources.create(
        conference_id="c1",
        url="https://lu.ma/x",
        scrape_interval_minutes=15,
    )

    def fake_run(*, conference_id, source_url, events_repo, suggestions_repo=None):
        raise RuntimeError("luma is down")

    monkeypatch.setattr("app.scraper.scheduler.run_for_source", fake_run)

    sched = Scheduler(
        sources_repo=sources,
        events_repo=events,
        settings_repo=_enabled_settings(),
        tick_seconds=60,
    )
    result = await sched.tick_once()

    assert result["failed_sources"] == 1
    refreshed = sources.get(row["id"])
    assert refreshed is not None
    assert refreshed["last_status"] == "error"
    assert refreshed["last_error"] == "luma is down"


@pytest.mark.asyncio
async def test_one_bad_source_doesnt_block_the_others(monkeypatch) -> None:
    sources = InMemoryScrapeSourcesRepo()
    events = InMemoryEventsAdminRepo()
    good = sources.create(
        conference_id="c1", url="https://lu.ma/good", scrape_interval_minutes=15
    )
    bad = sources.create(
        conference_id="c1", url="https://lu.ma/bad", scrape_interval_minutes=15
    )

    def fake_run(*, conference_id, source_url, events_repo, suggestions_repo=None):
        if source_url == "https://lu.ma/bad":
            raise RuntimeError("nope")
        return SourceScrapeStats(events_added=1)

    monkeypatch.setattr("app.scraper.scheduler.run_for_source", fake_run)

    sched = Scheduler(
        sources_repo=sources,
        events_repo=events,
        settings_repo=_enabled_settings(),
        tick_seconds=60,
    )
    result = await sched.tick_once()

    assert result == {"due": 2, "ran": 1, "failed_sources": 1}
    assert sources.get(good["id"])["last_status"] == "ok"  # type: ignore[index]
    assert sources.get(bad["id"])["last_status"] == "error"  # type: ignore[index]


@pytest.mark.asyncio
async def test_tick_skips_everything_when_disabled(monkeypatch) -> None:
    """When the UI toggle is off, the tick must not call run_for_source
    even if there are due sources."""
    sources = InMemoryScrapeSourcesRepo()
    events = InMemoryEventsAdminRepo()
    sources.create(
        conference_id="c1", url="https://lu.ma/x", scrape_interval_minutes=15
    )

    calls: list[str] = []

    def fake_run(*, conference_id, source_url, events_repo, suggestions_repo=None):
        calls.append(source_url)
        return SourceScrapeStats(events_added=1)

    monkeypatch.setattr("app.scraper.scheduler.run_for_source", fake_run)

    sched = Scheduler(
        sources_repo=sources,
        events_repo=events,
        settings_repo=_disabled_settings(),
        tick_seconds=60,
    )
    result = await sched.tick_once()
    assert result == {"due": 0, "ran": 0, "failed_sources": 0}
    assert calls == []


@pytest.mark.asyncio
async def test_concurrent_tick_is_skipped(monkeypatch) -> None:
    """If a tick is in flight, a concurrent call should bail rather than
    double-process the due set."""
    sources = InMemoryScrapeSourcesRepo()
    events = InMemoryEventsAdminRepo()
    sources.create(
        conference_id="c1", url="https://lu.ma/x", scrape_interval_minutes=15
    )

    call_count = 0
    gate = asyncio.Event()

    def fake_run(*, conference_id, source_url, events_repo, suggestions_repo=None):
        nonlocal call_count
        call_count += 1
        # Block until the gate is set, simulating a slow Luma fetch.
        # Note: this runs in a thread via asyncio.to_thread, so a blocking
        # wait is OK here.
        import time
        while not gate.is_set():
            time.sleep(0.01)
        return SourceScrapeStats(events_added=1)

    monkeypatch.setattr("app.scraper.scheduler.run_for_source", fake_run)

    sched = Scheduler(
        sources_repo=sources,
        events_repo=events,
        settings_repo=_enabled_settings(),
        tick_seconds=60,
    )

    # Kick off the first tick — it will block inside fake_run waiting on the gate.
    first = asyncio.create_task(sched.tick_once())
    # Give the first tick a moment to acquire the lock.
    await asyncio.sleep(0.05)
    # Second tick must skip immediately.
    second = await sched.tick_once()
    assert second == {"due": 0, "ran": 0, "failed_sources": 0}

    # Release the first tick.
    gate.set()
    first_result = await first
    assert first_result["ran"] == 1
    assert call_count == 1
