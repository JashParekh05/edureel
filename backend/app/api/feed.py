import math
import re
import random
import logging
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Query
from app.models.schemas import Clip, ClipEvent, FeedResponse, TopicRecommendation
from app.db.supabase import get_client
from app.services.embeddings import embed_text, cosine_similarity, ema_update

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/feed", tags=["feed"])


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
    events = (
        db.table("clip_events")
        .select("clip_id, watch_ms, completed")
        .eq("session_id", session_id)
        .execute()
    )

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


def _update_interest_vector(db, session_id: str, topic_slug: str, completed: bool, replay_count: int, feedback: str | None = None, clip_embedding: list[float] | None = None, user_id: str | None = None) -> None:
    """Real-time interest vector + taste vector update after a clip event."""
    existing = (
        db.table("session_embeddings")
        .select("interest_vector, taste_vector")
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )
    row = existing.data[0] if existing.data else {}
    vector: dict = row.get("interest_vector") or {}
    taste: list[float] | None = _parse_vector(row.get("taste_vector"))

    if feedback == "want_more":
        delta = 0.6
    elif feedback == "already_know":
        delta = -1.0
    else:
        delta = (0.15 if completed else -0.05) + replay_count * 0.3

    current = float(vector.get(topic_slug, 0.0))
    vector[topic_slug] = round(max(-1.0, min(1.0, current + delta)), 3)

    # Update taste vector via EMA when we have a clip embedding and the event is positive
    update_taste = clip_embedding and delta > 0
    new_taste = taste
    if update_taste:
        if taste and len(taste) == len(clip_embedding):
            new_taste = ema_update(taste, clip_embedding, alpha=0.2)
        else:
            new_taste = clip_embedding

    upsert_data: dict = {
        "session_id": session_id,
        "interest_vector": vector,
        "updated_at": "now()",
    }
    if new_taste is not None:
        upsert_data["taste_vector"] = new_taste

    db.table("session_embeddings").upsert(upsert_data).execute()

    # Merge into user-level profile for cross-session persistence
    if user_id:
        try:
            u_row = db.table("user_profiles").select("interest_vector, taste_vector").eq("user_id", user_id).limit(1).execute()
            u = u_row.data[0] if u_row.data else {}
            u_interest: dict = u.get("interest_vector") or {}
            u_taste = _parse_vector(u.get("taste_vector"))

            u_current = float(u_interest.get(topic_slug, 0.0))
            u_interest[topic_slug] = round(max(-1.0, min(1.0, u_current + delta * 0.5)), 3)

            user_upsert: dict = {"user_id": user_id, "interest_vector": u_interest}
            if new_taste is not None:
                if u_taste and len(u_taste) == len(new_taste):
                    user_upsert["taste_vector"] = ema_update(u_taste, new_taste, alpha=0.1)
                else:
                    user_upsert["taste_vector"] = new_taste

            db.table("user_profiles").upsert(user_upsert).execute()
        except Exception as e:
            logger.warning(f"Failed to update user-level vectors for {user_id}: {e}")


# ---------------------------------------------------------------------------
# Multi-signal scoring (PLE-inspired)
# ---------------------------------------------------------------------------

