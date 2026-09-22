"""
==============================================================================
Agent Features for AuraStream 2.0 — the "hands-off channel manager" pack
==============================================================================
1.  Auto-Schedule helpers        (parse schedule text -> APScheduler config)
2.  YouTube Analytics report     (youtubeAnalytics v2, free, same OAuth token)
3.  A/B Thumbnail variants       (3 styles + Telegram pick)
4.  Shorts pipeline wrapper      (uses app.create_shorts_trailer)
5.  Trend spike detector         (pytrends, free)
6.  Multi-language localization  (translate -> TTS -> audio swap on last video)
7.  Topic research               (Google News RSS + Wikipedia — keyless, free)
8.  Engagement booster           (top-comment reply + video stats — API pinning
                                  is NOT supported by YouTube Data API, so we
                                  do the next best thing: detect the top-liked
                                  comment and craft a creator reply)
10. Voice options                (edge-tts voice listing/selection)
==============================================================================
"""

import os
import re
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Tuple

import requests

logger = logging.getLogger("agent_features")

USER_AGENT = "AuraStream2.0-Agent/1.0 (YouTube automation bot)"


# =============================================================================
# 7. Topic research — Google News RSS + Wikipedia (no API keys, free forever)
# =============================================================================
def _parse_news_rss(xml_text: str, limit: int = 6) -> List[Dict]:
    """Parse Google News RSS into clean headline entries."""
    items = []
    try:
        root = ET.fromstring(xml_text)
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            pub = (item.findtext("pubDate") or "").strip()
            if not title:
                continue
            # Google News titles end with " - Source"; strip the source suffix
            clean = re.sub(r"\s+-\s+[^-]+$", "", title).strip()
            items.append({"title": clean, "date": pub})
            if len(items) >= limit:
                break
    except ET.ParseError as e:
        logger.warning(f"RSS parse failed: {e}")
    return items


def _fetch_wikipedia_summary(topic: str) -> str:
    try:
        slug = requests.utils.quote(topic.strip().replace(" ", "_"))
        res = requests.get(
            f"https://en.wikipedia.org/api/rest_v1/page/summary/{slug}",
            headers={"User-Agent": USER_AGENT}, timeout=8,
        )
        if res.status_code == 200:
            data = res.json()
            return data.get("extract", "") or ""
    except Exception as e:
        logger.warning(f"Wikipedia fetch failed: {e}")
    return ""


def _fetch_news_headlines(topic: str, limit: int = 6) -> List[Dict]:
    try:
        q = requests.utils.quote(topic)
        url = f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en"
        res = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=8)
        if res.status_code == 200:
            return _parse_news_rss(res.text, limit)
    except Exception as e:
        logger.warning(f"Google News fetch failed: {e}")
    return []


def research_topic(topic: str) -> Dict:
    """Return verified research material for a topic: wiki summary + fresh news."""
    wiki = _fetch_wikipedia_summary(topic)
    news = _fetch_news_headlines(topic)
    lines = []
    if wiki:
        lines.append(f"WIKIPEDIA: {wiki[:900]}")
    if news:
        headlines = "; ".join(n["title"] for n in news[:5])
        lines.append(f"LATEST NEWS HEADLINES: {headlines[:900]}")
    return {
        "wiki": wiki,
        "news": news,
        "facts_text": "\n".join(lines),
    }


