"""
AuraStream 2.0 - Fixed Version
Fully-automated YouTube AI Agent

Fixes applied:
- MoviePy 2.x API compatibility (removed moviepy.editor, fixed subclip->subclipped, set_audio->with_audio, etc.)
- Added python-dotenv loading
- Robust Gemini client initialization with API key handling
- Robust JSON extraction for Gemini response
- Fixed TextClip API (font_size, color, stroke handling) with Linux font fallback
- Fixed YouTube comment reply parentId bug
- Implemented real Streamlit pipeline instead of fake progress
- Fixed os.sys hack, added proper sys import
- Improved error handling for Pexels/Pixabay, thumbnail, telegram, trends
- Added sync wrapper for async TTS
- Added timeouts, temp file cleanup, logging
"""

import os
import json
import re
import sys
import asyncio
import logging
import random
import time
import urllib.parse
from pathlib import Path
from typing import List, Dict

import requests

# --- Dotenv ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    # dotenv not installed, will rely on system env
    pass

# --- External Libraries ---
from google import genai
from google.genai import types
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow

import edge_tts

# MoviePy 2.x compatibility: new import path is `moviepy`, old is `moviepy.editor`
try:
    # MoviePy >=2.0
    from moviepy import (
        VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip,
        CompositeAudioClip, concatenate_videoclips
    )
    from moviepy import vfx
    MOVIEPY_V2 = True
except ImportError:
    # Fallback MoviePy 1.x
    from moviepy.editor import (
        VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip,
        CompositeAudioClip, concatenate_videoclips
    )
    MOVIEPY_V2 = False
    vfx = None

import whisper_timestamped as whisper
from PIL import Image, ImageDraw, ImageFont
from pytrends.request import TrendReq
import streamlit as st

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Env keys
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

TEMP_DIR = Path("temp_assets")
TEMP_DIR.mkdir(exist_ok=True)

# ==============================================================================
# Helpers
# ==============================================================================
def get_gemini_client() -> genai.Client:
    """Initialize Gemini client with API key from env."""
    if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
        raise ValueError("GEMINI_API_KEY is missing. Set it in .env file.")
    return genai.Client(api_key=GEMINI_API_KEY)

def extract_json_from_text(text: str) -> dict:
    """Robustly extract JSON from Gemini response that may contain markdown fences."""
    # Remove markdown code fences
    cleaned = text.strip()
    # Pattern to extract JSON block inside ```json ... ``` or ``` ... ```
    # Try to find first { and last }
    if "```" in cleaned:
        # Remove all ``` markers
        cleaned = re.sub(r"```(?:json)?", "", cleaned)
        cleaned = cleaned.replace("```", "").strip()

    # Fallback: find JSON object boundaries
    first_brace = cleaned.find("{")
    last_brace = cleaned.rfind("}")
    if first_brace != -1 and last_brace != -1:
        cleaned = cleaned[first_brace:last_brace+1]

    try:
        return json.loads(cleaned)
    except json.JSONDecodeError as e:
        logger.error(f"JSON decode failed: {e}\nRaw: {text[:500]}")
        return {}

def get_font_path(size: int = 90):
    """Get a usable bold font path with fallback for Linux/Windows/Mac."""
    candidates = [
        "arialbd.ttf",  # Windows
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",  # Linux
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",  # Mac
        "DejaVuSans-Bold.ttf",
    ]
    for candidate in candidates:
        try:
            # Check if file exists or PIL can load it
            if os.path.exists(candidate) or candidate == "arialbd.ttf":
                ImageFont.truetype(candidate, size)
                return candidate
        except Exception:
            continue
    return None  # Will use default

# ==============================================================================
# 1. Gemini Script & Metadata Prompt
# ==============================================================================
def generate_long_script(topic: str, duration_mins: int) -> dict:
    """Generate documentary script and metadata using Gemini."""
    client = get_gemini_client()
    target_words = duration_mins * 150

    prompt = f"""
    Write a full documentary script about "{topic}".
    Target word count should be around {target_words} words.
    Enforce strict JSON output with these exact keys:
    - title: Catchy YouTube title under 100 characters.
    - description: SEO-friendly summary with timestamps/chapters.
    - tags: Comma-separated tags string.
    - hashtags: Top 5 relevant YouTube hashtags.
    - chapter1 to chapter5: Divided parts of the full text script.
    - visual_keywords: A python list of 20 single-word English terms for stock footage search.
    - thumbnail_prompt: Detailed visual prompt for AI image generators.
    """

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
            ),
        )
        return extract_json_from_text(response.text)
    except Exception as e:
        logger.error(f"Gemini generation failed: {e}")
        return {}

