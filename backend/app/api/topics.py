import asyncio
import logging
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request
from app.models.schemas import TopicRequest, LearningPath
from app.db.supabase import get_client
from app.auth import require_user
from app.rate_limit import limiter

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/topics", tags=["topics"])


async def _process_single_topic(slug: str, name: str) -> None:
    from app.agents.pipeline_agent import run_pipeline
    from app.agents.section_planner import plan_and_store_sections
    try:
        sections = await asyncio.to_thread(plan_and_store_sections, slug, name)
    except Exception as e:
        logger.error(f"[topics] Section planning failed for topic={slug}: {e}")
        sections = []

    if sections:
        for i, section in enumerate(sections):
            try:
                await asyncio.to_thread(
                    run_pipeline,
                    slug,
                    name,
                    section["search_query"],
                    section["section_index"],
                    i == 0,  # clear existing clips only before the first section
                )
            except Exception as e:
                logger.error(f"[topics] Pipeline failed for {slug} section {section['section_index']}: {e}")
    else:
        try:
            await asyncio.to_thread(run_pipeline, slug, name)
        except Exception as e:
            logger.error(f"[topics] Background pipeline failed for topic={slug}: {e}")


async def _process_topics_parallel(topics: list[tuple[str, str]]) -> None:
    """Process each topic concurrently; sections within a topic remain sequential."""
    await asyncio.gather(*(_process_single_topic(slug, name) for slug, name in topics))


@router.post("/", response_model=LearningPath)
@limiter.limit("10/minute")
async def create_learning_path(request: Request, req: TopicRequest, background_tasks: BackgroundTasks, caller_id: str = Depends(require_user)):
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

    topics_to_process: list[tuple[str, str]] = []
    for topic in path.topics:
        # Upsert topic row
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

        # Only queue pipeline for topics that don't already have cached clips
        try:
            cached = (
                db.table("clips")
                .select("id")
                .eq("topic_slug", topic.slug)
                .limit(1)
                .execute()
            )
            if cached.data:
                logger.info(f"[topics] Cache hit for topic='{topic.slug}', skipping pipeline")
            else:
                topics_to_process.append((topic.slug, topic.name))
        except Exception as e:
            logger.warning(f"[topics] Cache check failed for {topic.slug}: {e}")
            topics_to_process.append((topic.slug, topic.name))

    if topics_to_process:
        background_tasks.add_task(_process_topics_parallel, topics_to_process)
        logger.info(f"[topics] Queued {len(topics_to_process)} new topics in parallel: {[t[0] for t in topics_to_process]}")
    else:
        logger.info("[topics] All path topics already cached, no pipeline work needed")

    return path


@router.get("/{slug}/sections")
async def get_topic_sections(slug: str, caller_id: str = Depends(require_user)):
    db = get_client()
    try:
        rows = (
            db.table("topic_sections")
            .select("section_index,title,description,search_query")
            .eq("topic_slug", slug)
            .order("section_index")
            .execute()
        )
    except Exception as e:
        logger.error(f"[topics] Failed to fetch sections for {slug}: {e}")
        return []
    return rows.data


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
