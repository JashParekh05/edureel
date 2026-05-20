-- pgvector migration
-- Run this in Supabase SQL editor (Dashboard → SQL Editor)

-- 1. Enable pgvector extension
create extension if not exists vector;

-- 2. Add embedding column to clips (384 dims for all-MiniLM-L6-v2)
alter table clips
  add column if not exists embedding vector(384);

-- 3. Add taste_vector column to session_embeddings
alter table session_embeddings
  add column if not exists taste_vector vector(384);

-- 4. HNSW index on clips.embedding for fast approximate nearest-neighbor search
create index if not exists clips_embedding_hnsw
  on clips
  using hnsw (embedding vector_cosine_ops)
  with (m = 16, ef_construction = 64);

-- 5. Atomic interest vector update (prevents concurrent-write clobber)
create or replace function merge_user_interest(p_user_id uuid, p_topic_slug text, p_delta float)
returns void language plpgsql as $$
declare
  current_iv jsonb;
  current_val float;
  new_val float;
begin
  select interest_vector into current_iv from user_profiles where user_id = p_user_id for update;
  if current_iv is null then current_iv := '{}'::jsonb; end if;
  current_val := coalesce((current_iv ->> p_topic_slug)::float, 0.0);
  new_val := greatest(-1.0, least(1.0, current_val + p_delta));
  current_iv := jsonb_set(current_iv, array[p_topic_slug], to_jsonb(round(new_val::numeric, 3)));
  insert into user_profiles (user_id, interest_vector) values (p_user_id, current_iv)
    on conflict (user_id) do update set interest_vector = excluded.interest_vector;
end; $$;

-- 6. Atomic taste vector EMA update (prevents concurrent-write clobber)
--    Requires pgvector >= 0.7 for vector arithmetic operators
create or replace function merge_user_taste(p_user_id uuid, p_new_taste vector(384), p_alpha float default 0.1)
returns void language plpgsql as $$
declare
  existing vector(384);
  merged vector(384);
begin
  select taste_vector into existing from user_profiles where user_id = p_user_id for update;
  if existing is null then
    merged := p_new_taste;
  else
    merged := (1 - p_alpha) * existing + p_alpha * p_new_taste;
  end if;
  insert into user_profiles (user_id, taste_vector) values (p_user_id, merged)
    on conflict (user_id) do update set taste_vector = excluded.taste_vector;
end; $$;

-- 7. Optional: match_clips RPC for direct vector search from the client
--    (not required — Python side does scoring, but useful for debugging)
create or replace function match_clips(
  query_embedding vector(384),
  match_count int default 10,
  filter_topic text default null
)
returns table (
  id text,
  topic_slug text,
  title text,
  similarity float
)
language sql stable
as $$
  select
    id,
    topic_slug,
    title,
    1 - (embedding <=> query_embedding) as similarity
  from clips
  where
    embedding is not null
    and (filter_topic is null or topic_slug = filter_topic)
  order by embedding <=> query_embedding
  limit match_count;
$$;
