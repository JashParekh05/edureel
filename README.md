# Curio

Educational short-form video reels, assembled on-demand from your learning goals.
Type what you want to learn and Curio builds an ordered micro-course, cutting the
best 45–90 second explanations out of YouTube videos into a TikTok-style feed.

## How it works

1. **User query** → "I want to learn hashmaps and dynamic programming".
2. **Curriculum agent** (LangGraph + OpenAI `gpt-4o-mini`) parses intent and builds an
   ordered roadmap of topics with prerequisites.
3. **Similar-topic resolver** matches each topic against already-seeded topics by name
   embedding — a hit reuses cached clips instead of regenerating (saves quota + latency).
4. For each new topic, a **section planner** splits it into 4 sequenced sections
   (hook → what-is-it → how-it-works → outcomes), each with its own search query.
5. The **pipeline** searches YouTube (Data API v3), fetches transcripts (TranscriptAPI.com),
   and uses the LLM to cut 2–3 hook-optimized clips per section. Clips are
   **start/end timestamps into a YouTube embed** — no video download, cutting, or hosting.
6. Each clip is embedded (`sentence-transformers`, 384-dim) and stored in Supabase + pgvector.
7. The **feed** ranks clips with a multi-signal scorer and serves them; user behavior
   (watch time, 🔥/✓, skips) updates per-session and per-user preference vectors online.

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the full ingestion/serving
pipeline, caching layers, and ML detail.

## Stack

| Layer | Tech |
|---|---|
| LLM (curriculum, segmentation, ranking) | OpenAI `gpt-4o-mini` |
| Transcripts | [TranscriptAPI.com](https://transcriptapi.com) |
| Video discovery | YouTube Data API v3 |
| Clips | YouTube embeds with `start`/`end` timestamps (no download/hosting) |
| Embeddings | `sentence-transformers/all-MiniLM-L6-v2` (384-dim, local) |
| Agents | LangGraph (curriculum, pipeline, recommendation) |
| Background work | FastAPI `BackgroundTasks` |
| Database + vectors | Supabase (Postgres + pgvector, HNSW) |
| Auth | Supabase Auth (JWT via JWKS) |
| Backend | FastAPI (deployed on Render) |
| Frontend | Next.js 15 + Tailwind (deployed on Vercel) |

## Setup

### Backend

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # fill in your keys
uvicorn app.main:app --reload --port 8000
```

API docs at http://localhost:8000/docs

**Database** — run these against your Supabase project (SQL editor):

```bash
# Core schema: pgvector, tables, atomic interest/taste merge RPCs, HNSW index
scripts/migration_pgvector.sql
scripts/migration_sections.sql        # topic_sections table
scripts/migration_grade_level.sql     # user_profiles.grade_level
```

Plus the cache tables + feedback column:

```sql
create table if not exists transcript_cache (
  video_id text primary key, segments jsonb not null, created_at timestamptz default now()
);
create table if not exists youtube_search_cache (
  query text primary key, videos jsonb not null, created_at timestamptz default now()
);
alter table clip_events add column if not exists feedback text;
```

### Frontend

```bash
cd frontend
npm install
# .env.local:
#   NEXT_PUBLIC_API_URL=http://localhost:8000      (or your Render URL)
#   NEXT_PUBLIC_SUPABASE_URL=...
#   NEXT_PUBLIC_SUPABASE_ANON_KEY=...
npm run dev
```

Frontend at http://localhost:3000

### Seeding content (optional)

Pre-seed topics so common queries serve instantly (the resolver routes similar
queries onto them):

```bash
cd backend
python -m scripts.seed_clips                          # seed curated_topics.json
python -m scripts.seed_clips binary-search hashmaps    # specific topics
python -m scripts.bulk_seed                           # bulk-seed from a CSV of (slug, url)
python -m scripts.backfill_embeddings                 # embed any clips missing a vector
```

### Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest
```

Covers the deterministic logic: scoring, ordering, the grade map, segmentation
bounding, and vector math. DB-dependent endpoints aren't covered (no Supabase mock).

## Environment variables

**Backend** (`backend/.env`):

| Key | Where |
|---|---|
| `OPENAI_API_KEY` | [platform.openai.com](https://platform.openai.com) |
| `TRANSCRIPT_API_KEY` | [transcriptapi.com](https://transcriptapi.com) |
| `YOUTUBE_API_KEY` | YouTube Data API v3 ([console.cloud.google.com](https://console.cloud.google.com)) |
| `SUPABASE_URL` + `SUPABASE_KEY` | Supabase → Settings → API (use the **secret** key, not publishable) |
| `ALLOWED_ORIGINS` | comma-separated CORS origins (e.g. your Vercel URL) |

**Frontend** (`frontend/.env.local`): `NEXT_PUBLIC_API_URL`,
`NEXT_PUBLIC_SUPABASE_URL`, `NEXT_PUBLIC_SUPABASE_ANON_KEY`.

> Note: YouTube Data API has a free quota of 10,000 units/day (each search = 100 units).
> The `youtube_search_cache` and `transcript_cache` tables keep re-runs free.

## API

| Endpoint | Method | Description |
|---|---|---|
| `/api/topics/` | POST | Parse query → build + return learning path |
| `/api/topics/{slug}/sections` | GET | Section plan for a topic |
| `/api/topics/history/{user_id}` | GET | A user's recent learning paths |
| `/api/feed/path/{session_id}` | GET | Full curriculum feed (multi-topic, ranked) |
| `/api/feed/recommendations/{session_id}` | GET | Suggested next topics |
| `/api/feed/{topic_slug}` | GET | Clips for a single topic |
| `/api/feed/discover/{user_id}` | GET | Personalized discover feed |
| `/api/feed/{clip_id}/events` | POST | Record watch/feedback telemetry |
| `/api/users/{user_id}/profile` | GET | User profile (interests, grade, onboarding) |
| `/api/users/{user_id}/interests` | POST | Save onboarding interests |