# ==============================================================================
# 2. Voiceover & Audio Engine Prompt
# ==============================================================================
async def generate_voiceover_async(text: str, output_path: str):
    """Async TTS using edge-tts with pause handling."""
    # Parse by periods, join with explicit pauses for more natural speech
    sentences = [s.strip() for s in text.split('.') if s.strip()]
    if not sentences:
        raise ValueError("No sentences to synthesize")

    processed_text = "... ".join(sentences) + "."
    communicate = edge_tts.Communicate(processed_text, "en-US-ChristopherNeural")
    await communicate.save(output_path)
    logger.info(f"Voiceover saved to {output_path}")

def generate_voiceover_sync(text: str, output_path: str):
    """Sync wrapper for Streamlit and non-async contexts."""
    try:
        # If there's already a running loop (unlikely in Streamlit), create new
        asyncio.run(generate_voiceover_async(text, output_path))
    except RuntimeError:
        # Fallback for environments with existing loop
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(generate_voiceover_async(text, output_path))
        loop.close()

# ==============================================================================
# 3. Dual Fallback Video Downloader Prompt
# ==============================================================================
def download_bulk_videos(keywords: list) -> list:
    """Download stock footage with Pexels primary and Pixabay fallback."""
    if not keywords:
        logger.warning("No keywords provided for video download")
        return []

    if not PEXELS_API_KEY and not PIXABAY_API_KEY:
        logger.warning("Both PEXELS and PIXABAY API keys missing, cannot download videos")
        return []

    downloaded_paths = []

    for i, keyword in enumerate(keywords):
        keyword = keyword.strip()
        if not keyword:
            continue

        encoded_keyword = urllib.parse.quote(keyword)
        output_path = str(TEMP_DIR / f"clip_{i}.mp4")
        success = False

        # Try Pexels API
        if PEXELS_API_KEY and PEXELS_API_KEY != "your_pexels_api_key_here":
            try:
                headers = {"Authorization": PEXELS_API_KEY}
                url = f"https://api.pexels.com/videos/search?query={encoded_keyword}&per_page=1&size=large"
                res = requests.get(url, headers=headers, timeout=10)
                res.raise_for_status()
                data = res.json()

                if data.get("videos"):
                    video_files = data["videos"][0].get("video_files", [])
                    if video_files:
                        # Prefer 1080p, else highest
                        hd_file = next((f for f in video_files if f.get("height") == 1080), None)
                        if not hd_file:
                            # Sort by height descending
                            hd_file = sorted(video_files, key=lambda x: x.get("height", 0), reverse=True)[0]
                        video_url = hd_file.get("link")
                        if video_url:
                            vid_res = requests.get(video_url, stream=True, timeout=20)
                            vid_res.raise_for_status()
                            with open(output_path, 'wb') as f:
                                for chunk in vid_res.iter_content(chunk_size=8192):
                                    if chunk:
                                        f.write(chunk)
                            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                                downloaded_paths.append(output_path)
                                success = True
                                logger.info(f"Pexels success for '{keyword}' -> {output_path}")
            except Exception as e:
                logger.warning(f"Pexels failed for '{keyword}': {e}")

        # Fallback to Pixabay API
        if not success and PIXABAY_API_KEY and PIXABAY_API_KEY != "your_pixabay_api_key_here":
            try:
                pixabay_url = f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}&q={encoded_keyword}&video_type=film&per_page=3"
                res = requests.get(pixabay_url, timeout=10)
                res.raise_for_status()
                data = res.json()

                if data.get("hits"):
                    # Try first hit with large video
                    for hit in data["hits"]:
                        videos = hit.get("videos", {})
                        large = videos.get("large", {}) or videos.get("medium", {}) or videos.get("small", {})
                        video_url = large.get("url")
                        if video_url:
                            vid_res = requests.get(video_url, stream=True, timeout=20)
                            vid_res.raise_for_status()
                            with open(output_path, 'wb') as f:
                                for chunk in vid_res.iter_content(chunk_size=8192):
                                    if chunk:
                                        f.write(chunk)
                            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                                downloaded_paths.append(output_path)
                                success = True
                                logger.info(f"Pixabay success for '{keyword}' -> {output_path}")
                                break
            except Exception as e:
                logger.warning(f"Pixabay fallback failed for '{keyword}': {e}")

    return downloaded_paths

