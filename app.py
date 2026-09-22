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

# --- Hugging Face Advanced Engine ---
try:
    from hf_engine import get_hf_engine, HuggingFaceEngine, DEFAULT_MODELS
    HF_AVAILABLE = True
except ImportError as e:
    HF_AVAILABLE = False
    logger.warning(f"Hugging Face engine not available: {e}")
    get_hf_engine = None
    DEFAULT_MODELS = {}

# Logging
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

# Env keys
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "").strip()
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
HUGGINGFACE_API_KEY = os.getenv("HUGGINGFACE_API_KEY", "").strip()
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
# 3. Dual Fallback Video Downloader - 1080p FULL HD ENGINE
# ==============================================================================
# Supported quality modes: "1080p" (Full HD, default), "720p" (HD), "best" (max available)
def _pick_pexels_video_file(video_files: list, quality: str = "1080p"):
    """Select the best Pexels video file for the requested quality (Full HD aware)."""
    files = [f for f in video_files if f.get("height")]
    if not files:
        return None
    target = 720 if quality == "720p" else 1080
    # 1) Exact match (e.g. exactly 1920x1080 Full HD)
    exact = [f for f in files if f.get("height") == target]
    if exact:
        return exact[0]
    # 2) Next resolution ABOVE target (downscaling loses less quality than upscaling)
    higher = [f for f in files if f.get("height", 0) > target]
    if higher:
        return sorted(higher, key=lambda f: f.get("height", 0))[0]
    # 3) Largest available as last resort
    return sorted(files, key=lambda f: f.get("height", 0), reverse=True)[0]


def _pick_pixabay_video_file(videos: dict, quality: str = "1080p"):
    """Select the best Pixabay rendition for the requested quality (Full HD aware)."""
    if quality == "720p":
        chain = ["medium", "large", "small"]
    else:
        # 1080p / best: prefer the large (typically 1920x1080 Full HD) rendition
        chain = ["large", "medium", "small"]
    for size in chain:
        if videos.get(size, {}).get("url"):
            return videos[size]["url"], videos[size]
    return None, None


def download_bulk_videos(keywords: list, quality: str = "1080p") -> list:
    """Download stock footage (1080p Full HD by default) with Pexels primary and Pixabay fallback."""
    if not keywords:
        logger.warning("No keywords provided for video download")
        return []

    if not PEXELS_API_KEY and not PIXABAY_API_KEY:
        logger.warning("Both PEXELS and PIXABAY API keys missing, cannot download videos")
        return []

    logger.info(f"Downloading stock footage at quality mode: {quality.upper()}")

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
                    hd_file = _pick_pexels_video_file(video_files, quality)
                    if hd_file:
                        video_url = hd_file.get("link")
                        resolution = f"{hd_file.get('width', '?')}x{hd_file.get('height', '?')}"
                        if video_url:
                            vid_res = requests.get(video_url, stream=True, timeout=30)
                            vid_res.raise_for_status()
                            with open(output_path, 'wb') as f:
                                for chunk in vid_res.iter_content(chunk_size=8192):
                                    if chunk:
                                        f.write(chunk)
                            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                                downloaded_paths.append(output_path)
                                success = True
                                logger.info(f"Pexels success for '{keyword}' [{resolution}] -> {output_path}")
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
                    for hit in data["hits"]:
                        videos = hit.get("videos", {})
                        video_url, rendition = _pick_pixabay_video_file(videos, quality)
                        if video_url:
                            vid_res = requests.get(video_url, stream=True, timeout=30)
                            vid_res.raise_for_status()
                            with open(output_path, 'wb') as f:
                                for chunk in vid_res.iter_content(chunk_size=8192):
                                    if chunk:
                                        f.write(chunk)
                            if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
                                downloaded_paths.append(output_path)
                                success = True
                                if rendition:
                                    logger.info(f"Pixabay success for '{keyword}' [{rendition.get('width', '?')}x{rendition.get('height', '?')}] -> {output_path}")
                                break
            except Exception as e:
                logger.warning(f"Pixabay fallback failed for '{keyword}': {e}")

    return downloaded_paths

