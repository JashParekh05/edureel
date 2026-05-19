"""Lazy-loaded sentence-transformers embeddings service.

Model: all-MiniLM-L6-v2 (384 dimensions, ~80MB download on first use).
Falls back gracefully when sentence-transformers is not installed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np

logger = logging.getLogger(__name__)

_model = None
_available: bool | None = None


def _is_available() -> bool:
    global _available
    if _available is None:
        try:
            import sentence_transformers  # noqa: F401
            _available = True
        except ImportError:
            _available = False
            logger.warning("sentence-transformers not installed; semantic scoring disabled")
    return _available


def get_model():
    global _model
    if _model is None and _is_available():
        from sentence_transformers import SentenceTransformer
        logger.info("Loading all-MiniLM-L6-v2 embedding model…")
        _model = SentenceTransformer("all-MiniLM-L6-v2")
        logger.info("Embedding model loaded")
    return _model


def embed_text(text: str) -> list[float] | None:
    """Return a 384-dim embedding or None if unavailable."""
    model = get_model()
    if model is None or not text:
        return None
    try:
        vec = model.encode(text, normalize_embeddings=True)
        return vec.tolist()
    except Exception as e:
        logger.warning(f"embed_text failed: {e}")
        return None


def embed_texts(texts: list[str]) -> list[list[float] | None]:
    """Batch embed. Returns None entries for empty strings."""
    model = get_model()
    if model is None:
        return [None] * len(texts)
    try:
        vecs = model.encode(texts, normalize_embeddings=True, batch_size=32, show_progress_bar=False)
        return [v.tolist() for v in vecs]
    except Exception as e:
        logger.warning(f"embed_texts failed: {e}")
        return [None] * len(texts)


def cosine_similarity(a: list[float], b: list[float]) -> float:
    """Dot product of two already-normalized vectors = cosine similarity."""
    return sum(x * y for x, y in zip(a, b))


def ema_update(old: list[float], new: list[float], alpha: float = 0.2) -> list[float]:
    """Exponential moving average: (1-alpha)*old + alpha*new, then re-normalize."""
    import math
    updated = [(1 - alpha) * o + alpha * n for o, n in zip(old, new)]
    norm = math.sqrt(sum(x * x for x in updated)) or 1.0
    return [x / norm for x in updated]
