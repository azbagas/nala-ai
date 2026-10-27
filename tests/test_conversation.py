from __future__ import annotations

from pathlib import Path
from zoneinfo import ZoneInfo
from unittest.mock import AsyncMock

import pytest

from app.config import ConversationSection, LLMSection, Persona, TypingDelay
from app.engine.conversation import (
    ConversationEngine,
    FALLBACK_REPLY,
    contains_assistant_voice,
    sanitize_text,
)
from app.engine.llm_client import LLMError, LLMResult
from app.engine.prompt_builder import PromptBuilder

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
WIB = ZoneInfo("Asia/Jakarta")


def test_contains_assistant_voice():
    assert contains_assistant_voice("Halo! Ada yang bisa saya bantu hari ini?")
    assert contains_assistant_voice("Sebagai AI, saya tidak memiliki perasaan.")
    assert contains_assistant_voice("Tentu! Sebagai asisten pribadi kamu, siap.")
    assert not contains_assistant_voice("Halo! Lagi ngapain nih kamu?")
    assert not contains_assistant_voice("Wah seru banget ceritanya wkwk")


def test_sanitize_text():
    raw = "Nala: Halo apa kabar? Ada yang bisa saya bantu?"
    cleaned = sanitize_text(raw, speaker_names=("Nala",))
    assert not cleaned.startswith("Nala:")
    assert "ada yang bisa saya bantu" not in cleaned.lower()

    header_raw = "### Catatan\nHari ini cerah banget!"
    assert "###" not in sanitize_text(header_raw)


@pytest.fixture
def test_engine():
    persona = Persona(name="Nala", traits=["ceria"])
    builder = PromptBuilder(PROMPTS_DIR, persona, WIB)
    llm_cfg = LLMSection(
        chat_model="gemini-test",
        utility_model="gemini-test-lite",
        history_window_messages=5,
    )
    conv_cfg = ConversationSection(
        debounce_seconds=1.0,
        max_bubbles=3,
        max_bubble_chars=280,
        typing_delay=TypingDelay(min_seconds=0.1, max_seconds=1.0, chars_per_second=25.0),
    )

    mock_llm = AsyncMock()
    engine = ConversationEngine(
        llm=mock_llm,
        prompt_builder=builder,
        llm_cfg=llm_cfg,
        conv_cfg=conv_cfg,
    )
    return engine, mock_llm


@pytest.mark.asyncio
async def test_conversation_engine_reply_success(test_engine):
    engine, mock_llm = test_engine
    mock_llm.generate.return_value = LLMResult(
        text="Haii! Lagi santai nih di kamar.\n---\nKamu sendiri lagi apa?",
        model="gemini-test",
        input_tokens=100,
        output_tokens=30,
        latency_ms=250,
    )

    bubbles = await engine.reply("Halo Nala")
    assert len(bubbles) == 2
    assert bubbles[0] == "Haii! Lagi santai nih di kamar."
    assert bubbles[1] == "Kamu sendiri lagi apa?"

    history = engine.get_history()
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "Halo Nala"
    assert history[1].role == "model"


@pytest.mark.asyncio
async def test_conversation_engine_quality_retry(test_engine):
    engine, mock_llm = test_engine
    # First call returns assistant voice, second call returns natural persona
    mock_llm.generate.side_effect = [
        LLMResult(
            text="Halo! Sebagai asisten AI, ada yang bisa saya bantu?",
            model="gemini-test",
            input_tokens=100,
            output_tokens=30,
            latency_ms=200,
        ),
        LLMResult(
            text="Ehh halo! Lagi santai aja nih hehe.",
            model="gemini-test",
            input_tokens=150,
            output_tokens=20,
            latency_ms=180,
        ),
    ]

    bubbles = await engine.reply("Hai Nala")
    assert mock_llm.generate.call_count == 2
    assert "santai" in bubbles[0]


@pytest.mark.asyncio
async def test_conversation_engine_fallback_on_error(test_engine):
    engine, mock_llm = test_engine
    mock_llm.generate.side_effect = LLMError("API timeout")

    bubbles = await engine.reply("Halo")
    assert bubbles == [FALLBACK_REPLY]


