import asyncio
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from app.models.schemas import TopicRequest, LearningPath
from app.db.supabase import get_client
from app.auth import require_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/topics", tags=["topics"])


async def _process_topic(topic_slug: str, topic_name: str) -> None:
    from app.agents.pipeline_agent import run_pipeline
    try:
        await asyncio.to_thread(run_pipeline, topic_slug, topic_name)
    except Exception as e:
        logger.error(f"[topics] Background pipeline failed for topic={topic_slug}: {e}")


@router.post("/", response_model=LearningPath)
async def create_learning_path(req: TopicRequest, background_tasks: BackgroundTasks, caller_id: str = Depends(require_user)):
    from app.agents.curriculum_agent import run_curriculum

    if req.user_id and req.user_id != caller_id:
        raise HTTPException(status_code=403, detail="Access denied")

    logger.info(f"[topics] Creating learning path for query='{req.query[:80]}' user={req.user_id}")

    try:
        path = run_curriculum(req.query)
    except Exception as e:
        logger.error(f"[topics] Curriculum agent failed for query='{req.query[:80]}': {e}")
        raise HTTPException(status_code=502, detail="Failed to generate learning path. Please try again.")

    db = get_client()

    try:
        db.table("learning_paths").insert(
            {
                "session_id": path.session_id,
                "user_query": path.user_query,
                "topic_slugs": [t.slug for t in path.topics],
                "user_id": req.user_id,
            }
        ).execute()
    except Exception as e:
        logger.error(f"[topics] Failed to insert learning_path session={path.session_id}: {e}")
        # Don't block the user — path is still usable even if DB write fails

    for topic in path.topics:
        try:
            existing = (
                db.table("topics")
                .select("slug")
                .eq("slug", topic.slug)
                .execute()
            )
            if not existing.data:
                db.table("topics").insert(
                    {
                        "slug": topic.slug,
                        "name": topic.name,
                        "difficulty": topic.difficulty,
                        "prerequisites": topic.prerequisites,
                    }
                ).execute()
        except Exception as e:
            logger.warning(f"[topics] Failed to upsert topic={topic.slug}: {e}")

        background_tasks.add_task(_process_topic, topic.slug, topic.name)
        logger.info(f"[topics] Queued pipeline for topic='{topic.slug}'")

    return path


@router.get("/history/{user_id}")
async def get_user_history(user_id: str, caller_id: str = Depends(require_user)):
    if caller_id != user_id:
        raise HTTPException(status_code=403, detail="Access denied")
    db = get_client()
    try:
        rows = (
            db.table("learning_paths")
            .select("session_id, user_query, topic_slugs, created_at")
            .eq("user_id", user_id)
            .order("created_at", desc=True)
            .limit(10)
            .execute()
        )
    except Exception as e:
        logger.error(f"[topics] Failed to fetch history for user={user_id}: {e}")
        return []

    return [
        {
            "session_id": r["session_id"],
            "user_query": r["user_query"],
            "topic_slugs": r["topic_slugs"] or [],
            "topic_count": len(r["topic_slugs"] or []),
            "created_at": r["created_at"],
        }
        for r in rows.data
    ]
