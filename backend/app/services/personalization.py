"""Session/user telemetry reads and interest+taste vector updates."""
import logging

from app.services.embeddings import ema_update
from app.services.feed_scoring import _parse_vector

logger = logging.getLogger(__name__)


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

    # Update taste vector via EMA when we have a clip embedding and the event is positive
    update_taste = clip_embedding and delta > 0
    new_taste = taste
    if update_taste:
        if taste and len(taste) == len(clip_embedding):
            new_taste = ema_update(taste, clip_embedding, alpha=0.2)
        else:
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
