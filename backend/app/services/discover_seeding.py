"""Discover cold-start: grade/interest → starter topic slugs, interest matching,
and background seeding of interest-aligned topics."""
import re
import logging

from app.db.supabase import get_client
from app.services.embeddings import embed_texts, cosine_similarity

logger = logging.getLogger(__name__)


_slug_emb_cache: tuple[tuple[str, ...], list[list[float] | None]] = ((), [])


def _cached_slug_embeddings(slugs: list[str]) -> list[list[float] | None]:
    global _slug_emb_cache
    key = tuple(slugs)
    if _slug_emb_cache[0] != key:
        _slug_emb_cache = (key, embed_texts([s.replace("-", " ") for s in slugs]))
    return _slug_emb_cache[1]


def _match_interest_slugs(interests: list[str], all_slugs: list[str], taste_vector: list[float] | None = None) -> list[str]:
    """Return topic slugs relevant to the user's interests.

    Uses semantic similarity when taste_vector is available, otherwise falls
    back to keyword overlap.
    """
    if not interests and taste_vector is None:
        return all_slugs[:10]

    # Semantic path: embed each slug and rank by cosine similarity to taste
    if taste_vector is not None:
        slug_embeddings = _cached_slug_embeddings(all_slugs)
        scored = []
        for slug, emb in zip(all_slugs, slug_embeddings):
            if emb is not None:
                scored.append((slug, cosine_similarity(taste_vector, emb)))
            else:
                scored.append((slug, 0.0))
        scored.sort(key=lambda x: x[1], reverse=True)
        return [s for s, _ in scored[:10]]

    # Fallback: keyword overlap
    if not interests:
        return all_slugs[:10]
    keywords = set()
    for tag in interests:
        keywords.update(re.findall(r'\b[a-z]{3,}\b', tag.lower()))
    matched = [s for s in all_slugs if any(kw in s for kw in keywords)]
    return matched or all_slugs[:10]


GRADE_LEVEL_TOPIC_MAP: dict[str, dict[str, list[str]]] = {
    "elementary_school": {
        "math": ["addition-subtraction", "multiplication-division", "place-value", "basic-fractions", "shapes-patterns"],
        "science": ["plants-animals", "weather-seasons", "simple-machines", "states-of-matter", "solar-system-basics"],
        "history": ["community-helpers", "native-american-history", "early-explorers", "ancient-civilizations-intro", "american-symbols"],
        "technology": ["computer-basics", "internet-safety", "typing-skills", "coding-blocks", "digital-citizenship"],
        "english_language_arts": ["phonics", "reading-comprehension", "story-elements", "vocabulary-building", "basic-grammar"],
        "arts": ["color-theory-basics", "drawing-basics", "music-rhythm", "crafts", "famous-artists-intro"],
        "life_skills": ["emotions", "friendship", "healthy-habits", "time-management-basics", "classroom-behavior"],
        "world_languages": ["spanish-basics", "french-basics", "greetings", "numbers-colors", "family-words"],
    },
    "middle_school": {
        "math": ["fractions", "ratios", "pre-algebra", "geometry-basics", "probability"],
        "science": ["cells", "ecosystems", "weather", "forces-motion", "earth-science"],
        "history": ["american-revolution", "ancient-egypt", "roman-empire", "middle-ages", "early-american-history"],
        "technology": ["internet-basics", "coding-intro", "scratch", "digital-research", "cybersecurity-basics"],
        "english_language_arts": ["essay-writing", "theme-analysis", "figurative-language", "grammar-punctuation", "argument-writing"],
        "arts": ["drawing-techniques", "music-theory-basics", "theater-intro", "art-history-basics", "creative-design"],
        "life_skills": ["study-skills", "organization", "conflict-resolution", "goal-setting", "media-literacy"],
        "world_languages": ["spanish-grammar-intro", "french-grammar-intro", "basic-conversation", "food-travel-vocabulary", "culture-intro"],
    },
    "high_school": {
        "math": ["algebra-2", "calculus-intro", "statistics", "trigonometry", "geometry-proofs"],
        "science": ["chemistry", "physics-mechanics", "biology", "environmental-science", "anatomy-physiology"],
        "history": ["ww2", "civil-war", "cold-war", "world-history", "us-government"],
        "technology": ["python-intro", "web-dev", "app-design", "databases-intro", "cybersecurity"],
        "english_language_arts": ["literary-analysis", "research-papers", "sat-vocabulary", "rhetorical-analysis", "creative-writing"],
        "arts": ["graphic-design", "photography", "music-composition", "film-analysis", "portfolio-building"],
        "life_skills": ["financial-literacy", "college-readiness", "career-exploration", "public-speaking", "mental-health-awareness"],
        "world_languages": ["spanish-conversation", "french-conversation", "grammar-intermediate", "literature-intro", "cultural-studies"],
    },
    "college": {
        "math": ["linear-algebra", "calc-3", "differential-equations", "discrete-math", "probability-theory"],
        "science": ["thermodynamics", "quantum-mechanics", "organic-chemistry", "molecular-biology", "geology"],
        "history": ["federalist-papers", "industrial-revolution", "modern-europe", "postcolonial-history", "constitutional-history"],
        "technology": ["data-structures", "machine-learning", "systems-programming", "software-engineering", "cloud-computing"],
        "english_language_arts": ["academic-writing", "literary-theory", "technical-writing", "composition", "research-methods"],
        "arts": ["art-criticism", "design-systems", "music-history", "media-production", "studio-art"],
        "life_skills": ["resume-building", "internship-prep", "personal-finance", "professional-communication", "time-management"],
        "world_languages": ["advanced-spanish", "advanced-french", "translation-practice", "conversation-advanced", "global-culture"],
    },
    "adult_learning": {
        "math": ["practical-math", "personal-finance-math", "statistics-for-work", "business-math", "data-literacy"],
        "science": ["health-science-basics", "climate-science", "nutrition", "astronomy-basics", "everyday-physics"],
        "history": ["modern-world-history", "american-history-review", "civic-history", "economic-history", "global-conflicts"],
        "technology": ["excel-basics", "ai-literacy", "productivity-tools", "online-privacy", "coding-for-career-switchers"],
        "english_language_arts": ["business-writing", "reading-skills", "presentation-writing", "professional-email", "critical-thinking"],
        "arts": ["creative-hobbies", "digital-photography", "music-appreciation", "interior-design-basics", "visual-storytelling"],
        "life_skills": ["career-growth", "parenting-skills", "budgeting", "health-wellness", "communication-skills"],
        "world_languages": ["travel-spanish", "travel-french", "workplace-english", "conversation-practice", "language-refreshers"],
    },
}

