"""Luma calendar scraper.

Library module — no CLI, no prints. Callers (admin trigger, future cron) feed
it a slug or URL and get back a list of normalized event dicts ready for
`EventsAdminRepo.scraper_upsert`.

Originally adapted from a standalone script; rewritten on top of `httpx`
to match the rest of the codebase and to get sane timeouts.
"""

from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

LUMA_API_BASE = "https://api.lu.ma"
LUMA_WEB_BASE = "https://lu.ma"

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=5.0, pool=5.0)
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "sidequest-scraper/0.1"
    ),
    "Accept": "application/json",
    "Referer": "https://lu.ma/",
}


# ---------- pure helpers ----------


def slug_from_url(url_or_slug: str) -> str:
    """Accept either a full Luma URL or a bare slug, return the slug.

    >>> slug_from_url("https://lu.ma/dc-blockchain-summit")
    'dc-blockchain-summit'
    >>> slug_from_url("lu.ma/dc-blockchain-summit/")
    'dc-blockchain-summit'
    >>> slug_from_url("dc-blockchain-summit")
    'dc-blockchain-summit'
    """
    value = url_or_slug.strip()
    if not value:
        raise ValueError("empty slug/url")

    # Bare slug — no slashes, no dots
    if "/" not in value and "." not in value:
        return value

    if "://" not in value:
        value = "https://" + value
    parsed = urlparse(value)

    host = (parsed.netloc or "").lower()
    if host and "lu.ma" not in host and "luma.com" not in host:
        raise ValueError(f"not a Luma URL: {url_or_slug!r}")

    # First non-empty path segment is the slug
    parts = [p for p in parsed.path.split("/") if p]
    if not parts:
        raise ValueError(f"no slug in URL: {url_or_slug!r}")
    return parts[0]


def extract_text_from_description(content: list[dict[str, Any]] | None) -> str:
    """Flatten Luma's ProseMirror-style description JSON into plain text."""
    if not content:
        return ""

    text_parts: list[str] = []

    for block in content:
        block_type = block.get("type", "")

        if block_type in ("paragraph", "heading"):
            line = _inline_text(block.get("content"))
            if line:
                text_parts.append(line)

        elif block_type == "bullet_list":
            for list_item in block.get("content", []) or []:
                for para in list_item.get("content", []) or []:
                    if para.get("type") == "paragraph":
                        line = _inline_text(para.get("content"))
                        if line:
                            text_parts.append(f"• {line}")

        elif block_type == "horizontal_rule":
            text_parts.append("---")

    return "\n".join(text_parts)


def _inline_text(nodes: list[dict[str, Any]] | None) -> str:
    if not nodes:
        return ""
    return "".join(n.get("text", "") for n in nodes if n.get("type") == "text")


def _extract_tags(details: dict[str, Any]) -> list[str]:
    """Pull tag slugs out of Luma's `categories` array on /event/get.

    Preserves order and dedupes case-insensitively, since admins later filter
    on these and a tags array with both 'crypto' and 'Crypto' is annoying.
    """
    categories = details.get("categories") or []
    seen: set[str] = set()
    out: list[str] = []
    for cat in categories:
        slug = (cat.get("slug") or "").strip().lower()
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)
    return out


def _resolve_venue(event: dict[str, Any]) -> str | None:
    """Pick the best human-readable venue for a Luma event.

    Many Luma events hide the full address until RSVP — geo_address_info
    then carries only city_state ("Milano, Italy"). Fall through:
      full_address → address → city_state → "Online" (virtual) → None
    so the schedule UI shows *something* instead of an empty cell.
    """
    geo = event.get("geo_address_info") or {}
    for key in ("full_address", "address", "city_state"):
        value = geo.get(key)
        if value:
            return value
    legacy = event.get("venue")
    if legacy:
        return legacy
    if (event.get("location_type") or "").lower() in ("virtual", "online", "zoom"):
        return "Online"
    return None


# ---------- scraper ----------


