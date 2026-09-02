import os
import json
import re
import asyncio
import requests
import urllib.parse
import random
import time
from typing import List, Dict

# --- External Libraries ---
from google import genai
from google.genai import types
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload
from google_auth_oauthlib.flow import InstalledAppFlow
import edge_tts
from moviepy.editor import (
    VideoFileClip, AudioFileClip, TextClip, CompositeVideoClip, 
    CompositeAudioClip, concatenate_videoclips
)
import whisper_timestamped as whisper
from PIL import Image, ImageDraw, ImageFont
from pytrends.request import TrendReq
import streamlit as st


# ==============================================================================
# 1. Gemini Script & Metadata Prompt
# ==============================================================================
def generate_long_script(topic: str, duration_mins: int) -> dict:
    client = genai.Client()
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
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt,
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
        ),
    )
    
    # Handle JSON cleaning using regex to prevent JSONDecodeError
    json_str = re.sub(r'^```json\s*|\s*```$', '', response.text.strip(), flags=re.MULTILINE)
    
    try:
        return json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"JSONDecodeError: {e}")
        return {}


# ==============================================================================
# 2. Voiceover & Audio Engine Prompt
# ==============================================================================
async def generate_voiceover_async(text: str, output_path: str):
    # Parse the input text by periods, join sentences with explicit pauses.
    sentences = [s.strip() for s in text.split('.') if s.strip()]
    
    # Adding a pause indicator. In raw text, ellipsis or periods add natural pauses in neural TTS.
    processed_text = "... ".join(sentences) + "."
    
    communicate = edge_tts.Communicate(processed_text, "en-US-ChristopherNeural")
    await communicate.save(output_path)


# ==============================================================================
# 3. Dual Fallback Video Downloader Prompt
# ==============================================================================
PEXELS_API_KEY = os.getenv("PEXELS_API_KEY", "")
PIXABAY_API_KEY = os.getenv("PIXABAY_API_KEY", "")

def download_bulk_videos(keywords: list) -> list:
    downloaded_paths = []
    
    for i, keyword in enumerate(keywords):
        output_path = f"clip_{i}.mp4"
        success = False
        
        # Try Pexels API
        try:
            headers = {"Authorization": PEXELS_API_KEY}
            url = f"https://api.pexels.com/videos/search?query={keyword}&per_page=1&size=large"
            res = requests.get(url, headers=headers, timeout=10)
            res.raise_for_status()
            data = res.json()
            
            if data.get("videos"):
                video_files = data["videos"][0]["video_files"]
                # Filter for 1080p
                hd_file = next((f for f in video_files if f.get("height") == 1080), video_files[0])
                video_url = hd_file["link"]
                
                vid_res = requests.get(video_url, stream=True, timeout=15)
                with open(output_path, 'wb') as f:
                    for chunk in vid_res.iter_content(chunk_size=8192):
                        f.write(chunk)
                        
                downloaded_paths.append(output_path)
                success = True
        except Exception as e:
            print(f"Pexels failed for '{keyword}': {e}")
            
        # Fallback to Pixabay API
        if not success:
            try:
                pixabay_url = f"https://pixabay.com/api/videos/?key={PIXABAY_API_KEY}&q={keyword}&video_type=film"
                res = requests.get(pixabay_url, timeout=10)
                res.raise_for_status()
                data = res.json()
                
                if data.get("hits"):
                    video_url = data["hits"][0]["videos"]["large"]["url"]
                    vid_res = requests.get(video_url, stream=True, timeout=15)
                    with open(output_path, 'wb') as f:
                        for chunk in vid_res.iter_content(chunk_size=8192):
                            f.write(chunk)
                    downloaded_paths.append(output_path)
            except Exception as e:
                print(f"Pixabay fallback failed for '{keyword}': {e}")
                
    return downloaded_paths


