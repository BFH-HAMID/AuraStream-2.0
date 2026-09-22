"""
==============================================================================
Telegram AI Agent for AuraStream 2.0 — with Persistent Memory (Agent Brain)
==============================================================================
A full conversational AI Agent on Telegram that KNOWS your channel:

    /start          - Welcome + capability overview
    /help           - Full command reference
    /status         - Check API keys, engines, YouTube auth & memory
    /suggest [region] - 5 trending topic suggestions + virality analysis (tap to produce!)
    /script <topic> - Generate full script + SEO metadata
    /produce <topic> - FULL AUTOPILOT: 1080p Full HD video + thumbnail, delivered here
    /upload [last]  - Upload the last produced video to YouTube (headless OAuth)
    /comments [id] [n] - Read comments, analyze sentiment, auto-reply
    /settings       - View channel profile | /settings set <key> <value> | /settings custom <text>
    /remember <fact> - Teach the agent a fact ("my audience loves space content")
    /history        - Everything produced so far
    /forget yes     - Wipe the agent's memory
    /translate <lang> <text> - Translate any text
    /thumbnail <prompt> | <title> - AI thumbnail
    /sentiment <text> - Sentiment + emotion analysis
    Free text       - Talk like a person: "suggest topics", "make a video about X",
                      "upload my last video", "reply to comments" — the agent routes it.

Run:  python telegram_agent.py
Docker:  docker compose --profile agent up agent
HF Space: set RUN_TELEGRAM_AGENT=true (see DEPLOY_HF_SPACE.md)
==============================================================================
"""

import io
import json
import logging
import os
import shutil
import tempfile
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("telegram_agent")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
MAX_TG_FILE_BYTES = 49 * 1024 * 1024  # Telegram Bot API upload limit is 50 MB

# One production at a time per chat (rendering is heavy)
_BUSY_CHATS = set()

# Pending headless YouTube OAuth: chat_id -> {"action": "upload"|"comments", "payload": {...}}
PENDING_AUTH = {}

# Inline suggestion buttons: callback_data id -> topic
SUGGESTION_MAP = {}

# Persistent productions (survive for /upload) — NOT cleaned up like tmp files
PRODUCTION_DIR = Path(os.getenv("AGENT_PRODUCTION_DIR", "temp_assets/agent_productions"))
PRODUCTION_DIR.mkdir(parents=True, exist_ok=True)

# Auto-pilot scheduler (started in post_init)
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    SCHEDULER = AsyncIOScheduler(timezone=os.getenv("SCHEDULER_TZ", "Asia/Dhaka"))
except ImportError:
    SCHEDULER = None
BOT_REF = None

# A/B thumbnail variants: variant letter -> file path
THUMB_MAP = {}

# ---------------------------------------------------------------------------
# Lazy AuraStream engine loader
# ---------------------------------------------------------------------------
_app_module = None


def get_engine():
    """Import the heavy AuraStream engine (app.py) once, lazily."""
    global _app_module
    if _app_module is None:
        import app as app_module  # heavy: torch, moviepy, streamlit...
        _app_module = app_module
    return _app_module


# ---------------------------------------------------------------------------
# AI helpers (Gemini primary, HF fallback) — with full agent memory context
# ---------------------------------------------------------------------------
GEMINI_CHAT_MODEL = "gemini-2.5-flash"

BASE_SYSTEM_PROMPT = (
    "You are AuraStream Agent, the built-in AI channel manager of AuraStream 2.0 - an autonomous "
    "YouTube production studio. You personally know this creator: their niche, audience, tone, past "
    "videos and every instruction they gave you (see context below). You can produce full 1080p videos, "
    "write scripts and SEO metadata, design thumbnails, upload to YouTube, analyze and reply to comments, "
    "and suggest trending topics. Be concise, friendly and practical - like a smart human manager. "
    "Use Telegram-friendly formatting (short paragraphs, emoji, bullets). "
    "Answer in the language the user writes in."
)


def ai_chat_reply(user_text: str) -> str:
    """Free-text AI agent reply with persistent memory: Gemini first, HF fallback."""
    import agent_brain

    system_prompt = BASE_SYSTEM_PROMPT + "\n\n" + agent_brain.build_agent_context()

    engine = get_engine()
    try:
        client = engine.get_gemini_client()
        response = client.models.generate_content(
            model=GEMINI_CHAT_MODEL,
            contents=user_text,
            config={"system_instruction": system_prompt},
        )
        if response and response.text:
            return response.text.strip()
    except Exception as e:
        logger.warning(f"Gemini chat failed: {e}")

    # Hugging Face fallback
    try:
        from hf_engine import get_hf_engine
        reply = get_hf_engine().generate_text(
            f"{system_prompt}\n\nUser: {user_text}\n\nAssistant:", max_new_tokens=400
        )
        if reply:
            return reply.strip()[:4000]
    except Exception as e:
        logger.warning(f"HF chat fallback failed: {e}")

    return "🤖 Sorry, all AI engines are unreachable right now. Check your GEMINI_API_KEY / HUGGINGFACE_API_KEY and try again."


def fmt_script(script_data: dict) -> str:
    """Format generated script metadata into a Telegram-friendly message."""
    title = script_data.get("title", "Untitled")
    desc = (script_data.get("description", "") or "")[:900]
    tags = script_data.get("tags", [])
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except Exception:
            tags = [t.strip() for t in tags.split(",")]
    keywords = script_data.get("visual_keywords", [])
    if isinstance(keywords, str):
        try:
            keywords = json.loads(keywords)
        except Exception:
            keywords = [k.strip() for k in keywords.split(",")]

    lines = [f"🎬 *{title}*"]
    chapters = [(f"Chapter {i}", script_data.get(f"chapter{i}", "")) for i in range(1, 6)]
    chapters = [(name, body) for name, body in chapters if body]
    for name, body in chapters:
        preview = body[:220] + ("..." if len(body) > 220 else "")
        lines.append(f"\n📜 *{name}:*\n{preview}")
    lines.append(f"\n📝 *Description:*\n{desc}{'...' if len(script_data.get('description', '') or '') > 900 else ''}")
    if tags:
        lines.append("\n🏷️ *Tags:* " + ", ".join(f"#{str(t).replace(' ', '')}" for t in tags[:12]))
    if keywords:
        lines.append("🖼️ *Visual keywords:* " + ", ".join(str(k) for k in keywords[:10]))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# YouTube headless auth — shared by /upload and /comments
# ---------------------------------------------------------------------------
def _oauth_setup_ready() -> bool:
    engine = get_engine()
    return os.path.exists("client_secrets.json") or bool(
        os.getenv("GOOGLE_CLIENT_ID", "").strip() and os.getenv("GOOGLE_CLIENT_SECRET", "").strip()
    )


async def ensure_youtube_auth(bot, chat_id: int, action: str, payload: dict = None) -> bool:
    """If YouTube auth is ready, return True. Otherwise send the auth link and
    remember the pending action so the pasted code resumes it."""
    engine = get_engine()
    if engine.get_cached_youtube_credentials() is not None:
        return True
    if not _oauth_setup_ready():
        await bot.send_message(
            chat_id,
            "⚠️ *YouTube upload needs one-time Google setup.*\n\n"
            "Either upload `client_secrets.json` (Google Cloud Console → OAuth client → Desktop app) "
            "or set `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET`.\n"
            "Guide: https://console.cloud.google.com/apis/credentials",
            parse_mode="Markdown",
        )
        return False
    auth_url = engine.build_headless_auth_url()
    PENDING_AUTH[chat_id] = {"action": action, "payload": payload or {}}
    await bot.send_message(
        chat_id,
        "🔐 *One-time YouTube sign-in*\n\n"
        "1️⃣ Open this link (phone/laptop — anywhere):\n"
        f"{auth_url}\n\n"
        "2️⃣ Google will send you to a page that *won't load* — that's OK!\n"
        "3️⃣ Copy the FULL URL from the address bar (it contains `code=4/0A...`)\n"
        "4️⃣ Paste it here.\n\n"
        "✅ I'll remember the login afterwards.",
    )
    return False


