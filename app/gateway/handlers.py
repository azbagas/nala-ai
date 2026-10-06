"""Telegram handlers (PRD §6.2, §8.1, §4.6).

Handles owner authentication, commands (/start, /ping, /reload_persona, /memory,
/forget, /reset_memory, /run_nightly, /clear), message debouncing, and routing
user chat to the conversation engine.
"""

from __future__ import annotations

import logging
from typing import Any

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
from app.memory.extractor import MemoryExtractor
from app.memory.store import MemoryStore
from app.memory.summarizer import ConversationSummarizer
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


def _store(ctx: ContextTypes.DEFAULT_TYPE) -> MemoryStore:
    return ctx.application.bot_data["store"]


def _extractor(ctx: ContextTypes.DEFAULT_TYPE) -> MemoryExtractor:
    return ctx.application.bot_data["extractor"]


def _summarizer(ctx: ContextTypes.DEFAULT_TYPE) -> ConversationSummarizer:
    return ctx.application.bot_data["summarizer"]


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
    if "store" in ctx.application.bot_data and chat_id:
        await _store(ctx).set_state("owner_chat_id", str(chat_id))

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
        if "summarizer" in ctx.application.bot_data:
            _summarizer(ctx).set_persona(persona)
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
    await update.effective_message.reply_text("Riwayat obrolan aktif di-reset ya 🧹")


async def view_memory(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """View stored facts and pending threads (FR-3.7)."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    store = _store(ctx)
    facts = await store.get_active_facts()
    threads = await store.get_pending_threads()
    summary = await store.get_latest_summary()

    parts = [f"🧠 <b>Memori {settings.persona.name} tentang kamu:</b>\n"]

    if facts:
        parts.append("📋 <b>Fakta yang diingat:</b>")
        for f in facts:
            parts.append(f"• <code>[#{f.id}]</code> <i>[{f.category}]</i> {f.content}")
    else:
        parts.append("📋 <b>Fakta:</b> <i>(Belum ada fakta tercatat)</i>")

    parts.append("")
    if threads:
        parts.append("📌 <b>Hal yang perlu ditindaklanjuti:</b>")
        for t in threads:
            tgt = f" <i>(target: {t.target_date})</i>" if t.target_date else ""
            parts.append(f"• <code>[#{t.id}]</code> {t.topic}{tgt}")
    else:
        parts.append("📌 <b>Thread terbuka:</b> <i>(Tidak ada topik tertunda)</i>")

    if summary:
        parts.append("")
        parts.append(f"📝 <b>Ringkasan terakhir:</b>\n<i>{summary.content}</i>")

    parts.append("\n💡 <i>Gunakan /forget &lt;id&gt; untuk menghapus fakta.</i>")
    await update.effective_message.reply_text("\n".join(parts), parse_mode="HTML")


async def forget_fact(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Deactivate a fact by ID (FR-3.7)."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    if not ctx.args:
        await update.effective_message.reply_text("Format: /forget <id_fakta>\nContoh: /forget 1")
        return

    try:
        fact_id = int(ctx.args[0])
    except ValueError:
        await update.effective_message.reply_text("ID fakta harus berupa angka.")
        return

    store = _store(ctx)
    success = await store.deactivate_fact(fact_id)
    if success:
        await update.effective_message.reply_text(f"Fakta #{fact_id} sudah dilupakan ya 🗑️")
    else:
        await update.effective_message.reply_text(f"Fakta #{fact_id} tidak ditemukan atau sudah tidak aktif.")


async def reset_memory_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Wipe memory after explicit confirmation (FR-3.7)."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    arg = ctx.args[0].lower() if ctx.args else ""
    if arg not in ("confirm", "ya", "yes"):
        await update.effective_message.reply_text(
            "⚠️ <b>Peringatan:</b> Ini akan menghapus SEMUA fakta yang diingat, thread, ringkasan, dan riwayat obrolan.\n\n"
            "Ketik <code>/reset_memory confirm</code> jika yakin.",
            parse_mode="HTML",
        )
        return

    store = _store(ctx)
    await store.reset_memory()
    _engine(ctx).clear_history()
    await update.effective_message.reply_text("Semua memori dan riwayat percakapan telah dihapus bersih 🧹")


async def run_nightly_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger the nightly memory extraction & summarization manually (FR-3.6)."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    await update.effective_message.reply_text("🌙 Menjalankan ekstraksi memori dan ringkasan...")

    extractor = _extractor(ctx)
    summarizer = _summarizer(ctx)

    ext_summary = await extractor.extract_new_memories()
    daily_sum = await summarizer.summarize_day()

    lines = [
        "✅ <b>Proses memori selesai!</b>",
        f"• Pesan dianalisis: {ext_summary.processed_messages}",
        f"• Fakta baru: {ext_summary.new_facts_count}",
        f"• Fakta diperbarui: {ext_summary.updated_facts_count}",
        f"• Thread baru: {ext_summary.new_threads_count}",
        f"• Thread terselesaikan: {ext_summary.resolved_threads_count}",
        f"• Ringkasan harian: {'Dibuat' if daily_sum else 'Tidak ada obrolan baru'}",
    ]
    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


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
    app.add_handler(CommandHandler("memory", view_memory))
    app.add_handler(CommandHandler("forget", forget_fact))
    app.add_handler(CommandHandler("reset_memory", reset_memory_command))
    app.add_handler(CommandHandler(["run_nightly", "extract_memory"], run_nightly_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    app.add_error_handler(on_error)
