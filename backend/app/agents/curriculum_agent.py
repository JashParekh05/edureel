"""LangGraph agent: multi-step learning path generation with intent understanding + validation."""
import json
import uuid
import logging
from typing import TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, END
from app.models.schemas import Topic, LearningPath

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"


class CurriculumState(TypedDict):
    query: str
    session_id: str
    intent: dict                  # {goal, level, domain, specific_concepts}
    curated_topics: list[dict]
    raw_path: dict                # LLM output before validation
    learning_path: LearningPath | None
    errors: Annotated[list[str], operator.add]
    suggested_start_index: int


def _groq():
    import os
    from openai import OpenAI
    return OpenAI(api_key=os.environ["OPENAI_API_KEY"])


def _node_understand_intent(state: CurriculumState) -> dict:
    """Classify the user's learning goal before generating a curriculum."""
    client = _groq()
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=256,
        messages=[
            {
                "role": "system",
                "content": "Extract the learner's intent from their query. Return JSON only.",
            },
            {
                "role": "user",
                "content": f"""Query: "{state['query']}"

Return JSON:
{{
  "goal": "what they want to achieve (one sentence)",
  "level": "beginner|intermediate|advanced",
  "domain": "broad subject area",
  "specific_concepts": ["list", "of", "specific", "things", "they", "mentioned"]
}}""",
            },
        ],
    )
    raw = response.choices[0].message.content.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        intent = json.loads(raw.strip())
    except Exception:
        intent = {"goal": state["query"], "level": "beginner", "domain": "general", "specific_concepts": []}
    logger.info(f"[curriculum_agent] intent: {intent}")
    return {"intent": intent}


def _build_familiarity_prompt(intent: dict, topics: list[Topic]) -> str | None:
    level = intent.get("level", "beginner")
    if level == "beginner" or len(topics) <= 1:
        return None
    idx = 1 if level == "intermediate" else min(2, len(topics) - 1)
    return f"You seem familiar with the basics. Want to start at \"{topics[idx].name}\"?"


def _node_assess_familiarity(state: CurriculumState) -> dict:
    level = state["intent"].get("level", "beginner")
    index_map = {"beginner": 0, "intermediate": 1, "advanced": 2}
    return {"suggested_start_index": index_map.get(level, 0)}


def _node_load_curated(state: CurriculumState) -> dict:
    from app.services.llm import _curated_topics
    return {"curated_topics": _curated_topics()}


def _node_build_curriculum(state: CurriculumState) -> dict:
    """Generate the ordered learning path using intent + curated library."""
    client = _groq()
    intent = state["intent"]
    curated = state["curated_topics"]

    # Only inject the curated library when the query is in a domain that overlaps with it.
    # The library is CS/ML/math — injecting it for unrelated domains (law, history, etc.)
    # causes the LLM to hallucinate spurious connections and return wrong topics.
    CS_DOMAINS = {"computer science", "programming", "mathematics", "machine learning",
                  "data science", "software engineering", "algorithms", "statistics"}
    domain = intent.get("domain", "").lower()
    library_relevant = any(kw in domain for kw in CS_DOMAINS)

    curated_block = (
        "\n\nExisting topic library (REUSE these slugs when semantically applicable — only if the concept is an EXACT match):\n"
        + json.dumps(curated, indent=2)
        if curated and library_relevant else ""
    )

    specific_concepts = intent.get('specific_concepts', [])
    system = f"""You are a curriculum designer for an educational short-form video platform.
The learner's goal: {intent.get('goal', state['query'])}
Their level: {intent.get('level', 'beginner')}
Domain: {intent.get('domain', 'general')}
Specific concepts they mentioned: {specific_concepts if specific_concepts else 'see goal above'}

Generate a sequential learning path of exactly 3 topics that builds from foundations up to exactly what they asked about.
Think of each topic as one chapter — narrow enough that a single 5-10 minute YouTube video can cover it well.
Keep it to 3 — more topics are added automatically as the learner progresses, so do not pad the path.

Rules:
- Start with the prerequisite context the learner needs, end with the specific thing they asked about
- Do NOT use broad survey topics (bad: "Introduction to American History"). Be specific (good: "Articles of Confederation Weaknesses")
- Each topic must directly build on the previous one — no tangents, no parallel alternatives
- The final 1-2 topics should be the learner's exact goal at the appropriate depth

Order topics from foundational to advanced (prerequisites first).
A library of pre-built topics exists. Only reuse a slug if the topic is EXACTLY the same concept.
If the user asks about something specific (e.g. a framework, tool, or historical event not in the library), create a new accurate slug.
Slugs must be lowercase with hyphens. Always return valid JSON."""

    schema = """
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
}"""

    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=2048,
        messages=[
            {"role": "system", "content": system},
            {
                "role": "user",
                "content": f"User wants to learn: {state['query']}{curated_block}\n\nReturn JSON matching this schema:\n{schema}",
            },
        ],
    )

    raw = response.choices[0].message.content.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        data = json.loads(raw.strip())
        return {"raw_path": data}
    except Exception as exc:
        return {"raw_path": {}, "errors": [f"JSON parse failed: {exc}"]}


