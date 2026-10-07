"""Proactive message sending job (PRD §4.4, §4.7, §4.8, §4.10, §8.3).

Executes scheduled proactive sends: performs pre-send checks, selects a varied
message type, builds the contextual proactive prompt, calls Gemini, validates
output, sends split bubbles via Telegram, and tracks state/plans idempotently.
"""

from __future__ import annotations

import logging
import random
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from telegram import Bot

from app.config import ConversationSection, LLMSection, ProactiveSection
from app.engine.conversation import contains_assistant_voice, is_predominantly_english, sanitize_text
from app.engine.gemini_client import GeminiClient, GeminiError
from app.engine.prompt_builder import PromptBuilder, format_time_elapsed_id
from app.gateway.sender import send_bubbles, split_bubbles
from app.memory.store import MemoryStore, ThreadRecord
from app.proactive.checks import is_in_backoff, run_presend_checks
from app.utils.time import now_local, now_utc

log = logging.getLogger(__name__)


def find_due_thread(
    threads: list[ThreadRecord],
    today_str: str,
    now_iso: str,
) -> ThreadRecord | None:
    """Find the most relevant pending open thread to follow up on."""
    for t in threads:
        if t.target_date and t.target_date <= today_str:
            return t
        if t.followup_after and t.followup_after <= now_iso:
            return t
    return threads[0] if threads else None


def choose_message_type(
    weights: dict[str, float],
    recent_types: list[str],
    due_thread: ThreadRecord | None = None,
    is_backoff_mode: bool = False,
    rng: random.Random | None = None,
) -> str:
    """Select the message type with weighted randomness and variety enforcement (FR-4.8)."""
    _rng = rng or random.Random()

    # If an open thread is clearly due, strongly prefer 'followup'
    if due_thread is not None and "followup" not in recent_types:
        return "followup"

    if is_backoff_mode:
        # Low pressure types when user has ignored previous messages
        return _rng.choice(["checkin", "thought"])

    candidate_keys = list(weights.keys())
    # Exclude last 2 types used if possible to prevent repetition
    excluded = set(recent_types[-2:])
    available = [k for k in candidate_keys if k not in excluded]

    if not available:
        available = candidate_keys

    candidate_weights = [weights.get(k, 1.0) for k in available]
    chosen = _rng.choices(available, weights=candidate_weights, k=1)[0]
    return chosen