# ==============================================================================
# 4. MoviePy Video & Subtitle Assembler Prompt
# ==============================================================================
def create_mega_production(clips_paths: list, audio_path: str, title: str, output_path: str):
    """Assemble final video with voiceover and word-level subtitles (MoviePy 2.x compatible)."""
    if not clips_paths:
        raise ValueError("No video clips provided")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    audio = AudioFileClip(audio_path)
    audio_duration = audio.duration

    clips = []
    current_duration = 0
    for path in clips_paths:
        try:
            if not os.path.exists(path):
                continue
            clip = VideoFileClip(path)
            # Ensure clip has no audio to avoid mixing
            if MOVIEPY_V2:
                clip = clip.without_audio()
            else:
                clip = clip.without_audio()
            clips.append(clip)
            current_duration += clip.duration
            if current_duration >= audio_duration:
                break
        except Exception as e:
            logger.warning(f"Error loading clip {path}: {e}")

    if not clips:
        raise ValueError("No valid video clips could be loaded")

    # Concatenate and trim to audio duration
    final_video = concatenate_videoclips(clips)
    if MOVIEPY_V2:
        final_video = final_video.subclipped(0, audio_duration)
        final_video = final_video.with_audio(audio)
    else:
        final_video = final_video.subclip(0, audio_duration)
        final_video = final_video.set_audio(audio)

    # Transcribe for subtitles - write temp wav
    temp_wav = str(TEMP_DIR / "temp_subtitle.wav")
    try:
        audio.write_audiofile(temp_wav, logger=None)
    except Exception as e:
        logger.error(f"Failed to write temp wav: {e}")
        # Fallback: use original audio path if it's wav, else skip subtitles
        temp_wav = audio_path if audio_path.endswith(".wav") else None

    subtitle_clips = []
    if temp_wav and os.path.exists(temp_wav):
        try:
            model = whisper.load_model("base")
            results = whisper.transcribe(model, temp_wav)

            colors = ['yellow', 'cyan', 'white', 'green']
            font_path = get_font_path(75)

            for segment in results.get("segments", []):
                for word_info in segment.get("words", []):
                    word = word_info.get("text", "").strip()
                    if not word:
                        continue
                    start = word_info.get("start", 0)
                    end = word_info.get("end", start + 0.5)
                    duration = max(0.1, end - start)

                    # Create TextClip with new API
                    try:
                        if MOVIEPY_V2:
                            txt_clip = TextClip(
                                text=word,
                                font_size=75,
                                color=random.choice(colors),
                                font=font_path,
                                stroke_color='black',
                                stroke_width=3,
                                method='label'
                            )
                            txt_clip = txt_clip.with_position(('center', 'bottom')).with_start(start).with_duration(duration)
                        else:
                            # MoviePy 1.x fallback
                            txt_clip = TextClip(
                                word,
                                fontsize=75,
                                color=random.choice(colors),
                                font=font_path or 'Arial-Bold',
                                stroke_color='black',
                                stroke_width=3
                            )
                            txt_clip = txt_clip.set_position(('center', 'bottom')).set_start(start).set_duration(duration)
                        subtitle_clips.append(txt_clip)
                    except Exception as e:
                        logger.warning(f"Failed to create subtitle for word '{word}': {e}")
                        continue
        except Exception as e:
            logger.error(f"Whisper transcription failed: {e}")

    # Composite
    if subtitle_clips:
        if MOVIEPY_V2:
            mega_production = CompositeVideoClip([final_video] + subtitle_clips)
        else:
            mega_production = CompositeVideoClip([final_video] + subtitle_clips)
    else:
        mega_production = final_video

    # Write final video
    try:
        mega_production.write_videofile(
            output_path,
            codec="libx264",
            fps=30,
            audio_codec="aac",
            logger=None
        )
    finally:
        # Cleanup
        try:
            audio.close()
            final_video.close()
            for c in clips:
                c.close()
            for sc in subtitle_clips:
                sc.close()
            mega_production.close()
        except Exception:
            pass

    logger.info(f"Mega production saved to {output_path}")

