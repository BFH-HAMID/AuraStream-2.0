#!/bin/sh
# AuraStream 2.0 container entrypoint (HF Space / Docker / Compose)
set -e

# If a specific command is passed (e.g. docker compose agent service), just run it.
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

# Optionally run the Telegram AI Agent alongside the web dashboard.
# Enable with:  RUN_TELEGRAM_AGENT=true   (see DEPLOY_HF_SPACE.md)
if [ "${RUN_TELEGRAM_AGENT}" = "true" ]; then
  echo "🤖 Starting Telegram AI Agent in background..."
  python telegram_agent.py &
fi

# Web dashboard (HF Spaces sets PORT=7860; Docker default 8501)
exec streamlit run app.py --server.address 0.0.0.0 --server.port "${PORT:-8501}"
