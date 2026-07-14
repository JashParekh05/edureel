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
    section_title: str | None    # this beat's title (for narrative-aware cutting)
    section_description: str | None  # what this beat must teach
    arc_titles: list[str]        # all 4 beat titles in order, for narrative context
    clear_existing: bool         # delete old clips before storing (False for sections 1-3)
    videos: list[dict]           # raw YouTube search items + details
    clips: list[dict]            # segmented clips ready for DB
    stored_count: int
    errors: Annotated[list[str], operator.add]


def _node_search(state: PipelineState) -> dict:
    # All YouTube quota spending lives in youtube.youtube_search — the single
    # charge site. It is cache-first (a youtube_search_cache hit costs 0 units
    # and never touches the quota pool), selects an affordable Google Cloud
    # project, charges the 100-unit search BEFORE the API call, then performs
    # videos.list (1 unit) and caches the result. This node only orchestrates;
    # it no longer issues HTTP requests or re-checks the cache itself (avoiding
    # a redundant double cache read), and returns the same external contract.
    from app.services.youtube import youtube_search

    query = state.get("search_query") or f"{state['topic_name']} explained"

    logger.info(
        f"[pipeline_agent] search: topic={state['topic_slug']} "
        f"section={state.get('section_index')} query='{query}'"
    )

    videos = youtube_search(query)
    if videos is None:
        # No project could afford the search (or none configured): nothing spent.
        return {"errors": ["YouTube search unavailable: no affordable quota"], "videos": []}
    if not videos:
        return {"errors": [f"No results for {state['topic_slug']}"], "videos": []}

    return {"videos": videos}


def _popularity_bonus(view_count: int) -> float:
    """Map raw view count to a small [0, 0.1] bonus on a log scale, so a
    well-watched video edges out a similarly-relevant obscure one without
    letting raw popularity override relevance. ~1k views ≈ 0.04, ~1M ≈ 0.075,
    ~100M ≈ 0.10. Capped so a viral video can't dominate the ranking."""
    import math
    if not view_count or view_count <= 0:
        return 0.0
    return min(math.log10(view_count + 1) / 80.0, 0.1)


# Channels we trust to teach well — the named sources Curio's seed hints already
# lean on (3Blue1Brown, StatQuest, NeetCode...). Stored as normalized keys
# (lowercase, alphanumeric only), matched as a substring of a candidate's channel
# title so "StatQuest with Josh Starmer" still hits "statquest". Editable — add
# the channels you trust. Title matching is cheap but can drift if a channel
# renames; switch to channelId if that ever bites.
TRUSTED_CHANNELS = (
    # CS / DSA / interview prep
    "neetcode", "abdulbari", "mycodeschool", "williamfiset", "backtobackswe",
    "tusharroy", "csdojo", "kunalkushwaha", "techdose",
    # Web / systems / tooling
    "fireship", "computerphile", "bytebytego", "gauravsen", "husseinnasser",
    "lowlevellearning", "traversymedia", "theprimeagen", "freecodecamp",
    # Math
    "3blue1brown", "khanacademy", "organicchemistrytutor", "professorleonard",
    # ML / AI
    "statquest", "andrejkarpathy", "twominutepapers", "deeplearningai",
    "sentdex", "codeemporium", "serranoacademy",
    # Science
    "veritasium", "kurzgesagt", "crashcourse", "pbsspacetime", "scishow",
    "minutephysics", "teded", "vsauce",
    # Economics
    "marginalrevolution",
    # General edutainment / history / current events (high-production, broad
    # appeal — the "fun hook" channels that anchor the engaging 70% of Discover)
    "oversimplified", "johnnyharris", "wendoverproductions", "halfasinteresting",
    "reallifelore", "kingsandgenerals", "historymatters", "economicsexplained",
    "tierzoo", "practicalengineering",
)

CHANNEL_BONUS = 0.15


def _norm_channel(name: str | None) -> str:
    return "".join(ch for ch in (name or "").lower() if ch.isalnum())


