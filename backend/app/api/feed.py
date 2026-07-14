import asyncio
import logging
from datetime import datetime, timezone
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, Request
from app.rate_limit import limiter
from app.models.schemas import (
    Clip,
    ClipEvent,
    FeedResponse,
    TopicRecommendation,
    DiscoverResponse,
    CheckpointCard,
    FeedLevel,
)
from app.db.supabase import get_client
from app.auth import require_user

from app.services.checkpoint_placement import place_checkpoints
from app.services.remediation import RewatchClip, clips_to_rewatch, DEFAULT_MAX_REWATCH

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
    select_topup_topics,
    _topup_discover_fresh,
    _GRADE_DIFFICULTY,
)
from app.services.path_extension import _should_extend, _extend_path, _LOW_CLIPS_THRESHOLD
from app.services.level_filter import (
    derive_content_level,
    rank_by_level,
    exclude_below,
    fallback_order,
)
from app.services import self_heal_state, self_heal_store
from app.services.telemetry import build_impressions
from app.services.impression_store import record_impressions
from app.services.topic_expansion import (
    _is_expansion_candidate,
    _should_expand_topic,
    _expand_topic,
    EXPAND_WHEN_UNSEEN_AT_OR_BELOW,
    ENGAGED_COMPLETION,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/feed", tags=["feed"])


def _schedule_impressions(
    background_tasks: BackgroundTasks,
    clips: list[Clip],
    *,
    feed_surface: str,
    session_id: str | None,
    user_id: str | None,
) -> None:
    """Best-effort, non-blocking serve hook: build Impressions for the served,
    ordered clip list and schedule the write as a BackgroundTask.

    Wraps the whole build + schedule in try/except so that building or scheduling
    Impressions can NEVER affect the already-prepared feed response — the feed is
    returned regardless of any telemetry failure (Req 2.1, 2.2, 2.3). The serve
    time is captured here as `datetime.now(timezone.utc)` and injected into the
    pure `build_impressions` core, and the actual insert runs after the response
    is sent via `record_impressions` (Req 1.1).
    """
    try:
        impressions = build_impressions(
            clips,
            feed_surface=feed_surface,
            session_id=session_id,
            user_id=user_id,
            served_at=datetime.now(timezone.utc),
        )
        if impressions:
            background_tasks.add_task(record_impressions, impressions)
    except Exception as e:
        logger.warning(f"[feed] Failed to schedule impressions for surface={feed_surface}: {e}")


def _deserialize_levels(payload) -> list[FeedLevel]:
    """Best-effort: turn the persisted ``learning_paths.levels`` jsonb projection
    into a list of :class:`FeedLevel`.

    Returns ``[]`` when the payload is null / empty / malformed so the caller can
    fall back to a single implicit level (legacy behavior). Never raises
    (Req 4.3, 4.4): a malformed row degrades to the implicit-level fallback rather
    than breaking the feed.
    """
    if not payload or not isinstance(payload, list):
        return []
    levels: list[FeedLevel] = []
    try:
        for item in payload:
            if not isinstance(item, dict):
                continue
            slugs = item.get("topic_slugs")
            if not isinstance(slugs, list):
                continue
            levels.append(
                FeedLevel(
                    ordinal=int(item.get("ordinal", len(levels) + 1)),
                    name=str(item.get("name") or f"Level {len(levels) + 1}"),
                    topic_slugs=[str(s) for s in slugs],
                )
            )
    except Exception as e:
        logger.warning(f"[feed] Failed to deserialize learning_paths.levels: {e}")
        return []
    return levels


def _implicit_levels(topic_order: list[str]) -> list[FeedLevel]:
    """A NULL / absent ``learning_paths.levels`` renders a single implicit level
    over all topics in feed order (Req 5.3, legacy single-level behavior)."""
    return [FeedLevel(ordinal=1, name="Foundations", topic_slugs=list(topic_order))]


# Columns needed to both render a rewatch suggestion clip and feed the
# Remediation_Select ordering (role_ordinal / final_score / section_index).
_REWATCH_COLS = (
    "id,topic_slug,title,description,video_url,thumbnail_url,duration_seconds,"
    "source_url,source_platform,hook_score,final_score,created_at,section_index,"
    "role_ordinal"
)


def _select_rewatch_clips(
    db,
    session_id: str,
    topic_slug: str,
    section_index: int,
    max_clips: int = DEFAULT_MAX_REWATCH,
) -> list[Clip]:
    """Soft remediation loader: pick the learner's already-seen clips for the weak
    beat to recommend rewatching (Remediation_Select shell, Req 3.2, 4.2, 4.3).

    Loads the clips the learner has seen in this session (from ``clip_events``)
    that belong to ``topic_slug`` and the weak beat ``section_index``, maps them
    to the pure :class:`~app.services.remediation.RewatchClip`, and lets
    :func:`~app.services.remediation.clips_to_rewatch` choose and order the
    suggestion (Canonical_Arc order: role_ordinal asc / None last, final_score
    desc, clip_id asc). The chosen ids are then returned as full :class:`Clip`
    objects in that order for the end-card.

    Best-effort and off the request path (Req 4.3): any read failure -- including
    the columns/filters not being present -- degrades to an empty list rather than
    raising, and an empty result simply means there is nothing to suggest. This is
    a soft suggestion only; it never blocks the learner from advancing (Req 4.2).
    """
    # Already-seen clips for this session.
    try:
        events = (
            db.table("clip_events")
            .select("clip_id")
            .eq("session_id", session_id)
            .execute()
        )
        seen_ids = sorted({e["clip_id"] for e in (events.data or []) if e.get("clip_id")})
    except Exception as e:
        logger.warning(f"[feed] remediation: failed to load seen clips for session={session_id}: {e}")
        return []
    if not seen_ids:
        return []

    # Of those, keep only the clips on the weak beat of the weak topic.
    try:
        rows = (
            db.table("clips")
            .select(_REWATCH_COLS)
            .in_("id", seen_ids)
            .eq("topic_slug", topic_slug)
            .eq("section_index", section_index)
            .execute()
        )
    except Exception as e:
        logger.warning(
            f"[feed] remediation: failed to load weak-beat clips for "
            f"topic={topic_slug} beat={section_index}: {e}"
        )
        return []

    clips_by_id: dict[str, Clip] = {}
    candidates: list[RewatchClip] = []
    for row in (rows.data or []):
        row.setdefault("hook_score", 0.5)
        try:
            clip = Clip(**row)
        except Exception as e:
            logger.warning(f"[feed] remediation: skipping malformed clip row: {e}")
            continue
        if clip.section_index is None:
            continue
        clips_by_id[clip.id] = clip
        candidates.append(
            RewatchClip(
                clip_id=clip.id,
                section_index=clip.section_index,
                role_ordinal=clip.role_ordinal,
                final_score=clip.final_score if clip.final_score is not None else clip.hook_score,
            )
        )

    # Pure core decides selection + order; never raises.
    ordered = clips_to_rewatch(section_index, candidates, max_clips=max_clips)
    return [clips_by_id[rc.clip_id] for rc in ordered if rc.clip_id in clips_by_id]


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

    # Leveled feed (Phase 1, Req 1.1/4.2): read the serialized LeveledPath
    # projection. Done as a SEPARATE best-effort query so a missing `levels`
    # column (operator-run migration not yet applied) degrades to a single
    # implicit level instead of breaking the main path read (Req 4.3, 5.3).
    levels_payload = None
    try:
        lv = (
            db.table("learning_paths")
            .select("levels")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
        if lv.data:
            levels_payload = lv.data[0].get("levels")
    except Exception as e:
        logger.warning(f"[feed] Failed to fetch learning_paths.levels for session={session_id} (column may be absent): {e}")
        levels_payload = None

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
    expand_slugs: list[str] = []   # engaged topics running low on unseen clips
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

        # Struggling on this topic: prefer shorter clips, but WITHIN each beat —
        # keep the narrative arc intact rather than flattening to global duration.
        if completion_rate < 0.3 and slug in topic_completion:
            clips = sorted(
                clips,
                key=lambda c: (c.section_index if c.section_index is not None else 1_000_000,
                               c.duration_seconds or 999),
            )

        clips = _transcript_boost(clips, user_query)

        # A topic is "processing" while its pipeline is still running — not just
        # when it has zero clips. Sections generate sequentially, so a topic can
        # have a few clips while more are still on the way; reporting it done too
        # early makes the frontend stop polling and the user gets stuck.
        is_generating = slug in generating_slugs
        attempts, last_age = self_heal_store.read(slug)
        has_clips = bool(clips)
        if self_heal_state.should_self_heal(has_clips, is_generating, attempts, last_age):
            missing_slugs.append(slug)

        # Endless expansion: an engaged viewer running low on unseen clips for
        # this topic gets fresh angles on the same subject generated ahead of
        # the drop-off, so the subject feels bottomless. The throttle check is
        # last so it only marks a cooldown when the topic is actually eligible.
        if (_is_expansion_candidate(len(clips), completion_rate, is_generating)
                and _should_expand_topic(session_id, slug)):
            expand_slugs.append(slug)

        failed = self_heal_state.is_terminal_failed(has_clips, is_generating, attempts)
        feeds.append(FeedResponse(
            topic_slug=slug,
            clips=clips,
            processing=is_generating or (not has_clips and not failed),
            failed=failed,
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

    # Endless expansion: queue fresh angles for engaged topics low on clips.
    if expand_slugs:
        names: dict[str, str] = {}
        try:
            rows = db.table("topics").select("slug,name").in_("slug", expand_slugs).execute()
            names = {r["slug"]: r["name"] for r in rows.data}
        except Exception as e:
            logger.warning(f"[feed] expansion name lookup failed: {e}")
        for slug in expand_slugs:
            name = names.get(slug) or slug.replace("-", " ").title()
            background_tasks.add_task(_expand_topic, slug, name)
            logger.info(f"[feed] queued topic expansion for engaged topic='{slug}'")

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
        deduped_feeds.append(FeedResponse(topic_slug=f.topic_slug, clips=unique, processing=f.processing, failed=f.failed))

    # Auto-extend the path when user is running low on unseen clips.
    # Skip if any topic is still processing — pipelines may still deliver clips.
    total_unseen = sum(len(f.clips) for f in deduped_feeds)
    still_processing = any(f.processing for f in deduped_feeds)
    if total_unseen < _LOW_CLIPS_THRESHOLD and not still_processing and _should_extend(session_id):
        background_tasks.add_task(_extend_path, session_id)
        logger.info(f"[feed] session={session_id} low on clips ({total_unseen}); queued path extension")

    # Serve hook (best-effort, non-blocking): the interleaved feeds are the final
    # ordered set the learner sees. _interleave_topics returns FeedResponse objects,
    # so flatten to the ordered Clip sequence for Feed_Position assignment. The
    # session-ownership 403 check above already gates this; user_id is the resolved
    # path owner.
    served = _interleave_topics(deduped_feeds)
    ordered_clips = [clip for feed in served for clip in feed.clips]
    _schedule_impressions(
        background_tasks,
        ordered_clips,
        feed_surface="learn_path",
        session_id=session_id,
        user_id=path.data[0].get("user_id"),
    )

    # Leveled feed + inline soft checkpoints (Checkpoint_Placement shell, Req 1.5,
    # 4.2, 4.3). Computed AFTER interleave/dedupe so every card's
    # `after_clip_index` aligns with the FINAL per-topic served clip list. The
    # LeveledPath projection (or a single implicit level when absent) is attached
    # so the frontend can render the Level -> Topic -> Beat stepper. Best-effort:
    # a failed placement yields no cards for that topic and the feed still serves
    # its clips normally; nothing here ever hard-locks or fails the request path.
    feed_levels = _deserialize_levels(levels_payload) or _implicit_levels(
        [f.topic_slug for f in served]
    )
    final_feeds: list[FeedResponse] = []
    for f in served:
        checkpoints: list[CheckpointCard] = []
        try:
            cards = place_checkpoints(
                [c.section_index if c.section_index is not None else -1 for c in f.clips],
                f.topic_slug,
            )
            checkpoints = [
                CheckpointCard(
                    stage=card.stage,
                    after_clip_index=card.after_clip_index,
                    topic_slug=card.topic_slug,
                    section_index=card.section_index,
                    skippable=card.skippable,
                )
                for card in cards
            ]
        except Exception as e:
            logger.warning(f"[feed] checkpoint placement failed for topic={f.topic_slug}: {e}")
            checkpoints = []
        final_feeds.append(
            FeedResponse(
                topic_slug=f.topic_slug,
                clips=f.clips,
                processing=f.processing,
                failed=f.failed,
                checkpoints=checkpoints,
                levels=feed_levels,
            )
        )
    return final_feeds


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


@router.get("/remediation/{session_id}", response_model=list[Clip])
async def get_remediation(
    session_id: str,
    topic_slug: str = Query(..., min_length=1, max_length=120),
    section_index: int = Query(..., ge=0, le=3),
    caller_id: str = Depends(require_user),
):
    """Soft "rewatch these clips" suggestion for a failed checkpoint's weak beat
    (Remediation_Select shell, Req 3.2, 4.2, 4.3).

    Given the learner's session, the weak topic, and the weak beat
    (``section_index``) from a failed ``check``/``post`` checkpoint, returns the
    small ordered list of already-seen clips on that beat to recommend rewatching.
    Owner-only and best-effort, exactly like the other feed endpoints: the caller
    may only read their own session (else 403), and any computation failure
    degrades to an empty list rather than raising. This is a soft suggestion
    surfaced on the end-card -- it never blocks the learner from advancing
    (Req 4.2)."""
    db = get_client()
    try:
        path = (
            db.table("learning_paths")
            .select("user_id")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.warning(f"[feed] remediation: failed to fetch learning_path for session={session_id}: {e}")
        return []
    if not path.data:
        return []
    if path.data[0].get("user_id") and path.data[0]["user_id"] != caller_id:
        raise HTTPException(status_code=403, detail="Access denied")

    return await asyncio.to_thread(
        _select_rewatch_clips, db, session_id, topic_slug, section_index
    )


@router.get("/clip/{clip_id}", response_model=Clip)
async def get_clip(clip_id: str, caller_id: str = Depends(require_user)):
    """Single-clip metadata source for refresh-on-return: return current metadata
    for exactly one clip. Auth via require_user like every other feed route. 404
    when the clip no longer exists so the client surfaces the unavailable state.

    Declared BEFORE `/{topic_slug}` so the literal `clip/` segment is never
    captured as a topic slug.
    """
    db = get_client()
    try:
        result = (
            db.table("clips")
            .select("id,topic_slug,title,description,video_url,thumbnail_url,duration_seconds,source_url,source_platform,hook_score,created_at,section_index")
            .eq("id", clip_id)
            .limit(1)
            .execute()
        )
    except Exception as e:
        logger.error(f"[feed] Failed to fetch clip {clip_id}: {e}")
        raise HTTPException(status_code=503, detail="Clip lookup failed")
    if not result.data:
        raise HTTPException(status_code=404, detail="Clip not found")
    row = result.data[0]
    row.setdefault("hook_score", 0.5)
    return Clip(**row)


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


@router.get("/discover/{user_id}", response_model=DiscoverResponse)
async def get_discover_feed(user_id: str, background_tasks: BackgroundTasks, limit: int = Query(20, le=50), exclude: str | None = None, caller_id: str = Depends(require_user)):
    # Trust the authenticated caller as the source of truth; never 403 on a
    # path-param mismatch (which silently broke the feed for anonymous/guest
    # sessions whose token sub differs from a stale path param). Self-lookup only.
    user_id = caller_id
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

    # Cold start: no behavioral taste yet — derive a taste vector from the user's
    # onboarding interests so discovery is personalized from interests alone
    # (semantic ranking + semantic scoring), no watch history required.
    if taste_vector is None and interests:
        from app.services.embeddings import embed_text
        taste_vector = embed_text(" ".join(interests))

    # Per_User_Topup: ALWAYS schedule the existing _seed_topics_bg path as a
    # background task so the library keeps growing from grade-aligned interest
    # seeds. It is never awaited on the request path and so can never block or
    # hang the Discover response; shortfalls in the served feed are filled now
    # via the Level_Filter soft fallback below and topped up later by this seed.
    if interests:
        seed_slugs = _interest_seed_slugs(interests, grade_level)
        if seed_slugs:
            background_tasks.add_task(_seed_topics_bg, seed_slugs, _GRADE_DIFFICULTY.get(grade_level, "intermediate"))

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
    # Discover/topic-feed events carry no session, only user_id — without this
    # branch a returning user gets re-served everything they watched on
    # Discover. Separate try: degrades to session-only until the user_id
    # column migration has run.
    try:
        user_events = db.table("clip_events").select("clip_id").eq("user_id", user_id).execute()
        seen_ids |= {e["clip_id"] for e in user_events.data}
    except Exception as e:
        logger.warning(f"[feed] Failed to build user-level seen_ids for user={user_id}: {e}")

    # Merge client-known in-session clip ids so successive load-more calls never
    # re-return clips already on screen (their telemetry may not be flushed yet).
    if exclude:
        seen_ids.update(cid for cid in exclude.split(",") if cid)

    try:
        all_topics = db.table("topics").select("slug").execute()
        all_slugs = [t["slug"] for t in all_topics.data]
    except Exception as e:
        logger.error(f"[feed] Failed to fetch topics for discover user={user_id}: {e}")
        return DiscoverResponse(clips=[], processing=True)
    # Restrict discovery to topics that actually have clips, so the feed is both
    # relevant AND populated. Grade-map seed slugs are mostly empty placeholders,
    # which is why discovery used to fall back to generic top-hook clips.
    try:
        clip_rows = db.table("clips").select("topic_slug").limit(2000).execute().data
        populated = {r["topic_slug"] for r in clip_rows}
        candidate_slugs = [s for s in all_slugs if s in populated] or all_slugs
    except Exception as e:
        logger.warning(f"[feed] Failed to fetch populated slugs for discover user={user_id}: {e}")
        candidate_slugs = all_slugs

    relevant_slugs = _match_interest_slugs(interests, candidate_slugs, taste_vector=taste_vector)

    clips = _fetch_discover_clips(db, relevant_slugs, candidate_slugs, seen_ids, limit, interest_vector=user_interest_vector, taste_vector=taste_vector)

    # Signal-driven fresh generation (rides the fail-closed multi-project quota
    # pool): top up the user's top taste-ranked topics with NEW clips from fresh
    # searches, so Discover keeps feeling fresh instead of recycling the library.
    # Gated on interests (like the seed top-up) so we never spend quota without a
    # signal; WHICH topics is taste-ranked, re-targeting as the vectors move.
    # Aggressiveness knobs: 4 topics x 2 distinct angles = up to 8 fresh searches
    # per load (quota-bounded; fails closed when the day's pool is spent).
    if interests:
        topup_slugs = select_topup_topics(relevant_slugs, user_interest_vector, max_topics=4)
        if topup_slugs:
            background_tasks.add_task(
                _topup_discover_fresh, topup_slugs, _GRADE_DIFFICULTY.get(grade_level, "intermediate"), 2
            )

    # Level-aware ranking (stage 2): the user's exact Content_Level leads, with
    # below-level clips dropped while a match exists. rank_by_level is a stable
    # sort, so the personalized order from _fetch_discover_clips is preserved
    # WITHIN each level group — level dominates, personalization is the tiebreak.
    user_level = derive_content_level(grade_level)
    clips = exclude_below(clips, user_level)
    clips = rank_by_level(clips, user_level)

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
            # Build the best-available candidate pool of UNSEEN clips, then order
            # by level distance (nearest-higher then nearest-lower) instead of
            # hook_score so a mis-leveled clip never leads the fallback feed.
            candidates: list[Clip] = []
            for row in fallback.data:
                if row["id"] not in seen_ids and row["id"] not in already:
                    row.setdefault("hook_score", 0.5)
                    candidates.append(Clip(**row))
            for clip in fallback_order(candidates, user_level):
                if len(clips) >= limit:
                    break
                clips.append(clip)
        except Exception as e:
            logger.warning(f"[feed] Global fallback query failed for user={user_id}: {e}")

    # Instant_Feed envelope: an empty feed signals the library has no level/
    # interest match yet and the background Per_User_Topup is generating content,
    # so the client can show a processing state instead of hanging (Req 5.6).
    _schedule_impressions(
        background_tasks,
        clips,
        feed_surface="discover",
        session_id=None,
        user_id=user_id,
    )
    return DiscoverResponse(clips=clips, processing=len(clips) == 0)


@router.get("/guest/progress")
async def get_guest_progress(caller_id: str = Depends(require_user)):
    """Server-side guest clip count for the signup gate, keyed on the
    (anonymous) auth user_id. Fail-open: returns 0 if the table/RPC is absent so
    the client falls back to its localStorage counter (migration_guest_progress.sql).
    Two-segment path avoids the dynamic /{topic_slug} and /{clip_id}/events routes."""
    db = get_client()
    try:
        res = db.table("guest_progress").select("clips_watched").eq("user_id", caller_id).limit(1).execute()
        return {"clips_watched": (res.data[0]["clips_watched"] if res.data else 0)}
    except Exception as e:
        logger.warning(f"[feed] guest_progress read failed for {caller_id}: {e}")
        return {"clips_watched": 0}


@router.post("/guest/increment")
async def increment_guest_progress(caller_id: str = Depends(require_user)):
    """Atomically increment + return the caller's guest clip count. Fail-open."""
    db = get_client()
    try:
        res = db.rpc("increment_guest_clips", {"p_user_id": caller_id}).execute()
        return {"clips_watched": int(res.data) if res.data is not None else 0}
    except Exception as e:
        logger.warning(f"[feed] increment_guest_clips failed for {caller_id}: {e}")
        return {"clips_watched": 0}


@router.post("/{clip_id}/events", status_code=204)
@limiter.limit("120/minute")
async def record_clip_event(request: Request, clip_id: str, event: ClipEvent, caller_id: str = Depends(require_user)):
    db = get_client()

    # Authorize the session BEFORE writing anything. A caller may only record
    # events against a session they own; otherwise a forged session_id could be
    # used to pollute another user's telemetry. Resolve the effective user_id
    # here too (session owner for session events, else the caller).
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
            raise HTTPException(status_code=403, detail="Access denied")
        user_id = owner or caller_id

    base_row = {
        "clip_id": clip_id,
        "session_id": event.session_id,
        "watch_ms": event.watch_ms,
        "completed": event.completed,
        "replay_count": event.replay_count,
    }
    try:
        # Persist feedback (fire/check) and the watching user so discover/topic-
        # feed events (which have no session) stay attributable — without user_id
        # the discover feed can't see them as watched and re-serves those clips.
        # Falls back to core columns if the newer columns haven't been migrated
        # yet, so telemetry is never lost.
        db.table("clip_events").insert({**base_row, "feedback": event.feedback, "user_id": user_id}).execute()
    except Exception:
        try:
            db.table("clip_events").insert(base_row).execute()
        except Exception as e:
            logger.warning(f"Failed to record event for clip {clip_id}: {e}")
            return

    # Personalize on every event. Session-feed events update both session- and
    # user-level vectors; topic-feed/discover events have no session but still
    # update the authenticated user's profile.
    try:
        clip = db.table("clips").select("topic_slug, embedding, duration_seconds").eq("id", clip_id).limit(1).execute()
    except Exception as e:
        logger.warning(f"[feed] Failed to fetch clip {clip_id} for event: {e}")
        return
    if not clip.data:
        return

    _update_interest_vector(
        db, event.session_id, clip.data[0]["topic_slug"],
        event.completed, event.replay_count, event.feedback,
        clip_embedding=_parse_vector(clip.data[0].get("embedding")),
        user_id=user_id,
        watch_ms=event.watch_ms,
        duration_seconds=clip.data[0].get("duration_seconds"),
    )
