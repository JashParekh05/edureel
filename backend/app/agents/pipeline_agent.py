"""LangGraph agent: YouTube search → transcript → Groq segmentation → Supabase store."""
import logging
from typing import TypedDict, Annotated
import operator

from langgraph.graph import StateGraph, END

logger = logging.getLogger(__name__)


class PipelineState(TypedDict):
    topic_slug: str
    topic_name: str
    search_query: str | None     # section-specific query; overrides default if set
    section_index: int | None    # which section these clips belong to
    clear_existing: bool         # delete old clips before storing (False for sections 1-3)
    videos: list[dict]           # raw YouTube search items + details
    clips: list[dict]            # segmented clips ready for DB
    stored_count: int
    errors: Annotated[list[str], operator.add]


def _node_search(state: PipelineState) -> dict:
    import os, requests
    from app.services.youtube import search_cache_get, search_cache_put

    query = state.get("search_query") or f"{state['topic_name']} explained"

    # Serve from cache when possible — a YouTube search costs 100 quota units
    # (10k/day free). Caching by query means re-testing the same topics is free.
    cached = search_cache_get(query)
    if cached:
        logger.info(f"[pipeline_agent] search cache hit: query='{query}' ({len(cached)} videos, 0 units)")
        return {"videos": cached}

    api_key = os.environ.get("YOUTUBE_API_KEY", "")
    if not api_key:
        return {"errors": ["YOUTUBE_API_KEY not set"], "videos": []}

    logger.info(f"[pipeline_agent] search: topic={state['topic_slug']} section={state.get('section_index')} query='{query}' (~100 units)")

    search = requests.get(
        "https://www.googleapis.com/youtube/v3/search",
        params={
            "key": api_key,
            "q": query,
            "type": "video",
            "videoDuration": "short",
            "videoEmbeddable": "true",
            "safeSearch": "strict",
            "relevanceLanguage": "en",
            "maxResults": 6,
            "part": "snippet",
        },
        timeout=10,
    )
    if not search.ok:
        return {"errors": [f"YouTube search failed: {search.status_code}"], "videos": []}

    items = search.json().get("items", [])
    if not items:
        return {"errors": [f"No results for {state['topic_slug']}"], "videos": []}

    video_ids = [i["id"]["videoId"] for i in items]
    details = requests.get(
        "https://www.googleapis.com/youtube/v3/videos",
        params={"key": api_key, "id": ",".join(video_ids), "part": "contentDetails,snippet"},
        timeout=10,
    )
    logger.info(f"[pipeline_agent] videos.list (~1 unit)")

    durations: dict[str, int] = {}
    if details.ok:
        import re
        for v in details.json().get("items", []):
            m = re.match(r"PT(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", v["contentDetails"]["duration"])
            if m:
                h, mn, s = (int(x or 0) for x in m.groups())
                durations[v["id"]] = h * 3600 + mn * 60 + s

    videos = []
    for item in items:
        vid_id = item["id"]["videoId"]
        snippet = item["snippet"]
        videos.append({
            "video_id": vid_id,
            "title": snippet["title"],
            "description": snippet.get("description", "")[:200] or None,
            "thumbnail_url": snippet.get("thumbnails", {}).get("high", {}).get("url"),
            "duration_seconds": durations.get(vid_id, 180),
        })

    if videos:
        search_cache_put(query, videos)
    return {"videos": videos}


def _node_transcribe(state: PipelineState) -> dict:
    from app.services.youtube import _fetch_transcript
    videos = state["videos"]
    errors = []
    for v in videos:
        transcript = _fetch_transcript(v["video_id"])
        v["transcript"] = transcript
        if not transcript:
            errors.append(f"No transcript: {v['video_id']}")
    return {"videos": videos, "errors": errors}


def _node_segment(state: PipelineState) -> dict:
    from app.services.pipeline import _identify_segments
    from app.services.embeddings import embed_texts
    topic_slug = state["topic_slug"]
    clips = []

    for v in state["videos"]:
        vid_id = v["video_id"]
        base = {
            "topic_slug": topic_slug,
            "section_index": state.get("section_index"),
            "title": v["title"],
            "description": v["description"],
            "video_url": f"https://www.youtube.com/embed/{vid_id}?autoplay=1&rel=0&modestbranding=1",
            "thumbnail_url": v["thumbnail_url"],
            "duration_seconds": v["duration_seconds"],
            "transcript": None,
            "source_url": f"https://www.youtube.com/watch?v={vid_id}",
            "source_platform": "youtube",
            "hook_score": 0.5,
        }
        if v.get("transcript"):
            try:
                segments = _identify_segments(v["transcript"], topic_slug)
                for seg in segments:
                    clips.append({
                        **base,
                        "title": seg["title"],
                        "description": seg.get("description", base["description"]),
                        "video_url": f"https://www.youtube.com/embed/{vid_id}?start={int(seg['start'])}&autoplay=1&rel=0&modestbranding=1",
                        "duration_seconds": int(seg["end"] - seg["start"]),
                        "transcript": seg.get("transcript"),
                        "hook_score": seg.get("hook_score", 0.5),
                    })
            except Exception as exc:
                logger.warning(f"[pipeline_agent] segment failed {vid_id}: {exc}")
                clips.append(base)
        else:
            clips.append(base)

    texts = [c.get("transcript") or c.get("title", "") for c in clips]
    embeddings = embed_texts(texts)
    for clip, emb in zip(clips, embeddings):
        if emb is not None:
            clip["embedding"] = emb

    logger.info(f"[pipeline_agent] {len(clips)} clips after segmentation for {topic_slug}")
    return {"clips": clips}


def _node_store(state: PipelineState) -> dict:
    from app.db.supabase import get_client
    db = get_client()
    if state.get("clear_existing", True):
        db.table("clips").delete().eq("topic_slug", state["topic_slug"]).execute()
    stored = 0
    for clip in state["clips"]:
        try:
            db.table("clips").insert(clip).execute()
            stored += 1
        except Exception as exc:
            logger.warning(f"[pipeline_agent] insert failed: {exc}")
    logger.info(f"[pipeline_agent] stored {stored}/{len(state['clips'])} clips for {state['topic_slug']}")
    return {"stored_count": stored}


def build_pipeline_graph() -> StateGraph:
    g = StateGraph(PipelineState)
    g.add_node("search", _node_search)
    g.add_node("transcribe", _node_transcribe)
    g.add_node("segment", _node_segment)
    g.add_node("store", _node_store)
    g.set_entry_point("search")
    g.add_edge("search", "transcribe")
    g.add_edge("transcribe", "segment")
    g.add_edge("segment", "store")
    g.add_edge("store", END)
    return g.compile()


_pipeline_graph = None


def run_pipeline(
    topic_slug: str,
    topic_name: str,
    search_query: str | None = None,
    section_index: int | None = None,
    clear_existing: bool = True,
) -> int:
    """Run the full pipeline for a topic (or one section of a topic). Returns clips stored."""
    global _pipeline_graph
    if _pipeline_graph is None:
        _pipeline_graph = build_pipeline_graph()

    result = _pipeline_graph.invoke({
        "topic_slug": topic_slug,
        "topic_name": topic_name,
        "search_query": search_query,
        "section_index": section_index,
        "clear_existing": clear_existing,
        "videos": [],
        "clips": [],
        "stored_count": 0,
        "errors": [],
    })
    return result["stored_count"]
