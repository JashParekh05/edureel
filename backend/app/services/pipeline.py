import os
import re
import json
import logging
from typing import get_args
from openai import OpenAI
from app.services.embeddings import embed_texts
from app.services import llm
from app.models.schemas import LearningAtom, PedagogicalRole, PlannedArc

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

_openai_client = None


def _get_client():
    global _openai_client
    if _openai_client is None:
        _openai_client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _openai_client


_VALID_ROLES: frozenset[str] = frozenset(get_args(PedagogicalRole))

_ATOM_MIN_DURATION = 3.0   # seconds (Req 2.1)
_ATOM_MAX_DURATION = 90.0  # seconds (Req 2.1)
_CONCEPT_MAX_LEN = 200     # chars (Req 2.3)
_PRIOR_KNOWLEDGE_MAX = 50  # items (Req 2.4)


def validate_atom(
    raw: dict, transcript_duration: float
) -> tuple["LearningAtom | None", "str | None"]:
    """Validate and normalise one candidate atom dict.

    Returns ``(atom, None)`` when the candidate is valid, or ``(None, reason)``
    where *reason* names the specific missing/invalid label so the caller can
    log and exclude the candidate without dropping valid sibling atoms.

    Enforced rules (Requirements 2.1–2.5, 2.7, 7.1):
    - Exactly one defined ``PedagogicalRole`` value in the ``role`` field.
    - ``concept`` is non-empty with 1–200 characters.
    - ``prior_knowledge`` is a list/set of 0–50 distinct concepts, none equal
      to the covered concept.
    - ``start >= 0``, ``end > start``, ``end <= transcript_duration``.
    - Duration (``end - start``) is between 3 and 90 seconds inclusive.
    """
    # --- role ---
    role = raw.get("role")
    if not isinstance(role, str) or role not in _VALID_ROLES:
        return None, f"invalid role: {role!r}"

    # --- concept ---
    concept = raw.get("concept")
    if not isinstance(concept, str) or not concept:
        return None, "missing concept label"
    concept = concept.strip()
    if not concept:
        return None, "concept label is blank"
    if len(concept) > _CONCEPT_MAX_LEN:
        return None, f"concept label exceeds {_CONCEPT_MAX_LEN} characters"

    # --- prior_knowledge ---
    raw_pk = raw.get("prior_knowledge", [])
    if not isinstance(raw_pk, (list, set, tuple)):
        return None, "prior_knowledge must be a list"
    # Normalise to deduplicated list of stripped non-empty strings
    seen: set[str] = set()
    prior_knowledge: list[str] = []
    for item in raw_pk:
        if not isinstance(item, str):
            return None, f"prior_knowledge item is not a string: {item!r}"
        item = item.strip()
        if not item:
            return None, "prior_knowledge contains a blank concept"
        if item in seen:
            continue  # silently deduplicate
        seen.add(item)
        prior_knowledge.append(item)

    if len(prior_knowledge) > _PRIOR_KNOWLEDGE_MAX:
        return None, f"prior_knowledge exceeds {_PRIOR_KNOWLEDGE_MAX} distinct concepts"
    if concept in seen:
        return None, "prior_knowledge contains the covered concept"

    # --- timestamps ---
    try:
        start = float(raw["start"])
        end = float(raw["end"])
    except (KeyError, TypeError, ValueError):
        return None, "start/end timestamps missing or non-numeric"

    if start < 0:
        return None, f"start timestamp is negative: {start}"
    if end <= start:
        return None, f"end ({end}) must be greater than start ({start})"
    if end > transcript_duration:
        return None, (
            f"end ({end}) exceeds transcript duration ({transcript_duration})"
        )

    duration = end - start
    if duration < _ATOM_MIN_DURATION:
        return None, (
            f"duration {duration:.3f}s is below minimum {_ATOM_MIN_DURATION}s"
        )
    if duration > _ATOM_MAX_DURATION:
        return None, (
            f"duration {duration:.3f}s exceeds maximum {_ATOM_MAX_DURATION}s"
        )

    # --- build atom ---
    try:
        atom = LearningAtom(
            id=str(raw.get("id", "")),
            topic_slug=str(raw.get("topic_slug", "")),
            video_id=str(raw.get("video_id", "")),
            source_url=str(raw.get("source_url", "")),
            role=role,  # type: ignore[arg-type]
            concept=concept,
            prior_knowledge=prior_knowledge,
            start=start,
            end=end,
            transcript=raw.get("transcript"),
        )
    except Exception as exc:
        return None, f"model validation error: {exc}"

    return atom, None


