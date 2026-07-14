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

Layered on top of those two pipelines:

- **Cold-start seeding** — a self-pacing cron worker grows the library ahead of
  demand from a persisted topic backlog, sharing a **multi-project** YouTube
  quota pool (per-project 10k/day, fail-closed accounting) so common queries
  serve instantly.
- **Deep content ingestion** — a DECODE → BREAK-DOWN → MAP → JUDGE → ADMIT
  pipeline that reasons about a source video before admitting clips, for
  higher-value topics.
- **Engagement telemetry** — captures *what was served* (impressions), not just
  what was watched, reconstructs per-session/per-user journeys, and rolls up
  engagement by slice. Cross-user reads are gated behind an operator allowlist.
- **Active-learning quiz** — per-topic MCQs with mastery tracking.

See **[docs/ARCHITECTURE.md](docs/ARCHITECTURE.md)** for the full ingestion/serving
pipeline, the three subsystems above, caching layers, ML detail, and a
**limitations & scaling roadmap**.

> **Design convention.** Every subsystem is a **pure decision core** (no DB,
> clock, or globals — Hypothesis property-tested) wrapped by a **thin best-effort
> I/O shell** that never blocks the request path and, where spend is involved,
> fails closed.

## Stack

| Layer | Tech |
|---|---|
| LLM (curriculum, segmentation, ranking) | OpenAI `gpt-4o-mini` |
| Transcripts | [TranscriptAPI.com](https://transcriptapi.com) |
| Video discovery | YouTube Data API v3 (multi-project quota pool via `YT_PROJECTS`) |
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

**Database** — run these against your Supabase project (SQL editor):

```bash
# Core schema: pgvector, tables, atomic interest/taste merge RPCs, HNSW index
scripts/migration_pgvector.sql
scripts/migration_sections.sql        # topic_sections table
scripts/migration_grade_level.sql     # user_profiles.grade_level
scripts/migration_cold_start.sql      # project_quota_usage + RPC, topic_backlog, clips.content_level
scripts/migration_telemetry.sql       # impressions table (engagement telemetry)
scripts/migration_clip_events_user.sql # clip_events.user_id (discover seen-history)
```

> **Required, not optional.** `migration_cold_start.sql` creates the
> `project_quota_usage` table and `increment_quota_usage` RPC. The YouTube quota
> charge site **fails closed**: if that table is missing, every search is treated
> as unaffordable and *no* clips are generated anywhere (learn page or Discover).
> If feeds hang on "processing" and logs show
> `Could not find the table 'public.project_quota_usage'`, this migration hasn't
> been run.

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

**Guest mode (required dashboard step):** the app signs new visitors in as
anonymous guests so they can watch with no login, then lets them upgrade in
place to a real account (same `user_id`, so their history carries over). Enable
this in **Supabase → Authentication → "Allow anonymous sign-ins"**. Without it,
`signInAnonymously()` returns a 422 and the app falls back to the sign-in screen.
For the smoothest upgrade, also disable email confirmation (Authentication →
Email) so `updateUser` makes the new account usable immediately.

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

### Offline seeding worker (cron)

The `Seeding_Worker` grows the cold-start library ahead of demand by draining the
persisted `topic_backlog` (Topic_Frontier) one paced chunk at a time. It is
self-pacing and resumable, and never overspends the per-project YouTube quota, so
it's safe to run on a schedule.

```bash
cd backend
python -m scripts.seeding_worker            # one paced pass, default cap (25 items)
python -m scripts.seeding_worker 10         # process at most 10 items this run
scripts/run_seeding_worker.sh               # cron wrapper: venv + lock + logging
```

**Production (Render):** `render.yaml` defines an `edureel-seeding-worker` cron
job that runs `python -m scripts.seeding_worker` every 6 hours using the same
Docker image as the web service. Apply via Render → Blueprints.

**Local / any server (crontab):** the wrapper resolves its own paths, prefers the
project venv, holds a single-instance lock, and logs to `backend/logs/`:

```cron
# every 6 hours
0 */6 * * * /Users/jbparekh/edureel/backend/scripts/run_seeding_worker.sh
```

> The worker shares the same 10,000 units/day quota pool as live learn/Discover
> traffic. Keep the cadence conservative (every few hours) so background seeding
> leaves quota headroom for interactive requests; a run that finds the budget
> spent stops cleanly having done nothing.

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
| `YOUTUBE_API_KEY` | YouTube Data API v3 ([console.cloud.google.com](https://console.cloud.google.com)) — legacy single key |
| `YT_PROJECTS` | Multi-project pool: comma-separated `project_id:api_key` pairs. Supersedes `YOUTUBE_API_KEY`. Each `project_id` is a stable label for one Google Cloud project (quota is per project, 10k/day) |
| `OPERATOR_USER_IDS` | Comma-separated Supabase auth user UUIDs (JWT `sub`) allowed to read cross-user telemetry. Empty = no operators |
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
| `/api/analytics/dropoff/{topic_slug}` | GET | Per-beat retention funnel for a topic |
| `/api/analytics/journey/session/{session_id}` | GET | Reconstructed session journey (operator/self) |
| `/api/analytics/journey/user/{user_id}` | GET | Cross-session user journey (operator/self) |
| `/api/analytics/rollup/{dimension}` | GET | Engagement rollup by slice (operator) |
| `/api/quiz/{topic_slug}` | GET | Cached MCQs for a topic (self-heals if missing) |
| `/api/quiz/{question_id}/answer` | POST | Record an answer, recompute mastery |
| `/api/quiz/mastery/{user_id}` | GET | Per-topic mastery summary + total points |