_GRADE_DIFFICULTY: dict[str, str] = {
    "preschool": "beginner",
    "elementary": "beginner",
    "elementary_school": "beginner",
    "middle_school": "beginner",
    "high_school": "intermediate",
    "college": "intermediate",
    "adult_learning": "intermediate",
    "professional": "advanced",
}

# Normalize onboarding grade-level values to GRADE_LEVEL_TOPIC_MAP keys
_GRADE_LEVEL_ALIASES: dict[str, str] = {
    "preschool": "elementary_school",
    "elementary": "elementary_school",
    "professional": "adult_learning",
}

# Normalize onboarding interest tags to GRADE_LEVEL_TOPIC_MAP category keys
_INTEREST_ALIASES: dict[str, str] = {
    "space": "science",
    "biology": "science",
    "philosophy": "english_language_arts",
    "economics": "life_skills",
    "engineering": "technology",
    "art": "arts",
    "psychology": "life_skills",
    "language": "world_languages",
}


def _interest_seed_slugs(interests: list[str], grade_level: str) -> list[str]:
    """Map user interest tags + grade level to age-appropriate starter topic slugs."""
    normalized_level = _GRADE_LEVEL_ALIASES.get(grade_level, grade_level)
    level_map = GRADE_LEVEL_TOPIC_MAP.get(normalized_level) or GRADE_LEVEL_TOPIC_MAP["high_school"]
    slugs: list[str] = []
    seen: set[str] = set()
    for tag in interests:
        normalized_tag = _INTEREST_ALIASES.get(tag.lower().strip(), tag.lower().strip())
        for slug in level_map.get(normalized_tag, []):
            if slug not in seen:
                seen.add(slug)
                slugs.append(slug)
    return slugs


def _seed_topics_bg(slugs: list[str], difficulty: str) -> None:
    """Background: upsert topic rows and run pipeline for any with no clips yet."""
    from app.agents.pipeline_agent import run_pipeline
    db = get_client()
    for slug in slugs:
        name = slug.replace("-", " ").title()
        try:
            existing = db.table("topics").select("slug").eq("slug", slug).execute()
            if not existing.data:
                db.table("topics").insert({
                    "slug": slug,
                    "name": name,
                    "difficulty": difficulty,
                    "prerequisites": [],
                }).execute()
        except Exception as exc:
            logger.warning(f"[feed] Failed to upsert seed topic {slug}: {exc}")
            continue
        try:
            clips = db.table("clips").select("id").eq("topic_slug", slug).limit(1).execute()
            if not clips.data:
                run_pipeline(slug, name)
                logger.info(f"[feed] seeded pipeline for interest topic={slug}")
        except Exception as exc:
            logger.warning(f"[feed] Failed to seed pipeline for {slug}: {exc}")