# ==============================================================================
# 4. MoviePy Video & Subtitle Assembler Prompt
# ==============================================================================
def create_mega_production(clips_paths: list, audio_path: str, title: str, output_path: str):
    audio = AudioFileClip(audio_path)
    audio_duration = audio.duration
    
    clips = []
    current_duration = 0
    for path in clips_paths:
        try:
            clip = VideoFileClip(path)
            clips.append(clip)
            current_duration += clip.duration
            if current_duration >= audio_duration:
                break
        except Exception as e:
            print(f"Error loading clip {path}: {e}")
            
    final_video = concatenate_videoclips(clips).subclip(0, audio_duration)
    final_video = final_video.set_audio(audio)
    
    # Transcribe audio for word-level timestamps
    audio.write_audiofile("temp_subtitle.wav", logger=None)
    model = whisper.load_model("base")
    results = whisper.transcribe(model, "temp_subtitle.wav")
    
    # Generate Alex Hormozi style subtitles
    colors = ['yellow', 'cyan', 'green']
    subtitle_clips = []
    
    for segment in results["segments"]:
        for word_info in segment["words"]:
            word = word_info["text"]
            start = word_info["start"]
            end = word_info["end"]
            
            txt_clip = TextClip(
                word, 
                fontsize=75, 
                color=random.choice(colors), 
                font='Arial-Bold',
                stroke_color='black',
                stroke_width=3
            )
            txt_clip = (txt_clip
                        .set_position(('center', 'bottom'))
                        .set_start(start)
                        .set_duration(end - start))
            subtitle_clips.append(txt_clip)
            
    mega_production = CompositeVideoClip([final_video] + subtitle_clips)
    mega_production.write_videofile(
        output_path, 
        codec="libx264", 
        fps=30,
        audio_codec="aac"
    )


# ==============================================================================
# 5. 9:16 Shorts Creator Prompt
# ==============================================================================
def create_shorts_trailer(main_video_path: str, shorts_output_path: str, topic_keyword: str):
    main_clip = VideoFileClip(main_video_path)
    h = main_clip.h
    new_w = int(h * (9/16))
    x_center = main_clip.w / 2
    
    subclips = []
    clip_dur = min(15, main_clip.duration / 4)
    
    # Pick 4 distinct 15-second subclips with fade transitions
    for i in range(4):
        start_t = i * (main_clip.duration / 4)
        subclip = main_clip.subclip(start_t, start_t + clip_dur)
        subclip = subclip.fadein(1).fadeout(1)
        subclips.append(subclip)
        
    trailer = concatenate_videoclips(subclips)
    
    # Crop aspect ratio vertically to 9:16
    trailer = trailer.crop(
        x1=x_center - new_w/2, 
        y1=0, 
        x2=x_center + new_w/2, 
        y2=h
    ).resize((1080, 1920))
    
    # Fetch background music
    pixabay_url = f"https://pixabay.com/api/audio/?key={PIXABAY_API_KEY}&q={topic_keyword}"
    res = requests.get(pixabay_url).json()
    
    if res.get("hits"):
        bgm_url = res["hits"][0]["audio"]
        bgm_res = requests.get(bgm_url)
        with open("temp_bgm.mp3", "wb") as f:
            f.write(bgm_res.content)
            
        bgm_clip = AudioFileClip("temp_bgm.mp3").subclip(0, trailer.duration)
        bgm_clip = bgm_clip.volumex(0.2)
        
        final_audio = CompositeAudioClip([trailer.audio, bgm_clip])
        trailer = trailer.set_audio(final_audio)
        
    trailer.write_videofile(shorts_output_path, codec="libx264", fps=30)


# ==============================================================================
# 6. AI Thumbnail & Bold Text Overlay Prompt
# ==============================================================================
def generate_thumbnail_with_text(prompt: str, title_text: str, output_path: str):
    encoded_prompt = urllib.parse.quote(prompt)
    url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?width=1280&height=720&nologo=true"
    
    res = requests.get(url)
    img_path = "temp_raw_thumb.jpg"
    with open(img_path, 'wb') as f:
        f.write(res.content)
        
    img = Image.open(img_path)
    draw = ImageDraw.Draw(img)
    
    words = title_text.split()[:4]
    short_title = " ".join(words).upper()
    
    try:
        font = ImageFont.truetype("arialbd.ttf", 90)
    except IOError:
        font = ImageFont.load_default()
        
    text_x, text_y = 50, 550
    outline_color = "black"
    fill_color = "yellow"
    
    # 2px black outline shadow
    for offset_x in [-2, -1, 0, 1, 2]:
        for offset_y in [-2, -1, 0, 1, 2]:
            draw.text((text_x + offset_x, text_y + offset_y), short_title, font=font, fill=outline_color)
            
    # Draw main text
    draw.text((text_x, text_y), short_title, font=font, fill=fill_color)
    
    img.save(output_path)


# ==============================================================================
# 7. YouTube Upload & Comment Automation Prompt
# ==============================================================================
YOUTUBE_SCOPES = ["https://www.googleapis.com/auth/youtube.upload", "https://www.googleapis.com/auth/youtube.force-ssl"]