def _channel_bonus(video: dict) -> float:
    """CHANNEL_BONUS when a candidate is from a TRUSTED_CHANNELS source, else 0.0.

    Deliberately larger than the caption (0.05) and popularity (<=0.1)
    tiebreakers, so a known teacher wins among comparable candidates and can
    overcome a SMALL relevance gap — but small enough that a clearly
    more-relevant video still wins, so a great channel's off-topic upload never
    beats a perfectly on-topic one."""
    norm = _norm_channel(video.get("channel_title"))
    if not norm:
        return 0.0
    return CHANNEL_BONUS if any(key in norm for key in TRUSTED_CHANNELS) else 0.0


def _rank_candidates(videos: list[dict], query: str) -> list[dict]:
    """Order search candidates by semantic relevance to the section query so the
    best-matching video is transcribed first (instead of YouTube's raw order).

    Relevance is primary. Caption availability, view count, and whether the
    video is from a trusted educational channel are bounded tiebreakers — a
    captioned, well-watched, or trusted-channel video edges out a similarly-
    relevant one, and a trusted channel can overcome a small relevance gap, but
    none override a clearly more relevant video (keeps the feed from collapsing
    onto the same few viral hits). Best-effort: if
    embeddings are unavailable or the candidates carry no text, the original
    order is preserved so this can never make selection worse.
    """
    if len(videos) <= 1 or not query:
        return videos
    texts = [f"{v.get('title', '')} {v.get('description') or ''}".strip() for v in videos]
    if not any(texts):
        return videos

    from app.services.embeddings import embed_text, embed_texts, cosine_similarity

    q_vec = embed_text(query)
    if q_vec is None:
        # No embeddings: trusted channels first, then captioned, then higher-view.
        return sorted(videos, key=lambda v: (-_channel_bonus(v),
                                             0 if v.get("has_caption") else 1,
                                             -_popularity_bonus(v.get("view_count", 0))))

    vecs = embed_texts(texts)

    def _score(pair) -> float:
        v, vec = pair
        sim = cosine_similarity(q_vec, vec) if vec else -1.0
        caption = 0.05 if v.get("has_caption") else 0.0
        return sim + caption + _popularity_bonus(v.get("view_count", 0)) + _channel_bonus(v)

    ranked = sorted(zip(videos, vecs), key=_score, reverse=True)
    order = [v.get("video_id") for v, _ in ranked]
    logger.info(f"[pipeline_agent] ranked {len(videos)} candidates by relevance to query='{query}': {order}")
    return [v for v, _ in ranked]


def _node_transcribe(state: PipelineState) -> dict:
    """Candidate bounding: we search 6 videos for recall, but only transcribe +
    keep the single best one that actually has a transcript. Segmentation
    downstream is the bottleneck (one LLM call per video) AND every successful
    transcript fetch is a paid TranscriptAPI credit, so capping at one per beat
    bounds both cost ~6-12x and credit burn. A topic still gets a full 4-beat
    arc -- one source video per beat -- and progressive backfill / reseed can
    widen a beat later if its Watch_Quality is low.

    Candidates are relevance-ranked first, so the one we keep is the best
    semantic match to the section query, not just YouTube's top result."""
    from app.services.youtube import _fetch_transcript
    from app.services.language_filter import transcript_looks_non_english

    query = state.get("search_query") or state.get("topic_name", "")
    candidates = _rank_candidates(state["videos"], query)

    # One paid transcript per beat (was 2 for sections 1-3). Failed fetches are
    # free on TranscriptAPI ("only pay for successful"), so the loop still scans
    # past caption-less candidates to find the one it keeps.
    limit = 1
    kept, errors = [], []
    for v in candidates:
        if len(kept) >= limit:
            break
        transcript = _fetch_transcript(v["video_id"])
        if not transcript:
            errors.append(f"No transcript: {v['video_id']}")
            continue
        # English-only catch-all: a non-Latin-script transcript (e.g. Devanagari
        # Hindi) means the video is non-English even when its title is English
        # and YouTube declared no language. Skip and keep scanning candidates.
        if transcript_looks_non_english(transcript):
            errors.append(f"Non-English transcript: {v['video_id']}")
            continue
        v["transcript"] = transcript
        kept.append(v)
    return {"videos": kept, "errors": errors}