# ==============================================================================
# 5. 9:16 Shorts Creator Prompt
# ==============================================================================
def create_shorts_trailer(main_video_path: str, shorts_output_path: str, topic_keyword: str):
    """Create vertical 9:16 shorts trailer from main video (MoviePy 2.x compatible)."""
    if not os.path.exists(main_video_path):
        raise FileNotFoundError(f"Main video not found: {main_video_path}")

    main_clip = VideoFileClip(main_video_path)
    h = main_clip.h
    w = main_clip.w
    new_w = int(h * (9/16))
    x_center = w / 2

    subclips = []
    clip_dur = min(15, main_clip.duration / 4) if main_clip.duration > 0 else 15

    # Pick 4 distinct subclips with fade transitions
    for i in range(4):
        start_t = i * (main_clip.duration / 4)
        end_t = min(start_t + clip_dur, main_clip.duration)
        if end_t <= start_t:
            continue

        if MOVIEPY_V2:
            subclip = main_clip.subclipped(start_t, end_t)
            # Apply fade effects using vfx
            if vfx:
                try:
                    subclip = subclip.with_effects([vfx.FadeIn(1), vfx.FadeOut(1)])
                except Exception as e:
                    logger.warning(f"Fade effect failed: {e}")
        else:
            subclip = main_clip.subclip(start_t, end_t)
            try:
                subclip = subclip.fadein(1).fadeout(1)
            except Exception:
                pass
        subclips.append(subclip)

    if not subclips:
        main_clip.close()
        raise ValueError("Could not create subclips for shorts")

    trailer = concatenate_videoclips(subclips)

    # Crop to 9:16 vertical
    try:
        if MOVIEPY_V2:
            trailer = trailer.cropped(
                x1=max(0, x_center - new_w/2),
                y1=0,
                x2=min(w, x_center + new_w/2),
                y2=h
            ).resized((1080, 1920))
        else:
            trailer = trailer.crop(
                x1=max(0, x_center - new_w/2),
                y1=0,
                x2=min(w, x_center + new_w/2),
                y2=h
            ).resize((1080, 1920))
    except Exception as e:
        logger.error(f"Crop/resize failed: {e}")
        # Fallback: just resize
        try:
            if MOVIEPY_V2:
                trailer = trailer.resized((1080, 1920))
            else:
                trailer = trailer.resize((1080, 1920))
        except Exception:
            pass

    # Fetch background music from Pixabay if key available
    if PIXABAY_API_KEY and PIXABAY_API_KEY != "your_pixabay_api_key_here":
        try:
            encoded_keyword = urllib.parse.quote(topic_keyword)
            pixabay_url = f"https://pixabay.com/api/audio/?key={PIXABAY_API_KEY}&q={encoded_keyword}&per_page=3"
            res = requests.get(pixabay_url, timeout=10)
            res.raise_for_status()
            data = res.json()

            if data.get("hits"):
                bgm_url = data["hits"][0].get("audio") or data["hits"][0].get("previewURL")
                if bgm_url:
                    bgm_path = str(TEMP_DIR / "temp_bgm.mp3")
                    bgm_res = requests.get(bgm_url, timeout=15)
                    bgm_res.raise_for_status()
                    with open(bgm_path, "wb") as f:
                        f.write(bgm_res.content)

                    if os.path.exists(bgm_path):
                        bgm_clip = AudioFileClip(bgm_path).subclipped(0, trailer.duration) if MOVIEPY_V2 else AudioFileClip(bgm_path).subclip(0, trailer.duration)
                        if MOVIEPY_V2:
                            bgm_clip = bgm_clip.with_volume_scaled(0.2)
                        else:
                            bgm_clip = bgm_clip.volumex(0.2)

                        # Mix with original audio if exists
                        if trailer.audio is not None:
                            if MOVIEPY_V2:
                                final_audio = CompositeAudioClip([trailer.audio, bgm_clip])
                                trailer = trailer.with_audio(final_audio)
                            else:
                                final_audio = CompositeAudioClip([trailer.audio, bgm_clip])
                                trailer = trailer.set_audio(final_audio)
                        else:
                            if MOVIEPY_V2:
                                trailer = trailer.with_audio(bgm_clip)
                            else:
                                trailer = trailer.set_audio(bgm_clip)
        except Exception as e:
            logger.warning(f"BGM fetch/mix failed: {e}")

    try:
        trailer.write_videofile(shorts_output_path, codec="libx264", fps=30, logger=None)
    finally:
        try:
            main_clip.close()
            trailer.close()
            for sc in subclips:
                sc.close()
        except Exception:
            pass

    logger.info(f"Shorts trailer saved to {shorts_output_path}")

