"""Generate and store 4-section teaching plans per topic.

Each topic gets four ordered sections: hook → what-is-it → how-it-works → outcomes.
Sections are cached in topic_sections — subsequent calls skip the LLM if rows exist.
"""
import json
import logging
import os
from openai import OpenAI

logger = logging.getLogger(__name__)
MODEL = "gpt-4o-mini"


def plan_and_store_sections(
    topic_slug: str,
    topic_name: str,
    difficulty: str = "intermediate",
    path_context: list[str] | None = None,
) -> list[dict]:
    """Generate 4 sections via LLM and store in topic_sections. Returns the section dicts."""
    from app.db.supabase import get_client
    db = get_client()

    existing = (
        db.table("topic_sections")
        .select("section_index,title,description,search_query")
        .eq("topic_slug", topic_slug)
        .order("section_index")
        .execute()
    )
    if existing.data:
        return existing.data

    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    context_line = f"\nTopics already covered: {', '.join(path_context)}" if path_context else ""

    prompt = f"""Topic to teach: "{topic_name}" (difficulty: {difficulty}){context_line}

Generate exactly 4 sections that teach this topic in order:
0. Hook — Why should I care? What surprising or counterintuitive thing will this reveal?
1. What is it — Precise definition and core concept in plain language
2. How it works — The mechanics, key examples, the real substance
3. Outcomes — What does understanding this unlock? Real-world significance

For each section return:
- section_index: 0-3
- title: curiosity-gap phrase, max 8 words
- description: what this section teaches, 1-2 sentences
- search_query: specific YouTube search string to find a 5-10 min video covering exactly this sub-concept (not just the broad topic name)

Return JSON array only:
[{{"section_index": 0, "title": "...", "description": "...", "search_query": "..."}}, ...]"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=1024,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = response.choices[0].message.content.strip()
        if "```" in raw:
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        sections = json.loads(raw.strip())
    except Exception as exc:
        logger.warning(f"[section_planner] LLM call failed for {topic_slug}: {exc}")
        sections = [
            {"section_index": 0, "title": f"Why {topic_name} Matters", "description": "The context and motivation for learning this topic.", "search_query": f"{topic_name} why it matters"},
            {"section_index": 1, "title": f"What Is {topic_name}", "description": "Core definition and key concepts.", "search_query": f"{topic_name} explained simply"},
            {"section_index": 2, "title": f"How {topic_name} Works", "description": "The mechanics and key examples.", "search_query": f"{topic_name} how it works in depth"},
            {"section_index": 3, "title": f"{topic_name} in Practice", "description": "Real-world significance and applications.", "search_query": f"{topic_name} real world examples applications"},
        ]

    stored: list[dict] = []
    for s in sections[:4]:
        row = {
            "topic_slug": topic_slug,
            "section_index": int(s.get("section_index", len(stored))),
            "title": str(s.get("title", f"Section {len(stored)}")),
            "description": str(s.get("description", "")),
            "search_query": str(s.get("search_query", f"{topic_name} explained")),
        }
        try:
            db.table("topic_sections").upsert(row, on_conflict="topic_slug,section_index").execute()
            stored.append(row)
        except Exception as exc:
            logger.warning(f"[section_planner] Failed to store section {row['section_index']} for {topic_slug}: {exc}")

    logger.info(f"[section_planner] {len(stored)} sections stored for {topic_slug}")
    return stored
