"""Shared test fixtures. Run from the backend/ dir: `python -m pytest`."""
import itertools
import pytest

from app.models.schemas import Clip

_counter = itertools.count()


def make_clip(**overrides) -> Clip:
    """Build a Clip with sane defaults; override any field per test."""
    n = next(_counter)
    base = {
        "id": f"clip-{n}",
        "topic_slug": "binary-search",
        "title": f"Clip {n}",
        "video_url": "https://example.com/v",
        "hook_score": 0.5,
    }
    base.update(overrides)
    return Clip(**base)


@pytest.fixture
def clip_factory():
    return make_clip
