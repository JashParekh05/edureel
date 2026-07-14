"use client";

import { Suspense, Fragment, useEffect, useMemo, useRef, useState, useCallback } from "react";
import { useSearchParams, useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { getPathFeed, getTopicFeed, recordClipEvent, getRecommendations, getClipMetadata, getRemediation, type Clip, type FeedResponse, type TopicRecommendation, type Checkpoint, type RewatchClip } from "@/lib/api";
import { flushClipEvent, type LastLogged } from "@/lib/clip-telemetry";
import { computeWarmWindow, AHEAD, BEHIND } from "@/lib/warm-window";
import { createOverlayCache, getOverlay, setOverlay, hasOverlay, deriveOverlay, type OverlayCache, type OverlayMetadata } from "@/lib/overlay-cache";
import { isRefreshEligible, REFRESH_MIN_INTERVAL_MS } from "@/lib/refresh-eligibility";
import { shareOrCopy, topicShareUrl } from "@/lib/share";
import ReelPlayer from "@/components/ReelPlayer";
import SoftCheckpointCard from "@/components/SoftCheckpointCard";

const POLL_INTERVAL_MS = 4000;




function FeedContent() {
  const params = useSearchParams();
  const router = useRouter();
  const { user, session, isGuest } = useAuth();
  const sessionId = params.get("session");
  const topicSlug = params.get("topic");
  // Basic Learn: structured videos with NO quiz checkpoints. When set, every
  // checkpoint card is suppressed regardless of what the backend returns.
  const noQuiz = params.get("quiz") === "off";

  const startTopicSlug = params.get("start_topic") ?? null;
  const startSection = params.get("start_section") !== null ? parseInt(params.get("start_section")!) : null;
  const startClipId = params.get("clip");
  const startIndex = Math.max(0, parseInt(params.get("start") ?? "0") || 0);

  const [clips, setClips] = useState<Clip[]>([]);
  const [activeIndex, setActiveIndex] = useState(startIndex);
  const [processing, setProcessing] = useState(false);
  const [initialLoading, setInitialLoading] = useState(true);
  const [topicLabels, setTopicLabels] = useState<Record<string, string>>({});
  // Per-topic soft checkpoints from the feed. Empty/absent for a topic means its
  // scroll renders exactly as before (no regression).
  const [checkpointsByTopic, setCheckpointsByTopic] = useState<Record<string, Checkpoint[]>>({});
  // Soft "rewatch these clips" suggestions for the just-finished topic, shown on
  // the end-card after a weak checkpoint. Empty unless the learner did poorly.
  const [rewatchClips, setRewatchClips] = useState<RewatchClip[]>([]);
  const [timedOut, setTimedOut] = useState(false);
  const [loadError, setLoadError] = useState(false);
  const [recommendations, setRecommendations] = useState<TopicRecommendation[]>([]);
  const [shareToast, setShareToast] = useState<string | null>(null);
  // Bumped after a refresh-on-return replaces a cached overlay so the active
  // caption re-renders from the updated Overlay_Cache entry.
  const [overlayVersion, setOverlayVersion] = useState(0);

  const containerRef = useRef<HTMLDivElement>(null);
  const pollingRef = useRef<NodeJS.Timeout | undefined>(undefined);
  const activeIndexRef = useRef(0);
  const clipsRef = useRef<Clip[]>([]);
  const sessionIdRef = useRef(sessionId);
  const sessionTokenRef = useRef(session?.access_token ?? "");
  const clipStartRef = useRef<number>(Date.now());
  const clipVisitsRef = useRef<Record<string, number>>({});
  const alreadyKnowRef = useRef<Record<string, number>>({});
  const seenClipIdsRef = useRef<Set<string>>(new Set());
  const fetchingMoreRef = useRef(false);
  const isGuestRef = useRef(isGuest);
  const lastLoggedRef = useRef<LastLogged | null>(null);
  // In-memory Overlay_Cache scoped to this feed session (not persisted).
  const overlayCacheRef = useRef<OverlayCache>(createOverlayCache());
  // Kept in sync so the refresh-on-return handler (empty-dep effect) never
  // closes over a stale topicLabels map.
  const topicLabelsRef = useRef<Record<string, string>>({});
  // Refresh-on-return bookkeeping: whether a Single_Clip_Refresh is in flight,
  // and the last completed refresh time per clip id.
  const refreshInFlightRef = useRef(false);
  const lastRefreshAtRef = useRef<Record<string, number>>({});
  // Per-checkpoint answer tally keyed by topic slug, used to detect a "weak"
  // checkpoint (more wrong than right) so the end-card can surface a soft
  // rewatch suggestion for that beat. Purely advisory — never blocks advancing.
  const weakCheckpointRef = useRef<Record<string, { sectionIndex: number | null; correct: number; total: number }>>({});
  const loadFeed = useCallback(async () => {
    if (!session) return;
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
          const sectionIdx = startSection !== null
            ? allClips.findIndex((c) => labels[c.id] === startTopicSlug && c.section_index === startSection)
            : -1;
          const topicIdx = allClips.findIndex((c) => labels[c.id] === startTopicSlug);
          const resolved = sectionIdx >= 0 ? sectionIdx : topicIdx;
          if (resolved >= 0) resolvedStartRef.current = resolved;
        }
        setClips((prev) => {
          if (prev.length === 0) return allClips;
          const existingIds = new Set(prev.map((c) => c.id));
          const brandNew = allClips.filter((c) => !existingIds.has(c.id));
          return brandNew.length > 0 ? [...prev, ...brandNew] : prev;
        });
        setTopicLabels((prev) => ({ ...prev, ...labels }));
        const cpByTopic: Record<string, Checkpoint[]> = {};
        feeds.forEach((f) => { if (f.checkpoints?.length) cpByTopic[f.topic_slug] = f.checkpoints; });
        if (Object.keys(cpByTopic).length > 0) {
          setCheckpointsByTopic((prev) => ({ ...prev, ...cpByTopic }));
        }
        setProcessing(feeds.some((f) => f.processing));
        // Terminal-empty short-circuit: when every topic is out of its
        // self-heal budget (failed) or finished empty, and no clips exist at
        // all, stop polling and show the "No clips found" screen immediately
        // instead of waiting for the 5-minute timeout.
        const allTerminal = feeds.length > 0 && feeds.every((f) => f.failed || (!f.processing && f.clips.length === 0));
        if (allClips.length === 0 && allTerminal) {
          clearInterval(pollingRef.current);
          setProcessing(false);
          setTimedOut(true);
        }
        setLoadError(false);
      } else if (topicSlug) {
        const feed = await getTopicFeed(topicSlug, session?.access_token ?? "");
        setClips(feed.clips);
        setProcessing(feed.processing);
        setLoadError(false);
        const labels: Record<string, string> = {};
        feed.clips.forEach((c) => { labels[c.id] = topicSlug; });
        setTopicLabels(labels);
        setCheckpointsByTopic(feed.checkpoints?.length ? { [topicSlug]: feed.checkpoints } : {});
      }
    } catch {
      setLoadError(true);
    } finally {
      setInitialLoading(false);
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
      const cpByTopic: Record<string, Checkpoint[]> = {};
      feeds.forEach((f) => { if (f.checkpoints?.length) cpByTopic[f.topic_slug] = f.checkpoints; });
      if (Object.keys(cpByTopic).length > 0) {
        setCheckpointsByTopic((prev) => ({ ...prev, ...cpByTopic }));
      }
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
    let target = resolvedStartRef.current;
    // A shared "?clip=" deep link lands the visitor on that exact clip.
    if (startClipId) {
      const i = clips.findIndex((c) => c.id === startClipId);
      if (i >= 0) target = i;
    }
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
      // Timer resets whenever new clips arrive (clips.length in deps below),
      // so back-end auto-extend keeps the feed alive past the initial 30s.
      const timeout = setTimeout(() => {
        clearInterval(pollingRef.current);
        // Only show timeout screen if we still have no clips — if clips exist,
        // stop polling silently (user already has content to watch)
        if (clipsRef.current.length === 0) setTimedOut(true);
        setProcessing(false);
      }, 300000);
      return () => { clearInterval(pollingRef.current); clearTimeout(timeout); };
    }
    return () => clearInterval(pollingRef.current);
  }, [processing, clips.length, loadFeed]);

  // Keep refs in sync so goTo/listeners never close over stale values
  activeIndexRef.current = activeIndex;
  clipsRef.current = clips;
  sessionIdRef.current = sessionId;
  sessionTokenRef.current = session?.access_token ?? "";
  isGuestRef.current = isGuest;
  topicLabelsRef.current = topicLabels;
  // The end card sits one slot past the last clip; goTo may land on it so the
  // last clip's autoplay/arrow advance reaches "You finished this topic".
  const trailingCardRef = useRef(0);
  trailingCardRef.current = clips.length > 0 && !processing ? 1 : 0;
  const goTo = useCallback((idx: number) => {
    const max = clipsRef.current.length - 1 + trailingCardRef.current;
    const clamped = Math.max(0, Math.min(max, idx));
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
      const visits = clipVisitsRef.current[leavingClip.id] ?? 1;
      flushClipEvent({
        clip: leavingClip,
        startedAt: clipStartRef.current,
        sessionId: sessionIdRef.current,
        replayCount: Math.max(0, visits - 1),
        feedback: null,
        token: sessionTokenRef.current,
        keepalive: false,
        isGuest: isGuestRef.current,
        lastLoggedRef,
      });
    }
    prevIndexRef.current = activeIndex;
    clipStartRef.current = Date.now();
    const arrivingClip = clipsRef.current[activeIndex];
    if (arrivingClip) clipVisitsRef.current[arrivingClip.id] = (clipVisitsRef.current[arrivingClip.id] ?? 0) + 1;
  }, [activeIndex]);

  // Flush the CURRENT clip on unmount / tab-close. The activeIndex effect above
  // only logs a clip when you leave it for another index, so the LAST clip
  // (navigating Home, tapping a recommendation, or closing the tab) would never
  // be recorded. The shared lastLoggedRef dedups against the transition path so
  // the clip is logged exactly once. pagehide + visibilitychange cover tab close
  // and mobile backgrounding; the cleanup covers SPA route changes.
  useEffect(() => {
    const flushCurrent = (keepalive: boolean) => {
      const clip = clipsRef.current[activeIndexRef.current];
      const visits = clip ? (clipVisitsRef.current[clip.id] ?? 1) : 1;
      flushClipEvent({
        clip,
        startedAt: clipStartRef.current,
        sessionId: sessionIdRef.current,
        replayCount: Math.max(0, visits - 1),
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

  // Refresh-on-return: when the learner returns to the feed (tab re-focus or
  // route re-entry), refresh ONLY the active clip's overlay metadata via a
  // lightweight single-clip fetch. Gated by the pure isRefreshEligible so
  // returns never cause overlapping or redundant requests. This never refetches
  // the whole feed and never records watch telemetry. The separate
  // visibilitychange->hidden telemetry flush above is independent and untouched.
  useEffect(() => {
    const onReturn = () => {
      const clip = clipsRef.current[activeIndexRef.current];
      const eligible = isRefreshEligible({
        hasActiveClip: !!clip,
        inFlight: refreshInFlightRef.current,
        lastRefreshAt: clip ? (lastRefreshAtRef.current[clip.id] ?? null) : null,
        now: Date.now(),
        minIntervalMs: REFRESH_MIN_INTERVAL_MS,
      });
      if (!eligible || !clip) return;
      refreshInFlightRef.current = true;
      getClipMetadata(clip.id, sessionTokenRef.current)
        .then((fresh) => {
          if (!fresh) return; // 404 -> existing per-clip unavailable/skip path
          setOverlay(
            overlayCacheRef.current,
            fresh.id,
            deriveOverlay(fresh, topicLabelsRef.current[fresh.id] ?? topicSlug ?? ""),
          );
          setOverlayVersion((v) => v + 1); // re-render the active caption
        })
        .catch(() => {
          // keep the existing overlay; the active clip stays playable
        })
        .finally(() => {
          lastRefreshAtRef.current[clip.id] = Date.now();
          refreshInFlightRef.current = false;
        });
    };
    const onVisibility = () => {
      if (document.visibilityState === "visible") onReturn();
    };
    document.addEventListener("visibilitychange", onVisibility);
    onReturn(); // route re-entry: this effect mounts on entering the feed route
    return () => document.removeEventListener("visibilitychange", onVisibility);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

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

  // When the learner reaches the end card, surface a soft "rewatch these clips"
  // suggestion for the just-finished topic if they did poorly — either a weak
  // checkpoint (more wrong than right) or below-threshold mastery. Best-effort:
  // the remediation seam may be absent (returns []), so nothing renders and the
  // feed never blocks advancing.
  useEffect(() => {
    if (clips.length === 0 || processing || activeIndex < clips.length - 1) return;
    if (!sessionId || !session?.access_token) return;
    const finishedSlug = topicLabels[clips[clips.length - 1]?.id] ?? activeTopicSlug;
    if (!finishedSlug) {
      setRewatchClips([]);
      return;
    }
    const weak = weakCheckpointRef.current[finishedSlug];
    const isWeakCheckpoint = !!weak && weak.total > 0 && weak.correct * 2 < weak.total;
    if (!isWeakCheckpoint) {
      setRewatchClips([]);
      return;
    }
    // Prefer the weak checkpoint's beat; otherwise let the server pick (null).
    const sectionIndex = isWeakCheckpoint ? weak.sectionIndex : null;
    let cancelled = false;
    getRemediation(sessionId, finishedSlug, sectionIndex, session.access_token)
      .then((cs) => { if (!cancelled) setRewatchClips(cs); })
      .catch(() => { if (!cancelled) setRewatchClips([]); });
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeIndex, clips.length, processing, sessionId, session]);

  // Bounded warm window of indices to mount as players around the active index.
  const warmSet = useMemo(
    () => new Set(computeWarmWindow(activeIndex, clips.length, AHEAD, BEHIND)),
    [activeIndex, clips.length],
  );

  // Map each global clip index -> the soft checkpoint cards to render right
  // after it. A checkpoint's `after_clip_index` is relative to its OWN topic's
  // served clip list, so we walk the flattened feed keeping a per-topic counter
  // and resolve each topic-local index to its global position. Cards are woven
  // in as extra scroll sections WITHOUT a `data-index`, so clip indexing,
  // telemetry, the warm window, and goTo/scroll-to-index stay clip-based and
  // unchanged. Topics with no checkpoints contribute nothing (no regression).
  const checkpointsByGlobalIndex = useMemo(() => {
    const result: Record<number, Checkpoint[]> = {};
    // Basic Learn (quiz=off) suppresses all checkpoints, even if the backend
    // returned some for these topics.
    if (noQuiz || Object.keys(checkpointsByTopic).length === 0) return result;
    const topicCounters: Record<string, number> = {};
    clips.forEach((clip, gi) => {
      const slug = topicLabels[clip.id];
      if (!slug) return;
      const localIdx = topicCounters[slug] ?? 0;
      topicCounters[slug] = localIdx + 1;
      const cps = checkpointsByTopic[slug];
      if (!cps) return;
      const matching = cps.filter((c) => c.after_clip_index === localIdx);
      if (matching.length > 0) result[gi] = matching;
    });
    return result;
  }, [clips, topicLabels, checkpointsByTopic, noQuiz]);

  // Resolve a clip's Overlay_Metadata, caching it so revisiting a clip never
  // re-derives or refetches. On a hit the cached value is returned directly (no
  // network); on a miss it is derived from clip fields + topicLabels and stored.
  // `overlayVersion` is a dep so a refresh-on-return that replaces a cache entry
  // re-renders the active caption.
  const resolveOverlay = useCallback(
    (clip: Clip): OverlayMetadata => {
      const cache = overlayCacheRef.current;
      if (hasOverlay(cache, clip.id)) return getOverlay(cache, clip.id)!;
      const meta = deriveOverlay(clip, topicLabels[clip.id] ?? topicSlug ?? "");
      setOverlay(cache, clip.id, meta);
      return meta;
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [topicLabels, topicSlug, overlayVersion],
  );

  // Derive current topic name from active clip
  const activeClip = clips[activeIndex];
  const activeTopicSlug = activeClip ? (topicLabels[activeClip.id] ?? topicSlug ?? "") : "";
  const activeTopicName = activeTopicSlug
    .split("-")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");

  // Share the current topic (deep link lands a new visitor straight on it).
  const handleShare = useCallback(async () => {
    if (!activeTopicSlug) return;
    const result = await shareOrCopy(
      topicShareUrl(activeTopicSlug, activeClip?.id),
      `Learn ${activeTopicName} on Curio`,
    );
    if (result === "copied") {
      setShareToast("Link copied");
      setTimeout(() => setShareToast(null), 2000);
    } else if (result === "failed") {
      setShareToast("Couldn't copy link");
      setTimeout(() => setShareToast(null), 2000);
    }
  }, [activeTopicSlug, activeTopicName, activeClip]);

  if (initialLoading && clips.length === 0) {
    return (
      <div className="fixed inset-0 bg-canvas flex items-center justify-center">
        <div className="w-12 h-12 border-[3px] border-outline border-t-primary rounded-full animate-spin" />
      </div>
    );
  }

  if (!sessionId && !topicSlug) {
    return (
      <div className="min-h-screen bg-canvas text-on-surface flex items-center justify-center">
        <div className="text-center space-y-4">
          <p className="font-display font-extrabold">No topic selected.</p>
          <button onClick={() => router.push("/")} className="rounded-pill bg-primary text-on-primary px-6 py-3 text-sm font-semibold shadow-elev-1 transition hover:brightness-[1.03]">Go back</button>
        </div>
      </div>
    );
  }

  // Network/load error with no clips
  if (loadError && clips.length === 0) {
    return (
      <div className="fixed inset-0 bg-canvas flex flex-col items-center justify-center gap-5 text-on-surface px-6">
        <button onClick={() => router.push("/")} className="rounded-pill bg-surface-alt text-on-surface absolute top-4 left-4 text-sm font-semibold px-4 py-2 shadow-elev-1 transition hover:brightness-95">
          Home
        </button>
        <p className="font-display text-3xl font-extrabold text-center">Couldn't load clips</p>
        <p className="text-on-surface-muted text-sm text-center">Check that the backend is running.</p>
        <button
          onClick={() => { setLoadError(false); loadFeed(); }}
          className="rounded-pill bg-primary text-on-primary px-6 py-3 text-sm font-semibold shadow-elev-1 transition hover:brightness-[1.03]"
        >
          Retry
        </button>
      </div>
    );
  }

  // No clips and timed out
  if (timedOut && clips.length === 0) {
    return (
      <div className="fixed inset-0 bg-canvas flex flex-col items-center justify-center gap-5 text-on-surface px-6">
        <button
          onClick={() => router.push("/")}
          className="rounded-pill bg-surface-alt text-on-surface absolute top-4 left-4 text-sm font-semibold px-4 py-2 shadow-elev-1 transition hover:brightness-95"
        >
          Home
        </button>
        <p className="font-display text-3xl font-extrabold text-center">No clips found</p>
        <p className="text-on-surface-muted text-sm text-center">Try a different topic. We may not have content for this one yet.</p>
        <button
          onClick={() => router.push("/")}
          className="rounded-pill bg-primary text-on-primary px-6 py-3 text-sm font-semibold shadow-elev-1 transition hover:brightness-[1.03]"
        >
          Try another topic
        </button>
      </div>
    );
  }

  // Pure loading (no clips at all yet)
  if (processing && clips.length === 0) {
    return (
      <div className="fixed inset-0 bg-canvas flex flex-col items-center justify-center gap-5 text-on-surface">
        <button
          onClick={() => router.push("/")}
          className="rounded-pill bg-surface-alt text-on-surface absolute top-4 left-4 text-sm font-semibold px-4 py-2 shadow-elev-1 transition hover:brightness-95"
        >
          Home
        </button>
        <div className="w-12 h-12 border-[3px] border-outline border-t-primary rounded-full animate-spin" />
        <div className="text-center space-y-1">
          <p className="font-display font-extrabold">Finding clips for you</p>
          <p className="text-on-surface-muted text-sm">Hang tight</p>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-0 bg-black">
      {/* HUD — glassy chrome over the video */}
      <div className="absolute top-0 inset-x-0 z-20 flex items-center justify-between px-4 pt-4 pb-2 pointer-events-none">
        <button
          onClick={() => router.push("/")}
          className="pointer-events-auto rounded-pill bg-black/40 backdrop-blur-sm text-white font-semibold px-4 py-2 text-sm leading-none transition hover:bg-black/55"
        >
          Home
        </button>

        {activeTopicName && (
          <span className="rounded-pill bg-black/40 backdrop-blur-sm text-white text-xs font-bold tracking-wide px-3 py-1.5 max-w-[40%] truncate">
            {activeTopicName}
          </span>
        )}

        <span className="text-white text-xs tabular-nums flex items-center gap-2 pointer-events-auto">
          {clips.length > 0 ? (
            <span className="rounded-pill bg-black/40 backdrop-blur-sm px-3 py-1.5 font-semibold">{Math.min(activeIndex + 1, clips.length)} / {clips.length}</span>
          ) : ""}
          {activeTopicSlug && (
            <button
              onClick={handleShare}
              className="rounded-pill bg-primary text-on-primary font-semibold px-3 py-1.5 text-xs leading-none shadow-elev-1 transition hover:brightness-[1.05]"
            >
              Share
            </button>
          )}
        </span>
      </div>

      {shareToast && (
        <div className="absolute bottom-8 inset-x-0 z-40 flex justify-center pointer-events-none">
          <div className="rounded-pill bg-on-surface text-canvas text-sm font-semibold px-4 py-2 shadow-elev-2">
            {shareToast}
          </div>
        </div>
      )}

      {/* Nav arrows */}
      {clips.length > 0 && (
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
      )}

      {/* Progress bar */}
      {clips.length > 0 && (
        <div className="absolute top-0 inset-x-0 z-30 h-1 bg-white/20">
          <div
            className="h-full bg-primary transition-all duration-300"
            style={{ width: `${Math.min(100, ((activeIndex + 1) / clips.length) * 100)}%` }}
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
          <Fragment key={clip.id}>
            <div
              data-index={i}
              className="w-full relative snap-start snap-always"
              style={{ height: "100dvh" }}
            >
            {warmSet.has(i) ? (
              <ReelPlayer
                clip={clip}
                mode={i === activeIndex ? "active" : "warm"}
                onEnded={() => goTo(i + 1)}
                overlay={resolveOverlay(clip)}
                onFeedback={i === activeIndex && sessionId ? (type) => {
                  recordClipEvent(clip.id, Date.now() - clipStartRef.current, false, sessionId, 0, type, session?.access_token ?? "");
                  if (type === "already_know") {
                    // Skip the current clip and stay within the topic. After a few
                    // ✓'s in the same topic, skip past the whole topic. Navigate by
                    // index only — mutating the clips array corrupts the snap scroll.
                    const topic = topicLabels[clip.id];
                    alreadyKnowRef.current[topic] = (alreadyKnowRef.current[topic] ?? 0) + 1;
                    if (alreadyKnowRef.current[topic] >= 3) {
                      const nextTopicIdx = clips.findIndex((c, idx) => idx > i && topicLabels[c.id] !== topic);
                      goTo(nextTopicIdx === -1 ? clips.length - 1 : nextTopicIdx);
                    } else {
                      goTo(i + 1);
                    }
                  }
                } : undefined}
              />
            ) : (
              // Outside the warm window: a zero-cost placeholder that fills the
              // same 100dvh section so snap layout and the IntersectionObserver
              // target are preserved, but mounts no media element.
              <div className="absolute inset-0 bg-black" aria-hidden />
            )}
            </div>

            {/* Soft checkpoint cards woven in right after this clip. They are
                plain snap sections with NO data-index, so the next clip keeps
                its index; scrolling past a card advances exactly as before and
                it never blocks. */}
            {(checkpointsByGlobalIndex[i] ?? []).map((cp) => (
              <div
                key={`cp-${cp.topic_slug}-${cp.stage}-${cp.section_index ?? "topic"}-${cp.after_clip_index}`}
                className="w-full relative snap-start snap-always"
                style={{ height: "100dvh" }}
              >
                <div className="absolute inset-0 bg-canvas flex items-center justify-center px-4 overflow-y-auto py-10">
                  <div className="w-full max-w-md">
                    <SoftCheckpointCard
                      topicSlug={cp.topic_slug}
                      topicName={(topicLabels[clip.id] ?? cp.topic_slug)
                        .split("-")
                        .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
                        .join(" ")}
                      stage={cp.stage}
                      sectionIndex={cp.section_index}
                      sessionId={sessionId}
                      token={session?.access_token ?? ""}
                      onSkip={() => goTo(i + 1)}
                      onAnswered={(correct) => {
                        // Tally this checkpoint's answers per topic so the
                        // end-card can offer a soft rewatch for a weak beat.
                        const prev = weakCheckpointRef.current[cp.topic_slug] ?? {
                          sectionIndex: cp.section_index,
                          correct: 0,
                          total: 0,
                        };
                        weakCheckpointRef.current[cp.topic_slug] = {
                          sectionIndex: cp.section_index,
                          correct: prev.correct + (correct ? 1 : 0),
                          total: prev.total + 1,
                        };
                      }}
                    />
                  </div>
                </div>
              </div>
            ))}
          </Fragment>
        ))}

        {/* End card — data-index keeps it goTo-reachable; it's the LAST indexed
            element so clip indexing stays positional (checkpoints have none). */}
        {clips.length > 0 && !processing && (
          <div data-index={clips.length} className="snap-start snap-always" style={{ height: "100dvh" }}>
            <div className="h-full flex flex-col items-center justify-center gap-5 bg-canvas text-on-surface px-6">
              <p className="font-display text-3xl font-extrabold text-center">You finished this topic.</p>
              <p className="text-on-surface-muted text-sm text-center">
                You watched {clips.length} clip{clips.length !== 1 ? "s" : ""}.
              </p>
              {/* Soft remediation: a gentle "rewatch these clips" nudge for the
                  beat the learner was weak on. Always optional — tapping a clip
                  just scrolls back to it; it never blocks moving on. */}
              {rewatchClips.length > 0 && (
                <div className="w-full max-w-sm space-y-2">
                  <p className="text-on-surface text-xs font-bold uppercase tracking-wide">Rewatch these clips</p>
                  <p className="text-on-surface-muted text-xs">
                    A quick refresher on what tripped you up — totally optional.
                  </p>
                  {rewatchClips.map((rc) => {
                    const idx = clips.findIndex((c) => c.id === rc.clip_id);
                    return (
                      <button
                        key={rc.clip_id}
                        onClick={() => { if (idx >= 0) goTo(idx); }}
                        disabled={idx < 0}
                        className="w-full text-left bg-surface rounded-card border border-outline shadow-elev-1 px-4 py-3 flex items-center gap-3 transition hover:shadow-elev-2 disabled:opacity-40"
                      >
                        {rc.thumbnail_url && (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img
                            src={rc.thumbnail_url}
                            alt=""
                            className="w-12 h-12 object-cover rounded-control shrink-0"
                          />
                        )}
                        <p className="font-semibold text-sm flex-1 min-w-0 truncate">
                          {rc.title ?? "Rewatch clip"}
                        </p>
                        <span className="text-on-surface-muted font-bold text-sm shrink-0">{">"}</span>
                      </button>
                    );
                  })}
                </div>
              )}
              {recommendations.length > 0 ? (
                <>
                  <p className="text-on-surface text-xs font-bold uppercase tracking-wide">What to learn next</p>
                  <div className="w-full max-w-sm space-y-3">
                    {recommendations.map((rec) => (
                      <button
                        key={rec.slug}
                        onClick={() => router.push(`/feed?topic=${rec.slug}`)}
                        className="w-full text-left bg-surface rounded-card border border-outline shadow-elev-1 px-4 py-3 transition hover:shadow-elev-2 hover:-translate-y-0.5 motion-reduce:transform-none"
                      >
                        <p className="font-display font-bold text-sm">{rec.name}</p>
                        <p className="text-on-surface-muted text-xs mt-0.5">{rec.clip_count} clips · {rec.difficulty}</p>
                      </button>
                    ))}
                  </div>
                </>
              ) : (
                <button
                  onClick={() => router.push("/")}
                  className="rounded-pill bg-primary text-on-primary px-6 py-3 text-sm font-semibold shadow-elev-1 transition hover:brightness-[1.03]"
                >
                  Learn something new
                </button>
              )}
            </div>
          </div>
        )}

        {/* Still loading more */}
        {clips.length > 0 && processing && (
          <div className="snap-start snap-always" style={{ height: "100dvh" }}>
            <div className="h-full flex flex-col items-center justify-center gap-4 bg-canvas text-on-surface">
              <div className="w-8 h-8 border-[3px] border-outline border-t-primary rounded-full animate-spin" />
              <p className="text-on-surface-muted text-sm font-medium">Loading more clips</p>
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
        <div className="fixed inset-0 bg-canvas flex items-center justify-center">
          <div className="w-12 h-12 border-[3px] border-outline border-t-primary rounded-full animate-spin" />
        </div>
      }
    >
      <FeedContent />
    </Suspense>
  );
}
