"""
==============================================================================
Telegram AI Agent for AuraStream 2.0
==============================================================================
A full conversational AI Agent on Telegram - control your entire YouTube AI
studio from your phone:

    /start          - Welcome + capability overview
    /help           - Full command reference
    /status         - Check API keys & engine status
    /trends [region]- Fetch a trending topic (Google Trends)
    /script <topic> - Generate full script + SEO metadata (Gemini -> HF fallback)
    /translate <lang> <text> - Translate any text (HF NLLB-200 -> Gemini)
    /thumbnail <prompt> | <title> - AI thumbnail (SDXL -> Pollinations)
    /sentiment <text> - Sentiment + emotion analysis for comments
    /produce <topic> - FULL AUTOPILOT: script -> 1080p Full HD video + thumbnail,
                       rendered and delivered straight into the chat
    Free text       - Chat with the AuraStream AI agent (Gemini + HF fallback)

Run:  python telegram_agent.py
Docker:  docker compose --profile agent up agent
==============================================================================
"""

import os
import io
import json
import asyncio
import logging
import tempfile
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("telegram_agent")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
MAX_TG_FILE_BYTES = 49 * 1024 * 1024  # Telegram Bot API upload limit is 50 MB

# One production at a time per chat (rendering is heavy)
_BUSY_CHATS = set()

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
# AI helpers (Gemini primary, HF fallback)
# ---------------------------------------------------------------------------
GEMINI_CHAT_MODEL = "gemini-2.5-flash"

SYSTEM_PROMPT = (
    "You are AuraStream Agent, the built-in AI assistant of AuraStream 2.0 - an autonomous "
    "YouTube production studio. You help creators with video ideas, scripts, SEO, thumbnails, "
    "trends and channel growth. Be concise, friendly and practical. Use Telegram-friendly "
    "formatting (short paragraphs, emoji, bullets). Answer in the language the user writes in."
)


