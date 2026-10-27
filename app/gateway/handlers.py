"""Telegram handlers.

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

from datetime import datetime, timedelta, timezone

from app.config import ConfigError, Settings
from app.engine.conversation import ConversationEngine, FALLBACK_REPLY
from app.engine.gemini_client import GeminiClient, GeminiError
from app.engine.prompt_builder import PromptBuilder
from app.gateway.debouncer import MessageDebouncer
from app.gateway.sender import send_bubbles
from app.memory.extractor import MemoryExtractor
from app.memory.store import MemoryStore
from app.memory.summarizer import ConversationSummarizer
from app.utils.time import format_local_id, from_utc_iso, now_local, to_utc_iso

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


def _life_simulator(ctx: ContextTypes.DEFAULT_TYPE) -> Any:
    return ctx.application.bot_data.get("life_simulator")


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
    store = _store(ctx) if "store" in ctx.application.bot_data else None
    user_name = await store.get_user_name() if store and hasattr(store, "get_user_name") else None
    if user_name:
        await update.effective_message.reply_text(
            f"haii {user_name}! aku {p.name} 👋 ada cerita apa nih hari ini?"
        )
    else:
        await update.effective_message.reply_text(
            f"haii! aku {p.name} 👋 salam kenal yaa. btw boleh panggil kamu siapa nihh?"
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
        if "life_simulator" in ctx.application.bot_data and _life_simulator(ctx):
            _life_simulator(ctx).set_persona(persona)
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
    """View stored facts and pending threads."""
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
    """Deactivate a fact by ID."""
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
    """Wipe memory after explicit confirmation."""
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


async def set_name_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually set or update the user's name/nickname."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or not update.effective_message:
        return
    text = update.effective_message.text or ""
    parts = text.strip().split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        await update.effective_message.reply_text("formatnya: /setname <nama kamu>\ncontoh: /setname Bagas")
        return
    name = parts[1].strip()
    store = _store(ctx)
    await store.set_user_name(name)
    await update.effective_message.reply_text(f"okee, mulai sekarang aku panggil kamu {name} yaa! 🥰")


async def run_nightly_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Trigger the nightly memory extraction & summarization manually."""
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

    if "store" in ctx.application.bot_data and chat_id:
        try:
            await _store(ctx).set_owner_chat_id(chat_id)
            await _store(ctx).update_last_user_message_time()
            await _store(ctx).reset_ignored_count()
        except Exception as exc:
            log.warning("failed_to_save_owner_chat_id error=%r", exc)

    # Feed incoming text into the debouncer
    debouncer = _debouncer(ctx)
    await debouncer.add_message(user_id=user.id, chat_id=chat_id, text=msg.text)


