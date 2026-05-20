-- Join table linking events to people/companies/speakers (conference_suggestions).
-- Three write sources land here:
--   'llm'    — POST /api/admin/conferences/{id}/generate-people
--   'manual' — admin pins someone to an event in the events UI (phase 2)
--   'luma'   — reserved for later, if the scraper starts persisting "host of
--              event X" links directly. Today the scraper only writes to
--              conference_suggestions, so no rows arrive with source='luma' yet.
--
-- Composite PK + ON CONFLICT (event_id, suggestion_id) DO UPDATE lets the LLM
-- pass re-run cleanly: re-associating the same person with the same event just
-- refreshes confidence / source instead of duplicating.

create table if not exists event_suggestions (
  event_id      text not null references events(id) on delete cascade,
  suggestion_id text not null references conference_suggestions(id) on delete cascade,
  source        text not null check (source in ('llm','manual','luma')),
  confidence    real,                                  -- 0..1 for LLM rows; null for manual
  created_at    timestamptz not null default now(),
  primary key (event_id, suggestion_id)
);

create index if not exists event_suggestions_event_idx
  on event_suggestions (event_id);
create index if not exists event_suggestions_suggestion_idx
  on event_suggestions (suggestion_id);

alter table event_suggestions enable row level security;

-- Public read so the onboarding UI can fetch "who's at this conference and
-- which events mention them" without auth. Writes go through the service-role
-- key from the admin endpoint.
create policy "public read event_suggestions"
  on event_suggestions for select using (true);
