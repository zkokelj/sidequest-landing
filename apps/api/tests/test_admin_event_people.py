from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.deps import CurrentUser, require_admin, require_user
from app.main import app
from app.models.schemas import ConferenceOut, EventOut
from app.services.admin_repo import InMemoryEventsAdminRepo, get_events_admin_repo
from app.services.catalog import CatalogRepo, get_catalog_repo
from app.services.event_suggestions_repo import (
    InMemoryEventSuggestionsRepo,
    get_event_suggestions_repo,
)
from app.services.suggestions_repo import (
    InMemorySuggestionsRepo,
    get_suggestions_repo,
)

ADMIN_ID = "00000000-aaaa-aaaa-aaaa-000000000001"
CONF_ID = "token2049"
OTHER_CONF_ID = "devcon7"
EVENT_ID = "evt-1"


def _admin() -> CurrentUser:
    return CurrentUser(
        id=ADMIN_ID, email="admin@e.com", role="admin", raw_claims={"sub": ADMIN_ID}
    )


class _StubCatalog:
    def __init__(self, valid_confs: set[str]) -> None:
        self._valid = valid_confs

    def list_conferences(self, *, include_inactive: bool = False) -> list[ConferenceOut]:
        return []

    def get_conference(self, conference_id: str) -> ConferenceOut | None:
        if conference_id in self._valid:
            return ConferenceOut(id=conference_id, name=conference_id, days=[])
        return None

    def list_events(self, conference_id: str) -> list[EventOut]:  # pragma: no cover
        return []


def _setup(
    *,
    seed_event: bool = True,
    seed_people: list[dict] | None = None,
) -> tuple[
    InMemoryEventsAdminRepo,
    InMemorySuggestionsRepo,
    InMemoryEventSuggestionsRepo,
]:
    events_repo = InMemoryEventsAdminRepo()
    if seed_event:
        events_repo.create_event(
            fields={
                "id": EVENT_ID,
                "conference_id": CONF_ID,
                "title": "Test event",
                "description": None,
                "starts_at": datetime(2026, 4, 29, 9, tzinfo=UTC).isoformat(),
                "ends_at": datetime(2026, 4, 29, 11, tzinfo=UTC).isoformat(),
                "venue": None,
                "tags": [],
                "url": None,
                "capacity": None,
                "attendees": None,
            },
            updated_by=ADMIN_ID,
        )
    suggestions = InMemorySuggestionsRepo()
    for p in seed_people or []:
        suggestions.upsert_luma_person(
            conference_id=p.get("conference_id", CONF_ID),
            name=p["name"],
            role=p.get("role"),
        )
    event_links = InMemoryEventSuggestionsRepo()
    catalog: CatalogRepo = _StubCatalog({CONF_ID, OTHER_CONF_ID})
    app.dependency_overrides[get_events_admin_repo] = lambda: events_repo
    app.dependency_overrides[get_suggestions_repo] = lambda: suggestions
    app.dependency_overrides[get_event_suggestions_repo] = lambda: event_links
    app.dependency_overrides[get_catalog_repo] = lambda: catalog
    app.dependency_overrides[require_admin] = _admin
    app.dependency_overrides[require_user] = _admin
    return events_repo, suggestions, event_links


def _clear() -> None:
    for dep in (
        get_events_admin_repo,
        get_suggestions_repo,
        get_event_suggestions_repo,
        get_catalog_repo,
        require_admin,
        require_user,
    ):
        app.dependency_overrides.pop(dep, None)


# ---------- attach: existing suggestion ----------


def test_attach_existing_suggestion() -> None:
    _setup(seed_people=[{"name": "Stani Kulechov"}])
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/admin/events/{EVENT_ID}/people",
            json={"suggestion_id": "luma:stani-kulechov"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["suggestion_id"] == "luma:stani-kulechov"
        assert body["name"] == "Stani Kulechov"
        assert body["link_source"] == "manual"
        # Listing reflects the new link
        rl = client.get(f"/api/admin/events/{EVENT_ID}/people")
        assert rl.status_code == 200
        listed = rl.json()
        assert len(listed) == 1
        assert listed[0]["suggestion_id"] == "luma:stani-kulechov"
    finally:
        _clear()


def test_attach_creates_new_manual_person() -> None:
    _setup()
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/admin/events/{EVENT_ID}/people",
            json={"name": "  New Speaker  ", "role": "Founder, Acme"},
        )
        assert resp.status_code == 201, resp.text
        body = resp.json()
        assert body["suggestion_id"] == "manual:new-speaker"
        assert body["name"] == "New Speaker"
        assert body["role"] == "Founder, Acme"
        assert body["person_source"] == "manual"
        assert body["link_source"] == "manual"
    finally:
        _clear()