async def status_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Shows today's plan, counters, and pause state."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    store = _store(ctx)
    now_dt = now_local(settings.tz)
    today_str = now_dt.strftime("%Y-%m-%d")

    plans = await store.get_plans_for_date(today_str)
    ignored = await store.get_ignored_count()
    paused_until = await store.get_paused_until()
    last_user_msg = await store.get_last_user_message_time()

    lines = [
        f"📊 <b>Status Proaktif {settings.persona.name}</b>",
        f"📅 Hari ini: <code>{today_str}</code> (WIB)\n",
        "⏰ <b>Jadwal Pesan Proaktif:</b>",
    ]

    if plans:
        for p in plans:
            planned_local = from_utc_iso(p.planned_at).astimezone(settings.tz)
            status_badge = {
                "planned": "⏳ direncanakan",
                "sent": "✅ terkirim",
                "skipped": f"⏭️ dilewati ({p.skip_reason or 'unknown'})",
                "failed": f"❌ gagal ({p.skip_reason or 'unknown'})",
            }.get(p.status, p.status)
            lines.append(f"• <code>{planned_local:%H:%M}</code> [{p.window_name}]: {status_badge}")
    else:
        lines.append("<i>(Belum ada jadwal yang dibuat untuk hari ini)</i>")

    lines.append("\n⚙️ <b>Status Sistem:</b>")
    lines.append(f"• Pesan diabaikan berturut-turut: <code>{ignored}</code>")
    if paused_until:
        paused_local = paused_until.astimezone(settings.tz)
        if paused_local > now_dt:
            lines.append(f"• Jeda aktif sampai: <code>{paused_local:%H:%M} WIB</code>")
        else:
            lines.append("• Jeda aktif: <i>Tidak</i>")
    else:
        lines.append("• Jeda aktif: <i>Tidak</i>")

    freq = await store.get_state("proactive_frequency", default="normal")
    lines.append(f"• Frekuensi proaktif: <code>{freq}</code>")

    if last_user_msg:
        last_local = last_user_msg.astimezone(settings.tz)
        lines.append(f"• Obrolan kamu terakhir: <code>{last_local:%H:%M} WIB</code>")
    else:
        lines.append("• Obrolan kamu terakhir: <i>(Belum ada)</i>")

    # Observability & token stats
    try:
        now_utc_str = now_local(settings.tz).astimezone(timezone.utc).strftime("%Y-%m-%d")
        llm_stats = await store.get_today_llm_stats(now_utc_str)
        if llm_stats["total_calls"] > 0:
            lines.append("\n📈 <b>Statistik Model & Biaya Hari Ini:</b>")
            lines.append(f"• Total panggilan LLM: <code>{llm_stats['total_calls']}</code>")
            lines.append(f"• Token input / output: <code>{llm_stats['total_input_tokens']}</code> / <code>{llm_stats['total_output_tokens']}</code>")
            lines.append(f"• Rata-rata latensi: <code>{llm_stats['avg_latency_ms']} ms</code>")
    except Exception as exc:
        log.warning("failed_to_fetch_llm_stats error=%r", exc)

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def frequency_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Adjust proactive messaging frequency: low, normal, high."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    store = _store(ctx)

    if not ctx.args:
        current_freq = await store.get_state("proactive_frequency", default="normal")
        await update.effective_message.reply_text(
            f"🎚️ <b>Frekuensi Pesan Proaktif Saat Ini:</b> <code>{current_freq}</code>\n\n"
            "Gunakan: <code>/frequency &lt;low|normal|high&gt;</code>\n"
            "• <b>low</b>: Lebih santai/jarang (maks 2 pesan/hari)\n"
            "• <b>normal</b>: Standar sesuai config (maks 4 pesan/hari)\n"
            "• <b>high</b>: Lebih sering dan aktif (maks 5 pesan/hari)",
            parse_mode="HTML",
        )
        return

    choice = ctx.args[0].lower()
    if choice not in ("low", "normal", "high"):
        await update.effective_message.reply_text("Pilihan tidak valid. Gunakan: `/frequency low`, `/frequency normal`, atau `/frequency high`.")
        return

    await store.set_state("proactive_frequency", choice)
    labels = {
        "low": "santai / jarang (maks 2x sehari)",
        "normal": "standar (maks 4x sehari)",
        "high": "lebih aktif (maks 5x sehari)",
    }
    await update.effective_message.reply_text(
        f"✅ Frekuensi proaktif diatur ke <b>{choice}</b> ({labels[choice]}).\n"
        "Pengaturan ini akan diterapkan pada penjadwalan berikutnya.",
        parse_mode="HTML",
    )


async def pause_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Stops proactive messages for a duration."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    now_dt = now_local(settings.tz)
    arg = ctx.args[0].lower() if ctx.args else "3h"

    if arg in ("today", "hari_ini", "hariini"):
        target_dt = now_dt.replace(hour=23, minute=59, second=59)
    elif arg.endswith("m"):
        try:
            minutes = int(arg[:-1])
            target_dt = now_dt + timedelta(minutes=minutes)
        except ValueError:
            target_dt = now_dt + timedelta(hours=3)
    elif arg.endswith("h") or arg.endswith("j"):
        try:
            hours = int(arg[:-1])
            target_dt = now_dt + timedelta(hours=hours)
        except ValueError:
            target_dt = now_dt + timedelta(hours=3)
    elif arg.endswith("d") or arg.endswith("hari"):
        try:
            days = int(arg[:-1].replace("hari", ""))
            target_dt = now_dt + timedelta(days=days)
        except ValueError:
            target_dt = now_dt + timedelta(days=1)
    else:
        try:
            hours = int(arg)
            target_dt = now_dt + timedelta(hours=hours)
        except ValueError:
            target_dt = now_dt + timedelta(hours=3)

    store = _store(ctx)
    await store.set_paused_until(target_dt)
    await update.effective_message.reply_text(
        f"Oke! Aku gak bakal chat duluan sampai <b>{target_dt:%H:%M} WIB</b> ya ⏸️",
        parse_mode="HTML",
    )


async def resume_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Resumes proactive messages."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    store = _store(ctx)
    await store.set_paused_until(None)
    await update.effective_message.reply_text("Pesan proaktif diaktifkan kembali ya ▶️")


