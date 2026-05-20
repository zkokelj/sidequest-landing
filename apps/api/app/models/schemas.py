from __future__ import annotations

from datetime import date as date_t, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class ConferenceDayOut(BaseModel):
    day_num: int = Field(..., serialization_alias="num")
    dow: str
    date: date_t | None = None
    enabled: bool

    model_config = ConfigDict(populate_by_name=True)


class ConferenceOut(BaseModel):
    id: str
    name: str
    city: str | None = None
    venue: str | None = None
    start_date: date_t | None = None
    end_date: date_t | None = None
    timezone: str | None = None
    is_active: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)
    days: list[ConferenceDayOut] = Field(default_factory=list)


class EventOut(BaseModel):
    """Matches the frontend Event type in apps/web/src/types/index.ts."""

    id: str
    conference_id: str
    title: str
    description: str | None = None
    start: datetime
    end: datetime
    venue: str | None = None
    tags: list[str] = Field(default_factory=list)
    url: str | None = None
    capacity: int | None = None
    attendees: int | None = None
    cover_url: str | None = None
    tint_color: str | None = None


class SuggestionOut(BaseModel):
    id: str
    conference_id: str
    kind: str
    name: str
    role: str | None = None


# ============================================================================
# Onboarding + curation — mirrors apps/web/src/stores/onboardingStore.ts
# ============================================================================


class OnboardingState(BaseModel):
    """Exact mirror of the frontend OnboardingState. Field names match the TS."""

    conferenceId: str
    attendance: str | None = None  # 'full' | 'partial' | 'side-only'
    days: list[int] = Field(default_factory=list)
    role: str | None = None
    goals: list[str] = Field(default_factory=list)
    topics: list[str] = Field(default_factory=list)
    pace: int = 50
    energy: int = 50
    social: int = 50
    mustHaves: list[str] = Field(default_factory=list)


class CuratedItem(BaseModel):
    event_id: str
    day: str
    start: str
    end: str
    rationale: str
    priority: str  # 'must' | 'should' | 'maybe'


class CurateRequest(BaseModel):
    onboarding: OnboardingState
    anon_id: str  # client-generated UUID v4
    model: str | None = None


class CurateResponse(BaseModel):
    curate_id: str
    schedule: list[CuratedItem]
    tokens_used: int


# ============================================================================
# Auth / unlock / schedule
# ============================================================================


class ClaimRequest(BaseModel):
    anon_id: str


class ClaimResponse(BaseModel):
    ok: bool = True
    user_curation_id: str


class UnlockRequest(BaseModel):
    conference_id: str


class UnlockResponse(BaseModel):
    ok: bool = True
    unlocked: bool


class EntitlementRead(BaseModel):
    unlocked: bool
    conference_id: str
    provider: str | None = None


# ============================================================================
# Checkout (Polar)
# ============================================================================


class CheckoutCreateRequest(BaseModel):
    anon_id: str
    conference_id: str


class CheckoutCreateResponse(BaseModel):
    checkout_url: str


class ScheduleItem(BaseModel):
    """Enriched event with curation overlay — what /api/me/schedule returns."""

    # Event fields (subset matching EventOut)
    id: str
    conference_id: str
    title: str
    description: str | None = None
    start: datetime
    end: datetime
    venue: str | None = None
    tags: list[str] = Field(default_factory=list)
    url: str | None = None
    capacity: int | None = None
    attendees: int | None = None
    cover_url: str | None = None
    tint_color: str | None = None
    # Curation overlay
    rationale: str
    priority: str  # 'must' | 'should' | 'maybe'
    inSchedule: bool = True


class ScheduleResponse(BaseModel):
    conference_id: str | None
    schedule: list[ScheduleItem]


# ============================================================================
# Pins
# ============================================================================


class PinRequest(BaseModel):
    event_id: str
    pinned: bool


class PinResponse(BaseModel):
    ok: bool = True
    event_id: str
    pinned: bool


# ============================================================================
# Admin
# ============================================================================


class AdminEventCreate(BaseModel):
    id: str
    conference_id: str
    title: str
    description: str | None = None
    starts_at: datetime
    ends_at: datetime
    venue: str | None = None
    tags: list[str] = Field(default_factory=list)
    url: str | None = None
    capacity: int | None = None
    attendees: int | None = None
    cover_url: str | None = None
    tint_color: str | None = None


class AdminEventUpdate(BaseModel):
    conference_id: str | None = None
    title: str | None = None
    description: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None
    venue: str | None = None
    tags: list[str] | None = None
    url: str | None = None
    capacity: int | None = None
    attendees: int | None = None
    cover_url: str | None = None
    tint_color: str | None = None


class AdminEventOut(BaseModel):
    id: str
    conference_id: str
    title: str
    description: str | None = None
    starts_at: datetime
    ends_at: datetime
    venue: str | None = None
    tags: list[str] = Field(default_factory=list)
    url: str | None = None
    capacity: int | None = None
    attendees: int | None = None
    cover_url: str | None = None
    tint_color: str | None = None
    is_manual: bool
    locked: bool
    updated_by: str | None = None
    updated_at: datetime | None = None
    created_at: datetime | None = None


class LockRequest(BaseModel):
    locked: bool


