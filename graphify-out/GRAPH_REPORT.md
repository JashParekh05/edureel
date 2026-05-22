# Graph Report - .  (2026-05-21)

## Corpus Check
- Corpus is ~20,867 words - fits in a single context window. You may not need a graph.

## Summary
- 729 nodes · 846 edges · 101 communities (85 shown, 16 thin omitted)
- Extraction: 95% EXTRACTED · 5% INFERRED · 0% AMBIGUOUS · INFERRED: 46 edges (avg confidence: 0.8)
- Token cost: 153,156 input · 27,026 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Frontend Pages & Reel Player|Frontend Pages & Reel Player]]
- [[_COMMUNITY_Feed Scoring & Retrieval|Feed Scoring & Retrieval]]
- [[_COMMUNITY_Frontend API Client & Models|Frontend API Client & Models]]
- [[_COMMUNITY_Recommendation Agent Graph|Recommendation Agent Graph]]
- [[_COMMUNITY_Seeding Scripts & Embeddings|Seeding Scripts & Embeddings]]
- [[_COMMUNITY_Learning Path Extension|Learning Path Extension]]
- [[_COMMUNITY_TypeScript Config|TypeScript Config]]
- [[_COMMUNITY_Curriculum Agent Graph|Curriculum Agent Graph]]
- [[_COMMUNITY_Video Pipeline Agent|Video Pipeline Agent]]
- [[_COMMUNITY_Frontend Build Dependencies|Frontend Build Dependencies]]
- [[_COMMUNITY_Clip Seeding Pipeline|Clip Seeding Pipeline]]
- [[_COMMUNITY_Path Feed & Recommendations|Path Feed & Recommendations]]
- [[_COMMUNITY_Curated Curriculum Generation|Curated Curriculum Generation]]
- [[_COMMUNITY_Multi-signal Feed Ranking|Multi-signal Feed Ranking]]
- [[_COMMUNITY_Recommendation Scoring Nodes|Recommendation Scoring Nodes]]
- [[_COMMUNITY_Community 15|Community 15]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]
- [[_COMMUNITY_Community 18|Community 18]]
- [[_COMMUNITY_Community 19|Community 19]]
- [[_COMMUNITY_Community 20|Community 20]]
- [[_COMMUNITY_Community 21|Community 21]]
- [[_COMMUNITY_Community 22|Community 22]]
- [[_COMMUNITY_Community 23|Community 23]]
- [[_COMMUNITY_Community 24|Community 24]]
- [[_COMMUNITY_Community 25|Community 25]]
- [[_COMMUNITY_Community 26|Community 26]]
- [[_COMMUNITY_Community 27|Community 27]]
- [[_COMMUNITY_Community 28|Community 28]]
- [[_COMMUNITY_Community 29|Community 29]]
- [[_COMMUNITY_Community 30|Community 30]]
- [[_COMMUNITY_Community 31|Community 31]]
- [[_COMMUNITY_Community 32|Community 32]]
- [[_COMMUNITY_Community 33|Community 33]]
- [[_COMMUNITY_Community 34|Community 34]]
- [[_COMMUNITY_Community 35|Community 35]]
- [[_COMMUNITY_Community 36|Community 36]]
- [[_COMMUNITY_Community 37|Community 37]]
- [[_COMMUNITY_Community 38|Community 38]]
- [[_COMMUNITY_Community 39|Community 39]]
- [[_COMMUNITY_Community 40|Community 40]]
- [[_COMMUNITY_Community 41|Community 41]]
- [[_COMMUNITY_Community 42|Community 42]]
- [[_COMMUNITY_Community 43|Community 43]]
- [[_COMMUNITY_Community 44|Community 44]]
- [[_COMMUNITY_Community 45|Community 45]]
- [[_COMMUNITY_Community 46|Community 46]]
- [[_COMMUNITY_Community 47|Community 47]]
- [[_COMMUNITY_Community 48|Community 48]]
- [[_COMMUNITY_Community 49|Community 49]]
- [[_COMMUNITY_Community 50|Community 50]]
- [[_COMMUNITY_Community 51|Community 51]]
- [[_COMMUNITY_Community 52|Community 52]]
- [[_COMMUNITY_Community 53|Community 53]]
- [[_COMMUNITY_Community 54|Community 54]]
- [[_COMMUNITY_Community 55|Community 55]]
- [[_COMMUNITY_Community 56|Community 56]]
- [[_COMMUNITY_Community 57|Community 57]]
- [[_COMMUNITY_Community 58|Community 58]]
- [[_COMMUNITY_Community 59|Community 59]]
- [[_COMMUNITY_Community 60|Community 60]]
- [[_COMMUNITY_Community 61|Community 61]]
- [[_COMMUNITY_Community 62|Community 62]]
- [[_COMMUNITY_Community 63|Community 63]]
- [[_COMMUNITY_Community 64|Community 64]]
- [[_COMMUNITY_Community 65|Community 65]]
- [[_COMMUNITY_Community 66|Community 66]]
- [[_COMMUNITY_Community 67|Community 67]]
- [[_COMMUNITY_Community 68|Community 68]]
- [[_COMMUNITY_Community 69|Community 69]]
- [[_COMMUNITY_Community 70|Community 70]]
- [[_COMMUNITY_Community 71|Community 71]]
- [[_COMMUNITY_Community 72|Community 72]]
- [[_COMMUNITY_Community 73|Community 73]]
- [[_COMMUNITY_Community 74|Community 74]]
- [[_COMMUNITY_Community 75|Community 75]]
- [[_COMMUNITY_Community 76|Community 76]]
- [[_COMMUNITY_Community 77|Community 77]]
- [[_COMMUNITY_Community 79|Community 79]]
- [[_COMMUNITY_Community 80|Community 80]]
- [[_COMMUNITY_Community 81|Community 81]]
- [[_COMMUNITY_Community 82|Community 82]]
- [[_COMMUNITY_Community 86|Community 86]]
- [[_COMMUNITY_Community 87|Community 87]]
- [[_COMMUNITY_Community 88|Community 88]]
- [[_COMMUNITY_Community 89|Community 89]]
- [[_COMMUNITY_Community 90|Community 90]]
- [[_COMMUNITY_Community 94|Community 94]]
- [[_COMMUNITY_Community 95|Community 95]]
- [[_COMMUNITY_Community 96|Community 96]]
- [[_COMMUNITY_Community 97|Community 97]]
- [[_COMMUNITY_Community 98|Community 98]]
- [[_COMMUNITY_Community 99|Community 99]]
- [[_COMMUNITY_Community 100|Community 100]]

