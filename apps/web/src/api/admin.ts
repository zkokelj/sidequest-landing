import { apiFetch } from '../lib/fetcher'

export type AdminEvent = {
  id: string
  conference_id: string
  title: string
  description: string | null
  starts_at: string
  ends_at: string
  venue: string | null
  tags: string[]
  url: string | null
  capacity: number | null
  attendees: number | null
  is_manual: boolean
  locked: boolean
  updated_by: string | null
  updated_at: string | null
  created_at: string | null
}

export type AdminEventCreate = {
  id: string
  conference_id: string
  title: string
  description?: string | null
  starts_at: string
  ends_at: string
  venue?: string | null
  tags?: string[]
  url?: string | null
  capacity?: number | null
  attendees?: number | null
}

export type AdminEventUpdate = Partial<Omit<AdminEventCreate, 'id'>>

export type AdminConferenceDay = {
  day_num: number
  dow: string
  date?: string | null
  enabled: boolean
}

export type AdminConferenceUpsert = {
  id: string
  name: string
  city?: string | null
  venue?: string | null
  start_date?: string | null
  end_date?: string | null
  timezone?: string | null
  is_active?: boolean
  meta?: Record<string, unknown>
  days?: AdminConferenceDay[]
}

export type ConferenceFromApi = {
  id: string
  name: string
  city: string | null
  venue: string | null
  start_date: string | null
  end_date: string | null
  timezone: string | null
  is_active: boolean
  meta: Record<string, unknown>
  days: { num: number; dow: string; date: string | null; enabled: boolean }[]
}

/** Public — only active conferences. Used for the user-facing picker. */
export function listConferences(): Promise<ConferenceFromApi[]> {
  return apiFetch<ConferenceFromApi[]>('/api/conferences')
}

/** Admin — all conferences including inactive ones. Used by the admin panel. */
export function listAllConferences(): Promise<ConferenceFromApi[]> {
  return apiFetch<ConferenceFromApi[]>('/api/admin/conferences')
}

export function getConference(id: string): Promise<ConferenceFromApi> {
  return apiFetch<ConferenceFromApi>(`/api/conferences/${encodeURIComponent(id)}`)
}

// Public — what the onboarding "mustHaves" step shows. No auth.
export type PublicSuggestion = {
  id: string
  conference_id: string
  kind: 'people' | 'companies' | 'speakers' | string
  name: string
  role: string | null
}

export function listPublicConferenceSuggestions(
  conferenceId: string,
  opts: { kind?: 'people' | 'companies' | 'speakers' } = {},
): Promise<PublicSuggestion[]> {
  const qs = opts.kind ? `?kind=${encodeURIComponent(opts.kind)}` : ''
  return apiFetch<PublicSuggestion[]>(
    `/api/conferences/${encodeURIComponent(conferenceId)}/suggestions${qs}`,
  )
}

// ---------- scrape sources ----------

export type ScrapeSource = {
  id: string
  conference_id: string
  source_type: string
  url: string
  enabled: boolean
  last_scraped_at: string | null
  last_status: string | null
  last_error: string | null
  events_added: number
  events_updated: number
  scrape_interval_minutes: number | null
  created_at: string | null
  updated_at: string | null
}

export type FailedEvent = {
  api_id: string | null
  reason: string
  detail: string | null
  url: string | null
  title: string | null
}

export type ScrapeRunResult = {
  ok: boolean
  message: string
  sources_attempted: number
  sources_failed: number
  events_added: number
  events_updated: number
  events_failed: number
  failed_events: FailedEvent[]
}

export function listScrapeSources(conferenceId: string): Promise<ScrapeSource[]> {
  return apiFetch<ScrapeSource[]>(
    `/api/admin/conferences/${encodeURIComponent(conferenceId)}/sources`,
  )
}

export function addScrapeSource(
  conferenceId: string,
  body: { url: string; source_type?: string; enabled?: boolean },
): Promise<ScrapeSource> {
  return apiFetch<ScrapeSource>(
    `/api/admin/conferences/${encodeURIComponent(conferenceId)}/sources`,
    { method: 'POST', body: JSON.stringify(body) },
  )
}

