from __future__ import annotations

from fastapi.testclient import TestClient

from app.main import app
from app.models.schemas import ConferenceOut
from app.services.catalog import CatalogRepo, get_catalog_repo
from app.services.suggestions_repo import (
    InMemorySuggestionsRepo,
    get_suggestions_repo,
)

CONF_ID = "token2049"


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


def _setup(*, seed: list[dict] | None = None) -> InMemorySuggestionsRepo:
    repo = InMemorySuggestionsRepo()
    # Use both upsert paths so we cover seed (luma) and llm-created rows.
    for p in seed or []:
        if p.get("via") == "llm":
            repo.upsert_llm_person(
                conference_id=CONF_ID, name=p["name"], role=p.get("role")
            )
        else:
            repo.upsert_luma_person(
                conference_id=CONF_ID, name=p["name"], role=p.get("role")
            )
    catalog: CatalogRepo = _StubCatalog({CONF_ID})
    app.dependency_overrides[get_suggestions_repo] = lambda: repo
    app.dependency_overrides[get_catalog_repo] = lambda: catalog
    return repo


def _clear() -> None:
    app.dependency_overrides.pop(get_suggestions_repo, None)
    app.dependency_overrides.pop(get_catalog_repo, None)


def test_public_suggestions_returns_all_for_conference() -> None:
    _setup(
        seed=[
            {"name": "Stani Kulechov", "role": "Aave"},
            {"name": "Vitalik Buterin", "role": "Ethereum", "via": "llm"},
        ]
    )
    try:
        client = TestClient(app)
        resp = client.get(f"/api/conferences/{CONF_ID}/suggestions")
        assert resp.status_code == 200
        data = resp.json()
        assert {p["name"] for p in data} == {"Stani Kulechov", "Vitalik Buterin"}
        # Kind is always 'people' for what we seeded
        assert all(p["kind"] == "people" for p in data)
    finally:
        _clear()


def test_public_suggestions_filters_by_kind() -> None:
    _setup(seed=[{"name": "Stani"}])
    try:
        client = TestClient(app)
        # Existing rows are kind='people'
        people = client.get(
            f"/api/conferences/{CONF_ID}/suggestions?kind=people"
        ).json()
        assert len(people) == 1
        # 'companies' filters to empty
        companies = client.get(
            f"/api/conferences/{CONF_ID}/suggestions?kind=companies"
        ).json()
        assert companies == []
    finally:
        _clear()


def test_public_suggestions_rejects_invalid_kind() -> None:
    _setup()
    try:
        client = TestClient(app)
        resp = client.get(
            f"/api/conferences/{CONF_ID}/suggestions?kind=robots"
        )
        assert resp.status_code == 400
    finally:
        _clear()


def test_public_suggestions_unknown_conference_404() -> None:
    _setup()
    try:
        client = TestClient(app)
        resp = client.get("/api/conferences/does-not-exist/suggestions")
        assert resp.status_code == 404
    finally:
        _clear()


def test_public_suggestions_empty_conference_returns_empty_list() -> None:
    _setup()
    try:
        client = TestClient(app)
        resp = client.get(f"/api/conferences/{CONF_ID}/suggestions")
        assert resp.status_code == 200
        assert resp.json() == []
    finally:
        _clear()
