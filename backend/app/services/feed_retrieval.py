"""Clip retrieval from the DB for path and discover feeds."""
import random
import logging

from app.models.schemas import Clip
from app.services.feed_scoring import _get_clip_population_stats, _compute_scores, _spread_by_source

logger = logging.getLogger(__name__)

_DISCOVER_COLS = "id,topic_slug,title,description,video_url,thumbnail_url,duration_seconds,source_url,source_platform,hook_score,created_at,embedding"


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
