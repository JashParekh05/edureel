"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth-context";
import { setUserInterests } from "@/lib/api";

const INTEREST_TAGS = [
  { label: "Science", emoji: "🔬" },
  { label: "History", emoji: "📜" },
  { label: "Math", emoji: "📐" },
  { label: "Technology", emoji: "💻" },
  { label: "Space", emoji: "🚀" },
  { label: "Biology", emoji: "🧬" },
  { label: "Philosophy", emoji: "🧠" },
  { label: "Economics", emoji: "📈" },
  { label: "Engineering", emoji: "⚙️" },
  { label: "Art", emoji: "🎨" },
  { label: "Psychology", emoji: "💭" },
  { label: "Language", emoji: "🗣️" },
];

export default function OnboardingPage() {
  const router = useRouter();
  const { user, session } = useAuth();
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [saving, setSaving] = useState(false);

  function toggle(label: string) {
    setSelected((prev) => {
      const next = new Set(prev);
      next.has(label) ? next.delete(label) : next.add(label);
      return next;
    });
  }

  async function handleContinue() {
    if (!user || !session || selected.size < 3) return;
    setSaving(true);
    try {
      await setUserInterests(user.id, Array.from(selected), session.access_token);
    } catch {
      // best-effort — still proceed to home
    }
    router.replace("/");
  }

  return (
    <div className="min-h-screen bg-black text-white flex flex-col items-center justify-center px-4 py-12">
      <div className="w-full max-w-md space-y-8">
        <div className="text-center space-y-2">
          <h1 className="text-3xl font-bold">What are you into?</h1>
          <p className="text-zinc-400 text-sm">Pick at least 3 topics to personalize your feed</p>
        </div>

        <div className="grid grid-cols-3 gap-3">
          {INTEREST_TAGS.map(({ label, emoji }) => {
            const active = selected.has(label);
            return (
              <button
                key={label}
                onClick={() => toggle(label)}
                className={`flex flex-col items-center gap-1.5 rounded-2xl px-3 py-4 text-sm font-medium transition active:scale-95 ${
                  active
                    ? "bg-white text-black"
                    : "bg-zinc-900 text-zinc-300 border border-zinc-800 hover:border-zinc-600"
                }`}
              >
                <span className="text-2xl">{emoji}</span>
                <span>{label}</span>
              </button>
            );
          })}
        </div>

        {selected.size > 0 && selected.size < 3 && (
          <p className="text-center text-zinc-500 text-sm -mt-4">
            Pick {3 - selected.size} more to continue
          </p>
        )}

        <button
          onClick={handleContinue}
          disabled={selected.size < 3 || saving}
          className="w-full bg-white text-black font-semibold py-4 rounded-xl text-base disabled:opacity-40 hover:bg-zinc-100 transition"
        >
          {saving ? "Saving…" : `Continue${selected.size >= 3 ? ` (${selected.size} selected)` : ""}`}
        </button>
      </div>
    </div>
  );
}
