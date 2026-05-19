import logging

logger = logging.getLogger(__name__)


def _fetch_transcript(video_id: str) -> list[dict] | None:
    try:
        from youtube_transcript_api import YouTubeTranscriptApi
        entries = YouTubeTranscriptApi.get_transcript(video_id, languages=["en", "en-US"])
        return [{"start": e["start"], "duration": e["duration"], "text": e["text"]} for e in entries]
    except Exception as exc:
        logger.warning(f"[transcript] Failed for {video_id}: {exc}")
        return None
