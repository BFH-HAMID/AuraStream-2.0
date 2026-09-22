# ==============================================================================
# AuraStream 2.0 — 1080p Full HD AI Studio + Telegram AI Agent
# Dockerfile (CPU-optimized, production ready)
#
#   Build:   docker build -t aurastream .
#   Web:     docker run -p 8501:8501 --env-file .env aurastream
#   Agent:   docker run --env-file .env aurastream python telegram_agent.py
# ==============================================================================

FROM python:3.10-slim

# ---- System dependencies -----------------------------------------------------
# ffmpeg      : required by whisper-timestamped + MoviePy rendering
# fonts       : DejaVu/Liberation for bold subtitle + thumbnail text overlay
# git         : needed by some transformers/hub operations
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        git \
        fonts-dejavu-core \
        fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/app/.cache/huggingface \
    STREAMLIT_SERVER_HEADLESS=true \
    STREAMLIT_SERVER_PORT=8501 \
    STREAMLIT_SERVER_ADDRESS=0.0.0.0

# ---- Python dependencies -----------------------------------------------------
# Install CPU-only PyTorch first (2.5 GB smaller than the CUDA default wheels),
# then the rest of the stack. `torch` from requirements.txt is already satisfied.
RUN pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu

COPY requirements.txt .
RUN pip install -r requirements.txt

# ---- Application -------------------------------------------------------------
COPY app.py hf_engine.py telegram_agent.py ./
COPY .env.example ./

# Non-root user for safety; temp_assets + model cache stay writable via volumes
RUN useradd -m -u 1000 aurastream \
    && mkdir -p temp_assets /app/.cache/huggingface \
    && chown -R aurastream:aurastream /app
USER aurastream

# Persist renders + whisper/HF model downloads
VOLUME ["/app/temp_assets", "/app/.cache/huggingface"]

EXPOSE 8501

HEALTHCHECK --interval=60s --timeout=10s --start-period=120s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=5).status==200 else 1)"

# Default: Streamlit web dashboard. For the Telegram AI Agent override with:
#   docker run --env-file .env aurastream python telegram_agent.py
#   (or: docker compose --profile agent up agent)
CMD ["streamlit", "run", "app.py", "--server.address", "0.0.0.0", "--server.port", "8501"]