## God Nodes (most connected - your core abstractions)
1. `topics` - 54 edges
2. `compilerOptions` - 16 edges
3. `Supabase get_client (singleton)` - 13 edges
4. `useAuth()` - 11 edges
5. `process_video()` - 9 edges
6. `process_video (service)` - 9 edges
7. `FeedContent` - 8 edges
8. `get_path_feed endpoint` - 8 edges
9. `get_path_feed()` - 7 edges
10. `embed_texts()` - 7 edges

## Surprising Connections (you probably didn't know these)
- `process_video (service)` --implements--> `On-demand reel generation pipeline`  [INFERRED]
  backend/app/services/pipeline.py → README.md
- `Supabase (Postgres)` --references--> `clips.embedding vector(384)`  [INFERRED]
  README.md → backend/scripts/migration_pgvector.sql
- `_extend_path()` --calls--> `next`  [INFERRED]
  backend/app/api/feed.py → frontend/package.json
- `create_learning_path()` --calls--> `run_curriculum()`  [INFERRED]
  backend/app/api/topics.py → backend/app/agents/curriculum_agent.py
- `process_video (service)` --semantically_similar_to--> `run_pipeline`  [INFERRED] [semantically similar]
  backend/app/services/pipeline.py → backend/app/agents/pipeline_agent.py

## Hyperedges (group relationships)
- **Auth-to-onboarding routing flow** — login_loginpage, callback_authcallback, page_home, concept_onboarding_gate, api_getuserprofile [INFERRED 0.85]
- **Reel feed players and telemetry** — feed_feedcontent, discover_discoverpage, reelplayer_reelplayer, api_recordclipevent, concept_clip_telemetry [INFERRED 0.85]
- **AuthProvider context consumers** — auth_context_useauth, feed_feedcontent, discover_discoverpage, page_home, login_loginpage [INFERRED 0.85]
- **Topic-to-clips generation flow** — topics_create_learning_path, topics__process_single_topic, section_planner_plan_and_store_sections, pipeline_agent_run_pipeline [EXTRACTED 0.95]
- **LangGraph pipeline DAG (search to store)** — pipeline_agent__node_search, pipeline_agent__node_transcribe, pipeline_agent__node_segment, pipeline_agent__node_store [EXTRACTED 0.95]
- **Personalized feed scoring + interest update** — feed__compute_scores, feed__update_interest_vector, embeddings_ema_update, embeddings_cosine_similarity [INFERRED 0.85]
- **Clip seeding scripts share pipeline + Supabase** — bulk_seed_main, seed_clips_main, add_clip_main, pipeline_process_video, supabase_get_client [INFERRED 0.85]
- **Atomic personalization vector RPCs over pgvector** — migration_pgvector_merge_user_interest, migration_pgvector_merge_user_taste, migration_pgvector_merge_session_interest, migration_pgvector_clips_embedding [INFERRED 0.85]

