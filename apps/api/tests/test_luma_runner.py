"""Slice 2 — end-to-end test of the admin trigger wired to the Luma runner.

Uses httpx.MockTransport to fake Luma's API and FastAPI dependency overrides
to inject in-memory repos. Verifies the upsert contract: first run adds, second
run updates in place (no duplicates), locked rows are skipped, and a failing
source doesn't kill the whole batch.
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi.testclient import TestClient

from app.deps import CurrentUser, require_admin, require_user
from app.main import app
from app.scraper.luma_runner import run_for_source
from app.scraper.sources.luma import LumaScraper
from app.services.admin_repo import (
    InMemoryEventsAdminRepo,
    get_events_admin_repo,
)
from app.services.scrape_sources_repo import (
    InMemoryScrapeSourcesRepo,
    get_scrape_sources_repo,
)
from app.services.suggestions_repo import (
    InMemorySuggestionsRepo,
    get_suggestions_repo,
)

ADMIN_ID = "00000000-aaaa-aaaa-aaaa-000000000001"


def _admin() -> CurrentUser:
    return CurrentUser(
        id=ADMIN_ID, email="admin@e.com", role="admin", raw_claims={"sub": ADMIN_ID}
    )


def _event(api_id: str, name: str, hour: int) -> dict[str, Any]:
    return {
        "event": {
            "api_id": api_id,
            "name": name,
            "url": f"slug-{api_id}",
            "start_at": f"2026-10-01T{hour:02d}:00:00Z",
            "end_at": f"2026-10-01T{hour + 1:02d}:00:00Z",
            "geo_address_info": {"full_address": "Marina Bay"},
        }
    }


def _build_handler(
    *,
    calendar_id: str = "cal_xyz",
    events_by_calendar: dict[str, list[dict[str, Any]]] | None = None,
    details_by_api_id: dict[str, dict[str, Any]] | None = None,
) -> Any:
    """Build a MockTransport handler that fakes Luma's API.

    `/event/get` returns an empty details object by default. Tests that care
    about description/capacity/attendees pass `details_by_api_id`.
    """
    events_by_calendar = events_by_calendar or {}
    details_by_api_id = details_by_api_id or {}

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/url":
            slug = req.url.params.get("url", "")
            if slug.startswith("missing"):
                return httpx.Response(200, json={"data": {"calendar": {}}})
            return httpx.Response(
                200, json={"data": {"calendar": {"api_id": calendar_id}}}
            )
        if req.url.path == "/calendar/get-items":
            cid = req.url.params.get("calendar_api_id", "")
            entries = events_by_calendar.get(cid, [])
            return httpx.Response(
                200, json={"entries": entries, "has_more": False, "next_cursor": None}
            )
        if req.url.path == "/event/get":
            api_id = req.url.params.get("event_api_id", "")
            return httpx.Response(200, json=details_by_api_id.get(api_id, {}))
        return httpx.Response(404)

    return handler


def _scraper_with(handler: Any) -> LumaScraper:
    return LumaScraper(
        client=httpx.Client(
            base_url="https://api.lu.ma",
            transport=httpx.MockTransport(handler),
        )
    )


# ---------- run_for_source: upsert semantics ----------


def test_run_for_source_first_run_adds_then_second_run_updates() -> None:
    handler = _build_handler(
        events_by_calendar={
            "cal_xyz": [
                _event("e1", "Opening", 9),
                _event("e2", "Closing", 18),
            ]
        }
    )
    repo = InMemoryEventsAdminRepo()

    with _scraper_with(handler) as scraper:
        first = run_for_source(
            conference_id="token2049",
            source_url="https://lu.ma/token2049",
            events_repo=repo,
            scraper=scraper,
        )

    assert first.events_added == 2
    assert first.events_updated == 0
    assert first.events_failed == 0
    assert {r["id"] for r in repo.list_events(conference_id="token2049")} == {
        "luma:e1",
        "luma:e2",
    }

    # Re-run with the same data — should be all updates, no new rows
    with _scraper_with(handler) as scraper:
        second = run_for_source(
            conference_id="token2049",
            source_url="https://lu.ma/token2049",
            events_repo=repo,
            scraper=scraper,
        )

    assert second.events_added == 0
    assert second.events_updated == 2
    assert len(repo.list_events(conference_id="token2049")) == 2  # no dupes


def test_run_for_source_fetches_details_by_default() -> None:
    """Regression: the default scrape must call /event/get and populate
    description/capacity/attendees. Was off by default before — events
    scraped via the admin trigger arrived blank."""
    handler = _build_handler(
        events_by_calendar={"cal_xyz": [_event("e1", "Stable Summit", 9)]},
        details_by_api_id={
            "e1": {
                "capacity": 320,
                "guest_count": 280,
                "description_mirror": {
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Top-tier stablecoin talks."}],
                        }
                    ]
                },
            }
        },
    )
    repo = InMemoryEventsAdminRepo()

    with _scraper_with(handler) as scraper:
        stats = run_for_source(
            conference_id="token2049",
            source_url="https://lu.ma/token2049",
            events_repo=repo,
            scraper=scraper,
        )

    assert stats.events_added == 1
    row = repo.get_event("luma:e1")
    assert row is not None
    assert row["description"] == "Top-tier stablecoin talks."
    assert row["capacity"] == 320
    assert row["attendees"] == 280


def test_run_for_source_skips_locked_rows() -> None:
    handler = _build_handler(
        events_by_calendar={"cal_xyz": [_event("e1", "Locked Event", 9)]}
    )
    repo = InMemoryEventsAdminRepo()

    # Seed a locked version first
    repo.scraper_upsert(
        {
            "id": "luma:e1",
            "conference_id": "token2049",
            "title": "Admin-edited title",
            "starts_at": "2026-10-01T09:00:00Z",
            "ends_at": "2026-10-01T10:00:00Z",
        }
    )
    repo.set_lock("luma:e1", locked=True, updated_by=ADMIN_ID)

    with _scraper_with(handler) as scraper:
        stats = run_for_source(
            conference_id="token2049",
            source_url="https://lu.ma/token2049",
            events_repo=repo,
            scraper=scraper,
        )

    assert stats.events_skipped_locked == 1
    assert stats.events_updated == 0
    # Admin's title preserved despite the scraper running
    row = repo.get_event("luma:e1")
    assert row is not None
    assert row["title"] == "Admin-edited title"


def test_run_for_source_captures_hosts_and_featured_guests() -> None:
    """Hosts + featured_guests from /event/get should land in
    conference_suggestions with source='luma'. Re-scraping the same
    name in another event should no-op (PK collision via slug)."""
    handler = _build_handler(
        events_by_calendar={
            "cal_xyz": [
                _event("e1", "DeFi Mafia Tram Party", 20),
                _event("e2", "Builders Breakfast", 9),
            ]
        },
        details_by_api_id={
            "e1": {
                "hosts": [
                    {"name": "DavideFi", "bio_short": "GM at LFJ dex"},
                    {"name": "C-l.hl",   "bio_short": "Bd @ Pear Protocol"},
                ],
                "featured_guests": [
                    {"name": "Julian Morla", "bio_short": "Head of Integrations @ LI.FI"},
                    {"name": "",             "bio_short": "should be skipped"},
                ],
            },
            "e2": {
                # DavideFi shows up again as a guest at a second event — dedup.
                "featured_guests": [
                    {"name": "davidefi", "bio_short": "DIFFERENT bio_short"},
                    {"name": "Andrea Mars", "bio_short": "Organizer"},
                ],
            },
        },
    )
    events_repo = InMemoryEventsAdminRepo()
    suggestions_repo = InMemorySuggestionsRepo()

    with _scraper_with(handler) as scraper:
        stats = run_for_source(
            conference_id="token2049",
            source_url="https://lu.ma/token2049",
            events_repo=events_repo,
            suggestions_repo=suggestions_repo,
            scraper=scraper,
        )

    # 4 distinct people: DavideFi, C-l.hl, Julian Morla, Andrea Mars
    assert stats.suggestions_added == 4
    rows = {r["name"]: r for r in suggestions_repo.list_for_conference("token2049")}
    assert set(rows.keys()) == {"DavideFi", "C-l.hl", "Julian Morla", "Andrea Mars"}
    # All rows tagged with source='luma' so the admin UI can tell them apart.
    assert all(r["source"] == "luma" for r in rows.values())
    # First role for DavideFi wins (the one from e1), not the e2 one.
    assert rows["DavideFi"]["role"] == "GM at LFJ dex"
    # Source event id captured so admins can see WHICH event surfaced the person.
    assert rows["DavideFi"]["meta"]["source_event_id"] == "luma:e1"


def test_run_for_source_without_suggestions_repo_skips_capture() -> None:
    """Backwards-compat: callers that don't pass a suggestions_repo must
    still work — the events upsert path is unchanged."""
    handler = _build_handler(
        events_by_calendar={"cal_xyz": [_event("e1", "Foo", 9)]},
        details_by_api_id={"e1": {"hosts": [{"name": "Someone"}]}},
    )
    events_repo = InMemoryEventsAdminRepo()
    with _scraper_with(handler) as scraper:
        stats = run_for_source(
            conference_id="c1",
            source_url="https://lu.ma/c1",
            events_repo=events_repo,
            scraper=scraper,
        )
    assert stats.events_added == 1
    assert stats.suggestions_added == 0


# ---------- admin trigger endpoint integration ----------


def _setup_admin_routes() -> tuple[
    InMemoryEventsAdminRepo, InMemoryScrapeSourcesRepo, InMemorySuggestionsRepo
]:
    events_repo = InMemoryEventsAdminRepo()
    sources_repo = InMemoryScrapeSourcesRepo()
    suggestions_repo = InMemorySuggestionsRepo()
    app.dependency_overrides[get_events_admin_repo] = lambda: events_repo
    app.dependency_overrides[get_scrape_sources_repo] = lambda: sources_repo
    app.dependency_overrides[get_suggestions_repo] = lambda: suggestions_repo
    app.dependency_overrides[require_admin] = _admin
    app.dependency_overrides[require_user] = _admin
    return events_repo, sources_repo, suggestions_repo


def _teardown_admin_routes() -> None:
    for dep in (
        get_events_admin_repo,
        get_scrape_sources_repo,
        get_suggestions_repo,
        require_admin,
        require_user,
    ):
        app.dependency_overrides.pop(dep, None)


def test_trigger_scrape_no_sources_returns_ok() -> None:
    events_repo, sources_repo, suggestions_repo = _setup_admin_routes()
    try:
        client = TestClient(app)
        resp = client.post("/api/admin/conferences/token2049/scrape")
        assert resp.status_code == 200
        body = resp.json()
        assert body["ok"] is True
        assert body["sources_attempted"] == 0
        assert body["events_added"] == 0
    finally:
        _teardown_admin_routes()


def test_trigger_scrape_runs_real_scraper_via_monkeypatch(monkeypatch) -> None:
    events_repo, sources_repo, suggestions_repo = _setup_admin_routes()
    try:
        sources_repo.create(
            conference_id="token2049", url="https://lu.ma/token2049", enabled=True
        )

        handler = _build_handler(
            events_by_calendar={
                "cal_xyz": [_event("e1", "A", 9), _event("e2", "B", 10)]
            }
        )

        def fake_scraper() -> LumaScraper:
            return _scraper_with(handler)

        # Patch the LumaScraper used inside run_for_source so it picks up our
        # MockTransport without us having to plumb it through the router.
        monkeypatch.setattr("app.scraper.luma_runner.LumaScraper", fake_scraper)

        client = TestClient(app)
        resp = client.post("/api/admin/conferences/token2049/scrape")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["sources_attempted"] == 1
        assert body["sources_failed"] == 0
        assert body["events_added"] == 2
        assert body["events_updated"] == 0

        # Second run should update, not add
        resp2 = client.post("/api/admin/conferences/token2049/scrape")
        body2 = resp2.json()
        assert body2["events_added"] == 0
        assert body2["events_updated"] == 2
        assert len(events_repo.list_events(conference_id="token2049")) == 2

        # And the source's last_scrape was recorded successfully
        sources = sources_repo.list_for_conference("token2049")
        assert sources[0]["last_status"] == "ok"
        assert sources[0]["last_error"] is None
        assert sources[0]["events_updated"] == 2
    finally:
        _teardown_admin_routes()


def test_trigger_scrape_surfaces_failed_events(monkeypatch) -> None:
    """A Luma entry missing start_at should land in failed_events with a useful reason."""
    events_repo, sources_repo, suggestions_repo = _setup_admin_routes()
    try:
        sources_repo.create(
            conference_id="token2049", url="https://lu.ma/token2049", enabled=True
        )

        good = _event("e1", "Good", 9)
        bad = _event("e2", "Bad", 10)
        bad["event"]["start_at"] = None  # forces normalize_event → None

        handler = _build_handler(events_by_calendar={"cal_xyz": [good, bad]})
        monkeypatch.setattr(
            "app.scraper.luma_runner.LumaScraper", lambda: _scraper_with(handler)
        )

        client = TestClient(app)
        resp = client.post("/api/admin/conferences/token2049/scrape")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["events_added"] == 1
        assert body["events_failed"] == 1
        assert len(body["failed_events"]) == 1
        failed = body["failed_events"][0]
        assert failed["api_id"] == "e2"
        assert failed["reason"] == "missing_required"
        assert failed["detail"] is not None
        assert "start_at" in failed["detail"]
        # Best-effort context for admins — the entry still had a slug + name
        assert failed["url"] == "https://lu.ma/slug-e2"
        assert failed["title"] == "Bad"
    finally:
        _teardown_admin_routes()


def test_trigger_scrape_records_error_when_source_fails(monkeypatch) -> None:
    events_repo, sources_repo, suggestions_repo = _setup_admin_routes()
    try:
        # Two sources: one good, one that 404s on /url
        sources_repo.create(
            conference_id="token2049", url="https://lu.ma/good", enabled=True
        )
        sources_repo.create(
            conference_id="token2049", url="https://lu.ma/missing-cal", enabled=True
        )

        handler = _build_handler(
            events_by_calendar={"cal_xyz": [_event("e1", "A", 9)]}
        )
        monkeypatch.setattr(
            "app.scraper.luma_runner.LumaScraper", lambda: _scraper_with(handler)
        )

        client = TestClient(app)
        resp = client.post("/api/admin/conferences/token2049/scrape")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is False
        assert body["sources_attempted"] == 2
        assert body["sources_failed"] == 1
        assert body["events_added"] == 1  # the good source still wrote its event

        sources = {s["url"]: s for s in sources_repo.list_for_conference("token2049")}
        assert sources["https://lu.ma/good"]["last_status"] == "ok"
        assert sources["https://lu.ma/missing-cal"]["last_status"] == "error"
        assert sources["https://lu.ma/missing-cal"]["last_error"]
    finally:
        _teardown_admin_routes()