# =============================================================================
# 2. YouTube Analytics report — free, uses the same cached OAuth token
# =============================================================================
def fetch_analytics_report(engine, days: int = 7) -> Dict:
    """Channel analytics for the last N days + top videos. Raises on auth issues."""
    from googleapiclient.discovery import build
    from agent_brain import get_history

    creds = engine.get_cached_youtube_credentials()
    if creds is None:
        raise RuntimeError("YouTube sign-in needed — use /upload or /reauth first")

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    yt = build("youtubeAnalytics", "v2", credentials=creds)
    response = yt.reports().query(
        ids="channel==MINE",
        startDate=start.isoformat(),
        endDate=end.isoformat(),
        metrics="views,estimatedMinutesWatched,averageViewDuration,subscribersGained,likes,comments",
        dimensions="video",
        sort="-views",
        maxResults="10",
    ).execute()

    rows = response.get("rows", [])
    cols = response.get("columnHeaders", [])
    if not rows:
        return {"summary": f"📭 No data for the last {days} days (video may be too new).", "videos": []}

    idx = {c["name"]: i for i, c in enumerate(cols)}

    # Resolve video titles (1 quota unit per call)
    video_ids = [r[idx["video"]] for r in rows[:10]]
    titles = {}
    try:
        ytd = build("youtube", "v3", credentials=creds)
        vresp = ytd.videos().list(part="snippet", id=",".join(video_ids)).execute()
        titles = {v["id"]: v["snippet"]["title"][:60] for v in vresp.get("items", [])}
    except Exception as e:
        logger.warning(f"Title fetch failed: {e}")

    total_views = sum(r[idx["views"]] for r in rows)
    total_watch = sum(r[idx["estimatedMinutesWatched"]] for r in rows)
    total_subs = sum(r[idx["subscribersGained"]] for r in rows)

    lines = [
        f"📊 *Last {days} days — Channel Performance*",
        f"👁️ Views: *{total_views:,}*  |  ⏱️ Watch time: *{total_watch:,} min*  |  "
        f"➕ Subs: *{total_subs:+,}*",
        "",
        "*Top videos:*",
    ]
    for r in rows[:10]:
        vid = r[idx["video"]]
        title = titles.get(vid, vid)
        lines.append(
            f"• {title}\n"
            f"   {r[idx['views']]:,} views · {r[idx['estimatedMinutesWatched']]:,} min · "
            f"👍 {r[idx['likes']]:,} · 💬 {r[idx['comments']]:,}"
        )
    lines.append("")
    lines.append("💡 Best performer = future topic direction. /suggest for ideas.")
    return {"summary": "\n".join(lines), "videos": video_ids}


# =============================================================================
# 3. A/B/C Thumbnail variants
# =============================================================================
THUMB_STYLES = [
    ("A", "cinematic dramatic lighting, high contrast, epic mood"),
    ("B", "bright vibrant colors, bold shapes, energetic YouTube style"),
    ("C", "minimalist clean composition, mysterious dark aesthetic, glowing accents"),
]


def generate_thumb_variants(engine, prompt: str, title: str, out_dir: str) -> List[Tuple[str, str]]:
    """Generate 3 differently-styled thumbnails. Returns [(variant, path), ...]."""
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for variant, style in THUMB_STYLES:
        path = os.path.join(out_dir, f"thumb_{variant}.jpg")
        styled_prompt = f"{prompt}. Style: {style}"
        try:
            ok = engine.generate_thumbnail_with_hf(styled_prompt, title, path, "auto")
            if ok and os.path.exists(path):
                results.append((variant, path))
        except Exception as e:
            logger.warning(f"Thumbnail variant {variant} failed: {e}")
    return results


# =============================================================================
# 4. Shorts pipeline — wraps app.create_shorts_trailer
# =============================================================================
def make_shorts(engine, main_video_path: str, out_path: str, topic: str) -> bool:
    from pathlib import Path
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    engine.create_shorts_trailer(main_video_path, out_path, topic)
    return os.path.exists(out_path) and os.path.getsize(out_path) > 0


