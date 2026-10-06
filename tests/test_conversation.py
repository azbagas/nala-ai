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
from app.engine.gemini_client import GeminiError, GeminiResult
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
    raw = "Nara: Halo apa kabar? Ada yang bisa saya bantu?"
    cleaned = sanitize_text(raw)
    assert not cleaned.startswith("Nara:")
    assert "ada yang bisa saya bantu" not in cleaned.lower()

    header_raw = "### Catatan\nHari ini cerah banget!"
    assert "###" not in sanitize_text(header_raw)


@pytest.fixture
def test_engine():
    persona = Persona(name="Nara", traits=["ceria"])
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

    mock_gemini = AsyncMock()
    engine = ConversationEngine(
        gemini=mock_gemini,
        prompt_builder=builder,
        llm_cfg=llm_cfg,
        conv_cfg=conv_cfg,
    )
    return engine, mock_gemini


@pytest.mark.asyncio
async def test_conversation_engine_reply_success(test_engine):
    engine, mock_gemini = test_engine
    mock_gemini.generate.return_value = GeminiResult(
        text="Haii! Lagi santai nih di kamar.\n---\nKamu sendiri lagi apa?",
        model="gemini-test",
        input_tokens=100,
        output_tokens=30,
        latency_ms=250,
    )

    bubbles = await engine.reply("Halo Nara")
    assert len(bubbles) == 2
    assert bubbles[0] == "Haii! Lagi santai nih di kamar."
    assert bubbles[1] == "Kamu sendiri lagi apa?"

    history = engine.get_history()
    assert len(history) == 2
    assert history[0].role == "user"
    assert history[0].content == "Halo Nara"
    assert history[1].role == "model"


@pytest.mark.asyncio
async def test_conversation_engine_quality_retry(test_engine):
    engine, mock_gemini = test_engine
    # First call returns assistant voice, second call returns natural persona
    mock_gemini.generate.side_effect = [
        GeminiResult(
            text="Halo! Sebagai asisten AI, ada yang bisa saya bantu?",
            model="gemini-test",
            input_tokens=100,
            output_tokens=30,
            latency_ms=200,
        ),
        GeminiResult(
            text="Ehh halo! Lagi santai aja nih hehe.",
            model="gemini-test",
            input_tokens=150,
            output_tokens=20,
            latency_ms=180,
        ),
    ]

    bubbles = await engine.reply("Hai Nara")
    assert mock_gemini.generate.call_count == 2
    assert "santai" in bubbles[0]


@pytest.mark.asyncio
async def test_conversation_engine_fallback_on_error(test_engine):
    engine, mock_gemini = test_engine
    mock_gemini.generate.side_effect = GeminiError("API timeout")

    bubbles = await engine.reply("Halo")
    assert bubbles == [FALLBACK_REPLY]


@pytest.mark.asyncio
async def test_conversation_engine_clear_history(test_engine):
    engine, mock_gemini = test_engine
    mock_gemini.generate.return_value = GeminiResult(
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