def _get_clip_population_stats(db, clip_ids: list[str]) -> dict[str, float]:
    """Population-level completion rate per clip across all sessions."""
    if not clip_ids:
        return {}
    rows = (
        db.table("clip_events")
        .select("clip_id, completed")
        .in_("clip_id", clip_ids)
        .execute()
    )
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

        clip.hook_score = round(
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
        clip.hook_score = round(min(1.0, clip.hook_score + 0.15 * matches / len(keywords)), 4)
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

def _fetch_clips_for_slug(
    db,
    slug: str,
    seen_ids: set[str] | None = None,
    limit: int = 20,
    user_avg_watch_seconds: float | None = None,
    interest_vector: dict[str, float] | None = None,
    taste_vector: list[float] | None = None,
) -> list[Clip]:
    result = (
        db.table("clips")
        .select("*")
        .eq("topic_slug", slug)
        .order("created_at", desc=False)
        .limit(limit)
        .execute()
    )
    clips = []
    for row in result.data:
        if seen_ids and row["id"] in seen_ids:
            continue
        row.setdefault("hook_score", 0.5)
        clips.append(Clip(**row))

    clip_ids = [c.id for c in clips]
    pop_stats = _get_clip_population_stats(db, clip_ids)
    clips = _compute_scores(clips, pop_stats, user_avg_watch_seconds, interest_vector, taste_vector)
    return sorted(clips, key=lambda c: c.hook_score, reverse=True)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/path/{session_id}", response_model=list[FeedResponse])
async def get_path_feed(session_id: str):
    db = get_client()
    path = (
        db.table("learning_paths")
        .select("topic_slugs, user_query")
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )
    if not path.data:
        return []

    user_query = path.data[0].get("user_query", "")
    seen_ids, topic_completion = _get_session_telemetry(db, session_id)

    # User's typical engagement length from completed clips
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

    # Live interest vector + taste vector for personalized re-ranking
    iv_res = (
        db.table("session_embeddings")
        .select("interest_vector, taste_vector")
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )
    if iv_res.data:
        iv_row = iv_res.data[0]
        interest_vector: dict[str, float] = iv_row.get("interest_vector") or {}
        taste_vector: list[float] | None = _parse_vector(iv_row.get("taste_vector"))
    else:
        # New session — seed from user-level profile
        interest_vector = {}
        taste_vector = None
        path_user = db.table("learning_paths").select("user_id").eq("session_id", session_id).limit(1).execute()
        if path_user.data and path_user.data[0].get("user_id"):
            uid = path_user.data[0]["user_id"]
            up = db.table("user_profiles").select("taste_vector, interest_vector").eq("user_id", uid).limit(1).execute()
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

    return _interleave_topics(feeds)


@router.get("/recommendations/{session_id}", response_model=list[TopicRecommendation])
async def get_recommendations(session_id: str):
    db = get_client()
    path = (
        db.table("learning_paths")
        .select("topic_slugs")
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )
    if not path.data:
        return []
    path_slugs = path.data[0]["topic_slugs"]

    from app.agents.recommendation_agent import run_recommendations
    return await asyncio.to_thread(run_recommendations, session_id, path_slugs)


@router.get("/{topic_slug}", response_model=FeedResponse)
async def get_feed(
    topic_slug: str,
    offset: int = Query(0, ge=0),
    limit: int = Query(10, ge=1, le=50),
):
    db = get_client()
    result = (
        db.table("clips")
        .select("*")
        .eq("topic_slug", topic_slug)
        .order("created_at", desc=False)
        .range(offset, offset + limit - 1)
        .execute()
    )
    clips = []
    for row in result.data:
        row.setdefault("hook_score", 0.5)
        clips.append(Clip(**row))
    clip_ids = [c.id for c in clips]
    pop_stats = _get_clip_population_stats(db, clip_ids)
    clips = _compute_scores(clips, pop_stats, None)
    clips = sorted(clips, key=lambda c: c.hook_score, reverse=True)
    return FeedResponse(topic_slug=topic_slug, clips=clips, processing=len(clips) == 0)


def _match_interest_slugs(interests: list[str], all_slugs: list[str], taste_vector: list[float] | None = None) -> list[str]:
    """Return topic slugs relevant to the user's interests.

    Uses semantic similarity when taste_vector is available, otherwise falls
    back to keyword overlap.
    """
    if not interests and taste_vector is None:
        return all_slugs[:10]

    # Semantic path: embed each slug and rank by cosine similarity to taste
    if taste_vector is not None:
        from app.services.embeddings import embed_texts, cosine_similarity
        slug_texts = [s.replace("-", " ") for s in all_slugs]
        slug_embeddings = embed_texts(slug_texts)
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
    for slug in relevant_slugs[:5]:
        result = db.table("clips").select("*").eq("topic_slug", slug).limit(6).execute()
        for row in result.data:
            if row["id"] not in seen_ids and len(clips) < relevant_limit:
                row.setdefault("hook_score", 0.5)
                clips.append(Clip(**row))

    # Diversity fill from other slugs
    other_slugs = [s for s in all_slugs if s not in relevant_slugs]
    random.shuffle(other_slugs)
    for slug in other_slugs[:8]:
        result = db.table("clips").select("*").eq("topic_slug", slug).limit(3).execute()
        for row in result.data:
            if row["id"] not in seen_ids and len(clips) < limit:
                row.setdefault("hook_score", 0.5)
                clips.append(Clip(**row))

    clip_ids = [c.id for c in clips]
    pop_stats = _get_clip_population_stats(db, clip_ids)
    clips = _compute_scores(clips, pop_stats, None, interest_vector=interest_vector, taste_vector=taste_vector)
    random.shuffle(clips)
    return clips[:limit]


@router.get("/discover/{user_id}", response_model=list[Clip])
async def get_discover_feed(user_id: str, limit: int = Query(20, le=50)):
    db = get_client()

    # Single query: user profile with accumulated vectors
    profile = db.table("user_profiles").select("interests, taste_vector, interest_vector").eq("user_id", user_id).limit(1).execute()
    p = profile.data[0] if profile.data else {}
    interests: list[str] = p.get("interests") or []
    taste_vector = _parse_vector(p.get("taste_vector"))
    user_interest_vector: dict[str, float] = p.get("interest_vector") or {}

    # Build seen_ids from all sessions — single batched query
    paths = db.table("learning_paths").select("session_id").eq("user_id", user_id).execute()
    session_ids = [p["session_id"] for p in paths.data]
    seen_ids: set[str] = set()
    if session_ids:
        events = db.table("clip_events").select("clip_id").in_("session_id", session_ids).execute()
        seen_ids = {e["clip_id"] for e in events.data}

    all_topics = db.table("topics").select("slug").execute()
    all_slugs = [t["slug"] for t in all_topics.data]
    relevant_slugs = _match_interest_slugs(interests, all_slugs, taste_vector=taste_vector)

    return _fetch_discover_clips(db, relevant_slugs, all_slugs, seen_ids, limit, interest_vector=user_interest_vector, taste_vector=taste_vector)


@router.post("/{clip_id}/events", status_code=204)
async def record_clip_event(clip_id: str, event: ClipEvent):
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
        clip = db.table("clips").select("topic_slug, embedding").eq("id", clip_id).limit(1).execute()
        if clip.data:
            raw_emb = _parse_vector(clip.data[0].get("embedding"))
            path = db.table("learning_paths").select("user_id").eq("session_id", event.session_id).limit(1).execute()
            user_id = path.data[0].get("user_id") if path.data else None
            _update_interest_vector(
                db, event.session_id, clip.data[0]["topic_slug"],
                event.completed, event.replay_count, event.feedback,
                clip_embedding=raw_emb,
                user_id=user_id,
            )
