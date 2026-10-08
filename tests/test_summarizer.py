from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.config import LLMSection, Persona
from app.engine.gemini_client import GeminiResult
from app.memory.store import MemoryStore
from app.memory.summarizer import ConversationSummarizer

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
WIB = ZoneInfo("Asia/Jakarta")


@pytest.mark.asyncio
async def test_summarize_day(tmp_path):
    store = MemoryStore(tmp_path / "test.db")
    await store.init_db()

    await store.save_message("user", "Hari ini aku lelah banget setelah kerja seharian.")
    await store.save_message("bot", "Istirahat dulu ya! Jangan lupa minum air.")

    mock_gemini = AsyncMock()
    mock_gemini.generate.return_value = GeminiResult(
        text="User merasa lelah setelah bekerja seharian. Nala mengingatkan untuk istirahat dan minum air.",
        model="gemini-test",
        input_tokens=100,
        output_tokens=30,
        latency_ms=250,
    )

    summarizer = ConversationSummarizer(
        gemini=mock_gemini,
        store=store,
        prompts_dir=PROMPTS_DIR,
        persona=Persona(name="Nala"),
        llm_cfg=LLMSection(chat_model="test", utility_model="test-lite"),
        tz=WIB,
    )

    summary_rec = await summarizer.summarize_day()
    assert summary_rec is not None
    assert summary_rec.scope == "daily"
    assert "lelah" in summary_rec.content

    # Check store has summary
    latest = await store.get_latest_summary()
    assert latest is not None
    assert latest.id == summary_rec.id


@pytest.mark.asyncio
async def test_check_and_create_rolling_summary(tmp_path):
    store = MemoryStore(tmp_path / "test.db")
    await store.init_db()

    # Populate 3 daily summaries
    for i in range(3):
        await store.add_summary(
            scope="daily",
            period_start=f"2026-10-0{i+1}T00:00:00",
            period_end=f"2026-10-0{i+1}T23:59:59",
            content=f"Ringkasan hari ke-{i+1}",
        )

    mock_gemini = AsyncMock()
    mock_gemini.generate.return_value = GeminiResult(
        text="Ringkasan bergulir komprehensif selama 3 hari terakhir.",
        model="gemini-test",
        input_tokens=150,
        output_tokens=40,
        latency_ms=200,
    )

    summarizer = ConversationSummarizer(
        gemini=mock_gemini,
        store=store,
        prompts_dir=PROMPTS_DIR,
        persona=Persona(name="Nala"),
        llm_cfg=LLMSection(chat_model="test", utility_model="test-lite"),
        tz=WIB,
    )

    rolling = await summarizer.check_and_create_rolling_summary(every_days=3)
    assert rolling is not None
    assert rolling.scope == "rolling"
    assert "bergulir" in rolling.content
