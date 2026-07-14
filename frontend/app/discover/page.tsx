"use client";

import { useEffect, useRef, useState, useCallback } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { getDiscoverFeed, recordClipEvent, type Clip } from "@/lib/api";
import { flushClipEvent, type LastLogged } from "@/lib/clip-telemetry";
import { shareOrCopy, topicShareUrl } from "@/lib/share";
import ReelPlayer from "@/components/ReelPlayer";

export default function DiscoverPage() {
  const router = useRouter();
  const { user, session, loading, isGuest } = useAuth();
  const [clips, setClips] = useState<Clip[]>([]);
  const [activeIndex, setActiveIndex] = useState(0);
  const [fetching, setFetching] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [coldStartTimedOut, setColdStartTimedOut] = useState(false);
  const [readySession, setReadySession] = useState<string | null>(null);
  const [shareToast, setShareToast] = useState<string | null>(null);
  const pollRef = useRef<NodeJS.Timeout | undefined>(undefined);
  const coldStartTimeoutRef = useRef<NodeJS.Timeout | undefined>(undefined);

  const containerRef = useRef<HTMLDivElement>(null);
  const activeIndexRef = useRef(0);
  const clipsRef = useRef<Clip[]>([]);
  const sessionTokenRef = useRef(session?.access_token ?? "");
  const fetchingMoreRef = useRef(false);
  const seenClipIdsRef = useRef<Set<string>>(new Set());
  const isGuestRef = useRef(isGuest);
  const lastLoggedRef = useRef<LastLogged | null>(null);

  activeIndexRef.current = activeIndex;
  clipsRef.current = clips;
  sessionTokenRef.current = session?.access_token ?? "";
  isGuestRef.current = isGuest;

  // Record clip ids as seen (in-session dedupe) and BOUND the set so a long
  // session can't grow it unbounded — the most-recent ids are kept (Set is
  // insertion-ordered) and also sent to the backend as `exclude` so load-more
  // never re-returns clips already on screen.
  const markSeen = useCallback((ids: string[]) => {
    const s = seenClipIdsRef.current;
    for (const id of ids) s.add(id);
    if (s.size > 500) seenClipIdsRef.current = new Set(Array.from(s).slice(-300));
  }, []);

  // Load the feed exactly ONCE per signed-in user. Keyed on the primitive
  // user id (not the session/user objects) and guarded by a ref, so Supabase
  // re-emitting auth events (tab focus, token refresh) can never re-run this
  // and swap the clips array out from under an in-progress scroll.
  const feedLoadedForRef = useRef<string | null>(null);
  useEffect(() => {
    if (!user || feedLoadedForRef.current === user.id) return;
    feedLoadedForRef.current = user.id;

    function doFetch() {
      getDiscoverFeed(user!.id, sessionTokenRef.current, Array.from(seenClipIdsRef.current)).then(({ clips: c, processing }) => {
        const fresh = c.filter((clip) => !seenClipIdsRef.current.has(clip.id));
        markSeen(fresh.map((cl) => cl.id));
        if (fresh.length > 0) {
          setClips(fresh);
          setFetching(false);
          clearInterval(pollRef.current);
          clearTimeout(coldStartTimeoutRef.current);
        } else if (!processing) {
          // Library is settled (nothing generating) and still no clips for us —
          // stop polling rather than waiting out the full cold-start window.
          setFetching(false);
          clearInterval(pollRef.current);
          clearTimeout(coldStartTimeoutRef.current);
        }
      }).catch(() => {
        setFetching(false);
        clearInterval(pollRef.current);
      });
    }

    doFetch();
    // Poll every 4s while cold-start seeds are generating.
    // With the backend over-fetch fix, a stocked DB resolves on the first call;
    // this 12s window only covers the genuine cold-start (seeds still generating).
    pollRef.current = setInterval(doFetch, 4000);
    coldStartTimeoutRef.current = setTimeout(() => {
      clearInterval(pollRef.current);
      setFetching(false);
      setColdStartTimedOut(true);
    }, 12000);

    return () => {
      clearInterval(pollRef.current);
      clearTimeout(coldStartTimeoutRef.current);
    };
  }, [user?.id]);

  const goTo = useCallback((idx: number) => {
    // length (not length-1): the end card occupies one slot past the last
    // clip, so the last clip's autoplay/arrow advance can reach it.
    const clamped = Math.max(0, Math.min(clipsRef.current.length, idx));
    const el = containerRef.current?.querySelectorAll("[data-index]")[clamped] as HTMLElement;
    el?.scrollIntoView({ behavior: "instant" });
  }, []);

  const handleShare = useCallback(async () => {
    const clip = clipsRef.current[activeIndexRef.current];
    if (!clip) return;
    const result = await shareOrCopy(topicShareUrl(clip.topic_slug, clip.id), "Watch this on Curio");
    if (result === "copied" || result === "failed") {
      setShareToast(result === "copied" ? "Link copied" : "Couldn't copy link");
      setTimeout(() => setShareToast(null), 2000);
    }
  }, []);

  // Telemetry — fires on every activeIndex change regardless of input method
  const prevIndexRef = useRef(activeIndex);
  const clipStartRef = useRef<number>(Date.now());
  useEffect(() => {
    const prev = prevIndexRef.current;
    if (prev === activeIndex) return;
    const leavingClip = clipsRef.current[prev];
    if (leavingClip) {
      flushClipEvent({
        clip: leavingClip,
        startedAt: clipStartRef.current,
        sessionId: null,
        replayCount: 0,
        feedback: null,
        token: sessionTokenRef.current,
        keepalive: false,
        isGuest: isGuestRef.current,
        lastLoggedRef,
      });
    }
    prevIndexRef.current = activeIndex;
    clipStartRef.current = Date.now();
  }, [activeIndex]);

  // Flush the CURRENT clip on unmount / tab-close so the last clip is recorded
  // exactly once (the activeIndex effect only logs a clip when leaving it for
  // another index). Mirrors the path-feed page; discover has no session.
  useEffect(() => {
    const flushCurrent = (keepalive: boolean) => {
      flushClipEvent({
        clip: clipsRef.current[activeIndexRef.current],
        startedAt: clipStartRef.current,
        sessionId: null,
        replayCount: 0,
        feedback: null,
        token: sessionTokenRef.current,
        keepalive,
        isGuest: isGuestRef.current,
        lastLoggedRef,
      });
    };
    const onPageHide = () => flushCurrent(true);
    const onVisibility = () => { if (document.visibilityState === "hidden") flushCurrent(true); };
    window.addEventListener("pagehide", onPageHide);
    document.addEventListener("visibilitychange", onVisibility);
    return () => {
      window.removeEventListener("pagehide", onPageHide);
      document.removeEventListener("visibilitychange", onVisibility);
      flushCurrent(true);
    };
  }, []);

  // Auto-load more when 2 from the end
  useEffect(() => {
    if (!user || clips.length === 0 || activeIndex < clips.length - 2) return;
    if (fetchingMoreRef.current) return;
    fetchingMoreRef.current = true;
    getDiscoverFeed(user.id, sessionTokenRef.current, Array.from(seenClipIdsRef.current))
      .then((more) => {
        const fresh = more.clips.filter((clip) => !seenClipIdsRef.current.has(clip.id));
        markSeen(fresh.map((cl) => cl.id));
        setClips((prev) => [...prev, ...fresh]);
      })
      .finally(() => { fetchingMoreRef.current = false; });
  }, [activeIndex, clips.length, user?.id]);

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

  // Toast: poll localStorage for a completed background path generation
  useEffect(() => {
    const existing = localStorage.getItem("lr_ready_session");
    if (existing) {
      localStorage.removeItem("lr_pending_query");
      localStorage.removeItem("lr_ready_session");
      setReadySession(existing);
      return;
    }
    if (!localStorage.getItem("lr_pending_query")) return;
    const interval = setInterval(() => {
      const sess = localStorage.getItem("lr_ready_session");
      if (sess) {
        clearInterval(interval);
        localStorage.removeItem("lr_pending_query");
        localStorage.removeItem("lr_ready_session");
        setReadySession(sess);
      }
    }, 2000);
    return () => clearInterval(interval);
  }, []);

  if (loading || !user) return null;

  const readyToast = readySession ? (
    <div className="absolute bottom-8 inset-x-4 z-30 flex justify-center">
      <div className="flex items-center gap-3 rounded-pill bg-primary text-on-primary px-4 py-3 shadow-elev-2">
        <button
          onClick={() => router.push(`/feed?session=${readySession}`)}
          className="text-sm font-semibold"
        >
          Your learning path is ready →
        </button>
        <button
          onClick={() => setReadySession(null)}
          className="opacity-70 hover:opacity-100 text-sm leading-none"
          aria-label="Dismiss"
        >
          ✕
        </button>
      </div>
    </div>
  ) : null;

  const homeButton = (
    <button
      onClick={() => router.push("/")}
      className="absolute top-4 left-4 rounded-pill bg-surface-alt text-on-surface text-sm font-semibold px-4 py-2 shadow-elev-1 transition hover:brightness-95"
    >
      Home
    </button>
  );

  if (fetching) {
    return (
      <div className="fixed inset-0 bg-canvas flex flex-col items-center justify-center gap-5 text-on-surface">
        {homeButton}
        <div className="w-10 h-10 border-[3px] border-outline border-t-primary rounded-full animate-spin" />
        <p className="text-on-surface-muted text-sm font-medium">Loading your feed…</p>
        {readyToast}
      </div>
    );
  }

  if (clips.length === 0) {
    if (coldStartTimedOut) {
      return (
        <div className="fixed inset-0 bg-canvas flex flex-col items-center justify-center gap-4 text-on-surface px-6">
          {homeButton}
          <p className="font-display text-2xl font-extrabold text-center">Nothing to discover yet</p>
          <p className="text-on-surface-muted text-sm text-center">Tell us a topic and we&apos;ll find clips for you.</p>
          <button onClick={() => router.push("/")} className="rounded-pill bg-primary text-on-primary px-6 py-3 text-sm font-semibold shadow-elev-1 transition hover:brightness-[1.03]">
            Start your feed
          </button>
          {readyToast}
        </div>
      );
    }
    return (
      <div className="fixed inset-0 bg-canvas flex flex-col items-center justify-center gap-5 text-on-surface">
        {homeButton}
        <div className="w-10 h-10 border-[3px] border-outline border-t-primary rounded-full animate-spin" />
        <div className="text-center space-y-1">
          <p className="font-display font-extrabold">Building your feed</p>
          <p className="text-on-surface-muted text-sm">Finding clips for your interests</p>
        </div>
        {readyToast}
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black">
      {/* Progress bar */}
      <div className="absolute top-0 inset-x-0 z-30 h-1 bg-white/20">
        <div
          className="h-full bg-primary transition-all duration-300"
          style={{ width: `${Math.min(100, ((activeIndex + 1) / clips.length) * 100)}%` }}
        />
      </div>

      {/* HUD — glassy chrome over the video, pushed below the embed's top bar */}
      <div className="absolute top-0 inset-x-0 z-20 flex items-center justify-between px-4 pt-12 pb-2 pointer-events-none">
        <span className="rounded-pill bg-black/40 backdrop-blur-sm text-white text-xs font-semibold px-3 py-1.5 tabular-nums pointer-events-auto">{Math.min(activeIndex + 1, clips.length)} / {clips.length}</span>
        <span className="flex items-center gap-2 pointer-events-auto">
          <button
            onClick={() => router.push("/")}
            className="rounded-pill bg-black/40 backdrop-blur-sm text-white font-semibold px-4 py-2 text-sm leading-none transition hover:bg-black/55"
          >
            Home
          </button>
          <span className="rounded-pill bg-black/40 backdrop-blur-sm text-white text-xs font-bold tracking-wide px-3 py-1.5">Discover</span>
        </span>
        <button
          onClick={handleShare}
          className="rounded-pill bg-primary text-on-primary font-semibold px-3 py-1.5 text-xs leading-none shadow-elev-1 transition hover:brightness-[1.05] pointer-events-auto"
        >
          Share
        </button>
      </div>

      {/* Nav arrows */}
      <div className="absolute left-3 top-1/2 -translate-y-1/2 z-20 flex flex-col gap-2">
        <button
          onClick={() => goTo(activeIndex - 1)}
          disabled={activeIndex === 0}
          aria-label="Previous clip"
          className="rounded-pill bg-black/40 backdrop-blur-sm w-10 h-10 flex items-center justify-center text-white text-lg transition hover:bg-black/55 disabled:opacity-20"
        >
          ↑
        </button>
        <button
          onClick={() => goTo(activeIndex + 1)}
          disabled={activeIndex >= clips.length - 1}
          aria-label="Next clip"
          className="rounded-pill bg-black/40 backdrop-blur-sm w-10 h-10 flex items-center justify-center text-white text-lg transition hover:bg-black/55 disabled:opacity-20"
        >
          ↓
        </button>
      </div>

      {/* Learning path ready toast */}
      {readyToast}

      {shareToast && (
        <div className="absolute bottom-8 inset-x-0 z-40 flex justify-center pointer-events-none">
          <div className="rounded-pill bg-on-surface text-canvas text-sm font-semibold px-4 py-2 shadow-elev-2">
            {shareToast}
          </div>
        </div>
      )}

      {/* Scroll container */}
      <div ref={containerRef} className="h-full overflow-y-scroll snap-y snap-mandatory" style={{ scrollbarWidth: "none" }}>
        {clips.map((clip, i) => (
          <div key={clip.id} data-index={i} className="w-full relative snap-start snap-always" style={{ height: "100dvh" }}>
            {i === activeIndex ? (
              <ReelPlayer
                clip={clip}
                mode="active"
                onEnded={() => goTo(i + 1)}
                onFeedback={(type) => recordClipEvent(clip.id, 0, false, null, 0, type, sessionTokenRef.current)}
                onLearnThis={() => router.push(`/feed?topic=${encodeURIComponent(clip.topic_slug)}`)}
              />
            ) : null}
          </div>
        ))}

        {/* End card */}
        <div data-index={clips.length} className="snap-start snap-always" style={{ height: "100dvh" }}>
          <div className="h-full flex flex-col items-center justify-center gap-5 bg-canvas text-on-surface px-6">
            <p className="font-display text-3xl font-extrabold text-center">You&apos;re all caught up</p>
            <p className="text-on-surface-muted text-sm text-center">Want to go deeper on something?</p>
            <button
              onClick={() => router.push("/")}
              className="rounded-pill bg-primary text-on-primary px-6 py-3 text-sm font-semibold shadow-elev-1 transition hover:brightness-[1.03]"
            >
              Learn something specific
            </button>
            <button
              disabled={loadingMore}
              onClick={() => {
                if (!user || !session || loadingMore) return;
                setLoadingMore(true);
                getDiscoverFeed(user.id, session.access_token, Array.from(seenClipIdsRef.current))
                  .then((more) => {
                    const fresh = more.clips.filter((clip) => !seenClipIdsRef.current.has(clip.id));
                    markSeen(fresh.map((cl) => cl.id));
                    setClips((prev) => [...prev, ...fresh]);
                  })
                  .finally(() => setLoadingMore(false));
              }}
              className="rounded-pill bg-surface-alt text-on-surface px-5 py-2.5 text-sm font-semibold border border-outline transition hover:brightness-95 disabled:opacity-40"
            >
              {loadingMore ? "Loading…" : "Load more clips"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
