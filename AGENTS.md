# AGENTS.md

## 1. Project Overview
Single-user proactive AI companion bot for Telegram speaking casual Indonesian, powered by Google Gemini and SQLite. Built with Python 3.12+, `python-telegram-bot`, `google-genai`, `APScheduler`, and `aiosqlite`. Implements autonomous messaging windows, memory extraction, open thread tracking, and simulated daily life routines.

## 2. Essential Commands
Always use the virtual environment Python interpreter (`.venv/bin/python` on Linux or `.\.venv\Scripts\python.exe` on Windows):
- **Setup/Install**:
  - Windows: `python -m venv .venv && .\.venv\Scripts\Activate.ps1 && pip install -r requirements-dev.txt`
  - Linux: `python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements-dev.txt`
- **Build / Syntax Check**:
  - `python -m compileall app tests deploy`
- **Health / Connectivity Check**:
  - `python -m app.main --check` (validates config, SQLite, Telegram token, and Gemini models)
- **Run Bot**:
  - `python -m app.main`
- **Test Suite**:
  - Run all tests: `python -m pytest`
  - Run single test file: `python -m pytest tests/test_conversation.py`
  - Run single test case: `python -m pytest tests/test_conversation.py -k test_sanitize_text`
  - Run with quiet output: `python -m pytest -q`
- **Disaster Recovery / Backups**:
  - List backups: `python -m deploy.restore --list`
  - Restore latest: `python -m deploy.restore --latest`
  - Restore specific file: `python -m deploy.restore --file data/backups/<backup_file>.db`

## 3. Architecture & Directory Map
- `app/main.py`: Entrypoint, CLI parsing (`--check`), APScheduler lifecycle, Telegram long-polling.
- `app/config.py`: Pydantic v2 settings loader for `config.yaml` + `persona.yaml` with `${VAR:-default}` env expansion.
- `app/gateway/`: Telegram I/O layer (`handlers.py`, `debouncer.py`, `sender.py`). Authenticates owner; routes incoming messages; splits text into timed bubbles. No direct DB mutations or prompt logic.
- `app/engine/`: LLM conversation pipeline (`conversation.py`, `prompt_builder.py`, `gemini_client.py`, `intent.py`, `safety.py`). Handles fallback replies, text sanitization, distress detection, and Gemini retries.
- `app/memory/`: Async persistence & offline jobs (`store.py`, `extractor.py`, `summarizer.py`, `backup.py`). SQLite WAL store (`messages`, `facts`, `open_threads`, `summaries`, `daily_life`, `proactive_plan`, `state`, `llm_calls`), nightly extraction, rolling summaries, online backup.
- `app/proactive/`: Outreach scheduler (`planner.py`, `checks.py`, `sender_job.py`). Generates daily windows, evaluates pre-send checks, tracks anti-repetition and ignored-message backoff.
- `app/life/`: World simulator (`simulator.py`). Generates her daily routine, mood, and energy levels.
- `app/utils/`: Common utilities (`logging.py` for rotating file logs, `time.py` for UTC/WIB conversions).
- `config/`: `config.yaml` (runtime options), `persona.yaml` (traits, speaking style, boundaries).
- `prompts/`: Indonesian prompt templates (`*.id.md`).
- `deploy/`: Systemd service unit (`companion.service`) and disaster recovery tool (`restore.py`).
- `data/`: Runtime directory (gitignored): SQLite DB (`companion.db`), rotating logs (`companion.log`), and `backups/`.
- `tests/`: Pytest suite (`test_*.py`).

**Module Boundary Rules**:
- `gateway` handlers must delegate to `engine`, `memory`, or `proactive`; do not put SQL or LLM prompt formatting in handlers.
- `engine` remains decoupled from Telegram types (operates strictly on text and dataclasses).
- Offline jobs (`extractor`, `summarizer`, `simulator`) accept injected dependencies (`GeminiClient`, `MemoryStore`), never global singletons.

