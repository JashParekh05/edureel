"use client";

import { useEffect, useRef, useState } from "react";
import type { Clip } from "@/lib/api";

interface Props {
  clip: Clip;
  active: boolean;
  onEnded: () => void;
  onFeedback?: (type: "want_more" | "already_know") => void;
}

function isYouTubeEmbed(url: string) {
  return url.includes("youtube.com/embed");
}

function sanitizeYTUrl(url: string): string {
  try {
    const u = new URL(url);
    u.searchParams.delete("enablejsapi");
    u.searchParams.set("autoplay", "1");
    u.searchParams.set("rel", "0");
    u.searchParams.set("modestbranding", "1");
    u.searchParams.set("origin", window.location.origin);
    return u.toString();
  } catch {
    return url;
  }
}

function parseYTParams(url: string): { start: number; end: number } {
  try {
    const u = new URL(url);
    return {
      start: parseInt(u.searchParams.get("start") ?? "0"),
      end: parseInt(u.searchParams.get("end") ?? "0"),
    };
  } catch {
    return { start: 0, end: 0 };
  }
}

function SourceBadge({ platform }: { platform: string | null }) {
  if (!platform) return null;
  const isKA = platform === "khan_academy";
  return (
    <span
      className={`text-[10px] font-semibold px-2 py-0.5 rounded-full ${
        isKA
          ? "bg-emerald-500/20 text-emerald-400 border border-emerald-500/30"
          : "bg-zinc-700/60 text-zinc-300 border border-zinc-600/40"
      }`}
    >
      {isKA ? "Khan Academy" : "YouTube"}
    </span>
  );
}

