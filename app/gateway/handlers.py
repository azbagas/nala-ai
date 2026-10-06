"""Telegram handlers (PRD §6.2, §8.1).

Handles owner authentication, commands (/start, /ping, /reload_persona, /clear),
message debouncing, and routing user chat to the conversation engine.
"""

from __future__ import annotations

import logging

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.config import ConfigError, Settings
from app.engine.conversation import ConversationEngine, FALLBACK_REPLY
from app.engine.gemini_client import GeminiClient, GeminiError
from app.engine.prompt_builder import PromptBuilder
from app.gateway.debouncer import MessageDebouncer
from app.gateway.sender import send_bubbles
from app.utils.time import format_local_id, now_local

log = logging.getLogger(__name__)


def _settings(ctx: ContextTypes.DEFAULT_TYPE) -> Settings:
    return ctx.application.bot_data["settings"]


def _gemini(ctx: ContextTypes.DEFAULT_TYPE) -> GeminiClient:
    return ctx.application.bot_data["gemini"]


def _engine(ctx: ContextTypes.DEFAULT_TYPE) -> ConversationEngine:
    return ctx.application.bot_data["engine"]


def _prompt_builder(ctx: ContextTypes.DEFAULT_TYPE) -> PromptBuilder:
    return ctx.application.bot_data["prompt_builder"]


def _debouncer(ctx: ContextTypes.DEFAULT_TYPE) -> MessageDebouncer:
    return ctx.application.bot_data["debouncer"]


def _is_owner(update: Update, settings: Settings) -> bool:
    owner_id = settings.config.app.owner_telegram_id
    user = update.effective_user
    return owner_id is not None and user is not None and user.id == owner_id


async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    user = update.effective_user
    if user is None or update.effective_message is None:
        return

    # First-run helper: no owner configured yet -> tell the sender their ID.
    if settings.config.app.owner_telegram_id is None:
        log.warning("owner_not_configured start_from_user_id=%s", user.id)
        await update.effective_message.reply_text(
            f"ID Telegram kamu: {user.id}\n"
            "Masukin angka ini ke OWNER_ID di file .env, lalu restart bot ya."
        )
        return

    if not _is_owner(update, settings):
        log.info("ignored_non_owner user_id=%s command=start", user.id)
        return

    chat_id = update.effective_chat.id if update.effective_chat else None
    log.info("owner_start chat_id=%s", chat_id)
    p = settings.persona
    await update.effective_message.reply_text(
        f"Haii! Aku {p.name} 👋 Senang bisa ngobrol sama kamu. Hari ini ada cerita apa?"
    )


async def ping(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    await update.effective_chat.send_action("typing")
    now = format_local_id(now_local(settings.tz))
    try:
        result = await _gemini(ctx).generate(
            f"Sekarang {now} WIB. Balas dengan satu kalimat sapaan santai dalam Bahasa Indonesia.",
            model=settings.config.llm.utility_model,
            temperature=settings.config.llm.temperature_chat,
            purpose="ping",
        )
    except GeminiError as exc:
        log.error("ping_failed error=%r", exc)
        await update.effective_message.reply_text("Gemini lagi gak bisa dihubungi 😅 cek log ya.")
        return

    await update.effective_message.reply_text(
        f"{result.text}\n\n"
        f"🟢 model={result.model} | {result.latency_ms} ms | "
        f"tokens in/out={result.input_tokens}/{result.output_tokens}"
    )


async def reload_persona(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return
    try:
        persona = settings.reload_persona()
        _prompt_builder(ctx).set_persona(persona)
    except ConfigError as exc:
        log.error("persona_reload_failed error=%s", exc)
        await update.effective_message.reply_text("Gagal reload persona, file-nya ada yang salah. Cek log ya.")
        return
    log.info("persona_reloaded name=%s", persona.name)
    await update.effective_message.reply_text(f"Persona di-reload ✅ (sekarang: {persona.name})")


async def clear_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return
    _engine(ctx).clear_history()
    await update.effective_message.reply_text("Riwayat obrolan kita udah di-reset ya 🧹")


async def text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    settings = _settings(ctx)
    user = update.effective_user
    if not _is_owner(update, settings):
        log.info("ignored_non_owner user_id=%s", user.id if user else None)
        return

    msg = update.effective_message
    if msg is None or not msg.text:
        return

    chat_id = update.effective_chat.id if update.effective_chat else user.id
    log.info("inbound_message user_id=%s chat_id=%s chars=%d", user.id, chat_id, len(msg.text))

    # Feed incoming text into the debouncer
    debouncer = _debouncer(ctx)
    await debouncer.add_message(user_id=user.id, chat_id=chat_id, text=msg.text)


async def on_error(update: object, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    log.exception("handler_error", exc_info=ctx.error)


def create_on_flush_callback(app: Application):
    """Factory creating the callback executed when debounced messages are flushed."""

    async def on_flush(chat_id: int, user_id: int, combined_text: str) -> None:
        settings: Settings = app.bot_data["settings"]
        engine: ConversationEngine = app.bot_data["engine"]

        try:
            bubbles = await engine.reply(combined_text)
            if not bubbles:
                bubbles = [FALLBACK_REPLY]

            await send_bubbles(
                bot=app.bot,
                chat_id=chat_id,
                bubbles=bubbles,
                typing_cfg=settings.config.conversation.typing_delay,
                simulate_typing=True,
            )
        except Exception as exc:
            log.exception("flush_processing_failed chat_id=%s error=%r", chat_id, exc)
            try:
                await app.bot.send_message(chat_id=chat_id, text=FALLBACK_REPLY)
            except Exception as send_err:
                log.error("fallback_send_failed error=%r", send_err)

    return on_flush


def register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("reload_persona", reload_persona))
    app.add_handler(CommandHandler(["clear", "reset"], clear_history))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    app.add_error_handler(on_error)
