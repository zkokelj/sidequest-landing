from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from app.deps import CurrentUser, require_admin, require_user
from app.main import app
from app.models.schemas import ConferenceOut, EventOut
from app.services.catalog import CatalogRepo, get_catalog_repo
from app.services.event_suggestions_repo import (
    InMemoryEventSuggestionsRepo,
    get_event_suggestions_repo,
)
from app.services.llm import LLMResult, get_llm_client
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
    def __init__(self, events: list[EventOut]) -> None:
        self._events = events

    def list_conferences(self, *, include_inactive: bool = False) -> list[ConferenceOut]:
        return []

    def get_conference(self, conference_id: str) -> ConferenceOut | None:
        if conference_id == CONF_ID:
            return ConferenceOut(id=CONF_ID, name="Token2049", days=[])
        return None

    def list_events(self, conference_id: str) -> list[EventOut]:
        return [e for e in self._events if e.conference_id == conference_id]


class _MockLLM:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[tuple[str, str, str | None]] = []

    async def complete_json(
        self, system: str, user: str, *, model: str | None = None
    ) -> LLMResult:
        self.calls.append((system, user, model))
        return LLMResult(
            content=json.dumps(self.payload),
            tokens_used=4242,
            model=model or "test/model",
        )


def _event(eid: str, title: str, description: str = "") -> EventOut:
    return EventOut(
        id=eid,
        conference_id=CONF_ID,
        title=title,
        description=description,
        start=datetime(2026, 4, 29, 9, tzinfo=UTC),
        end=datetime(2026, 4, 29, 11, tzinfo=UTC),
        tags=[],
    )


def _setup(
    *,
    events: list[EventOut],
    seed_people: list[dict] | None = None,
    llm_payload: dict,
) -> tuple[_MockLLM, InMemorySuggestionsRepo, InMemoryEventSuggestionsRepo]:
    suggestions = InMemorySuggestionsRepo()
    for p in seed_people or []:
        suggestions.upsert_luma_person(
            conference_id=CONF_ID,
            name=p["name"],
            role=p.get("role"),
        )
    event_links = InMemoryEventSuggestionsRepo()
    mock_llm = _MockLLM(llm_payload)
    catalog: CatalogRepo = _StubCatalog(events)
    app.dependency_overrides[get_catalog_repo] = lambda: catalog
    app.dependency_overrides[get_suggestions_repo] = lambda: suggestions
    app.dependency_overrides[get_event_suggestions_repo] = lambda: event_links
    app.dependency_overrides[get_llm_client] = lambda: mock_llm
    app.dependency_overrides[require_admin] = _admin
    app.dependency_overrides[require_user] = _admin
    return mock_llm, suggestions, event_links


def _clear() -> None:
    for dep in (
        get_catalog_repo,
        get_suggestions_repo,
        get_event_suggestions_repo,
        get_llm_client,
        require_admin,
        require_user,
    ):
        app.dependency_overrides.pop(dep, None)


def test_generate_people_creates_new_people_and_links() -> None:
    events = [
        _event("e1", "Fireside with Vitalik", "Vitalik Buterin on Ethereum's roadmap."),
        _event("e2", "DeFi panel", "Stani Kulechov hosts a deep dive."),
    ]
    # Luma scraped Stani already. Vitalik is new. The LLM associates Stani and
    # creates Vitalik.
    seed_people = [{"name": "Stani Kulechov", "role": "Founder, Aave"}]
    payload = {
        "associations": [
            {
                "suggestion_id": "luma:stani-kulechov",
                "event_ids": ["e2"],
                "confidence": 0.95,
            }
        ],
        "new_people": [
            {
                "name": "Vitalik Buterin",
                "role": "Ethereum",
                "event_ids": ["e1"],
                "confidence": 0.99,
            }
        ],
    }
    mock_llm, suggestions, event_links = _setup(
        events=events, seed_people=seed_people, llm_payload=payload
    )
    try:
        client = TestClient(app)
        resp = client.post(f"/api/admin/conferences/{CONF_ID}/generate-people")
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["ok"] is True
        assert body["events_considered"] == 2
        assert body["known_people_considered"] == 1
        assert body["new_people_created"] == 1
        assert body["associations_added"] == 2  # one for Stani+e2, one for Vitalik+e1
        assert body["rejected_hallucinations"] == 0
        assert body["tokens_used"] == 4242

        # Vitalik landed in conference_suggestions with source='llm'
        rows = suggestions.list_for_conference(CONF_ID)
        sources = {r["id"]: r["source"] for r in rows}
        assert sources["llm:vitalik-buterin"] == "llm"
        assert sources["luma:stani-kulechov"] == "luma"

        # Links written for both
        links = event_links.list_for_event("e1")
        assert any(
            link["suggestion_id"] == "llm:vitalik-buterin" and link["source"] == "llm"
            for link in links
        )
        links2 = event_links.list_for_event("e2")
        assert any(
            link["suggestion_id"] == "luma:stani-kulechov" and link["confidence"] == 0.95
            for link in links2
        )

        # LLM saw exactly the known person
        prompt = json.loads(mock_llm.calls[0][1])
        assert {p["id"] for p in prompt["known_people"]} == {"luma:stani-kulechov"}
        assert {e["id"] for e in prompt["events"]} == {"e1", "e2"}
    finally:
        _clear()


