from __future__ import annotations

from fastapi.testclient import TestClient

from app.deps import CurrentUser, require_admin, require_user
from app.main import app
from app.models.schemas import ConferenceOut
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


def _admin() -> CurrentUser:
    return CurrentUser(
        id=ADMIN_ID, email="admin@e.com", role="admin", raw_claims={"sub": ADMIN_ID}
    )


class _StubCatalog:
    def __init__(self, valid: set[str]) -> None:
        self._valid = valid

    def list_conferences(self, *, include_inactive: bool = False) -> list[ConferenceOut]:
        return []

    def get_conference(self, conference_id: str) -> ConferenceOut | None:
        if conference_id in self._valid:
            return ConferenceOut(id=conference_id, name=conference_id, days=[])
        return None

    def list_events(self, conference_id: str):  # pragma: no cover
        return []


def _setup() -> tuple[InMemorySuggestionsRepo, InMemoryEventSuggestionsRepo]:
    suggestions = InMemorySuggestionsRepo()
    # Seed a mix so we can prove the source filter does what it claims.
    suggestions.upsert_luma_person(
        conference_id=CONF_ID, name="Berko", role=None
    )
    suggestions.upsert_luma_person(
        conference_id=CONF_ID, name="Fifi", role=None
    )
    suggestions.upsert_llm_person(
        conference_id=CONF_ID, name="Vitalik Buterin", role="Ethereum"
    )
    suggestions.upsert_manual_person(
        conference_id=CONF_ID, name="Manual Pick", role=None
    )
    event_links = InMemoryEventSuggestionsRepo()
    catalog: CatalogRepo = _StubCatalog({CONF_ID})
    app.dependency_overrides[get_catalog_repo] = lambda: catalog
    app.dependency_overrides[get_suggestions_repo] = lambda: suggestions
    app.dependency_overrides[get_event_suggestions_repo] = lambda: event_links
    app.dependency_overrides[require_admin] = _admin
    app.dependency_overrides[require_user] = _admin
    return suggestions, event_links


def _clear() -> None:
    for dep in (
        get_catalog_repo,
        get_suggestions_repo,
        get_event_suggestions_repo,
        require_admin,
        require_user,
    ):
        app.dependency_overrides.pop(dep, None)


# ---------- bulk delete ----------


def test_bulk_delete_defaults_to_llm_only() -> None:
    suggestions, _ = _setup()
    try:
        client = TestClient(app)
        resp = client.delete(f"/api/admin/conferences/{CONF_ID}/suggestions")
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"deleted": 1}
        # Only the LLM row is gone; Luma + manual survive.
        names = {r["name"] for r in suggestions.list_for_conference(CONF_ID)}
        assert names == {"Berko", "Fifi", "Manual Pick"}
    finally:
        _clear()


def test_bulk_delete_source_luma() -> None:
    suggestions, _ = _setup()
    try:
        client = TestClient(app)
        resp = client.delete(
            f"/api/admin/conferences/{CONF_ID}/suggestions?source=luma"
        )
        assert resp.status_code == 200
        assert resp.json() == {"deleted": 2}
        names = {r["name"] for r in suggestions.list_for_conference(CONF_ID)}
        assert names == {"Vitalik Buterin", "Manual Pick"}
    finally:
        _clear()


def test_bulk_delete_source_all_wipes_everything() -> None:
    suggestions, _ = _setup()
    try:
        client = TestClient(app)
        resp = client.delete(
            f"/api/admin/conferences/{CONF_ID}/suggestions?source=all"
        )
        assert resp.status_code == 200
        assert resp.json() == {"deleted": 4}
        assert suggestions.list_for_conference(CONF_ID) == []
    finally:
        _clear()


def test_bulk_delete_rejects_unknown_source() -> None:
    _setup()
    try:
        client = TestClient(app)
        resp = client.delete(
            f"/api/admin/conferences/{CONF_ID}/suggestions?source=garbage"
        )
        assert resp.status_code == 400
    finally:
        _clear()


def test_bulk_delete_unknown_conference_404() -> None:
    _setup()
    try:
        client = TestClient(app)
        resp = client.delete("/api/admin/conferences/missing/suggestions")
        assert resp.status_code == 404
    finally:
        _clear()


# ---------- PATCH ----------


def test_patch_updates_name_and_role() -> None:
    suggestions, _ = _setup()
    try:
        client = TestClient(app)
        resp = client.patch(
            "/api/admin/suggestions/llm:vitalik-buterin",
            json={"name": "Vitalik B.", "role": "Co-founder, Ethereum"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # ID is immutable even though name changed
        assert body["id"] == "llm:vitalik-buterin"
        assert body["name"] == "Vitalik B."
        assert body["role"] == "Co-founder, Ethereum"
        # And it persisted
        row = suggestions.get_by_id("llm:vitalik-buterin")
        assert row is not None and row["name"] == "Vitalik B."
    finally:
        _clear()


def test_patch_clear_role_with_empty_string() -> None:
    suggestions, _ = _setup()
    try:
        client = TestClient(app)
        resp = client.patch(
            "/api/admin/suggestions/llm:vitalik-buterin",
            json={"role": ""},
        )
        assert resp.status_code == 200
        assert resp.json()["role"] is None
        row = suggestions.get_by_id("llm:vitalik-buterin")
        assert row is not None and row["role"] is None
    finally:
        _clear()


def test_patch_rejects_empty_name() -> None:
    _setup()
    try:
        client = TestClient(app)
        resp = client.patch(
            "/api/admin/suggestions/llm:vitalik-buterin",
            json={"name": "   "},
        )
        assert resp.status_code == 400
    finally:
        _clear()


def test_patch_unknown_id_404() -> None:
    _setup()
    try:
        client = TestClient(app)
        resp = client.patch(
            "/api/admin/suggestions/llm:never-existed",
            json={"name": "Whoever"},
        )
        assert resp.status_code == 404
    finally:
        _clear()


def test_patch_omitting_both_returns_current_row() -> None:
    # No-op PATCH with empty body — useful sanity check for the client
    # treating PATCH idempotently.
    _setup()
    try:
        client = TestClient(app)
        resp = client.patch(
            "/api/admin/suggestions/llm:vitalik-buterin", json={}
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Vitalik Buterin"
    finally:
        _clear()