# ---------------------------------------------------------------------------
# Full autopilot pipeline (1080p Full HD) + memory
# ---------------------------------------------------------------------------
async def run_full_production(bot, chat_id: int, topic: str):
    """Script -> voiceover -> 1080p footage -> assemble -> thumbnail -> deliver + remember."""
    import agent_brain

    if chat_id in _BUSY_CHATS:
        await bot.send_message(chat_id, "⏳ A production is already running in this chat. Please wait for it to finish.")
        return
    _BUSY_CHATS.add(chat_id)
    started = time.time()
    tmp_dir = tempfile.mkdtemp(prefix="aurastream_tg_")
    status = await bot.send_message(chat_id, f"🚀 *Autopilot engaged!*\n\nTopic: *{topic}*\nTarget: *1080p Full HD*\n\n1/6 · Generating script...")
    try:
        engine = get_engine()
        import agent_features

        # 0) Fresh research (Wikipedia + Google News — free, keyless) for factual scripts
        research_facts = ""
        try:
            research = await asyncio_to_thread(agent_features.research_topic, topic)
            research_facts = research.get("facts_text", "")
            if research_facts:
                await bot.send_message(chat_id, "🔬 Research done: verified facts + latest news collected for the script.")
        except Exception as e:
            logger.warning(f"Research skipped: {e}")

        voice_pref = agent_brain.get_profile().get("tts_voice") or None

        # 1) Script + metadata (Gemini -> HF fallback), flavored by profile + research
        script_data = await asyncio_to_thread(engine.generate_long_script_hf_fallback, topic, 2, "auto", research_facts)
        if not script_data:
            script_data = await asyncio_to_thread(engine.generate_long_script, topic, 2)
        if not script_data:
            await status.edit_text("❌ Script generation failed. Check GEMINI/HUGGINGFACE API keys.")
            return
        full_script = " ".join(script_data.get(f"chapter{i}", "") for i in range(1, 6)).strip() \
            or script_data.get("description", topic)

        # Send the full script as a document so nothing is truncated
        script_file = io.BytesIO(json.dumps(script_data, indent=2, ensure_ascii=False).encode("utf-8"))
        script_file.name = "script.json"
        await bot.send_document(chat_id, script_file, caption=f"📜 Script & metadata for *{topic}*")

        # 2) Voiceover
        await status.edit_text("2/6 · Synthesizing voiceover (Edge-TTS → HF MMS)...")
        voiceover_path = os.path.join(tmp_dir, "voiceover.mp3")
        try:
            await asyncio_to_thread(engine.generate_voiceover_sync, full_script[:4000], voiceover_path, voice_pref)
        except Exception as e:
            logger.warning(f"Edge-TTS failed, trying HF: {e}")
            ok = await asyncio_to_thread(engine.generate_voiceover_hf, full_script[:500], voiceover_path, "en")
            if not ok:
                await status.edit_text("❌ Voiceover synthesis failed.")
                return

        # 3) 1080p Full HD footage
        await status.edit_text("3/6 · Downloading 1080p Full HD footage...")
        keywords = script_data.get("visual_keywords", [])
        if isinstance(keywords, str):
            try:
                keywords = json.loads(keywords)
            except Exception:
                keywords = [k.strip() for k in keywords.split(",")]
        if not keywords:
            keywords = topic.split()[:10]
        clips = await asyncio_to_thread(engine.download_bulk_videos, keywords[:10], "1080p")
        if not clips:
            await status.edit_text("❌ No stock footage could be downloaded. Check PEXELS/PIXABAY keys.")
            return

        # 4) Assemble 1080p Full HD video — persisted for later /upload
        await status.edit_text(f"4/6 · Assembling *1080p Full HD* video ({len(clips)} clips)...")
        stamp = time.strftime("%Y%m%d_%H%M%S")
        prod_dir = PRODUCTION_DIR / stamp
        prod_dir.mkdir(parents=True, exist_ok=True)
        main_video_path = str(prod_dir / "main_video.mp4")
        await asyncio_to_thread(
            engine.create_mega_production, clips, voiceover_path,
            script_data.get("title", topic), main_video_path, (1920, 1080)
        )

        # 5) Thumbnail
        await status.edit_text("5/6 · Designing AI thumbnail (SDXL → Pollinations)...")
        thumb_path = str(prod_dir / "thumbnail.jpg")
        thumb_ok = await asyncio_to_thread(
            engine.generate_thumbnail_with_hf,
            script_data.get("thumbnail_prompt", topic),
            script_data.get("title", topic),
            thumb_path,
            "auto",
        )

        # 6) Deliver + remember in agent memory
        await status.edit_text("6/6 · Uploading to Telegram...")
        caption = (
            f"✅ *{script_data.get('title', topic)}*\n"
            f"🎥 1080p Full HD · 30fps · H.264\n"
            f"⏱️ {time.time() - started:.0f}s render time\n\n"
            f"⬆️ Upload to YouTube: `/upload last`"
        )
        if os.path.exists(thumb_path) and thumb_ok:
            with open(thumb_path, "rb") as f:
                await bot.send_photo(chat_id, f, caption="🖼️ AI Thumbnail (SDXL → Pollinations)")
        video_size = 0
        if os.path.exists(main_video_path):
            video_size = os.path.getsize(main_video_path)
            if video_size <= MAX_TG_FILE_BYTES:
                with open(main_video_path, "rb") as f:
                    await bot.send_video(chat_id, f, caption=caption, supports_streaming=True)
            else:
                await bot.send_message(
                    chat_id,
                    f"📦 Final video is {video_size / (1024 * 1024):.0f} MB — above Telegram's 50 MB bot limit.\n"
                    f"Saved on server at: `{main_video_path}`\n\nUse `/upload last` to publish it to YouTube.",
                )
            engine.send_telegram_message(f"✅ AuraStream Telegram Agent: delivered '{script_data.get('title', topic)}' ({video_size / (1024 * 1024):.0f} MB, 1080p Full HD)")
        else:
            await status.edit_text("❌ Video assembly produced no file.")
            return

        agent_brain.add_history(
            topic=topic,
            title=script_data.get("title", topic),
            video_path=main_video_path,
            thumb_path=thumb_path if thumb_ok else "",
            tags=script_data.get("tags", ""),
            description=script_data.get("description", ""),
            narration=full_script[:8000],
            status="produced",
        )
        await status.edit_text(
            f"🏁 Production complete in {time.time() - started:.0f}s! 🎬\n"
            f"🧠 Saved to memory.\n\n"
            f"`/upload last` → publish • `/shorts` → 9:16 Shorts • `/localize bn,hi` → more languages • `/thumbs` → A/B thumbnail"
        )
    except Exception as e:
        logger.exception("Full production failed")
        try:
            await status.edit_text(f"❌ Production failed: {e}")
        except Exception:
            pass
    finally:
        _BUSY_CHATS.discard(chat_id)
        # Cleanup temp clips + voiceover only (production files are kept for /upload)
        try:
            for f in Path(tmp_dir).glob("*"):
                f.unlink(missing_ok=True)
            os.rmdir(tmp_dir)
        except Exception:
            pass


# asyncio.to_thread shim (py3.8 compatibility not needed, but keep import local)
from asyncio import to_thread as asyncio_to_thread  # noqa: E402


