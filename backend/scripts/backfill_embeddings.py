"""Backfill embeddings for clips that don't have one yet.

Usage:
    cd backend
    python -m scripts.backfill_embeddings
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv()

from app.db.supabase import get_client
from app.services.embeddings import embed_texts

BATCH = 50


def main():
    db = get_client()
    offset = 0
    total_updated = 0

    while True:
        rows = (
            db.table("clips")
            .select("id, title, transcript")
            .is_("embedding", "null")
            .range(offset, offset + BATCH - 1)
            .execute()
        )
        if not rows.data:
            break

        texts = [r.get("transcript") or r.get("title", "") for r in rows.data]
        embeddings = embed_texts(texts)

        for row, emb in zip(rows.data, embeddings):
            if emb is None:
                continue
            db.table("clips").update({"embedding": emb}).eq("id", row["id"]).execute()
            total_updated += 1

        print(f"Updated {total_updated} clips so far…")
        if len(rows.data) < BATCH:
            break
        offset += BATCH

    print(f"Done. {total_updated} clips backfilled.")


if __name__ == "__main__":
    main()