# ==============================================================================
# 4. MoviePy Video & Subtitle Assembler - 1080p FULL HD Prompt
# ==============================================================================
def _fit_clip_to_resolution(clip, target_resolution):
    """Scale + center-crop a clip to exactly fill target_resolution (w, h). MoviePy 1.x & 2.x compatible."""
    tw, th = target_resolution
    try:
        scale = max(tw / max(clip.w, 1), th / max(clip.h, 1))
        if MOVIEPY_V2:
            clip = clip.resized(scale)
        else:
            clip = clip.resize(newsize=(max(1, int(clip.w * scale)), max(1, int(clip.h * scale))))
        x1 = max(0, (clip.w - tw) // 2)
        y1 = max(0, (clip.h - th) // 2)
        if MOVIEPY_V2:
            clip = clip.cropped(x1=x1, y1=y1, width=min(tw, clip.w), height=min(th, clip.h))
        else:
            clip = clip.crop(x1=x1, y1=y1, width=min(tw, clip.w), height=min(th, clip.h))
    except Exception as e:
        logger.warning(f"Could not fit clip to {target_resolution}: {e}")
    return clip


def create_mega_production(clips_paths: list, audio_path: str, title: str, output_path: str, target_resolution=(1920, 1080)):
    """Assemble final 1080p Full HD video with voiceover and word-level subtitles (MoviePy 2.x compatible)."""
    if not clips_paths:
        raise ValueError("No video clips provided")
    if not os.path.exists(audio_path):
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    logger.info(f"Assembling production at target resolution: {target_resolution[0]}x{target_resolution[1]}")

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
            # Normalize every clip to the target resolution (1080p Full HD) and 30fps
            clip = _fit_clip_to_resolution(clip, target_resolution)
            if MOVIEPY_V2:
                clip = clip.with_fps(30)
            else:
                clip = clip.set_fps(30)
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

    # Write final video - adaptive bitrate for crisp Full HD output
    render_bitrate = "5000k" if target_resolution[1] <= 720 else "8000k"
    try:
        mega_production.write_videofile(
            output_path,
            codec="libx264",
            fps=30,
            audio_codec="aac",
            bitrate=render_bitrate,
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
    """Translate script using Gemini with HF fallback."""
    if not text:
        return ""
    if target_lang.lower() == "english":
        return text

    # Try HF NLLB first if available (more cost-effective, 200+ languages)
    if HF_AVAILABLE and HUGGINGFACE_API_KEY and HUGGINGFACE_API_KEY != "your_huggingface_api_key_here":
        try:
            hf_engine = get_hf_engine()
            if hf_engine.is_configured():
                hf_translated = hf_engine.translate_text(text[:1000], target_lang, "english")
                if hf_translated and len(hf_translated) > 20:
                    logger.info(f"Translated via HF NLLB to {target_lang}")
                    # For long text, need to chunk, but for now return HF result for short or combine
                    if len(text) <= 1000:
                        return hf_translated
        except Exception as e:
            logger.warning(f"HF translation failed, falling back to Gemini: {e}")

    try:
        client = get_gemini_client()
        prompt = f"Translate the following documentary script into {target_lang} while maintaining a natural, engaging tone:\n\n{text}"
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt
        )
        return response.text.strip() if response.text else text
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return text

# ==============================================================================
# 9b. Hugging Face Advanced Features - NEW ADVANCED LEVEL
# ==============================================================================
def generate_long_script_hf_fallback(topic: str, duration_mins: int, provider: str = "auto") -> dict:
    """
    Advanced script generation with HF fallback
    provider: auto, gemini, huggingface, hybrid
    """
    if provider == "huggingface" or provider == "hf":
        if HF_AVAILABLE and HUGGINGFACE_API_KEY:
            try:
                hf_engine = get_hf_engine()
                if hf_engine.is_configured():
                    logger.info(f"Generating script via HF Mistral for topic: {topic}")
                    result = hf_engine.generate_documentary_script(topic, duration_mins)
                    if result:
                        return result
            except Exception as e:
                logger.error(f"HF script generation failed: {e}")
        return {}

    if provider == "gemini":
        return generate_long_script(topic, duration_mins)

    # Auto / Hybrid: Try Gemini first, fallback to HF
    try:
        result = generate_long_script(topic, duration_mins)
        if result and result.get("title"):
            return result
    except Exception as e:
        logger.warning(f"Gemini failed, trying HF fallback: {e}")

    if HF_AVAILABLE and HUGGINGFACE_API_KEY:
        try:
            hf_engine = get_hf_engine()
            if hf_engine.is_configured():
                logger.info("Gemini failed or empty, using HF fallback")
                result = hf_engine.generate_documentary_script(topic, duration_mins)
                if result:
                    return result
        except Exception as e:
            logger.error(f"HF fallback also failed: {e}")

    return {}

def generate_thumbnail_with_hf(prompt: str, title_text: str, output_path: str, provider: str = "auto") -> bool:
    """
    Advanced thumbnail generation with HF SDXL
    provider: auto, huggingface, pollinations, hybrid
    """
    # Try HF SDXL if available and requested
    if provider in ["huggingface", "hf", "auto", "hybrid"]:
        if HF_AVAILABLE and HUGGINGFACE_API_KEY and HUGGINGFACE_API_KEY != "your_huggingface_api_key_here":
            try:
                hf_engine = get_hf_engine()
                if hf_engine.is_configured():
                    logger.info(f"Generating thumbnail via HF SDXL: {prompt[:50]}")
                    # First generate base image via HF
                    temp_hf_path = str(TEMP_DIR / "temp_hf_thumb.jpg")
                    success = hf_engine.generate_image(prompt, temp_hf_path)
                    
                    if success and os.path.exists(temp_hf_path):
                        # Now add text overlay like original function
                        try:
                            img = Image.open(temp_hf_path).convert("RGB")
                            img = img.resize((1280, 720), Image.Resampling.LANCZOS if hasattr(Image, 'Resampling') else Image.LANCZOS)
                            draw = ImageDraw.Draw(img)
                            words = title_text.split()[:4]
                            short_title = " ".join(words).upper() if words else "WATCH NOW"
                            font_path = get_font_path(90)
                            try:
                                font = ImageFont.truetype(font_path, 90) if font_path else ImageFont.load_default()
                            except:
                                font = ImageFont.load_default()
                            
                            text_x, text_y = 50, 550
                            for ox in [-3, -2, -1, 0, 1, 2, 3]:
                                for oy in [-3, -2, -1, 0, 1, 2, 3]:
                                    if ox == 0 and oy == 0:
                                        continue
                                    draw.text((text_x+ox, text_y+oy), short_title, font=font, fill="black")
                            draw.text((text_x, text_y), short_title, font=font, fill="yellow")
                            img.save(output_path, quality=95)
                            logger.info(f"HF Thumbnail with text saved to {output_path}")
                            return True
                        except Exception as e:
                            logger.error(f"Failed to add text to HF thumbnail: {e}")
                            # Fallback to raw HF image
                            import shutil
                            shutil.copy(temp_hf_path, output_path)
                            return True
            except Exception as e:
                logger.warning(f"HF thumbnail generation failed: {e}, falling back")

    # Fallback to Pollinations (original method)
    if provider in ["pollinations", "auto", "hybrid"]:
        try:
            generate_thumbnail_with_text(prompt, title_text, output_path)
            if os.path.exists(output_path):
                return True
        except Exception as e:
            logger.error(f"Pollinations thumbnail also failed: {e}")

    return False

def generate_background_music_advanced(topic: str, output_path: str, duration: int = 20, provider: str = "auto") -> bool:
    """
    Advanced background music generation
    provider: pixabay, huggingface, auto
    Tries HF MusicGen first if available, fallback to Pixabay
    """
    # Try HF MusicGen
    if provider in ["huggingface", "hf", "auto"]:
        if HF_AVAILABLE and HUGGINGFACE_API_KEY:
            try:
                hf_engine = get_hf_engine()
                if hf_engine.is_configured():
                    logger.info(f"Generating music via HF MusicGen for topic: {topic}")
                    if hf_engine.generate_music(f"Cinematic background music for {topic}, inspiring, documentary style", output_path, duration):
                        return True
            except Exception as e:
                logger.warning(f"HF MusicGen failed: {e}")

    # Fallback to Pixabay audio API (original logic is in create_shorts_trailer, but we can implement here)
    if provider in ["pixabay", "auto"] and PIXABAY_API_KEY:
        try:
            encoded = urllib.parse.quote(topic)
            url = f"https://pixabay.com/api/audio/?key={PIXABAY_API_KEY}&q={encoded}&per_page=3"
            res = requests.get(url, timeout=10)
            res.raise_for_status()
            data = res.json()
            if data.get("hits"):
                bgm_url = data["hits"][0].get("audio") or data["hits"][0].get("previewURL")
                if bgm_url:
                    r = requests.get(bgm_url, timeout=15)
                    r.raise_for_status()
                    with open(output_path, "wb") as f:
                        f.write(r.content)
                    return os.path.exists(output_path) and os.path.getsize(output_path) > 1000
        except Exception as e:
            logger.warning(f"Pixabay music fallback failed: {e}")

    return False

def analyze_video_with_hf(title: str, script: str) -> Dict:
    """Full AI analysis using HF for SEO optimization"""
    if not HF_AVAILABLE or not HUGGINGFACE_API_KEY:
        return {}
    
    try:
        hf_engine = get_hf_engine()
        if hf_engine.is_configured():
            return hf_engine.full_video_analysis(title, script)
    except Exception as e:
        logger.warning(f"HF video analysis failed: {e}")
    
    return {}

def analyze_comment_sentiment_advanced(comment_text: str) -> Dict:
    """Advanced comment sentiment + emotion using HF"""
    if HF_AVAILABLE and HUGGINGFACE_API_KEY:
        try:
            from hf_engine import analyze_comment_advanced
            return analyze_comment_advanced(comment_text)
        except Exception as e:
            logger.warning(f"HF sentiment analysis failed: {e}")
    
    # Fallback simple
    return {"sentiment": {"label": "neutral", "score": 0.5}, "emotion": {"emotion": "neutral", "score": 0.5}}

def generate_voiceover_hf(text: str, output_path: str, lang: str = "en") -> bool:
    """
    Experimental HF TTS voiceover (MMS-TTS)
    Note: HF TTS via Inference API returns binary audio
    """
    if not HF_AVAILABLE or not HUGGINGFACE_API_KEY:
        return False
    
    try:
        hf_engine = get_hf_engine()
        if not hf_engine.is_configured():
            return False
        
        # MMS-TTS model selection based on language
        lang_model_map = {
            "en": "facebook/mms-tts-eng",
            "spa": "facebook/mms-tts-spa",
            "hin": "facebook/mms-tts-hin",
            "ben": "facebook/mms-tts-ben",
            "fra": "facebook/mms-tts-fra",
            "deu": "facebook/mms-tts-deu",
        }
        model = lang_model_map.get(lang.lower()[:3], "facebook/mms-tts-eng")
        
        # HF TTS payload
        payload = {"inputs": text[:500]}  # Limit for TTS
        result = hf_engine._query_api(model, payload, binary_response=True)
        
        if result and isinstance(result, bytes) and len(result) > 1000:
            # Check if not JSON error
            if result[:1] != b"{":
                with open(output_path, "wb") as f:
                    f.write(result)
                logger.info(f"HF TTS voiceover saved to {output_path}")
                return True
    except Exception as e:
        logger.warning(f"HF TTS failed: {e}")
    
    return False

# ==============================================================================
# 10. Streamlit Web Dashboard - ADVANCED with Hugging Face Integration
# ==============================================================================
def run_streamlit_dashboard():
    st.set_page_config(page_title="AuraStream 2.0 - Advanced", page_icon="🎥", layout="wide")
    st.title("🎥 AuraStream 2.0 - Advanced AI Studio")
    st.caption("Fully-automated YouTube AI Agent + Hugging Face Superpowers 🚀🤗")

    # Sidebar for API status
    with st.sidebar:
        st.header("🔑 API Status")
        st.write(f"Gemini: {'✅' if GEMINI_API_KEY and GEMINI_API_KEY != 'your_gemini_api_key_here' else '❌ Missing'}")
        st.write(f"HuggingFace: {'✅' if HUGGINGFACE_API_KEY and HUGGINGFACE_API_KEY != 'your_huggingface_api_key_here' else '❌ Missing'}")
        st.write(f"Pexels: {'✅' if PEXELS_API_KEY and PEXELS_API_KEY != 'your_pexels_api_key_here' else '❌ Missing'}")
        st.write(f"Pixabay: {'✅' if PIXABAY_API_KEY and PIXABAY_API_KEY != 'your_pixabay_api_key_here' else '❌ Missing'}")
        st.write(f"Telegram Alerts: {'✅' if TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_TOKEN != 'your_telegram_bot_token_here' else '⚠️ Optional'}")
        st.write(f"Telegram AI Agent: {'✅ Ready' if TELEGRAM_BOT_TOKEN and TELEGRAM_BOT_TOKEN != 'your_telegram_bot_token_here' else '❌ No bot token'}")
        st.caption("Run agent: `python telegram_agent.py`")
        st.divider()
        
        # HF Advanced Settings
        st.header("🤗 Hugging Face Advanced")
        if HF_AVAILABLE:
            hf_enabled = st.toggle("Enable HF Superpowers", value=bool(HUGGINGFACE_API_KEY and HUGGINGFACE_API_KEY != "your_huggingface_api_key_here"), help="Enable Hugging Face models for next-level AI")
            if hf_enabled:
                st.success("HF Engine Active")
                script_provider = st.selectbox("Script AI", ["auto", "gemini", "huggingface"], index=0, help="auto = Gemini primary, HF fallback")
                thumb_provider = st.selectbox("Thumbnail AI", ["auto", "huggingface", "pollinations", "hybrid"], index=0, help="HF SDXL = ultra quality, Pollinations = fast")
                music_provider = st.selectbox("Music AI", ["auto", "huggingface", "pixabay"], index=0)
                tts_provider = st.selectbox("Voice AI", ["edge-tts", "huggingface", "auto"], index=0)
                
                # Store in session state
                st.session_state['hf_enabled'] = hf_enabled
                st.session_state['script_provider'] = script_provider
                st.session_state['thumb_provider'] = thumb_provider
                st.session_state['music_provider'] = music_provider
                st.session_state['tts_provider'] = tts_provider
                
                st.divider()
                st.subheader("🧠 HF Models")
                st.caption(f"Text: {DEFAULT_MODELS.get('text_generation', 'Mistral-7B')}")
                st.caption(f"Image: {DEFAULT_MODELS.get('image_generation', 'SDXL')}")
                st.caption(f"Music: {DEFAULT_MODELS.get('musicgen', 'MusicGen')}")
            else:
                st.session_state['hf_enabled'] = False
                st.session_state['script_provider'] = "gemini"
                st.session_state['thumb_provider'] = "pollinations"
                st.session_state['music_provider'] = "pixabay"
                st.session_state['tts_provider'] = "edge-tts"
        else:
            st.warning("HF engine not installed")
            st.session_state['hf_enabled'] = False
        
        st.divider()
        st.info("Set API keys in .env file. See .env.example\n\nGet HF key: https://huggingface.co/settings/tokens")

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
        topic = st.session_state.get('topic', fetch_trending_topic() if auto_fetch else "")
        st.text_input("Video Topic", value=topic, disabled=True, key="topic_display")
        final_topic = topic
    else:
        final_topic = st.text_input("Video Topic", placeholder="Enter your topic...", key="manual_topic")

    col_a, col_b, col_c, col_d = st.columns(4)
    with col_a:
        duration = st.slider("Video Duration (mins)", min_value=1, max_value=10, value=3)
    with col_b:
        language = st.selectbox("Language", ["English", "Bengali", "Hindi", "Spanish", "French", "German", "Arabic", "Japanese"])
    with col_c:
        quality = st.selectbox("Quality Mode", ["balanced", "speed", "ultra-quality"], index=0, help="Ultra-quality uses HF SDXL + MusicGen (slower but better)")
    with col_d:
        video_quality = st.selectbox(
            "Video Resolution",
            ["1080p Full HD (Recommended)", "720p HD", "Best Available"],
            index=0,
            help="1080p Full HD downloads + renders in 1920x1080 with adaptive bitrate"
        )
    resolution_map = {"1080p Full HD (Recommended)": "1080p", "720p HD": "720p", "Best Available": "best"}
    selected_video_quality = resolution_map[video_quality]

    # Advanced options expander
    with st.expander("⚙️ Advanced AI Options", expanded=False):
        col1, col2 = st.columns(2)
        with col1:
            st.write("**AI Analysis**")
            enable_auto_tags = st.checkbox("Auto-Tagging via HF Zero-Shot", value=True)
            enable_sentiment = st.checkbox("Sentiment Analysis", value=True)
            enable_seo = st.checkbox("SEO Optimization via HF BART", value=True)
        with col2:
            st.write("**Experimental**")
            enable_hf_music = st.checkbox("HF MusicGen Background Music", value=st.session_state.get('hf_enabled', False))
            enable_enhanced_thumb = st.checkbox("HF Enhanced Thumbnail Prompt", value=st.session_state.get('hf_enabled', False))

    if st.button("🚀 Run Advanced Automation Pipeline", type="primary", use_container_width=True):
        if not final_topic or not final_topic.strip():
            st.error("Please provide a video topic!")
            return

        # Check API keys
        hf_enabled = st.session_state.get('hf_enabled', False)
        script_provider = st.session_state.get('script_provider', 'auto')
        thumb_provider = st.session_state.get('thumb_provider', 'auto')
        music_provider = st.session_state.get('music_provider', 'auto')
        tts_provider = st.session_state.get('tts_provider', 'edge-tts')

        if not hf_enabled and (not GEMINI_API_KEY or GEMINI_API_KEY == "your_gemini_api_key_here"):
            st.error("GEMINI_API_KEY is missing! Please set it in .env file or enable Hugging Face.")
            return

        if hf_enabled and (not HUGGINGFACE_API_KEY or HUGGINGFACE_API_KEY == "your_huggingface_api_key_here"):
            st.warning("HF enabled but API key missing - will use public rate limits (slower)")

        st.info(f"Pipeline started for topic: **{final_topic}** | Mode: {quality} | Resolution: **{video_quality}** | HF: {'✅' if hf_enabled else '❌'}")
        progress_bar = st.progress(0)
        status_text = st.empty()

        try:
            # Step 1: Generate Script with HF fallback
            status_text.text(f"1/6 Generating Script & Metadata via {script_provider}...")
            progress_bar.progress(5)
            if hf_enabled:
                script_data = generate_long_script_hf_fallback(final_topic, duration, provider=script_provider)
            else:
                script_data = generate_long_script(final_topic, duration)
            
            if not script_data:
                st.error("Failed to generate script. Check API keys and quota.")
                return
            progress_bar.progress(20)
            
            with st.expander("📜 Generated Script & Metadata", expanded=False):
                st.json(script_data)

            # Combine chapters
            full_script = " ".join([script_data.get(f"chapter{i}", "") for i in range(1, 6)]).strip()
            if not full_script:
                full_script = script_data.get("description", final_topic)

            # HF Advanced Analysis
            hf_analysis = {}
            if hf_enabled and enable_auto_tags:
                status_text.text("🔍 Running HF Advanced Analysis (Zero-Shot, Sentiment, SEO)...")
                progress_bar.progress(22)
                hf_analysis = analyze_video_with_hf(script_data.get("title", final_topic), full_script)
                if hf_analysis:
                    with st.expander("🤗 Hugging Face AI Analysis", expanded=True):
                        col1, col2 = st.columns(2)
                        with col1:
                            if hf_analysis.get("auto_tags"):
                                st.write("**🏷️ Auto-Generated Tags (HF Zero-Shot):**")
                                st.write(", ".join(hf_analysis["auto_tags"]))
                            if hf_analysis.get("sentiment"):
                                st.write("**😊 Sentiment:**")
                                st.json(hf_analysis["sentiment"])
                        with col2:
                            if hf_analysis.get("seo_summary"):
                                st.write("**📈 SEO Summary (HF BART):**")
                                st.write(hf_analysis["seo_summary"])
                            if hf_analysis.get("enhanced_thumbnail_prompt"):
                                st.write("**🎨 Enhanced Thumbnail Prompt:**")
                                st.write(hf_analysis["enhanced_thumbnail_prompt"])
                                # Use enhanced prompt if enabled
                                if enable_enhanced_thumb:
                                    script_data["thumbnail_prompt"] = hf_analysis["enhanced_thumbnail_prompt"]

            # Translate if needed
            if language != "English":
                status_text.text(f"Translating script to {language} via {'HF NLLB' if hf_enabled else 'Gemini'}...")
                progress_bar.progress(25)
                full_script = translate_script(full_script, language)

            # Step 2: Voiceover with HF option
            status_text.text(f"2/6 Synthesizing Voiceover via {tts_provider}...")
            progress_bar.progress(30)
            voiceover_path = str(TEMP_DIR / "voiceover.mp3")
            
            voiceover_success = False
            if hf_enabled and tts_provider in ["huggingface", "auto"]:
                # Try HF TTS first
                if generate_voiceover_hf(full_script[:500], voiceover_path, lang=language[:2].lower()):
                    voiceover_success = True
                    st.info("Voiceover via HF MMS-TTS (multilingual)")
                else:
                    # Fallback to Edge-TTS for full length
                    generate_voiceover_sync(full_script, voiceover_path)
                    voiceover_success = True
            else:
                generate_voiceover_sync(full_script, voiceover_path)
                voiceover_success = True
            
            progress_bar.progress(40)
            if os.path.exists(voiceover_path):
                st.audio(voiceover_path)
            else:
                st.warning("Voiceover file not created")

            # Step 3: Download footage & assemble
            status_text.text("3/6 Downloading Footage & Assembling Video...")
            progress_bar.progress(50)
            keywords = script_data.get("visual_keywords", [])
            if isinstance(keywords, str):
                try:
                    keywords = json.loads(keywords)
                except:
                    keywords = [k.strip() for k in keywords.split(",")]
            if not keywords:
                keywords = final_topic.split()[:10]

            clips = download_bulk_videos(keywords[:15], quality=selected_video_quality)
            if not clips:
                st.warning("No stock footage downloaded, check Pexels/Pixabay keys")
                st.error("Cannot assemble video without footage.")
                return

            progress_bar.progress(60)
            main_video_path = str(TEMP_DIR / "main_video.mp4")
            target_resolution = (1280, 720) if selected_video_quality == "720p" else (1920, 1080)
            create_mega_production(clips, voiceover_path, script_data.get("title", final_topic), main_video_path, target_resolution=target_resolution)
            progress_bar.progress(70)
            if os.path.exists(main_video_path):
                st.video(main_video_path)

            # Step 4: Thumbnail with HF SDXL
            status_text.text(f"4/6 Generating AI Thumbnail via {thumb_provider}...")
            progress_bar.progress(80)
            thumb_path = str(TEMP_DIR / "thumbnail.jpg")
            
            if hf_enabled:
                success = generate_thumbnail_with_hf(
                    script_data.get("thumbnail_prompt", final_topic),
                    script_data.get("title", final_topic),
                    thumb_path,
                    provider=thumb_provider
                )
                if not success:
                    # Fallback
                    generate_thumbnail_with_text(script_data.get("thumbnail_prompt", final_topic), script_data.get("title", final_topic), thumb_path)
            else:
                generate_thumbnail_with_text(script_data.get("thumbnail_prompt", final_topic), script_data.get("title", final_topic), thumb_path)
            
            progress_bar.progress(85)
            if os.path.exists(thumb_path):
                st.image(thumb_path, caption=f"Generated Thumbnail via {thumb_provider}")

            # Step 5: Background Music (HF MusicGen)
            bgm_path = None
            if enable_hf_music and hf_enabled:
                status_text.text("🎵 Generating Background Music via HF MusicGen...")
                progress_bar.progress(87)
                bgm_path = str(TEMP_DIR / "hf_bgm.mp3")
                if generate_background_music_advanced(final_topic, bgm_path, duration=20, provider=music_provider):
                    st.audio(bgm_path)
                    st.success("HF MusicGen background music generated!")
                else:
                    bgm_path = None

            # Step 6: Upload
            status_text.text("5/6 Uploading to YouTube...")
            progress_bar.progress(90)

            if os.path.exists("client_secrets.json"):
                try:
                    video_id = upload_video_to_youtube(main_video_path, thumb_path, script_data)
                    progress_bar.progress(100)
                    st.success(f"🎉 Video uploaded successfully! Video ID: {video_id}")
                    st.markdown(f"[Watch Here](https://youtube.com/watch?v={video_id})")
                    send_telegram_message(f"✅ AuraStream Advanced: Video '{script_data.get('title')}' uploaded! https://youtube.com/watch?v={video_id}\nHF Analysis: {hf_analysis.get('auto_tags', []) if hf_analysis else 'N/A'}")
                except Exception as e:
                    st.warning(f"YouTube upload failed: {e}. Video saved locally at {main_video_path}")
                    progress_bar.progress(100)
                    st.success("🎉 Video production completed locally!")
            else:
                progress_bar.progress(100)
                st.success("🎉 Advanced Video production completed! (Local mode)")
                st.info(f"Main video: {main_video_path}\nThumbnail: {thumb_path}")
                if bgm_path:
                    st.info(f"BGM: {bgm_path}")
                send_telegram_message(f"✅ AuraStream Advanced: Local production complete for '{final_topic}' | HF: {hf_enabled}")

            # Bonus: Shorts with HF Music
            with st.expander("📱 Create Shorts Trailer (Advanced)", expanded=False):
                col1, col2 = st.columns(2)
                with col1:
                    if st.button("Generate Shorts (Standard)"):
                        shorts_path = str(TEMP_DIR / "shorts_trailer.mp4")
                        create_shorts_trailer(main_video_path, shorts_path, final_topic)
                        st.video(shorts_path)
                        st.success(f"Shorts trailer created: {shorts_path}")
                with col2:
                    if hf_enabled and st.button("Generate Shorts + HF MusicGen"):
                        shorts_path = str(TEMP_DIR / "shorts_trailer_hf.mp4")
                        # Use HF music if available
                        if bgm_path and os.path.exists(bgm_path):
                            # Custom shorts with HF music
                            st.info("Creating shorts with HF MusicGen track...")
                            create_shorts_trailer(main_video_path, shorts_path, final_topic)
                            # Mix HF music
                            try:
                                from moviepy import VideoFileClip, AudioFileClip, CompositeAudioClip
                                shorts_clip = VideoFileClip(shorts_path)
                                hf_bgm = AudioFileClip(bgm_path).subclipped(0, shorts_clip.duration) if MOVIEPY_V2 else AudioFileClip(bgm_path).subclip(0, shorts_clip.duration)
                                if MOVIEPY_V2:
                                    hf_bgm = hf_bgm.with_volume_scaled(0.3)
                                    if shorts_clip.audio:
                                        mixed = CompositeAudioClip([shorts_clip.audio, hf_bgm])
                                        shorts_clip = shorts_clip.with_audio(mixed)
                                    else:
                                        shorts_clip = shorts_clip.with_audio(hf_bgm)
                                shorts_clip.write_videofile(shorts_path, codec="libx264", fps=30, logger=None)
                                shorts_clip.close()
                            except Exception as e:
                                logger.warning(f"HF music mix for shorts failed: {e}")
                        else:
                            create_shorts_trailer(main_video_path, shorts_path, final_topic)
                        st.video(shorts_path)
                        st.success(f"Advanced Shorts with HF Music: {shorts_path}")

            # Advanced Comment Analysis Demo
            if hf_enabled and enable_sentiment:
                with st.expander("💬 Advanced Comment Sentiment Demo (HF)", expanded=False):
                    test_comment = st.text_input("Test a comment for sentiment analysis:", "This video is amazing! Love the content!")
                    if st.button("Analyze Comment"):
                        analysis = analyze_comment_sentiment_advanced(test_comment)
                        st.json(analysis)

        except Exception as e:
            logger.exception("Pipeline failed")
            st.error(f"Pipeline failed: {e}")
            send_telegram_message(f"❌ AuraStream pipeline failed for '{final_topic}': {e}")

# ==============================================================================
# 11. YouTube Comments Auto-Reply Prompt - FIXED + HF Sentiment Enhanced
# ==============================================================================
def reply_to_youtube_comments(video_id: str, use_hf_sentiment: bool = True):
    """Auto-reply to YouTube comments using Gemini + HF Sentiment Analysis."""
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
        # Try HF fallback for reply generation
        if not (HF_AVAILABLE and HUGGINGFACE_API_KEY):
            return
        client = None

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
            top_comment_id = top_comment.get("id")

            if not top_comment_id:
                continue

            author = top_snippet.get("authorDisplayName", "Unknown")
            text = top_snippet.get("textOriginal", "")
            replies = item.get("replies", {}).get("comments", [])

            if len(replies) == 0 and text:
                try:
                    # Advanced: Analyze sentiment with HF before replying
                    sentiment_info = ""
                    if use_hf_sentiment and HF_AVAILABLE and HUGGINGFACE_API_KEY:
                        try:
                            analysis = analyze_comment_sentiment_advanced(text)
                            sentiment = analysis.get("sentiment", {}).get("label", "neutral")
                            emotion = analysis.get("emotion", {}).get("emotion", "neutral")
                            sentiment_info = f" (User sentiment: {sentiment}, emotion: {emotion})"
                            logger.info(f"Comment from {author}: sentiment={sentiment}, emotion={emotion}")
                            
                            # Skip toxic negative comments or customize reply based on sentiment
                            if sentiment.lower() in ["negative"] and analysis.get("sentiment", {}).get("score", 0) > 0.8:
                                # For very negative comments, be extra empathetic
                                prompt = f"Draft a very empathetic, understanding response (under 280 chars) to this negative YouTube comment from {author}: '{text}'. Be kind and address concerns."
                            else:
                                prompt = f"Draft a polite, friendly, and engaging response (under 280 chars) to this YouTube comment from {author}{sentiment_info}: '{text}'"
                        except Exception as e:
                            logger.warning(f"Sentiment analysis failed, using generic prompt: {e}")
                            prompt = f"Draft a polite, friendly, and engaging response (under 280 characters) to this YouTube comment from {author}:\n\n'{text}'"
                    else:
                        prompt = f"Draft a polite, friendly, and engaging response (under 280 characters) to this YouTube comment from {author}:\n\n'{text}'"

                    # Generate reply via Gemini or HF fallback
                    reply_text = "Thanks for your comment! 🙏"
                    if client:
                        try:
                            ai_response = client.models.generate_content(
                                model='gemini-2.5-flash',
                                contents=prompt
                            )
                            reply_text = ai_response.text.strip() if ai_response.text else reply_text
                        except Exception as e:
                            logger.warning(f"Gemini reply failed, trying HF: {e}")
                            if HF_AVAILABLE:
                                try:
                                    hf_engine = get_hf_engine()
                                    hf_reply = hf_engine.generate_text(prompt, max_new_tokens=100)
                                    if hf_reply:
                                        reply_text = hf_reply.strip()[:280]
                                except:
                                    pass
                    else:
                        # No Gemini, use HF directly
                        if HF_AVAILABLE:
                            try:
                                hf_engine = get_hf_engine()
                                hf_reply = hf_engine.generate_text(prompt, max_new_tokens=100)
                                if hf_reply:
                                    reply_text = hf_reply.strip()[:280]
                            except Exception as e:
                                logger.warning(f"HF reply generation failed: {e}")

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
                    time.sleep(1.5)
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
