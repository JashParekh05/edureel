"""
Manually insert clips for demo topics without running the caption pipeline.
Uses pre-computed timestamps from known embeddable educational videos.

Usage:
    cd backend
    python -m scripts.manual_seed
"""
import uuid
from dotenv import load_dotenv
load_dotenv()

from app.db.supabase import get_client

# Each clip: (topic_slug, video_id, start_sec, end_sec, title, description)
DEMO_CLIPS = [
    # --- neural-networks-basics (StatQuest: CqOfi41LfDw) ---
    ("neural-networks-basics", "CqOfi41LfDw", 0, 90,
     "Neural Networks: The Big Picture",
     "What problem do neural networks solve and why they matter."),
    ("neural-networks-basics", "CqOfi41LfDw", 90, 200,
     "Neurons and Activation",
     "How individual neurons fire and what activation functions do."),
    ("neural-networks-basics", "CqOfi41LfDw", 200, 330,
     "Layers and Learning",
     "How stacked layers let networks learn complex patterns."),

    # --- gradient-descent (StatQuest: sDv4f4s2SB8) ---
    ("gradient-descent", "sDv4f4s2SB8", 0, 90,
     "What Is Gradient Descent?",
     "The core idea: follow the slope downhill to minimize loss."),
    ("gradient-descent", "sDv4f4s2SB8", 90, 210,
     "The Loss Landscape",
     "Visualizing the surface we're trying to navigate."),
    ("gradient-descent", "sDv4f4s2SB8", 210, 360,
     "Learning Rate and Convergence",
     "How step size determines whether we find the minimum."),

    # --- calculus-derivatives (Khan Academy style: 9vKqVkMQHKk) ---
    ("calculus-derivatives", "9vKqVkMQHKk", 0, 100,
     "What Is a Derivative?",
     "Derivatives measure how a function changes at any point."),
    ("calculus-derivatives", "9vKqVkMQHKk", 100, 220,
     "The Slope of a Curve",
     "Using limits to find the instantaneous rate of change."),
    ("calculus-derivatives", "9vKqVkMQHKk", 220, 360,
     "Derivative Rules",
     "Power rule, constant rule, and how to apply them."),

    # --- hashmaps (CS Dojo: shs0KM3wKv8) ---
    ("hashmaps", "shs0KM3wKv8", 0, 90,
     "Hash Maps Explained",
     "Why hash maps give O(1) lookup and how the hash function works."),
    ("hashmaps", "shs0KM3wKv8", 90, 200,
     "Handling Collisions",
     "What happens when two keys hash to the same bucket."),
    ("hashmaps", "shs0KM3wKv8", 200, 330,
     "Real-World Hash Map Uses",
     "Caches, frequency counts, and lookup tables in practice."),

    # --- binary-search (KXJSjte_OAI) ---
    ("binary-search", "KXJSjte_OAI", 0, 90,
     "Binary Search Intuition",
     "Why halving the search space every step is so powerful."),
    ("binary-search", "KXJSjte_OAI", 90, 210,
     "Implementing Binary Search",
     "Walking through the algorithm step by step."),

    # --- big-o-notation (Mo4vesaut8g) ---
    ("big-o-notation", "Mo4vesaut8g", 0, 100,
     "Big O Notation Basics",
     "How to express algorithm efficiency independent of hardware."),
    ("big-o-notation", "Mo4vesaut8g", 100, 240,
     "O(n) vs O(log n) vs O(n²)",
     "Comparing the most common complexity classes with examples."),

    # --- backpropagation (Andrej Karpathy: VMj-3S1tku0) ---
    ("backpropagation", "VMj-3S1tku0", 0, 120,
     "Backprop From Scratch",
     "Building intuition for how gradients flow backward through a network."),
    ("backpropagation", "VMj-3S1tku0", 120, 280,
     "The Chain Rule in Action",
     "Applying the chain rule at each layer to compute gradients."),
    ("backpropagation", "VMj-3S1tku0", 280, 450,
     "Numerical Gradient Check",
     "Verifying backprop is correct using finite differences."),
]

TOPICS = {
    "neural-networks-basics": ("Neural Networks Basics", "beginner", []),
    "gradient-descent": ("Gradient Descent", "intermediate", ["neural-networks-basics"]),
    "calculus-derivatives": ("Calculus: Derivatives", "beginner", []),
    "hashmaps": ("Hash Maps", "beginner", []),
    "binary-search": ("Binary Search", "beginner", []),
    "big-o-notation": ("Big O Notation", "beginner", []),
    "backpropagation": ("Backpropagation", "intermediate", ["gradient-descent"]),
}


def main():
    db = get_client()

    # Ensure topic rows exist
    for slug, (name, difficulty, prereqs) in TOPICS.items():
        existing = db.table("topics").select("slug").eq("slug", slug).execute()
        if not existing.data:
            db.table("topics").insert({
                "slug": slug,
                "name": name,
                "difficulty": difficulty,
                "prerequisites": prereqs,
            }).execute()
            print(f"Inserted topic: {slug}")

    # Group by topic so we can check existence once per topic
    from collections import defaultdict
    by_topic: dict[str, list] = defaultdict(list)
    for clip in DEMO_CLIPS:
        by_topic[clip[0]].append(clip)

    inserted = 0
    skipped = 0

    for slug, clips in by_topic.items():
        existing = db.table("clips").select("id").eq("topic_slug", slug).limit(1).execute()
        if existing.data:
            print(f"[{slug}] already has clips, skipping")
            skipped += 1
            continue

        for (_, vid_id, start, end, title, desc) in clips:
            embed_url = (
                f"https://www.youtube.com/embed/{vid_id}"
                f"?start={start}&end={end}&autoplay=1&rel=0&modestbranding=1"
            )
            db.table("clips").insert({
                "topic_slug": slug,
                "title": title,
                "description": desc,
                "video_url": embed_url,
                "duration_seconds": end - start,
                "source_platform": "youtube",
                "hook_score": 0.75,
            }).execute()
            inserted += 1

        print(f"[{slug}] inserted {len(clips)} clips")

    print(f"Done. Inserted {inserted} clips, skipped {skipped} topics.")


if __name__ == "__main__":
    main()
