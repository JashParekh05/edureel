import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

# Suppress noisy httpx/supabase request logs — only show warnings+
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

from app.api import topics, feed, users

app = FastAPI(title="LearnReel API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origin_regex=r"http://(localhost|127\.0\.0\.1|192\.168\.\d+\.\d+|10\.\d+\.\d+\.\d+):\d+",
    allow_origins=["https://your-domain.vercel.app"],
    allow_methods=["*"],
    allow_headers=["*"],
    allow_credentials=True,
)

app.include_router(topics.router)
app.include_router(feed.router)
app.include_router(users.router)


@app.get("/health")
def health():
    return {"status": "ok"}