export function updateScrapeSource(
  sourceId: string,
  patch: { url?: string; enabled?: boolean; scrape_interval_minutes?: number | null },
): Promise<ScrapeSource> {
  return apiFetch<ScrapeSource>(`/api/admin/sources/${encodeURIComponent(sourceId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export function deleteScrapeSource(sourceId: string): Promise<void> {
  return apiFetch<void>(`/api/admin/sources/${encodeURIComponent(sourceId)}`, {
    method: 'DELETE',
  })
}

export function triggerScrape(conferenceId: string): Promise<ScrapeRunResult> {
  return apiFetch<ScrapeRunResult>(
    `/api/admin/conferences/${encodeURIComponent(conferenceId)}/scrape`,
    { method: 'POST' },
  )
}

// ---------- LLM people generation + event-people management ----------

export type GeneratePeopleResult = {
  ok: boolean
  message: string
  events_considered: number
  known_people_considered: number
  new_people_created: number
  associations_added: number
  rejected_hallucinations: number
  tokens_used: number
  model: string | null
  errors: string[]
}

export type AdminSuggestion = {
  id: string
  conference_id: string
  kind: string
  name: string
  role: string | null
  source: string | null
}

export type EventPerson = {
  suggestion_id: string
  name: string
  role: string | null
  person_source: string | null
  link_source: string  // 'llm' | 'manual' | 'luma'
  confidence: number | null
}

export function generateConferencePeople(
  conferenceId: string,
): Promise<GeneratePeopleResult> {
  return apiFetch<GeneratePeopleResult>(
    `/api/admin/conferences/${encodeURIComponent(conferenceId)}/generate-people`,
    { method: 'POST' },
  )
}

export function listConferenceSuggestions(
  conferenceId: string,
  opts: { kind?: 'people' | 'companies' | 'speakers' } = {},
): Promise<AdminSuggestion[]> {
  const qs = opts.kind ? `?kind=${encodeURIComponent(opts.kind)}` : ''
  return apiFetch<AdminSuggestion[]>(
    `/api/admin/conferences/${encodeURIComponent(conferenceId)}/suggestions${qs}`,
  )
}

export function listEventPeople(eventId: string): Promise<EventPerson[]> {
  return apiFetch<EventPerson[]>(
    `/api/admin/events/${encodeURIComponent(eventId)}/people`,
  )
}

export function attachEventPerson(
  eventId: string,
  body: { suggestion_id?: string; name?: string; role?: string | null },
): Promise<EventPerson> {
  return apiFetch<EventPerson>(
    `/api/admin/events/${encodeURIComponent(eventId)}/people`,
    { method: 'POST', body: JSON.stringify(body) },
  )
}

export function detachEventPerson(
  eventId: string,
  suggestionId: string,
): Promise<void> {
  return apiFetch<void>(
    `/api/admin/events/${encodeURIComponent(eventId)}/people/${encodeURIComponent(suggestionId)}`,
    { method: 'DELETE' },
  )
}

// ---------- scheduler ----------

export type SchedulerSettings = {
  enabled: boolean
  tick_seconds: number
}

export function getSchedulerSettings(): Promise<SchedulerSettings> {
  return apiFetch<SchedulerSettings>('/api/admin/scheduler')
}

export function setSchedulerEnabled(enabled: boolean): Promise<SchedulerSettings> {
  return apiFetch<SchedulerSettings>('/api/admin/scheduler', {
    method: 'PUT',
    body: JSON.stringify({ enabled }),
  })
}

// ---------- events ----------

export function listAdminEvents(params: {
  conference_id?: string
  locked?: boolean
  is_manual?: boolean
}): Promise<AdminEvent[]> {
  const qs = new URLSearchParams()
  if (params.conference_id) qs.set('conference_id', params.conference_id)
  if (params.locked !== undefined) qs.set('locked', String(params.locked))
  if (params.is_manual !== undefined) qs.set('is_manual', String(params.is_manual))
  const suffix = qs.toString() ? `?${qs}` : ''
  return apiFetch<AdminEvent[]>(`/api/admin/events${suffix}`)
}

export function createAdminEvent(body: AdminEventCreate): Promise<AdminEvent> {
  return apiFetch<AdminEvent>('/api/admin/events', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

export function updateAdminEvent(id: string, patch: AdminEventUpdate): Promise<AdminEvent> {
  return apiFetch<AdminEvent>(`/api/admin/events/${encodeURIComponent(id)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  })
}

export function deleteAdminEvent(id: string): Promise<void> {
  return apiFetch<void>(`/api/admin/events/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  })
}

export type BulkDeleteEventsResult = {
  deleted: number
  skipped_locked: number
}

export function deleteAllConferenceEvents(
  conferenceId: string,
  opts: { includeLocked?: boolean } = {},
): Promise<BulkDeleteEventsResult> {
  const qs = opts.includeLocked ? '?include_locked=true' : ''
  return apiFetch<BulkDeleteEventsResult>(
    `/api/admin/conferences/${encodeURIComponent(conferenceId)}/events${qs}`,
    { method: 'DELETE' },
  )
}

export function setAdminEventLock(id: string, locked: boolean): Promise<AdminEvent> {
  return apiFetch<AdminEvent>(`/api/admin/events/${encodeURIComponent(id)}/lock`, {
    method: 'POST',
    body: JSON.stringify({ locked }),
  })
}

// ---------- bulk import ----------

export type BulkEventInput = {
  id?: string | null
  title: string
  description?: string | null
  starts_at: string
  ends_at: string
  venue?: string | null
  tags?: string[]
  url?: string | null
  capacity?: number | null
  attendees?: number | null
}

export type BulkEventsImportRequest = {
  conference_id: string
  on_conflict?: 'upsert' | 'skip'
  events: BulkEventInput[]
}

export type BulkImportError = {
  index: number
  id: string | null
  message: string
}

export type BulkEventsImportResponse = {
  dry_run: boolean
  inserted: number
  updated: number
  skipped_locked: number
  skipped_conflict: number
  errors: BulkImportError[]
}

export function bulkImportEvents(
  body: BulkEventsImportRequest,
  opts: { dryRun?: boolean } = {},
): Promise<BulkEventsImportResponse> {
  const qs = opts.dryRun ? '?dry_run=true' : ''
  return apiFetch<BulkEventsImportResponse>(`/api/admin/events/bulk${qs}`, {
    method: 'POST',
    body: JSON.stringify(body),
  })
}

// ---------- conferences ----------

export function upsertAdminConference(body: AdminConferenceUpsert) {
  return apiFetch<Record<string, unknown>>('/api/admin/conferences', {
    method: 'POST',
    body: JSON.stringify(body),
  })
}