# ==============================================================================
# 6. AI Thumbnail & Bold Text Overlay Prompt
# ==============================================================================
def generate_thumbnail_with_text(prompt: str, title_text: str, output_path: str):
    """Generate thumbnail via Pollinations AI and overlay bold text."""
    if not prompt:
        prompt = "Epic cinematic documentary thumbnail"

    encoded_prompt = urllib.parse.quote(prompt[:500])  # Limit length
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&nologo=true"

    try:
        res = requests.get(url, timeout=30)
        res.raise_for_status()
        img_path = str(TEMP_DIR / "temp_raw_thumb.jpg")
        with open(img_path, 'wb') as f:
            f.write(res.content)
    except Exception as e:
        logger.error(f"Thumbnail download failed: {e}")
        # Create blank image as fallback
        img = Image.new('RGB', (1280, 720), color=(30, 30, 30))
        img.save(output_path)
        return

    try:
        img = Image.open(img_path).convert("RGB")
    except Exception as e:
        logger.error(f"Failed to open thumbnail image: {e}")
        img = Image.new('RGB', (1280, 720), color=(30, 30, 30))

    draw = ImageDraw.Draw(img)

    words = title_text.split()[:4]
    short_title = " ".join(words).upper() if words else "WATCH NOW"

    font_path = get_font_path(90)
    try:
        if font_path:
            font = ImageFont.truetype(font_path, 90)
        else:
            font = ImageFont.load_default()
    except Exception:
        font = ImageFont.load_default()

    text_x, text_y = 50, 550
    outline_color = "black"
    fill_color = "yellow"

    # Draw outline/shadow for bold effect
    for offset_x in [-3, -2, -1, 0, 1, 2, 3]:
        for offset_y in [-3, -2, -1, 0, 1, 2, 3]:
            if offset_x == 0 and offset_y == 0:
                continue
            draw.text((text_x + offset_x, text_y + offset_y), short_title, font=font, fill=outline_color)

    # Main text
    draw.text((text_x, text_y), short_title, font=font, fill=fill_color)

    try:
        img.save(output_path, quality=95)
        logger.info(f"Thumbnail saved to {output_path}")
    except Exception as e:
        logger.error(f"Failed to save thumbnail: {e}")

# ==============================================================================
# 7. YouTube Upload & Comment Automation Prompt
# ==============================================================================
YOUTUBE_SCOPES = [
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.force-ssl"
]

def upload_video_to_youtube(video_path: str, thumb_path: str, meta_data: dict) -> str:
    """Upload video to YouTube with thumbnail and pinned comment."""
    if not os.path.exists(video_path):
        raise FileNotFoundError(f"Video file not found: {video_path}")
    if not os.path.exists("client_secrets.json"):
        raise FileNotFoundError("client_secrets.json not found. Download from Google Cloud Console.")

    flow = InstalledAppFlow.from_client_secrets_file('client_secrets.json', YOUTUBE_SCOPES)
    credentials = flow.run_local_server(port=0)
    youtube = build('youtube', 'v3', credentials=credentials)

    # Clean tags
    tags_raw = meta_data.get('tags', '')
    if isinstance(tags_raw, str):
        tags = [t.strip() for t in tags_raw.split(',') if t.strip()][:20]  # YouTube max 500 chars, ~20 tags safe
    else:
        tags = []

    body = {
        'snippet': {
            'title': meta_data.get('title', 'Default Title')[:100],
            'description': meta_data.get('description', 'Default Description')[:5000],
            'tags': tags,
            'categoryId': '27'  # Education
        },
        'status': {
            'privacyStatus': 'public'
        }
    }

    media = MediaFileUpload(video_path, chunksize=-1, resumable=True)
    request = youtube.videos().insert(
        part=','.join(body.keys()),
        body=body,
        media_body=media
    )
    response = request.execute()
    video_id = response.get('id')

    if not video_id:
        raise RuntimeError("YouTube upload failed, no video ID returned")

    # Set custom thumbnail
    if thumb_path and os.path.exists(thumb_path):
        try:
            youtube.thumbnails().set(
                videoId=video_id,
                media_body=MediaFileUpload(thumb_path)
            ).execute()
        except Exception as e:
            logger.warning(f"Thumbnail upload failed: {e}")

    # Post engaging pinned comment
    try:
        comment_body = {
            'snippet': {
                'videoId': video_id,
                'topLevelComment': {
                    'snippet': {
                        'textOriginal': 'What did you think of the video? Let us know below! 👇'
                    }
                }
            }
        }
        youtube.commentThreads().insert(
            part='snippet',
            body=comment_body
        ).execute()
    except Exception as e:
        logger.warning(f"Pinned comment failed: {e}")

    return video_id