# =============================================================================
# 5. Trend spike detector — free pytrends, run every few hours
# =============================================================================
def trend_spike_check(keywords: List[str], region: str = "united_states",
                      ratio_threshold: float = 1.8) -> List[Dict]:
    """Return [{'keyword', 'ratio', 'now'}] for keywords spiking in the last hours."""
    clean = [k.strip() for k in keywords if k and k.strip()][:5]
    if not clean:
        return []
    try:
        from pytrends.request import TrendReq
        pt = TrendReq(hl="en-US", tz=360)
        pt.build_payload(clean, timeframe="now 7-d")
        df = pt.interest_over_time()
        if df is None or df.empty:
            return []
        spikes = []
        for kw in clean:
            if kw not in df.columns:
                continue
            series = df[kw].astype(float)
            if len(series) < 24:
                continue
            recent = series.tail(6).mean()          # last ~6 hours
            baseline = series.head(max(1, len(series) - 24)).mean() or 0.001
            ratio = recent / max(baseline, 0.001)
            if ratio >= ratio_threshold and recent > 2:
                spikes.append({"keyword": kw, "ratio": round(ratio, 1), "now": round(recent, 1)})
        return sorted(spikes, key=lambda s: -s["ratio"])
    except Exception as e:
        logger.warning(f"Trend spike check failed: {e}")
        return []


# =============================================================================
# 6. Multi-language localization — audio swap on the last production
# =============================================================================
# edge-tts neural voice per language code (all free)
LANG_VOICE = {
    "en": "en-US-ChristopherNeural",
    "bn": "bn-BD-NabanitaNeural",
    "hi": "hi-IN-SwaraNeural",
    "es": "es-ES-ElviraNeural",
    "fr": "fr-FR-DeniseNeural",
    "de": "de-DE-KatjaNeural",
    "ar": "ar-SA-ZariyahNeural",
    "ja": "ja-JP-NanamiNeural",
    "pt": "pt-BR-FranciscaNeural",
    "ru": "ru-RU-SvetlanaNeural",
}


def swap_video_audio(main_video_path: str, new_audio_path: str, out_path: str) -> bool:
    """Replace the audio track of the rendered 1080p video with a new voiceover.
    Duration = min(video, audio); keeps Full HD visuals."""
    try:
        try:
            from moviepy import VideoFileClip, AudioFileClip  # MoviePy 2.x
            video = VideoFileClip(main_video_path)
            audio = AudioFileClip(new_audio_path)
            dur = min(video.duration, audio.duration)
            final = video.subclipped(0, dur).with_audio(audio.subclipped(0, dur))
            final.write_videofile(out_path, codec="libx264", audio_codec="aac",
                                  bitrate="8000k", logger=None)
        except ImportError:  # MoviePy 1.x
            from moviepy.editor import VideoFileClip, AudioFileClip
            video = VideoFileClip(main_video_path)
            audio = AudioFileClip(new_audio_path)
            dur = min(video.duration, audio.duration)
            final = video.subclip(0, dur).set_audio(audio.subclip(0, dur))
            final.write_videofile(out_path, codec="libx264", audio_codec="aac",
                                  bitrate="8000k", logger=None)
        return os.path.exists(out_path) and os.path.getsize(out_path) > 0
    except Exception as e:
        logger.error(f"Audio swap failed: {e}")
        return False


