from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo
import pytest

from app.config import (
    AppConfig,
    AppSection,
    ConversationSection,
    LLMSection,
    Persona,
    ProactiveSection,
    Settings,
    TimeRange,
    Window,
)
from app.gateway.handlers import (
    backup_command,
    frequency_command,
    non_text_message,
    pause_command,
    resume_command,
    status_command,
)
from app.memory.store import MemoryStore

WIB = ZoneInfo("Asia/Jakarta")


@pytest.fixture
async def temp_store(tmp_path):
    db_file = tmp_path / "test_handlers.db"
    store = MemoryStore(db_file)
    await store.init_db()
    return store


@pytest.fixture
def mock_settings():
    cfg = AppConfig(
        app=AppSection(
            timezone="Asia/Jakarta",
            owner_telegram_id=12345,
            log_level="INFO",
        ),
        llm=LLMSection(chat_model="gemini-test", utility_model="gemini-test"),
        conversation=ConversationSection(),
        proactive=ProactiveSection(
            daily_cap=4,
            quiet_hours=TimeRange(start="22:00", end="06:00"),
            windows=[Window(name="morning", start="06:00", end="08:00", chance=1.0)],
            message_type_weights={"checkin": 3, "activity": 3},
        ),

    )

    persona = Persona(name="Nara", user_nickname="kamu")
    from pathlib import Path
    from app.config import Secrets
    return Settings(
        config=cfg,
        persona=persona,
        secrets=Secrets(telegram_bot_token="dummy", gemini_api_key="dummy"),
        config_dir=Path("."),
        prompts_dir=Path("."),
        data_dir=Path("."),
    )



def create_mock_update_and_ctx(settings, store, args=None, user_id=12345):
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_chat.id = user_id
    update.effective_message = MagicMock()
    update.effective_message.reply_text = AsyncMock()

    ctx = MagicMock()
    ctx.application.bot_data = {
        "settings": settings,
        "store": store,
    }
    ctx.args = args or []
    return update, ctx


@pytest.mark.asyncio
async def test_frequency_command_view_and_set(mock_settings, temp_store):
    # 1. View default frequency
    update, ctx = create_mock_update_and_ctx(mock_settings, temp_store, args=[])
    await frequency_command(update, ctx)
    update.effective_message.reply_text.assert_called_once()
    assert "Frekuensi Pesan Proaktif Saat Ini" in update.effective_message.reply_text.call_args[0][0]

    # 2. Set to high
    update, ctx = create_mock_update_and_ctx(mock_settings, temp_store, args=["high"])
    await frequency_command(update, ctx)
    assert await temp_store.get_state("proactive_frequency") == "high"
    assert "lebih aktif" in update.effective_message.reply_text.call_args[0][0]

    # 3. Invalid choice
    update, ctx = create_mock_update_and_ctx(mock_settings, temp_store, args=["extreme"])
    await frequency_command(update, ctx)
    assert "Pilihan tidak valid" in update.effective_message.reply_text.call_args[0][0]


@pytest.mark.asyncio
async def test_pause_and_resume_commands(mock_settings, temp_store):
    # Pause for 2 hours
    update, ctx = create_mock_update_and_ctx(mock_settings, temp_store, args=["2h"])
    await pause_command(update, ctx)
    paused = await temp_store.get_paused_until()
    assert paused is not None
    assert "Aku gak bakal chat duluan sampai" in update.effective_message.reply_text.call_args[0][0]

    # Resume command
    update, ctx = create_mock_update_and_ctx(mock_settings, temp_store, args=[])
    await resume_command(update, ctx)
    paused_after = await temp_store.get_paused_until()
    assert paused_after is None
    assert "diaktifkan kembali" in update.effective_message.reply_text.call_args[0][0]



@pytest.mark.asyncio
async def test_status_command_with_llm_stats(mock_settings, temp_store):
    # Log an LLM call
    await temp_store.log_llm_call(
        purpose="chat",
        model="gemini-2.5-flash",
        input_tokens=200,
        output_tokens=60,
        latency_ms=350,
        success=True,
    )

    update, ctx = create_mock_update_and_ctx(mock_settings, temp_store, args=[])
    await status_command(update, ctx)
    text = update.effective_message.reply_text.call_args[0][0]
    assert "Status Proaktif Nara" in text
    assert "Statistik Model & Biaya Hari Ini" in text
    assert "Total panggilan LLM" in text


@pytest.mark.asyncio
async def test_backup_command(mock_settings, temp_store, tmp_path):
    mock_settings.data_dir = tmp_path
    mock_settings.config.storage.db_path = temp_store.db_path.name
    mock_settings.config.storage.backup_dir = "backups"


    update, ctx = create_mock_update_and_ctx(mock_settings, temp_store, args=[])
    await backup_command(update, ctx)

    text = update.effective_message.reply_text.call_args[0][0]
    assert "Backup database berhasil dibuat!" in text
    assert "File:" in text


@pytest.mark.asyncio
async def test_non_text_message(mock_settings, temp_store):
    await temp_store.set_state("ignored_count", "3")
    assert await temp_store.get_ignored_count() == 3

    update, ctx = create_mock_update_and_ctx(mock_settings, temp_store)
    await non_text_message(update, ctx)

    text = update.effective_message.reply_text.call_args[0][0]
    assert "aku belum bisa liat atau denger itu nih" in text
    # Ignored count should be reset to 0
    assert await temp_store.get_ignored_count() == 0

