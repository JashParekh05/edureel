import os
import json
import uuid
import tempfile
import subprocess
from pathlib import Path
import yt_dlp
import whisper
import ffmpeg
from groq import Groq
from app.services.storage import upload_clip

_whisper_model = None
_groq_client = None

MODEL = "llama-3.3-70b-versatile"


def get_whisper():
    global _whisper_model
    if _whisper_model is None:
        _whisper_model = whisper.load_model("base")
    return _whisper_model


def get_groq():
    global _groq_client
    if _groq_client is None:
        _groq_client = Groq(api_key=os.environ["GROQ_API_KEY"])
    return _groq_client


def process_video(video_url: str, topic_slug: str) -> list[dict]:
    """
    Full pipeline: download → transcribe → segment → cut → upload.
    Returns list of clip metadata dicts ready to insert into Supabase.
    """
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp = Path(tmpdir)
        video_path = _download_video(video_url, tmp)
        transcript = _transcribe(video_path)
        segments = _identify_segments(transcript, topic_slug)
        clips = []
        for seg in segments:
            clip_path = _cut_clip(video_path, seg["start"], seg["end"], tmp)
            clip_url, thumb_url = upload_clip(clip_path, topic_slug)
            clips.append(
                {
                    "topic_slug": topic_slug,
                    "title": seg["title"],
                    "description": seg["description"],
                    "video_url": clip_url,
                    "thumbnail_url": thumb_url,
                    "duration_seconds": int(seg["end"] - seg["start"]),
                    "transcript": seg["transcript"],
                    "source_url": video_url,
                    "source_platform": _detect_platform(video_url),
                }
            )
        return clips


def _download_video(url: str, output_dir: Path) -> Path:
    out_template = str(output_dir / "video.%(ext)s")
    ydl_opts = {
        "format": "bestvideo[height<=720]+bestaudio/best[height<=720]",
        "outtmpl": out_template,
        "quiet": True,
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        ydl.download([url])

    matches = list(output_dir.glob("video.*"))
    if not matches:
        raise RuntimeError(f"Download failed for {url}")
    return matches[0]


def _transcribe(video_path: Path) -> dict:
    model = get_whisper()
    result = model.transcribe(str(video_path), word_timestamps=True)
    return result


def _identify_segments(transcript: dict, topic_slug: str) -> list[dict]:
    full_text = transcript["text"]
    segments_with_times = [
        {"start": s["start"], "end": s["end"], "text": s["text"]}
        for s in transcript["segments"]
    ]

    client = get_groq()
    response = client.chat.completions.create(
        model=MODEL,
        max_tokens=2048,
        messages=[
            {
                "role": "user",
                "content": f"""You are segmenting an educational video about "{topic_slug}" into short clips (30-90 seconds each).

Here is the full transcript with timestamps:
{json.dumps(segments_with_times, indent=2)}

Identify 3-6 self-contained conceptual segments. Each segment should cover one clear idea.
Return JSON array:
[
  {{
    "title": "Short clip title",
    "description": "One sentence description",
    "start": 12.5,
    "end": 67.3,
    "transcript": "the text spoken in this segment"
  }}
]
Only return the JSON array, no other text.""",
            }
        ],
    )

    raw = response.choices[0].message.content.strip()
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    return json.loads(raw.strip())


def _cut_clip(video_path: Path, start: float, end: float, output_dir: Path) -> Path:
    clip_id = str(uuid.uuid4())[:8]
    out_path = output_dir / f"clip_{clip_id}.mp4"

    (
        ffmpeg
        .input(str(video_path), ss=start, to=end)
        .output(
            str(out_path),
            vcodec="libx264",
            acodec="aac",
            vf="scale=1080:-2",   # vertical 1080p
        )
        .overwrite_output()
        .run(quiet=True)
    )
    return out_path


def _detect_platform(url: str) -> str:
    if "youtube.com" in url or "youtu.be" in url:
        return "youtube"
    if "khanacademy.org" in url:
        return "khan_academy"
    return "other"
