"""Regression tests for the issues from VALIDATION_REPORT.md."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo

import pytest
from telegram.error import Forbidden

from app.config import ConversationSection, LLMSection, Persona, ProactiveSection, TimeRange, Window
from app.engine.conversation import ConversationEngine, FALLBACK_REPLY, sanitize_text
from app.engine.llm_client import LLMError, LLMResult
from app.engine.prompt_builder import PromptBuilder
from app.engine.safety import build_distress_instruction, detect_distress
from app.memory.store import MemoryStore
from app.proactive.checks import frequency_profile, run_presend_checks
from app.proactive.planner import resolve_overdue_plans
from app.proactive.sender_job import send_proactive
from app.utils.time import to_utc_iso

ROOT = Path(__file__).resolve().parent.parent
WIB = ZoneInfo("Asia/Jakarta")


@pytest.fixture
async def store(tmp_path):
    s = MemoryStore(tmp_path / "fix.db")
    await s.init_db()
    return s


@pytest.fixture
def proactive_cfg() -> ProactiveSection:
    return ProactiveSection(
        daily_cap=4,
        quiet_hours=TimeRange(start="22:00", end="06:00"),
        windows=[Window(name="morning", start="06:00", end="08:00", chance=0.8)],
        message_type_weights={"checkin": 3},
    )


def _engine(store, llm, resources=()):
    builder = PromptBuilder(ROOT / "prompts", Persona(name="Nala"), WIB)
    return ConversationEngine(
        llm=llm,
        prompt_builder=builder,
        llm_cfg=LLMSection(chat_model="m", utility_model="u", history_window_messages=5),
        conv_cfg=ConversationSection(),
        store=store,
        crisis_resources=resources,
    )


# Issue 1 + 2: user message persisted even when LLM fails
@pytest.mark.asyncio
async def test_user_message_saved_when_llm_fails(store):
    llm = AsyncMock()
    llm.generate.side_effect = LLMError("down")
    bubbles = await _engine(store, llm).reply("Halo Nala")

    assert bubbles == [FALLBACK_REPLY]
    msgs = await store.get_recent_messages()
    assert [(m.role, m.content) for m in msgs] == [("user", "Halo Nala")]
    assert await store.get_last_user_message_time() is not None


@pytest.mark.asyncio
async def test_user_turn_not_duplicated_in_prompt(store):
    llm = AsyncMock()
    llm.generate.return_value = LLMResult("Hai juga!", "m", 1, 1, 1)
    await _engine(store, llm).reply("Halo unik")

    contents = llm.generate.call_args.kwargs["contents"]
    texts = [c["content"] if isinstance(c, dict) else c.parts[0].text for c in contents]
    assert texts.count("Halo unik") == 1


# Issue 3: overdue plans
@pytest.mark.asyncio
async def test_resolve_overdue_plans(store):
    now = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)
    recent = await store.create_plan("2026-10-08", "recent", to_utc_iso(now - timedelta(minutes=10)))
    stale = await store.create_plan("2026-10-08", "stale", to_utc_iso(now - timedelta(hours=3)))
    future = await store.create_plan("2026-10-08", "future", to_utc_iso(now + timedelta(hours=1)))

    plans = await store.get_plans_for_date("2026-10-08")
    send_now = await resolve_overdue_plans(store, plans, now=now)

    assert [p.id for p in send_now] == [recent]
    stale_plan = await store.get_plan_by_id(stale)
    assert stale_plan.status == "skipped" and stale_plan.skip_reason == "missed"
    assert (await store.get_plan_by_id(future)).status == "planned"
    assert (await store.get_plan_by_id(recent)).status == "planned"


# Issue 4: distress handling
def test_detect_distress():
    assert detect_distress("Aku pengen mati rasanya")
    assert detect_distress("kayaknya aku mau bunuh diri")
    assert not detect_distress("Capek banget kerja hari ini wkwk")
    assert not detect_distress("")


def test_distress_instruction_includes_resources():
    text = build_distress_instruction(["Sejiwa 119 ext 8"])
    assert "JANGAN bercanda" in text
    assert "Sejiwa 119 ext 8" in text


@pytest.mark.asyncio
async def test_engine_uses_supportive_prompt_on_distress(store):
    llm = AsyncMock()
    llm.generate.return_value = LLMResult("Aku di sini ya.", "m", 1, 1, 1)
    await _engine(store, llm, resources=["Sejiwa 119 ext 8"]).reply("aku pengen mati")

    system = llm.generate.call_args.kwargs["system_instruction"]
    assert "KONDISI SERIUS" in system
    assert "Sejiwa 119 ext 8" in system


# Issue 5: bot blocked
@pytest.mark.asyncio
async def test_forbidden_pauses_proactive(store, proactive_cfg):
    llm = AsyncMock()
    llm.generate.return_value = LLMResult("Pagi! Lagi apa nih?", "m", 1, 1, 1)
    builder = PromptBuilder(ROOT / "prompts", Persona(name="Nala"), WIB)
    bot = MagicMock()
    bot.send_chat_action = AsyncMock()
    bot.send_message = AsyncMock(side_effect=Forbidden("Forbidden: bot was blocked by the user"))

    now = datetime(2026, 10, 8, 3, 0, tzinfo=timezone.utc)  # 10:00 WIB
    await store.set_owner_chat_id(123)
    plan_id = await store.create_plan("2026-10-08", "morning", to_utc_iso(now))

    sent = await send_proactive(
        plan_id=plan_id,
        bot=bot,
        store=store,
        llm=llm,
        prompt_builder=builder,
        proactive_cfg=proactive_cfg,
        conv_cfg=ConversationSection(),
        llm_cfg=LLMSection(chat_model="m", utility_model="u"),
        tz=WIB,
        now=now,
        simulate_typing=False,
    )

    assert sent is False
    plan = await store.get_plan_by_id(plan_id)
    assert plan.status == "failed" and plan.skip_reason == "bot_blocked"
    paused = await store.get_paused_until()
    assert paused is not None and paused > now + timedelta(days=7)


# Issue 6: effective cap honours /frequency
def test_frequency_profile():
    assert frequency_profile("low", 4) == (2, 0.6)
    assert frequency_profile("normal", 4) == (4, 1.0)
    assert frequency_profile("high", 4) == (5, 1.2)
    assert frequency_profile(None, 4) == (4, 1.0)


@pytest.mark.asyncio
async def test_presend_cap_uses_frequency(store, proactive_cfg):
    now = datetime(2026, 10, 7, 3, 0, tzinfo=timezone.utc)  # 10:00 WIB on 2026-10-07
    for i in range(4):
        pid = await store.create_plan("2026-10-07", f"w{i}", f"2026-10-07T0{i}:00:00Z")
        await store.update_plan_status(pid, "sent")

    assert (await run_presend_checks(store, proactive_cfg, WIB, now=now)).reason == "daily_cap_reached"
    await store.set_state("proactive_frequency", "high")
    assert (await run_presend_checks(store, proactive_cfg, WIB, now=now)).can_send is True


# Issue 7: sanitizer
def test_sanitize_keeps_normal_colon_text_and_honest_ai():
    assert sanitize_text("Catatan: besok libur ya") == "Catatan: besok libur ya"
    assert sanitize_text("Nala: halo!", speaker_names=("Nala",)) == "halo!"
    honest = "Iya, sebagai AI aku memang bukan manusia, tapi aku peduli sama kamu."
    assert sanitize_text(honest) == honest
    assert sanitize_text("Seru banget! Ada yang bisa saya bantu?") == "Seru banget!"
