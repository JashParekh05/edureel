import math
import re
import time
import random
import logging
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from app.rate_limit import limiter
from app.models.schemas import Clip, ClipEvent, FeedResponse, TopicRecommendation
from app.db.supabase import get_client
from app.services.embeddings import embed_text, cosine_similarity, ema_update
from app.auth import require_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/feed", tags=["feed"])

# In-memory throttle for path auto-extension. Prevents 4s-poll storm from queuing
# multiple extensions per session. Best-effort only — fine if it drops on restart.
_LOW_CLIPS_THRESHOLD = 3            # extend when unseen clips fall below this
_EXTEND_COOLDOWN_S = 20             # don't re-extend a session within this window
_extending_sessions: dict[str, float] = {}


def _should_extend(session_id: str) -> bool:
    """Returns True if this session is eligible for auto-extension right now."""
    now = time.time()
    last = _extending_sessions.get(session_id, 0)
    if now - last < _EXTEND_COOLDOWN_S:
        return False
    _extending_sessions[session_id] = now
    return True


async def _extend_path(session_id: str) -> None:
    """Background: pick the next topic via recommendation_agent and add it to the path.
    Uses user's accumulated taste/interest vectors to choose what's next."""
    from app.agents.recommendation_agent import run_recommendations
    from app.api.topics import _process_single_topic

    db = get_client()
    try:
        path = (
            db.table("learning_paths")
            .select("topic_slugs")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.warning(f"[feed] extend: failed to read path for session={session_id}: {e}")
        return
    if not path.data:
        return
    current_slugs: list[str] = path.data[0].get("topic_slugs") or []

    try:
        recs = await asyncio.to_thread(run_recommendations, session_id, current_slugs)
    except Exception as e:
        logger.warning(f"[feed] extend: recommendation_agent failed for session={session_id}: {e}")
        return

    new_rec = next((r for r in recs if r.slug not in current_slugs), None)
    if not new_rec:
        logger.info(f"[feed] extend: no novel recommendation for session={session_id}")
        return

    try:
        db.table("learning_paths").update({
            "topic_slugs": current_slugs + [new_rec.slug],
        }).eq("session_id", session_id).execute()
    except Exception as e:
        logger.warning(f"[feed] extend: failed to append topic for session={session_id}: {e}")
        return

    logger.info(f"[feed] extended session={session_id} with topic='{new_rec.slug}' (rationale: {new_rec.rationale[:80]})")

    # Ensure topics row exists before pipeline runs (pipeline clips FK to topics table)
    try:
        existing = db.table("topics").select("slug").eq("slug", new_rec.slug).execute()
        if not existing.data:
            db.table("topics").insert({"slug": new_rec.slug, "name": new_rec.name}).execute()
    except Exception as e:
        logger.warning(f"[feed] extend: failed to upsert topic row for slug={new_rec.slug}: {e}")

    # Cache-check is inside _process_single_topic — safe to call even if clips exist
    await _process_single_topic(new_rec.slug, new_rec.name)


def _parse_vector(v) -> list[float] | None:
    """Supabase returns pgvector columns as strings; parse them to list[float]."""
    if v is None:
        return None
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        import json as _json
        try:
            return _json.loads(v)
        except Exception:
            return None
    return None


# ---------------------------------------------------------------------------
# Telemetry helpers
# ---------------------------------------------------------------------------

def _get_session_telemetry(db, session_id: str) -> tuple[set[str], dict[str, float]]:
    """Returns (seen_clip_ids, topic_completion_rates)."""
    try:
        events = (
            db.table("clip_events")
            .select("clip_id, watch_ms, completed")
            .eq("session_id", session_id)
            .execute()
        )
    except Exception as e:
        logger.warning(f"[feed] Failed to fetch telemetry for session={session_id}: {e}")
        return set(), {}

    seen_ids: set[str] = set()
    topic_watches: dict[str, list[bool]] = {}

    clip_ids = list({ev["clip_id"] for ev in events.data})
    seen_ids = set(clip_ids)

    slug_lookup: dict[str, str] = {}
    if clip_ids:
        clips_res = db.table("clips").select("id, topic_slug").in_("id", clip_ids).execute()
        slug_lookup = {c["id"]: c["topic_slug"] for c in clips_res.data}

    for ev in events.data:
        slug = slug_lookup.get(ev["clip_id"])
        if slug:
            topic_watches.setdefault(slug, []).append(ev["completed"])

    topic_completion = {
        slug: sum(completions) / len(completions)
        for slug, completions in topic_watches.items()
    }
    return seen_ids, topic_completion


def _update_interest_vector(
    db,
    session_id: str,
    topic_slug: str,
    completed: bool,
    replay_count: int,
    feedback: str | None = None,
    clip_embedding: list[float] | None = None,
    user_id: str | None = None,
    watch_ms: int = 0,
    duration_seconds: int | None = None,
) -> None:
    """Real-time interest vector + taste vector update after a clip event.

    Skip velocity matters: bailing in <10% of a clip is a much stronger 'no' than
    watching most of it. Lets the algorithm tell 'topic is boring' from 'this
    specific clip didn't quite land'.
    """
    existing = (
        db.table("session_embeddings")
        .select("taste_vector")
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )
    row = existing.data[0] if existing.data else {}
    taste: list[float] | None = _parse_vector(row.get("taste_vector"))

    if feedback == "want_more":
        delta = 0.6
    elif feedback == "already_know":
        delta = -1.0
    elif completed:
        delta = 0.15 + replay_count * 0.3
    else:
        # Tiered penalty based on how much the user watched
        duration_s = max(1.0, float(duration_seconds or 60))
        watch_ratio = (watch_ms or 0) / 1000.0 / duration_s
        if watch_ratio < 0.1:        # bailed almost instantly
            base = -0.30
        elif watch_ratio < 0.4:      # casual skip
            base = -0.10
        else:                        # watched most of it
            base = -0.02
        delta = base + replay_count * 0.3

    # Atomic interest vector update via RPC (prevents concurrent-write clobber)
    try:
        db.rpc("merge_session_interest", {
            "p_session_id": session_id,
            "p_topic_slug": topic_slug,
            "p_delta": round(delta, 4),
        }).execute()
    except Exception as e:
        logger.warning(f"[feed] Failed to merge session interest for session={session_id}: {e}")

    # Update taste vector via EMA when we have a clip embedding and the event is positive
    update_taste = clip_embedding and delta > 0
    new_taste = taste
    if update_taste:
        if taste and len(taste) == len(clip_embedding):
            new_taste = ema_update(taste, clip_embedding, alpha=0.2)
        else:
            new_taste = clip_embedding

    if new_taste is not None:
        try:
            db.table("session_embeddings").upsert({
                "session_id": session_id,
                "taste_vector": new_taste,
                "updated_at": "now()",
            }).execute()
        except Exception as e:
            logger.warning(f"[feed] Failed to upsert taste_vector for session={session_id}: {e}")

    # Merge into user-level profile for cross-session persistence (atomic via RPC)
    if user_id:
        try:
            db.rpc("merge_user_interest", {
                "p_user_id": user_id,
                "p_topic_slug": topic_slug,
                "p_delta": round(delta * 0.5, 4),
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to update user-level interest for {user_id}: {e}")

        if new_taste is not None:
            try:
                db.rpc("merge_user_taste", {
                    "p_user_id": user_id,
                    "p_new_taste": new_taste,
                    "p_alpha": 0.1,
                }).execute()
            except Exception as e:
                logger.warning(f"Failed to update taste_vector for {user_id}: {e}")


# ---------------------------------------------------------------------------
# Multi-signal scoring (PLE-inspired)
# ---------------------------------------------------------------------------

def _get_clip_population_stats(db, clip_ids: list[str]) -> dict[str, float]:
    """Population-level completion rate per clip across all sessions."""
    if not clip_ids:
        return {}
    try:
        rows = (
            db.table("clip_events")
            .select("clip_id, completed")
            .in_("clip_id", clip_ids)
            .execute()
        )
    except Exception as e:
        logger.warning(f"[feed] Failed to fetch population stats for {len(clip_ids)} clips: {e}")
        return {}
    totals: dict[str, list[bool]] = {}
    for r in rows.data:
        totals.setdefault(r["clip_id"], []).append(bool(r["completed"]))
    return {cid: sum(v) / len(v) for cid, v in totals.items()}


def _compute_scores(
    clips: list[Clip],
    pop_stats: dict[str, float],
    user_avg_watch_seconds: float | None,
    interest_vector: dict[str, float] | None = None,
    taste_vector: list[float] | None = None,
) -> list[Clip]:
    """
    final_score = 0.28 * hook_score
                + 0.23 * population_completion_rate
                + 0.18 * duration_affinity
                + 0.13 * recency_bonus
                + 0.10 * interest_affinity
                + 0.08 * semantic_affinity   (0 when no taste_vector or no clip embedding)
    """
    now = datetime.now(timezone.utc)
    for clip in clips:
        hook = clip.hook_score or 0.5
        pop = pop_stats.get(clip.id, hook)

        dur_affinity = 1.0
        if user_avg_watch_seconds and clip.duration_seconds:
            ratio = clip.duration_seconds / max(user_avg_watch_seconds, 10)
            dur_affinity = math.exp(-0.3 * max(0, ratio - 1.5))

        recency = 0.5
        if clip.created_at:
            try:
                age_days = (now - datetime.fromisoformat(clip.created_at.replace("Z", "+00:00"))).days
                recency = math.exp(-age_days / 7)
            except Exception:
                pass

        # Normalize interest affinity from [-1, 1] → [0, 1]
        raw_affinity = float((interest_vector or {}).get(clip.topic_slug, 0.0))
        affinity = (raw_affinity + 1.0) / 2.0

        # Semantic affinity: cosine similarity between taste vector and clip embedding
        semantic = 0.5  # neutral default
        if taste_vector and getattr(clip, "embedding", None):
            try:
                raw_sim = cosine_similarity(taste_vector, clip.embedding)
                semantic = (raw_sim + 1.0) / 2.0  # [-1,1] → [0,1]
            except Exception:
                pass

        clip.final_score = round(
            0.28 * hook + 0.23 * pop + 0.18 * dur_affinity + 0.13 * recency + 0.10 * affinity + 0.08 * semantic,
            4,
        )
    return clips


# ---------------------------------------------------------------------------
# Transcript keyword boost
# ---------------------------------------------------------------------------

_STOPWORDS = {"the", "and", "for", "that", "with", "how", "what", "want", "learn", "about", "using"}


def _transcript_boost(clips: list[Clip], user_query: str) -> list[Clip]:
    """Boost clips whose transcript contains keywords from the user's query (Transcript SEO)."""
    if not user_query:
        return clips
    keywords = set(re.findall(r'\b[a-z]{3,}\b', user_query.lower())) - _STOPWORDS
    if not keywords:
        return clips
    for clip in clips:
        if not clip.transcript:
            continue
        transcript_lower = clip.transcript.lower()
        matches = sum(1 for kw in keywords if kw in transcript_lower)
        clip.final_score = round(min(1.0, (clip.final_score or clip.hook_score) + 0.15 * matches / len(keywords)), 4)
    return clips


# ---------------------------------------------------------------------------
# Diversity injection (anti-echo-chamber)
# ---------------------------------------------------------------------------

def _interleave_topics(feeds: list[FeedResponse]) -> list[FeedResponse]:
    """Round-robin: every 4th clip in primary topic, inject one from next topic."""
    if len(feeds) <= 1:
        return feeds
    active = [(f.topic_slug, list(f.clips)) for f in feeds if f.clips]
    if not active:
        return feeds

    result: dict[str, list[Clip]] = {slug: [] for slug, _ in active}
    primary_slug, primary_clips = active[0]
    others = active[1:]
    other_idx = 0

    for i, clip in enumerate(primary_clips):
        result[primary_slug].append(clip)
        if (i + 1) % 4 == 0 and others:
            o_slug, o_clips = others[other_idx % len(others)]
            if o_clips:
                result[o_slug].append(o_clips.pop(0))
            other_idx += 1

    for o_slug, o_clips in others:
        result[o_slug].extend(o_clips)

    return [
        FeedResponse(
            topic_slug=f.topic_slug,
            clips=result.get(f.topic_slug, f.clips),
            processing=f.processing,
        )
        for f in feeds
    ]


# ---------------------------------------------------------------------------
# Clip fetching
# ---------------------------------------------------------------------------

def _spread_by_source(clips: list[Clip]) -> list[Clip]:
    """Reorder so consecutive clips don't share the same source video.
    Round-robins one clip per source_url until exhausted. Preserves relative
    score order within each source group."""
    if len(clips) <= 1:
        return clips
    by_source: dict[str, list[Clip]] = {}
    order: list[str] = []
    for c in clips:
        key = c.source_url or c.id
        if key not in by_source:
            by_source[key] = []
            order.append(key)
        by_source[key].append(c)
    result: list[Clip] = []
    while any(by_source[k] for k in order):
        for k in order:
            if by_source[k]:
                result.append(by_source[k].pop(0))
    return result


def _fetch_clips_for_slug(
    db,
    slug: str,
    seen_ids: set[str] | None = None,
    limit: int = 16,
    user_avg_watch_seconds: float | None = None,
    interest_vector: dict[str, float] | None = None,
    taste_vector: list[float] | None = None,
) -> list[Clip]:
    # Discover which sections exist so we sample evenly across the curriculum.
    # Without this, ordering by created_at puts all section-0 clips first and
    # section 3 clips never appear within the limit.
    try:
        sections_res = (
            db.table("clips")
            .select("section_index")
            .eq("topic_slug", slug)
            .execute()
        )
        section_indices = sorted({r["section_index"] for r in sections_res.data if r["section_index"] is not None})
    except Exception as e:
        logger.warning(f"[feed] Failed to fetch section indices for slug={slug}: {e}")
        section_indices = []

    clips: list[Clip] = []

    if section_indices:
        per_section = max(2, limit // len(section_indices))
        for section_idx in section_indices:
            try:
                result = (
                    db.table("clips")
                    .select("*")
                    .eq("topic_slug", slug)
                    .eq("section_index", section_idx)
                    .order("hook_score", desc=True)
                    .limit(per_section)
                    .execute()
                )
            except Exception as e:
                logger.warning(f"[feed] Failed to fetch clips for slug={slug} section={section_idx}: {e}")
                continue
            for row in result.data:
                if seen_ids and row["id"] in seen_ids:
                    continue
                row.setdefault("hook_score", 0.5)
                clips.append(Clip(**row))

    # Fallback when no section data exists yet (pipeline still running)
    if not clips:
        try:
            result = (
                db.table("clips")
                .select("*")
                .eq("topic_slug", slug)
                .order("hook_score", desc=True)
                .limit(limit)
                .execute()
            )
        except Exception as e:
            logger.warning(f"[feed] Failed to fetch clips for slug={slug}: {e}")
            return []
        for row in result.data:
            if seen_ids and row["id"] in seen_ids:
                continue
            row.setdefault("hook_score", 0.5)
            clips.append(Clip(**row))

    clip_ids = [c.id for c in clips]
    pop_stats = _get_clip_population_stats(db, clip_ids)
    clips = _compute_scores(clips, pop_stats, user_avg_watch_seconds, interest_vector, taste_vector)
    sorted_clips = sorted(clips, key=lambda c: c.final_score or c.hook_score, reverse=True)
    return _spread_by_source(sorted_clips)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/path/{session_id}", response_model=list[FeedResponse])
async def get_path_feed(session_id: str, background_tasks: BackgroundTasks, caller_id: str = Depends(require_user)):
    db = get_client()
    try:
        path = (
            db.table("learning_paths")
            .select("topic_slugs, user_query, user_id")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"[feed] Failed to fetch learning_path for session={session_id}: {e}")
        return []
    if not path.data:
        return []
    if path.data[0].get("user_id") and path.data[0]["user_id"] != caller_id:
        raise HTTPException(status_code=403, detail="Access denied")

    user_query = path.data[0].get("user_query", "")
    seen_ids, topic_completion = _get_session_telemetry(db, session_id)

    # User's typical engagement length from completed clips
    try:
        watch_rows = (
            db.table("clip_events")
            .select("watch_ms")
            .eq("session_id", session_id)
            .eq("completed", True)
            .limit(20)
            .execute()
        )
        user_avg_watch_seconds = (
            sum(r["watch_ms"] for r in watch_rows.data) / len(watch_rows.data) / 1000
            if watch_rows.data else None
        )
    except Exception as e:
        logger.warning(f"[feed] Failed to fetch watch_ms for session={session_id}: {e}")
        user_avg_watch_seconds = None

    # Live interest vector + taste vector for personalized re-ranking
    try:
        iv_res = (
            db.table("session_embeddings")
            .select("interest_vector, taste_vector")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.warning(f"[feed] Failed to fetch session_embeddings for session={session_id}: {e}")
        iv_res = type("R", (), {"data": []})()

    if iv_res.data:
        iv_row = iv_res.data[0]
        interest_vector: dict[str, float] = iv_row.get("interest_vector") or {}
        taste_vector: list[float] | None = _parse_vector(iv_row.get("taste_vector"))
    else:
        # New session — seed from user-level profile
        interest_vector = {}
        taste_vector = None
        try:
            path_user = db.table("learning_paths").select("user_id").eq("session_id", session_id).limit(1).execute()
        except Exception as e:
            logger.warning(f"[feed] Failed to fetch user_id for session={session_id}: {e}")
            path_user = type("R", (), {"data": []})()
        if path_user.data and path_user.data[0].get("user_id"):
            uid = path_user.data[0]["user_id"]
            try:
                up = db.table("user_profiles").select("taste_vector, interest_vector").eq("user_id", uid).limit(1).execute()
            except Exception as e:
                logger.warning(f"[feed] Failed to fetch user_profiles for user={uid}: {e}")
                up = type("R", (), {"data": []})()
            if up.data:
                interest_vector = up.data[0].get("interest_vector") or {}
                taste_vector = _parse_vector(up.data[0].get("taste_vector"))
                seed_row: dict = {
                    "session_id": session_id,
                    "interest_vector": interest_vector,
                    "updated_at": "now()",
                }
                if taste_vector is not None:
                    seed_row["taste_vector"] = taste_vector
                try:
                    db.table("session_embeddings").upsert(seed_row).execute()
                except Exception as e:
                    logger.warning(f"Failed to seed session_embeddings for {session_id}: {e}")

    feeds = []
    for slug in path.data[0]["topic_slugs"]:
        # Skip topics the user has marked as already known
        if interest_vector.get(slug, 0.0) <= -0.8:
            continue

        clips = _fetch_clips_for_slug(
            db, slug,
            seen_ids=seen_ids,
            user_avg_watch_seconds=user_avg_watch_seconds,
            interest_vector=interest_vector,
            taste_vector=taste_vector,
        )
        completion_rate = topic_completion.get(slug, 0.0)

        # Struggling on this topic: sort by shortest clips first
        if completion_rate < 0.3 and slug in topic_completion:
            clips = sorted(clips, key=lambda c: c.duration_seconds or 999)

        clips = _transcript_boost(clips, user_query)

        feeds.append(FeedResponse(
            topic_slug=slug,
            clips=clips,
            processing=len(clips) == 0,
        ))

    # Bubble "want more" topics (high interest) to the front
    feeds.sort(key=lambda f: interest_vector.get(f.topic_slug, 0.0), reverse=True)

    # Cross-topic dedupe: same clip shouldn't appear under multiple topic feeds
    seen_clip_ids: set[str] = set()
    deduped_feeds: list[FeedResponse] = []
    for f in feeds:
        unique: list[Clip] = []
        for c in f.clips:
            if c.id not in seen_clip_ids:
                seen_clip_ids.add(c.id)
                unique.append(c)
        deduped_feeds.append(FeedResponse(topic_slug=f.topic_slug, clips=unique, processing=f.processing))

    # Auto-extend the path when user is running low on unseen clips.
    # Skip if any topic is still processing — pipelines may still deliver clips.
    total_unseen = sum(len(f.clips) for f in deduped_feeds)
    still_processing = any(f.processing for f in deduped_feeds)
    if total_unseen < _LOW_CLIPS_THRESHOLD and not still_processing and _should_extend(session_id):
        background_tasks.add_task(_extend_path, session_id)
        logger.info(f"[feed] session={session_id} low on clips ({total_unseen}); queued path extension")

    return _interleave_topics(deduped_feeds)


@router.get("/recommendations/{session_id}", response_model=list[TopicRecommendation])
async def get_recommendations(session_id: str, caller_id: str = Depends(require_user)):
    db = get_client()
    try:
        path = (
            db.table("learning_paths")
            .select("topic_slugs, user_id")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"[feed] Failed to fetch learning_path for recommendations session={session_id}: {e}")
        return []
    if not path.data:
        return []
    if path.data[0].get("user_id") and path.data[0]["user_id"] != caller_id:
        raise HTTPException(status_code=403, detail="Access denied")
    path_slugs = path.data[0]["topic_slugs"]

    from app.agents.recommendation_agent import run_recommendations
    return await asyncio.to_thread(run_recommendations, session_id, path_slugs)


@router.get("/{topic_slug}", response_model=FeedResponse)
async def get_feed(
    topic_slug: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
    caller_id: str = Depends(require_user),
):
    db = get_client()
    try:
        result = (
            db.table("clips")
            .select("id,topic_slug,title,description,video_url,thumbnail_url,duration_seconds,source_url,source_platform,hook_score,created_at,section_index")
            .eq("topic_slug", topic_slug)
            .order("created_at", desc=False)
            .range(offset, offset + limit - 1)
            .execute()
        )
    except Exception as e:
        logger.error(f"[feed] Failed to fetch clips for slug={topic_slug}: {e}")
        return FeedResponse(topic_slug=topic_slug, clips=[], processing=True)
    clips = []
    for row in result.data:
        row.setdefault("hook_score", 0.5)
        clips.append(Clip(**row))
    clip_ids = [c.id for c in clips]
    pop_stats = _get_clip_population_stats(db, clip_ids)
    clips = _compute_scores(clips, pop_stats, None)
    clips = sorted(clips, key=lambda c: c.final_score or c.hook_score, reverse=True)
    return FeedResponse(topic_slug=topic_slug, clips=clips, processing=len(clips) == 0)


_slug_emb_cache: tuple[tuple[str, ...], list[list[float] | None]] = ((), [])


def _cached_slug_embeddings(slugs: list[str]) -> list[list[float] | None]:
    global _slug_emb_cache
    from app.services.embeddings import embed_texts
    key = tuple(slugs)
    if _slug_emb_cache[0] != key:
        _slug_emb_cache = (key, embed_texts([s.replace("-", " ") for s in slugs]))
    return _slug_emb_cache[1]


def _match_interest_slugs(interests: list[str], all_slugs: list[str], taste_vector: list[float] | None = None) -> list[str]:
    """Return topic slugs relevant to the user's interests.

    Uses semantic similarity when taste_vector is available, otherwise falls
    back to keyword overlap.
    """
    if not interests and taste_vector is None:
        return all_slugs[:10]

    # Semantic path: embed each slug and rank by cosine similarity to taste
    if taste_vector is not None:
        from app.services.embeddings import cosine_similarity
        slug_embeddings = _cached_slug_embeddings(all_slugs)
        scored = []
        for slug, emb in zip(all_slugs, slug_embeddings):
            if emb is not None:
                scored.append((slug, cosine_similarity(taste_vector, emb)))
            else:
                scored.append((slug, 0.0))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[:10]]

    # Fallback: keyword overlap
    if not interests:
        return all_slugs[:10]
    keywords = set()
    for tag in interests:
        keywords.update(re.findall(r'\b[a-z]{3,}\b', tag.lower()))
    matched = [s for s in all_slugs if any(kw in s for kw in keywords)]
    return matched or all_slugs[:10]


def _fetch_discover_clips(
    db,
    relevant_slugs: list[str],
    all_slugs: list[str],
    seen_ids: set[str],
    limit: int,
    interest_vector: dict[str, float] | None = None,
    taste_vector: list[float] | None = None,
) -> list[Clip]:
    relevant_limit = int(limit * 0.6)

    clips: list[Clip] = []

    # Relevant clips first
    _DISCOVER_COLS = "id,topic_slug,title,description,video_url,thumbnail_url,duration_seconds,source_url,source_platform,hook_score,created_at,embedding"
    for slug in relevant_slugs[:5]:
        try:
            result = db.table("clips").select(_DISCOVER_COLS).eq("topic_slug", slug).limit(6).execute()
        except Exception as e:
            logger.warning(f"[feed] Failed to fetch discover clips for slug={slug}: {e}")
            continue
        for row in result.data:
            if row["id"] not in seen_ids and len(clips) < relevant_limit:
                row.setdefault("hook_score", 0.5)
                clips.append(Clip(**row))

    # Diversity fill from other slugs
    other_slugs = [s for s in all_slugs if s not in relevant_slugs]
    random.shuffle(other_slugs)
    for slug in other_slugs[:8]:
        try:
            result = db.table("clips").select(_DISCOVER_COLS).eq("topic_slug", slug).limit(3).execute()
        except Exception as e:
            logger.warning(f"[feed] Failed to fetch discover clips for slug={slug}: {e}")
            continue
        for row in result.data:
            if row["id"] not in seen_ids and len(clips) < limit:
                row.setdefault("hook_score", 0.5)
                clips.append(Clip(**row))

    clip_ids = [c.id for c in clips]
    pop_stats = _get_clip_population_stats(db, clip_ids)
    clips = _compute_scores(clips, pop_stats, None, interest_vector=interest_vector, taste_vector=taste_vector)
    random.shuffle(clips)
    return _spread_by_source(clips[:limit])


GRADE_LEVEL_TOPIC_MAP: dict[str, dict[str, list[str]]] = {
    "elementary_school": {
        "math": ["addition-subtraction", "multiplication-division", "place-value", "basic-fractions", "shapes-patterns"],
        "science": ["plants-animals", "weather-seasons", "simple-machines", "states-of-matter", "solar-system-basics"],
        "history": ["community-helpers", "native-american-history", "early-explorers", "ancient-civilizations-intro", "american-symbols"],
        "technology": ["computer-basics", "internet-safety", "typing-skills", "coding-blocks", "digital-citizenship"],
        "english_language_arts": ["phonics", "reading-comprehension", "story-elements", "vocabulary-building", "basic-grammar"],
        "arts": ["color-theory-basics", "drawing-basics", "music-rhythm", "crafts", "famous-artists-intro"],
        "life_skills": ["emotions", "friendship", "healthy-habits", "time-management-basics", "classroom-behavior"],
        "world_languages": ["spanish-basics", "french-basics", "greetings", "numbers-colors", "family-words"],
    },
    "middle_school": {
        "math": ["fractions", "ratios", "pre-algebra", "geometry-basics", "probability"],
        "science": ["cells", "ecosystems", "weather", "forces-motion", "earth-science"],
        "history": ["american-revolution", "ancient-egypt", "roman-empire", "middle-ages", "early-american-history"],
        "technology": ["internet-basics", "coding-intro", "scratch", "digital-research", "cybersecurity-basics"],
        "english_language_arts": ["essay-writing", "theme-analysis", "figurative-language", "grammar-punctuation", "argument-writing"],
        "arts": ["drawing-techniques", "music-theory-basics", "theater-intro", "art-history-basics", "creative-design"],
        "life_skills": ["study-skills", "organization", "conflict-resolution", "goal-setting", "media-literacy"],
        "world_languages": ["spanish-grammar-intro", "french-grammar-intro", "basic-conversation", "food-travel-vocabulary", "culture-intro"],
    },
    "high_school": {
        "math": ["algebra-2", "calculus-intro", "statistics", "trigonometry", "geometry-proofs"],
        "science": ["chemistry", "physics-mechanics", "biology", "environmental-science", "anatomy-physiology"],
        "history": ["ww2", "civil-war", "cold-war", "world-history", "us-government"],
        "technology": ["python-intro", "web-dev", "app-design", "databases-intro", "cybersecurity"],
        "english_language_arts": ["literary-analysis", "research-papers", "sat-vocabulary", "rhetorical-analysis", "creative-writing"],
        "arts": ["graphic-design", "photography", "music-composition", "film-analysis", "portfolio-building"],
        "life_skills": ["financial-literacy", "college-readiness", "career-exploration", "public-speaking", "mental-health-awareness"],
        "world_languages": ["spanish-conversation", "french-conversation", "grammar-intermediate", "literature-intro", "cultural-studies"],
    },
    "college": {
        "math": ["linear-algebra", "calc-3", "differential-equations", "discrete-math", "probability-theory"],
        "science": ["thermodynamics", "quantum-mechanics", "organic-chemistry", "molecular-biology", "geology"],
        "history": ["federalist-papers", "industrial-revolution", "modern-europe", "postcolonial-history", "constitutional-history"],
        "technology": ["data-structures", "machine-learning", "systems-programming", "software-engineering", "cloud-computing"],
        "english_language_arts": ["academic-writing", "literary-theory", "technical-writing", "composition", "research-methods"],
        "arts": ["art-criticism", "design-systems", "music-history", "media-production", "studio-art"],
        "life_skills": ["resume-building", "internship-prep", "personal-finance", "professional-communication", "time-management"],
        "world_languages": ["advanced-spanish", "advanced-french", "translation-practice", "conversation-advanced", "global-culture"],
    },
    "adult_learning": {
        "math": ["practical-math", "personal-finance-math", "statistics-for-work", "business-math", "data-literacy"],
        "science": ["health-science-basics", "climate-science", "nutrition", "astronomy-basics", "everyday-physics"],
        "history": ["modern-world-history", "american-history-review", "civic-history", "economic-history", "global-conflicts"],
        "technology": ["excel-basics", "ai-literacy", "productivity-tools", "online-privacy", "coding-for-career-switchers"],
        "english_language_arts": ["business-writing", "reading-skills", "presentation-writing", "professional-email", "critical-thinking"],
        "arts": ["creative-hobbies", "digital-photography", "music-appreciation", "interior-design-basics", "visual-storytelling"],
        "life_skills": ["career-growth", "parenting-skills", "budgeting", "health-wellness", "communication-skills"],
        "world_languages": ["travel-spanish", "travel-french", "workplace-english", "conversation-practice", "language-refreshers"],
    },
}

_GRADE_DIFFICULTY: dict[str, str] = {
    "preschool": "beginner",
    "elementary": "beginner",
    "elementary_school": "beginner",
    "middle_school": "beginner",
    "high_school": "intermediate",
    "college": "intermediate",
    "adult_learning": "intermediate",
    "professional": "advanced",
}

# Normalize onboarding grade-level values to GRADE_LEVEL_TOPIC_MAP keys
_GRADE_LEVEL_ALIASES: dict[str, str] = {
    "preschool": "elementary_school",
    "elementary": "elementary_school",
    "professional": "adult_learning",
}

# Normalize onboarding interest tags to GRADE_LEVEL_TOPIC_MAP category keys
_INTEREST_ALIASES: dict[str, str] = {
    "space": "science",
    "biology": "science",
    "philosophy": "english_language_arts",
    "economics": "life_skills",
    "engineering": "technology",
    "art": "arts",
    "psychology": "life_skills",
    "language": "world_languages",
}


def _interest_seed_slugs(interests: list[str], grade_level: str) -> list[str]:
    """Map user interest tags + grade level to age-appropriate starter topic slugs."""
    normalized_level = _GRADE_LEVEL_ALIASES.get(grade_level, grade_level)
    level_map = GRADE_LEVEL_TOPIC_MAP.get(normalized_level) or GRADE_LEVEL_TOPIC_MAP["high_school"]
    slugs: list[str] = []
    seen: set[str] = set()
    for tag in interests:
        normalized_tag = _INTEREST_ALIASES.get(tag.lower().strip(), tag.lower().strip())
        for slug in level_map.get(normalized_tag, []):
            if slug not in seen:
                seen.add(slug)
                slugs.append(slug)
    return slugs


def _seed_topics_bg(slugs: list[str], difficulty: str) -> None:
    """Background: upsert topic rows and run pipeline for any with no clips yet."""
    from app.agents.pipeline_agent import run_pipeline
    db = get_client()
    for slug in slugs:
        name = slug.replace("-", " ").title()
        try:
            existing = db.table("topics").select("slug").eq("slug", slug).execute()
            if not existing.data:
                db.table("topics").insert({
                    "slug": slug,
                    "name": name,
                    "difficulty": difficulty,
                    "prerequisites": [],
                }).execute()
        except Exception as exc:
            logger.warning(f"[feed] Failed to upsert seed topic {slug}: {exc}")
            continue
        try:
            clips = db.table("clips").select("id").eq("topic_slug", slug).limit(1).execute()
            if not clips.data:
                run_pipeline(slug, name)
                logger.info(f"[feed] seeded pipeline for interest topic={slug}")
        except Exception as exc:
            logger.warning(f"[feed] Failed to seed pipeline for {slug}: {exc}")


@router.get("/discover/{user_id}", response_model=list[Clip])
async def get_discover_feed(user_id: str, background_tasks: BackgroundTasks, limit: int = Query(20, le=50), caller_id: str = Depends(require_user)):
    if caller_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    db = get_client()

    # Single query: user profile with accumulated vectors
    try:
        profile = db.table("user_profiles").select("interests, taste_vector, interest_vector, grade_level").eq("user_id", user_id).limit(1).execute()
        p = profile.data[0] if profile.data else {}
    except Exception as e:
        logger.warning(f"[feed] Failed to fetch user_profiles for user={user_id}: {e}")
        p = {}
    interests: list[str] = p.get("interests") or []
    taste_vector = _parse_vector(p.get("taste_vector"))
    user_interest_vector: dict[str, float] = p.get("interest_vector") or {}
    grade_level: str = p.get("grade_level") or "high_school"

    # Build seen_ids from all sessions — single batched query
    seen_ids: set[str] = set()
    try:
        paths = db.table("learning_paths").select("session_id").eq("user_id", user_id).execute()
        session_ids = [r["session_id"] for r in paths.data]
        if session_ids:
            events = db.table("clip_events").select("clip_id").in_("session_id", session_ids).execute()
            seen_ids = {e["clip_id"] for e in events.data}
    except Exception as e:
        logger.warning(f"[feed] Failed to build seen_ids for user={user_id}: {e}")

    try:
        all_topics = db.table("topics").select("slug").execute()
        all_slugs = [t["slug"] for t in all_topics.data]
    except Exception as e:
        logger.error(f"[feed] Failed to fetch topics for discover user={user_id}: {e}")
        return []
    # Cold start: no taste signal yet — seed interest-aligned topics and prefer them for discovery
    if taste_vector is None and interests:
        difficulty = _GRADE_DIFFICULTY.get(grade_level, "intermediate")
        seed_slugs = _interest_seed_slugs(interests, grade_level)
        if seed_slugs:
            background_tasks.add_task(_seed_topics_bg, seed_slugs, difficulty)
            existing_seed = [s for s in seed_slugs if s in all_slugs]
            relevant_slugs = existing_seed[:10] if existing_seed else _match_interest_slugs(interests, all_slugs)
        else:
            relevant_slugs = _match_interest_slugs(interests, all_slugs)
    else:
        relevant_slugs = _match_interest_slugs(interests, all_slugs, taste_vector=taste_vector)

    clips = _fetch_discover_clips(db, relevant_slugs, all_slugs, seen_ids, limit, interest_vector=user_interest_vector, taste_vector=taste_vector)

    # Global fallback: seed topics are still generating — return best available clips from any topic.
    # Over-fetch so we still surface UNSEEN clips for returning users who've already watched the
    # top-hook_score ones (otherwise they get an empty feed and the UI hangs).
    if len(clips) < limit:
        _DISCOVER_COLS = "id,topic_slug,title,description,video_url,thumbnail_url,duration_seconds,source_url,source_platform,hook_score,created_at,embedding"
        already = {c.id for c in clips}
        try:
            fallback = (
                db.table("clips")
                .select(_DISCOVER_COLS)
                .order("hook_score", desc=True)
                .limit(limit * 5)
                .execute()
            )
            for row in fallback.data:
                if len(clips) >= limit:
                    break
                if row["id"] not in seen_ids and row["id"] not in already:
                    row.setdefault("hook_score", 0.5)
                    clips.append(Clip(**row))
        except Exception as e:
            logger.warning(f"[feed] Global fallback query failed for user={user_id}: {e}")

    return clips


@router.post("/{clip_id}/events", status_code=204)
@limiter.limit("120/minute")
async def record_clip_event(request: Request, clip_id: str, event: ClipEvent, caller_id: str = Depends(require_user)):
    db = get_client()
    try:
        db.table("clip_events").insert({
            "clip_id": clip_id,
            "session_id": event.session_id,
            "watch_ms": event.watch_ms,
            "completed": event.completed,
            "replay_count": event.replay_count,
        }).execute()
    except Exception as e:
        logger.warning(f"Failed to record event for clip {clip_id}: {e}")
        return

    if event.session_id:
        try:
            clip = db.table("clips").select("topic_slug, embedding, duration_seconds").eq("id", clip_id).limit(1).execute()
        except Exception as e:
            logger.warning(f"[feed] Failed to fetch clip {clip_id} for event: {e}")
            return
        if clip.data:
            raw_emb = _parse_vector(clip.data[0].get("embedding"))
            try:
                path = db.table("learning_paths").select("user_id").eq("session_id", event.session_id).limit(1).execute()
                user_id = path.data[0].get("user_id") if path.data else None
            except Exception as e:
                logger.warning(f"[feed] Failed to fetch user_id for session={event.session_id}: {e}")
                user_id = None
            if user_id and user_id != caller_id:
                logger.warning(f"[feed] session ownership mismatch: caller={caller_id} session_owner={user_id}")
                return
            _update_interest_vector(
                db, event.session_id, clip.data[0]["topic_slug"],
                event.completed, event.replay_count, event.feedback,
                clip_embedding=raw_emb,
                user_id=user_id,
                watch_ms=event.watch_ms,
                duration_seconds=clip.data[0].get("duration_seconds"),
            )
