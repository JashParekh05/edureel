"""YouTube transcript fetcher — uses TranscriptAPI.com to bypass IP-block issues on cloud hosts."""
import os
import logging
import httpx

logger = logging.getLogger(__name__)

TRANSCRIPT_API_KEY = os.environ.get("TRANSCRIPT_API_KEY", "")
TRANSCRIPT_API_URL = "https://transcriptapi.com/api/v2/youtube/transcript"


def _fetch_transcript(video_id: str) -> list[dict] | None:
    """Fetch a YouTube transcript via TranscriptAPI.com.

    Returns list of {start, duration, text} segments, or None on failure.
    Works from any IP (Render etc.) because the request hits TranscriptAPI's
    network, not YouTube directly.
    """
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
        text = (seg.get("text") or "").strip()
        if text:
            out.append({
                "start": float(seg.get("start", 0)),
                "duration": float(seg.get("duration", 0.5)),
                "text": text,
            })

    return out if out else None
