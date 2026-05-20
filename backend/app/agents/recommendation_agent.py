"""LangGraph agent: analyze watch history → score topics → surface next recommendations."""
import json
import logging
from typing import TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, END
from app.models.schemas import TopicRecommendation

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"


class RecommendationState(TypedDict):
    session_id: str
    path_slugs: list[str]
    seen_ids: set[str]
    topic_completion: dict[str, float]       # slug → 0.0-1.0
    interest_vector: dict                    # slug → affinity (-1.0 to 1.0)
    mastered_slugs: list[str]
    candidates: list[dict]                   # {slug, name, difficulty, clip_count, source}
    recommendations: list[TopicRecommendation]
    trigger_fetch_slugs: list[str]           # new topics that need YouTube search queued
    errors: Annotated[list[str], operator.add]


def _node_load_telemetry(state: RecommendationState) -> dict:
    from app.db.supabase import get_client
    db = get_client()

    events = (
        db.table("clip_events")
        .select("clip_id, watch_ms, completed")
        .eq("session_id", state["session_id"])
        .execute()
    )

    seen_ids: set[str] = set()
    topic_watches: dict[str, list[bool]] = {}

    for ev in events.data:
        seen_ids.add(ev["clip_id"])
        clip = db.table("clips").select("topic_slug").eq("id", ev["clip_id"]).limit(1).execute()
        if clip.data:
            slug = clip.data[0]["topic_slug"]
            topic_watches.setdefault(slug, []).append(bool(ev["completed"]))

    topic_completion = {
        slug: sum(completions) / len(completions)
        for slug, completions in topic_watches.items()
    }

    iv_res = (
        db.table("session_embeddings")
        .select("interest_vector")
        .eq("session_id", state["session_id"])
        .limit(1)
        .execute()
    )
    interest_vector = iv_res.data[0]["interest_vector"] if iv_res.data else {}

    return {"seen_ids": seen_ids, "topic_completion": topic_completion, "interest_vector": interest_vector}


def _node_identify_mastered(state: RecommendationState) -> dict:
    """Topics with ≥70% completion rate are considered mastered."""
    mastered = [slug for slug, rate in state["topic_completion"].items() if rate >= 0.7]
    logger.info(f"[rec_agent] mastered: {mastered}")
    return {"mastered_slugs": mastered}


def _node_find_candidates(state: RecommendationState) -> dict:
    """Forward-graph traversal: find topics whose prerequisites include mastered topics."""
    from app.db.supabase import get_client
    db = get_client()

    path_slugs = state["path_slugs"]
    candidates: list[dict] = []
    seen_candidate_slugs: set[str] = set()

    # 1. Forward graph from mastered topics
    for slug in (state["mastered_slugs"] or state["path_slugs"]):
        forward = (
            db.table("topics")
            .select("slug,name,difficulty")
            .contains("prerequisites", [slug])
            .not_.in_("slug", path_slugs)
            .limit(3)
            .execute()
        )
        for t in forward.data:
            if t["slug"] not in seen_candidate_slugs:
                seen_candidate_slugs.add(t["slug"])
                candidates.append({**t, "source": "prerequisite_graph"})

    # 2. Fallback: topics not in current path, ordered by clip volume descending
    if not candidates:
        fallback = (
            db.table("topics")
            .select("slug,name,difficulty")
            .not_.in_("slug", path_slugs)
            .limit(20)
            .execute()
        )
        fallback_with_counts = []
        for t in fallback.data:
            if t["slug"] not in seen_candidate_slugs:
                count_res = db.table("clips").select("id", count="exact").eq("topic_slug", t["slug"]).execute()
                clip_count = count_res.count or 0
                if clip_count > 0:
                    seen_candidate_slugs.add(t["slug"])
                    fallback_with_counts.append({**t, "source": "fallback", "clip_count": clip_count})
        # Sort by clip count so well-stocked topics rank first
        fallback_with_counts.sort(key=lambda x: x["clip_count"], reverse=True)
        candidates.extend(fallback_with_counts[:10])

    # Attach clip counts (skip if already set by fallback path)
    for c in candidates:
        if "clip_count" not in c:
            count_res = db.table("clips").select("id", count="exact").eq("topic_slug", c["slug"]).execute()
            c["clip_count"] = count_res.count or 0

    logger.info(f"[rec_agent] candidates: {[c['slug'] for c in candidates]}")
    return {"candidates": candidates}