# =============================================================================
# 8. Engagement booster — top comment reply + stats
#    (YouTube Data API has NO pin endpoint, so we detect + reply instead)
# =============================================================================
def engagement_boost(engine, video_id: str) -> Dict:
    """Find the most-liked fresh comment, reply as the creator, return a report."""
    from googleapiclient.discovery import build
    from agent_brain import get_profile, get_custom_instructions, get_facts

    creds = engine.get_cached_youtube_credentials()
    if creds is None:
        raise RuntimeError("YouTube sign-in needed — use /upload or /reauth first")
    yt = build("youtube", "v3", credentials=creds)

    # Video stats
    stats = {}
    try:
        vresp = yt.videos().list(part="statistics,snippet", id=video_id).execute()
        if vresp.get("items"):
            item = vresp["items"][0]
            st = item.get("statistics", {})
            stats = {
                "title": item["snippet"]["title"],
                "views": int(st.get("viewCount", 0)),
                "likes": int(st.get("likeCount", 0)),
                "comments": int(st.get("commentCount", 0)),
            }
    except Exception as e:
        logger.warning(f"Video stats failed: {e}")

    # Comments sorted by likes
    top = None
    try:
        resp = yt.commentThreads().list(part="snippet", videoId=video_id,
                                        maxResults=100, order="time").execute()
        candidates = []
        for it in resp.get("items", []):
            c = it["snippet"]["topLevelComment"]["snippet"]
            if c.get("authorChannelId", {}).get("value") == getattr(creds, "id_", None):
                continue  # skip own comments
            candidates.append({
                "id": it["snippet"]["topLevelComment"]["id"],
                "author": c.get("authorDisplayName", "?"),
                "text": c.get("textOriginal", ""),
                "likes": int(c.get("likeCount", 0)),
            })
        if candidates:
            top = max(candidates, key=lambda c: c["likes"])
    except Exception as e:
        logger.warning(f"Comment fetch failed: {e}")

    replied = False
    reply_text = ""
    if top:
        profile = get_profile()
        custom = get_custom_instructions()
        facts = "; ".join(get_facts(limit=5))
        prompt = (
            f"You are the creator of a YouTube video titled '{stats.get('title', video_id)}'.\n"
            f"Channel tone: {profile.get('tone', 'friendly')}. Niche: {profile.get('niche', 'general')}.\n"
            + (f"Creator's style notes: {custom}. " if custom else "")
            + (f"Things to remember: {facts}. " if facts else "")
            + f"\nThe most-liked comment ({top['likes']} likes) is from {top['author']}: \"{top['text'][:300]}\"\n"
            "Write ONE warm, personal reply (under 200 chars) that makes this viewer feel special and invites more engagement."
        )
        try:
            reply_text = engine.get_gemini_client().models.generate_content(
                model="gemini-2.5-flash", contents=prompt).text.strip()[:200]
        except Exception as e:
            logger.warning(f"Gemini boost reply failed: {e}")
            try:
                from hf_engine import get_hf_engine
                reply_text = (get_hf_engine().generate_text(prompt, max_new_tokens=80) or "").strip()[:200]
            except Exception as e2:
                logger.warning(f"HF boost reply failed: {e2}")
        if not reply_text:
            reply_text = f"Thank you so much {top['author']}! This made our day 🙏❤️"
        try:
            yt.comments().insert(part="snippet", body={"snippet": {
                "parentId": top["id"], "textOriginal": reply_text}}).execute()
            replied = True
        except Exception as e:
            logger.warning(f"Boost reply post failed: {e}")

    return {"stats": stats, "top_comment": top, "replied": replied, "reply_text": reply_text}


# =============================================================================
# 10. Voice options — edge-tts voice catalogue (free, 400+ voices)
# =============================================================================
def list_edge_voices(lang_prefix: str = "", limit: int = 12) -> List[Dict]:
    """Return edge-tts voices, optionally filtered by locale prefix like 'bn' or 'en-US'."""
    import edge_tts
    loop_holder = {}

    async def _fetch():
        return await edge_tts.list_voices()

    import asyncio
    try:
        asyncio.get_running_loop()
        # Called from async context — use a thread to avoid nested-loop crashes
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as ex:
            voices = ex.submit(asyncio.run, _fetch()).result(timeout=20)
    except RuntimeError:
        voices = asyncio.run(_fetch())

    out = []
    for v in voices:
        locale = v.get("Locale", "")
        if lang_prefix and not locale.lower().startswith(lang_prefix.lower()):
            continue
        out.append({
            "name": v.get("ShortName", ""),
            "locale": locale,
            "gender": v.get("Gender", ""),
            "friendly": v.get("FriendlyName", ""),
        })
        if len(out) >= limit:
            break
    return out