## 4. Code Style & Conventions
- **Imports**: Start every `.py` file with `from __future__ import annotations`. Use absolute imports from root (e.g., `from app.config import Settings`, `from app.memory.store import MemoryStore`). Do not use relative parent imports (`..`).
- **Typing**: Use standard Python 3.12+ union types (`int | None`, `dict[str, Any]`, `Sequence[str]`). Validate configuration and structured outputs using Pydantic (`ConfigDict(extra="forbid")`).
- **Naming**: `snake_case` for modules, functions, and variables; `PascalCase` for classes and Pydantic models; `UPPER_CASE` for constants. DB tables and columns use `snake_case`.
- **Logging**: Use `log = logging.getLogger(__name__)`. Format key=value style: `log.info("event_name key=%s count=%d", k, v)`. Never log API keys, bot tokens, or raw secrets. (httpx and httpcore are restricted to WARNING).
- **Error Handling**: Use custom typed exceptions (`ConfigError`, `GeminiError`). Catch external network/API errors gracefully with fallbacks (`FALLBACK_REPLY`). Never allow unhandled exceptions to crash the polling loop.
- **Timezone & Dates**: Store all database timestamps in UTC ISO format via `to_utc_iso()` / `now_utc()`. Process user interactions, quiet hours, and daily job schedules in local time (`Asia/Jakarta` / WIB) using `now_local(tz)`.
- **Persona Agnosticism**: Never hardcode persona name "Nala" in prompts, sanitizers, or extractors; read dynamically from `persona.name` or pass `speaker_names`.

## 5. Testing Guidelines
- **Location**: Top-level `tests/` directory; files named `test_<module>.py`.
- **Configuration**: Standard `pytest.ini` with `asyncio_mode = auto`.
- **Fixtures & Isolation**: Use the `tmp_path` fixture for SQLite databases (`MemoryStore(tmp_path / "test.db")`). Never run tests against live `data/companion.db`.
- **Mocking**: Mock external services (Gemini API, Telegram bot/context) using `unittest.mock.AsyncMock` or `unittest.mock.MagicMock`.
- **Time in Tests**: Never hardcode static past calendar dates for assertions (causes date-drift test failures). Use dynamic dates via `now_utc()` or mock `now`.

## 6. Critical Guardrails & "Do Not Touch" Zones
- **Git Commits**: Only create git commits if explicitly asked by the user. Never commit autonomously.
- **Documentation Maintenance**: Always update `AGENTS.md` and `README.md` whenever changes affect setup, commands, architecture, configuration, or operational workflows.
- **Live Data Directory (`data/`)**: Never manually edit or delete `data/companion.db`, WAL files, logs, or backups. Test restore operations only against temp files.
- **Secrets & Environment**: Never commit `.env`. When adding new configuration keys, add commented placeholder entries to `.env.example` and update Pydantic models in `app/config.py`.
- **SQLite Schema Changes**: Modify `SCHEMA_SQL` in `app/memory/store.py` using backward-compatible `IF NOT EXISTS` syntax or non-destructive migrations. The DB runs in WAL mode; use SQLite online backup API (`backup.py`) rather than copying open database files while running.
- **Owner Authentication**: Every gateway handler must check `_is_owner(update, settings)`. Non-owner messages must be ignored silently (except `/start` when `OWNER_ID` is unconfigured to assist initial setup).

## 7. Verification Checklist (Definition of Done)
Before marking any task complete, run and pass:
1. Syntax / compile check: `python -m compileall app tests deploy`
2. Targeted single test: `python -m pytest tests/test_<module>.py -k <test_name>`
3. Full test suite: `python -m pytest` (all 113+ tests must pass with 0 failures)
4. Config & connectivity check: `python -m app.main --check` (when credentials are present)
5. Documentation sync: Update `AGENTS.md` and `README.md` if the change requires documentation updates.
6. Git check: Verify `git status` contains no untracked `.db`, `.log`, or `.env` files.