def _node_score_and_rank(state: RecommendationState) -> dict:
    """Use Groq to rank candidates by relevance to the user's learning context."""
    candidates_with_clips = [c for c in state["candidates"] if c["clip_count"] > 0]

    if not candidates_with_clips:
        # No clips exist yet for candidates — queue them
        trigger = [c["slug"] for c in state["candidates"][:3]]
        return {"recommendations": [], "trigger_fetch_slugs": trigger}

    if len(candidates_with_clips) <= 3:
        # No need to rank, just return them
        recs = [
            TopicRecommendation(
                slug=c["slug"],
                name=c["name"],
                difficulty=c["difficulty"],
                clip_count=c["clip_count"],
                rationale="Builds on what you just learned",
            )
            for c in candidates_with_clips[:3]
        ]
        return {"recommendations": recs, "trigger_fetch_slugs": []}

    # Use Groq to rank
    import os
    from openai import OpenAI
    client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])

    context = {
        "current_topics": state["path_slugs"],
        "mastered": state["mastered_slugs"],
        "completion_rates": state["topic_completion"],
        "topic_affinities": state.get("interest_vector", {}),
    }

    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=512,
        messages=[
            {
                "role": "system",
                "content": "You are recommending next learning topics for a student. Return a JSON array of the top 3 candidate slugs in order of relevance, with a short rationale for each.",
            },
            {
                "role": "user",
                "content": f"""Student learning context:
{json.dumps(context, indent=2)}

Candidate topics:
{json.dumps(candidates_with_clips, indent=2)}

Return JSON array:
[{{"slug": "...", "rationale": "one sentence why this is the right next step"}}]
Pick top 3. Return JSON only.""",
            },
        ],
    )

    raw = response.choices[0].message.content.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        ranked = json.loads(raw.strip())
    except Exception:
        ranked = [{"slug": c["slug"], "rationale": "Builds on what you just learned"} for c in candidates_with_clips[:3]]

    cand_map = {c["slug"]: c for c in candidates_with_clips}
    recs = []
    for r in ranked[:3]:
        c = cand_map.get(r["slug"])
        if c:
            recs.append(TopicRecommendation(
                slug=c["slug"],
                name=c["name"],
                difficulty=c["difficulty"],
                clip_count=c["clip_count"],
                rationale=r.get("rationale", "Builds on what you just learned"),
            ))

    logger.info(f"[rec_agent] recommendations: {[r.slug for r in recs]}")
    return {"recommendations": recs, "trigger_fetch_slugs": []}


def _node_trigger_fetch(state: RecommendationState) -> dict:
    """Queue YouTube searches for any recommended topics that have no clips yet."""
    slugs = state.get("trigger_fetch_slugs", [])
    if not slugs:
        return {}

    from app.db.supabase import get_client
    db = get_client()

    for slug in slugs:
        topic_res = db.table("topics").select("slug,name").eq("slug", slug).limit(1).execute()
        if topic_res.data:
            t = topic_res.data[0]
            try:
                from app.agents.pipeline_agent import run_pipeline
                import threading
                threading.Thread(target=run_pipeline, args=(t["slug"], t["name"]), daemon=True).start()
                logger.info(f"[rec_agent] queued pipeline for {slug}")
            except Exception as exc:
                logger.warning(f"[rec_agent] failed to queue pipeline for {slug}: {exc}")

    return {}


def build_recommendation_graph() -> StateGraph:
    g = StateGraph(RecommendationState)
    g.add_node("load_telemetry", _node_load_telemetry)
    g.add_node("identify_mastered", _node_identify_mastered)
    g.add_node("find_candidates", _node_find_candidates)
    g.add_node("score_and_rank", _node_score_and_rank)
    g.add_node("trigger_fetch", _node_trigger_fetch)
    g.set_entry_point("load_telemetry")
    g.add_edge("load_telemetry", "identify_mastered")
    g.add_edge("identify_mastered", "find_candidates")
    g.add_edge("find_candidates", "score_and_rank")
    g.add_edge("score_and_rank", "trigger_fetch")
    g.add_edge("trigger_fetch", END)
    return g.compile()


_rec_graph = None


def run_recommendations(session_id: str, path_slugs: list[str]) -> list[TopicRecommendation]:
    """Run the recommendation agent. Returns up to 3 TopicRecommendation objects."""
    global _rec_graph
    if _rec_graph is None:
        _rec_graph = build_recommendation_graph()

    result = _rec_graph.invoke({
        "session_id": session_id,
        "path_slugs": path_slugs,
        "seen_ids": set(),
        "topic_completion": {},
        "interest_vector": {},
        "mastered_slugs": [],
        "candidates": [],
        "recommendations": [],
        "trigger_fetch_slugs": [],
        "errors": [],
    })
    return result["recommendations"]