async def test_proactive_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Manual trigger to test a proactive message immediately."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    chat_id = update.effective_chat.id if update.effective_chat else update.effective_user.id
    store = _store(ctx)
    await store.set_owner_chat_id(chat_id)

    window_name = ctx.args[0] if ctx.args else "manual"
    now_dt = now_local(settings.tz)
    today_str = now_dt.strftime("%Y-%m-%d")

    plan_id = await store.create_plan(today_str, window_name, to_utc_iso(now_dt))
    # Reset status to planned if it already existed
    await store.update_plan_status(plan_id, "planned")

    await update.effective_message.reply_text("🚀 Menjalankan tes pesan proaktif...")

    from app.proactive.sender_job import send_proactive

    sent = await send_proactive(
        plan_id=plan_id,
        bot=ctx.bot,
        store=store,
        gemini=_gemini(ctx),
        prompt_builder=_prompt_builder(ctx),
        proactive_cfg=settings.config.proactive,
        conv_cfg=settings.config.conversation,
        llm_cfg=settings.config.llm,
        tz=settings.tz,
        owner_id=chat_id,
        simulate_typing=True,
    )
    if not sent:
        plan = await store.get_plan_by_id(plan_id)
        reason = plan.skip_reason if plan else "unknown"
        await update.effective_message.reply_text(
            f"⚠️ Pesan proaktif tidak dikirim (status: {plan.status if plan else 'none'}, reason: {reason})"
        )


async def life_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """View or regenerate her simulated daily schedule."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    simulator = _life_simulator(ctx)
    now_dt = now_local(settings.tz)
    today_str = now_dt.strftime("%Y-%m-%d")

    force = False
    if ctx.args and ctx.args[0].lower() in ("generate", "reroll", "buat", "reset"):
        force = True
        await update.effective_message.reply_text("☕ Merancang ulang jadwal hari ini...")

    if simulator:
        rec = await simulator.generate_daily_life(target_date=now_dt.date(), force=force)
    else:
        rec = await _store(ctx).get_daily_life(today_str)

    if not rec:
        await update.effective_message.reply_text("Jadwal hari ini belum dibuat. Coba `/life generate`.")
        return

    from app.life.simulator import resolve_current_activity

    curr_act = resolve_current_activity(rec.schedule, now_dt)

    lines = [
        f"🌸 <b>Jadwal & Kehidupan {settings.persona.name} Hari Ini</b>",
        f"📅 <code>{today_str}</code> | Jam sekarang: <code>{now_dt:%H:%M} WIB</code>\n",
        f"📍 <b>Sedang apa sekarang:</b>\n<i>{curr_act}</i>\n",
        f"✨ <b>Suasana hati:</b> {rec.mood} (energi: {rec.energy})",
    ]
    if rec.notes:
        lines.append(f"📝 <b>Catatan hari ini:</b> <i>{rec.notes}</i>")

    lines.append("\n🗓️ <b>Rencana kegiatan hari ini:</b>")
    for b in rec.schedule:
        lines.append(f"• <code>{b.from_time} - {b.to_time}</code>: {b.activity}")

    await update.effective_message.reply_text("\n".join(lines), parse_mode="HTML")


async def backup_command(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Manually creates a timestamped SQLite database backup."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    from app.memory.backup import perform_backup

    try:
        bk_path = perform_backup(
            db_path=settings.db_path,
            backup_dir=settings.backup_dir,
            keep_days=settings.config.backup.keep_days,
        )
        size_kb = bk_path.stat().st_size / 1024
        await update.effective_message.reply_text(
            f"✅ Backup database berhasil dibuat!\n"
            f"📁 File: <code>{bk_path.name}</code>\n"
            f"📦 Ukuran: <code>{size_kb:.1f} KB</code>\n"
            f"🗄️ Direktori: <code>{settings.backup_dir}</code>",
            parse_mode="HTML",
        )
    except Exception as exc:
        log.exception("manual_backup_failed error=%r", exc)
        await update.effective_message.reply_text(f"❌ Gagal membuat backup: {exc}")


async def non_text_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Acknowledges non-text messages (photos, voice, stickers) naturally in Indonesian."""
    settings = _settings(ctx)
    if not _is_owner(update, settings) or update.effective_message is None:
        return

    # Reset ignored count so proactive backoff knows user interacted
    store = _store(ctx)
    await store.reset_ignored_count()

    reply = f"aku belum bisa liat atau denger itu nih 🥺 ceritain langsung aja yaa {settings.persona.user_nickname}!"
    await update.effective_message.reply_text(reply)


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
    app.add_handler(CommandHandler(["setname", "nama"], set_name_command))
    app.add_handler(CommandHandler("reset_memory", reset_memory_command))
    app.add_handler(CommandHandler(["run_nightly", "extract_memory"], run_nightly_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler(["pause", "jeda"], pause_command))
    app.add_handler(CommandHandler(["resume", "lanjut"], resume_command))
    app.add_handler(CommandHandler(["frequency", "frekuensi"], frequency_command))
    app.add_handler(CommandHandler(["test_proactive", "trigger_proactive"], test_proactive_command))
    app.add_handler(CommandHandler(["life", "jadwal"], life_command))
    app.add_handler(CommandHandler(["backup", "cadangkan"], backup_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_message))
    app.add_handler(MessageHandler(~filters.COMMAND & ~filters.TEXT, non_text_message))
    app.add_error_handler(on_error)

