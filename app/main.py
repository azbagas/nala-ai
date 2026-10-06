"""Entrypoint.

    python -m app.main           # run the bot (long polling)
    python -m app.main --check   # validate config + Telegram + Gemini connectivity
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from telegram import Bot, Update
from telegram.ext import Application

from app.config import ConfigError, Settings, load_settings
from app.engine.conversation import ConversationEngine
from app.engine.gemini_client import GeminiClient, GeminiError
from app.engine.prompt_builder import PromptBuilder
from app.gateway.debouncer import MessageDebouncer
from app.gateway.handlers import create_on_flush_callback, register_handlers
from app.utils.logging import setup_logging
from app.utils.time import format_local_id, now_local

log = logging.getLogger("app.main")


async def run_checks(settings: Settings) -> bool:
    ok = True
    cfg = settings.config
    print(f"Config OK   persona={settings.persona.name} tz={cfg.app.timezone} "
          f"now={format_local_id(now_local(settings.tz))}")
    print(f"Owner ID    {cfg.app.owner_telegram_id or 'NOT SET (send /start to the bot to get it)'}")

    # 1. Test Prompt Builder
    try:
        builder = PromptBuilder(settings.prompts_dir, settings.persona, settings.tz)
        system_prompt = builder.build_system_prompt()
        print(f"Prompt OK   system_persona rendered ({len(system_prompt)} chars)")
    except Exception as exc:
        ok = False
        print(f"Prompt FAIL {exc!r}")

    # 2. Test Telegram Bot
    try:
        async with Bot(settings.secrets.telegram_bot_token) as bot:
            me = await bot.get_me()
        print(f"Telegram OK @{me.username} (id={me.id})")
    except Exception as exc:
        ok = False
        print(f"Telegram FAIL {exc!r}")

    # 3. Test Gemini API
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
    log.info("bot_started username=@%s persona=%s owner_configured=%s",
             me.username, settings.persona.name, settings.config.app.owner_telegram_id is not None)


def run_bot(settings: Settings) -> None:
    app = (
        Application.builder()
        .token(settings.secrets.telegram_bot_token)
        .post_init(_post_init)
        .build()
    )

    gemini = GeminiClient(settings.secrets.gemini_api_key, settings.config.llm)
    prompt_builder = PromptBuilder(settings.prompts_dir, settings.persona, settings.tz)
    engine = ConversationEngine(
        gemini=gemini,
        prompt_builder=prompt_builder,
        llm_cfg=settings.config.llm,
        conv_cfg=settings.config.conversation,
    )

    on_flush = create_on_flush_callback(app)
    debouncer = MessageDebouncer(
        debounce_seconds=settings.config.conversation.debounce_seconds,
        on_flush=on_flush,
    )

    app.bot_data["settings"] = settings
    app.bot_data["gemini"] = gemini
    app.bot_data["prompt_builder"] = prompt_builder
    app.bot_data["engine"] = engine
    app.bot_data["debouncer"] = debouncer

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