@pytest.mark.asyncio
async def test_conversation_engine_clear_history(test_engine):
    engine, mock_llm = test_engine
    mock_llm.generate.return_value = LLMResult(
        text="Balasan",
        model="gemini-test",
        input_tokens=10,
        output_tokens=5,
        latency_ms=100,
    )

    await engine.reply("Pesan 1")
    assert len(engine.get_history()) == 2

    engine.clear_history()
    assert len(engine.get_history()) == 0


@pytest.mark.asyncio
async def test_conversation_engine_with_memory_store(tmp_path):
    from app.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "conv_test.db")
    await store.init_db()
    await store.add_fact("work", "Software Engineer di Bandung")

    persona = Persona(name="Nala")
    builder = PromptBuilder(PROMPTS_DIR, persona, WIB)
    mock_llm = AsyncMock()
    mock_llm.generate.return_value = LLMResult(
        text="Halo! Aku dengar kamu kerja di Bandung ya?",
        model="test",
        input_tokens=100,
        output_tokens=25,
        latency_ms=150,
    )

    engine = ConversationEngine(
        llm=mock_llm,
        prompt_builder=builder,
        llm_cfg=LLMSection(chat_model="test", utility_model="test-lite", history_window_messages=5),
        conv_cfg=ConversationSection(),
        store=store,
    )

    bubbles = await engine.reply("Halo!")
    assert len(bubbles) >= 1

    # Verify messages saved to database
    db_messages = await store.get_recent_messages()
    assert len(db_messages) == 2
    assert db_messages[0].role == "user"
    assert db_messages[0].content == "Halo!"
    assert db_messages[1].role == "bot"

    # Verify that the system prompt passed to LLM included the fact
    call_args = mock_llm.generate.call_args
    assert "Software Engineer di Bandung" in call_args.kwargs["system_instruction"]

    # Verify last_user_message_time updated and ignored_count reset
    assert await store.get_last_user_message_time() is not None
    assert await store.get_ignored_count() == 0


def test_extract_introduced_name():
    from app.engine.conversation import extract_introduced_name

    assert extract_introduced_name("namaku Bagas") == "Bagas"
    assert extract_introduced_name("nama aku Azhar yaa") == "Azhar"
    assert extract_introduced_name("panggil aku Ryan") == "Ryan"
    assert extract_introduced_name("panggil aja Budi") == "Budi"
    assert extract_introduced_name("kenalin aku Dinda") == "Dinda"
    assert extract_introduced_name("salam kenal, aku Bagas") == "Bagas"
    assert extract_introduced_name("aku lagi di jalan") is None
    assert extract_introduced_name("aku mau makan dulu ya") is None
    assert extract_introduced_name("halo nala apa kabar") is None


@pytest.mark.asyncio
async def test_conversation_learns_user_name(tmp_path):
    from app.memory.store import MemoryStore

    store = MemoryStore(tmp_path / "name_test.db")
    await store.init_db()

    persona = Persona(name="Nala")
    builder = PromptBuilder(PROMPTS_DIR, persona, WIB)
    mock_llm = AsyncMock()
    mock_llm.generate.return_value = LLMResult(
        text="haii Bagas! salam kenal yaa",
        model="test",
        input_tokens=50,
        output_tokens=15,
        latency_ms=100,
    )

    engine = ConversationEngine(
        llm=mock_llm,
        prompt_builder=builder,
        llm_cfg=LLMSection(chat_model="test", utility_model="test-lite", history_window_messages=5),
        conv_cfg=ConversationSection(),
        store=store,
    )

    assert await store.get_user_name() is None
    await engine.reply("halo nala, namaku Bagas salam kenal!")

    assert await store.get_user_name() == "Bagas"
    call_args = mock_llm.generate.call_args
    assert "Bagas" in call_args.kwargs["system_instruction"]
    assert "Kamu sudah tahu nama user" in call_args.kwargs["system_instruction"]