# ==============================================================================
# 8. Telegram Bot Alert System Prompt
# ==============================================================================
def send_telegram_message(message: str):
    """Send notification via Telegram Bot."""
    token = TELEGRAM_BOT_TOKEN
    chat_id = TELEGRAM_CHAT_ID
    if not token or not chat_id or token == "your_telegram_bot_token_here":
        logger.info("Telegram credentials not set, skipping notification")
        return

    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        resp = requests.post(url, json=payload, timeout=10)
        resp.raise_for_status()
    except requests.exceptions.RequestException as e:
        logger.warning(f"Telegram notification failed: {e}")

# ==============================================================================
# 9. Automated Scraper & Translation Prompt
# ==============================================================================
def fetch_trending_topic(region='united_states') -> str:
    """Fetch trending topic via pytrends with robust DataFrame handling."""
    try:
        pytrends = TrendReq(hl='en-US', tz=360)
        trending_df = pytrends.trending_searches(pn=region)
        # trending_searches returns DataFrame with one column containing topics
        if trending_df is not None and not trending_df.empty:
            # Try iloc first
            try:
                topic = trending_df.iloc[0, 0]
                if isinstance(topic, str) and topic.strip():
                    return topic.strip()
            except Exception:
                pass
            # Fallback: first column first row
            try:
                topic = trending_df[0][0]
                if isinstance(topic, str) and topic.strip():
                    return topic.strip()
            except Exception:
                pass
        return "Artificial Intelligence"
    except Exception as e:
        logger.warning(f"Trends fetch error: {e}")
        return "Artificial Intelligence"

def translate_script(text: str, target_lang: str) -> str:
    """Translate script using Gemini."""
    if not text:
        return ""
    if target_lang.lower() == "english":
        return text

    client = get_gemini_client()
    prompt = f"Translate the following documentary script into {target_lang} while maintaining a natural, engaging tone:\n\n{text}"

    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return response.text.strip() if response.text else text
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return text

