# 🎥 AuraStream 2.0 - Advanced AI Studio Edition

AuraStream is a complete, fully-automated YouTube AI Agent with **Hugging Face Superpowers**. It acts as an autonomous production studio that handles the entire lifecycle of a YouTube channel—from finding trending topics and generating scripts, to producing voiceovers, downloading stock footage, editing, generating thumbnails, and even uploading and replying to comments via the YouTube API.

## 🚀 Features - Now Ultra Advanced with Hugging Face 🤗

### Core Features (Fixed & Production Ready)
1. **AI Script & Metadata Generation:** Uses Google Gemini 2.5 Flash + **HF Mistral-7B fallback** to generate fully structured scripts, titles, descriptions, and tags.
2. **Automated Voiceovers:** Employs `edge-tts` + **HF MMS-TTS (1100+ languages)** for high-quality, natural-sounding voiceovers.
3. **Dual Fallback Video Downloader:** Automatically fetches HD stock footage from Pexels, with a seamless fallback to Pixabay.
4. **Mega Production Assembler:** Leverages MoviePy 2.x and Whisper to assemble clips, align them with audio, and generate engaging, Alex Hormozi-style subtitles with word-level timestamps.
5. **Shorts/Reels Creator:** Automatically extracts engaging sections from horizontal 16:9 videos and converts them into 9:16 vertical shorts.
6. **AI Thumbnail Generator:** **HF SDXL (Stable Diffusion XL) + Pollinations AI hybrid** to generate stunning, ultra-quality thumbnails with bold text overlay.
7. **YouTube Auto-Uploader & Commenter:** Uploads videos directly to your channel, sets custom thumbnails, and pins an engaging top comment.
8. **Automated Translation & Trends:** Scrapes Google Trends + **HF NLLB-200 (200+ languages)** for viral topics and translation.
9. **YouTube Auto-Reply with Sentiment:** Monitors comments and uses **HF Sentiment + Emotion detection** to craft empathetic, context-aware replies.
10. **Telegram Alerts:** Notifies your mobile device about rendering status and uploads.
11. **Streamlit Dashboard:** Beautiful, advanced web interface with HF model selection.

### 🆕 NEW - Hugging Face Advanced Level Features
12. **🎨 SDXL Thumbnail Generation:** Ultra-quality 1280x720 thumbnails via `stabilityai/stable-diffusion-xl-base-1.0` - way better than Pollinations
13. **🎵 MusicGen Background Music:** AI-generated background music via `facebook/musicgen-small` - no more copyright issues!
14. **🏷️ Auto-Tagging via Zero-Shot:** `facebook/bart-large-mnli` automatically generates perfect YouTube tags
15. **😊 Sentiment & Emotion Analysis:** `cardiffnlp/twitter-roberta-base-sentiment-latest` + `j-hartmann/emotion-english-distilroberta-base` for smart comment handling
16. **📈 SEO Optimization:** `facebook/bart-large-cnn` summarization for SEO-friendly descriptions
17. **🌍 NLLB-200 Translation:** 200+ language translation, better than Gemini for many languages
18. **🧠 Hybrid AI Fallback:** If Gemini fails, automatically falls back to Mistral-7B/Zephyr-7B via HF - never fails!
19. **✨ Enhanced Prompt Engineering:** HF LLM enhances thumbnail prompts for viral results
20. **🔊 MMS-TTS Multilingual:** 1100+ languages TTS for truly global content
21. **🤖 Telegram AI Agent (with persistent memory):** A full conversational AI agent on Telegram that *knows your channel* — channel profile, custom instructions, facts, full video history stored in SQLite. `/produce`, `/suggest` (trend analysis + tap-to-produce buttons), `/upload` (headless YouTube OAuth), `/comments` (sentiment-aware auto-replies), `/settings`, `/remember`, `/history`, free-text chat with intent detection → `telegram_agent.py` + `agent_brain.py`
22. **🎞️ 1080p Full HD Engine:** Footage is auto-selected at exactly **1920×1080** (Pexels/Pixabay best-rendition picker), every clip is scale+cropped to Full HD, rendered at 30fps H.264 with adaptive 8 Mbps bitrate
24. **⏰ Auto-Pilot Pack (ALL FREE):** `/schedule daily 18:00` auto-productions with auto-upload · `/analytics` YouTube performance reports · `/shorts` 9:16 Shorts · `/localize bn,hi` multi-language versions · `/thumbs` A/B/C thumbnail testing · `/research` facts from Wikipedia + Google News woven into scripts · `/alerts` proactive trend-spike notifications · `/boost` top-comment engagement replies · `/voices` 400+ free neural voices · `/reauth` fresh sign-in
23. **🐳 Dockerfile Ready:** One-command deployment — `docker compose up` for the dashboard, `docker compose --profile agent up` to also run the Telegram AI Agent, or `RUN_TELEGRAM_AGENT=true` for both in one container (**HF Space ready** — see `DEPLOY_HF_SPACE.md`)

