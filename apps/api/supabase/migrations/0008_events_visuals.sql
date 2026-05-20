-- Add visual fields to events so scraped Luma rows can carry the cover image
-- and tint color into the schedule UI. Nullable — existing seed events stay
-- unchanged.

alter table events
  add column if not exists cover_url text,
  add column if not exists tint_color text;
