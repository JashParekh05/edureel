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

-- 5. Optional: match_clips RPC for direct vector search from the client
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