## Communities (101 total, 16 thin omitted)

### Community 0 - "Frontend Pages & Reel Player"
Cohesion: 0.08
Nodes (37): metadata, Home(), SUGGESTIONS, isYouTubeEmbed(), Props, ReelPlayer(), sanitizeYTUrl(), DiscoverPage() (+29 more)

### Community 1 - "Feed Scoring & Retrieval"
Cohesion: 0.10
Nodes (31): _cached_slug_embeddings(), _compute_scores(), _fetch_clips_for_slug(), _fetch_discover_clips(), _get_clip_population_stats(), get_discover_feed(), get_feed(), get_path_feed() (+23 more)

### Community 2 - "Frontend API Client & Models"
Cohesion: 0.09
Nodes (32): authHeaders, Clip (data model), createLearningPath, FeedResponse (data model), getDiscoverFeed, getPathFeed, getRecommendations, getTopicFeed (+24 more)

### Community 3 - "Recommendation Agent Graph"
Cohesion: 0.09
Nodes (27): CurriculumState, PipelineState, build_recommendation_graph(), _generate_related_topics(), _node_find_candidates(), _node_identify_mastered(), _node_score_and_rank(), _node_trigger_fetch() (+19 more)

### Community 4 - "Seeding Scripts & Embeddings"
Cohesion: 0.08
Nodes (25): _node_segment(), main(), Add clips for one topic from one (or more) video URLs. Runs the same pipeline as, slug_to_name(), main(), Backfill embeddings for clips that don't have one yet.  Usage:     cd backend, main(), Bulk-seed clips from a CSV file of (topic-slug, url) pairs.  Usage:     cd backe (+17 more)

### Community 5 - "Learning Path Extension"
Cohesion: 0.08
Nodes (21): _extend_path(), Background: pick the next topic via recommendation_agent and add it to the path., create_learning_path(), _process_single_topic(), _process_topics_parallel(), Process the first topic on its own so its clips land fast (cold start),     then, dependencies, class-variance-authority (+13 more)

### Community 6 - "TypeScript Config"
Cohesion: 0.10
Nodes (19): compilerOptions, allowJs, esModuleInterop, incremental, isolatedModules, jsx, lib, module (+11 more)

### Community 7 - "Curriculum Agent Graph"
Cohesion: 0.14
Nodes (17): build_curriculum_graph(), _build_familiarity_prompt(), _groq(), _node_build_curriculum(), _node_load_curated(), _node_understand_intent(), _node_validate_path(), LangGraph agent: multi-step learning path generation with intent understanding + (+9 more)

### Community 8 - "Video Pipeline Agent"
Cohesion: 0.13
Nodes (14): build_pipeline_graph(), _node_transcribe(), LangGraph agent: YouTube search → transcript → Groq segmentation → Supabase stor, Run the full pipeline for a topic (or one section of a topic). Returns clips sto, run_pipeline(), Background: upsert topic rows and run pipeline for any with no clips yet., _seed_topics_bg(), _cache_get() (+6 more)

### Community 9 - "Frontend Build Dependencies"
Cohesion: 0.11
Nodes (17): devDependencies, autoprefixer, postcss, tailwindcss, @types/node, @types/react, @types/react-dom, @types/uuid (+9 more)

### Community 10 - "Clip Seeding Pipeline"
Cohesion: 0.23
Nodes (14): add_clip.main, add_clip.slug_to_name, bulk_seed.main, bulk_seed.slug_to_name, bulk_urls.seen.txt checkpoint, clear_topics.main, manual_seed.main, _extract_video_id (+6 more)

### Community 11 - "Path Feed & Recommendations"
Cohesion: 0.19
Nodes (13): _extend_path (auto-extend), _interleave_topics (diversity), _transcript_boost, get_path_feed endpoint, get_recommendations endpoint, RecommendationState, run_recommendations, TopicRequest schema (+5 more)

