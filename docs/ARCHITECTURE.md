# Curio — Architecture

Curio is a staged retrieval→ranking system, structured like a large-scale
recommender (Netflix/Apple-TV homepage style) but scaled down and made **lazy**:
content is encoded on-demand the first time a topic is requested, then cached so
it's never rebuilt.

There are two pipelines: an **ingestion** path ("get the video → encode it →
move on") and a **serving** path ("query → retrieve → rank → feed").

---

## Ingestion pipeline — "get the video → encode it → move on"

```
USER QUERY ──► "teach me binary search"
     │
     ▼
┌──────────────────┐   curriculum_agent.run_curriculum()
│  CURRICULUM AGENT │   LLM parses intent → ordered roadmap of ~3 topics
│  (orchestrator)   │   e.g. [binary-search, big-o-notation, recursion]
└─────────┬─────────┘
          │
          ▼
┌──────────────────┐   topic_resolver.resolve_topic()         ┌────────────────┐
│  TOPIC RESOLVER   │── match by name-embedding (≥ 0.84) ────► │  EXISTING CLIPS │ ✅ reuse, STOP
│  (similar-topic   │   "binary-search-basics" → binary-       │  (cache hit)    │    (no encode)
│   cache)          │    search (already seeded)               └────────────────┘
└─────────┬─────────┘
          │  miss → must build
          ▼
┌──────────────────┐   section_planner.plan_and_store_sections()
│  SECTION PLANNER  │   1 topic → 4 sequenced sections, each with its own
│                   │   search_query: hook → what-is-it → how-it-works → outcomes
└─────────┬─────────┘
          │   per section:
          ▼
╔═══════════ pipeline_agent  (LangGraph DAG = the "encode") ════════════════╗
║                                                                            ║
║  ┌──────────┐   ┌──────────────┐   ┌────────────┐   ┌──────────┐          ║
║  │ _node_   │   │  _node_      │   │  _node_    │   │ _node_   │          ║
║  │ search   │──►│  transcribe  │──►│  segment   │──►│ store    │          ║
║  │          │   │              │   │            │   │          │          ║
║  │ YouTube  │   │ TranscriptAPI│   │ GPT cuts   │   │ insert   │          ║
║  │ (CACHED) │   │  (CACHED     │   │ 2-3 clips +│   │ clips +  │          ║
║  │ 100 units│   │  by video_id)│   │ EMBED 384d │   │ embedding│          ║
║  └──────────┘   └──────────────┘   └────────────┘   └────┬─────┘          ║
║   = candidate    = fetch raw        = ENCODE          = land               ║
║     retrieval      transcript         (segment +        in DB              ║
║                                       vector)                              ║
╚════════════════════════════════════════════════════════════│═════════════╝
   "get video → encode → move on" runs HERE, in a background  │
   task; subsequent requests hit the cache and skip it.        ▼
                                                       ┌──────────────────┐
                                                       │   clips table    │
                                                       │ (Supabase+pgvec) │
                                                       └──────────────────┘
```

### Caching layers (so we encode once, then move on)
- **topic_resolver** — semantically-equivalent queries collapse onto an existing
  seeded topic (name-embedding cosine ≥ 0.84 **and** the topic already has clips).
  No YouTube search, no transcript fetch, no segmentation.
- **youtube_search_cache** — search results keyed by query string. A YouTube
  search costs 100 quota units (10k/day free); re-testing a topic is free.
- **transcript_cache** — transcripts keyed by `video_id`, so the same source
  video is never re-fetched from TranscriptAPI across sections or topics.

### In-flight tracking
`topics.generating_slugs` holds slugs whose pipeline is currently running. The
feed reports a topic as `processing` while its slug is in this set — so the
client keeps polling until **all** sections finish, not just until the first
clip lands. The feed also self-heals: an empty topic with no in-flight pipeline
gets its generation re-triggered on feed load.

---

## Serving pipeline — online, on every feed request

```
FEED REQUEST ──► get_path_feed(session)
     │
     ▼
┌────────────────────┐   _fetch_clips_for_slug()  per topic, sampled across sections
│  RETRIEVAL          │   pulls candidate clips from the DB
└─────────┬──────────┘
          ▼
┌────────────────────┐   _compute_scores()  — the learned ranker
│  MULTI-SIGNAL RANKER│   0.28 hook + 0.23 pop-completion + 0.18 duration-affinity
│  (within-row rank)  │   + 0.13 recency + 0.10 interest + 0.08 semantic (taste)
└─────────┬──────────┘
          ▼
┌────────────────────┐   _interleave_topics() + cross-topic dedup
│  ROW / DIVERSITY    │   orders topics and stops the same clip appearing under
│                     │   multiple topic feeds
└─────────┬──────────┘
          ▼
   List<Clip>  ──►  ReelPlayer (display)
          │
          ▼  user watches / 🔥 / ✓ / skips
┌────────────────────┐   record_clip_event → _update_interest_vector
│  TELEMETRY LOOP     │   updates session- and user-level interest + taste vectors,
│  (feedback→vectors) │   which feed back into the ranker above
└────────────────────┘
```

### Telemetry attribution
`record_clip_event` always personalizes for the authenticated user. Path-feed
events update both **session-level** (`session_embeddings`) and **user-level**
(`user_profiles`) vectors; topic-feed / discover events have no session but still
update the user's profile. Feedback (🔥 `want_more` / ✓ `already_know`) is
persisted on the `clip_events` row, not just applied as a live vector nudge.

---

## How this maps to a large-scale recommender

| Curio component | Large-scale analog |
|---|---|
| `curriculum_agent` (roadmap orchestration) | Orchestrator that fans out to candidate carousels |
| `topic_resolver` (reuse-or-build) | Candidate source + cache |
| `pipeline_agent` search → transcribe → segment → store | Candidate retrieval + offline **encode** (embeddings) |
| `_fetch_clips_for_slug` | Unified retrieval service (browse / history / continue) |
| `_compute_scores` | Within-row (carousel) ranker |
| `_interleave_topics` + dedup | Row ranker / diversity layer |
| `record_clip_event` → interest/taste vectors | Online feedback loop / experimentation |

### The key difference
In a large-scale system, "get content → encode" is a giant **offline batch job**
that ran long before any query. In Curio it's **lazy and on-demand**: the encode
(`_node_segment` = GPT cut + 384-d embedding) happens in a background task the
first time a topic is requested, then the caches mean we "move on" and never
re-encode it. We can't pre-encode the whole world the way Netflix/Apple can — so
we encode just-in-time and cache aggressively.
