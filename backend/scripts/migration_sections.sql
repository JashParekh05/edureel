-- Section planning migration
-- Run this in Supabase SQL editor (Dashboard → SQL Editor)

-- 1. Ordered teaching sections per topic (hook → what-is-it → how-it-works → outcomes)
create table if not exists topic_sections (
  id uuid primary key default gen_random_uuid(),
  topic_slug text not null,
  section_index int not null,
  title text not null,
  description text not null,
  search_query text not null,
  created_at timestamptz default now(),
  unique (topic_slug, section_index)
);

-- 2. Track which section each clip belongs to
alter table clips
  add column if not exists section_index int;

-- 3. Index for fast section-scoped clip lookups
create index if not exists clips_section_idx
  on clips (topic_slug, section_index);