# ==============================================================================
# 10. Streamlit Web Dashboard Prompt - FIXED TO RUN REAL PIPELINE
# ==============================================================================
def run_streamlit_dashboard():
    st.set_page_config(page_title="AuraStream 2.0", page_icon="🎥", layout="wide")
    st.title("🎥 AuraStream 2.0 Dashboard")
    st.caption("Fully-automated YouTube AI Agent - Fixed & Production Ready")

    # Sidebar for API status
    with st.sidebar:
        st.header("🔑 API Status")
        st.write(f"Gemini: {'✅' if GEMINI_API_KEY and GEMINI_API_KEY != 'your_gemini_api_key_here' else '❌ Missing'}")
        st.write(f"Pexels: {'✅' if PEXELS_API_KEY and PEXELS_API_KEY != 'your_pexels_api_key_here' else '❌ Missing'}")
        st.write(f"Pixabay: {'✅' if PIXABAY_API_KEY and PIXABAY_API_KEY != 'your_pixabay_api_key_here' else '❌ Missing'}")
        st.write(f"Telegram: {'✅' if TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_TOKEN != 'your_telegram_bot_token_here' else '⚠️ Optional'}")
        st.divider()
        st.info("Set API keys in .env file. See .env.example")

    # Main controls
    col1, col2 = st.columns([3, 1])
    with col1:
        auto_fetch = st.toggle("Auto-Fetch Trend", value=True, help="Automatically fetch trending topic from Google Trends")

    topic = ""
    if auto_fetch:
        if st.button("🔄 Fetch Trending Topic"):
            with st.spinner("Fetching trending topic..."):
                topic = fetch_trending_topic()
                st.session_state['topic'] = topic
        # Use session state if available
        topic = st.session_state.get('topic', fetch_trending_topic() if auto_fetch else "")
        st.text_input("Video Topic", value=topic, disabled=True, key="topic_display")
        # Actual editable value for pipeline
        final_topic = topic
    else:
        final_topic = st.text_input("Video Topic", placeholder="Enter your topic...", key="manual_topic")

    duration = st.slider("Video Duration (mins)", min_value=1, max_value=10, value=3)
    language = st.selectbox("Language", ["English", "Bengali", "Hindi", "Spanish"])

    if st.button("🚀 Run Automation Pipeline", type="primary"):
        if not final_topic or not final_topic.strip():
            st.error("Please provide a video topic!")
            return

        if not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here":
            st.error("GEMINI_API_KEY is missing! Please set it in .env file.")
            return

        st.info(f"Pipeline started for topic: **{final_topic}**")
        progress_bar = st.progress(0)
        status_text = st.empty()
        log_area = st.empty()

        try:
            # Step 1: Generate Script
            status_text.text("1/5 Generating Script & Metadata...")
            progress_bar.progress(10)
            script_data = generate_long_script(final_topic, duration)
            if not script_data:
                st.error("Failed to generate script. Check Gemini API key and quota.")
                return
            progress_bar.progress(20)
            st.json(script_data)

            # Combine chapters into full script
            full_script = " ".join([
                script_data.get(f"chapter{i}", "") for i in range(1, 6)
            ]).strip()
            if not full_script:
                full_script = script_data.get("description", final_topic)

            # Translate if needed
            if language != "English":
                status_text.text(f"Translating script to {language}...")
                full_script = translate_script(full_script, language)

            # Step 2: Voiceover
            status_text.text("2/5 Synthesizing Voiceover...")
            progress_bar.progress(30)
            voiceover_path = str(TEMP_DIR / "voiceover.mp3")
            generate_voiceover_sync(full_script, voiceover_path)
            progress_bar.progress(40)
            if os.path.exists(voiceover_path):
                st.audio(voiceover_path)
            else:
                st.warning("Voiceover file not created")

            # Step 3: Download footage & assemble
            status_text.text("3/5 Downloading Footage & Assembling Video...")
            progress_bar.progress(50)
            keywords = script_data.get("visual_keywords", [])
            if isinstance(keywords, str):
                # If Gemini returned string instead of list, parse
                try:
                    keywords = json.loads(keywords)
                except Exception:
                    keywords = [k.strip() for k in keywords.split(",")]

            if not keywords:
                keywords = final_topic.split()[:10]

            clips = download_bulk_videos(keywords[:15])  # Limit to 15 to save time
            if not clips:
                st.warning("No stock footage downloaded, check Pexels/Pixabay keys")
                # Continue with placeholder? For demo, we error
                st.error("Cannot assemble video without footage. Please check API keys.")
                return

            progress_bar.progress(60)
            main_video_path = str(TEMP_DIR / "main_video.mp4")
            create_mega_production(clips, voiceover_path, script_data.get("title", final_topic), main_video_path)
            progress_bar.progress(70)
            if os.path.exists(main_video_path):
                st.video(main_video_path)

            # Step 4: Thumbnail
            status_text.text("4/5 Generating AI Thumbnail...")
            progress_bar.progress(80)
            thumb_path = str(TEMP_DIR / "thumbnail.jpg")
            generate_thumbnail_with_text(
                script_data.get("thumbnail_prompt", final_topic),
                script_data.get("title", final_topic),
                thumb_path
            )
            progress_bar.progress(85)
            if os.path.exists(thumb_path):
                st.image(thumb_path, caption="Generated Thumbnail")

            # Step 5: Upload (optional, only if client_secrets.json exists)
            status_text.text("5/5 Uploading to YouTube...")
            progress_bar.progress(90)

            if os.path.exists("client_secrets.json"):
                try:
                    video_id = upload_video_to_youtube(main_video_path, thumb_path, script_data)
                    progress_bar.progress(100)
                    st.success(f"🎉 Video uploaded successfully! Video ID: {video_id}")
                    st.markdown(f"[Watch Here](https://youtube.com/watch?v={video_id})")
                    send_telegram_message(f"✅ AuraStream: Video '{script_data.get('title')}' uploaded! https://youtube.com/watch?v={video_id}")
                except Exception as e:
                    st.warning(f"YouTube upload failed: {e}. Video saved locally at {main_video_path}")
                    progress_bar.progress(100)
                    st.success("🎉 Video production completed locally!")
            else:
                progress_bar.progress(100)
                st.success("🎉 Video production completed! (Local mode - client_secrets.json not found for YouTube upload)")
                st.info(f"Main video: {main_video_path}\nThumbnail: {thumb_path}")
                send_telegram_message(f"✅ AuraStream: Local production complete for '{final_topic}'")

            # Bonus: Create Shorts
            with st.expander("Create Shorts Trailer (Optional)"):
                if st.button("Generate Shorts"):
                    shorts_path = str(TEMP_DIR / "shorts_trailer.mp4")
                    create_shorts_trailer(main_video_path, shorts_path, final_topic)
                    st.video(shorts_path)
                    st.success(f"Shorts trailer created: {shorts_path}")

        except Exception as e:
            logger.exception("Pipeline failed")
            st.error(f"Pipeline failed: {e}")
            send_telegram_message(f"❌ AuraStream pipeline failed for '{final_topic}': {e}")

