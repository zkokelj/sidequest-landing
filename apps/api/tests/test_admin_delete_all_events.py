"""Tests for DELETE /api/admin/conferences/{id}/events.

The endpoint wipes every event under one conference. By default it preserves
locked rows — admin must unlock them first or pass include_locked=true.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.deps import CurrentUser, require_admin, require_user
from app.main import app
from app.models.schemas import ConferenceOut
from app.services.admin_repo import InMemoryEventsAdminRepo, get_events_admin_repo
from app.services.catalog import CatalogRepo, get_catalog_repo

ADMIN_ID = "00000000-aaaa-aaaa-aaaa-000000000001"
NON_ADMIN_ID = "00000000-bbbb-bbbb-bbbb-000000000002"
CONF_ID = "token2049"
OTHER_CONF_ID = "devcon"


def _admin() -> CurrentUser:
    return CurrentUser(
        id=ADMIN_ID, email="admin@e.com", role="admin", raw_claims={"sub": ADMIN_ID}
    )


def _non_admin() -> CurrentUser:
    return CurrentUser(
        id=NON_ADMIN_ID, email="u@e.com", role=None, raw_claims={"sub": NON_ADMIN_ID}
    )


class _StubCatalog:
    def __init__(self, valid_ids: set[str]) -> None:
        self._valid = valid_ids

    def list_conferences(self, *, include_inactive: bool = False) -> list[ConferenceOut]:
        return []

    def get_conference(self, conference_id: str) -> ConferenceOut | None:
        if conference_id in self._valid:
            return ConferenceOut(id=conference_id, name=conference_id, days=[])
        return None

    def list_events(self, conference_id: str):  # pragma: no cover
        return []


def _setup_admin(
    valid_confs: set[str] = frozenset({CONF_ID, OTHER_CONF_ID}),  # type: ignore[assignment]
) -> InMemoryEventsAdminRepo:
    repo = InMemoryEventsAdminRepo()
    catalog: CatalogRepo = _StubCatalog(set(valid_confs))
    app.dependency_overrides[get_events_admin_repo] = lambda: repo
    app.dependency_overrides[get_catalog_repo] = lambda: catalog
    app.dependency_overrides[require_admin] = _admin
    app.dependency_overrides[require_user] = _admin
    return repo


def _setup_non_admin() -> None:
    app.dependency_overrides[get_events_admin_repo] = lambda: InMemoryEventsAdminRepo()
    app.dependency_overrides[get_catalog_repo] = lambda: _StubCatalog({CONF_ID})
    app.dependency_overrides[require_user] = _non_admin


def _teardown() -> None:
    for dep in (get_events_admin_repo, get_catalog_repo, require_admin, require_user):
        app.dependency_overrides.pop(dep, None)


def _seed(repo: InMemoryEventsAdminRepo) -> None:
    # Unlocked scraped row in CONF_ID
    repo.scraper_upsert(
        {
            "id": "scraped-a",
            "conference_id": CONF_ID,
            "title": "Scraped A",
            "starts_at": "2026-04-29T10:00:00+04:00",
            "ends_at": "2026-04-29T11:00:00+04:00",
        }
    )
    repo.scraper_upsert(
        {
            "id": "scraped-b",
            "conference_id": CONF_ID,
            "title": "Scraped B",
            "starts_at": "2026-04-29T12:00:00+04:00",
            "ends_at": "2026-04-29T13:00:00+04:00",
        }
    )
    # Manual (locked=true) in CONF_ID
    repo.create_event(
        fields={
            "id": "manual-locked",
            "conference_id": CONF_ID,
            "title": "Manual Locked",
            "starts_at": "2026-04-29T14:00:00+04:00",
            "ends_at": "2026-04-29T15:00:00+04:00",
        },
        updated_by=ADMIN_ID,
    )
    # Unrelated conference — must not be touched
    repo.scraper_upsert(
        {
            "id": "other-1",
            "conference_id": OTHER_CONF_ID,
            "title": "Other",
            "starts_at": "2026-04-29T10:00:00+04:00",
            "ends_at": "2026-04-29T11:00:00+04:00",
        }
    )


def test_rejects_non_admin() -> None:
    _setup_non_admin()
    try:
        client = TestClient(app)
        r = client.delete(
            f"/api/admin/conferences/{CONF_ID}/events",
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 403
    finally:
        _teardown()


def test_404_when_conference_missing() -> None:
    _setup_admin()
    try:
        client = TestClient(app)
        r = client.delete(
            "/api/admin/conferences/does-not-exist/events",
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 404
    finally:
        _teardown()


def test_default_preserves_locked_and_scopes_to_conference() -> None:
    repo = _setup_admin()
    try:
        _seed(repo)
        client = TestClient(app)

        r = client.delete(
            f"/api/admin/conferences/{CONF_ID}/events",
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"deleted": 2, "skipped_locked": 1}

        # Locked row survives; other conference untouched.
        assert repo.get_event("scraped-a") is None
        assert repo.get_event("scraped-b") is None
        assert repo.get_event("manual-locked") is not None
        assert repo.get_event("other-1") is not None
    finally:
        _teardown()


def test_include_locked_true_wipes_everything_in_conference() -> None:
    repo = _setup_admin()
    try:
        _seed(repo)
        client = TestClient(app)

        r = client.delete(
            f"/api/admin/conferences/{CONF_ID}/events?include_locked=true",
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body == {"deleted": 3, "skipped_locked": 0}

        assert repo.get_event("scraped-a") is None
        assert repo.get_event("manual-locked") is None
        # Other conference still untouched.
        assert repo.get_event("other-1") is not None
    finally:
        _teardown()


def test_empty_conference_returns_zero_counts() -> None:
    _setup_admin()
    try:
        client = TestClient(app)
        r = client.delete(
            f"/api/admin/conferences/{CONF_ID}/events",
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 200
        assert r.json() == {"deleted": 0, "skipped_locked": 0}
    finally:
        _teardown()
