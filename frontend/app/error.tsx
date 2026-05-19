"use client";

export default function GlobalError({ reset }: { error: Error; reset: () => void }) {
  return (
    <div className="fixed inset-0 bg-black flex flex-col items-center justify-center gap-5 text-white px-6">
      <p className="text-2xl font-semibold">Something went wrong</p>
      <button
        onClick={reset}
        className="bg-white/10 text-white text-sm px-5 py-2.5 rounded-xl hover:bg-white/20 transition"
      >
        Try again
      </button>
    </div>
  );
}