## 🤔 Why Hugging Face Makes It More Advanced?

| Feature | Before (Without HF) | After (With HF) | Advantage |
|---------|-------------------|-----------------|-----------|
| **Thumbnail** | Pollinations (basic) | SDXL + Pollinations hybrid | 10x better quality, cinematic |
| **Script** | Gemini only (fails if quota) | Gemini + Mistral fallback | 99.9% uptime, never fails |
| **Music** | Pixabay only (copyright risk) | MusicGen + Pixabay | AI-generated, no copyright, custom for topic |
| **Translation** | Gemini only | NLLB-200 (200 langs) + Gemini | More languages, cost-effective |
| **Comment Reply** | Generic reply | Sentiment-aware empathetic reply | Better engagement, handles hate |
| **Tagging** | Gemini tags only | Zero-Shot + Gemini | More accurate, trending tags |
| **Voice** | Edge-TTS only | Edge-TTS + MMS-TTS | 1100+ languages |

**Result:** Project goes from **Intermediate** to **Advanced/Production-Grade AI Studio** with SOTA open-source models!

## 🛠️ Installation

1. Clone this repository.
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your API keys:
   - **GEMINI_API_KEY** - from https://aistudio.google.com/app/apikey
   - **HUGGINGFACE_API_KEY** - from https://huggingface.co/settings/tokens (FREE, get in 30 sec)
   - **PEXELS_API_KEY** - from https://www.pexels.com/api/
   - **PIXABAY_API_KEY** - from https://pixabay.com/api/docs/
   - Telegram (optional)

4. Download your `client_secrets.json` from the Google Cloud Console (with YouTube Data API v3 enabled) and place it in the root directory.

## 🐳 Docker (Recommended — Ready Out of the Box)

```bash
cp .env.example .env        # add your API keys

# Streamlit dashboard only
docker compose up web

# Dashboard + Telegram AI Agent together
docker compose --profile agent up

# Rebuild after code changes
docker compose up --build
```

The image uses **CPU-only PyTorch** (2.5 GB smaller), ships with ffmpeg + fonts, exposes a healthcheck on `http://localhost:8501/_stcore/health`, and persists renders/models in volumes (`temp_assets/`, `hf-cache`).

## 🤖 Telegram AI Agent

Control the whole studio from your phone — and the agent **remembers your channel**:

1. Talk to **@BotFather** on Telegram → `/newbot` → copy the token
2. Add to `.env`: `TELEGRAM_BOT_TOKEN=123456:ABC-DEF...`
3. Run: `python telegram_agent.py` (or `docker compose --profile agent up agent`, or deploy to HF Space — see `DEPLOY_HF_SPACE.md`)