async def send_proactive(
    plan_id: int,
    bot: Bot,
    store: MemoryStore,
    gemini: GeminiClient,
    prompt_builder: PromptBuilder,
    proactive_cfg: ProactiveSection,
    conv_cfg: ConversationSection,
    llm_cfg: LLMSection,
    tz: ZoneInfo,
    owner_id: int | None = None,
    now: datetime | None = None,
    rng: random.Random | None = None,
    simulate_typing: bool = True,
) -> bool:
    """Execute a planned proactive message send (PRD §8.3)."""
    # 1. Load plan row; if status != 'planned', exit (idempotency, §8.3 step 1)
    plan = await store.get_plan_by_id(plan_id)
    if not plan:
        log.warning("send_proactive_aborted plan_not_found plan_id=%d", plan_id)
        return False
    if plan.status != "planned":
        log.info("send_proactive_aborted already_processed plan_id=%d status=%s", plan_id, plan.status)
        return False

    now_dt = now or now_utc()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)
    local_dt = now_dt.astimezone(tz)
    today_str = local_dt.strftime("%Y-%m-%d")

    # 2. Pre-send checks (§8.3 step 2)
    presend = await run_presend_checks(store, proactive_cfg, tz, now=now_dt)
    if not presend.can_send:
        await store.update_plan_status(plan_id, status="skipped", skip_reason=presend.reason)
        log.info("send_proactive_skipped plan_id=%d reason=%s", plan_id, presend.reason)
        return False

    # Check recipient chat_id
    chat_id = await store.get_owner_chat_id()
    if chat_id is None:
        chat_id = owner_id
    if chat_id is None:
        await store.update_plan_status(plan_id, status="skipped", skip_reason="no_owner_chat_id")
        log.warning("send_proactive_skipped plan_id=%d reason=no_owner_chat_id", plan_id)
        return False

    # 3. Message type selection (§8.3 step 3)
    recent_proactive = await store.get_recent_proactive_messages(limit=5)
    recent_types = [m.proactive_type for m in recent_proactive if m.proactive_type]
    recent_proactive_texts = [m.content for m in recent_proactive[:3]]

    pending_threads = await store.get_pending_threads()
    due_thread = find_due_thread(pending_threads, today_str, local_dt.isoformat())

    ignored_count = await store.get_ignored_count()
    backoff_mode = is_in_backoff(ignored_count, proactive_cfg.backoff_after_ignored)

    selected_type = choose_message_type(
        weights=proactive_cfg.message_type_weights,
        recent_types=recent_types,
        due_thread=due_thread,
        is_backoff_mode=backoff_mode,
        rng=rng,
    )

    # Calculate time since last user chat
    last_user_msg = await store.get_last_user_message_time()
    if last_user_msg:
        elapsed_sec = (now_dt - last_user_msg).total_seconds()
        time_since_last_chat = format_time_elapsed_id(elapsed_sec)
    else:
        time_since_last_chat = "cukup lama"

    # Recent conversation messages
    recent_db_msgs = await store.get_recent_messages(limit=6)
    recent_msgs_formatted = [
        f"{'Kamu' if m.role == 'user' else prompt_builder.persona.name}: {m.content}"
        for m in recent_db_msgs
    ]

    due_thread_text = (
        f"{due_thread.topic}" + (f" ({due_thread.detail})" if due_thread.detail else "")
        if (due_thread and selected_type == "followup")
        else None
    )

    # 4. Build prompt (§8.3 step 4)
    system_instruction = await prompt_builder.build_system_prompt_from_store(store, dt=local_dt)
    proactive_prompt = prompt_builder.build_proactive_prompt(
        window_name=plan.window_name,
        message_type=selected_type,
        time_since_last_chat=time_since_last_chat,
        due_thread=due_thread_text,
        recent_proactive=recent_proactive_texts,
        recent_messages=recent_msgs_formatted,
        dt=local_dt,
    )

    if backoff_mode:
        proactive_prompt += (
            "\n\nCATATAN KHUSUS: User belum membalas beberapa pesan terakhirmu. "
            "Kirim pesan yang SANGAT SANTAI dan tanpa tekanan sama sekali "
            "(misal: 'lagi sibuk ya? santai aja, kabarin kalau udah senggang'). "
            "Jangan bertanya hal berat atau menuntut jawaban."
        )

    # 5. Call Gemini (§8.3 step 5)
    try:
        result = await gemini.generate(
            contents=proactive_prompt,
            model=llm_cfg.chat_model,
            system_instruction=system_instruction,
            temperature=llm_cfg.temperature_chat,
            max_output_tokens=llm_cfg.max_output_tokens_chat,
            purpose="proactive",
        )
        raw_text = result.text
    except GeminiError as exc:
        log.error("send_proactive_gemini_error plan_id=%d error=%r", plan_id, exc)
        await store.update_plan_status(plan_id, status="failed", skip_reason=f"gemini_error: {exc}")
        return False

    # Validation & retry if quality flagged (PRD §10)
    if contains_assistant_voice(raw_text) or is_predominantly_english(raw_text):
        log.warning("proactive_quality_flagged plan_id=%d raw=%r -> retrying once", plan_id, raw_text[:80])
        try:
            retry_res = await gemini.generate(
                contents=proactive_prompt + "\n\nIngat: Bahasa Indonesia santai chat, jangan gaya asisten AI.",
                model=llm_cfg.chat_model,
                system_instruction=system_instruction,
                temperature=llm_cfg.temperature_chat,
                max_output_tokens=llm_cfg.max_output_tokens_chat,
                purpose="proactive_retry",
            )
            raw_text = retry_res.text
        except GeminiError:
            pass

    cleaned_text = sanitize_text(raw_text)
    bubbles = split_bubbles(
        cleaned_text,
        max_bubbles=conv_cfg.max_bubbles,
        max_chars=conv_cfg.max_bubble_chars,
    )
    if not bubbles:
        bubbles = ["Lagi senggang nih, gimana harimu?"]

    # 6. Send bubbles via Telegram (§8.3 step 6)
    try:
        await send_bubbles(
            bot=bot,
            chat_id=chat_id,
            bubbles=bubbles,
            typing_cfg=conv_cfg.typing_delay,
            simulate_typing=simulate_typing,
        )
    except Exception as exc:
        log.error("send_proactive_telegram_error plan_id=%d chat_id=%s error=%r", plan_id, chat_id, exc)
        await store.update_plan_status(plan_id, status="failed", skip_reason=f"telegram_error: {exc}")
        return False

    # 7. Store message and increment ignored_count (§8.3 step 6 & 7)
    saved_content = "\n---\n".join(bubbles)
    msg_id = await store.save_message(
        role="bot",
        content=saved_content,
        origin="proactive",
        proactive_type=selected_type,
    )
    await store.increment_ignored_count()

    # 8. Update plan row to sent (§8.3 step 8)
    await store.update_plan_status(plan_id, status="sent", message_id=msg_id)
    log.info(
        "proactive_message_sent plan_id=%d type=%s msg_id=%d bubbles=%d chars=%d",
        plan_id,
        selected_type,
        msg_id,
        len(bubbles),
        len(saved_content),
    )
    return True