class BulkEventInput(BaseModel):
    """One event in a bulk-import payload. `id` is optional — when omitted the
    server derives a stable hash from `title + starts_at` and prefixes with
    `import:<conference_id>:`, so re-importing the same JSON updates rather
    than duplicates."""

    id: str | None = None
    title: str
    description: str | None = None
    starts_at: datetime
    ends_at: datetime
    venue: str | None = None
    tags: list[str] = Field(default_factory=list)
    url: str | None = None
    capacity: int | None = None
    attendees: int | None = None
    cover_url: str | None = None
    tint_color: str | None = None


class BulkEventsImportRequest(BaseModel):
    conference_id: str
    on_conflict: Literal["upsert", "skip"] = "upsert"
    events: list[BulkEventInput] = Field(default_factory=list, max_length=500)


class BulkImportError(BaseModel):
    index: int
    id: str | None = None
    message: str


class BulkEventsImportResponse(BaseModel):
    dry_run: bool
    inserted: int
    updated: int
    skipped_locked: int
    skipped_conflict: int
    errors: list[BulkImportError] = Field(default_factory=list)


class BulkDeleteEventsResult(BaseModel):
    """Returned by DELETE /api/admin/conferences/{id}/events."""

    deleted: int
    skipped_locked: int


class AdminConferenceDay(BaseModel):
    day_num: int
    dow: str
    date: date_t | None = None
    enabled: bool = True


class AdminConferenceUpsert(BaseModel):
    id: str
    name: str
    city: str | None = None
    venue: str | None = None
    start_date: date_t | None = None
    end_date: date_t | None = None
    timezone: str | None = None
    is_active: bool = True
    meta: dict[str, Any] = Field(default_factory=dict)
    days: list[AdminConferenceDay] | None = None


# ============================================================================
# Scrape sources
# ============================================================================


class ScrapeSourceCreate(BaseModel):
    url: str
    source_type: str = "luma"
    enabled: bool = True
    scrape_interval_minutes: int | None = Field(default=None, ge=1, le=10080)


class ScrapeSourceUpdate(BaseModel):
    url: str | None = None
    enabled: bool | None = None
    scrape_interval_minutes: int | None = Field(default=None, ge=1, le=10080)


class ScrapeSourceOut(BaseModel):
    id: str
    conference_id: str
    source_type: str
    url: str
    enabled: bool
    last_scraped_at: datetime | None = None
    last_status: str | None = None
    last_error: str | None = None
    events_added: int = 0
    events_updated: int = 0
    scrape_interval_minutes: int | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class FailedEventOut(BaseModel):
    """A single entry from a scraped source that couldn't be persisted.

    `url` and `title` are best-effort — present whenever Luma returned
    them in the malformed entry, so admins can click through and
    recreate the event manually.
    """

    api_id: str | None = None
    reason: str  # 'missing_required' | 'exception'
    detail: str | None = None
    url: str | None = None
    title: str | None = None


class SchedulerSettingsOut(BaseModel):
    """Returned by GET/PUT /api/admin/scheduler."""

    enabled: bool
    tick_seconds: int


class SchedulerSettingsUpdate(BaseModel):
    enabled: bool


class AdminSuggestionOut(BaseModel):
    """Returned by GET /api/admin/conferences/{id}/suggestions and by the
    event-people endpoints. Wider than SuggestionOut because admin tools want
    `source` to distinguish seed/luma/llm/manual rows."""

    id: str
    conference_id: str
    kind: str
    name: str
    role: str | None = None
    source: str | None = None


class EventPersonOut(BaseModel):
    """One person attached to one event, joined for display."""

    suggestion_id: str
    name: str
    role: str | None = None
    person_source: str | None = None  # source on conference_suggestions
    link_source: str                  # source on event_suggestions ('llm' | 'manual' | 'luma')
    confidence: float | None = None


class AdminSuggestionPatch(BaseModel):
    """PATCH /api/admin/suggestions/{id}. Both fields optional.

    `name` cannot be empty — server rejects whitespace-only.
    `role` accepts empty string to clear the field, or null/omitted to leave unchanged.
    """

    name: str | None = None
    role: str | None = None


class BulkDeleteSuggestionsResult(BaseModel):
    deleted: int


class EventPersonAttach(BaseModel):
    """Either attach an existing person by id, or create a new manual person
    from name+role and attach in one shot. Exactly one of the two must be set."""

    suggestion_id: str | None = None
    name: str | None = None
    role: str | None = None


class GeneratePeopleResult(BaseModel):
    """Returned by POST /api/admin/conferences/{id}/generate-people."""

    ok: bool
    message: str
    events_considered: int = 0
    known_people_considered: int = 0
    new_people_created: int = 0
    associations_added: int = 0
    rejected_hallucinations: int = 0
    tokens_used: int = 0
    model: str | None = None
    errors: list[str] = Field(default_factory=list)


class ScrapeRunResult(BaseModel):
    """Returned by POST /api/admin/conferences/{id}/scrape."""

    ok: bool
    message: str
    sources_attempted: int = 0
    sources_failed: int = 0
    events_added: int = 0
    events_updated: int = 0
    events_failed: int = 0
    failed_events: list[FailedEventOut] = []


# ============================================================================
# Chat
# ============================================================================


class ChatMessage(BaseModel):
    role: str  # 'user' | 'assistant' | 'system'
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: str | None = None
    conference_id: str | None = None
