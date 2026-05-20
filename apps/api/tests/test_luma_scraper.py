"""Slice 1 — Luma scraper unit tests (no live HTTP)."""

from __future__ import annotations

from typing import Any

import httpx
import pytest

from app.scraper.sources.luma import (
    LumaScraper,
    extract_text_from_description,
    normalize_event,
    slug_from_url,
)

# ---------- slug_from_url ----------


@pytest.mark.parametrize(
    "given, expected",
    [
        ("dc-blockchain-summit", "dc-blockchain-summit"),
        ("https://lu.ma/dc-blockchain-summit", "dc-blockchain-summit"),
        ("https://lu.ma/dc-blockchain-summit/", "dc-blockchain-summit"),
        ("http://lu.ma/dc-blockchain-summit?ref=x", "dc-blockchain-summit"),
        ("lu.ma/dc-blockchain-summit", "dc-blockchain-summit"),
        ("https://lu.ma/dc-blockchain-summit/some/sub", "dc-blockchain-summit"),
        ("https://luma.com/ethmilan2026", "ethmilan2026"),
        ("https://www.luma.com/ethmilan2026/", "ethmilan2026"),
    ],
)
def test_slug_from_url_happy(given: str, expected: str) -> None:
    assert slug_from_url(given) == expected


@pytest.mark.parametrize("given", ["", "   ", "https://example.com/foo", "https://lu.ma/", "https://luma.com/"])
def test_slug_from_url_rejects(given: str) -> None:
    with pytest.raises(ValueError):
        slug_from_url(given)


# ---------- extract_text_from_description ----------


def test_extract_description_empty() -> None:
    assert extract_text_from_description(None) == ""
    assert extract_text_from_description([]) == ""


def test_extract_description_mixed_blocks() -> None:
    content = [
        {
            "type": "heading",
            "content": [{"type": "text", "text": "Welcome"}],
        },
        {
            "type": "paragraph",
            "content": [
                {"type": "text", "text": "Join us at "},
                {"type": "text", "text": "Token2049"},
            ],
        },
        {
            "type": "bullet_list",
            "content": [
                {
                    "type": "list_item",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Coffee"}],
                        }
                    ],
                },
                {
                    "type": "list_item",
                    "content": [
                        {
                            "type": "paragraph",
                            "content": [{"type": "text", "text": "Talks"}],
                        }
                    ],
                },
            ],
        },
        {"type": "horizontal_rule"},
    ]
    out = extract_text_from_description(content)
    assert out.splitlines() == [
        "Welcome",
        "Join us at Token2049",
        "• Coffee",
        "• Talks",
        "---",
    ]


# ---------- normalize_event ----------


def _entry(**overrides: Any) -> dict[str, Any]:
    base = {
        "event": {
            "api_id": "evt_abc",
            "name": "Opening Keynote",
            "url": "opening-keynote",
            "start_at": "2026-10-01T09:00:00Z",
            "end_at": "2026-10-01T10:00:00Z",
            "timezone": "UTC",
            "geo_address_info": {
                "full_address": "Marina Bay Sands, Singapore",
                "city": "Singapore",
            },
        }
    }
    base["event"].update(overrides)
    return base


def test_normalize_event_minimal() -> None:
    row = normalize_event(_entry(), conference_id="token2049")
    assert row is not None
    assert row["id"] == "luma:evt_abc"
    assert row["conference_id"] == "token2049"
    assert row["title"] == "Opening Keynote"
    assert row["starts_at"] == "2026-10-01T09:00:00Z"
    assert row["ends_at"] == "2026-10-01T10:00:00Z"
    assert row["venue"] == "Marina Bay Sands, Singapore"
    assert row["url"] == "https://lu.ma/opening-keynote"
    assert row["tags"] == []
    assert row["source"] == "luma"
    assert row["description"] is None
    assert row["capacity"] is None
    assert row["raw"]["entry"]["event"]["api_id"] == "evt_abc"
    assert row["raw"]["details"] is None


def test_normalize_event_with_details() -> None:
    details = {
        "description_mirror": {
            "content": [
                {"type": "paragraph", "content": [{"type": "text", "text": "Hi"}]}
            ]
        },
        "capacity": 250,
        "guest_count": 180,
    }
    row = normalize_event(_entry(), conference_id="c1", details=details, source="luma-src-1")
    assert row is not None
    assert row["description"] == "Hi"
    assert row["capacity"] == 250
    assert row["attendees"] == 180
    assert row["source"] == "luma-src-1"


