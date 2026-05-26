"""Similar-topic resolver.

When the curriculum agent invents a fresh slug for a concept we already have
clips for (e.g. "binary-search" vs an existing "binary-search-algorithm"), we'd
otherwise run the whole pipeline again — burning YouTube quota and OpenAI calls
to rebuild content that already exists. This resolves a generated topic to an
existing, already-seeded topic when their names are semantically close, so the
path reuses cached clips instead of regenerating.

Conservative by design: only remaps on a high name-similarity match AND only
when the matched topic actually has clips.
"""
from __future__ import annotations

import logging

from app.services.embeddings import embed_text, embed_texts, cosine_similarity

logger = logging.getLogger(__name__)

# Name-embedding cosine above which two topics are treated as the same concept.
# High on purpose — a wrong merge serves the wrong content, which is worse than
# regenerating.
SIMILARITY_THRESHOLD = 0.84

# Filler words that don't change a topic's core concept, so they shouldn't count
# toward specificity (e.g. "Binary Search Fundamentals" ≈ "binary-search").
_FILLER = {
    "introduction", "intro", "fundamentals", "fundamental", "basics", "basic",
    "explained", "overview", "guide", "understanding", "the", "a", "an", "of",
    "to", "and", "for", "with", "in", "on", "what", "is", "are", "how", "101",
}


def _core_tokens(*texts: str) -> set[str]:
    import re
    toks: set[str] = set()
    for t in texts:
        toks |= {w for w in re.findall(r"[a-z0-9]+", t.lower()) if len(w) > 2}
    return toks - _FILLER


def _is_specificity_drift(input_name: str, input_slug: str, matched_slug: str) -> bool:
    """True when merging would lose specificity — the matched concept is a strict
    generalization of the input (specific→generic parent) or the input is a strict
    generalization of the match (broad parent→specific child). Either is a routing
    error: only same-concept merges (equal or overlapping-but-not-subset) are safe.
    """
    in_core = _core_tokens(input_name, input_slug)
    match_core = _core_tokens(matched_slug)
    if not in_core or not match_core:
        return False  # nothing lexical to judge — defer to the cosine threshold
    # Proper subset in either direction = one is strictly broader than the other.
    return match_core < in_core or in_core < match_core

# (slug, name, embedding) for every known topic. Built once per process.
_index: list[tuple[str, str, list[float]]] | None = None


def _build_index() -> list[tuple[str, str, list[float]]]:
    from app.db.supabase import get_client
    db = get_client()
    try:
        rows = db.table("topics").select("slug,name").limit(5000).execute().data
    except Exception as exc:
        logger.warning(f"[topic_resolver] failed to load topics: {exc}")
        return []
    names = [r.get("name") or r["slug"] for r in rows]
    embs = embed_texts(names)
    return [(r["slug"], r.get("name") or r["slug"], e) for r, e in zip(rows, embs) if e is not None]


def _get_index() -> list[tuple[str, str, list[float]]]:
    global _index
    if _index is None:
        _index = _build_index()
        logger.info(f"[topic_resolver] built index of {len(_index)} topics")
    return _index


def register_topic(slug: str, name: str) -> None:
    """Add a freshly created topic to the in-memory index so later queries in
    the same process can match against it."""
    idx = _get_index()
    if any(s == slug for s, _, _ in idx):
        return
    emb = embed_text(name or slug)
    if emb is not None:
        idx.append((slug, name or slug, emb))


def resolve_topic(slug: str, name: str) -> str | None:
    """Return an existing topic slug (with clips) that means the same thing as
    (slug, name), or None if there's no close match worth reusing."""
    from app.db.supabase import get_client

    qv = embed_text(name or slug)
    if qv is None:
        return None

    best_slug, best_score = None, 0.0
    for s, _, e in _get_index():
        if s == slug:
            continue
        score = cosine_similarity(qv, e)
        if score > best_score:
            best_slug, best_score = s, score

    if not best_slug or best_score < SIMILARITY_THRESHOLD:
        return None

    # Routing guard: don't let a specific topic collapse into a broader generic
    # one (or vice-versa) on cosine alone — that serves the wrong content.
    if _is_specificity_drift(name, slug, best_slug):
        logger.info(f"[topic_resolver] block specificity drift '{slug}' -> '{best_slug}' (sim={best_score:.3f})")
        return None

    # Only reuse if the matched topic actually has clips — otherwise reusing it
    # buys nothing and could point at an empty topic.
    try:
        has_clips = (
            get_client().table("clips").select("id").eq("topic_slug", best_slug).limit(1).execute().data
        )
    except Exception as exc:
        logger.warning(f"[topic_resolver] clip check failed for {best_slug}: {exc}")
        return None
    if not has_clips:
        return None

    logger.info(f"[topic_resolver] reuse '{slug}' -> '{best_slug}' (sim={best_score:.3f})")
    return best_slug