### Community 12 - "Curated Curriculum Generation"
Cohesion: 0.24
Nodes (11): Curated topics seed library, _node_load_curated, _node_understand_intent, _node_validate_path, build_curriculum_graph, CurriculumState, run_curriculum, _curated_topics loader (+3 more)

### Community 13 - "Multi-signal Feed Ranking"
Cohesion: 0.18
Nodes (11): cosine_similarity, _compute_scores (multi-signal ranking), _fetch_clips_for_slug, _fetch_discover_clips, _get_clip_population_stats, _interest_seed_slugs (cold start), _match_interest_slugs, _seed_topics_bg (+3 more)

### Community 14 - "Recommendation Scoring Nodes"
Cohesion: 0.22
Nodes (9): _node_build_curriculum, _get_session_telemetry, _generate_related_topics, _node_find_candidates, _node_identify_mastered, _node_load_telemetry, _node_score_and_rank, build_recommendation_graph (+1 more)

### Community 15 - "Community 15"
Cohesion: 0.25
Nodes (7): enabledPlugins, superpowers@claude-plugins-official, ui-ux-pro-max@ui-ux-pro-max-skill, env, CLAUDE_AUTOCOMPACT_PCT_OVERRIDE, MAX_THINKING_TOKENS, model

### Community 16 - "Community 16"
Cohesion: 0.29
Nodes (8): clips.embedding vector(384), clips_embedding_hnsw index, match_clips (SQL RPC), Curio (project), Groq (llama-3.3-70b-versatile), On-demand reel generation pipeline, Supabase (Postgres), OpenAI Whisper transcription

### Community 17 - "Community 17"
Cohesion: 0.29
Nodes (7): backfill_embeddings.main, embed_text, embed_texts, get_model (sentence-transformers), Startup warmup (preload embedding model), _identify_segments (LLM segmentation), _node_segment

### Community 18 - "Community 18"
Cohesion: 0.29
Nodes (7): _node_search (YouTube search), _node_store, _node_transcribe, build_pipeline_graph, PipelineState, run_pipeline, _node_trigger_fetch

### Community 19 - "Community 19"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, docker-basics

### Community 20 - "Community 20"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, eigenvalues-eigenvectors

### Community 21 - "Community 21"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, http-basics

### Community 22 - "Community 22"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, integrals-intro

### Community 23 - "Community 23"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, overfitting-regularization

### Community 24 - "Community 24"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, probability-basics

### Community 25 - "Community 25"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, recursion

### Community 26 - "Community 26"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, tcp-ip-explained

### Community 27 - "Community 27"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, concurrency-threads

### Community 28 - "Community 28"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, embeddings-intuition

### Community 29 - "Community 29"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, evolution-natural-selection

### Community 30 - "Community 30"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, large-language-models

### Community 31 - "Community 31"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, newtons-laws

### Community 32 - "Community 32"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, oauth-explained

### Community 33 - "Community 33"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, photosynthesis

### Community 34 - "Community 34"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, reinforcement-learning-basics

### Community 35 - "Community 35"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, convolutional-neural-networks

### Community 36 - "Community 36"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, game-theory-basics

### Community 37 - "Community 37"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, heaps-priority-queues

### Community 38 - "Community 38"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, how-cpus-work

### Community 39 - "Community 39"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, linked-lists

### Community 40 - "Community 40"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, loss-functions

### Community 41 - "Community 41"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, memory-management

### Community 42 - "Community 42"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, oop-basics

### Community 43 - "Community 43"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, opportunity-cost

### Community 44 - "Community 44"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, recurrent-neural-networks

### Community 45 - "Community 45"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, sorting-algorithms

### Community 46 - "Community 46"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, statistics-fundamentals

### Community 47 - "Community 47"
Cohesion: 0.33
Nodes (6): difficulty, name, prerequisites, _search_hint, videos, system-design-basics

### Community 48 - "Community 48"
Cohesion: 0.33
Nodes (6): wave-particle-duality, difficulty, name, prerequisites, _search_hint, videos

### Community 49 - "Community 49"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, big-o-notation

### Community 50 - "Community 50"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, linear-algebra-matrices

### Community 51 - "Community 51"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, calculus-chain-rule

### Community 52 - "Community 52"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, general-relativity

### Community 53 - "Community 53"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, supply-and-demand