class LumaScraper:
    """Thin client for Luma's public read endpoints."""

    def __init__(
        self,
        *,
        client: httpx.Client | None = None,
        timeout: httpx.Timeout = DEFAULT_TIMEOUT,
    ) -> None:
        self._client = client or httpx.Client(
            base_url=LUMA_API_BASE,
            headers=DEFAULT_HEADERS,
            timeout=timeout,
            follow_redirects=True,
        )
        self._owns_client = client is None

    def __enter__(self) -> LumaScraper:
        return self

    def __exit__(self, *_: Any) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    # --- raw endpoints ---

    def get_calendar_info(self, slug: str) -> dict[str, Any]:
        resp = self._client.get("/url", params={"url": slug})
        resp.raise_for_status()
        return resp.json()

    def get_calendar_events(
        self,
        calendar_api_id: str,
        *,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "calendar_api_id": calendar_api_id,
            "pagination_limit": limit,
        }
        if cursor:
            params["pagination_cursor"] = cursor
        resp = self._client.get("/calendar/get-items", params=params)
        resp.raise_for_status()
        return resp.json()

    def get_event_details(self, event_api_id: str) -> dict[str, Any]:
        resp = self._client.get("/event/get", params={"event_api_id": event_api_id})
        resp.raise_for_status()
        return resp.json()

    # --- higher-level ---

    def scrape_calendar(
        self,
        slug_or_url: str,
        *,
        max_pages: int | None = None,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        """Walk all pages of a calendar and return the raw `entries`."""
        slug = slug_from_url(slug_or_url)
        logger.info("luma.scrape_calendar slug=%s", slug)

        info = self.get_calendar_info(slug)
        calendar = (info.get("data") or {}).get("calendar") or {}
        calendar_api_id = calendar.get("api_id")
        if not calendar_api_id:
            raise ValueError(f"no calendar_api_id for slug={slug!r}")

        all_entries: list[dict[str, Any]] = []
        cursor: str | None = None
        page = 0

        while True:
            page += 1
            data = self.get_calendar_events(
                calendar_api_id, cursor=cursor, limit=page_size
            )
            entries = data.get("entries") or []
            all_entries.extend(entries)

            logger.info(
                "luma.scrape_calendar slug=%s page=%d batch=%d total=%d",
                slug,
                page,
                len(entries),
                len(all_entries),
            )

            cursor = data.get("next_cursor")
            if not data.get("has_more") or not cursor:
                break
            if max_pages is not None and page >= max_pages:
                logger.info("luma.scrape_calendar slug=%s max_pages=%d reached", slug, max_pages)
                break

        return all_entries

    def fetch_all_details(
        self,
        entries: list[dict[str, Any]],
    ) -> dict[str, dict[str, Any]]:
        """Fetch /event/get for each entry; failures are logged + skipped."""
        out: dict[str, dict[str, Any]] = {}
        for entry in entries:
            api_id = (entry.get("event") or {}).get("api_id")
            if not api_id:
                continue
            try:
                out[api_id] = self.get_event_details(api_id)
            except httpx.HTTPError as exc:
                logger.warning("luma.detail_failed api_id=%s err=%s", api_id, exc)
        return out


# ---------- entry → normalized SideQuest event ----------


def normalize_event(
    entry: dict[str, Any],
    *,
    conference_id: str,
    source: str | None = None,
    details: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Map a Luma entry (+ optional details) to an `events` row.

    Returns None if the entry is missing required fields (id, name, timestamps).
    The id is deterministic — `luma:{api_id}` — so re-scrapes are idempotent
    via `EventsAdminRepo.scraper_upsert`.
    """
    event = entry.get("event") or {}
    api_id = event.get("api_id")
    title = event.get("name")
    starts_at = event.get("start_at")
    ends_at = event.get("end_at") or starts_at  # some Luma events lack end_at

    if not (api_id and title and starts_at):
        slug = event.get("url")
        url = f"{LUMA_WEB_BASE}/{slug}" if slug else None
        logger.warning(
            "luma.normalize skipped api_id=%s title=%s starts_at=%s url=%s",
            api_id, title, starts_at, url,
        )
        return None

    venue = _resolve_venue(event)

    url = f"{LUMA_WEB_BASE}/{event['url']}" if event.get("url") else None

    description: str | None = None
    capacity: int | None = None
    attendees: int | None = None
    tags: list[str] = []
    if details:
        desc_mirror = details.get("description_mirror") or {}
        description = extract_text_from_description(desc_mirror.get("content")) or None
        capacity = details.get("capacity")
        attendees = details.get("guest_count")
        tags = _extract_tags(details)

    return {
        "id": f"luma:{api_id}",
        "conference_id": conference_id,
        "title": title,
        "description": description,
        "starts_at": starts_at,
        "ends_at": ends_at,
        "venue": venue,
        "tags": tags,
        "url": url,
        "capacity": capacity,
        "attendees": attendees,
        "source": source or "luma",
        "raw": {"entry": entry, "details": details},
    }
