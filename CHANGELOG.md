# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [1.0.0] - 2026-10-15

### Added
- **Core Chat & Telegram Gateway**:
  - Single-user Telegram bot integration using `python-telegram-bot` with strict owner ID authentication.
  - Message debouncing (`MessageDebouncer`) with a 4-second sliding window to batch rapid user messages.
  - Human-like typing indicators and delays proportional to message character counts.
  - Multi-bubble response formatting (`split_bubbles`) splitting long replies into natural, timed bubbles.
  - Non-text message handler supporting polite, in-character acknowledgement of stickers, photos, voice notes, and documents.
- **Engine & Personality**:
  - Google Gemini client integration via `google-genai` with automatic retry handling and timeout protection.
  - Dynamic prompt builder generating system instructions from persona configuration, memory facts, open threads, and recent conversation.
  - Natural Jakarta-Indonesian conversational persona with lowercase styling, particle usage, and vowel elongations.
  - Hot-reloading capability for `config/persona.yaml` via `/reload_persona` command without restarting the service.
  - Natural language intent parser detecting pause and resume requests (e.g., *"aku mau tidur dulu ya"*, *"lagi sibuk 2 jam"*).
- **Proactive Outreach System**:
  - Autonomous outreach planner (`planner.py`) creating daily window dispatches (morning, midday, afternoon, evening, and bonus windows).
  - Pre-send validation checks respecting quiet hours (22:00–06:00 WIB), recent user activity, and backoff when previous outreach was left unread.
  - Dynamic proactive controls: `/pause`, `/resume`, `/frequency` (low/normal/high), and `/test_proactive`.
- **Memory & Life Simulation**:
  - SQLite persistence layer using `aiosqlite` configured with Write-Ahead Logging (WAL).
  - Automated nightly memory extraction job (23:30 WIB) extracting user profile facts with duplicate superseding.
  - Open thread tracker maintaining contextual follow-up topics with expiration policies.
  - Rolling conversation summarizer generating daily and 7-day conversation recaps.
  - Daily life simulator (05:30 WIB) generating schedule routines, mood, and energy levels for persona realism.
  - Memory management commands: `/memory`, `/forget <id>`, and `/reset_memory confirm`.
- **Safety & Wellbeing**:
  - Emotional distress and crisis detector scanning incoming messages for self-harm or hopelessness cues.
  - Empathetic crisis response system directing users to verified Indonesian crisis resources (Layanan Sejiwa 119 ext. 8).
- **Deployment & Disaster Recovery**:
  - Automated daily online SQLite backup job (03:00 WIB) with 14-day retention pruning.
  - Interactive disaster recovery CLI (`deploy/restore.py`) supporting list, latest, and specific snapshot restoration with automatic pre-restore safety copies.
  - Pre-flight diagnostic checker (`python -m app.main --check`) validating configuration, SQLite connection, Telegram bot token, and Gemini API access.
  - Production Dockerfile and `docker-compose.yml` with host volume persistence and log rotation.
  - Hardened Linux systemd service unit (`deploy/companion.service`).
  - Comprehensive Pytest test suite with 113 passing tests.

[Unreleased]: https://github.com/azbagas/nala-ai/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/azbagas/nala-ai/releases/tag/v1.0.0
