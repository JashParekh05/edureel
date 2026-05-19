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
  const touchStartY = useRef<number | null>(null);

  useEffect(() => {
    if (!loading && !user) router.replace("/login");
  }, [user, loading, router]);

  useEffect(() => {
    if (!user || !session) return;
    getDiscoverFeed(user.id, session.access_token).then((c) => {
      setClips(c);
      setFetching(false);
    }).catch(() => setFetching(false));
  }, [user, session]);

  activeIndexRef.current = activeIndex;

  const goTo = useCallback((idx: number) => {
    if (!clips.length) return;
    const clamped = Math.max(0, Math.min(clips.length - 1, idx));
    if (clamped === activeIndexRef.current) return;
    const el = containerRef.current?.querySelectorAll("[data-index]")[clamped] as HTMLElement;
    el?.scrollIntoView({ behavior: "smooth" });
    setActiveIndex(clamped);
  }, [clips.length]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const handleWheel = (e: WheelEvent) => {
      e.preventDefault();
      goTo(activeIndexRef.current + (e.deltaY > 0 ? 1 : -1));
    };
    container.addEventListener("wheel", handleWheel, { passive: false });
    return () => container.removeEventListener("wheel", handleWheel);
  }, [goTo]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    const handleTouchStart = (e: TouchEvent) => { touchStartY.current = e.touches[0].clientY; };
    const handleTouchEnd = (e: TouchEvent) => {
      if (touchStartY.current === null) return;
      const delta = touchStartY.current - e.changedTouches[0].clientY;
      if (Math.abs(delta) > 40) goTo(activeIndexRef.current + (delta > 0 ? 1 : -1));
      touchStartY.current = null;
    };
    container.addEventListener("touchstart", handleTouchStart, { passive: true });
    container.addEventListener("touchend", handleTouchEnd, { passive: true });
    return () => {
      container.removeEventListener("touchstart", handleTouchStart);
      container.removeEventListener("touchend", handleTouchEnd);
    };
  }, [goTo]);

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
          <div key={clip.id} data-index={i} className="w-full snap-start snap-always relative" style={{ height: "100dvh" }}>
            <ReelPlayer
              clip={clip}
              active={i === activeIndex}
              onEnded={() => goTo(i + 1)}
              onFeedback={(type) => recordClipEvent(clip.id, 0, false, null, 0, type, session?.access_token ?? "")}
            />
          </div>
        ))}

        {/* End card */}
        <div className="snap-start snap-always" style={{ height: "100dvh" }}>
          <div className="h-full flex flex-col items-center justify-center gap-5 bg-black text-white px-6">
            <p className="text-2xl font-semibold text-center">You're all caught up</p>
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
                  .then((more) => setClips((prev) => [...prev, ...more]))
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
