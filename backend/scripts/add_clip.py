"""
Add clips for one topic from one (or more) video URLs.
Runs the same pipeline as seed_clips.py but bypasses the JSON — fast one-off seeding.

Usage:
    cd backend
    python -m scripts.add_clip <topic-slug> <youtube-url> [more-urls...]

Examples:
    python -m scripts.add_clip kalman-filters https://www.youtube.com/watch?v=mwn8xhgNpFY
    python -m scripts.add_clip recursion https://youtu.be/abc123 https://youtu.be/def456

Behavior:
    - Creates the topic row if it doesn't exist (difficulty=beginner, no prereqs)
    - Calls process_video() for each URL (Groq segmentation + embeddings)
    - Inserts clips into Supabase (same DB the deployed app reads from)
    - Skips silently if a video URL fails (logs warning)
"""
import sys
import logging
from dotenv import load_dotenv

load_dotenv()

from app.db.supabase import get_client
from app.services.pipeline import process_video

logger = logging.getLogger("add_clip")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")


def slug_to_name(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.split("-"))


def main() -> None:
    if len(sys.argv) < 3:
        print("Usage: python -m scripts.add_clip <topic-slug> <youtube-url> [more-urls...]")
        sys.exit(1)

    slug = sys.argv[1]
    urls = sys.argv[2:]

    db = get_client()

    # Create topic row if missing
    existing_topic = db.table("topics").select("slug").eq("slug", slug).execute()
    if not existing_topic.data:
        db.table("topics").insert({
            "slug": slug,
            "name": slug_to_name(slug),
            "difficulty": "beginner",
            "prerequisites": [],
        }).execute()
        logger.info(f"Created topic '{slug}'")

    total_clips = 0
    for url in urls:
        try:
            clips = process_video(url, slug)
        except Exception as e:
            logger.warning(f"[{slug}] {url} failed: {e}")
            continue

        if not clips:
            logger.warning(f"[{slug}] {url} produced 0 clips (transcript fetch likely failed)")
            continue

        for clip in clips:
            try:
                db.table("clips").insert(clip).execute()
                total_clips += 1
            except Exception as e:
                logger.warning(f"[{slug}] clip insert failed: {e}")

        logger.info(f"[{slug}] inserted {len(clips)} clips from {url}")

    logger.info(f"Done. Topic: {slug}, total clips added: {total_clips}")


if __name__ == "__main__":
    main()
