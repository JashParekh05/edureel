import asyncio
import logging
from fastapi import APIRouter, BackgroundTasks
from app.models.schemas import TopicRequest, LearningPath
from app.db.supabase import get_client

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/topics", tags=["topics"])


async def _process_topic(topic_slug: str, topic_name: str) -> None:
    from app.agents.pipeline_agent import run_pipeline
    await asyncio.to_thread(run_pipeline, topic_slug, topic_name)


@router.post("/", response_model=LearningPath)
async def create_learning_path(req: TopicRequest, background_tasks: BackgroundTasks):
    from app.agents.curriculum_agent import run_curriculum
    path = run_curriculum(req.query)

    db = get_client()
    db.table("learning_paths").insert(
        {
            "session_id": path.session_id,
            "user_query": path.user_query,
            "topic_slugs": [t.slug for t in path.topics],
            "user_id": req.user_id,
        }
    ).execute()

    for topic in path.topics:
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

        background_tasks.add_task(_process_topic, topic.slug, topic.name)
        logger.info(f"[YouTube API] Queued search for '{topic.slug}' (~101 units)")

    return path


@router.get("/history/{user_id}")
async def get_user_history(user_id: str):
    db = get_client()
    rows = (
        db.table("learning_paths")
        .select("session_id, user_query, topic_slugs, created_at")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(10)
        .execute()
    )
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
