"""Routing-quality diagnostic. Pipeline metadata ONLY — never sends the user
query to an LLM, never prints/embeds the raw query (only a hash + redaction).

Given a learning-path session id (or a stored query value), prints:
  - normalized query hash + redacted query
  - generated topic slugs/names (the stored roadmap)
  - resolver input/output + similarity scores per topic
  - whether a specific topic resolved into a broader/generic topic
  - retrieval candidate slugs + selected clip topic slugs
  - clip counts per slug

Goal: tell whether a bad result came from (a) topic generation going too broad,
(b) the resolver merging a specific topic into a generic one, or (c) weak
library coverage.

Usage (from backend/):
    python -m scripts.diagnose_routing <session_id>
"""
import sys
import hashlib

from dotenv import load_dotenv
load_dotenv()

from app.db.supabase import get_client
from app.services import topic_resolver as tr
from app.services.embeddings import embed_text, cosine_similarity

# Filler words that don't change a topic's core concept.
_FILLER = {
    "introduction", "intro", "fundamentals", "basics", "basic", "explained",
    "overview", "guide", "understanding", "the", "a", "an", "of", "to", "and",
    "for", "with", "in", "on", "what", "is", "are", "how", "101",
}


def _core_tokens(text: str) -> set[str]:
    import re
    toks = {t for t in re.findall(r"[a-z0-9]+", text.lower()) if len(t) > 2}
    return toks - _FILLER


def _redact(q: str) -> str:
    return f"<{len(q)} chars, starts '{q[:2]}…'>" if q else "<empty>"


def _clip_count(db, slug: str) -> int:
    try:
        rows = db.table("clips").select("id").eq("topic_slug", slug).limit(500).execute().data
        return len(rows)
    except Exception:
        return -1


def diagnose(session_id: str) -> None:
    db = get_client()
    path = (
        db.table("learning_paths")
        .select("user_query, topic_slugs")
        .eq("session_id", session_id)
        .limit(1)
        .execute()
    )
    if not path.data:
        print(f"no learning_path for session_id={session_id}")
        return
    query = path.data[0].get("user_query") or ""
    slugs = path.data[0].get("topic_slugs") or []

    print("=" * 70)
    print(f"session_id     : {session_id}")
    print(f"query_hash     : {hashlib.sha256(query.encode()).hexdigest()[:12]}")
    print(f"query_redacted : {_redact(query)}")
    print(f"path slugs ({len(slugs)}): {slugs}")
    print("=" * 70)

    # Pull topic display names for the stored slugs
    names = {}
    try:
        rows = db.table("topics").select("slug,name").in_("slug", slugs).execute().data
        names = {r["slug"]: r.get("name") or r["slug"] for r in rows}
    except Exception:
        pass

    index = tr._get_index()

    for slug in slugs:
        name = names.get(slug, slug.replace("-", " ").title())
        qv = embed_text(name)
        # Top-3 resolver candidates by cosine (excluding self)
        scored = sorted(
            ((cosine_similarity(qv, e), s) for s, _, e in index if s != slug),
            reverse=True,
        )[:3]
        chosen = scored[0] if scored and scored[0][0] >= tr.SIMILARITY_THRESHOLD else None

        print(f"\n[{slug}]  name={name!r}  clips={_clip_count(db, slug)}")
        print(f"  top matches: " + ", ".join(f"{s}={sc:.3f}" for sc, s in scored))
        if chosen:
            cs, cslug = chosen
            in_core = _core_tokens(name)
            match_core = _core_tokens(cslug)
            overlap = in_core & match_core
            # Specific → generic: matched concept is a strict generalization
            # (its core tokens are a subset of the input's) with no extra signal.
            generic = bool(match_core) and match_core < in_core
            verdict = "MERGE"
            if not overlap:
                verdict = "BLOCK (cosine-only, no lexical overlap → likely specific→generic drift)"
            elif generic and not (match_core == in_core):
                verdict = "REVIEW (matched is a generalization of input)"
            print(f"  → would merge into '{cslug}' (cos={cs:.3f}); "
                  f"core_overlap={sorted(overlap)}  verdict={verdict}")
        else:
            print("  → no merge (below threshold) — served as its own topic")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python -m scripts.diagnose_routing <session_id>")
        sys.exit(1)
    diagnose(sys.argv[1])