# =============================================================================
# 1. Auto-schedule parsing — "/schedule daily 18:00 upload"
# =============================================================================
def parse_schedule_text(tokens: List[str]) -> Optional[Dict]:
    """
    Parse: ['daily', '18:00', 'upload'] | ['daily', '6pm'] | ['interval', '6h', 'upload'] | ['off']
    Returns {'kind': 'daily'|'interval', 'time_text', 'hour', 'minute', 'interval_hours', 'auto_upload'} or {'kind':'off'}
    """
    if not tokens:
        return {"kind": "list"}
    if tokens[0].lower() in ("off", "stop", "cancel"):
        return {"kind": "off"}

    auto_upload = any(t.lower() in ("upload", "+upload") for t in tokens)
    tokens = [t for t in tokens if t.lower() not in ("upload", "+upload")]

    if not tokens:
        return {"kind": "list"}

    mode = tokens[0].lower()
    if mode in ("daily", "everyday"):
        if len(tokens) < 2:
            return None
        parsed = _parse_time(tokens[1])
        if parsed is None:
            return None
        hour, minute = parsed
        return {"kind": "daily", "time_text": f"{hour:02d}:{minute:02d}",
                "hour": hour, "minute": minute, "auto_upload": auto_upload}
    if mode in ("interval", "every"):
        if len(tokens) < 2:
            return None
        m = re.fullmatch(r"(\d+)\s*h(our)?s?", tokens[1].lower())
        if not m:
            return None
        hours = int(m.group(1))
        if not (1 <= hours <= 48):
            return None
        return {"kind": "interval", "time_text": f"{hours}h",
                "interval_hours": hours, "auto_upload": auto_upload}
    return None


def _parse_time(text: str) -> Optional[Tuple[int, int]]:
    """'18:00' | '6pm' | '6:30pm' | '6am' -> (hour, minute)"""
    text = text.strip().lower()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", text)
    if m:
        h, mi = int(m.group(1)), int(m.group(2))
        if 0 <= h <= 23 and 0 <= mi <= 59:
            return h, mi
        return None
    m = re.fullmatch(r"(\d{1,2})(?::(\d{2}))?\s*(am|pm)", text)
    if m:
        h = int(m.group(1)) % 12
        mi = int(m.group(2) or 0)
        if m.group(3) == "pm":
            h += 12
        return h, mi
    return None

# =============================================================================
# 5. SEO Scorer — heuristic scoring (free, instant) + optional AI rewrite tips
# =============================================================================
POWER_WORDS = ("secret", "proven", "ultimate", "insane", "shocking", "truth",
               "finally", "exposed", "mistakes", "nobody", "best", "why", "how",
               "hidden", "banned", "crazy")


