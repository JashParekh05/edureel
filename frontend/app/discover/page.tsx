"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { getDiscoverFeed, recordClipEvent, type Clip } from "@/lib/api";
import ReelPlayer from "@/components/ReelPlayer";

export default function DiscoverPage() {
  const router = useRouter();
  const { user, session, loading } = useAuth();
  const [clips, setClips] = useState<Clip[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [fetching, setFetching] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const activeIndexRef = useRef(0);
  const clipsRef = useRef<Clip[]>([]);
  const sessionTokenRef = useRef(session?.access_token ?? "");
  const fetchingMoreRef = useRef(false);
  const seenClipIdsRef = useRef<Set<string>>(new Set());

  activeIndexRef.current = activeIndex;
  clipsRef.current = clips;
  sessionTokenRef.current = session?.access_token ?? "";

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);

  useEffect(() => {
    if (!user || !session) return;
    getDiscoverFeed(user.id, session.access_token).then((c) => {
      const fresh = c.filter((clip) => !seenClipIdsRef.current.has(clip.id));
      fresh.forEach((clip) => seenClipIdsRef.current.add(clip.id));
      setClips(fresh);
      setFetching(false);
    }).catch(() => setFetching(false));
  }, [user, session]);

  const goTo = useCallback((idx: number) => {
    const clamped = Math.max(0, Math.min(clipsRef.current.length - 1, idx));
    const el = containerRef.current?.querySelectorAll("[data-index]")[clamped] as HTMLElement;
    el?.scrollIntoView({ behavior: "instant" });
  }, []);

  // Telemetry — fires on every activeIndex change regardless of input method
  const prevIndexRef = useRef(activeIndex);
  const clipStartRef = useRef<number>(Date.now());
  useEffect(() => {
    const prev = prevIndexRef.current;
    if (prev === activeIndex) return;
    const leavingClip = clipsRef.current[prev];
    if (leavingClip) {
      const watchMs = Date.now() - clipStartRef.current;
      const durationMs = (leavingClip.duration_seconds ?? 60) * 1000;
      recordClipEvent(leavingClip.id, watchMs, watchMs >= durationMs * 0.8, null, 0, null, sessionTokenRef.current);
    }
    prevIndexRef.current = activeIndex;
    clipStartRef.current = Date.now();
  }, [activeIndex]);

  // Auto-load more when 2 from the end
  useEffect(() => {
    if (!user || !session || clips.length === 0 || activeIndex < clips.length - 2) return;
    if (fetchingMoreRef.current) return;
    fetchingMoreRef.current = true;
    getDiscoverFeed(user.id, session.access_token)
      .then((more) => {
        const fresh = more.filter((clip) => !seenClipIdsRef.current.has(clip.id));
        fresh.forEach((clip) => seenClipIdsRef.current.add(clip.id));
        setClips((prev) => [...prev, ...fresh]);
      })
      .finally(() => { fetchingMoreRef.current = false; });
  }, [activeIndex, clips.length, user, session]);

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

  useEffect(() => {
    const handleKey = (e: KeyboardEvent) => {
      if (e.key === "ArrowDown") goTo(activeIndexRef.current + 1);
      if (e.key === "ArrowUp") goTo(activeIndexRef.current - 1);
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [goTo]);

  if (loading || !user) return null;

  if (fetching) {
    return (
      <div className="fixed inset-0 bg-black flex flex-col items-center justify-center gap-5 text-white">
        <button onClick={() => router.push("/")} className="absolute top-4 left-4 text-zinc-500 hover:text-white text-sm transition">
          ← Home
        </button>
        <div className="w-12 h-12 border-2 border-zinc-700 border-t-white rounded-full animate-spin" />
        <p className="text-zinc-500 text-sm">Loading your feed…</p>
      </div>
    );
  }

  if (clips.length === 0) {
    return (
      <div className="fixed inset-0 bg-black flex flex-col items-center justify-center gap-5 text-white px-6">
        <button onClick={() => router.push("/")} className="absolute top-4 left-4 text-zinc-500 hover:text-white text-sm transition">
          ← Home
        </button>
        <p className="text-2xl font-semibold text-center">Nothing to discover yet</p>
        <p className="text-zinc-500 text-sm text-center">Try learning a few topics first — we'll find more content for you.</p>
        <button onClick={() => router.push("/")} className="bg-white text-black font-semibold px-6 py-3 rounded-2xl text-sm hover:bg-zinc-100 transition">
          Start learning →
        </button>
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
        <span className="text-white/70 text-xs font-medium tracking-wide">Discover</span>
        <span className="text-zinc-500 text-xs tabular-nums">
          {activeIndex + 1} / {clips.length}
        </span>
      </div>

      {/* Nav arrows */}
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

      {/* Progress bar */}
      <div className="absolute top-0 inset-x-0 z-30 h-0.5 bg-zinc-800">
        <div
          className="h-full bg-white transition-all duration-300"
          style={{ width: `${((activeIndex + 1) / clips.length) * 100}%` }}
        />
      </div>

      {/* Scroll container */}
      <div ref={containerRef} className="h-full overflow-y-scroll snap-y snap-mandatory" style={{ scrollbarWidth: "none" }}>
        {clips.map((clip, i) => (
          <div key={clip.id} data-index={i} className="w-full relative snap-start snap-always" style={{ height: "100dvh" }}>
            {i === activeIndex ? (
              <ReelPlayer
                clip={clip}
                active={true}
                onEnded={() => goTo(i + 1)}
                onFeedback={(type) => recordClipEvent(clip.id, 0, false, null, 0, type, sessionTokenRef.current)}
              />
            ) : null}
          </div>
        ))}

        {/* End card */}
        <div className="snap-start snap-always" style={{ height: "100dvh" }}>
          <div className="h-full flex flex-col items-center justify-center gap-5 bg-black text-white px-6">
            <p className="text-2xl font-semibold text-center">You&apos;re all caught up</p>
            <p className="text-zinc-500 text-sm text-center">Want to go deeper on something?</p>
            <button
              onClick={() => router.push("/")}
              className="bg-white text-black font-semibold px-6 py-3 rounded-2xl text-sm hover:bg-zinc-100 transition"
            >
              Learn something specific →
            </button>
            <button
              disabled={loadingMore}
              onClick={() => {
                if (!user || !session || loadingMore) return;
                setLoadingMore(true);
                getDiscoverFeed(user.id, session.access_token)
                  .then((more) => {
                    const fresh = more.filter((clip) => !seenClipIdsRef.current.has(clip.id));
                    fresh.forEach((clip) => seenClipIdsRef.current.add(clip.id));
                    setClips((prev) => [...prev, ...fresh]);
                  })
                  .finally(() => setLoadingMore(false));
              }}
              className="text-zinc-500 text-sm hover:text-white transition disabled:opacity-40"
            >
              {loadingMore ? "Loading…" : "Load more clips"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