def _node_segment(state: PipelineState) -> dict:
    from app.services.pipeline import _identify_segments
    from app.services.embeddings import embed_texts
    topic_slug = state["topic_slug"]
    clips = []

    # When this run belongs to a section, give segmentation the beat's role and
    # the surrounding arc so it cuts a connected mini-story instead of isolated
    # hooks. Non-section runs (e.g. discover seeding) pass None and keep the
    # original standalone behavior.
    section_context = None
    if state.get("section_index") is not None:
        section_context = {
            "section_index": state.get("section_index"),
            "title": state.get("section_title") or "",
            "description": state.get("section_description") or "",
            "arc_titles": state.get("arc_titles") or [],
        }

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
                segments = _identify_segments(v["transcript"], topic_slug, section_context)
            except Exception as exc:
                logger.warning(f"[pipeline_agent] segment failed {vid_id}: {exc}")
                segments = []
            if segments:
                for seg in segments:
                    clips.append({
                        **base,
                        "title": seg["title"],
                        "description": seg.get("description", base["description"]),
                        "video_url": f"https://www.youtube.com/embed/{vid_id}?start={int(seg['start'])}&end={int(seg['end'])}&autoplay=1&rel=0&modestbranding=1",
                        "duration_seconds": int(seg["end"] - seg["start"]),
                        "transcript": seg.get("transcript"),
                        "hook_score": seg.get("hook_score", 0.5),
                    })
            else:
                # Graceful fallback: a transient empty segmentation (LLM error /
                # unparseable JSON) must still yield the base clip, not zero.
                logger.warning(f"[pipeline_agent] segmentation empty for {vid_id}; using base clip")
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
    section_title: str | None = None,
    section_description: str | None = None,
    arc_titles: list[str] | None = None,
) -> int:
    """Run the full pipeline for a topic (or one section of a topic). Returns clips stored.

    section_title/section_description/arc_titles give the segmenter narrative
    context so a section's clips form a connected mini-story; they're optional
    so non-section callers (discover seeding, recommendations) are unaffected.

    Routing (task 11.1): a WHOLE-TOPIC call (``section_index is None``) is routed
    through the shared Ingestion_Pipeline (``ingestion_pipeline.ingest_topic`` ->
    DECODE -> MAP -> JUDGE -> ADMIT) -- the SAME path the cold-start Seeding_Worker
    uses -- so on-demand topics produce plan-mapped, coherence/alignment-checked,
    per-segment-judged Admitted_Clips instead of raw hand-picked segments
    (Req 5.1, 7.3). ``ingest_topic`` is best-effort end to end, drives the
    Planned_Arc + the single ``youtube.youtube_search`` charge site itself, and
    returns an ``IngestionSummary`` whose ``stored`` is the Admitted_Clip count.

    A SECTION-BASED call (``section_index is not None``, e.g. per-beat planning
    from ``topics.py``) KEEPS the existing LangGraph pipeline below unchanged, so
    per-section narrative generation is not broken (``ingest_topic`` does a
    whole-topic ingestion and is not section-aware).

    Imported lazily inside the function to avoid an import-time cycle
    (services -> ... -> pipeline_agent).
    """
    if section_index is None:
        from app.services.ingestion_pipeline import ingest_topic

        summary = ingest_topic(topic_slug, topic_name)
        logger.info(
            f"[pipeline_agent] whole-topic '{topic_slug}' routed through "
            f"ingest_topic: outcome={summary.outcome} stored={summary.stored}"
        )
        return summary.stored

    global _pipeline_graph
    if _pipeline_graph is None:
        _pipeline_graph = build_pipeline_graph()

    result = _pipeline_graph.invoke({
        "topic_slug": topic_slug,
        "topic_name": topic_name,
        "search_query": search_query,
        "section_index": section_index,
        "section_title": section_title,
        "section_description": section_description,
        "arc_titles": arc_titles or [],
        "clear_existing": clear_existing,
        "videos": [],
        "clips": [],
        "stored_count": 0,
        "errors": [],
    })
    return result["stored_count"]