| Command | What it does |
|---|---|
| `/suggest [region]` | 💡 **5 trending topic ideas with virality scores + reasons**, tailored to your niche & past videos — tap **🎬 Produce** or **📜 Script** buttons right in the chat |
| `/produce <topic>` | 🎬 **Full autopilot**: script → voiceover → **1080p Full HD video** → AI thumbnail → delivered into the chat (remembered for `/upload`) |
| `/upload [last\|id]` | ⬆️ **Uploads to YouTube** — headless OAuth: bot sends a Google sign-in link, you paste the redirect URL back, done. Token saved for reuse |
| `/comments [video_id] [max]` | 💬 **Reads comments, analyzes sentiment + emotion, writes empathetic auto-replies**, reports a summary |
| `/settings` | ⚙️ Teach the agent your channel: `/settings set niche tech reviews`, `/settings set tone funny`, `/settings custom always open with a shocking fact` |
| `/remember <fact>` | 🧠 "my audience loves short tutorials" — used in every future script/reply/suggestion |
| `/history` | 📚 Every produced/uploaded video with links |
| `/script <topic>` | Full script + SEO metadata (Gemini → HF Mistral fallback) |
| `/trends [region]` | Quick trending topic peek |
| `/thumbnail <prompt> \| <title>` | AI thumbnail (SDXL → Pollinations) |
| `/translate <lang> <text>` | Translation via NLLB-200 → Gemini (200+ languages) |
| `/sentiment <text>` | Comment sentiment + emotion analysis |
| `/status` | API keys, engines, YouTube auth & memory status |
| `/forget yes` | 🧹 Wipe agent memory |
| `/schedule daily 18:00 [upload]` | ⏰ **Auto-pilot**: every day at that time the bot picks the top trending suggestion and produces a full 1080p video (add `upload` to publish automatically) |
| `/analytics [days]` | 📊 YouTube Analytics report: views, watch time, subs gained + top videos — with "best performer = next topic" advice |
| `/shorts` | 📱 Cuts a 9:16 vertical Shorts from the last produced video, delivered in chat |
| `/localize bn,hi` | 🌐 Makes Bengali/Hindi/etc. versions of the last video (translate → neural voice → Full HD audio swap) |
| `/thumbs <prompt> \| <title>` | 🖼️ Generates **3 thumbnail variants** (cinematic / vibrant / minimalist) — tap the winner, it becomes the upload thumbnail |
| `/research <topic>` | 🔬 Wikipedia summary + live Google News headlines — auto-injected into every script for accuracy |
| `/alerts <keywords>` | 🚨 Checks trends every 6h — messages you proactively when a keyword spikes 1.8x+ |
| `/boost [video_id]` | 🎁 Finds the most-liked comment, replies personally (YouTube API cannot pin — this is the next best thing), sends stats |
| `/voices [lang]` | 🎙️ 400+ free Edge-TTS voices (Bangla `bn-BD` included) — `/voices use <name>` sets the default |
| `/reauth` | 🔐 Fresh YouTube sign-in (adds the analytics scope) |
| Just type anything | 💬 Free-text chat **with full memory context** — "make a video about X", "suggest topics", "upload my last video", "reply to comments" all work as plain sentences |

**Agent memory (`agent_brain.py`):** SQLite database storing channel profile, custom instructions, learned facts, production history and suggestions. Every AI reply (chat, scripts, suggestions) is conditioned on this context — the more you use it, the more personalized it gets. Override location with `AGENT_MEMORY_DB`.

## 🤗 Deploy on Hugging Face Spaces (Free)

