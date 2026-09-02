# 🎥 AuraStream 2.0

AuraStream is a complete, fully-automated YouTube AI Agent. It acts as an autonomous production studio that handles the entire lifecycle of a YouTube channel—from finding trending topics and generating scripts, to producing voiceovers, downloading stock footage, editing, generating thumbnails, and even uploading and replying to comments via the YouTube API.

## 🚀 Features

1. **AI Script & Metadata Generation:** Uses Google Gemini 2.5 Flash to generate fully structured scripts, titles, descriptions, and tags.
2. **Automated Voiceovers:** Employs `edge-tts` to generate high-quality, natural-sounding voiceovers.
3. **Dual Fallback Video Downloader:** Automatically fetches HD stock footage from Pexels, with a seamless fallback to Pixabay.
4. **Mega Production Assembler:** Leverages MoviePy and Whisper to assemble clips, align them with audio, and generate engaging, Alex Hormozi-style subtitles with word-level timestamps.
5. **Shorts/Reels Creator:** Automatically extracts engaging sections from horizontal 16:9 videos and converts them into 9:16 vertical shorts.
6. **AI Thumbnail Generator:** Connects to Pollinations AI to generate stunning thumbnails, using Pillow to overlay bold, custom text.
7. **YouTube Auto-Uploader & Commenter:** Uploads videos directly to your channel, sets custom thumbnails, and pins an engaging top comment.
8. **Automated Translation & Trends:** Scrapes Google Trends to find viral topics and dynamically translates scripts into multiple languages (e.g., Bengali, Hindi, Spanish).
9. **YouTube Auto-Reply:** Monitors comments and uses Gemini to craft friendly, engaging replies on your behalf.
10. **Telegram Alerts:** Notifies your mobile device about rendering status and uploads.
11. **Streamlit Dashboard:** A beautiful, easy-to-use web interface for controlling the entire pipeline.

## 🛠️ Installation

1. Clone this repository.
2. Install the required dependencies:
   ```bash
   pip install -r requirements.txt
   ```
3. Copy `.env.example` to `.env` and fill in your API keys (Pexels, Pixabay, Gemini, Telegram).
4. Download your `client_secrets.json` from the Google Cloud Console (with YouTube Data API v3 enabled) and place it in the root directory.

## 🎯 Usage

To launch the interactive dashboard, simply run:
```bash
streamlit run app.py
```

From the dashboard, you can automatically fetch trending topics, set your desired video duration, select your language, and trigger the entire end-to-end automation pipeline.