def test_generate_people_rejects_hallucinated_ids() -> None:
    events = [_event("real-1", "Real event", "x")]
    payload = {
        "associations": [
            {
                "suggestion_id": "luma:never-existed",
                "event_ids": ["real-1"],
                "confidence": 0.8,
            }
        ],
        "new_people": [
            {
                "name": "Ghost Person",
                "role": "x",
                "event_ids": ["FAKE-EVENT"],  # not in candidates → row dropped
                "confidence": 0.9,
            },
            {
                "name": "Real Person",
                "role": "x",
                "event_ids": ["real-1"],
                "confidence": 0.9,
            },
        ],
    }
    _, suggestions, event_links = _setup(events=events, llm_payload=payload)
    try:
        client = TestClient(app)
        resp = client.post(f"/api/admin/conferences/{CONF_ID}/generate-people")
        assert resp.status_code == 200
        body = resp.json()
        # association referenced unknown suggestion → rejected
        # ghost-person row had no valid event_ids → rejected
        assert body["rejected_hallucinations"] == 2
        assert body["new_people_created"] == 1
        assert body["associations_added"] == 1

        # Only "Real Person" persisted
        names = {r["name"] for r in suggestions.list_for_conference(CONF_ID)}
        assert names == {"Real Person"}
    finally:
        _clear()


def test_generate_people_unknown_conference_returns_404() -> None:
    _setup(events=[], llm_payload={"associations": [], "new_people": []})
    try:
        client = TestClient(app)
        resp = client.post("/api/admin/conferences/does-not-exist/generate-people")
        assert resp.status_code == 404
    finally:
        _clear()


def test_generate_people_with_no_events_skips_llm() -> None:
    mock_llm, _, _ = _setup(
        events=[], llm_payload={"associations": [], "new_people": []}
    )
    try:
        client = TestClient(app)
        resp = client.post(f"/api/admin/conferences/{CONF_ID}/generate-people")
        assert resp.status_code == 200
        body = resp.json()
        assert body["events_considered"] == 0
        assert "No events" in body["message"]
        # Never called the LLM
        assert mock_llm.calls == []
    finally:
        _clear()


def test_generate_people_is_idempotent_on_rerun() -> None:
    events = [_event("e1", "Foo", "Vitalik Buterin keynotes.")]
    payload = {
        "associations": [],
        "new_people": [
            {
                "name": "Vitalik Buterin",
                "role": "Ethereum",
                "event_ids": ["e1"],
                "confidence": 0.99,
            }
        ],
    }
    _, suggestions, event_links = _setup(events=events, llm_payload=payload)
    try:
        client = TestClient(app)
        first = client.post(f"/api/admin/conferences/{CONF_ID}/generate-people").json()
        second = client.post(f"/api/admin/conferences/{CONF_ID}/generate-people").json()
        # Both runs report "created 1" because the in-memory upsert returns the
        # id regardless of whether the row was new. What matters: no duplicate
        # rows landed.
        assert first["associations_added"] == 1
        assert second["associations_added"] == 1
        rows = suggestions.list_for_conference(CONF_ID)
        assert len([r for r in rows if r["id"] == "llm:vitalik-buterin"]) == 1
        links = event_links.list_for_event("e1")
        assert len(links) == 1
    finally:
        _clear()
