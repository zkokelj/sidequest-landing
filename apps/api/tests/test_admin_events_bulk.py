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


def _admin() -> CurrentUser:
    return CurrentUser(
        id=ADMIN_ID, email="admin@e.com", role="admin", raw_claims={"sub": ADMIN_ID}
    )


def _non_admin() -> CurrentUser:
    return CurrentUser(
        id=NON_ADMIN_ID, email="u@e.com", role=None, raw_claims={"sub": NON_ADMIN_ID}
    )


class _StubCatalog:
    """Returns a conference for CONF_ID, None for anything else."""

    def __init__(self, valid_ids: set[str]) -> None:
        self._valid = valid_ids

    def list_conferences(self, *, include_inactive: bool = False) -> list[ConferenceOut]:
        return []

    def get_conference(self, conference_id: str) -> ConferenceOut | None:
        if conference_id in self._valid:
            return ConferenceOut(id=conference_id, name=conference_id, days=[])
        return None

    def list_events(self, conference_id: str):  # pragma: no cover — unused in these tests
        return []


def _setup_admin(
    valid_confs: set[str] = frozenset({CONF_ID}),  # type: ignore[assignment]
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
    # Do NOT override require_admin — real dep should 403 this user.
    app.dependency_overrides[require_user] = _non_admin


def _teardown() -> None:
    for dep in (get_events_admin_repo, get_catalog_repo, require_admin, require_user):
        app.dependency_overrides.pop(dep, None)


def _ev(title: str, day: int = 29, hour: int = 9, **extra) -> dict:
    return {
        "title": title,
        "starts_at": f"2026-04-{day:02d}T{hour:02d}:00:00+04:00",
        "ends_at": f"2026-04-{day:02d}T{hour + 1:02d}:00:00+04:00",
        **extra,
    }


# ---------- auth ----------


def test_bulk_import_rejects_non_admin() -> None:
    _setup_non_admin()
    try:
        client = TestClient(app)
        r = client.post(
            "/api/admin/events/bulk",
            json={"conference_id": CONF_ID, "events": [_ev("a")]},
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 403, r.text
    finally:
        _teardown()


# ---------- validation ----------


def test_bulk_import_rejects_unknown_conference() -> None:
    _setup_admin()
    try:
        client = TestClient(app)
        r = client.post(
            "/api/admin/events/bulk",
            json={"conference_id": "no-such-conf", "events": [_ev("a")]},
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 404, r.text
        assert "no-such-conf" in r.json()["detail"]
    finally:
        _teardown()


def test_bulk_import_returns_per_row_errors_without_blocking_other_rows() -> None:
    repo = _setup_admin()
    try:
        client = TestClient(app)
        r = client.post(
            "/api/admin/events/bulk",
            json={
                "conference_id": CONF_ID,
                "events": [
                    _ev("good 1"),
                    # bad: ends before starts
                    {
                        "title": "bad",
                        "starts_at": "2026-04-29T10:00:00+04:00",
                        "ends_at": "2026-04-29T09:00:00+04:00",
                    },
                    _ev("good 2", hour=11),
                ],
            },
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["inserted"] == 2
        assert len(body["errors"]) == 1
        assert body["errors"][0]["index"] == 1
        assert "ends_at" in body["errors"][0]["message"]
        # 2 good rows landed
        assert len(repo.list_events(conference_id=CONF_ID)) == 2
    finally:
        _teardown()


def test_bulk_import_rejects_duplicate_ids_within_payload() -> None:
    _setup_admin()
    try:
        client = TestClient(app)
        r = client.post(
            "/api/admin/events/bulk",
            json={
                "conference_id": CONF_ID,
                "events": [
                    {"id": "dup", **_ev("a")},
                    {"id": "dup", **_ev("b", hour=11)},
                ],
            },
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["inserted"] == 1
        assert len(body["errors"]) == 1
        assert body["errors"][0]["index"] == 1
        assert "duplicate id" in body["errors"][0]["message"]
    finally:
        _teardown()


# ---------- happy path + ID derivation ----------


def test_bulk_import_generates_stable_ids_when_omitted() -> None:
    repo = _setup_admin()
    try:
        client = TestClient(app)
        payload = {
            "conference_id": CONF_ID,
            "events": [_ev("Opening Keynote")],
        }
        r1 = client.post("/api/admin/events/bulk", json=payload,
                         headers={"Authorization": "Bearer dummy"})
        assert r1.status_code == 200 and r1.json()["inserted"] == 1

        # Same JSON again → updates, not duplicates
        r2 = client.post("/api/admin/events/bulk", json=payload,
                         headers={"Authorization": "Bearer dummy"})
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["inserted"] == 0
        assert body["updated"] == 1
        # Only one row in the DB
        assert len(repo.list_events(conference_id=CONF_ID)) == 1
        # ID matches our derivation scheme
        row = repo.list_events(conference_id=CONF_ID)[0]
        assert row["id"].startswith(f"import:{CONF_ID}:")
    finally:
        _teardown()


def test_bulk_import_supplied_id_is_used_verbatim() -> None:
    repo = _setup_admin()
    try:
        client = TestClient(app)
        r = client.post(
            "/api/admin/events/bulk",
            json={
                "conference_id": CONF_ID,
                "events": [{"id": "myagent:eth-keynote-1", **_ev("Keynote")}],
            },
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 200 and r.json()["inserted"] == 1
        assert repo.get_event("myagent:eth-keynote-1") is not None
    finally:
        _teardown()


def test_bulk_import_marks_rows_manual_and_unlocked() -> None:
    # Stays unlocked so admin/agent can re-import the same JSON to update.
    # Admin can call /events/{id}/lock to protect specific rows.
    repo = _setup_admin()
    try:
        client = TestClient(app)
        r = client.post(
            "/api/admin/events/bulk",
            json={"conference_id": CONF_ID, "events": [{"id": "x", **_ev("a")}]},
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 200
        row = repo.get_event("x")
        assert row["is_manual"] is True
        assert row["locked"] is False
        assert row["updated_by"] == ADMIN_ID
    finally:
        _teardown()


# ---------- conflict handling ----------


def test_bulk_import_skip_existing_locked_row() -> None:
    repo = _setup_admin()
    try:
        # Seed an admin-locked row
        repo.create_event(
            fields={
                "id": "locked-1",
                "conference_id": CONF_ID,
                "title": "Hand-curated",
                "starts_at": "2026-04-29T10:00:00+04:00",
                "ends_at": "2026-04-29T11:00:00+04:00",
            },
            updated_by="someone-else",
        )
        client = TestClient(app)
        r = client.post(
            "/api/admin/events/bulk",
            json={
                "conference_id": CONF_ID,
                "events": [{"id": "locked-1", **_ev("would clobber")}],
            },
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["skipped_locked"] == 1
        # Original title preserved
        assert repo.get_event("locked-1")["title"] == "Hand-curated"
    finally:
        _teardown()


def test_bulk_import_on_conflict_skip_leaves_existing_untouched() -> None:
    repo = _setup_admin()
    try:
        # Seed a scraper-style unlocked row
        repo.scraper_upsert(
            {
                "id": "scraped-1",
                "conference_id": CONF_ID,
                "title": "Original",
                "starts_at": "2026-04-29T10:00:00+04:00",
                "ends_at": "2026-04-29T11:00:00+04:00",
            }
        )
        client = TestClient(app)
        r = client.post(
            "/api/admin/events/bulk",
            json={
                "conference_id": CONF_ID,
                "on_conflict": "skip",
                "events": [{"id": "scraped-1", **_ev("would clobber")}],
            },
            headers={"Authorization": "Bearer dummy"},
        )
        body = r.json()
        assert body["skipped_conflict"] == 1
        assert body["updated"] == 0
        assert repo.get_event("scraped-1")["title"] == "Original"
    finally:
        _teardown()


def test_bulk_import_on_conflict_upsert_overwrites_unlocked() -> None:
    repo = _setup_admin()
    try:
        repo.scraper_upsert(
            {
                "id": "scraped-2",
                "conference_id": CONF_ID,
                "title": "Original",
                "starts_at": "2026-04-29T10:00:00+04:00",
                "ends_at": "2026-04-29T11:00:00+04:00",
            }
        )
        client = TestClient(app)
        r = client.post(
            "/api/admin/events/bulk",
            json={
                "conference_id": CONF_ID,
                "on_conflict": "upsert",
                "events": [{"id": "scraped-2", **_ev("Replaced")}],
            },
            headers={"Authorization": "Bearer dummy"},
        )
        body = r.json()
        assert body["updated"] == 1
        row = repo.get_event("scraped-2")
        assert row["title"] == "Replaced"
        # Bulk import marks rows as admin-curated but keeps them unlocked
        # so the next re-import of the same JSON updates rather than skips.
        assert row["locked"] is False
        assert row["is_manual"] is True
    finally:
        _teardown()


# ---------- dry run ----------


def test_bulk_import_dry_run_writes_nothing_but_returns_full_report() -> None:
    repo = _setup_admin()
    try:
        # Pre-seed one locked row to exercise the skipped_locked branch in dry-run
        repo.create_event(
            fields={
                "id": "existing-locked",
                "conference_id": CONF_ID,
                "title": "Locked",
                "starts_at": "2026-04-29T10:00:00+04:00",
                "ends_at": "2026-04-29T11:00:00+04:00",
            },
            updated_by="other-admin",
        )
        client = TestClient(app)
        r = client.post(
            "/api/admin/events/bulk?dry_run=true",
            json={
                "conference_id": CONF_ID,
                "events": [
                    {"id": "new-1", **_ev("new")},
                    {"id": "existing-locked", **_ev("would skip")},
                ],
            },
            headers={"Authorization": "Bearer dummy"},
        )
        body = r.json()
        assert body["dry_run"] is True
        assert body["inserted"] == 1
        assert body["skipped_locked"] == 1
        # Nothing actually written: still 1 row, with original title
        assert len(repo.list_events(conference_id=CONF_ID)) == 1
        assert repo.get_event("existing-locked")["title"] == "Locked"
        assert repo.get_event("new-1") is None
    finally:
        _teardown()


# ---------- limits ----------


def test_bulk_import_caps_payload_at_500_events() -> None:
    _setup_admin()
    try:
        client = TestClient(app)
        r = client.post(
            "/api/admin/events/bulk",
            json={
                "conference_id": CONF_ID,
                "events": [_ev(f"e{i}", hour=(i % 12) + 1) for i in range(501)],
            },
            headers={"Authorization": "Bearer dummy"},
        )
        assert r.status_code == 422, r.text
    finally:
        _teardown()
