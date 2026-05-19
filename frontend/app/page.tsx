"use client";

import { useState, useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { createLearningPath, getUserHistory, getUserProfile, type LearningPath, type LearningPathSummary } from "@/lib/api";

const SUGGESTIONS = [
  "I want to learn hashmaps and binary trees",
  "Teach me cell biology from scratch",
  "Explain machine learning basics",
  "I need to understand calculus derivatives",
];

export default function Home() {
  const router = useRouter();
  const { user, session, loading, signOut } = useAuth();
  const [query, setQuery] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [path, setPath] = useState<LearningPath | null>(null);
  const [error, setError] = useState("");
  const [history, setHistory] = useState<LearningPathSummary[]>([]);
  const [expandedSession, setExpandedSession] = useState<string | null>(null);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);

  useEffect(() => {
    if (!user || !session) return;
    getUserHistory(user.id, session.access_token).then(setHistory).catch(() => {});
    getUserProfile(user.id, session.access_token).then((p) => {
      if (!p.onboarding_complete) router.replace("/onboarding");
    }).catch(() => {});
  }, [user, session]);

  async function handleSubmit(q: string) {
    const trimmed = q.trim();
    if (!trimmed || !user || !session) return;
    setSubmitting(true);
    setError("");
    try {
      const result = await createLearningPath(trimmed, user.id, session.access_token);
      setPath(result);
    } catch {
      setError("Something went wrong. Is the backend running?");
    } finally {
      setSubmitting(false);
    }
  }

  if (loading || !user) return null;

  return (
    <main className="min-h-screen bg-black text-white flex flex-col items-center justify-center px-4">
      <div className="w-full max-w-xl space-y-8">
        {/* Header */}
        <div className="flex items-center justify-between">
          <div>
            <h1 className="text-4xl font-bold tracking-tight">LearnReel</h1>
            <p className="text-zinc-400 text-sm mt-1">{user.email}</p>
          </div>
          <button
            onClick={signOut}
            className="text-zinc-500 hover:text-white text-sm transition"
          >
            Sign out
          </button>
        </div>

        {/* Mode tabs */}
        <div className="flex gap-3">
          <button
            onClick={() => router.push("/discover")}
            className="flex-1 bg-zinc-900 border border-zinc-800 text-zinc-300 hover:text-white hover:border-zinc-600 font-medium py-3 rounded-xl text-sm transition"
          >
            Discover
          </button>
          <div className="flex-1 bg-white text-black font-semibold py-3 rounded-xl text-sm text-center cursor-default">
            Learn
          </div>
        </div>

        {!path ? (
          <>
            <div className="space-y-2">
              <p className="text-zinc-400 text-lg">What do you want to learn today?</p>
              <div className="flex gap-2">
                <input
                  className="flex-1 bg-zinc-900 border border-zinc-700 rounded-xl px-4 py-3 text-white placeholder-zinc-500 focus:outline-none focus:border-zinc-400"
                  placeholder="e.g. I want to learn hashmaps and dynamic programming"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  onKeyDown={(e) => e.key === "Enter" && handleSubmit(query)}
                  disabled={submitting}
                />
                <button
                  onClick={() => handleSubmit(query)}
                  disabled={submitting || !query.trim()}
                  className="bg-white text-black font-semibold px-5 py-3 rounded-xl disabled:opacity-40 hover:bg-zinc-100 transition"
                >
                  {submitting ? "…" : "Go"}
                </button>
              </div>
            </div>

            <div className="space-y-2">
              <p className="text-zinc-500 text-sm">Try:</p>
              {SUGGESTIONS.map((s) => (
                <button
                  key={s}
                  onClick={() => { setQuery(s); handleSubmit(s); }}
                  className="block w-full text-left text-zinc-400 hover:text-white text-sm px-3 py-2 rounded-lg hover:bg-zinc-900 transition"
                >
                  {s}
                </button>
              ))}
            </div>

            {history.length > 0 && (
              <div className="space-y-3">
                <p className="text-zinc-500 text-sm">Continue where you left off:</p>
                <div className="space-y-2">
                  {history.map((h) => (
                    <button
                      key={h.session_id}
                      onClick={() => setPath({
                        session_id: h.session_id,
                        user_query: h.user_query,
                        topics: h.topic_slugs.map((slug) => ({
                          slug,
                          name: slug.split("-").map(w => w.charAt(0).toUpperCase() + w.slice(1)).join(" "),
                          difficulty: "beginner" as const,
                          prerequisites: [],
                          rationale: "",
                        })),
                        summary: h.user_query,
                        familiarity_prompt: null,
                        suggested_start_index: 0,
                      })}
                      className="w-full text-left bg-zinc-900 border border-zinc-800 rounded-2xl px-4 py-3 hover:bg-zinc-800 hover:border-zinc-700 active:scale-[0.98] transition flex items-center justify-between"
                    >
                      <div>
                        <p className="text-white text-sm font-medium line-clamp-1">{h.user_query}</p>
                        <p className="text-zinc-500 text-xs mt-0.5">{h.topic_count} topics</p>
                      </div>
                      <span className="text-zinc-500 text-xs">→</span>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {error && <p className="text-red-400 text-sm text-center">{error}</p>}
          </>
        ) : (
          <div className="space-y-6">
            <div className="bg-zinc-900 rounded-2xl p-5 space-y-4">
              <p className="text-zinc-300 text-sm">{path.summary}</p>
              <div className="space-y-2">
                {path.topics.map((topic, i) => (
                  <button
                    key={topic.slug}
                    onClick={() => router.push(`/feed?session=${path.session_id}&start_topic=${topic.slug}`)}
                    className="w-full flex items-center gap-3 text-left rounded-xl px-4 py-3 bg-zinc-800/50 border border-zinc-700/50 hover:bg-zinc-700/60 hover:border-zinc-600 active:scale-[0.98] transition cursor-pointer"
                  >
                    <span className="text-zinc-500 text-xs w-5 shrink-0">{i + 1}.</span>
                    <div className="flex-1 min-w-0">
                      <p className="font-medium text-white text-sm">{topic.name}</p>
                      <p className="text-zinc-500 text-xs capitalize mt-0.5">{topic.difficulty}</p>
                    </div>
                    <span className="text-zinc-400 text-sm shrink-0">▶</span>
                  </button>
                ))}
              </div>
            </div>

            {/* Familiarity prompt */}
            {path.familiarity_prompt && path.suggested_start_index > 0 ? (
              <div className="bg-zinc-900 border border-zinc-700 rounded-2xl p-4 space-y-3">
                <p className="text-zinc-300 text-sm">{path.familiarity_prompt}</p>
                <div className="flex gap-3">
                  <button
                    onClick={() => router.push(`/feed?session=${path.session_id}`)}
                    className="flex-1 border border-zinc-700 text-zinc-300 hover:text-white py-3 rounded-xl text-sm font-medium transition"
                  >
                    Start from scratch
                  </button>
                  <button
                    onClick={() => router.push(`/feed?session=${path.session_id}&start=${path.suggested_start_index}`)}
                    className="flex-1 bg-white text-black font-semibold py-3 rounded-xl text-sm hover:bg-zinc-100 transition"
                  >
                    Jump ahead →
                  </button>
                </div>
              </div>
            ) : (
              <button
                onClick={() => router.push(`/feed?session=${path.session_id}`)}
                className="w-full bg-white text-black font-semibold py-4 rounded-xl text-lg hover:bg-zinc-100 transition"
              >
                Start Watching →
              </button>
            )}

            <button
              onClick={() => { setPath(null); setQuery(""); }}
              className="w-full text-zinc-500 hover:text-white text-sm py-2 transition"
            >
              Start over
            </button>
          </div>
        )}
      </div>
    </main>
  );
}
