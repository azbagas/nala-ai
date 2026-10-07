# Proactive AI Companion Bot

A Telegram companion that speaks Indonesian, remembers you, and texts first, powered by Gemini.
See [PRD_AI_Companion_Bot.md](PRD_AI_Companion_Bot.md) for the full spec.

## Status

| Phase | Scope | Status |
|---|---|---|
| **M0** | Repo, Docker skeleton, config loader, connectivity check | ✅ |
| **M1** | Basic persona chat, bubbles, typing delay, debouncing | ✅ |
| **M2** | SQLite memory, extraction, summaries, memory commands | ✅ |
| **M3** | Proactive planner with windows, persistence, pre-send checks, quiet hours | ✅ |
| **M4** | Her daily life (schedule generation, mood/energy, activity matching) | ✅ |
| **M5** | Controls & polish (natural language pauses, anti-repetition, frequency, token stats) | ✅ |
| **M6** | Deploy & harden (systemd/Docker, WAL backups, rotation & restore, non-text handling) | ✅ |

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
| `/status` | Shows today's proactive plan, counters, pause state, and token usage / latency stats |
| `/frequency [low|normal|high]` | Adjusts proactive messaging frequency (low: 2x/day, normal: 4x/day, high: 5x/day) |
| `/pause [duration]` | Pauses proactive messages (e.g., `/pause 3h`, `/pause today`, `/pause 30m`) |
| `/resume` | Resumes proactive messages |
| `/test_proactive` | Manually triggers a proactive message immediately for testing |
| `/life [reroll]` | Views her current activity, mood, energy, and full schedule (or rerolls it) |
| `/backup` | Manually triggers a timestamped SQLite database backup to `data/backups/` |

> **Natural Language Controls:** In addition to commands, you can pause or resume naturally in chat! For example:
> - *"aku mau tidur dulu ya"* -> Pauses until next morning (06:00 WIB).
> - *"jangan chat dulu hari ini"* -> Pauses until midnight.
> - *"lagi sibuk 2 jam"* -> Pauses for 2 hours.
> - *"udah bangun"* / *"aku udah luang"* -> Resumes proactive messages immediately.

> **Non-Text Messages:** If you send voice notes, photos, or stickers, she warmly acknowledges in Indonesian (*"aku belum bisa liat atau denger itu nih..."*) and registers your activity so proactive messages don't interrupt awkwardly.

## Deployment & Production Hardening

### Option A: Docker Compose (Recommended)

```bash
# 1. Build and run in background with automatic restart
docker compose up -d --build

# 2. View real-time logs
docker compose logs -f

# 3. Stop
docker compose down
```

- **Restart policy:** `restart: unless-stopped` ensures the bot automatically comes back up after crashes or host reboots.
- **Log rotation:** Docker daemon logs are capped to 10MB x 3 files.
- **Volumes:** `data/` is persisted on the host (`./data:/app/data`), while `config/` and `prompts/` are mounted read-only for hot-reloading without rebuilding.

### Option B: Linux VPS with Systemd

A production-ready systemd unit is provided at `deploy/companion.service`.

```bash
# 1. Copy unit file
sudo cp deploy/companion.service /etc/systemd/system/companion.service

# 2. Reload and enable service
sudo systemctl daemon-reload
sudo systemctl enable --now companion

# 3. Check status & logs
sudo systemctl status companion
sudo journalctl -u companion -f
```

Security sandboxing is enabled by default (`ProtectSystem=full`, `PrivateTmp=true`, `NoNewPrivileges=true`).

### Backups & Disaster Recovery

- **Automated backups:** Scheduled every day at 03:00 WIB via `app/memory/backup.py`. It uses SQLite's online backup API (safe with WAL mode) and saves timestamped files to `data/backups/companion_backup_YYYYMMDD_HHMMSS.db`.
- **Automatic pruning:** Backups older than `14` days (`keep_days` in `config.yaml`) are automatically pruned.
- **Restore utility:** Use `deploy/restore.py` for disaster recovery:

```bash
# List available backups
python -m deploy.restore --list

# Restore the most recent backup
python -m deploy.restore --latest

# Restore a specific backup file
python -m deploy.restore --file data/backups/companion_backup_20261007_084629.db
```
*(Before restoring, a pre-restore safety copy of the current live database is created automatically).*

## Layout

```
app/            main.py, config.py, gateway/, engine/, memory/, proactive/, life/, utils/
config/         config.yaml (behavior), persona.yaml (her personality)
prompts/        Indonesian prompt templates (*.id.md)
deploy/         companion.service (systemd), restore.py (disaster recovery)
data/           runtime data (gitignored): SQLite DB, companion.log, backups/
tests/          pytest test suite
```

## Tests

```powershell
pytest
```