def seo_score(engine, title: str, description: str, tags) -> Dict:
    """Score YouTube metadata 0-100 with actionable checks + AI improved title/tips."""
    from agent_brain import _extract_json

    title = (title or "").strip()
    description = (description or "").strip()
    if isinstance(tags, str):
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
    else:
        tag_list = [str(t) for t in (tags or [])]

    checks = []

    def add(name, ok, points, tip):
        checks.append({"check": name, "ok": bool(ok), "points": points if ok else 0,
                       "max": points, "tip": tip})

    # --- Title (35 pts) ---
    tlen = len(title)
    add("Title length 40-70 chars", 40 <= tlen <= 70, 12,
        f"Title is {tlen} chars — 40-70 displays fully on mobile search")
    add("Number or brackets in title", bool(re.search(r"\d", title)) or ("[" in title or "(" in title), 8,
        "Numbers/brackets boost CTR: '7 Ways...' or '[2026]'")
    add("Power word in title", any(w in title.lower() for w in POWER_WORDS), 8,
        "Add a curiosity word: secret, nobody, finally, exposed...")
    title_alpha = sum(1 for c in title if c.isalpha()) or 1
    add("No shouty ALL-CAPS", sum(1 for c in title if c.isupper()) / title_alpha < 0.4, 7,
        "Excessive caps looks spammy")

    # --- Description (35 pts) ---
    dlen = len(description)
    add("Description 300+ chars", dlen >= 300, 12, f"Description is {dlen} chars — aim for 300+")
    add("First 125 chars front-load keywords", len(description[:125].split()) >= 15, 8,
        "First 125 chars show in search results — put main keywords there")
    add("Chapter timestamps (0:00)", bool(re.search(r"\b\d{1,2}:\d{2}\b", description)), 8,
        "Add '0:00 Intro' style timestamps — enables Chapters + boosts watch time")
    add("Hashtags present", "#" in description, 7, "Add 2-3 #hashtags at the end")

    # --- Tags (20 pts) ---
    add("10-20 tags", 10 <= len(tag_list) <= 20, 10,
        f"You have {len(tag_list)} tags — 10-20 is the sweet spot")
    add("Mix of short + long-tail tags",
        any(len(t.split()) >= 2 for t in tag_list) and any(len(t.split()) == 1 for t in tag_list), 10,
        "Mix single-word and multi-word (long-tail) tags")

    # --- Consistency (10 pts) ---
    if tag_list:
        kw = tag_list[0].lower()
        add("Main tag in title AND description", kw in title.lower() and kw in description.lower(), 10,
            f"Your main tag '{tag_list[0]}' should appear in both title and description")
    else:
        add("Has tags at all", False, 10, "Add tags!")

    score = sum(c["points"] for c in checks)
    grade = "A" if score >= 80 else "B" if score >= 65 else "C" if score >= 50 else "D"

    # --- AI improvement suggestions (best effort) ---
    ai = {}
    try:
        prompt = (
            "You are a YouTube SEO expert. Improve this metadata.\n"
            f"Title: {title}\nDescription: {description[:500]}\nTags: {', '.join(tag_list)}\n\n"
            'Reply ONLY with JSON: {"improved_title": "...", "improved_tags": ["tag1", "tag2"], '
            '"tips": ["tip1", "tip2", "tip3"]}'
        )
        data = None
        try:
            resp = engine.get_gemini_client().models.generate_content(model="gemini-2.5-flash", contents=prompt)
            data = _extract_json(resp.text)
        except Exception:
            pass
        if not isinstance(data, dict):
            try:
                from hf_engine import get_hf_engine
                data = _extract_json(get_hf_engine().generate_text(prompt, max_new_tokens=400))
            except Exception:
                data = None
        if isinstance(data, dict):
            ai = {
                "improved_title": str(data.get("improved_title", ""))[:120],
                "improved_tags": [str(t) for t in (data.get("improved_tags") or [])][:15],
                "tips": [str(t) for t in (data.get("tips") or [])][:3],
            }
    except Exception as e:
        logger.warning(f"SEO AI suggestions unavailable: {e}")

    return {"score": score, "grade": grade, "checks": checks, "ai": ai}


# =============================================================================
# 6. Pre-upload Safety Check — policy/copyright/demonetization audit
# =============================================================================
RISK_KEYWORDS = ("kill", "murder", "drug", "weapon", "gun", "nude", "porn", "sex",
                 "gambling", "casino", "hate", "terror", "bomb", "scam", "free money",
                 "get rich quick", "copyright", "cracked", "pirated")


def safety_check(engine, title: str, text: str) -> Dict:
    """Audit a video (title + script/description) for upload risks BEFORE publishing."""
    from agent_brain import _extract_json

    combined = f"{title}\n{text or ''}"[:3000].lower()
    flagged = [w for w in RISK_KEYWORDS if w in combined]

    prompt = (
        "Audit this YouTube video BEFORE upload for: policy violations, copyright risks "
        "(music/footage/brands), demonetization/advertiser-unfriendly content, misinformation.\n"
        f"Title: {title}\nContent: {(text or '')[:1200]}\n\n"
        'Reply ONLY with JSON: {"risk_level": "low|medium|high", "issues": ["..."], '
        '"fixes": ["actionable fix", ...]}'
    )
    data = None
    try:
        resp = engine.get_gemini_client().models.generate_content(model="gemini-2.5-flash", contents=prompt)
        data = _extract_json(resp.text)
    except Exception as e:
        logger.warning(f"Gemini safety audit failed: {e}")
    if not isinstance(data, dict):
        try:
            from hf_engine import get_hf_engine
            data = _extract_json(get_hf_engine().generate_text(prompt, max_new_tokens=400))
        except Exception:
            data = None

    if isinstance(data, dict) and data.get("risk_level") in ("low", "medium", "high"):
        data["keyword_flags"] = flagged
        return data

    # Offline heuristic fallback
    level = "high" if len(flagged) >= 2 else "medium" if flagged else "low"
    return {
        "risk_level": level,
        "issues": [f"Flagged keyword: '{w}'" for w in flagged] or ["No obvious risk keywords found (offline check only)"],
        "fixes": [] if not flagged else ["Remove/replace flagged terms, or reword the script"],
        "keyword_flags": flagged,
    }


