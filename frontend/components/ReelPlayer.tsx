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
    u.searchParams.set("enablejsapi", "1");
    u.searchParams.set("autoplay", "1");
    u.searchParams.set("rel", "0");
    u.searchParams.set("modestbranding", "1");
    u.searchParams.set("origin", window.location.origin);
    return u.toString();
  } catch {
    return url;
  }
}

export default function ReelPlayer({ clip, active, onEnded, onFeedback }: Props) {
  const videoRef = useRef<HTMLVideoElement>(null);
  const iframeRef = useRef<HTMLIFrameElement>(null);
  const [clipExpired, setClipExpired] = useState(false);
  const [videoError, setVideoError] = useState(false);
  const [feedback, setFeedback] = useState<"want_more" | "already_know" | null>(null);
  const [controlsVisible, setControlsVisible] = useState(true);
  const hideTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const showControls = () => {
    setControlsVisible(true);
    if (hideTimerRef.current) clearTimeout(hideTimerRef.current);
    hideTimerRef.current = setTimeout(() => setControlsVisible(false), 2500);
  };

  useEffect(() => {
    if (!active) return;
    showControls();
    return () => { if (hideTimerRef.current) clearTimeout(hideTimerRef.current); };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [active, clip.id]);

  const isYT = isYouTubeEmbed(clip.video_url);

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

  // Pause/play inactive YouTube iframes via postMessage
  useEffect(() => {
    if (!isYT) return;
    iframeRef.current?.contentWindow?.postMessage(
      JSON.stringify({ event: "command", func: active ? "playVideo" : "pauseVideo", args: [] }),
      "*"
    );
  }, [active, isYT]);

  // YouTube ended detection via postMessage
  useEffect(() => {
    if (!isYT || !active) return;
    setClipExpired(false);
    function onMessage(e: MessageEvent) {
      try {
        const data = typeof e.data === "string" ? JSON.parse(e.data) : e.data;
        if (data?.event === "onStateChange" && data?.info === 0) setClipExpired(true);
      } catch { /* ignore non-JSON */ }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [active, isYT, clip.id]);

  return (
    <div
      className="absolute inset-0 bg-black"
      onMouseMove={showControls}
      onTouchStart={showControls}
    >
      {isYT ? (
        <iframe
          ref={iframeRef}
          key={clip.id}
          src={sanitizeYTUrl(clip.video_url)}
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

      {/* YouTube clip ended */}
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

      {/* Feedback buttons — auto-hide with overlay */}
      {onFeedback && (
        <div
          className={`absolute right-3 bottom-16 flex flex-col gap-3 items-center z-10 transition-opacity duration-300 ${
            controlsVisible ? "opacity-100" : "opacity-0 pointer-events-none"
          }`}
        >
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
        </div>
      )}
    </div>
  );
}
