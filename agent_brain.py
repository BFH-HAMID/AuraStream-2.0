"""
==============================================================================
Agent Brain for AuraStream 2.0 — Memory + Suggestions + Intent Router
==============================================================================
Gives the Telegram AI Agent a persistent brain:

  - Channel profile (niche, tone, audience, language, region)  -> /settings
  - Custom instructions ("always use hooks in first 5 seconds") -> /settings custom
  - Facts it learns ("my audience loves space content")         -> /remember
  - Full video production history (topic, title, video id)      -> /history
  - Trending-topic suggestion engine with virality analysis     -> /suggest
  - Intent router so free text like "make a video about X" works -> free chat

Storage: SQLite (stdlib). Override path with AGENT_MEMORY_DB env var.
==============================================================================
"""

import json
import logging
import os
import random
import re
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger("agent_brain")

DB_PATH = Path(os.getenv("AGENT_MEMORY_DB", "temp_assets/agent_memory.db"))
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

PROFILE_KEYS = ["niche", "tone", "audience", "language", "region", "upload_privacy", "channel_name",
                "tts_voice", "produce_languages", "alert_keywords"]

_INTENTS = [
    "produce",      # full video production
    "script",       # script + metadata only
    "suggest",      # topic suggestions
    "upload",       # upload last/specified video to YouTube
    "comments",     # read + reply to comments
    "thumbnail",    # thumbnail only
    "translate",    # translate text
    "sentiment",    # analyze sentiment
    "status",       # engine status
    "history",      # show past videos
    "remember",     # store a fact
    "settings",     # view/update profile
    "schedule",     # auto-pilot scheduler
    "analytics",    # YouTube analytics report
    "shorts",       # make shorts from last video
    "localize",     # multi-language versions
    "research",     # research a topic
    "alerts",       # trend spike alert settings
    "boost",        # engagement boost (top comment reply)
    "voices",       # list/select TTS voices
    "chat",         # plain conversation
]


