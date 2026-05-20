-- Distinguish hand-curated suggestion rows from auto-discovered ones, so the
-- Luma scraper can land people it sees in event hosts/featured_guests without
-- the admin UI losing track of which rows it owns.
--
-- Re-scrape idempotence comes from the deterministic id slug
-- (`luma:<ascii-slug>`) — PK collision drops duplicates. Seed rows use bare
-- ids (`stani`, `vitalik`, ...) so they coexist in parallel with any scraped
-- row for the same human; future work can add a display-time dedup join.

alter table conference_suggestions
  add column if not exists source text not null default 'seed';