### Community 54 - "Community 54"
Cohesion: 0.40
Nodes (5): _get_jwks_client, require_user (JWT auth dependency), InterestsPayload schema, get_profile endpoint, set_interests endpoint

### Community 55 - "Community 55"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, backpropagation

### Community 56 - "Community 56"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, dynamic-programming

### Community 57 - "Community 57"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, git-basics

### Community 58 - "Community 58"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, neural-networks-basics

### Community 59 - "Community 59"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, sql-fundamentals

### Community 60 - "Community 60"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, bayes-theorem

### Community 61 - "Community 61"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, binary-search

### Community 62 - "Community 62"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, binary-trees

### Community 63 - "Community 63"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, calculus-derivatives

### Community 64 - "Community 64"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, dna-replication

### Community 65 - "Community 65"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, fourier-transform

### Community 66 - "Community 66"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, gradient-descent

### Community 67 - "Community 67"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, graphs-bfs-dfs

### Community 68 - "Community 68"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, hashmaps

### Community 69 - "Community 69"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, linear-algebra-vectors

### Community 70 - "Community 70"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, quantum-entanglement

### Community 71 - "Community 71"
Cohesion: 0.40
Nodes (5): difficulty, name, prerequisites, videos, rest-apis

### Community 72 - "Community 72"
Cohesion: 0.40
Nodes (5): transformers-attention, difficulty, name, prerequisites, videos

### Community 73 - "Community 73"
Cohesion: 0.50
Nodes (3): plan_and_store_sections(), Generate and store 4-section teaching plans per topic.  Each topic gets four ord, Generate 4 sections via LLM and store in topic_sections. Returns the section dic

### Community 74 - "Community 74"
Cohesion: 0.67
Nodes (3): _get_jwks_client(), FastAPI dependency — validates Supabase JWT and returns the caller's user_id., require_user()

### Community 76 - "Community 76"
Cohesion: 0.50
Nodes (3): model, permissions, allow

### Community 77 - "Community 77"
Cohesion: 0.50
Nodes (4): ema_update (EMA taste vector), _update_interest_vector, record_clip_event endpoint, ClipEvent schema

### Community 80 - "Community 80"
Cohesion: 0.67
Nodes (3): FastAPI app (main), _real_ip (key func), SlowAPI limiter

## Knowledge Gaps
- **353 isolated node(s):** `config`, `config`, `name`, `version`, `private` (+348 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **16 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `topics` connect `Community 79` to `Community 19`, `Community 20`, `Community 21`, `Community 22`, `Community 23`, `Community 24`, `Community 25`, `Community 26`, `Community 27`, `Community 28`, `Community 29`, `Community 30`, `Community 31`, `Community 32`, `Community 33`, `Community 34`, `Community 35`, `Community 36`, `Community 37`, `Community 38`, `Community 39`, `Community 40`, `Community 41`, `Community 42`, `Community 43`, `Community 44`, `Community 45`, `Community 46`, `Community 47`, `Community 48`, `Community 49`, `Community 50`, `Community 51`, `Community 52`, `Community 53`, `Community 55`, `Community 56`, `Community 57`, `Community 58`, `Community 59`, `Community 60`, `Community 61`, `Community 62`, `Community 63`, `Community 64`, `Community 65`, `Community 66`, `Community 67`, `Community 68`, `Community 69`, `Community 70`, `Community 71`, `Community 72`?**
  _High betweenness centrality (0.164) - this node is a cross-community bridge._
- **Why does `_extend_path()` connect `Learning Path Extension` to `Feed Scoring & Retrieval`?**
  _High betweenness centrality (0.024) - this node is a cross-community bridge._
- **Why does `dependencies` connect `Learning Path Extension` to `Frontend Build Dependencies`?**
  _High betweenness centrality (0.019) - this node is a cross-community bridge._
- **What connects `config`, `config`, `name` to the rest of the system?**
  _404 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `Frontend Pages & Reel Player` be split into smaller, more focused modules?**
  _Cohesion score 0.07607843137254902 - nodes in this community are weakly interconnected._
- **Should `Feed Scoring & Retrieval` be split into smaller, more focused modules?**
  _Cohesion score 0.10416666666666667 - nodes in this community are weakly interconnected._
- **Should `Frontend API Client & Models` be split into smaller, more focused modules?**
  _Cohesion score 0.08870967741935484 - nodes in this community are weakly interconnected._