# ==============================================================================
# 11. YouTube Comments Auto-Reply Prompt - FIXED parentId bug
# ==============================================================================
def reply_to_youtube_comments(video_id: str):
    """Auto-reply to YouTube comments using Gemini."""
    if not os.path.exists("client_secrets.json"):
        logger.error("client_secrets.json missing for comment reply")
        return

    flow = InstalledAppFlow.from_client_secrets_file('client_secrets.json', YOUTUBE_SCOPES)
    credentials = flow.run_local_server(port=0)
    youtube = build('youtube', 'v3', credentials=credentials)

    try:
        client = get_gemini_client()
    except Exception as e:
        logger.error(f"Gemini client init failed: {e}")
        return

    try:
        request = youtube.commentThreads().list(
            part="snippet,replies",
            videoId=video_id,
            maxResults=50
        )
        response = request.execute()

        for item in response.get("items", []):
            snippet = item.get("snippet", {})
            top_comment = snippet.get("topLevelComment", {})
            top_snippet = top_comment.get("snippet", {})
            # FIXED: Use top-level comment ID as parentId, not thread ID
            top_comment_id = top_comment.get("id")
            thread_id = item.get("id")

            if not top_comment_id:
                continue

            author = top_snippet.get("authorDisplayName", "Unknown")
            text = top_snippet.get("textOriginal", "")

            # Check if channel owner already replied
            # More robust: check replies for authorChannelId or if reply count >0 and owner
            replies = item.get("replies", {}).get("comments", [])
            # Simple heuristic: if no replies, we reply
            # For better check, you could fetch channel ID and compare
            if len(replies) == 0 and text:
                try:
                    prompt = f"Draft a polite, friendly, and engaging response (under 280 characters) to this YouTube comment from {author}:\n\n'{text}'"
                    ai_response = client.models.generate_content(
                        model='gemini-2.5-flash',
                        contents=prompt
                    )
                    reply_text = ai_response.text.strip() if ai_response.text else "Thanks for your comment! 🙏"

                    # FIXED: parentId should be top_comment_id (the comment we're replying to)
                    youtube.comments().insert(
                        part="snippet",
                        body={
                            "snippet": {
                                "parentId": top_comment_id,
                                "textOriginal": reply_text
                            }
                        }
                    ).execute()
                    logger.info(f"Replied to {author}: {reply_text[:50]}")
                    time.sleep(1.5)  # Rate limit handling
                except Exception as e:
                    logger.warning(f"Failed to reply to comment {top_comment_id}: {e}")
                    time.sleep(2)

    except Exception as e:
        logger.error(f"API Quota/Rate Limit Error: {e}")

if __name__ == "__main__":
    # Streamlit sets __name__ == "__main__" when running via `streamlit run`
    # Also check if we're in Streamlit context
    try:
        # This will work when run via Streamlit
        run_streamlit_dashboard()
    except Exception as e:
        # Fallback for direct python execution
        print(f"Running outside Streamlit context: {e}")
        print("Launch with: streamlit run app.py")
