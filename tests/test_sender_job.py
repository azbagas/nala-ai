from __future__ import annotations

import random
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo
import pytest

from app.config import ConversationSection, LLMSection, Persona, ProactiveSection, TimeRange, Window
from app.engine.gemini_client import GeminiClient, GeminiResult
from app.engine.prompt_builder import PromptBuilder
from app.memory.store import MemoryStore, ThreadRecord
from app.proactive.sender_job import choose_message_type, find_due_thread, send_proactive

WIB = ZoneInfo("Asia/Jakarta")


@pytest.fixture
async def temp_store(tmp_path):
    db_file = tmp_path / "test_sender_job.db"
    store = MemoryStore(db_file)
    await store.init_db()
    return store


@pytest.fixture
def mock_gemini():
    gemini = MagicMock(spec=GeminiClient)
    gemini.generate = AsyncMock(
        return_value=GeminiResult(
            text="Lagi santai nih, kamu lagi apa sekarang?",
            model="gemini-test",
            input_tokens=150,
            output_tokens=30,
            latency_ms=250,
        )
    )
    return gemini


@pytest.fixture
def prompt_builder(tmp_path):
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    (prompts_dir / "system_persona.id.md").write_text("Kamu {{persona.name}}.", encoding="utf-8")
    (prompts_dir / "proactive.id.md").write_text("Sapa {{user.nickname}}.", encoding="utf-8")
    persona = Persona(name="Nara", user_nickname="kamu")
    return PromptBuilder(prompts_dir, persona, WIB)


@pytest.fixture
def proactive_cfg():
    return ProactiveSection(
        daily_cap=4,
        quiet_hours=TimeRange(start="22:00", end="06:00"),
        windows=[Window(name="morning", start="06:00", end="08:00", chance=1.0)],
        skip_if_user_active_within_minutes=20,
        backoff_after_ignored=2,
        stop_after_ignored=4,
        message_type_weights={"checkin": 3, "activity": 3, "joke": 2, "followup": 4},
    )


def test_choose_message_type_due_thread():
    thread = ThreadRecord(
        id=1, topic="Presentasi", detail=None, target_date="2026-10-07",
        followup_after=None, status="pending", created_at="", updated_at="",
    )
    chosen = choose_message_type(
        weights={"checkin": 3, "activity": 3, "followup": 4},
        recent_types=["checkin"],
        due_thread=thread,
    )
    assert chosen == "followup"


def test_choose_message_type_backoff():
    rng = random.Random(42)
    chosen = choose_message_type(
        weights={"checkin": 3, "activity": 3, "joke": 2},
        recent_types=["activity"],
        is_backoff_mode=True,
        rng=rng,
    )
    assert chosen in ["checkin", "thought"]


def test_choose_message_type_avoids_recent():
    rng = random.Random(42)
    weights = {"checkin": 5, "activity": 5, "joke": 5}
    # If recent types are checkin and activity, it should strongly prefer joke
    chosen = choose_message_type(
        weights=weights,
        recent_types=["checkin", "activity"],
        rng=rng,
    )
    assert chosen == "joke"


def test_find_due_thread():
    t1 = ThreadRecord(id=1, topic="Nanti", detail=None, target_date="2026-10-10", followup_after=None, status="pending", created_at="", updated_at="")
    t2 = ThreadRecord(id=2, topic="Hari ini", detail=None, target_date="2026-10-07", followup_after=None, status="pending", created_at="", updated_at="")
    found = find_due_thread([t1, t2], today_str="2026-10-07", now_iso="2026-10-07T12:00:00")
    assert found is not None
    assert found.id == 2


@pytest.mark.asyncio
async def test_send_proactive_success(temp_store, mock_gemini, prompt_builder, proactive_cfg):
    # Setup plan
    p_id = await temp_store.create_plan("2026-10-07", "morning", "2026-10-07T07:15:00Z")
    await temp_store.set_owner_chat_id(99999)

    bot = MagicMock()
    bot.send_message = AsyncMock()
    bot.send_chat_action = AsyncMock()

    conv_cfg = ConversationSection()
    llm_cfg = LLMSection(chat_model="gemini-test", utility_model="gemini-test")
    now = datetime(2026, 10, 7, 3, 0, 0, tzinfo=timezone.utc)  # 10:00 WIB (awake)

    success = await send_proactive(
        plan_id=p_id,
        bot=bot,
        store=temp_store,
        gemini=mock_gemini,
        prompt_builder=prompt_builder,
        proactive_cfg=proactive_cfg,
        conv_cfg=conv_cfg,
        llm_cfg=llm_cfg,
        tz=WIB,
        now=now,
        simulate_typing=False,
    )

    assert success is True
    # Verify plan status updated to sent
    plan = await temp_store.get_plan_by_id(p_id)
    assert plan.status == "sent"
    assert plan.message_id is not None

    # Verify message saved to DB
    messages = await temp_store.get_recent_messages()
    assert len(messages) == 1
    assert messages[0].origin == "proactive"
    assert messages[0].role == "bot"

    # Verify ignored count incremented
    assert await temp_store.get_ignored_count() == 1

    # Verify telegram send called
    bot.send_message.assert_called_once()


@pytest.mark.asyncio
async def test_send_proactive_idempotent(temp_store, mock_gemini, prompt_builder, proactive_cfg):
    p_id = await temp_store.create_plan("2026-10-07", "morning", "2026-10-07T07:15:00Z")
    # Mark already sent
    msg_id = await temp_store.save_message("bot", "Pagi!", origin="proactive")
    await temp_store.update_plan_status(p_id, "sent", message_id=msg_id)

    bot = MagicMock()
    success = await send_proactive(
        plan_id=p_id,
        bot=bot,
        store=temp_store,
        gemini=mock_gemini,
        prompt_builder=prompt_builder,
        proactive_cfg=proactive_cfg,
        conv_cfg=ConversationSection(),
        llm_cfg=LLMSection(chat_model="test", utility_model="test"),
        tz=WIB,
    )

    assert success is False
    mock_gemini.generate.assert_not_called()


@pytest.mark.asyncio
async def test_send_proactive_skipped_presend(temp_store, mock_gemini, prompt_builder, proactive_cfg):
    p_id = await temp_store.create_plan("2026-10-07", "morning", "2026-10-07T07:15:00Z")
    await temp_store.set_owner_chat_id(99999)

    # Time during quiet hours (23:00 WIB / 16:00 UTC)
    now = datetime(2026, 10, 7, 16, 0, 0, tzinfo=timezone.utc)
    bot = MagicMock()

    success = await send_proactive(
        plan_id=p_id,
        bot=bot,
        store=temp_store,
        gemini=mock_gemini,
        prompt_builder=prompt_builder,
        proactive_cfg=proactive_cfg,
        conv_cfg=ConversationSection(),
        llm_cfg=LLMSection(chat_model="test", utility_model="test"),
        tz=WIB,
        now=now,
    )

    assert success is False
    plan = await temp_store.get_plan_by_id(p_id)
    assert plan.status == "skipped"
    assert plan.skip_reason == "quiet_hours"
    mock_gemini.generate.assert_not_called()