# ---------------------------------------------------------------------------
# Upload (module level — shared by /upload, free-text auth flow, scheduler)
# ---------------------------------------------------------------------------
async def perform_upload(bot, chat_id: int, item: dict):
    engine = get_engine()
    msg = await bot.send_message(chat_id, f"⬆️ Uploading *{item.get('title') or item.get('topic')}* to YouTube...")
    meta = {
        "title": item.get("title") or item.get("topic") or "AuraStream Video",
        "description": item.get("description", ""),
        "tags": item.get("tags", ""),
    }
    try:
        video_id = await asyncio_to_thread(engine.upload_video_to_youtube, item["video_path"], item.get("thumb_path") or "", meta)
        agent_brain.add_history(
            topic=item.get("topic", ""), title=meta["title"], video_id=video_id,
            video_path=item.get("video_path", ""), thumb_path=item.get("thumb_path", ""),
            tags=meta["tags"], status="uploaded", description=meta["description"],
            narration=item.get("narration", ""),
        )
        await msg.edit_text(
            f"🎉 *Published on YouTube!*\n\n🔗 https://youtube.com/watch?v={video_id}\n"
            f"📌 Engagement comment added\n"
            f"⏰ Engagement boost scheduled (+24h) — top comment reply + stats\n"
            f"🧠 Saved to history — use `/comments` to manage replies",
            parse_mode="Markdown",
        )
        engine.send_telegram_message(f"🚀 AuraStream: '{meta['title']}' is LIVE → https://youtube.com/watch?v={video_id}")
        # Schedule the 24h engagement booster (top-comment reply + stats report)
        try:
            if SCHEDULER is not None:
                from apscheduler.triggers.date import DateTrigger
                SCHEDULER.add_job(
                    scheduled_engagement_job, DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(hours=24)),
                    args=[chat_id, video_id], id=f"boost_{video_id}", misfire_grace_time=3600, replace_existing=True,
                )
        except Exception as e:
            logger.warning(f"Could not schedule engagement boost: {e}")
    except Exception as e:
        logger.exception("Upload failed")
        await msg.edit_text(f"❌ Upload failed: `{e}`", parse_mode="Markdown")


# ---------------------------------------------------------------------------
# Scheduler jobs (auto-pilot production, engagement boost, trend alerts)
# ---------------------------------------------------------------------------
def register_one_schedule(s: dict):
    if SCHEDULER is None:
        return
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    job_id = f"prod_{s['id']}"
    if s["kind"] == "daily":
        h, m = s["time_text"].split(":")
        trigger = CronTrigger(hour=int(h), minute=int(m))
    else:
        trigger = IntervalTrigger(hours=int(str(s["time_text"]).rstrip("h")))
    SCHEDULER.add_job(scheduled_production_job, trigger,
                      args=[int(s["chat_id"]), bool(s["auto_upload"])],
                      id=job_id, misfire_grace_time=3600, replace_existing=True)


def register_schedule_jobs():
    import agent_brain
    for s in agent_brain.get_schedules(active_only=True):
        try:
            register_one_schedule(s)
        except Exception as e:
            logger.warning(f"Could not register schedule #{s.get('id')}: {e}")


async def scheduled_production_job(chat_id: int, auto_upload: bool):
    """Auto-pilot: pick the top suggestion, produce a full 1080p video, optionally upload."""
    import agent_brain
    if BOT_REF is None:
        return
    try:
        region = agent_brain.get_profile().get("region", "united_states")
        sugs = await asyncio_to_thread(agent_brain.suggest_topics, region, 3)
        topic = sugs[0]["topic"] if sugs else ""
        if not topic:
            await BOT_REF.send_message(chat_id, "⏰ Auto-pilot: could not pick a topic (check API keys).")
            return
        agent_brain.mark_suggestion_used(topic)
        await BOT_REF.send_message(chat_id, f"⏰ *Auto-pilot triggered!*\n🎬 Producing top suggestion: *{topic}*", parse_mode="Markdown")
        await run_full_production(BOT_REF, chat_id, topic)
        if auto_upload:
            item = agent_brain.get_last_production()
            if item and item.get("video_path") and item.get("status") == "produced":
                await perform_upload(BOT_REF, chat_id, item)
    except Exception as e:
        logger.exception("Scheduled production failed")
        try:
            await BOT_REF.send_message(chat_id, f"❌ Auto-pilot run failed: {e}")
        except Exception:
            pass


async def scheduled_engagement_job(chat_id: int, video_id: str):
    """+24h after upload: reply to the most-liked comment + send a stats report."""
    import agent_features
    if BOT_REF is None:
        return
    try:
        result = await asyncio_to_thread(agent_features.engagement_boost, get_engine(), video_id)
        stats = result.get("stats", {})
        top = result.get("top_comment")
        lines = [f"🎁 *Engagement boost* for *{stats.get('title', video_id)}*",
                 f"👁️ {stats.get('views', 0):,} views · 👍 {stats.get('likes', 0):,} · 💬 {stats.get('comments', 0):,}"]
        if top:
            lines.append(f"\n🏆 Hottest comment ({top['likes']} likes) from {top['author']}:\n\"{top['text'][:150]}\"")
            if result.get("replied"):
                lines.append(f"↩️ Replied: _{result.get('reply_text', '')[:150]}_")
            else:
                lines.append("⚠️ Could not post the reply (permissions?)")
        else:
            lines.append("\n📭 No comments found yet.")
        lines.append("\n💡 Note: YouTube API cannot *pin* comments — I replied to the top one instead.")
        await BOT_REF.send_message(int(chat_id), "\n".join(lines), parse_mode="Markdown")
    except Exception as e:
        logger.exception("Engagement boost failed")
        try:
            await BOT_REF.send_message(int(chat_id), f"❌ Engagement boost failed: {e}")
        except Exception:
            pass


async def scheduled_trend_alert_job():
    """Every 6h: check niche keywords for trend spikes and proactively alert."""
    import agent_brain
    import agent_features
    if BOT_REF is None:
        return
    chat_id_raw = agent_brain.kv_get("last_chat_id", "")
    if not chat_id_raw:
        return
    profile = agent_brain.get_profile()
    kws = [k.strip() for k in (profile.get("alert_keywords") or "").split(",") if k.strip()]
    niche = profile.get("niche", "")
    if niche:
        kws += [w for w in niche.split() if len(w) > 3][:2]
    if not kws:
        return
    region = profile.get("region", "united_states")
    spikes = await asyncio_to_thread(agent_features.trend_spike_check, kws, region)
    if spikes:
        top = spikes[0]
        try:
            await BOT_REF.send_message(
                int(chat_id_raw),
                f"🚨 *Trend spike alert!*\n\n`{top['keyword']}` is *{top['ratio']}x* hotter than usual right now!\n"
                f"Produce it before others do:\n`/produce {top['keyword']}`\n\n"
                f"(Turn alerts on/off: /alerts your keywords, comma separated)",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"Trend alert send failed: {e}")


async def post_init(app):
    """PTB post-init: start the scheduler, register jobs, keep the bot ref."""
    global BOT_REF
    BOT_REF = app.bot
    register_schedule_jobs()
    if SCHEDULER is not None:
        from apscheduler.triggers.interval import IntervalTrigger
        SCHEDULER.add_job(scheduled_trend_alert_job, IntervalTrigger(hours=6),
                          id="trend_alerts", misfire_grace_time=3600)
        SCHEDULER.start()
        logger.info("⏰ Auto-pilot scheduler started (productions + trend alerts)")
    else:
        logger.warning("APScheduler not installed — /schedule disabled (pip install apscheduler)")