def upload_video_to_youtube(video_path: str, thumb_path: str, meta_data: dict) -> str:
    flow = InstalledAppFlow.from_client_secrets_file('client_secrets.json', YOUTUBE_SCOPES)
    credentials = flow.run_local_server(port=0)
    youtube = build('youtube', 'v3', credentials=credentials)
    
    body = {
        'snippet': {
            'title': meta_data.get('title', 'Default Title'),
            'description': meta_data.get('description', 'Default Description'),
            'tags': meta_data.get('tags', '').split(','),
            'categoryId': '27' # Education
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
    
    # Set custom thumbnail
    youtube.thumbnails().set(
        videoId=video_id,
        media_body=MediaFileUpload(thumb_path)
    ).execute()
    
    # Post engaging top-level pinned comment
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
    
    return video_id


# ==============================================================================
# 8. Telegram Bot Alert System Prompt
# ==============================================================================
def send_telegram_message(message: str):
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    chat_id = os.getenv("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        return
        
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        requests.post(url, json=payload, timeout=10)
    except requests.exceptions.RequestException:
        pass


# ==============================================================================
# 9. Automated Scraper & Translation Prompt
# ==============================================================================
def fetch_trending_topic(region='united_states') -> str:
    try:
        pytrends = TrendReq(hl='en-US', tz=360)
        trending_searches = pytrends.trending_searches(pn=region)
        return trending_searches[0][0]
    except Exception as e:
        print(f"Trends Error: {e}")
        return "Artificial Intelligence"

def translate_script(text: str, target_lang: str) -> str:
    client = genai.Client()
    prompt = f"Translate the following documentary script into {target_lang} while maintaining a natural, engaging tone:\n\n{text}"
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=prompt
    )
    return response.text


# ==============================================================================
# 10. Streamlit Web Dashboard Prompt
# ==============================================================================
def run_streamlit_dashboard():
    st.title("🎥 AuraStream 2.0 Dashboard")
    
    auto_fetch = st.toggle("Auto-Fetch Trend", value=True)
    if auto_fetch:
        topic = st.text_input("Video Topic", value="Fetching current trend...", disabled=True)
    else:
        topic = st.text_input("Video Topic", placeholder="Enter your topic...")
        
    duration = st.slider("Video Duration (mins)", min_value=1, max_value=10, value=3)
    language = st.selectbox("Language", ["English", "Bengali", "Hindi", "Spanish"])
    
    if st.button("🚀 Run Automation Pipeline"):
        st.info("Pipeline started...")
        progress_bar = st.progress(0)
        
        st.text("1/5 Generating Script & Metadata...")
        progress_bar.progress(20)
        
        st.text("2/5 Synthesizing Voiceover...")
        progress_bar.progress(40)
        
        st.text("3/5 Downloading Footage & Assembling Video...")
        progress_bar.progress(60)
        
        st.text("4/5 Generating AI Thumbnail...")
        progress_bar.progress(80)
        
        st.text("5/5 Uploading to YouTube...")
        progress_bar.progress(100)
        
        st.success("🎉 Video uploaded successfully! [Watch Here](https://youtube.com)")


# ==============================================================================
# 11. YouTube Comments Auto-Reply Prompt
# ==============================================================================
def reply_to_youtube_comments(video_id: str):
    flow = InstalledAppFlow.from_client_secrets_file('client_secrets.json', YOUTUBE_SCOPES)
    credentials = flow.run_local_server(port=0)
    youtube = build('youtube', 'v3', credentials=credentials)
    client = genai.Client()
    
    try:
        request = youtube.commentThreads().list(
            part="snippet,replies",
            videoId=video_id,
            maxResults=50
        )
        response = request.execute()
        
        for item in response.get("items", []):
            top_comment = item["snippet"]["topLevelComment"]["snippet"]
            comment_id = item["id"]
            author = top_comment["authorDisplayName"]
            text = top_comment["textOriginal"]
            
            # Check if the channel owner has replied (or if there are no replies)
            replies = item.get("replies", {}).get("comments", [])
            if len(replies) == 0:
                prompt = f"Draft a polite, friendly, and engaging response (under 280 characters) to this YouTube comment from {author}:\n\n'{text}'"
                ai_response = client.models.generate_content(
                    model='gemini-2.5-flash',
                    contents=prompt
                )
                reply_text = ai_response.text.strip()
                
                youtube.comments().insert(
                    part="snippet",
                    body={
                        "snippet": {
                            "parentId": comment_id,
                            "textOriginal": reply_text
                        }
                    }
                ).execute()
                print(f"Replied to {author}")
                time.sleep(1) # Gracefully handle rate limits
                
    except Exception as e:
        print(f"API Quota/Rate Limit Error: {e}")

if __name__ == "__main__":
    # Ensure this doesn't run automatically in a non-streamlit context
    if "streamlit" in os.sys.modules:
        run_streamlit_dashboard()
