"""Multi-signal clip scoring, ordering, and shared vector/telemetry utilities.

Base layer of the feed stack — depends only on embeddings + schemas, never on
other feed modules, so it can be imported anywhere without cycles.
"""
import math
import re
import logging
from datetime import datetime, timezone

from app.models.schemas import Clip, FeedResponse
from app.services.embeddings import cosine_similarity

logger = logging.getLogger(__name__)


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