Full step-by-step guide in **`DEPLOY_HF_SPACE.md`**. TL;DR: create a **Docker** Space → upload the repo files → add secrets (`TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, ..., `RUN_TELEGRAM_AGENT=true`) → set `app_port: 8501` in the Space README → the dashboard + Telegram agent run together in one free Space. Add a free UptimeRobot ping so the bot never sleeps.

## 🎞️ 1080p Full HD Engine

- **Smart source picking:** prefers the exact 1920×1080 rendition from Pexels, else the smallest resolution *above* 1080p (downscaling preserves sharpness), Pixabay `large` rendition as fallback
- **Uniform render:** every clip is scale + center-cropped to 1920×1080 @ 30fps, no black bars or mixed resolutions
- **Adaptive bitrate:** 8 Mbps for 1080p, 5 Mbps for 720p, H.264 + AAC
- Switch resolution in the dashboard sidebar ("Video Resolution") or via the Telegram agent (always Full HD)

## 🎯 Usage

### Basic (Without HF - still works)
```bash
streamlit run app.py
```

### Advanced (With HF Superpowers)
1. Get free HF API key: https://huggingface.co/settings/tokens → Create new token → Read role
2. Add to `.env`: `HUGGINGFACE_API_KEY=hf_xxx`
3. Run:
```bash
streamlit run app.py
```
4. In sidebar, enable **"HF Superpowers"** and select:
   - Script AI: `auto` (Gemini primary, HF fallback)
   - Thumbnail AI: `huggingface` (SDXL ultra-quality) or `hybrid`
   - Music AI: `huggingface` (MusicGen)
   - Voice AI: `auto`

From the dashboard, you can:
- Fetch trending topics
- Select AI providers
- Enable auto-tagging, sentiment analysis, SEO optimization
- Generate background music via MusicGen
- Create shorts with HF music
- Test comment sentiment analysis

## 🔧 Architecture - Advanced Edition

```
User Topic → [Trends API]
    ↓
[Script Generation: Gemini 2.5 Flash → HF Mistral-7B fallback]
    ↓
[HF Analysis: Zero-Shot Tags + Sentiment + SEO Summary + Enhanced Thumbnail Prompt]
    ↓
[Translation: HF NLLB-200 (200 langs) → Gemini]
    ↓
[Voiceover: Edge-TTS + HF MMS-TTS (1100+ langs)]
    ↓
[Stock Footage: Pexels → Pixabay]
    ↓
[Video Assembly: MoviePy 2.x + Whisper timestamped]
    ↓
[Thumbnail: HF SDXL → Pollinations + Text Overlay]
    ↓
[Background Music: HF MusicGen → Pixabay]
    ↓
[YouTube Upload + HF Sentiment-aware Comment Reply]
    ↓
[Telegram Alerts]
```

## 📦 New Files Added

- `hf_engine.py` - Complete Hugging Face engine with 10+ SOTA models
- `agent_brain.py` - 🧠 Persistent agent memory (SQLite) + topic suggestion engine + intent router
- `telegram_agent.py` - 🤖 Full Telegram AI Agent with memory (suggest/produce/upload/comments/settings)
- `Dockerfile` + `docker-compose.yml` + `docker-entrypoint.sh` + `.dockerignore` - 🐳 Production-ready Docker / HF Space deployment
- `DEPLOY_HF_SPACE.md` - 🤗 Free Hugging Face Spaces deployment guide
- Updated `app.py` - Hybrid AI pipeline + 1080p Full HD render + headless YouTube OAuth
- Updated `.env.example` - HF key + Telegram agent + Google OAuth env vars
- Updated `requirements.txt` - Includes HF libraries + python-telegram-bot

## 🌟 Demo - What HF Adds

### Before HF:
- Thumbnail: Basic Pollinations image
- Music: Random Pixabay track
- Comment: "Thanks for your comment! 🙏"
- Tags: Only Gemini

### After HF:
- Thumbnail: SDXL cinematic 8k ultra-detailed
- Music: Custom AI-generated for your topic "Epic cinematic for AI documentary"
- Comment: Sentiment-aware "I see you're excited! Thanks for the love! 😊 What part did you enjoy most?"
- Tags: Zero-Shot classification finds trending tags + Gemini

## 🤝 Contributing

PRs welcome! This is now an advanced-level AI project suitable for portfolio, startup, or YouTube automation business.

## 📄 License

MIT