def test_attach_rejects_both_or_neither() -> None:
    _setup()
    try:
        client = TestClient(app)
        # Both → 400
        r1 = client.post(
            f"/api/admin/events/{EVENT_ID}/people",
            json={"suggestion_id": "x", "name": "y"},
        )
        assert r1.status_code == 400
        # Neither → 400
        r2 = client.post(f"/api/admin/events/{EVENT_ID}/people", json={})
        assert r2.status_code == 400
    finally:
        _clear()


def test_attach_rejects_cross_conference_suggestion() -> None:
    _setup(
        seed_people=[
            {"name": "Other Person", "conference_id": OTHER_CONF_ID},
        ]
    )
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/admin/events/{EVENT_ID}/people",
            json={"suggestion_id": "luma:other-person"},
        )
        assert resp.status_code == 400
        assert "different conference" in resp.json()["detail"]
    finally:
        _clear()


def test_attach_unknown_suggestion_404() -> None:
    _setup()
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/admin/events/{EVENT_ID}/people",
            json={"suggestion_id": "luma:does-not-exist"},
        )
        assert resp.status_code == 404
    finally:
        _clear()


def test_attach_unknown_event_404() -> None:
    _setup(seed_event=False)
    try:
        client = TestClient(app)
        resp = client.post(
            "/api/admin/events/missing/people",
            json={"name": "Whoever"},
        )
        assert resp.status_code == 404
    finally:
        _clear()


def test_attach_rejects_empty_name() -> None:
    _setup()
    try:
        client = TestClient(app)
        resp = client.post(
            f"/api/admin/events/{EVENT_ID}/people",
            json={"name": "   "},
        )
        # has_name == False (whitespace strips empty), has_id == False → 400 from
        # the "provide exactly one" check.
        assert resp.status_code == 400
    finally:
        _clear()


# ---------- detach ----------


def test_detach_removes_link() -> None:
    _setup(seed_people=[{"name": "Stani"}])
    try:
        client = TestClient(app)
        client.post(
            f"/api/admin/events/{EVENT_ID}/people",
            json={"suggestion_id": "luma:stani"},
        )
        resp = client.delete(
            f"/api/admin/events/{EVENT_ID}/people/luma:stani"
        )
        assert resp.status_code == 204
        # Person row preserved; only the edge gone
        listed = client.get(f"/api/admin/events/{EVENT_ID}/people").json()
        assert listed == []
        # And the underlying suggestion is still in the conference list
        sugg = client.get(
            f"/api/admin/conferences/{CONF_ID}/suggestions?kind=people"
        ).json()
        assert any(s["id"] == "luma:stani" for s in sugg)
    finally:
        _clear()


def test_detach_is_idempotent_on_missing_link() -> None:
    _setup()
    try:
        client = TestClient(app)
        resp = client.delete(
            f"/api/admin/events/{EVENT_ID}/people/never-linked"
        )
        # Idempotent: 204 even when nothing was removed
        assert resp.status_code == 204
    finally:
        _clear()


# ---------- list conference suggestions ----------


def test_list_conference_suggestions_filters_by_kind() -> None:
    _setup(
        seed_people=[
            {"name": "Person A"},
            {"name": "Person B"},
        ]
    )
    try:
        client = TestClient(app)
        all_resp = client.get(f"/api/admin/conferences/{CONF_ID}/suggestions").json()
        assert len(all_resp) == 2
        kind_resp = client.get(
            f"/api/admin/conferences/{CONF_ID}/suggestions?kind=people"
        ).json()
        assert len(kind_resp) == 2
        # Wrong kind filters to empty
        other = client.get(
            f"/api/admin/conferences/{CONF_ID}/suggestions?kind=companies"
        ).json()
        assert other == []
    finally:
        _clear()


def test_list_conference_suggestions_unknown_conference_404() -> None:
    _setup()
    try:
        client = TestClient(app)
        resp = client.get("/api/admin/conferences/missing/suggestions")
        assert resp.status_code == 404
    finally:
        _clear()