def ai_chat_reply(user_text: str) -> str:
    """Free-text AI agent reply: Gemini first, Hugging Face fallback."""
    engine = get_engine()
    try:
        client = engine.get_gemini_client()
        response = client.models.generate_content(
            model=GEMINI_CHAT_MODEL,
            contents=user_text,
            config={"system_instruction": SYSTEM_PROMPT},
        )
        if response and response.text:
            return response.text.strip()
    except Exception as e:
        logger.warning(f"Gemini chat failed: {e}")

    # Hugging Face fallback
    try:
        from hf_engine import get_hf_engine
        reply = get_hf_engine().generate_text(
            f"{SYSTEM_PROMPT}\n\nUser: {user_text}\n\nAssistant:", max_new_tokens=400
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
# Full autopilot pipeline (1080p Full HD)
# ---------------------------------------------------------------------------
async def run_full_production(bot, chat_id: int, topic: str):
    """Script -> voiceover -> 1080p footage -> assemble -> thumbnail -> deliver."""
    if chat_id in _BUSY_CHATS:
        await bot.send_message(chat_id, "⏳ A production is already running in this chat. Please wait for it to finish.")
        return
    _BUSY_CHATS.add(chat_id)
    started = time.time()
    tmp_dir = tempfile.mkdtemp(prefix="aurastream_tg_")
    status = await bot.send_message(chat_id, f"🚀 *Autopilot engaged!*\n\nTopic: *{topic}*\nTarget: *1080p Full HD*\n\n1/6 · Generating script...")
    try:
        engine = get_engine()

        # 1) Script + metadata (Gemini -> HF fallback)
        script_data = await asyncio.to_thread(engine.generate_long_script_hf_fallback, topic, 2, "auto")
        if not script_data:
            script_data = await asyncio.to_thread(engine.generate_long_script, topic, 2)
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
            await asyncio.to_thread(engine.generate_voiceover_sync, full_script[:4000], voiceover_path)
        except Exception as e:
            logger.warning(f"Edge-TTS failed, trying HF: {e}")
            ok = await asyncio.to_thread(engine.generate_voiceover_hf, full_script[:500], voiceover_path, "en")
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
        clips = await asyncio.to_thread(engine.download_bulk_videos, keywords[:10], "1080p")
        if not clips:
            await status.edit_text("❌ No stock footage could be downloaded. Check PEXELS/PIXABAY keys.")
            return

        # 4) Assemble 1080p Full HD video
        await status.edit_text(f"4/6 · Assembling *1080p Full HD* video ({len(clips)} clips)...")
        main_video_path = os.path.join(tmp_dir, "main_video.mp4")
        await asyncio.to_thread(
            engine.create_mega_production, clips, voiceover_path,
            script_data.get("title", topic), main_video_path, (1920, 1080)
        )

        # 5) Thumbnail
        await status.edit_text("5/6 · Designing AI thumbnail (SDXL → Pollinations)...")
        thumb_path = os.path.join(tmp_dir, "thumbnail.jpg")
        thumb_ok = await asyncio.to_thread(
            engine.generate_thumbnail_with_hf,
            script_data.get("thumbnail_prompt", topic),
            script_data.get("title", topic),
            thumb_path,
            "auto",
        )

        # 6) Deliver
        await status.edit_text("6/6 · Uploading to Telegram...")
        caption = (
            f"✅ *{script_data.get('title', topic)}*\n"
            f"🎥 1080p Full HD · 30fps · H.264\n"
            f"⏱️ {time.time() - started:.0f}s render time"
        )
        if os.path.exists(thumb_path) and thumb_ok:
            with open(thumb_path, "rb") as f:
                await bot.send_photo(chat_id, f, caption="🖼️ AI Thumbnail (SDXL → Pollinations)")
        if os.path.exists(main_video_path):
            size = os.path.getsize(main_video_path)
            if size <= MAX_TG_FILE_BYTES:
                with open(main_video_path, "rb") as f:
                    await bot.send_video(chat_id, f, caption=caption, supports_streaming=True)
            else:
                await bot.send_message(
                    chat_id,
                    f"📦 Final video is {size / (1024 * 1024):.0f} MB — above Telegram's 50 MB bot limit.\n"
                    f"Saved on server at: `{main_video_path}`",
                )
            engine.send_telegram_message(f"✅ AuraStream Telegram Agent: delivered '{script_data.get('title', topic)}' ({size / (1024 * 1024):.0f} MB, 1080p Full HD)")
        else:
            await status.edit_text("❌ Video assembly produced no file.")
            return

        await status.edit_text(f"🏁 Production complete in {time.time() - started:.0f}s! 🎬")
    except Exception as e:
        logger.exception("Full production failed")
        try:
            await status.edit_text(f"❌ Production failed: {e}")
        except Exception:
            pass
    finally:
        _BUSY_CHATS.discard(chat_id)
        # Cleanup temp clips
        try:
            for f in Path(tmp_dir).glob("*"):
                f.unlink(missing_ok=True)
            os.rmdir(tmp_dir)
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Telegram bot wiring (python-telegram-bot v21+)
# ---------------------------------------------------------------------------
def build_application():
    from telegram import Update
    from telegram.constants import ChatAction
    from telegram.ext import (
        Application, CommandHandler, MessageHandler, ContextTypes, filters
    )

    async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "👋 *Welcome to AuraStream 2.0 — Telegram AI Agent!*\n\n"
            "I'm your autonomous YouTube studio. What I can do:\n\n"
            "🎬 `/produce <topic>` — full autopilot: script → voiceover → *1080p Full HD* video → AI thumbnail, delivered here\n"
            "📜 `/script <topic>` — script + SEO metadata\n"
            "🔥 `/trends` — trending topic ideas\n"
            "🖼️ `/thumbnail <prompt> \\| <title>` — AI thumbnail\n"
            "🌍 `/translate <lang> <text>` — 200+ languages\n"
            "💬 `/sentiment <text>` — comment sentiment analysis\n"
            "📊 `/status` — engine status\n\n"
            "Or just *talk to me* — I'm a full AI agent 🤖",
            parse_mode="Markdown",
        )

    async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.reply_text(
            "*📖 AuraStream Agent Commands*\n\n"
            "/start — welcome\n"
            "/help — this guide\n"
            "/status — API keys & engines\n"
            "/trends \\[region\\] — trending topics\n"
            "/script \\<topic\\> — full script + metadata\n"
            "/translate \\<lang\\> \\<text\\> — translation\n"
            "/thumbnail \\<prompt\\> \\| \\<title\\> — AI thumbnail\n"
            "/sentiment \\<text\\> — sentiment analysis\n"
            "/produce \\<topic\\> — 🎬 full 1080p autopilot production\n\n"
            "_Any other text = free chat with the AI agent._",
            parse_mode="MarkdownV2",
        )

    async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
        def check(v, placeholder="your_"):
            return bool(v) and not v.startswith(placeholder)
        engine = get_engine()
        lines = [
            "📊 *AuraStream Engine Status*",
            f"Gemini: {'✅' if check(engine.GEMINI_API_KEY) else '❌'}",
            f"Hugging Face: {'✅' if check(engine.HUGGINGFACE_API_KEY) else '❌'}",
            f"Pexels: {'✅' if check(engine.PEXELS_API_KEY) else '❌'}",
            f"Pixabay: {'✅' if check(engine.PIXABAY_API_KEY) else '❌'}",
            f"Telegram Alerts: {'✅' if check(engine.TELEGRAM_BOT_TOKEN) else '⚠️ optional'}",
            f"HF Engine loaded: {'✅' if engine.HF_AVAILABLE else '❌'}",
            "🎥 Render target: *1080p Full HD (1920x1080 @ 30fps)*",
        ]
        await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

    async def cmd_trends(update: Update, context: ContextTypes.DEFAULT_TYPE):
        await update.message.chat.send_action(ChatAction.TYPING)
        region = " ".join(context.args).strip() or "united_states"
        try:
            topic = await asyncio.to_thread(get_engine().fetch_trending_topic, region.replace(" ", "_"))
            await update.message.reply_text(f"🔥 Trending in *{region}*:\n\n*{topic}*\n\nTry: `/produce {topic[:80]}`", parse_mode="Markdown")
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
        data = await asyncio.to_thread(engine.generate_long_script_hf_fallback, topic, 2, "auto") \
            or await asyncio.to_thread(engine.generate_long_script, topic, 2)
        if not data:
            await msg.edit_text("❌ Script generation failed. Check your API keys.")
            return
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
            result = await asyncio.to_thread(get_engine().translate_script, text, lang)
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
            ok = await asyncio.to_thread(get_engine().generate_thumbnail_with_hf, prompt, title, path, "auto")
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
            result = await asyncio.to_thread(get_engine().analyze_comment_sentiment_advanced, text)
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

    async def free_chat(update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Any non-command text -> AI agent chat."""
        text = (update.message.text or "").strip()
        lowered = text.lower()
        # Intent shortcut: "make/create a video about X"
        for trigger in ("make a video", "create a video", "produce a video", "video about"):
            if trigger in lowered:
                topic = text[lowered.index(trigger) + len(trigger):].strip(" :'\"") or text
                await update.message.reply_text(f"🎬 Got it! Starting full 1080p production for: *{topic[:100]}*", parse_mode="Markdown")
                await run_full_production(context.bot, update.effective_chat.id, topic)
                return
        await update.message.chat.send_action(ChatAction.TYPING)
        reply = await asyncio.to_thread(ai_chat_reply, text)
        if len(reply) > 4096:
            reply = reply[:4090] + "…"
        await update.message.reply_text(reply)

    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
    app.add_handler(CommandHandler(["start"], cmd_start))
    app.add_handler(CommandHandler(["help"], cmd_help))
    app.add_handler(CommandHandler(["status"], cmd_status))
    app.add_handler(CommandHandler(["trends"], cmd_trends))
    app.add_handler(CommandHandler(["script"], cmd_script))
    app.add_handler(CommandHandler(["translate"], cmd_translate))
    app.add_handler(CommandHandler(["thumbnail"], cmd_thumbnail))
    app.add_handler(CommandHandler(["sentiment"], cmd_sentiment))
    app.add_handler(CommandHandler(["produce"], cmd_produce))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, free_chat))
    return app


def main():
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
        print("❌ TELEGRAM_BOT_TOKEN is missing!")
        print("   1. Talk to @BotFather on Telegram -> /newbot -> copy the token")
        print("   2. Add it to your .env:  TELEGRAM_BOT_TOKEN=123456:ABC-DEF...")
        raise SystemExit(1)

    logger.info("🤖 Starting AuraStream 2.0 Telegram AI Agent (1080p Full HD ready)...")
    app = build_application()
    logger.info("✅ Bot polling started — press Ctrl+C to stop")
    app.run_polling(drop_pending_updates=True, allowed_updates=["message"])


if __name__ == "__main__":
    main()