def test_normalize_event_missing_required_returns_none(caplog: pytest.LogCaptureFixture) -> None:
    bad = _entry()
    bad["event"]["start_at"] = None
    caplog.set_level("WARNING")
    assert normalize_event(bad, conference_id="c1") is None
    assert "skipped" in caplog.text


def test_normalize_event_maps_categories_to_tags() -> None:
    """Luma's /event/get returns categories[]; we lift the slugs into events.tags
    so the schedule UI can filter on them."""
    details = {
        "categories": [
            {"slug": "crypto", "name": "Crypto"},
            {"slug": "ai", "name": "AI"},
            # Duplicates with different cases should collapse.
            {"slug": "Crypto", "name": "Crypto"},
            {"slug": "", "name": "Empty"},
            {"name": "no-slug"},
        ]
    }
    row = normalize_event(_entry(), conference_id="c1", details=details)
    assert row is not None
    assert row["tags"] == ["crypto", "ai"]


def test_normalize_event_tags_empty_when_no_categories() -> None:
    row = normalize_event(_entry(), conference_id="c1", details={"categories": []})
    assert row is not None
    assert row["tags"] == []


def test_normalize_event_venue_falls_back_to_city_state_when_address_hidden() -> None:
    """Many Luma events expose only city_state until the user RSVPs. The old
    code returned None; we now return 'Milano, Italy' so the UI has something
    to show."""
    e = _entry()
    e["event"]["geo_address_info"] = {"city_state": "Milano, Italy"}
    row = normalize_event(e, conference_id="c1")
    assert row is not None
    assert row["venue"] == "Milano, Italy"


def test_normalize_event_venue_falls_back_to_online_for_virtual_events() -> None:
    e = _entry()
    e["event"]["geo_address_info"] = {}
    e["event"]["location_type"] = "virtual"
    row = normalize_event(e, conference_id="c1")
    assert row is not None
    assert row["venue"] == "Online"


def test_normalize_event_venue_none_when_nothing_resolves() -> None:
    e = _entry()
    e["event"]["geo_address_info"] = {}
    e["event"].pop("venue", None)
    row = normalize_event(e, conference_id="c1")
    assert row is not None
    assert row["venue"] is None


def test_normalize_event_no_end_at_falls_back_to_start() -> None:
    e = _entry()
    e["event"]["end_at"] = None
    row = normalize_event(e, conference_id="c1")
    assert row is not None
    assert row["ends_at"] == row["starts_at"]


# ---------- LumaScraper with mocked transport ----------


def _build_scraper(handler) -> LumaScraper:
    transport = httpx.MockTransport(handler)
    client = httpx.Client(
        base_url="https://api.lu.ma",
        transport=transport,
    )
    return LumaScraper(client=client)


def test_scrape_calendar_paginates() -> None:
    pages = {
        None: {
            "entries": [
                {"event": {"api_id": "e1", "name": "A", "start_at": "2026-10-01T09:00:00Z"}},
                {"event": {"api_id": "e2", "name": "B", "start_at": "2026-10-01T10:00:00Z"}},
            ],
            "has_more": True,
            "next_cursor": "cur2",
        },
        "cur2": {
            "entries": [
                {"event": {"api_id": "e3", "name": "C", "start_at": "2026-10-01T11:00:00Z"}}
            ],
            "has_more": False,
            "next_cursor": None,
        },
    }

    def handler(req: httpx.Request) -> httpx.Response:
        if req.url.path == "/url":
            return httpx.Response(200, json={"data": {"calendar": {"api_id": "cal_xyz"}}})
        if req.url.path == "/calendar/get-items":
            cursor = req.url.params.get("pagination_cursor")
            return httpx.Response(200, json=pages[cursor])
        return httpx.Response(404)

    with _build_scraper(handler) as s:
        entries = s.scrape_calendar("https://lu.ma/token2049")

    assert [e["event"]["api_id"] for e in entries] == ["e1", "e2", "e3"]


def test_scrape_calendar_raises_when_no_calendar_id() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"calendar": {}}})

    with _build_scraper(handler) as s:
        with pytest.raises(ValueError, match="no calendar_api_id"):
            s.scrape_calendar("token2049")