def order_atoms(atoms: list[LearningAtom]) -> list[LearningAtom]:
    """Sort atoms by ascending start timestamp and resolve overlaps.

    When two atoms overlap in time, the earlier-starting atom is kept intact
    and the later one is either trimmed (its start is advanced to the earlier
    atom's end) or dropped if trimming would violate the minimum duration
    constraint (``_ATOM_MIN_DURATION``).

    This is a **pure function**: the input list and individual atoms are never
    mutated.  A new list of new ``LearningAtom`` instances is returned.

    Requirements: 2.6
    """
    # Sort by start timestamp (ascending), breaking ties by end timestamp.
    sorted_atoms = sorted(atoms, key=lambda a: (a.start, a.end))

    result: list[LearningAtom] = []
    # Track the furthest end timestamp seen so far to detect overlaps.
    timeline_end: float = -1.0

    for atom in sorted_atoms:
        if atom.start >= timeline_end:
            # No overlap — include as-is.
            result.append(atom)
            timeline_end = atom.end
        else:
            # Overlap: the current atom's start is before the previous atom's
            # end.  Attempt to trim by advancing the start to timeline_end.
            new_start = timeline_end
            new_duration = atom.end - new_start
            if new_duration < _ATOM_MIN_DURATION:
                # Trimmed atom would be too short — drop it entirely.
                continue
            # Produce a trimmed copy without mutating the original.
            trimmed = atom.model_copy(update={"start": new_start})
            result.append(trimmed)
            timeline_end = trimmed.end

    return result


def segment_into_atoms(
    transcript: list[dict],
    topic_slug: str,
    planned_arc: PlannedArc,
) -> list[LearningAtom]:
    """LLM shell: prompt the configured model to cut the transcript into single-idea
    atoms, each labeled with role, concept, prior_knowledge (list of strings),
    start, and end (in seconds).

    For every candidate returned by the model, call ``validate_atom``; excluded
    candidates are logged together with the specific rejection reason so the
    caller can trace what was discarded.  Survivors are passed to
    ``order_atoms`` and returned.

    Best-effort (Req 7.1): if the model call or JSON parse fails, the function
    returns an empty list and logs a warning that names *topic_slug*, leaving
    any previously recorded data unchanged.

    Requirements: 2.1-2.7, 7.1
    """
    if not transcript:
        logger.warning(
            "[pipeline] segment_into_atoms: empty transcript for topic=%s, returning []",
            topic_slug,
        )
        return []

    # Derive total duration from the last transcript segment.
    last = transcript[-1]
    transcript_duration: float = float(last["start"]) + float(last["duration"])

    # Build a compact view of the transcript for the prompt (cap at 300 entries
    # to mirror _identify_segments and keep the context window manageable).
    segments_with_times = [
        {
            "start": s["start"],
            "end": round(s["start"] + s["duration"], 3),
            "text": s["text"],
        }
        for s in transcript
    ]
    transcript_json = json.dumps(segments_with_times[:300], indent=2)

    # Enumerate the planned roles so the model knows which labels to use.
    planned_roles = [arc_role.role for arc_role in planned_arc.roles]
    roles_block = (
        "The planned pedagogical arc for this topic defines these roles (in order):\n"
        + "\n".join(f"  - {r}" for r in planned_roles)
        if planned_roles
        else "Use any valid pedagogical role from the defined set."
    )

    valid_roles_list = ", ".join(sorted(_VALID_ROLES))
    prompt = f"""You are an educational content analyst. Your task is to cut a YouTube transcript about "{topic_slug}" into fine-grained single-idea atoms for a pedagogical learning arc.

{roles_block}

Valid role values (use EXACTLY one of these): {valid_roles_list}

Rules for each atom:
- Cover exactly ONE clear idea or concept claim.
- Duration must be between 3 and 90 seconds (end - start).
- "concept": a non-empty text label of 1-200 characters describing what the atom teaches.
- "prior_knowledge": a JSON array of 0-50 distinct concept strings the viewer must already know (none may equal the atom's own "concept").
- "start" and "end": float seconds, matching positions in the transcript.

Here is the transcript with timestamps:
{transcript_json}

Return a JSON array only — no markdown, no extra text:
[
  {{
    "role": "definition",
    "concept": "binary search",
    "prior_knowledge": ["arrays", "sorted order"],
    "start": 4.2,
    "end": 18.7
  }}
]"""

    client = _get_client()
    try:
        response = client.chat.completions.create(
            model=llm.resolve_model(),
            max_tokens=4096,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        logger.warning(
            "[pipeline] segment_into_atoms: model call failed for topic=%s: %s",
            topic_slug,
            exc,
        )
        return []

    raw = response.choices[0].message.content.strip()
    # Strip markdown code fences if present (mirrors _identify_segments).
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]

    try:
        candidates: list[dict] = json.loads(raw.strip())
    except json.JSONDecodeError as exc:
        logger.warning(
            "[pipeline] segment_into_atoms: JSON parse failed for topic=%s: %s | raw=%s",
            topic_slug,
            exc,
            raw[:200],
        )
        return []

    if not isinstance(candidates, list):
        logger.warning(
            "[pipeline] segment_into_atoms: model returned non-list JSON for topic=%s",
            topic_slug,
        )
        return []

    # Validate each candidate; log and exclude invalid ones.
    valid_atoms: list[LearningAtom] = []
    for idx, candidate in enumerate(candidates):
        # Inject context fields the validator reads from the dict but the
        # model does not produce (id, topic_slug, video_id, source_url).
        candidate.setdefault("id", "")
        candidate.setdefault("topic_slug", topic_slug)
        candidate.setdefault("video_id", "")
        candidate.setdefault("source_url", "")

        atom, reason = validate_atom(candidate, transcript_duration)
        if atom is None:
            logger.info(
                "[pipeline] segment_into_atoms: excluded candidate %d for topic=%s — %s",
                idx,
                topic_slug,
                reason,
            )
        else:
            valid_atoms.append(atom)

    return order_atoms(valid_atoms)


