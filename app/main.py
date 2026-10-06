"""Entrypoint.

    python -m app.main           # run the bot (long polling)
    python -m app.main --check   # validate config + SQLite + Telegram + Gemini connectivity
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import timedelta
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from telegram import Bot, Update
from telegram.ext import Application

from app.config import ConfigError, Settings, load_settings
from app.engine.conversation import ConversationEngine
from app.engine.gemini_client import GeminiClient, GeminiError
from app.engine.prompt_builder import PromptBuilder
from app.gateway.debouncer import MessageDebouncer
from app.gateway.handlers import create_on_flush_callback, register_handlers
from app.memory.extractor import MemoryExtractor
from app.memory.store import MemoryStore
from app.memory.summarizer import ConversationSummarizer
from app.utils.logging import setup_logging
from app.utils.time import format_local_id, now_local

log = logging.getLogger("app.main")


async def run_nightly_job(
    extractor: MemoryExtractor,
    summarizer: ConversationSummarizer,
    store: MemoryStore,
    tz: ZoneInfo,
    expire_days: int,
    rolling_days: int,
) -> None:
    """Scheduled nightly job: extracts facts, expires stale threads, writes summaries (PRD §8.4)."""
    log.info("nightly_memory_job_started")
    try:
        ext_summary = await extractor.extract_new_memories()

        # Expire stale threads
        now_dt = now_local(tz)
        cutoff_date = (now_dt - timedelta(days=expire_days)).strftime("%Y-%m-%d")
        expired_count = await store.expire_threads_before(cutoff_date)

        # Write daily summary
        daily_summary = await summarizer.summarize_day()

        # Check rolling summary
        rolling_summary = await summarizer.check_and_create_rolling_summary(every_days=rolling_days)

        log.info(
            "nightly_memory_job_completed processed=%d new_facts=%d new_threads=%d expired_threads=%d daily=%s rolling=%s",
            ext_summary.processed_messages,
            ext_summary.new_facts_count,
            ext_summary.new_threads_count,
            expired_count,
            bool(daily_summary),
            bool(rolling_summary),
        )
    except Exception as exc:
        log.exception("nightly_memory_job_failed error=%r", exc)


async def run_checks(settings: Settings) -> bool:
    ok = True
    cfg = settings.config
    print(f"Config OK   persona={settings.persona.name} tz={cfg.app.timezone} "
          f"now={format_local_id(now_local(settings.tz))}")
    print(f"Owner ID    {cfg.app.owner_telegram_id or 'NOT SET (send /start to the bot to get it)'}")

    # 1. Test SQLite Memory Store
    try:
        store = MemoryStore(settings.db_path)
        await store.init_db()
        await store.set_state("check_ping", "ok")
        val = await store.get_state("check_ping")
        assert val == "ok"
        print(f"Database OK {settings.db_path}")
    except Exception as exc:
        ok = False
        print(f"Database FAIL {exc!r}")

    # 2. Test Prompt Builder
    try:
        builder = PromptBuilder(settings.prompts_dir, settings.persona, settings.tz)
        system_prompt = builder.build_system_prompt()
        print(f"Prompt OK   system_persona rendered ({len(system_prompt)} chars)")
    except Exception as exc:
        ok = False
        print(f"Prompt FAIL {exc!r}")

    # 3. Test Telegram Bot
    try:
        async with Bot(settings.secrets.telegram_bot_token) as bot:
            me = await bot.get_me()
        print(f"Telegram OK @{me.username} (id={me.id})")
    except Exception as exc:
        ok = False
        print(f"Telegram FAIL {exc!r}")

    # 4. Test Gemini API
    gemini = GeminiClient(settings.secrets.gemini_api_key, cfg.llm)
    for label, model in (("chat", cfg.llm.chat_model), ("utility", cfg.llm.utility_model)):
        try:
            r = await gemini.generate("Balas dengan satu kata: halo", model=model, purpose="check")
            print(f"Gemini OK   {label}={model} {r.latency_ms}ms reply={r.text[:40]!r}")
        except GeminiError as exc:
            ok = False
            print(f"Gemini FAIL {label}={model} {exc}")
    return ok


async def _post_init(app: Application) -> None:
    me = await app.bot.get_me()
    settings: Settings = app.bot_data["settings"]
    store: MemoryStore = app.bot_data["store"]
    await store.init_db()

    # Start APScheduler for nightly jobs
    scheduler: AsyncIOScheduler = app.bot_data["scheduler"]
    scheduler.start()

    log.info("bot_started username=@%s persona=%s owner_configured=%s",
             me.username, settings.persona.name, settings.config.app.owner_telegram_id is not None)


def run_bot(settings: Settings) -> None:
    app = (
        Application.builder()
        .token(settings.secrets.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )

    store = MemoryStore(settings.db_path)
    gemini = GeminiClient(settings.secrets.gemini_api_key, settings.config.llm, store=store)
    prompt_builder = PromptBuilder(settings.prompts_dir, settings.persona, settings.tz)
    engine = ConversationEngine(
        gemini=gemini,
        prompt_builder=prompt_builder,
        llm_cfg=settings.config.llm,
        conv_cfg=settings.config.conversation,
        store=store,
    )

    extractor = MemoryExtractor(
        gemini=gemini,
        store=store,
        prompts_dir=settings.prompts_dir,
        llm_cfg=settings.config.llm,
        tz=settings.tz,
    )
    summarizer = ConversationSummarizer(
        gemini=gemini,
        store=store,
        prompts_dir=settings.prompts_dir,
        persona=settings.persona,
        llm_cfg=settings.config.llm,
        tz=settings.tz,
    )

    on_flush = create_on_flush_callback(app)
    debouncer = MessageDebouncer(
        debounce_seconds=settings.config.conversation.debounce_seconds,
        on_flush=on_flush,
    )

    # APScheduler for nightly jobs
    scheduler = AsyncIOScheduler(timezone=settings.config.app.timezone)
    hh, mm = settings.config.memory.nightly_job_time.split(":")
    scheduler.add_job(
        run_nightly_job,
        trigger="cron",
        hour=int(hh),
        minute=int(mm),
        args=[
            extractor,
            summarizer,
            store,
            settings.tz,
            settings.config.memory.thread_expire_days,
            settings.config.memory.rolling_summary_every_days,
        ],
        id="nightly_memory_job",
        replace_existing=True,
    )

    app.bot_data["settings"] = settings
    app.bot_data["store"] = store
    app.bot_data["gemini"] = gemini
    app.bot_data["prompt_builder"] = prompt_builder
    app.bot_data["engine"] = engine
    app.bot_data["extractor"] = extractor
    app.bot_data["summarizer"] = summarizer
    app.bot_data["debouncer"] = debouncer
    app.bot_data["scheduler"] = scheduler

    register_handlers(app)
    app.run_polling(allowed_updates=Update.ALL_TYPES, drop_pending_updates=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Proactive AI Companion Bot")
    parser.add_argument("--check", action="store_true", help="validate config and API connectivity, then exit")
    args = parser.parse_args()

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2

    setup_logging(settings.config.app.log_level, settings.log_dir)

    if args.check:
        return 0 if asyncio.run(run_checks(settings)) else 1

    run_bot(settings)
    return 0


if __name__ == "__main__":
    sys.exit(main())
