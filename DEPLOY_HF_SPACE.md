# 🤗 Deploy AuraStream 2.0 on Hugging Face Spaces (FREE)

> **Telegram AI Agent + 1080p Full HD studio + Streamlit dashboard — ekta Space e sob!**

---

## Step 1 — Space banano (2 minute)

1. https://huggingface.co/new-space → Space name: `AuraStream`
2. **SDK:** select **Docker** → **Blank**
3. Visibility: **Public** (free CPU) — secrets gulo thik thakle safe
4. **Create Space**

## Step 2 — Files upload

Space er **Files** tab e ei repo er file gulo upload koro:

```
Dockerfile  docker-entrypoint.sh  app.py  agent_brain.py  hf_engine.py
telegram_agent.py  requirements.txt  .env.example  README.md
```

⚠️ **client_secrets.json upload koro na!** Secret er jonno Step 3 dekho.
`.env` o dorkar nei — HF Space **Settings → Variables and secrets** use kore.

## Step 3 — Secrets add koro

Space → **Settings → Variables and secrets** → ei secrets gulo add koro:

| Secret | Dorkar? | Kothay pabo |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | ✅ (agent er jonno) | @BotFather → `/newbot` |
| `GEMINI_API_KEY` | ✅ | https://aistudio.google.com/app/apikey |
| `HUGGINGFACE_API_KEY` | recommended | https://huggingface.co/settings/tokens |
| `PEXELS_API_KEY` | ✅ (video footage) | https://www.pexels.com/api/ |
| `PIXABAY_API_KEY` | fallback | https://pixabay.com/api/docs/ |
| `GOOGLE_CLIENT_ID` + `GOOGLE_CLIENT_SECRET` | YouTube upload er jonno | Google Cloud Console → Credentials → OAuth Client (type: **Desktop app**) |
| `RUN_TELEGRAM_AGENT` | ✅ value: `true` | ei dile dashboard + bot dutoi cholbe |
| `TELEGRAM_CHAT_ID` | optional (alerts) | @userinfobot |

## Step 4 — Space README metadata

Space er `README.md` er upore ei front matter ta thakte hobe:

```yaml
---
title: AuraStream 2.0
emoji: 🎥
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 8501
---
```

> `app_port: 8501` sarbotto — Dockerfile er Streamlit port er sathe match kore.

## Step 5 — Telegram e YouTube connect koro (one-time)

1. Telegram e bot ke pathao: `/upload last`
2. Bot ekta **Google sign-in link** dibe → phone/laptop e khulo
3. Google ekta page e pathabe ja **load hobe na** — thik ache!
4. Address bar er **full URL ta copy kore bot ke paste** koro
5. ✅ Done! Bot YouTube sign-in mone rakhbe (Space restart korle abar korte hobe)

---

## ⚠️ Jene rakho (Free Space limits)

| Bishoy | Limit | Solution |
|---|---|---|
| Free CPU: 2 vCPU, 16 GB RAM | 1080p render slow (5-15 min) | Thik ache, just wait |
| **48h inactivity** por Space sleep kore | Bot offline hoye jay | https://uptimerobot.com (free) theke Space URL `https://<user>-<space>.hf.space` 10 min por por ping koro — bot always-on thakbe |
| Space **restart** hole file haray | `token.pickle`, agent memory reset | Re-auth koro (`/upload`), profile abar `/settings` diye dao |
| Long render er somoy bot ta reply kore na | — | Normal — rendering background e cholche, progress message update hoy |

## Local vs Space

| Feature | Local (`python telegram_agent.py`) | HF Space |
|---|---|---|
| Telegram bot | ✅ | ✅ (`RUN_TELEGRAM_AGENT=true`) |
| Dashboard | ✅ `:8501` | ✅ `:8501` (public URL) |
| 1080p render | ✅ fast | ✅ slow but kaj kore |
| YouTube upload | ✅ token.pickle persist | ✅ restart e re-auth lagbe |
| Agent memory | ✅ persist | Space restart e reset |
