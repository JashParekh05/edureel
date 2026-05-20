"""
Bulk-seed clips from a CSV file of (topic-slug, url) pairs.

Usage:
    cd backend
    python -m scripts.bulk_seed                       # uses seed/bulk_urls.csv
    python -m scripts.bulk_seed seed/my_urls.csv      # custom path

CSV format (no header):
    topic-slug,https://www.youtube.com/watch?v=XXXXX
    other-topic,https://youtu.be/YYYYY

Behavior:
    - Skips (slug, url) pairs already processed (checkpoint: <csv>.seen.txt)
    - Skips pairs already present in DB (topic_slug + source_url match)
    - Auto-creates topic rows for new slugs (difficulty=beginner, no prereqs)
    - Sleeps 3s between successful videos to avoid YouTube IP rate-limit
    - Logs progress every 10 videos
    - Resume-safe: re-run after crash; it picks up where it left off
"""
import sys
import time
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

from app.db.supabase import get_client
from app.services.pipeline import process_video

logger = logging.getLogger("bulk_seed")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

DEFAULT_CSV = Path(__file__).resolve().parent.parent / "seed" / "bulk_urls.csv"
SLEEP_BETWEEN = 3  # seconds — gentle on YouTube IP limits


def slug_to_name(slug: str) -> str:
    return " ".join(w.capitalize() for w in slug.split("-"))


def main() -> None:
    csv_path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_CSV
    if not csv_path.exists():
        logger.error(f"CSV not found: {csv_path}")
        sys.exit(1)

    seen_path = csv_path.with_suffix(".seen.txt")
    seen: set[str] = set(seen_path.read_text().splitlines()) if seen_path.exists() else set()
    if seen:
        logger.info(f"Resuming — {len(seen)} pairs already processed")

    # Parse CSV
    rows: list[tuple[str, str]] = []
    for line in csv_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",", 1)
        if len(parts) != 2:
            logger.warning(f"Skipping malformed line: {line[:60]}")
            continue
        rows.append((parts[0].strip(), parts[1].strip()))

    logger.info(f"Loaded {len(rows)} (slug, url) pairs from {csv_path.name}")

    db = get_client()
    ensured_topics: set[str] = set()
    inserted_clips = 0
    skipped = 0
    failed = 0

    def mark_seen(pair_key: str) -> None:
        seen.add(pair_key)
        seen_path.write_text("\n".join(sorted(seen)))

    for i, (slug, url) in enumerate(rows, start=1):
        pair_key = f"{slug}\t{url}"

        if pair_key in seen:
            skipped += 1
            continue

        # DB-level dedupe — in case seen file got wiped but clips already exist
        try:
            existing = db.table("clips").select("id").eq("topic_slug", slug).eq("source_url", url).limit(1).execute()
        except Exception as e:
            logger.warning(f"DB dedupe check failed for ({slug}, {url}): {e}")
            failed += 1
            continue
        if existing.data:
            mark_seen(pair_key)
            skipped += 1
            continue

        # Auto-create topic row if it doesn't exist (cached per run)
        if slug not in ensured_topics:
            topic_check = db.table("topics").select("slug").eq("slug", slug).execute()
            if not topic_check.data:
                db.table("topics").insert({
                    "slug": slug,
                    "name": slug_to_name(slug),
                    "difficulty": "beginner",
                    "prerequisites": [],
                }).execute()
                logger.info(f"Created topic '{slug}'")
            ensured_topics.add(slug)

        # Run the pipeline
        try:
            clips = process_video(url, slug)
        except Exception as e:
            logger.warning(f"[{slug}] {url} pipeline failed: {e}")
            mark_seen(pair_key)  # don't retry on next run; user can edit .seen.txt to retry
            failed += 1
            continue

        if not clips:
            logger.warning(f"[{slug}] {url} produced 0 clips (likely dead URL or no captions)")
            mark_seen(pair_key)
            failed += 1
            continue

        for clip in clips:
            try:
                db.table("clips").insert(clip).execute()
                inserted_clips += 1
            except Exception as e:
                logger.warning(f"[{slug}] clip insert failed: {e}")

        mark_seen(pair_key)

        if i % 10 == 0:
            logger.info(
                f"Progress: {i}/{len(rows)} | inserted={inserted_clips} skipped={skipped} failed={failed}"
            )

        time.sleep(SLEEP_BETWEEN)

    logger.info(
        f"Done. Total pairs: {len(rows)} | inserted clips: {inserted_clips} | skipped: {skipped} | failed: {failed}"
    )


if __name__ == "__main__":
    main()
