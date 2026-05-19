from pydantic import BaseModel
from typing import Literal


class Topic(BaseModel):
    slug: str
    name: str
    difficulty: Literal["beginner", "intermediate", "advanced"]
    prerequisites: list[str] = []
    rationale: str


class LearningPath(BaseModel):
    session_id: str
    user_query: str
    topics: list[Topic]
    summary: str
    familiarity_prompt: str | None = None
    suggested_start_index: int = 0


class Clip(BaseModel):
    id: str
    topic_slug: str
    title: str
    description: str | None
    video_url: str
    thumbnail_url: str | None
    duration_seconds: int | None
    transcript: str | None
    source_url: str | None
    source_platform: str | None
    hook_score: float = 0.5
    created_at: str | None = None


class InterestsPayload(BaseModel):
    interests: list[str]


class ClipEvent(BaseModel):
    session_id: str | None = None
    watch_ms: int
    completed: bool = False
    replay_count: int = 0
    feedback: Literal["want_more", "already_know"] | None = None


class TopicRequest(BaseModel):
    query: str
    user_id: str | None = None


class FeedResponse(BaseModel):
    topic_slug: str
    clips: list[Clip]
    processing: bool = False


class TopicRecommendation(BaseModel):
    slug: str
    name: str
    difficulty: str
    clip_count: int
    rationale: str
