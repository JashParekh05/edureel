import asyncio
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from app.rate_limit import limiter
from app.models.schemas import Clip, ClipEvent, FeedResponse, TopicRecommendation
from app.db.supabase import get_client
from app.auth import require_user

from app.services.feed_scoring import (
    _parse_vector,
    _get_clip_population_stats,
    _compute_scores,
    _transcript_boost,
    _interleave_topics,
)
from app.services.feed_retrieval import _fetch_clips_for_slug, _fetch_discover_clips, _DISCOVER_COLS
from app.services.personalization import _get_session_telemetry, _update_interest_vector
from app.services.discover_seeding import (
    _interest_seed_slugs,
    _match_interest_slugs,
    _seed_topics_bg,
    _GRADE_DIFFICULTY,
)
from app.services.path_extension import _should_extend, _extend_path, _LOW_CLIPS_THRESHOLD

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/feed", tags=["feed"])


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

    from app.api.topics import generating_slugs, _process_single_topic

    feeds = []
    missing_slugs: list[str] = []  # topics with no clips that aren't already generating
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

        # A topic is "processing" while its pipeline is still running — not just
        # when it has zero clips. Sections generate sequentially, so a topic can
        # have a few clips while more are still on the way; reporting it done too
        # early makes the frontend stop polling and the user gets stuck.
        is_generating = slug in generating_slugs
        if not clips and not is_generating:
            missing_slugs.append(slug)

        feeds.append(FeedResponse(
            topic_slug=slug,
            clips=clips,
            processing=is_generating or len(clips) == 0,
        ))

    # Self-heal: if a topic has no clips and nothing is generating it (e.g. the
    # original background task was lost on a server restart / OOM), kick off its
    # pipeline now. Marking the slug immediately prevents duplicate triggers on
    # the next poll. This is why entering a topic now reliably generates clips
    # without the user having to leave and come back.
    if missing_slugs:
        slug_names: dict[str, str] = {}
        try:
            rows = db.table("topics").select("slug,name").in_("slug", missing_slugs).execute()
            slug_names = {r["slug"]: r["name"] for r in rows.data}
        except Exception as e:
            logger.warning(f"[feed] self-heal name lookup failed: {e}")
        for slug in missing_slugs:
            generating_slugs.add(slug)
            name = slug_names.get(slug) or slug.replace("-", " ").title()
            background_tasks.add_task(_process_single_topic, slug, name)
            logger.info(f"[feed] self-heal: triggered generation for empty topic='{slug}'")

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
    base_row = {
        "clip_id": clip_id,
        "session_id": event.session_id,
        "watch_ms": event.watch_ms,
        "completed": event.completed,
        "replay_count": event.replay_count,
    }
    try:
        # Persist feedback (🔥/✓) so it survives as history, not just as a live
        # vector nudge. Falls back to core columns if the `feedback` column hasn't
        # been migrated yet, so telemetry is never lost.
        db.table("clip_events").insert({**base_row, "feedback": event.feedback}).execute()
    except Exception:
        try:
            db.table("clip_events").insert(base_row).execute()
        except Exception as e:
            logger.warning(f"Failed to record event for clip {clip_id}: {e}")
            return

    # Personalize on every event. Session-feed events update both session- and
    # user-level vectors; topic-feed/discover events have no session but still
    # update the authenticated user's profile (previously they were dropped —
    # ~half of all telemetry).
    try:
        clip = db.table("clips").select("topic_slug, embedding, duration_seconds").eq("id", clip_id).limit(1).execute()
    except Exception as e:
        logger.warning(f"[feed] Failed to fetch clip {clip_id} for event: {e}")
        return
    if not clip.data:
        return

    user_id = caller_id
    if event.session_id:
        try:
            path = db.table("learning_paths").select("user_id").eq("session_id", event.session_id).limit(1).execute()
            owner = path.data[0].get("user_id") if path.data else None
        except Exception as e:
            logger.warning(f"[feed] Failed to fetch user_id for session={event.session_id}: {e}")
            owner = None
        if owner and owner != caller_id:
            logger.warning(f"[feed] session ownership mismatch: caller={caller_id} session_owner={owner}")
            return
        user_id = owner or caller_id

    _update_interest_vector(
        db, event.session_id, clip.data[0]["topic_slug"],
        event.completed, event.replay_count, event.feedback,
        clip_embedding=_parse_vector(clip.data[0].get("embedding")),
        user_id=user_id,
        watch_ms=event.watch_ms,
        duration_seconds=clip.data[0].get("duration_seconds"),
    )