def process_video(video_url: str, topic_slug: str) -> list[dict]:
    """Transcript pipeline: TranscriptAPI fetches captions → GPT segments → YouTube embed clips."""
    from app.services.youtube import _fetch_transcript

    video_id = _extract_video_id(video_url)
    if not video_id:
        logger.warning(f"Could not extract video_id from {video_url}")
        return []

    logger.info(f"Fetching transcript for video_id={video_id} topic={topic_slug}")
    transcript = _fetch_transcript(video_id)
    if not transcript:
        logger.warning(f"No transcript for {video_id}, skipping")
        return []

    logger.info(f"Got {len(transcript)} transcript entries, segmenting...")
    segments = _identify_segments(transcript, topic_slug)
    logger.info(f"Got {len(segments)} segments")

    texts = [seg.get("transcript") or seg.get("title", "") for seg in segments]
    embeddings = embed_texts(texts)

    clips = []
    for seg, emb in zip(segments, embeddings):
        clip: dict = {
            "topic_slug": topic_slug,
            "title": seg["title"],
            "description": seg["description"],
            "video_url": f"https://www.youtube.com/embed/{video_id}?start={int(seg['start'])}&end={int(seg['end'])}&autoplay=1&rel=0&modestbranding=1",
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


# The role each beat plays in the 4-part micro-lesson arc. Lets segmentation
# cut clips that fit THIS beat instead of drifting into other beats' material.
_ARC_ROLES = {
    0: "the HOOK — spark curiosity and motivate why this topic matters",
    1: "the DEFINITION — establish the core concept in plain language",
    2: "the MECHANICS — show how it actually works, the real substance",
    3: "the OUTCOMES — the significance, applications, and payoff",
}

_HOOKS_BLOCK = """Strong hooks are:
- A surprising or counterintuitive claim: "Most people believe X, but actually..."
- A question that creates curiosity: "Why does X happen even when Y?"
- A stakes-setter: "If you get this wrong, the whole thing falls apart"
- A counterexample: "Here's where every textbook gets it wrong"
Avoid segments that open with intros, transitions, or "In this section we will...\""""

_JSON_SHAPE = """Return a JSON array only, no other text:
[
  {
    "title": "Why Nobody Understands This Correctly",
    "description": "One sentence that makes them want to watch",
    "start": 12.5,
    "end": 72.3,
    "transcript": "the text spoken in this segment",
    "hook_score": 0.85
  }
]"""


def _build_segment_prompt(segments_with_times: list[dict], topic_slug: str,
                          section_context: dict | None) -> str:
    """Build the segmentation prompt.

    With section_context the cuts are narrative-aware: they fulfill this beat's
    role in the 4-part arc and form a CONNECTED mini-sequence that bridges from
    the previous beat and ends on an open loop. Without it, this falls back to
    the original standalone hook-cut behavior (used by legacy / non-section
    callers), so existing behavior is unchanged when no context is supplied.
    """
    transcript_json = json.dumps(segments_with_times[:300], indent=2)

    if section_context:
        idx = section_context.get("section_index")
        role = _ARC_ROLES.get(
            idx,
            "a DEEPER DIVE — a fresh, surprising angle beyond the basics that rewards the viewer who's still here",
        )
        title = section_context.get("title", "")
        desc = section_context.get("description", "")
        arc = section_context.get("arc_titles") or []
        arc_block = ""
        if arc:
            arc_lines = "\n".join(
                f"  {i}. {t}{'   <-- THIS BEAT' if i == idx else ''}" for i, t in enumerate(arc)
            )
            arc_block = f"\nThe full lesson arc (4 beats, in order):\n{arc_lines}\n"
        bridge = (
            "Because this is the opening beat, the FIRST clip must cold-open the entire "
            "lesson with the strongest possible hook."
            if idx == 0 else
            "The FIRST clip must BRIDGE from the previous beat — open by paying off the "
            "curiosity the prior beat created, then carry the story forward."
        )
        # Only the core arc's outcomes beat (3) closes the loop. Depth beats
        # (4+) are open-ended "keep watching" material — no terminal payoff.
        payoff = (
            "\n- This is the CLOSING beat: the LAST clip must land the payoff — resolve the "
            "central question the lesson opened with and leave the viewer with a clear, "
            "satisfying \"so what\" (why this matters / what it unlocks), not a flat summary."
            if idx == 3 else ""
        )
        return f"""You are cutting an educational video about "{topic_slug}" into a CONNECTED sequence of short reels (TikTok-style) for ONE specific beat of a 4-part micro-lesson.
{arc_block}
This beat is {role}.
Beat title: "{title}"
What this beat must teach: {desc}

Produce 2-3 clips that together form a mini-story for THIS beat:
- Each clip MUST open with a hook. {_HOOKS_BLOCK}
- Each clip must also deliver a self-contained micro-payoff — one satisfying insight — BEFORE its open loop, so a viewer who stops after any clip still walks away having learned something.
- {bridge}
- Order the clips so each one ends on an OPEN LOOP the next clip resolves — curiosity should pull the viewer from one clip to the next.
- Every clip must serve THIS beat's role; do NOT drift into other beats' material.
- No two clips may cover the same point. 45-90 seconds each, one clear idea each.{payoff}

Here is the transcript with timestamps:
{transcript_json}

For each clip, score its hook quality: 1.0 = irresistible opening, 0.5 = adequate, 0.0 = boring intro.
Write the title as a curiosity-gap phrase (max 8 words). Return the clips IN PLAYBACK ORDER.

{_JSON_SHAPE}"""

    return f"""You are cutting an educational video about "{topic_slug}" into short reels optimized for viewer retention (TikTok-style).

CRITICAL RULE: Every segment MUST open with a hook — the very first words of the segment should grab attention. {_HOOKS_BLOCK}

Here is the transcript with timestamps:
{transcript_json}

Identify ONLY 2-3 segments — the single most hook-worthy moments. Each 45-90 seconds long, each covering one clear idea. Prefer cuts that start mid-thought at a moment of tension or revelation. More can be generated later if users engage; quality over quantity.

For each segment, score its hook quality: 1.0 = irresistible opening, 0.5 = adequate, 0.0 = boring intro.
Write the title as a curiosity-gap phrase (max 8 words) — something that makes the viewer NEED to know more.

{_JSON_SHAPE}"""


def _identify_segments(transcript: list[dict], topic_slug: str,
                       section_context: dict | None = None) -> list[dict]:
    segments_with_times = [
        {"start": s["start"], "end": s["start"] + s["duration"], "text": s["text"]}
        for s in transcript
    ]

    client = _get_client()
    prompt = _build_segment_prompt(segments_with_times, topic_slug, section_context)

    try:
        response = client.chat.completions.create(
            model=llm.resolve_model(),
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