def _node_validate_path(state: CurriculumState) -> dict:
    """Validate and assemble the LearningPath object. Falls back gracefully on errors."""
    data = state["raw_path"]
    if not data or "topics" not in data:
        return {"errors": ["Empty or invalid path from LLM"], "learning_path": None}

    try:
        topics = [Topic(**t) for t in data["topics"]]
        # Validate slug uniqueness
        slugs = [t.slug for t in topics]
        if len(slugs) != len(set(slugs)):
            # deduplicate
            seen: set[str] = set()
            unique = []
            for t in topics:
                if t.slug not in seen:
                    seen.add(t.slug)
                    unique.append(t)
            topics = unique

        path = LearningPath(
            session_id=state["session_id"],
            user_query=state["query"],
            topics=topics,
            summary=data.get("summary", ""),
            familiarity_prompt=_build_familiarity_prompt(state["intent"], topics),
            suggested_start_index=state.get("suggested_start_index", 0),
        )
        logger.info(f"[curriculum_agent] path validated: {len(topics)} topics")
        return {"learning_path": path}
    except Exception as exc:
        return {"errors": [f"Validation failed: {exc}"], "learning_path": None}


def build_curriculum_graph() -> StateGraph:
    g = StateGraph(CurriculumState)
    g.add_node("understand_intent", _node_understand_intent)
    g.add_node("assess_familiarity", _node_assess_familiarity)
    g.add_node("load_curated", _node_load_curated)
    g.add_node("build_curriculum", _node_build_curriculum)
    g.add_node("validate_path", _node_validate_path)
    g.set_entry_point("understand_intent")
    g.add_edge("understand_intent", "assess_familiarity")
    g.add_edge("assess_familiarity", "load_curated")
    g.add_edge("load_curated", "build_curriculum")
    g.add_edge("build_curriculum", "validate_path")
    g.add_edge("validate_path", END)
    return g.compile()


_curriculum_graph = None


def run_curriculum(query: str, session_id: str | None = None) -> LearningPath:
    """Run multi-step curriculum generation. Returns a LearningPath."""
    global _curriculum_graph
    if _curriculum_graph is None:
        _curriculum_graph = build_curriculum_graph()

    sid = session_id or str(uuid.uuid4())
    result = _curriculum_graph.invoke({
        "query": query,
        "session_id": sid,
        "intent": {},
        "curated_topics": [],
        "raw_path": {},
        "learning_path": None,
        "errors": [],
        "suggested_start_index": 0,
    })

    if result["learning_path"] is None:
        # Hard fallback to single-step generation
        logger.warning(f"[curriculum_agent] falling back to direct llm call: {result['errors']}")
        from app.services.llm import parse_learning_path
        return parse_learning_path(query, sid)

    return result["learning_path"]