export default function ReelPlayer({ clip, active, onEnded, onFeedback }: Props) {
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [showCaption, setShowCaption] = useState(true);
  const [clipExpired, setClipExpired] = useState(false);
  const [videoError, setVideoError] = useState(false);
  const [feedback, setFeedback] = useState<"want_more" | "already_know" | null>(null);

  const isYT = isYouTubeEmbed(clip.video_url);
  const { end } = parseYTParams(clip.video_url);

  // Reset state when clip changes
  useEffect(() => {
    setClipExpired(false);
    setVideoError(false);
    setFeedback(null);
  }, [clip.id]);

  // Native video: play/reset on active
  useEffect(() => {
    if (isYT || !videoRef.current) return;
    if (active) {
      videoRef.current.currentTime = 0;
      videoRef.current.play().catch(() => {});
    } else {
      videoRef.current.pause();
    }
  }, [active, isYT]);

  // YouTube: show "next clip" overlay when the IFrame API fires onStateChange=0 (ended).
  // We listen via postMessage since we can't reliably time it (buffering, slow connections).
  useEffect(() => {
    if (!isYT || !active) return;
    setClipExpired(false);
    function onMessage(e: MessageEvent) {
      try {
        const data = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
        if (data?.event === "onStateChange" && data?.info === 0) setClipExpired(true);
      } catch { /* ignore non-JSON messages */ }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [active, isYT, clip.id]);

  return (
    <div className="absolute inset-0 bg-zinc-950 flex items-center justify-center">
      {isYT ? (
        <iframe
          ref={iframeRef}
          src={active ? sanitizeYTUrl(clip.video_url) : "about:blank"}
          title={clip.title}
          className="absolute inset-0 w-full h-full"
          allow="autoplay; encrypted-media; fullscreen"
          allowFullScreen
        />
      ) : (
        <video
          ref={videoRef}
          src={clip.video_url}
          className="absolute inset-0 w-full h-full object-cover"
          playsInline
          onEnded={onEnded}
          onError={() => setVideoError(true)}
          preload="auto"
        />
      )}

      {/* Gradient overlays */}
      <div className="absolute inset-0 bg-gradient-to-t from-black/90 via-black/10 to-black/30 pointer-events-none" />

      {/* Native video load error */}
      {videoError && (
        <div className="absolute inset-0 bg-black/80 flex flex-col items-center justify-center gap-3 z-10">
          <p className="text-zinc-400 text-sm">Couldn&apos;t load video</p>
          <button
            onClick={onEnded}
            className="bg-white/10 text-white text-sm px-4 py-2 rounded-xl hover:bg-white/20 transition"
          >
            Skip →
          </button>
        </div>
      )}

      {/* Clip expired overlay for YouTube (end timestamp) */}
      {clipExpired && (
        <div className="absolute inset-0 bg-black/70 backdrop-blur-sm flex items-center justify-center z-10">
          <button
            onClick={onEnded}
            className="bg-white text-black font-semibold px-6 py-3 rounded-2xl text-sm hover:bg-zinc-100 active:scale-95 transition"
          >
            Next clip →
          </button>
        </div>
      )}

      {/* Caption — only for native video; YouTube has built-in CC */}
      {!isYT && showCaption && clip.transcript && !clipExpired && (
        <div className="absolute bottom-28 left-4 right-16 pointer-events-none">
          <p className="text-white text-sm leading-relaxed bg-black/60 backdrop-blur-sm rounded-xl px-3 py-2 inline-block max-w-full">
            {clip.transcript.slice(0, 140)}
            {clip.transcript.length > 140 ? "…" : ""}
          </p>
        </div>
      )}

      {/* Bottom info */}
      <div className="absolute bottom-5 left-4 right-16 space-y-1.5 pointer-events-none">
        <div className="flex items-center gap-2">
          <SourceBadge platform={clip.source_platform} />
          {clip.duration_seconds && (
            <span className="text-zinc-500 text-[10px]">{clip.duration_seconds}s</span>
          )}
        </div>
        <p className="text-white font-semibold text-base leading-tight drop-shadow">{clip.title}</p>
        {clip.description && (
          <p className="text-zinc-300 text-sm line-clamp-2 leading-snug">{clip.description}</p>
        )}
      </div>

      {/* Right controls */}
      <div className="absolute right-3 bottom-16 flex flex-col gap-3 items-center z-10">
        {onFeedback && (
          <>
            <button
              onClick={() => { setFeedback("want_more"); onFeedback("want_more"); }}
              className={`w-11 h-11 rounded-full backdrop-blur-sm border flex items-center justify-center text-lg transition active:scale-95 ${
                feedback === "want_more"
                  ? "bg-orange-500/80 border-orange-400 text-white"
                  : "bg-black/30 border-zinc-700 text-zinc-400 hover:border-orange-500/60 hover:text-orange-400"
              }`}
              title="I want more of this"
            >
              🔥
            </button>
            <button
              onClick={() => { setFeedback("already_know"); onFeedback("already_know"); }}
              className={`w-11 h-11 rounded-full backdrop-blur-sm border flex items-center justify-center text-lg transition active:scale-95 ${
                feedback === "already_know"
                  ? "bg-emerald-500/80 border-emerald-400 text-white"
                  : "bg-black/30 border-zinc-700 text-zinc-400 hover:border-emerald-500/60 hover:text-emerald-400"
              }`}
              title="I already know this topic"
            >
              ✓
            </button>
          </>
        )}
        <button
          onClick={() => setShowCaption((c) => !c)}
          className={`w-11 h-11 rounded-full backdrop-blur-sm border flex items-center justify-center text-xs font-bold transition ${
            showCaption
              ? "bg-white/20 border-white/30 text-white"
              : "bg-black/30 border-zinc-700 text-zinc-500"
          }`}
          title="Toggle captions"
        >
          CC
        </button>
        <button
          onClick={onEnded}
          className="w-11 h-11 rounded-full bg-white/10 backdrop-blur-sm border border-white/20 flex items-center justify-center text-white text-lg hover:bg-white/20 active:scale-95 transition"
          title="Next clip"
        >
          ↓
        </button>
      </div>
    </div>
  );
}
