import os
import re
import json
import logging
import yt_dlp
from groq import Groq
from app.services.embeddings import embed_texts

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_groq_client = None
MODEL = "llama-3.3-70b-versatile"
COOKIES_PATH = os.getenv("YOUTUBE_COOKIES_PATH", "cookies.txt")


def get_groq():
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client


def process_video(video_url: str, topic_slug: str) -> list[dict]:
    """Caption pipeline: yt-dlp fetches captions → Groq segments → YouTube embed clips."""
    video_id = _extract_video_id(video_url)
    if not video_id:
        logger.warning(f"Could not extract video_id from {video_url}")
        return []

    logger.info(f"Fetching captions for video_id={video_id} topic={topic_slug}")
    transcript = _fetch_captions(video_id)
    if not transcript:
        logger.warning(f"No captions for {video_id}, skipping")
        return []

    logger.info(f"Got {len(transcript)} caption entries, calling Groq...")
    segments = _identify_segments(transcript, topic_slug)
    logger.info(f"Groq returned {len(segments)} segments")

    # Batch embed all segment transcripts
    texts = [seg.get("transcript") or seg.get("title", "") for seg in segments]
    embeddings = embed_texts(texts)

    clips = []
    for seg, emb in zip(segments, embeddings):
        clip: dict = {
            "topic_slug": topic_slug,
            "title": seg["title"],
            "description": seg["description"],
            "video_url": f"https://www.youtube.com/embed/{video_id}?start={int(seg['start'])}&end={int(seg['end'])}&autoplay=1&enablejsapi=1",
            "thumbnail_url": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
            "duration_seconds": int(seg["end"] - seg["start"]),
            "transcript": seg["transcript"],
            "source_url": video_url,
            "source_platform": "youtube",
            "hook_score": seg.get("hook_score", 0.5),
        }
        if emb is not None:
            clip["embedding"] = emb
        clips.append(clip)
    return clips


def _extract_video_id(url: str) -> str | None:
    if "v=" in url:
        vid = url.split("v=")[1].split("&")[0]
    elif "youtu.be/" in url:
        vid = url.split("youtu.be/")[1].split("?")[0]
    else:
        return None
    if not re.match(r'^[A-Za-z0-9_-]{11}$', vid):
        return None
    return vid


def _fetch_captions(video_id: str) -> list[dict] | None:
    """Fetch auto-captions via yt-dlp. No audio download."""
    url = f"https://www.youtube.com/watch?v={video_id}"
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "noplaylist": True,
    }
    if os.path.exists(COOKIES_PATH):
        ydl_opts["cookiefile"] = COOKIES_PATH

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False, process=False)

            # Prefer manual subtitles, fall back to auto-generated
            subtitle_url = None
            subs = info.get("subtitles", {})
            auto = info.get("automatic_captions", {})
            for track_dict in [subs, auto]:
                en = track_dict.get("en") or track_dict.get("en-orig")
                if not en:
                    continue
                for fmt in en:
                    if fmt.get("ext") == "json3":
                        subtitle_url = fmt["url"]
                        break
                if subtitle_url:
                    break

            if not subtitle_url:
                logger.warning(f"No English captions found for {video_id}")
                return None

            # Use yt-dlp's session to fetch (carries cookies/headers YouTube expects)
            raw = ydl.urlopen(subtitle_url).read()
            data = json.loads(raw)
    except Exception as e:
        logger.warning(f"Caption fetch failed for {video_id}: {e}")
        return None

    return _parse_json3_captions(data)


def _parse_json3_captions(data: dict) -> list[dict] | None:
    """Parse YouTube json3 subtitle format into [{start, duration, text}]."""
    entries = []
    for event in data.get("events", []):
        segs = event.get("segs")
        if not segs:
            continue
        text = "".join(s.get("utf8", "") for s in segs).strip()
        if not text or text == "\n":
            continue
        start_ms = event.get("tStartMs", 0)
        dur_ms = event.get("dDurationMs", 500)
        entries.append({
            "start": start_ms / 1000,
            "duration": max(dur_ms / 1000, 0.5),
            "text": text,
        })

    return entries if entries else None


def _identify_segments(transcript: list[dict], topic_slug: str) -> list[dict]:
    segments_with_times = [
        {"start": s["start"], "end": s["start"] + s["duration"], "text": s["text"]}
        for s in transcript
    ]

    client = get_groq()
    prompt = f"""You are cutting an educational video about "{topic_slug}" into short reels optimized for viewer retention (TikTok-style).

CRITICAL RULE: Every segment MUST open with a hook — the very first words of the segment should grab attention. Strong hooks are:
- A surprising or counterintuitive claim: "Most people believe X, but actually..."
- A question that creates curiosity: "Why does X happen even when Y?"
- A stakes-setter: "If you get this wrong, the whole thing falls apart"
- A counterexample: "Here's where every textbook gets it wrong"
Avoid segments that open with intros, transitions, or "In this section we will..."

Here is the transcript with timestamps:
{json.dumps(segments_with_times[:80], indent=2)}

Identify 3-6 segments, each 45-90 seconds long, each covering one clear idea. Prefer cuts that start mid-thought at a moment of tension or revelation.

For each segment, score its hook quality: 1.0 = irresistible opening, 0.5 = adequate, 0.0 = boring intro.
Write the title as a curiosity-gap phrase (max 8 words) — something that makes the viewer NEED to know more.

Return a JSON array only, no other text:
[
  {{
    "title": "Why Nobody Understands This Correctly",
    "description": "One sentence that makes them want to watch",
    "start": 12.5,
    "end": 72.3,
    "transcript": "the text spoken in this segment",
    "hook_score": 0.85
  }}
]"""

    try:
        response = client.chat.completions.create(
            model=MODEL,
            max_tokens=2048,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as e:
        logger.error(f"[pipeline] Groq segmentation API call failed for topic={topic_slug}: {e}")
        return []

    raw = response.choices[0].message.content.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        segments = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        logger.error(f"[pipeline] Failed to parse segmentation JSON for topic={topic_slug}: {e} | raw={raw[:200]}")
        return []

    for seg in segments:
        seg.setdefault("hook_score", 0.5)
        seg["hook_score"] = max(0.0, min(1.0, float(seg["hook_score"])))
    return segments
