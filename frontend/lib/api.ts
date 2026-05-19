const API_BASE = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

export interface Topic {
  slug: string;
  name: string;
  difficulty: "beginner" | "intermediate" | "advanced";
  prerequisites: string[];
  rationale: string;
}

export interface LearningPath {
  session_id: string;
  user_query: string;
  topics: Topic[];
  summary: string;
  familiarity_prompt: string | null;
  suggested_start_index: number;
}

export interface Clip {
  id: string;
  topic_slug: string;
  title: string;
  description: string | null;
  video_url: string;
  thumbnail_url: string | null;
  duration_seconds: number | null;
  transcript: string | null;
  source_url: string | null;
  source_platform: string | null;
  hook_score: number;
}

export interface FeedResponse {
  topic_slug: string;
  clips: Clip[];
  processing: boolean;
}

export async function createLearningPath(
  query: string,
  userId?: string
): Promise<LearningPath> {
  const res = await fetch(`${API_BASE}/api/topics/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ query, user_id: userId }),
  });
  if (!res.ok) throw new Error("Failed to create learning path");
  return res.json();
}

export interface LearningPathSummary {
  session_id: string;
  user_query: string;
  topic_slugs: string[];
  topic_count: number;
  created_at: string;
}

export async function getUserHistory(userId: string): Promise<LearningPathSummary[]> {
  const res = await fetch(`${API_BASE}/api/topics/history/${encodeURIComponent(userId)}`);
  if (!res.ok) return [];
  return res.json();
}

export async function getTopicFeed(
  topicSlug: string,
  offset = 0,
  limit = 10
): Promise<FeedResponse> {
  const res = await fetch(
    `${API_BASE}/api/feed/${topicSlug}?offset=${offset}&limit=${limit}`
  );
  if (!res.ok) throw new Error("Failed to fetch feed");
  return res.json();
}

export async function getPathFeed(sessionId: string): Promise<FeedResponse[]> {
  const res = await fetch(`${API_BASE}/api/feed/path/${sessionId}`);
  if (!res.ok) throw new Error("Failed to fetch path feed");
  return res.json();
}

export interface TopicRecommendation {
  slug: string;
  name: string;
  difficulty: "beginner" | "intermediate" | "advanced";
  clip_count: number;
  rationale: string;
}

export async function getRecommendations(sessionId: string): Promise<TopicRecommendation[]> {
  const res = await fetch(`${API_BASE}/api/feed/recommendations/${sessionId}`);
  if (!res.ok) return [];
  return res.json();
}

export interface UserProfile {
  user_id: string;
  interests: string[];
  onboarding_complete: boolean;
}

export async function getUserProfile(userId: string): Promise<UserProfile> {
  const res = await fetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}/profile`);
  if (!res.ok) return { user_id: userId, interests: [], onboarding_complete: false };
  return res.json();
}

export async function setUserInterests(userId: string, interests: string[]): Promise<void> {
  await fetch(`${API_BASE}/api/users/${encodeURIComponent(userId)}/interests`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ interests }),
  });
}

export async function getDiscoverFeed(userId: string): Promise<Clip[]> {
  const res = await fetch(`${API_BASE}/api/feed/discover/${encodeURIComponent(userId)}`);
  if (!res.ok) return [];
  return res.json();
}

export function recordClipEvent(
  clipId: string,
  watchMs: number,
  completed: boolean,
  sessionId?: string | null,
  replayCount?: number,
  feedback?: "want_more" | "already_know" | null,
): void {
  fetch(`${API_BASE}/api/feed/${clipId}/events`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      watch_ms: watchMs,
      completed,
      session_id: sessionId ?? null,
      replay_count: replayCount ?? 0,
      feedback: feedback ?? null,
    }),
  }).catch(() => {});
}
