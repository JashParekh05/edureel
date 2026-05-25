"""Auto-extension of a learning path when the user runs low on unseen clips."""
import time
import asyncio
import logging

from app.db.supabase import get_client

logger = logging.getLogger(__name__)

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
