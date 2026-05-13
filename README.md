# LearnReel

Educational short-form video reels, generated on-demand from your learning goals.

## How it works

1. User types what they want to learn ("I want to learn hashmaps and dynamic programming")
2. Claude parses the query and builds an ordered curriculum (topic → prerequisites → difficulty)
3. Firecrawl + Scrapy discover relevant YouTube/Khan Academy videos per topic
4. yt-dlp downloads the video, Whisper transcribes it, Claude segments it into 30-90s clips, FFmpeg cuts them
5. Clips are uploaded to Cloudflare R2 and stored in Supabase
6. User scrolls a TikTok-style reel feed through their personalized curriculum

## Stack

| Layer | Tech |
|---|---|
| LLM | Groq (llama-3.3-70b-versatile) |
| Scraping | Firecrawl + Scrapy |
| Video download | yt-dlp |
| Transcription | OpenAI Whisper |
| Video cutting | FFmpeg |
| Queue | Celery + Redis |
| Storage | Cloudflare R2 |
| Database | Supabase (Postgres) |
| Backend | FastAPI |
| Frontend | Next.js 15 + Tailwind |

## Setup

### 1. Clone and configure
```bash
cp backend/.env.example backend/.env
# Fill in your keys in backend/.env
```

### 2. Create Supabase tables
Run the SQL in `backend/app/db/supabase.py` → `SCHEMA` in your Supabase SQL editor.

### 3. Start everything
```bash
docker compose up
```

Frontend: http://localhost:3000  
Backend API: http://localhost:8000  
API docs: http://localhost:8000/docs

### 4. Run Scrapy spiders (optional, pre-indexes Khan Academy)
```bash
cd backend/scrapy_spiders
scrapy crawl khan_academy -a subject=cs
scrapy crawl mit_ocw
```

## API

| Endpoint | Method | Description |
|---|---|---|
| `/api/topics/` | POST | Parse query → return learning path |
| `/api/feed/{slug}` | GET | Get clips for a topic |
| `/api/feed/path/{session_id}` | GET | Get full curriculum feed |

## Keys you need

- `GROQ_API_KEY` — [console.groq.com](https://console.groq.com) (free)
- `FIRECRAWL_API_KEY` — [firecrawl.dev](https://firecrawl.dev) (or self-host)
- `SUPABASE_URL` + `SUPABASE_KEY` — [supabase.com](https://supabase.com)
- `R2_*` — Cloudflare R2 bucket ([cloudflare.com](https://cloudflare.com))