# ---------------------------------------------------------------------------
# Telegram bot wiring (python-telegram-bot v21+)
# ---------------------------------------------------------------------------
def build_application():
    import agent_brain
    from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
    from telegram.constants import ChatAction
    from telegram.ext import (
        Application, CommandHandler, MessageHandler, ContextTypes,
        CallbackQueryHandler, filters,
    )

    # ------------------------------------------------------------------
    # Basic commands
    # ------------------------------------------------------------------
    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "👋 *Welcome to AuraStream 2.0 — your AI Channel Manager!*\n\n"
            "I know your channel, I learn your style, and I do the work:\n\n"
            "💡 `/suggest` — trending topics + virality analysis (tap to produce)\n"
            "🎬 `/produce <topic>` — full autopilot: script → *1080p Full HD* video → thumbnail\n"
            "⬆️ `/upload last` — publish the last video to YouTube\n"
            "💬 `/comments` — read comments, analyze sentiment, auto-reply\n"
            "📜 `/script <topic>` — script + SEO metadata\n"
            "⚙️ `/settings` — teach me your niche, tone, audience\n"
            "🧠 `/remember <fact>` — make me remember anything\n"
            "📚 `/history` — everything we made together\n\n"
            "Or just *talk to me like a human* 🤖",
            parse_mode="Markdown",
        )

    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "*📖 AuraStream Agent Commands*\n\n"
            "/suggest \\[region\\] — 💡 trending topics + analysis\n"
            "/produce \\<topic\\> — 🎬 full 1080p autopilot production\n"
            "/upload \\[last\\] — ⬆️ YouTube upload (headless OAuth)\n"
            "/comments \\[video\\_id\\] \\[max\\] — 💬 sentiment-aware auto-replies\n"
            "/script \\<topic\\> — 📜 script + metadata\n"
            "/thumbnail \\<prompt\\> \\| \\<title\\> — 🖼️ AI thumbnail\n"
            "/translate \\<lang\\> \\<text\\> — 🌍 translation\n"
            "/sentiment \\<text\\> — 💭 sentiment analysis\n"
            "/settings — ⚙️ view profile\n"
            "/settings set \\<key\\> \\<value\\> — niche/tone/audience/language/region\n"
            "/settings custom \\<text\\> — custom instructions\n"
            "/remember \\<fact\\> — 🧠 store a fact\n"
            "/history — 📚 production history\n"
            "/forget yes — 🧹 wipe memory\n"
            "/status — 📊 engines + auth + memory\n"
            "⏰ */schedule* daily 18:00 \[upload\] — auto\-pilot productions\n"
            "/analytics \[days\] — 📊 YouTube performance report\n"
            "/shorts — 📱 9:16 Shorts from last video\n"
            "/localize bn,hi — 🌐 multi\-language versions\n"
            "/thumbs \<prompt\> — 🖼️ A/B/C thumbnail test\n"
            "/research \<topic\> — 🔬 facts \+ fresh news\n"
            "/alerts \<keywords\> — 🚨 trend spike alerts\n"
            "/boost \[video\_id\] — 🎁 top\-comment engagement reply\n"
            "/voices \[lang\] — 🎙️ voice catalogue (400+)\n"
            "/reauth — 🔐 fresh YouTube sign\-in\n\n"
            "_Free text works too — I understand intents._",
            parse_mode="MarkdownV2",
        )

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        def check(v, placeholder="your_"):
            return bool(v) and not v.startswith(placeholder)
        engine = get_engine()
        profile = agent_brain.get_profile()
        history_count = len(agent_brain.get_history(limit=100))
        facts_count = len(agent_brain.get_facts(limit=100))
        yt_auth = "✅ signed in" if engine.get_cached_youtube_credentials() else ("⚙️ setup ready" if _oauth_setup_ready() else "❌ not configured")
        lines = [
            "📊 *AuraStream Engine Status*",
            f"Gemini: {'✅' if check(engine.GEMINI_API_KEY) else '❌'}",
            f"Hugging Face: {'✅' if check(engine.HUGGINGFACE_API_KEY) else '❌'}",
            f"Pexels: {'✅' if check(engine.PEXELS_API_KEY) else '❌'}",
            f"Pixabay: {'✅' if check(engine.PIXABAY_API_KEY) else '❌'}",
            f"YouTube: {yt_auth}",
            f"🎥 Render target: *1080p Full HD (1920x1080 @ 30fps)*",
            "",
            f"🧠 *Agent Memory*: niche={profile.get('niche', '—')} | tone={profile.get('tone', '—')}",
            f"📚 Videos produced: {history_count} | Facts learned: {facts_count}",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    # ------------------------------------------------------------------
    # 💡 /suggest — trending topics with analysis + tap-to-produce buttons
    # ------------------------------------------------------------------
    async def cmd_suggest(update: Update, context: ContextTypes.DEFAULT_TYPE):
        region = " ".join(context.args).strip().replace(" ", "_") or agent_brain.get_profile().get("region", "united_states")
        msg = await update.message.reply_text(f"💡 Analyzing trends in *{region}* + your channel profile...")
        await update.message.chat.send_action(ChatAction.TYPING)
        try:
            suggestions = await asyncio_to_thread(agent_brain.suggest_topics, region, 5)
        except Exception as e:
            await msg.edit_text(f"❌ Suggestion engine failed: {e}")
            return
        if not suggestions:
            await msg.edit_text("❌ No suggestions generated. Check GEMINI/HF keys.")
            return

        lines = [f"💡 *Top {len(suggestions)} topic ideas for you:*\n"]
        SUGGESTION_MAP.clear()
        buttons = []
        for i, s in enumerate(suggestions, 1):
            lines.append(
                f"*{i}. {s['topic']}*\n"
                f"🔥 Virality score: *{s['score']}/10*\n"
                f"💬 {s['reason']}\n"
                f"👤 Audience: {s.get('audience', '—')}\n"
            )
            pid, sid = f"p{i}", f"s{i}"
            SUGGESTION_MAP[pid] = s["topic"]
            SUGGESTION_MAP[sid] = s["topic"]
            buttons.append([
                InlineKeyboardButton(f"🎬 Produce #{i}", callback_data=pid),
                InlineKeyboardButton(f"📜 Script #{i}", callback_data=sid),
            ])
        await msg.edit_text("\n".join(lines), reply_markup=InlineKeyboardMarkup(buttons))

    async def on_suggestion_tap(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        topic = SUGGESTION_MAP.get(query.data)
        if not topic:
            await query.edit_message_text("⌋ This suggestion expired — run /suggest again.")
            return
        if query.data.startswith("s"):  # script only
            await query.edit_message_text(f"📜 Writing script for: *{topic}*...")
            engine = get_engine()
            data = await asyncio_to_thread(engine.generate_long_script_hf_fallback, topic, 2, "auto") \
                or await asyncio_to_thread(engine.generate_long_script, topic, 2)
            if not data:
                await query.edit_message_text("❌ Script generation failed.")
                return
            agent_brain.add_history(topic=topic, title=data.get("title", topic), tags=data.get("tags", ""),
                                    description=data.get("description", ""), status="script_only")
            text = fmt_script(data)
            await query.edit_message_text(text[:4090], parse_mode="Markdown")
        else:  # full production
            agent_brain.mark_suggestion_used(topic)
            await query.edit_message_text(f"🎬 Starting full production: *{topic}*")
            await run_full_production(context.bot, query.message.chat_id, topic)

    # ------------------------------------------------------------------
    # ⬆️ /upload — headless OAuth + upload last produced video
    # ------------------------------------------------------------------
    async def cmd_upload(update: Update, context: ContextTypes.DEFAULT_TYPE):
        engine = get_engine()
        target = " ".join(context.args).strip() or "last"
        history = agent_brain.get_history(limit=20)
        item = None
        if target == "last":
            item = next((h for h in history if h.get("video_path") and h.get("status") == "produced"), None)
        else:
            # match by video path fragment or history id
            for h in history:
                if target in (h.get("video_path") or "") or str(h.get("id")) == target:
                    item = h
                    break
        if not item:
            await update.message.reply_text(
                "🤔 No produced video found to upload.\nProduce one first with `/produce <topic>`.",
                parse_mode="Markdown",
            )
            return
        if item.get("status") == "uploaded":
            await update.message.reply_text(
                f"ℹ️ This video was already uploaded: https://youtube.com/watch?v={item.get('video_id')}\n"
                "Produce a new one or pass a different id: `/upload <history_id>`",
                parse_mode="Markdown",
            )
            return

        payload = {"history_id": item["id"]}
        ready = await ensure_youtube_auth(context.bot, update.effective_chat.id, "upload", payload)
        if not ready:
            return
        await perform_upload(context.bot, update.effective_chat.id, item)

    # Upload/boost logic lives at module level (perform_upload) so the scheduler can reuse it.

    # ------------------------------------------------------------------
    # 💬 /comments — sentiment-aware auto-reply
    # ------------------------------------------------------------------
    async def cmd_comments(update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args or []
        max_replies = 10
        if args and args[-1].isdigit():
            max_replies = min(int(args[-1]), 30)
            args = args[:-1]
        target = " ".join(args).strip() or "last"

        video_id = None
        if target != "last" and len(target) >= 8:  # looks like a video id
            video_id = target
        else:
            last = next((h for h in agent_brain.get_history(limit=20) if h.get("video_id")), None)
            video_id = last["video_id"] if last else None
        if not video_id:
            await update.message.reply_text(
                "🤔 I don't know any uploaded video yet. Use `/upload last` first, or pass a video id:\n`/comments dQw4w9WgXcQ 10`",
                parse_mode="Markdown",
            )
            return

        ready = await ensure_youtube_auth(context.bot, update.effective_chat.id, "comments", {"video_id": video_id, "max": max_replies})
        if not ready:
            return
        await do_comments(context.bot, update.effective_chat.id, video_id, max_replies)

    async def do_comments(bot, chat_id: int, video_id: str, max_replies: int):
        engine = get_engine()
        msg = await bot.send_message(chat_id, f"💬 Reading comments of `{video_id}`...\n🧠 Analyzing sentiment + writing empathetic replies...")
        try:
            stats = await asyncio_to_thread(engine.reply_to_youtube_comments, video_id, True, max_replies)
            await msg.edit_text(
                f"✅ *Comment management done!*\n\n"
                f"📖 Scanned: {stats.get('scanned', 0)}\n"
                f"↩️ Replied: *{stats.get('replied', 0)}*\n"
                f"⚠️ Errors: {stats.get('errors', 0)}\n\n"
                f"💡 Replies were sentiment-aware: empathetic for criticism, enthusiastic for love.",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.exception("Comment reply failed")
            await msg.edit_text(f"❌ Comment reply failed: `{e}`", parse_mode="Markdown")

    # ------------------------------------------------------------------
    # ⚙️ /settings — channel profile + custom instructions
    # ------------------------------------------------------------------
    async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args or []
        if not args:
            profile = agent_brain.get_profile()
            lines = ["⚙️ *Channel Profile*\n"]
            if profile:
                for k, v in profile.items():
                    label = "🧠 Custom instructions" if k == "custom_instructions" else k
                    lines.append(f"*{label}:* {v}")
            else:
                lines.append("_Empty! Teach me:_\n`/settings set niche tech reviews`\n`/settings set tone funny`\n`/settings set audience students`\n`/settings set region bangladesh`\n`/settings custom always start videos with a shocking fact`")
            lines.append(f"\n🧠 Facts remembered: {len(agent_brain.get_facts(limit=100))}")
            lines.append("➕ Add a fact: `/remember I post every Friday`")
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            return

        if args[0].lower() == "set" and len(args) >= 3:
            key, value = args[1].lower(), " ".join(args[2:])
            if agent_brain.set_profile(key, value):
                await update.message.reply_text(f"✅ Saved: *{key}* = {value}", parse_mode="Markdown")
            else:
                await update.message.reply_text(
                    f"❌ Unknown key `{key}`. Valid: {', '.join(agent_brain.PROFILE_KEYS)}", parse_mode="Markdown"
                )
        elif args[0].lower() == "custom" and len(args) >= 2:
            text = " ".join(args[1:])
            agent_brain.set_custom_instructions(text)
            await update.message.reply_text("🧠 Custom instructions saved! I'll follow them in every script, reply and suggestion.", parse_mode="Markdown")
        else:
            await update.message.reply_text("Usage:\n`/settings set niche tech reviews`\n`/settings custom always use hooks`", parse_mode="Markdown")

    # ------------------------------------------------------------------
    # 🧠 /remember, 📚 /history, 🧹 /forget
    # ------------------------------------------------------------------
    async def cmd_remember(update: Update, context: ContextTypes.DEFAULT_TYPE):
        fact = " ".join(context.args).strip()
        if not fact:
            await update.message.reply_text("Usage: `/remember my audience loves quick tutorials`", parse_mode="Markdown")
            return
        agent_brain.remember_fact(fact)
        await update.message.reply_text(f"🧠 Remembered: _{fact}_\nI'll use this in scripts, replies and suggestions.", parse_mode="Markdown")

    async def cmd_history(update: Update, context: ContextTypes.DEFAULT_TYPE):
        history = agent_brain.get_history(limit=10)
        if not history:
            await update.message.reply_text("📚 Nothing in history yet! Try `/produce <topic>`.")
            return
        lines = ["📚 *Recent productions:*\n"]
        for h in history:
            icon = {"uploaded": "🌍", "produced": "🎬", "script_only": "📜"}.get(h.get("status"), "•")
            title = h.get("title") or h.get("topic")
            vid = f" → youtube.com/watch?v={h['video_id']}" if h.get("video_id") else ""
            lines.append(f"{icon} {title}{vid}")
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_forget(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if " ".join(context.args).strip().lower() == "yes":
            agent_brain.forget_everything()
            await update.message.reply_text("🧹 Memory wiped. We're strangers again — run /settings to teach me your channel.")
        else:
            await update.message.reply_text("⚠️ This wipes profile, facts and history. Confirm with `/forget yes`")

    # ------------------------------------------------------------------
    # /trends, /script, /translate, /thumbnail, /sentiment, /produce
    # ------------------------------------------------------------------
    async def cmd_trends(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.chat.send_action(ChatAction.TYPING)
        region = " ".join(context.args).strip() or "united_states"
        try:
            topic = await asyncio_to_thread(get_engine().fetch_trending_topic, region.replace(" ", "_"))
            await update.message.reply_text(f"🔥 Trending in *{region}*:\n\n*{topic}*\n\nTry: `/suggest` for analyzed ideas", parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Trends fetch failed: {e}")

    async def cmd_script(update: Update, context: ContextTypes.DEFAULT_TYPE):
        topic = " ".join(context.args).strip()
        if not topic:
            await update.message.reply_text("Usage: `/script how black holes work`", parse_mode="Markdown")
            return
        msg = await update.message.reply_text("🧠 Writing your script (Gemini → HF fallback)...")
        await update.message.chat.send_action(ChatAction.TYPING)
        engine = get_engine()
        data = await asyncio_to_thread(engine.generate_long_script_hf_fallback, topic, 2, "auto") \
            or await asyncio_to_thread(engine.generate_long_script, topic, 2)
        if not data:
            await msg.edit_text("❌ Script generation failed. Check your API keys.")
            return
        agent_brain.add_history(topic=topic, title=data.get("title", topic), tags=data.get("tags", ""),
                                description=data.get("description", ""), status="script_only")
        text = fmt_script(data)
        if len(text) > 4096:
            text = text[:4090] + "…"
        await msg.edit_text(text, parse_mode="Markdown")

    async def cmd_translate(update: Update, context: ContextTypes.DEFAULT_TYPE):
        args = context.args or []
        if len(args) < 2:
            await update.message.reply_text("Usage: `/translate Bengali the future of AI`", parse_mode="Markdown")
            return
        lang, text = args[0], " ".join(args[1:])
        await update.message.chat.send_action(ChatAction.TYPING)
        try:
            result = await asyncio_to_thread(get_engine().translate_script, text, lang)
            await update.message.reply_text(f"🌍 *{lang} translation:*\n\n{result}", parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Translation failed: {e}")

    async def cmd_thumbnail(update: Update, context: ContextTypes.DEFAULT_TYPE):
        raw = " ".join(context.args).strip()
        if not raw:
            await update.message.reply_text("Usage: `/thumbnail futuristic city at sunset | FUTURE CITIES`", parse_mode="Markdown")
            return
        prompt, _, title = raw.partition("|")
        prompt, title = prompt.strip(), (title.strip() or prompt.strip()[:40])
        msg = await update.message.reply_text("🎨 Generating AI thumbnail (SDXL → Pollinations)...")
        await update.message.chat.send_action(ChatAction.UPLOAD_PHOTO)
        tmp = tempfile.mkdtemp(prefix="aurastream_thumb_")
        path = os.path.join(tmp, "thumb.jpg")
        try:
            ok = await asyncio_to_thread(get_engine().generate_thumbnail_with_hf, prompt, title, path, "auto")
            if ok and os.path.exists(path):
                with open(path, "rb") as f:
                    await update.message.reply_photo(f, caption=f"🖼️ {title}")
                await msg.delete()
            else:
                await msg.edit_text("❌ Thumbnail generation failed.")
        finally:
            try:
                os.remove(path)
                os.rmdir(tmp)
            except Exception:
                pass

    async def cmd_sentiment(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = " ".join(context.args).strip()
        if not text:
            await update.message.reply_text("Usage: `/sentiment I absolutely loved this video!`", parse_mode="Markdown")
            return
        await update.message.chat.send_action(ChatAction.TYPING)
        try:
            result = await asyncio_to_thread(get_engine().analyze_comment_sentiment_advanced, text)
            await update.message.reply_text(f"💬 *Sentiment analysis:*\n\n```json\n{json.dumps(result, indent=2, ensure_ascii=False)}\n```", parse_mode="Markdown")
        except Exception as e:
            await update.message.reply_text(f"❌ Sentiment analysis failed: {e}")

    async def cmd_produce(update: Update, context: ContextTypes.DEFAULT_TYPE):
        topic = " ".join(context.args).strip()
        if not topic:
            await update.message.reply_text("Usage: `/produce top 10 facts about space`", parse_mode="Markdown")
            return
        await update.message.chat.send_action(ChatAction.UPLOAD_VIDEO)
        await run_full_production(context.bot, update.effective_chat.id, topic)

    # ------------------------------------------------------------------
    # 🤖 Free-text brain: pending auth codes + intent routing
    # ------------------------------------------------------------------
    _PRODUCE_TRIGGERS = ("make a video", "create a video", "produce a video", "video about", "video on")

    async def free_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = (update.message.text or "").strip()
        chat_id = update.effective_chat.id
        agent_brain.kv_set("last_chat_id", str(chat_id))
        lowered = text.lower()

        # 1) Waiting for an OAuth code?
        if chat_id in PENDING_AUTH:
            if "code=" in text or (text.startswith("4/") or text.startswith(("1//", "AQ."))):
                pending = PENDING_AUTH.pop(chat_id)
                wait = await update.message.reply_text("🔐 Verifying your sign-in code...")
                try:
                    await asyncio_to_thread(get_engine().exchange_auth_code, text)
                    await wait.edit_text("✅ YouTube signed in! Saved the login — continuing...")
                    if pending["action"] == "upload":
                        item = next((h for h in agent_brain.get_history(limit=20) if h["id"] == pending["payload"].get("history_id")), None)
                        if item:
                            await perform_upload(context.bot, chat_id, item)
                            return
                    elif pending["action"] == "comments":
                        await do_comments(context.bot, chat_id, pending["payload"]["video_id"], pending["payload"].get("max", 10))
                        return
                    elif pending["action"] == "reauth":
                        await wait.edit_text("✅ Re-auth complete — analytics scope added! Try /analytics 📊")
                        return
                except Exception as e:
                    logger.warning(f"OAuth exchange failed: {e}")
                    await wait.edit_text(
                        f"❌ That code didn't work (`{str(e)[:120]}`).\nSend /upload or /comments again to get a fresh link.",
                        parse_mode="Markdown",
                    )
                    return
            else:
                await update.message.reply_text("⏳ I'm still waiting for your Google sign-in code (the URL containing `code=...`). Or type /cancel.", parse_mode="Markdown")
                return

        # 2) Intent routing (keyword fast-path + LLM fallback)
        routed = await asyncio_to_thread(agent_brain.route_intent, text, True)
        intent = routed["intent"]
        args = routed.get("args", text)

        if intent == "produce":
            topic = args.strip()
            for trigger in _PRODUCE_TRIGGERS:
                if trigger in lowered:
                    idx = lowered.index(trigger) + len(trigger)
                    topic = text[idx:].strip(" :'\"") or topic
                    break
            topic = topic.removeprefix("make a video about ").removeprefix("produce ").strip() or topic
            await update.message.reply_text(f"🎬 On it! Starting full 1080p production for: *{topic[:120]}*", parse_mode="Markdown")
            await run_full_production(context.bot, chat_id, topic)
        elif intent == "suggest":
            await cmd_suggest(update, context)
        elif intent == "upload":
            await cmd_upload(update, context)
        elif intent == "comments":
            await cmd_comments(update, context)
        elif intent == "script":
            topic = args.removeprefix("script for ").removeprefix("script about ").removeprefix("script on ").removeprefix("script ").strip()
            context.args = topic.split()
            await cmd_script(update, context)
        elif intent == "remember":
            fact = args.removeprefix("remember ").strip()
            context.args = fact.split()
            await cmd_remember(update, context)
        elif intent == "history":
            await cmd_history(update, context)
        elif intent == "status":
            await cmd_status(update, context)
        elif intent == "settings":
            await cmd_settings(update, context)
        else:
            await update.message.chat.send_action(ChatAction.TYPING)
            reply = await asyncio_to_thread(ai_chat_reply, text)
            if len(reply) > 4096:
                reply = reply[:4090] + "…"
            await update.message.reply_text(reply)

    async def cmd_cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
        popped = PENDING_AUTH.pop(update.effective_chat.id, None)
        if popped:
            await update.message.reply_text("🚫 Cancelled the pending sign-in.")
        else:
            await update.message.reply_text("Nothing to cancel 🙂")

    # ------------------------------------------------------------------
    # ⏰ /schedule — auto-pilot scheduler
    # ------------------------------------------------------------------
    async def cmd_schedule(update: Update, context: ContextTypes.DEFAULT_TYPE):
        import agent_features
        parsed = agent_features.parse_schedule_text(list(context.args or []))
        if parsed is None:
            await update.message.reply_text(
                "Usage:\n`/schedule daily 18:00` (9am/6pm o chole)\n`/schedule daily 9am upload`\n"
                "`/schedule interval 6h upload`\n`/schedule off` — stop all\n\n"
                "Each run: top trending suggestion → full 1080p production → (optional) upload.",
                parse_mode="Markdown",
            )
            return
        if parsed["kind"] == "list":
            rows = agent_brain.get_schedules()
            if not rows:
                await update.message.reply_text("📭 No schedules yet. Set one: `/schedule daily 18:00`", parse_mode="Markdown")
                return
            lines = ["⏰ *Schedules:*"] + [
                f"• #{r['id']} — {r['kind']} {r['time_text']}" + (" +upload" if r["auto_upload"] else "") for r in rows
            ]
            await update.message.reply_text("\n".join(lines), parse_mode="Markdown")
            return
        if parsed["kind"] == "off":
            n = agent_brain.set_all_schedules_active(False)
            if SCHEDULER is not None:
                for job in SCHEDULER.get_jobs():
                    if job.id.startswith("prod_"):
                        job.remove()
            await update.message.reply_text(f"🛑 Stopped {n} schedule(s).")
            return
        sid = agent_brain.add_schedule(update.effective_chat.id, parsed["kind"], parsed["time_text"], parsed["auto_upload"])
        register_one_schedule({"id": sid, "chat_id": update.effective_chat.id, "kind": parsed["kind"],
                               "time_text": parsed["time_text"], "auto_upload": parsed["auto_upload"]})
        tz = os.getenv("SCHEDULER_TZ", "Asia/Dhaka")
        desc = f"daily at {parsed['time_text']} ({tz})" if parsed["kind"] == "daily" else f"every {parsed['time_text']}"
        await update.message.reply_text(
            f"✅ *Auto-pilot armed:* {desc}" + (" + auto-upload 🚀" if parsed["auto_upload"] else "") +
            "\nBot nije top trending topic pick kore full video banabe!",
            parse_mode="Markdown",
        )

    # ------------------------------------------------------------------
    # 📊 /analytics — YouTube Analytics report
    # ------------------------------------------------------------------
    async def cmd_analytics(update: Update, context: ContextTypes.DEFAULT_TYPE):
        import agent_features
        days = 7
        if context.args and context.args[0].isdigit():
            days = min(max(int(context.args[0]), 1), 90)
        msg = await update.message.reply_text(f"📊 Crunching your last *{days} days*...")
        try:
            report = await asyncio_to_thread(agent_features.fetch_analytics_report, get_engine(), days)
            await msg.edit_text(report["summary"], parse_mode="Markdown")
        except RuntimeError as e:
            await msg.edit_text(f"🔐 {e}\nThen try /analytics again.", parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"❌ Analytics failed: `{e}`\n(If permissions error → /reauth to add analytics scope)", parse_mode="Markdown")

    # ------------------------------------------------------------------
    # 🖼️ /thumbs — A/B/C thumbnail variants + pick buttons
    # ------------------------------------------------------------------
    async def cmd_thumbs(update: Update, context: ContextTypes.DEFAULT_TYPE):
        import agent_features
        raw = " ".join(context.args).strip()
        if not raw:
            await update.message.reply_text("Usage: `/thumbs futuristic city | FUTURE CITIES`", parse_mode="Markdown")
            return
        prompt, _, title = raw.partition("|")
        prompt, title = prompt.strip(), (title.strip() or prompt.strip()[:40])
        msg = await update.message.reply_text("🎨 Generating *3 thumbnail variants* (A/B/C)... patience 🙏")
        engine = get_engine()
        variants = await asyncio_to_thread(agent_features.generate_thumb_variants, engine, prompt, title, str(PRODUCTION_DIR / "ab_test"))
        if not variants:
            await msg.edit_text("❌ All variants failed. Check HF/Pollinations access.")
            return
        THUMB_MAP.clear()
        for variant, path in variants:
            THUMB_MAP[variant] = path
        media = []
        for variant, path in variants:
            with open(path, "rb") as f:
                from telegram import InputMediaPhoto
                media.append(InputMediaPhoto(f.read(), caption=f"Variant {variant}"))
        await update.message.reply_media_group(media=media)
        buttons = [[InlineKeyboardButton(f"Use {v}", callback_data=f"ab:{v}") for v, _ in variants]]
        await msg.edit_text("👆 Which one should I use?", reply_markup=InlineKeyboardMarkup(buttons))

    async def on_thumb_pick(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        variant = query.data.split(":")[1]
        path = THUMB_MAP.get(variant)
        if not path or not os.path.exists(path):
            await query.edit_message_text("⌛ Variant expired — run /thumbs again.")
            return
        last = agent_brain.get_last_production()
        if last and last.get("video_path"):
            new_path = os.path.join(os.path.dirname(last["video_path"]), "thumbnail.jpg")
            shutil.copyfile(path, new_path)
            agent_brain.add_history(topic=last.get("topic", ""), title=last.get("title", ""),
                                    video_path=last["video_path"], thumb_path=new_path,
                                    tags=last.get("tags", ""), description=last.get("description", ""),
                                    narration=last.get("narration", ""), status="produced")
            await query.edit_message_text(f"✅ Thumbnail *{variant}* set as final! `/upload last` will use it.", parse_mode="Markdown")
        else:
            await query.edit_message_text(f"✅ Saved variant {variant}: `{path}`", parse_mode="Markdown")

    # ------------------------------------------------------------------
    # 📱 /shorts — 9:16 Shorts from the last production
    # ------------------------------------------------------------------
    async def cmd_shorts(update: Update, context: ContextTypes.DEFAULT_TYPE):
        import agent_features
        last = next((h for h in agent_brain.get_history(limit=20)
                     if h.get("video_path") and os.path.exists(h["video_path"])), None)
        if not last:
            await update.message.reply_text("🤔 No produced video found. `/produce <topic>` first!", parse_mode="Markdown")
            return
        out_path = os.path.join(os.path.dirname(last["video_path"]), "shorts_trailer.mp4")
        msg = await update.message.reply_text(f"📱 Cutting *9:16 Shorts* from _{last.get('title') or last.get('topic')}_ ...")
        try:
            ok = await asyncio_to_thread(agent_features.make_shorts, get_engine(), last["video_path"], out_path, last.get("topic") or last.get("title") or "video")
            if ok and os.path.getsize(out_path) <= MAX_TG_FILE_BYTES:
                with open(out_path, "rb") as f:
                    await update.message.reply_video(f, caption="📱 Vertical Shorts — ready for YouTube Shorts!", supports_streaming=True)
                await msg.delete()
            else:
                await msg.edit_text("❌ Shorts generation failed or file too big.")
        except Exception as e:
            logger.exception("Shorts failed")
            await msg.edit_text(f"❌ Shorts failed: `{e}`", parse_mode="Markdown")

    # ------------------------------------------------------------------
    # 🌐 /localize — multi-language versions of the last video
    # ------------------------------------------------------------------
    async def cmd_localize(update: Update, context: ContextTypes.DEFAULT_TYPE):
        import agent_features
        langs_raw = " ".join(context.args).strip()
        if not langs_raw:
            await update.message.reply_text("Usage: `/localize bn,hi` — makes Bengali + Hindi versions of your last video.\nSupported: bn hi es fr de ar ja pt ru en", parse_mode="Markdown")
            return
        langs = [l.strip().lower() for l in langs_raw.split(",") if l.strip()][:3]
        last = next((h for h in agent_brain.get_history(limit=20)
                     if h.get("video_path") and os.path.exists(h["video_path"])), None)
        if not last:
            await update.message.reply_text("🤔 No produced video found. `/produce <topic>` first!", parse_mode="Markdown")
            return
        narration = last.get("narration") or last.get("description") or ""
        if not narration:
            await update.message.reply_text("🤔 No narration stored for that video — produce a new one first.")
            return
        engine = get_engine()
        prod_dir = os.path.dirname(last["video_path"])
        for lang in langs:
            voice = agent_features.LANG_VOICE.get(lang)
            msg = await update.message.reply_text(f"🌍 Making *{lang.upper()}* version (translate → voice → audio swap)...")
            try:
                translated = await asyncio_to_thread(engine.translate_script, narration[:3500], lang)
                vo_path = os.path.join(prod_dir, f"voiceover_{lang}.mp3")
                await asyncio_to_thread(engine.generate_voiceover_sync, translated, vo_path, voice)
                out_path = os.path.join(prod_dir, f"video_{lang}.mp4")
                ok = await asyncio_to_thread(agent_features.swap_video_audio, last["video_path"], vo_path, out_path)
                if ok and os.path.getsize(out_path) <= MAX_TG_FILE_BYTES:
                    with open(out_path, "rb") as f:
                        await update.message.reply_video(f, caption=f"🌍 {lang.upper()} version — Full HD visuals, {lang} voiceover", supports_streaming=True)
                    await msg.delete()
                else:
                    await msg.edit_text(f"❌ {lang.upper()} version failed or too big.")
            except Exception as e:
                logger.exception(f"Localization {lang} failed")
                await msg.edit_text(f"❌ {lang.upper()} failed: `{e}`", parse_mode="Markdown")

    # ------------------------------------------------------------------
    # 🔬 /research — keyless research (Wikipedia + Google News)
    # ------------------------------------------------------------------
    async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE):
        import agent_features
        topic = " ".join(context.args).strip()
        if not topic:
            await update.message.reply_text("Usage: `/research quantum computing`", parse_mode="Markdown")
            return
        msg = await update.message.reply_text(f"🔬 Researching *{topic}* (Wikipedia + fresh news)...")
        try:
            result = await asyncio_to_thread(agent_features.research_topic, topic)
            lines = [f"🔬 *Research: {topic}*\n"]
            if result.get("wiki"):
                lines.append(f"📚 *Wikipedia:*\n{result['wiki'][:800]}\n")
            if result.get("news"):
                lines.append("📰 *Latest headlines:*")
                lines.extend(f"• {n['title']}" for n in result["news"][:6])
            if len(lines) == 2:
                lines.append("Nothing found — try a broader topic.")
            lines.append("\nProduce with this research: `/produce " + topic[:80] + "`")
            await msg.edit_text("\n".join(lines)[:4090], parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"❌ Research failed: `{e}`", parse_mode="Markdown")

    # ------------------------------------------------------------------
    # 🚨 /alerts — trend spike alert keywords
    # ------------------------------------------------------------------
    async def cmd_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
        kws = " ".join(context.args).strip()
        if kws:
            agent_brain.set_profile("alert_keywords", kws)
            await update.message.reply_text(
                f"🚨 Alert keywords saved: *{kws}*\nI check trends every 6 hours — spike korle proactively janabo!",
                parse_mode="Markdown",
            )
            return
        current = agent_brain.get_profile().get("alert_keywords", "")
        if current:
            await update.message.reply_text(f"🚨 Current alert keywords: *{current}*\nUpdate: `/alerts ai, space, tech`", parse_mode="Markdown")
        else:
            await update.message.reply_text("🚨 *Trend Spike Alerts*\n\nI monitor Google Trends every 6 hours for your keywords and message you when something spikes 1.8x+.\n\nSet keywords:\n`/alerts artificial intelligence, space, gadgets`\n\n(Uses your niche too!)", parse_mode="Markdown")

    # ------------------------------------------------------------------
    # 🎁 /boost — engagement boost now (instead of waiting 24h)
    # ------------------------------------------------------------------
    async def cmd_boost(update: Update, context: ContextTypes.DEFAULT_TYPE):
        import agent_features
        video_id = " ".join(context.args).strip()
        if not video_id:
            last = next((h for h in agent_brain.get_history(limit=20) if h.get("video_id")), None)
            video_id = last["video_id"] if last else ""
        if not video_id:
            await update.message.reply_text("Usage: `/boost <video_id>` (or upload first with /upload)", parse_mode="Markdown")
            return
        msg = await update.message.reply_text("🎁 Finding the hottest comment + crafting a personal reply...")
        try:
            result = await asyncio_to_thread(agent_features.engagement_boost, get_engine(), video_id)
            stats = result.get("stats", {})
            top = result.get("top_comment")
            lines = [f"🎁 *{stats.get('title', video_id)}*", f"👁️ {stats.get('views', 0):,} views · 👍 {stats.get('likes', 0):,} · 💬 {stats.get('comments', 0):,}"]
            if top:
                lines.append(f"\n🏆 Top comment ({top['likes']} ❤️) — {top['author']}:\n\"{top['text'][:150]}\"")
                lines.append(("↩️ Replied: _" + result.get("reply_text", "")[:150] + "_") if result.get("replied") else "⚠️ Reply post failed")
            else:
                lines.append("\n📭 No comments yet.")
            await msg.edit_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"❌ Boost failed: `{e}`\n(Permissions error → /reauth)", parse_mode="Markdown")

    # ------------------------------------------------------------------
    # 🎙️ /voices — edge-tts voice selection (400+ free voices)
    # ------------------------------------------------------------------
    async def cmd_voices(update: Update, context: ContextTypes.DEFAULT_TYPE):
        import agent_features
        args = context.args or []
        if args and args[0].lower() == "use" and len(args) >= 2:
            voice = args[1]
            agent_brain.set_profile("tts_voice", voice)
            await update.message.reply_text(f"🎙️ Default voice set: *{voice}*\nAll future productions will use it!", parse_mode="Markdown")
            return
        lang = args[0] if args else "en"
        msg = await update.message.reply_text(f"🎙️ Loading {lang} voices...")
        try:
            voices = await asyncio_to_thread(agent_features.list_edge_voices, lang, 12)
            if not voices:
                await msg.edit_text(f"No voices for `{lang}`. Try: en, bn, hi, es, fr, de, ar, ja, ru, pt", parse_mode="Markdown")
                return
            lines = [f"🎙️ *Voices for `{lang}`:*\n"] + [
                f"• `{v['name']}` ({v['gender']})" for v in voices
            ]
            lines.append("\nSet default: `/voices use en-US-ChristopherNeural`\nBangla: `/voices bn`")
            await msg.edit_text("\n".join(lines), parse_mode="Markdown")
        except Exception as e:
            await msg.edit_text(f"❌ Voice list failed: `{e}`", parse_mode="Markdown")

    # ------------------------------------------------------------------
    # 🔐 /reauth — re-run headless OAuth (adds analytics scope)
    # ------------------------------------------------------------------
    async def cmd_reauth(update: Update, context: ContextTypes.DEFAULT_TYPE):
        engine = get_engine()
        try:
            if os.path.exists(engine.YOUTUBE_TOKEN_FILE):
                os.remove(engine.YOUTUBE_TOKEN_FILE)
        except Exception:
            pass
        if not _oauth_setup_ready():
            await update.message.reply_text("⚠️ Set GOOGLE_CLIENT_ID + GOOGLE_CLIENT_SECRET (or client_secrets.json) first!", parse_mode="Markdown")
            return
        auth_url = engine.build_headless_auth_url()
        PENDING_AUTH[update.effective_chat.id] = {"action": "reauth", "payload": {}}
        await update.message.reply_text(
            "🔐 *Fresh sign-in (with analytics scope)*\n\n1️⃣ Open:\n" + auth_url +
            "\n\n2️⃣ Paste the full redirect URL (the one with `code=...`) here.",
            parse_mode="Markdown",
        )

    app = (Application.builder()
           .token(TELEGRAM_BOT_TOKEN)
           .post_init(post_init)
           .build())
    app.add_handler(CommandHandler(["start"], cmd_start))
    app.add_handler(CommandHandler(["help"], cmd_help))
    app.add_handler(CommandHandler(["status"], cmd_status))
    app.add_handler(CommandHandler(["suggest"], cmd_suggest))
    app.add_handler(CommandHandler(["trends"], cmd_trends))
    app.add_handler(CommandHandler(["script"], cmd_script))
    app.add_handler(CommandHandler(["translate"], cmd_translate))
    app.add_handler(CommandHandler(["thumbnail"], cmd_thumbnail))
    app.add_handler(CommandHandler(["sentiment"], cmd_sentiment))
    app.add_handler(CommandHandler(["produce"], cmd_produce))
    app.add_handler(CommandHandler(["upload"], cmd_upload))
    app.add_handler(CommandHandler(["comments"], cmd_comments))
    app.add_handler(CommandHandler(["settings"], cmd_settings))
    app.add_handler(CommandHandler(["remember"], cmd_remember))
    app.add_handler(CommandHandler(["history"], cmd_history))
    app.add_handler(CommandHandler(["forget"], cmd_forget))
    app.add_handler(CommandHandler(["cancel"], cmd_cancel))
    app.add_handler(CommandHandler(["schedule"], cmd_schedule))
    app.add_handler(CommandHandler(["analytics"], cmd_analytics))
    app.add_handler(CommandHandler(["thumbs"], cmd_thumbs))
    app.add_handler(CommandHandler(["shorts"], cmd_shorts))
    app.add_handler(CommandHandler(["localize"], cmd_localize))
    app.add_handler(CommandHandler(["research"], cmd_research))
    app.add_handler(CommandHandler(["alerts"], cmd_alerts))
    app.add_handler(CommandHandler(["boost"], cmd_boost))
    app.add_handler(CommandHandler(["voices"], cmd_voices))
    app.add_handler(CommandHandler(["reauth"], cmd_reauth))
    app.add_handler(CallbackQueryHandler(on_suggestion_tap, pattern="^(p|s)\\d+$"))
    app.add_handler(CallbackQueryHandler(on_thumb_pick, pattern="^ab:"))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_chat))
    return app


def main():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
        print("❌ TELEGRAM_BOT_TOKEN is missing!")
        print("   1. Talk to @BotFather on Telegram -> /newbot -> copy the token")
        print("   2. Add it to your .env:  TELEGRAM_BOT_TOKEN=123456:ABC-DEF...")
        raise SystemExit(1)

    logger.info("🤖 Starting AuraStream 2.0 Telegram AI Agent (memory + autopilot + 1080p)...")
    app = build_application()
    logger.info("✅ Bot polling started — press Ctrl+C to stop")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message"])


if __name__ == "__main__":
    main()
