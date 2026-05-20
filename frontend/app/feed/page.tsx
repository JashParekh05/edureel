"use client";

import { Suspense, useEffect, useRef, useState, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { getPathFeed, getTopicFeed, recordClipEvent, getRecommendations, type Clip, type FeedResponse, type TopicRecommendation } from "@/lib/api";
import ReelPlayer from "@/components/ReelPlayer";

const POLL_INTERVAL_MS = 4000;

function FeedContent() {
  const params = useSearchParams();
  const router = useRouter();
  const { session } = useAuth();
  const sessionId = params.get("session");
  const topicSlug = params.get("topic");

  const startTopicSlug = params.get("start_topic") ?? null;
  const startIndex = Math.max(0, parseInt(params.get("start") ?? "0") || 0);

  const [clips, setClips] = useState<Clip[]>([]);
  const [activeIndex, setActiveIndex] = useState(startIndex);
  const [processing, setProcessing] = useState(false);
  const [topicLabels, setTopicLabels] = useState<Record<string, string>>({});
  const [timedOut, setTimedOut] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [recommendations, setRecommendations] = useState<TopicRecommendation[]>([]);

  const containerRef = useRef<HTMLDivElement>(null);
  const pollingRef = useRef<NodeJS.Timeout | undefined>(undefined);
  const activeIndexRef = useRef(0);
  const clipsRef = useRef<Clip[]>([]);
  const sessionIdRef = useRef(sessionId);
  const sessionTokenRef = useRef(session?.access_token ?? "");
  const clipStartRef = useRef<number>(Date.now());
  const clipVisitsRef = useRef<Record<string, number>>({});
  const seenClipIdsRef = useRef<Set<string>>(new Set());
  const fetchingMoreRef = useRef(false);
  const loadFeed = useCallback(async () => {
    try {
      if (sessionId) {
        const feeds: FeedResponse[] = await getPathFeed(sessionId, session?.access_token ?? "");
        const allClips = feeds.flatMap((f) => f.clips);
        const labels: Record<string, string> = {};
        feeds.forEach((f) => {
          f.clips.forEach((c) => { labels[c.id] = f.topic_slug; });
        });
        allClips.forEach((c) => seenClipIdsRef.current.add(c.id));
        if (startTopicSlug) {
          const idx = allClips.findIndex((c) => labels[c.id] === startTopicSlug);
          if (idx >= 0) resolvedStartRef.current = idx;
        }
        setClips((prev) => {
          if (prev.length === 0) return allClips;
          const existingIds = new Set(prev.map((c) => c.id));
          const brandNew = allClips.filter((c) => !existingIds.has(c.id));
          return brandNew.length > 0 ? [...prev, ...brandNew] : prev;
        });
        setTopicLabels((prev) => ({ ...prev, ...labels }));
        setProcessing(feeds.some((f) => f.processing));
        setLoadError(false);
      } else if (topicSlug) {
        const feed = await getTopicFeed(topicSlug, session?.access_token ?? "");
        setClips(feed.clips);
        setProcessing(feed.processing);
        setLoadError(false);
        const labels: Record<string, string> = {};
        feed.clips.forEach((c) => { labels[c.id] = topicSlug; });
        setTopicLabels(labels);
      }
    } catch {
      setLoadError(true);
    }
  }, [sessionId, topicSlug, session]);

  const fetchMore = useCallback(async () => {
    if (!sessionId || fetchingMoreRef.current) return;
    fetchingMoreRef.current = true;
    try {
      const feeds: FeedResponse[] = await getPathFeed(sessionId, session?.access_token ?? "");
      const newClips = feeds.flatMap((f) => f.clips).filter((c) => !seenClipIdsRef.current.has(c.id));
      if (newClips.length === 0) return;
      const newLabels: Record<string, string> = {};
      feeds.forEach((f) => {
        f.clips.forEach((c) => { newLabels[c.id] = f.topic_slug; });
      });
      newClips.forEach((c) => seenClipIdsRef.current.add(c.id));
      setClips((prev) => [...prev, ...newClips]);
      setTopicLabels((prev) => ({ ...prev, ...newLabels }));
    } catch {
      // silently fail — user still has remaining clips
    } finally {
      fetchingMoreRef.current = false;
    }
  }, [sessionId]);

  const initialScrollDoneRef = useRef(false);
  const resolvedStartRef = useRef<number>(startIndex);

  // Restore progress from localStorage (only when no explicit start param)
  useEffect(() => {
    if (!sessionId || startTopicSlug || startIndex > 0) return;
    const saved = localStorage.getItem(`learnreel_progress_${sessionId}`);
    if (saved) resolvedStartRef.current = parseInt(saved, 10) || 0;
  }, [sessionId, startTopicSlug, startIndex]);

  useEffect(() => {
    loadFeed();
    clipStartRef.current = Date.now();
  }, [loadFeed]);

  // Persist progress when activeIndex advances
  useEffect(() => {
    if (!sessionId || activeIndex === 0) return;
    localStorage.setItem(`learnreel_progress_${sessionId}`, String(activeIndex));
  }, [activeIndex, sessionId]);

  // Scroll to resolved start index once clips are available
  useEffect(() => {
    if (initialScrollDoneRef.current || clips.length === 0) return;
    const target = resolvedStartRef.current;
    if (target === 0) { initialScrollDoneRef.current = true; return; }
    if (clips.length > target) {
      initialScrollDoneRef.current = true;
      requestAnimationFrame(() => {
        requestAnimationFrame(() => {
          const el = containerRef.current?.querySelectorAll("[data-index]")[target] as HTMLElement;
          el?.scrollIntoView({ behavior: "instant" });
          setActiveIndex(target);
        });
      });
    }
  }, [clips.length]);

  useEffect(() => {
    if (processing) {
      setTimedOut(false);
      pollingRef.current = setInterval(loadFeed, POLL_INTERVAL_MS);
      const timeout = setTimeout(() => {
        clearInterval(pollingRef.current);
        setTimedOut(true);
        setProcessing(false);
      }, 30000);
      return () => { clearInterval(pollingRef.current); clearTimeout(timeout); };
    }
    return () => clearInterval(pollingRef.current);
  }, [processing, loadFeed]);

  // Keep refs in sync so goTo/listeners never close over stale values
  activeIndexRef.current = activeIndex;
  clipsRef.current = clips;
  sessionIdRef.current = sessionId;
  sessionTokenRef.current = session?.access_token ?? "";

  const goTo = useCallback((idx: number) => {
    const clamped = Math.max(0, Math.min(clipsRef.current.length - 1, idx));
    const el = containerRef.current?.querySelectorAll("[data-index]")[clamped] as HTMLElement;
    el?.scrollIntoView({ behavior: "instant" });
  }, []);

  // Single source of truth for telemetry — fires on every activeIndex change regardless of input method
  const prevIndexRef = useRef(activeIndex);
  useEffect(() => {
    const prev = prevIndexRef.current;
    if (prev === activeIndex) return;
    const leavingClip = clipsRef.current[prev];
    if (leavingClip) {
      const watchMs = Date.now() - clipStartRef.current;
      const durationMs = (leavingClip.duration_seconds ?? 60) * 1000;
      const visits = clipVisitsRef.current[leavingClip.id] ?? 1;
      recordClipEvent(leavingClip.id, watchMs, watchMs >= durationMs * 0.8, sessionIdRef.current, Math.max(0, visits - 1), null, sessionTokenRef.current);
    }
    prevIndexRef.current = activeIndex;
    clipStartRef.current = Date.now();
    const arrivingClip = clipsRef.current[activeIndex];
    if (arrivingClip) clipVisitsRef.current[arrivingClip.id] = (clipVisitsRef.current[arrivingClip.id] ?? 0) + 1;
  }, [activeIndex]);

  // Stable IntersectionObserver — created once, re-observes new clips as count grows
  const observerRef = useRef<IntersectionObserver | null>(null);
  useEffect(() => {
    observerRef.current = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting && entry.intersectionRatio >= 0.6) {
            const idx = parseInt((entry.target as HTMLElement).dataset.index ?? "-1");
            if (idx >= 0 && idx !== activeIndexRef.current) setActiveIndex(idx);
          }
        }
      },
      { root: containerRef.current, threshold: 0.6 }
    );
    return () => observerRef.current?.disconnect();
  }, []);
  useEffect(() => {
    containerRef.current?.querySelectorAll("[data-index]").forEach((el) => observerRef.current?.observe(el));
  }, [clips.length]);

  // Keyboard navigation
  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") goTo(activeIndexRef.current + 1);
      if (e.key === "ArrowUp") goTo(activeIndexRef.current - 1);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [goTo]);

  // Fetch more clips when 2 from the end (uses updated interest vector)
  useEffect(() => {
    if (sessionId && clips.length > 0 && activeIndex >= clips.length - 2) {
      fetchMore();
    }
  }, [activeIndex, clips.length, sessionId, fetchMore]);

  // Fetch recommendations when user reaches the last clip
  useEffect(() => {
    if (!sessionId || clips.length === 0 || activeIndex < clips.length - 1) return;
    getRecommendations(sessionId, session?.access_token ?? "").then(setRecommendations).catch(() => {});
  }, [activeIndex, clips.length, sessionId]);

  // Derive current topic name from active clip
  const activeClip = clips[activeIndex];
  const activeTopicSlug = activeClip ? (topicLabels[activeClip.id] ?? topicSlug ?? "") : "";
  const activeTopicName = activeTopicSlug
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");

  if (!sessionId && !topicSlug) {
    return (
      <div className="min-h-screen bg-black text-white flex items-center justify-center">
        <div className="text-center space-y-4">
          <p className="text-zinc-400">No topic selected.</p>
          <button onClick={() => router.push("/")} className="text-white underline">Go back</button>
        </div>
      </div>
    );
  }

  // Network/load error with no clips
  if (loadError && clips.length === 0) {
    return (
      <div className="fixed inset-0 bg-black flex flex-col items-center justify-center gap-5 text-white px-6">
        <button onClick={() => router.push("/")} className="absolute top-4 left-4 text-zinc-500 hover:text-white text-sm transition">
          ← Home
        </button>
        <p className="text-2xl font-semibold text-center">Couldn't load clips</p>
        <p className="text-zinc-500 text-sm text-center">Check that the backend is running.</p>
        <button
          onClick={() => { setLoadError(false); loadFeed(); }}
          className="bg-white text-black font-semibold px-6 py-3 rounded-2xl text-sm hover:bg-zinc-100 transition"
        >
          Retry
        </button>
      </div>
    );
  }

  // No clips and timed out
  if (timedOut && clips.length === 0) {
    return (
      <div className="fixed inset-0 bg-black flex flex-col items-center justify-center gap-5 text-white px-6">
        <button
          onClick={() => router.push("/")}
          className="absolute top-4 left-4 text-zinc-500 hover:text-white text-sm transition"
        >
          ← Home
        </button>
        <p className="text-2xl font-semibold text-center">No clips found</p>
        <p className="text-zinc-500 text-sm text-center">Try a different topic — we may not have content for this one yet.</p>
        <button
          onClick={() => router.push("/")}
          className="bg-white text-black font-semibold px-6 py-3 rounded-2xl text-sm hover:bg-zinc-100 transition"
        >
          Try another topic →
        </button>
      </div>
    );
  }

  // Pure loading (no clips at all yet)
  if (processing && clips.length === 0) {
    return (
      <div className="fixed inset-0 bg-black flex flex-col items-center justify-center gap-5 text-white">
        <button
          onClick={() => router.push("/")}
          className="absolute top-4 left-4 text-zinc-500 hover:text-white text-sm transition"
        >
          ← Home
        </button>
        <div className="w-12 h-12 border-2 border-zinc-700 border-t-white rounded-full animate-spin" />
        <div className="text-center space-y-1">
          <p className="text-white font-medium">Finding clips for you</p>
          <p className="text-zinc-500 text-sm">Hang tight…</p>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black">
      {/* HUD */}
      <div className="absolute top-0 inset-x-0 z-20 flex items-center justify-between px-4 pt-4 pb-2 pointer-events-none">
        <button
          onClick={() => router.push("/")}
          className="pointer-events-auto text-white bg-black/40 backdrop-blur-sm rounded-full px-3 py-1.5 text-sm leading-none"
        >
          ← Home
        </button>

        {activeTopicName && (
          <span className="text-white/70 text-xs font-medium tracking-wide max-w-[45%] truncate">
            {activeTopicName}
          </span>
        )}

        <span className="text-zinc-500 text-xs tabular-nums">
          {clips.length > 0 ? `${activeIndex + 1} / ${clips.length}` : ""}
          {processing && clips.length > 0 && (
            <span className="ml-1 text-amber-400">•</span>
          )}
        </span>
      </div>

      {/* Nav arrows */}
      {clips.length > 0 && (
        <div className="absolute left-3 top-1/2 -translate-y-1/2 z-20 flex flex-col gap-2">
          <button
            onClick={() => goTo(activeIndex - 1)}
            disabled={activeIndex === 0}
            className="w-9 h-9 rounded-full bg-black/40 backdrop-blur-sm border border-zinc-700 flex items-center justify-center text-white disabled:opacity-20 hover:bg-black/60 transition active:scale-95"
          >
            ▲
          </button>
          <button
            onClick={() => goTo(activeIndex + 1)}
            disabled={activeIndex >= clips.length}
            className="w-9 h-9 rounded-full bg-black/40 backdrop-blur-sm border border-zinc-700 flex items-center justify-center text-white disabled:opacity-20 hover:bg-black/60 transition active:scale-95"
          >
            ▼
          </button>
        </div>
      )}

      {/* Progress bar */}
      {clips.length > 0 && (
        <div className="absolute top-0 inset-x-0 z-30 h-0.5 bg-zinc-800">
          <div
            className="h-full bg-white transition-all duration-300"
            style={{ width: `${((activeIndex + 1) / clips.length) * 100}%` }}
          />
        </div>
      )}

      {/* Clip scroll container */}
      <div
        ref={containerRef}
        className="h-full overflow-y-scroll snap-y snap-mandatory"
        style={{ scrollbarWidth: "none" }}
      >
        {clips.map((clip, i) => (
          <div
            key={clip.id}
            data-index={i}
            className="w-full relative snap-start snap-always"
            style={{ height: "100dvh" }}
          >
            {i === activeIndex ? (
              <ReelPlayer
                clip={clip}
                active={true}
                onEnded={() => goTo(i + 1)}
                onFeedback={sessionId ? (type) => {
                  recordClipEvent(clip.id, Date.now() - clipStartRef.current, false, sessionId, 0, type, session?.access_token ?? "");
                  if (type === "already_know") {
                    setClips((prev) => prev.filter((c) => c.id === clip.id || topicLabels[c.id] !== topicLabels[clip.id]));
                    goTo(i + 1);
                  }
                } : undefined}
              />
            ) : null}
          </div>
        ))}

        {/* End card */}
        {clips.length > 0 && !processing && (
          <div className="snap-start snap-always" style={{ height: "100dvh" }}>
            <div className="h-full flex flex-col items-center justify-center gap-5 bg-black text-white px-6">
              <p className="text-2xl font-semibold text-center">You finished this topic.</p>
              <p className="text-zinc-500 text-sm text-center">
                You watched {clips.length} clip{clips.length !== 1 ? "s" : ""}.
              </p>
              {recommendations.length > 0 ? (
                <>
                  <p className="text-zinc-400 text-sm font-medium">What to learn next:</p>
                  <div className="w-full max-w-sm space-y-3">
                    {recommendations.map((rec) => (
                      <button
                        key={rec.slug}
                        onClick={() => router.push(`/feed?topic=${rec.slug}`)}
                        className="w-full text-left bg-zinc-900 border border-zinc-800 rounded-2xl px-4 py-3 hover:bg-zinc-800 active:scale-95 transition"
                      >
                        <p className="text-white font-semibold text-sm">{rec.name}</p>
                        <p className="text-zinc-500 text-xs mt-0.5">{rec.clip_count} clips · {rec.difficulty}</p>
                      </button>
                    ))}
                  </div>
                </>
              ) : (
                <button
                  onClick={() => router.push("/")}
                  className="bg-white text-black font-semibold px-6 py-3 rounded-2xl text-sm hover:bg-zinc-100 transition"
                >
                  Learn something new →
                </button>
              )}
            </div>
          </div>
        )}

        {/* Still loading more */}
        {clips.length > 0 && processing && (
          <div className="snap-start snap-always" style={{ height: "100dvh" }}>
            <div className="h-full flex flex-col items-center justify-center gap-4 bg-black text-white">
              <div className="w-8 h-8 border-2 border-zinc-700 border-t-white rounded-full animate-spin" />
              <p className="text-zinc-500 text-sm">Loading more clips…</p>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

export default function FeedPage() {
  return (
    <Suspense
      fallback={
        <div className="fixed inset-0 bg-black flex items-center justify-center">
          <div className="w-12 h-12 border-2 border-zinc-700 border-t-white rounded-full animate-spin" />
        </div>
      }
    >
      <FeedContent />
    </Suspense>
  );
}
