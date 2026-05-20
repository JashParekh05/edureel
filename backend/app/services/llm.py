import os
import json
import uuid
import logging
from pathlib import Path
from openai import OpenAI
from app.models.schemas import Topic, LearningPath

logger = logging.getLogger(__name__)

_client: OpenAI | None = None
_curated_cache: list[dict] | None = None

MODEL = "gpt-4o-mini"
CURATED_PATH = Path(__file__).resolve().parent.parent.parent / "seed" / "curated_topics.json"


def get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


def _curated_topics() -> list[dict]:
    """Load curated topic list once. Used to bias slug naming toward existing seeded topics."""
    global _curated_cache
    if _curated_cache is None:
        try:
            data = json.loads(CURATED_PATH.read_text())
            _curated_cache = [
                {"slug": slug, "name": meta["name"], "difficulty": meta.get("difficulty", "beginner")}
                for slug, meta in data["topics"].items()
            ]
        except Exception:
            _curated_cache = []
    return _curated_cache


SYSTEM_PROMPT = """You are a curriculum designer for an educational short-form video platform.
When a user describes what they want to learn, you:
1. Extract specific topics from their query
2. Order them from foundational to advanced (prerequisites first)
3. Assign difficulty levels
4. Return a structured JSON learning path

A library of pre-built topics exists. Only reuse a slug from this library if the user's query is asking about EXACTLY that topic. If the user is asking about something different (e.g. a specific framework, tool, or concept not in the library), create a new accurate slug. Never force-fit a query into an existing slug if it's not a genuine match.

Always return valid JSON matching the schema exactly. Slugs must be lowercase with hyphens.
"""

TOPIC_SCHEMA = """
{
  "summary": "one sentence describing the learning path",
  "topics": [
    {
      "slug": "topic-slug",
      "name": "Human Readable Name",
      "difficulty": "beginner|intermediate|advanced",
      "prerequisites": ["slug-of-prereq"],
      "rationale": "why this topic is ordered here"
    }
  ]
}
"""


def parse_learning_path(query: str, session_id: str | None = None) -> LearningPath:
    client = get_client()
    sid = session_id or str(uuid.uuid4())

    curated = _curated_topics()
    curated_block = (
        "\n\nExisting topic library (REUSE these slugs when semantically applicable):\n"
        + json.dumps(curated, indent=2)
        if curated else ""
    )

    logger.info(f"[LLM] Generating learning path for query='{query[:80]}'")
    try:
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=1024,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": f"User wants to learn: {query}{curated_block}\n\nReturn JSON matching this schema:\n{TOPIC_SCHEMA}",
                },
            ],
        )
    except Exception as e:
        logger.error(f"[LLM] OpenAI API call failed for query='{query[:80]}': {e}")
        raise

    raw = response.choices[0].message.content
    logger.debug(f"[LLM] Raw response length={len(raw)}")

    # Strip markdown code fences if present
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        logger.error(f"[LLM] Failed to parse JSON response: {e} | raw={raw[:200]}")
        raise

    try:
        topics = [Topic(**t) for t in data["topics"]]
    except (KeyError, TypeError) as e:
        logger.error(f"[LLM] Unexpected response shape: {e} | keys={list(data.keys())}")
        raise

    logger.info(f"[LLM] Generated {len(topics)} topics for session={sid}")
    return LearningPath(
        session_id=sid,
        user_query=query,
        topics=topics,
        summary=data.get("summary", ""),
    )
