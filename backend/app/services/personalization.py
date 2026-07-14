"""Session/user telemetry reads and interest+taste vector updates."""
import logging

from app.services.embeddings import ema_update, ema_repel
from app.services.feed_scoring import _parse_vector

logger = logging.getLogger(__name__)

# Taste-learning tuning. Higher α = faster adaptation (raised from the prior
# 0.2/0.1 so the feed learns a solo user's taste quickly). A signal must exceed
# _SIGNAL_EPS in magnitude to move the taste vector (attract on +, repel on −).
_SIGNAL_EPS = 0.05
_TASTE_ALPHA_SESSION = 0.3
_TASTE_ALPHA_USER = 0.2


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
        try:
            clips_res = db.table("clips").select("id, topic_slug").in_("id", clip_ids).execute()
            slug_lookup = {c["id"]: c["topic_slug"] for c in clips_res.data}
        except Exception as e:
            # Degrade to seen_ids-only rather than 500ing the whole path feed.
            logger.warning(f"[feed] Failed to fetch clip slugs for session={session_id}: {e}")

    for ev in events.data:
        slug = slug_lookup.get(ev["clip_id"])
        if slug:
            topic_watches.setdefault(slug, []).append(ev["completed"])

    topic_completion = {
        slug: sum(completions) / len(completions)
        for slug, completions in topic_watches.items()
    }
    return seen_ids, topic_completion


def _event_delta(
    completed: bool,
    replay_count: int,
    feedback: str | None = None,
    watch_ms: int = 0,
    duration_seconds: int | None = None,
) -> float:
    """Interest delta for a single clip event. Pure so it's unit-testable.

    Precedence: explicit feedback > completion > skip penalty.

    Skip velocity matters: bailing in <10% of a clip is a much stronger 'no'
    than watching most of it. Replays on a SKIP can mitigate the penalty (the
    viewer came back) but never flip it positive — abandoning a clip is not a
    'want more this topic' signal, so the skip branch is capped at 0.
    """
    if feedback == "want_more":
        return 0.6
    if feedback == "already_know":
        return -1.0
    if completed:
        return round(0.15 + replay_count * 0.3, 4)

    duration_s = max(1.0, float(duration_seconds or 60))
    watch_ratio = (watch_ms or 0) / 1000.0 / duration_s
    if watch_ratio < 0.1:        # bailed almost instantly
        base = -0.30
    elif watch_ratio < 0.4:      # casual skip
        base = -0.10
    else:                        # watched most of it
        base = -0.02
    return round(min(0.0, base + replay_count * 0.3), 4)


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
    # Session-level taste is only available when this event belongs to a path
    # session. Topic-feed / discover events have no session — they still
    # personalize at the user level below.
    taste: list[float] | None = None
    if session_id:
        existing = (
            db.table("session_embeddings")
            .select("taste_vector")
            .eq("session_id", session_id)
            .limit(1)
            .execute()
        )
        row = existing.data[0] if existing.data else {}
        taste = _parse_vector(row.get("taste_vector"))

    delta = _event_delta(completed, replay_count, feedback, watch_ms, duration_seconds)

    # Atomic interest vector update via RPC (prevents concurrent-write clobber).
    # Session-level only — skipped for topic-feed/discover events with no session.
    if session_id:
        try:
            db.rpc("merge_session_interest", {
                "p_session_id": session_id,
                "p_topic_slug": topic_slug,
                "p_delta": round(delta, 4),
            }).execute()
        except Exception as e:
            logger.warning(f"[feed] Failed to merge session interest for session={session_id}: {e}")

    # Taste vector learns from BOTH directions (negative semantic learning):
    # attract toward liked clips, repel away from disliked ones. Repel strength
    # scales with how strong the 'no' was, capped so one skip can't whipsaw it.
    new_taste = taste
    if clip_embedding is not None and abs(delta) > _SIGNAL_EPS:
        if taste and len(taste) == len(clip_embedding):
            if delta > 0:
                new_taste = ema_update(taste, clip_embedding, alpha=_TASTE_ALPHA_SESSION)
            else:
                new_taste = ema_repel(taste, clip_embedding, alpha=min(0.25, abs(delta) * 0.2))
        elif delta > 0:
            # No prior taste yet — seed only from a LIKED clip, never a dislike.
            new_taste = clip_embedding

    if new_taste is not None and session_id:
        try:
            db.table("session_embeddings").upsert({
                "session_id": session_id,
                "taste_vector": new_taste,
                "updated_at": "now()",
            }).execute()
        except Exception as e:
            logger.warning(f"[feed] Failed to upsert taste_vector for session={session_id}: {e}")

    # Merge into user-level profile for cross-session persistence.
    if user_id:
        try:
            db.rpc("merge_user_interest", {
                "p_user_id": user_id,
                "p_topic_slug": topic_slug,
                "p_delta": round(delta * 0.5, 4),
            }).execute()
        except Exception as e:
            logger.warning(f"Failed to update user-level interest for {user_id}: {e}")

        # Semantic taste: attract toward liked clips (atomic via RPC), repel away
        # from disliked ones. Repel reads the user's current taste and pushes it
        # away — so discover events (which carry no session) also learn negatives.
        if clip_embedding is not None and delta > _SIGNAL_EPS:
            try:
                db.rpc("merge_user_taste", {
                    "p_user_id": user_id,
                    "p_new_taste": clip_embedding,
                    "p_alpha": _TASTE_ALPHA_USER,
                }).execute()
            except Exception as e:
                logger.warning(f"Failed to update taste_vector for {user_id}: {e}")
        elif clip_embedding is not None and delta < -_SIGNAL_EPS:
            try:
                prof = (
                    db.table("user_profiles")
                    .select("taste_vector")
                    .eq("user_id", user_id)
                    .limit(1)
                    .execute()
                )
                cur = _parse_vector(prof.data[0].get("taste_vector")) if prof.data else None
                if cur and len(cur) == len(clip_embedding):
                    repelled = ema_repel(cur, clip_embedding, alpha=min(0.25, abs(delta) * 0.2))
                    db.table("user_profiles").upsert({
                        "user_id": user_id,
                        "taste_vector": repelled,
                    }).execute()
            except Exception as e:
                logger.warning(f"Failed to repel taste_vector for {user_id}: {e}")
