"""YouTube transcript fetcher — uses TranscriptAPI.com to bypass IP-block issues on cloud hosts."""
import os
import logging
import httpx

logger = logging.getLogger(__name__)

TRANSCRIPT_API_KEY = os.environ.get("TRANSCRIPT_API_KEY", "")
TRANSCRIPT_API_URL = "https://transcriptapi.com/api/v2/youtube/transcript"


def _cache_get(video_id: str) -> list[dict] | None:
    """Return cached transcript segments for video_id, or None if not cached."""
    from app.db.supabase import get_client
    try:
        res = (
            get_client()
            .table("transcript_cache")
            .select("segments")
            .eq("video_id", video_id)
            .limit(1)
            .execute()
        )
    except Exception as exc:
        logger.warning(f"[transcript] cache read failed for {video_id}: {exc}")
        return None
    if res.data and res.data[0].get("segments"):
        return res.data[0]["segments"]
    return None


def _cache_put(video_id: str, segments: list[dict]) -> None:
    """Store transcript segments for video_id (idempotent upsert)."""
    from app.db.supabase import get_client
    try:
        get_client().table("transcript_cache").upsert(
            {"video_id": video_id, "segments": segments},
            on_conflict="video_id",
        ).execute()
    except Exception as exc:
        logger.warning(f"[transcript] cache write failed for {video_id}: {exc}")


def _fetch_transcript(video_id: str) -> list[dict] | None:
    """Fetch a YouTube transcript via TranscriptAPI.com, caching by video_id.

    Returns list of {start, duration, text} segments, or None on failure.
    Works from any IP (Render etc.) because the request hits TranscriptAPI's
    network, not YouTube directly. Results are cached in Supabase so repeated
    pipeline runs over the same video never re-pay TranscriptAPI.
    """
    cached = _cache_get(video_id)
    if cached is not None:
        logger.info(f"[transcript] cache hit for {video_id} ({len(cached)} segments)")
        return cached

    if not TRANSCRIPT_API_KEY:
        logger.error("[transcript] TRANSCRIPT_API_KEY not set")
        return None

    try:
        resp = httpx.get(
            TRANSCRIPT_API_URL,
            params={"video_url": video_id},
            headers={"Authorization": f"Bearer {TRANSCRIPT_API_KEY}"},
            timeout=30.0,
        )
    except httpx.HTTPError as exc:
        logger.warning(f"[transcript] Network error for {video_id}: {exc}")
        return None

    if resp.status_code != 200:
        logger.warning(f"[transcript] {video_id} returned {resp.status_code}: {resp.text[:200]}")
        return None

    try:
        data = resp.json()
    except Exception as exc:
        logger.warning(f"[transcript] Bad JSON for {video_id}: {exc}")
        return None

    segments = data.get("transcript") or []
    if not segments:
        logger.warning(f"[transcript] No transcript for {video_id} | payload keys: {list(data.keys())}")
        return None

    out: list[dict] = []
    for seg in segments:
        if not isinstance(seg, dict):
            logger.warning(f"[transcript] Skipping non-dict segment for {video_id}: {type(seg)}")
            continue
        text = (seg.get("text") or "").strip()
        if text:
            out.append({
                "start": float(seg.get("start", 0)),
                "duration": float(seg.get("duration", 0.5)),
                "text": text,
            })

    if out:
        _cache_put(video_id, out)
    return out if out else None
