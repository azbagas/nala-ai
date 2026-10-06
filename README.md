# Proactive AI Companion Bot

A Telegram companion that speaks Indonesian, remembers you, and texts first, powered by Gemini.
See [PRD_AI_Companion_Bot.md](PRD_AI_Companion_Bot.md) for the full spec.

## Status

| Phase | Scope | Status |
|---|---|---|
| **M0** | Repo, Docker skeleton, config loader, connectivity check | ✅ |
| **M1** | Basic persona chat, bubbles, typing delay, debouncing | ✅ |
| **M2** | SQLite memory, extraction, summaries, memory commands | ✅ |
| M3 | Proactive planner | ⏳ |
| M4 | Her daily life | ⏳ |
| M5 | Controls & polish | ⏳ |
| M6 | Deploy & harden | ⏳ |

## Quick start (local)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt

copy .env.example .env      # then fill TELEGRAM_BOT_TOKEN and GEMINI_API_KEY
python -m app.main --check  # validates config, Telegram token, both Gemini models
python -m app.main          # run the bot
```

**Find your OWNER_ID:** leave `OWNER_ID` empty, run the bot, send `/start` to it on Telegram.
It replies with your numeric ID. Put it in `.env` and restart. After that, everyone else is ignored.

## Available commands

| Command | What it does |
|---|---|
| `/start` | Greets the owner warmly in character (or gives your ID on first setup) |
| `/ping` | Round-trip check to Gemini, shows model, latency, and tokens |
| `/reload_persona` | Hot-reloads `config/persona.yaml` without restarting the bot |
| `/memory` | Views remembered facts, pending follow-up threads, and summaries |
| `/forget <id>` | Deactivates a remembered fact by its numeric ID |
| `/reset_memory` | Wipes remembered facts, open threads, summaries, and chat history |
| `/run_nightly` | Runs the memory extraction and daily summary job manually |
| `/clear` or `/reset` | Clears recent in-memory conversation history |

## Docker

```powershell
docker compose up -d --build
docker compose logs -f
```

`config/` and `prompts/` are mounted read-only, so you can edit the persona and use `/reload_persona` without rebuilding. `data/` holds the DB, logs and backups.

## Layout

```
app/            main.py, config.py, gateway/, engine/, memory/, proactive/, life/, utils/
config/         config.yaml (behavior), persona.yaml (her personality)
prompts/        Indonesian prompt templates (*.id.md)
data/           runtime data (gitignored)
tests/          pytest
```

## Tests

```powershell
pytest
```