# =============================================================================
# 7. Best Time to Post — YouTube Analytics per-hour views (free)
# =============================================================================
def best_time_to_post(engine, days: int = 90) -> Dict:
    """Find when the channel's viewers actually watch — per-hour view histogram."""
    from googleapiclient.discovery import build

    creds = engine.get_cached_youtube_credentials()
    if creds is None:
        raise RuntimeError("YouTube sign-in needed — use /reauth first")

    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days)
    yta = build("youtubeAnalytics", "v2", credentials=creds)
    resp = yta.reports().query(
        ids="channel==MINE", startDate=start.isoformat(), endDate=end.isoformat(),
        metrics="views", dimensions="hour", sort="hour",
    ).execute()

    buckets = {}
    for row in resp.get("rows", []):
        try:
            raw = str(row[0])
            # hour values can be 'HH' or full timestamps like '2026-09-23T14:00:00Z' / '2026092314'
            if "T" in raw:
                hour = int(raw.split("T")[1][:2])
            elif len(raw) > 2 and raw.isdigit():
                hour = int(raw[-2:])
            else:
                hour = int(raw)
            buckets[hour] = buckets.get(hour, 0) + int(row[1])
        except Exception:
            continue

    if not buckets:
        return {"summary": f"📭 Not enough analytics data for the last {days} days yet. Try again after more views.", "best_hour": None, "buckets": {}}

    peak = max(buckets.values())
    best_hour = max(buckets, key=buckets.get)
    lines = [f"📅 *Best time to post* (viewer activity, last {days} days)", ""]
    for h, v in sorted(buckets.items(), key=lambda x: -x[1])[:6]:
        bar = "▇" * max(1, int(v / peak * 12))
        lines.append(f"`{h:02d}:00` {bar} {v:,}")
    lines.append(f"\n🏆 Your audience peaks at *{best_hour:02d}:00 UTC*")
    lines.append(f"Suggested: `/schedule daily {best_hour:02d}:00 upload`")
    return {"summary": "\n".join(lines), "best_hour": best_hour, "buckets": buckets}


# =============================================================================
# 10. Breaking-News detector — keyless Google News RSS (free)
# =============================================================================
def check_breaking_news(keywords: List[str], seen_titles: List[str], hours: float = 12.0,
                        per_keyword: int = 5) -> List[Dict]:
    """Fresh news (last `hours`) for the given keywords, excluding already-seen titles."""
    from email.utils import parsedate_to_datetime

    fresh = []
    seen = set(seen_titles or [])
    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)

    for kw in [k.strip() for k in keywords if k and k.strip()][:3]:
        for item in _fetch_news_headlines(kw, limit=per_keyword):
            title = item["title"]
            if title in seen:
                continue
            pub_dt = None
            try:
                pub_dt = parsedate_to_datetime(item.get("date", ""))
                if pub_dt is not None and pub_dt.tzinfo is None:
                    pub_dt = pub_dt.replace(tzinfo=timezone.utc)
            except Exception:
                pub_dt = None
            if pub_dt is not None and pub_dt < cutoff:
                continue
            fresh.append({"keyword": kw, "title": title, "date": item.get("date", ""),
                          "fresh": pub_dt is not None})
    return fresh[:5]