# =============================================================================
# Database bootstrap
# =============================================================================
def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    with _connect() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS channel_profile (
                key   TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fact TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS video_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT, title TEXT, video_id TEXT,
                video_path TEXT, thumb_path TEXT,
                status TEXT DEFAULT 'produced',
                tags TEXT, description TEXT, narration TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS suggested_topics (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                topic TEXT NOT NULL,
                reason TEXT, score REAL DEFAULT 0,
                audience TEXT, status TEXT DEFAULT 'suggested',
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS schedules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                kind TEXT NOT NULL,
                time_text TEXT NOT NULL,
                auto_upload INTEGER DEFAULT 0,
                active INTEGER DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS kv (
                key TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        # Migrations for databases created by older versions
        for col in ("narration TEXT", "description TEXT"):
            try:
                conn.execute(f"ALTER TABLE video_history ADD COLUMN {col}")
            except Exception:
                pass  # column already exists


_init_db()


# =============================================================================
# Key-value store (last chat id, small runtime state)
# =============================================================================
def kv_set(key: str, value: str):
    with _connect() as conn:
        conn.execute(
            "INSERT INTO kv (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (key, str(value)),
        )


def kv_get(key: str, default: str = "") -> str:
    with _connect() as conn:
        row = conn.execute("SELECT value FROM kv WHERE key = ?", (key,)).fetchone()
        return row["value"] if row and row["value"] is not None else default


# =============================================================================
# Channel profile — "agent customized kore"
# =============================================================================
def set_profile(key: str, value: str) -> bool:
    key = key.strip().lower()
    if key not in PROFILE_KEYS:
        return False
    with _connect() as conn:
        conn.execute(
            "INSERT INTO channel_profile (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (key, value.strip()),
        )
    return True


def get_profile() -> Dict[str, str]:
    with _connect() as conn:
        return {r["key"]: r["value"] for r in conn.execute("SELECT key, value FROM channel_profile")}


# =============================================================================
# Custom instructions & facts — "agent sob kichu jane"
# =============================================================================
def remember_fact(fact: str) -> int:
    with _connect() as conn:
        cur = conn.execute("INSERT INTO facts (fact) VALUES (?)", (fact.strip(),))
        return cur.lastrowid


def set_custom_instructions(text: str):
    set_profile("custom_instructions", text) if False else None
    with _connect() as conn:
        conn.execute(
            "INSERT INTO channel_profile (key, value) VALUES ('custom_instructions', ?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=CURRENT_TIMESTAMP",
            (text.strip(),),
        )


def get_custom_instructions() -> str:
    return get_profile().get("custom_instructions", "")


def get_facts(limit: int = 20) -> List[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT fact FROM facts ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [r["fact"] for r in rows]


def forget_everything() -> None:
    with _connect() as conn:
        conn.executescript(
            "DELETE FROM channel_profile; DELETE FROM facts; "
            "DELETE FROM video_history; DELETE FROM suggested_topics;"
        )


# =============================================================================
# Video history — agent knows every produced video
# =============================================================================
def add_history(topic: str, title: str, video_id: str = "", video_path: str = "",
                thumb_path: str = "", tags=None, status: str = "produced",
                description: str = "", narration: str = "") -> int:
    if isinstance(tags, (list, tuple)):
        tags = ", ".join(str(t) for t in tags)
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO video_history (topic, title, video_id, video_path, thumb_path, status, tags, description, narration) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (topic, title, video_id, video_path, thumb_path, status, tags or "", description or "", narration or ""),
        )
        return cur.lastrowid


def get_history(limit: int = 10) -> List[Dict]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT * FROM video_history ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_last_production() -> Optional[Dict]:
    items = get_history(limit=1)
    return items[0] if items else None


# =============================================================================
# Suggestion store
# =============================================================================
def save_suggestions(items: List[Dict]) -> List[int]:
    ids = []
    with _connect() as conn:
        for it in items:
            cur = conn.execute(
                "INSERT INTO suggested_topics (topic, reason, score, audience) VALUES (?, ?, ?, ?)",
                (it.get("topic", ""), it.get("reason", ""), float(it.get("score", 0) or 0),
                 it.get("audience", "")),
            )
            ids.append(cur.lastrowid)
    return ids


def mark_suggestion_used(topic: str):
    with _connect() as conn:
        conn.execute(
            "UPDATE suggested_topics SET status='used' WHERE id = "
            "(SELECT id FROM suggested_topics WHERE topic = ? ORDER BY id DESC LIMIT 1)",
            (topic,),
        )


# =============================================================================
# Schedules (auto-pilot) — "ekbar set, bot nije nije banabe"
# =============================================================================
def add_schedule(chat_id: int, kind: str, time_text: str, auto_upload: bool = False) -> int:
    with _connect() as conn:
        cur = conn.execute(
            "INSERT INTO schedules (chat_id, kind, time_text, auto_upload) VALUES (?, ?, ?, ?)",
            (chat_id, kind, time_text, int(auto_upload)),
        )
        return cur.lastrowid


def get_schedules(active_only: bool = True) -> List[Dict]:
    q = "SELECT * FROM schedules" + (" WHERE active = 1" if active_only else "") + " ORDER BY id DESC"
    with _connect() as conn:
        return [dict(r) for r in conn.execute(q)]


def set_all_schedules_active(active: bool) -> int:
    with _connect() as conn:
        cur = conn.execute("UPDATE schedules SET active = ?", (int(active),))
        return cur.rowcount


# =============================================================================
# AI helper — Gemini first, Hugging Face fallback (both lazy)
# =============================================================================
def _ai_text(prompt: str, max_tokens: int = 900) -> Optional[str]:
    gemini_key = os.getenv("GEMINI_API_KEY", "").strip()
    if gemini_key and gemini_key != "your_gemini_api_key_here":
        try:
            from google import genai
            client = genai.Client(api_key=gemini_key)
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"system_instruction": "You are AuraStream Agent's strategic brain. "
                                              "Always reply with valid JSON when asked."},
            )
            if resp and resp.text:
                return resp.text.strip()
        except Exception as e:
            logger.warning(f"Gemini failed in agent brain: {e}")

    try:
        from hf_engine import get_hf_engine
        reply = get_hf_engine().generate_text(prompt, max_new_tokens=max_tokens)
        if reply:
            return reply.strip()
    except Exception as e:
        logger.warning(f"HF fallback failed in agent brain: {e}")
    return None


def _extract_json(text: str):
    """Best-effort JSON extraction from an LLM reply."""
    if not text:
        return None
    cleaned = re.sub(r"```(?:json)?", "", text).replace("```", "").strip()
    start, end = cleaned.find("["), cleaned.rfind("]")
    if start == -1 or end == -1:
        start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1:
        cleaned = cleaned[start:end + 1]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        return None


# =============================================================================
# Trending topic suggestions with analysis — "suggest kore (analysis soho)"
# =============================================================================
_FALLBACK_TOPICS = [
    "The Untold Story of {seed}", "What If {seed} Disappeared Tomorrow?",
    "{seed}: 10 Facts That Sound Fake (But Are 100% True)",
    "Why Everyone Is Talking About {seed} Right Now",
    "The Dark Side of {seed} Nobody Shows You",
    "How {seed} Will Change the Next 10 Years",
    "{seed} Explained in 3 Minutes (Finally!)",
    "Top 5 {seed} Moments That Broke the Internet",
]


def _fetch_raw_trends(region: str = "united_states", n: int = 15) -> List[str]:
    try:
        from pytrends.request import TrendReq
        pytrends = TrendReq(hl="en-US", tz=360)
        df = pytrends.trending_searches(pn=region)
        if df is not None and not df.empty:
            return [str(v).strip() for v in df[0].tolist()[:n] if str(v).strip()]
    except Exception as e:
        logger.warning(f"pytrends failed: {e}")
    return ["Artificial Intelligence", "Space Exploration", "Cryptocurrency",
            "Electric Vehicles", "New Smartphone Launch"]


def suggest_topics(region: str = "united_states", n: int = 5) -> List[Dict]:
    """Return n topic suggestions with virality score + reason, tailored to the channel."""
    profile = get_profile()
    niche = profile.get("niche", "").strip()
    audience = profile.get("audience", "").strip()
    tone = profile.get("tone", "").strip()
    history = get_history(limit=15)
    past_topics = [h.get("topic", "") for h in history if h.get("topic")]
    trends = _fetch_raw_trends(region)

    prompt = (
        "You are a YouTube growth strategist. Suggest exactly "
        f"{n} video topics that are likely to go viral.\n\n"
        f"Channel niche: {niche or 'general (not set)'}\n"
        f"Target audience: {audience or 'not set'}\n"
        f"Desired tone: {tone or 'engaging'}\n"
        f"Google Trends right now ({region}): {', '.join(trends[:12])}\n"
        f"Videos already made (do NOT repeat these): {', '.join(past_topics) if past_topics else 'none yet'}\n\n"
        'Reply ONLY with a JSON array like:\n'
        '[{"topic": "...", "reason": "why it can go viral (1 sentence)", '
        '"score": 8.5, "audience": "who will watch"}]'
    )

    data = _extract_json(_ai_text(prompt)) or []

    suggestions = []
    if isinstance(data, list) and data:
        for item in data[:n]:
            if isinstance(item, dict) and item.get("topic"):
                try:
                    score = float(item.get("score", 7))
                except (TypeError, ValueError):
                    score = 7.0
                suggestions.append({
                    "topic": str(item["topic"])[:120],
                    "reason": str(item.get("reason", "Aligned with your niche and current trends."))[:300],
                    "score": max(0.0, min(10.0, score)),
                    "audience": str(item.get("audience", audience or "General audience"))[:120],
                })
    else:
        # No AI available — heuristic fallback built from raw trends
        seeds = random.sample(trends, k=min(n, len(trends)))
        for seed in seeds:
            suggestions.append({
                "topic": random.choice(_FALLBACK_TOPICS).format(seed=seed),
                "reason": f"'{seed}' is trending in {region} right now" +
                          (f" and fits your {niche} niche" if niche else ""),
                "score": round(random.uniform(6.5, 8.5), 1),
                "audience": audience or "General audience",
            })

    save_suggestions(suggestions)
    return suggestions


# =============================================================================
# Intent router — free text -> structured action ("agent er moto kotha bole")
# =============================================================================
_FAST_PATHS = [
    (re.compile(r"^\s*shorts?\b|\bmake\b.*\bshorts?\b|\bshorts?\b.*\b(make|create|banao)\b", re.I), "shorts"),
    (re.compile(r"\b(make|create|produce|generate|banao|banabo|bana)\b.*\bvideo\b|\bvideo\b.*\b(banao|banabo|make|produce)\b", re.I), "produce"),
    (re.compile(r"\b(suggest|topic ideas?|ideas? for|kemon topic|topic suggest)\b", re.I), "suggest"),
    (re.compile(r"^\s*/?upload\b|\bupload\b.*\byoutube\b", re.I), "upload"),
    (re.compile(r"\b(reply|respond|answer)\b.*\bcomments?\b|\bcomments?\b.*\b(reply|manage|check)\b", re.I), "comments"),
    (re.compile(r"^\s*script\s+(for|about|on)?\s*\S+", re.I), "script"),
    (re.compile(r"\b(thumbnail|thumbail|thumb nail)\b", re.I), "thumbnail"),
    (re.compile(r"^\s*translate\b", re.I), "translate"),
    (re.compile(r"\b(sentiment|emotion)\b.*\b(analy|check|detect)", re.I), "sentiment"),
    (re.compile(r"^\s*(status|engine status)\b|\b(engine|system) status\b", re.I), "status"),
    (re.compile(r"^\s*(schedule|autopilot schedule|auto schedule)\b|\bschedule\b.*\b(daily|every|interval)\b", re.I), "schedule"),
    (re.compile(r"\banalytics\b|\bhow (are|is) my (videos?|channel)|\bviews report\b|\bperformance report\b", re.I), "analytics"),
    (re.compile(r"^\s*(localize|localise|translate video|multi.?language)\b", re.I), "localize"),
    (re.compile(r"^\s*research\b|\bresearch (about|on)\b|\bfacts? about\b", re.I), "research"),
    (re.compile(r"^\s*(trend )?alerts?\b", re.I), "alerts"),
    (re.compile(r"^\s*(boost|engagement boost|pin)\b|\btop comment\b", re.I), "boost"),
    (re.compile(r"^\s*voices?\b", re.I), "voices"),
    (re.compile(r"^\s*(history|my videos|past videos)\b|\bvideo history\b.*\b(dekhao|show|dik)\b|\bshow\b.*\bhistory\b", re.I), "history"),
    (re.compile(r"^\s*remember\s+\S+", re.I), "remember"),
    (re.compile(r"^\s*(settings|setting)\b", re.I), "settings"),
]


def route_intent(text: str, ai_available: bool = True) -> Dict:
    """Map free text to an intent. Fast keyword path first, LLM fallback."""
    text = (text or "").strip()
    for pattern, intent in _FAST_PATHS:
        if pattern.search(text):
            return {"intent": intent, "args": text, "via": "keyword"}

    if ai_available:
        prompt = (
            "Classify the user message for a YouTube automation assistant.\n"
            f"Possible intents: {', '.join(_INTENTS)}.\n"
            f'User message: "{text[:500]}"\n\n'
            'Reply ONLY with JSON: {"intent": "...", "args": "the actionable part"}\n'
            'Use "chat" if it is just conversation.'
        )
        data = _extract_json(_ai_text(prompt, max_tokens=120))
        if isinstance(data, dict) and data.get("intent") in _INTENTS:
            return {"intent": data["intent"], "args": data.get("args", text), "via": "llm"}

    return {"intent": "chat", "args": text, "via": "default"}


# =============================================================================
# Agent context — injected into every AI reply ("sob kichu jane")
# =============================================================================
def build_agent_context() -> str:
    profile = get_profile()
    facts = get_facts(limit=10)
    history = get_history(limit=8)

    lines = ["=== WHAT YOU KNOW ABOUT THIS CREATOR ==="]
    if profile:
        pretty = {k: v for k, v in profile.items() if v}
        lines.append("Channel profile: " + json.dumps(pretty, ensure_ascii=False))
    else:
        lines.append("Channel profile: not set yet (ask them to use /settings)")
    if facts:
        lines.append("Things the creator told you to remember:")
        lines.extend(f"- {f}" for f in facts)
    if history:
        lines.append("Recent videos produced (do not repeat topics):")
        for h in history:
            vid = f" (YouTube: {h['video_id']})" if h.get("video_id") else ""
            lines.append(f"- {h.get('title') or h.get('topic')}{vid} [{h.get('status')}]")
    else:
        lines.append("No videos produced yet.")
    lines.append("Capabilities you can mention: full 1080p autopilot production, scripts, "
                 "SEO metadata, SDXL thumbnails, YouTube upload, sentiment-aware comment replies, "
                 "trend-based topic suggestions, 200+ language translation.")
    return "\n".join(lines)
