from __future__ import annotations

from datetime import date, datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock
from zoneinfo import ZoneInfo
import pytest

from app.config import LLMSection, Persona
from app.engine.gemini_client import GeminiClient, GeminiError, GeminiResult
from app.engine.prompt_builder import PromptBuilder
from app.life.simulator import (
    LifeSimulator,
    format_schedule_summary,
    get_fallback_daily_life,
    parse_daily_life_json,
    resolve_current_activity,
)
from app.memory.store import MemoryStore, ScheduleBlock

WIB = ZoneInfo("Asia/Jakarta")


@pytest.fixture
async def temp_store(tmp_path):
    db_file = tmp_path / "test_life.db"
    store = MemoryStore(db_file)
    await store.init_db()
    return store


@pytest.fixture
def persona():
    return Persona(
        name="Nala",
        age=24,
        backstory="Desainer freelance di Jakarta bareng kucing Mochi.",
        traits=["ceria", "perhatian"],
        interests=["kopi", "kucing", "desain"],
    )


@pytest.fixture
def mock_gemini():
    gemini = MagicMock(spec=GeminiClient)
    sample_json = """
    {
      "mood": "senang dan berenergi",
      "energy": "high",
      "notes": "Mochi tidur di atas laptop seharian",
      "schedule": [
        {"from": "07:00", "to": "08:30", "activity": "Ngopi dan sarapan santai"},
        {"from": "08:30", "to": "12:00", "activity": "Ngerjain desain ilustrasi"},
        {"from": "12:00", "to": "13:30", "activity": "Makan siang bareng teman"},
        {"from": "13:30", "to": "17:00", "activity": "Lanjut kerjaan freelance"},
        {"from": "17:00", "to": "19:00", "activity": "Jalan sore dan beli bahan makanan"},
        {"from": "19:00", "to": "21:00", "activity": "Masak makan malam & santai"},
        {"from": "21:00", "to": "23:00", "activity": "Nonton drakor & siap-siap tidur"}
      ]
    }
    """
    gemini.generate = AsyncMock(
        return_value=GeminiResult(
            text=sample_json,
            model="gemini-test",
            input_tokens=200,
            output_tokens=100,
            latency_ms=300,
        )
    )
    return gemini


@pytest.fixture
def prompt_dir(tmp_path):
    p_dir = tmp_path / "prompts"
    p_dir.mkdir()
    (p_dir / "daily_life.id.md").write_text("Buat jadwal {{persona.name}} untuk {{date}}.", encoding="utf-8")
    (p_dir / "system_persona.id.md").write_text("Kamu {{persona.name}}. Kegiatan: {{her_current_activity}}. Mood: {{her_mood}}.", encoding="utf-8")
    return p_dir


def test_parse_daily_life_json():
    # 1. Clean JSON
    raw = '{"mood": "ceria", "energy": "high", "schedule": []}'
    parsed = parse_daily_life_json(raw)
    assert parsed["mood"] == "ceria"

    # 2. Markdown fenced code block
    fenced = '```json\n{"mood": "santai", "energy": "medium", "schedule": []}\n```'
    parsed_fenced = parse_daily_life_json(fenced)
    assert parsed_fenced["mood"] == "santai"

    # 3. Text with preamble
    wrapped = 'Tentu, ini jadwalnya:\n{"mood": "tenang", "energy": "low", "schedule": []}\nSemoga cocok!'
    parsed_wrapped = parse_daily_life_json(wrapped)
    assert parsed_wrapped["mood"] == "tenang"


def test_resolve_current_activity():
    blocks = [
        ScheduleBlock("07:00", "08:30", "Bikin kopi & sarapan"),
        ScheduleBlock("08:30", "12:00", "Kerja desain"),
        ScheduleBlock("12:00", "13:30", "Makan siang"),
        ScheduleBlock("13:30", "18:00", "Lanjut kerja"),
        ScheduleBlock("18:00", "22:00", "Santai malam"),
    ]

    # In block
    t_work = datetime(2026, 10, 7, 10, 15, tzinfo=WIB)
    assert resolve_current_activity(blocks, t_work) == "Kerja desain"

    t_lunch = datetime(2026, 10, 7, 12, 45, tzinfo=WIB)
    assert resolve_current_activity(blocks, t_lunch) == "Makan siang"

    # Before first block
    t_early = datetime(2026, 10, 7, 6, 0, tzinfo=WIB)
    assert "bangun" in resolve_current_activity(blocks, t_early).lower()

    # After last block
    t_late = datetime(2026, 10, 7, 23, 15, tzinfo=WIB)
    assert "istirahat" in resolve_current_activity(blocks, t_late).lower() or "tidur" in resolve_current_activity(blocks, t_late).lower()


def test_format_schedule_summary():
    blocks = [
        ScheduleBlock("07:00", "08:00", "Sarapan"),
        ScheduleBlock("08:00", "12:00", "Kerja"),
    ]
    summary = format_schedule_summary(blocks)
    assert "07:00-08:00 Sarapan" in summary
    assert "08:00-12:00 Kerja" in summary


def test_get_fallback_daily_life(persona):
    fallback = get_fallback_daily_life(date(2026, 10, 7), persona)
    assert fallback.date == "2026-10-07"
    assert len(fallback.schedule) >= 5
    assert fallback.mood != ""
    assert fallback.energy in ["low", "medium", "high"]


@pytest.mark.asyncio
async def test_life_simulator_generate_and_idempotency(
    temp_store: MemoryStore, mock_gemini, persona, prompt_dir
):
    sim = LifeSimulator(
        gemini=mock_gemini,
        store=temp_store,
        persona=persona,
        prompts_dir=prompt_dir,
        llm_cfg=LLMSection(chat_model="test", utility_model="test-lite"),
        tz=WIB,
    )

    t_date = date(2026, 10, 7)

    # 1. First generation
    rec1 = await sim.generate_daily_life(target_date=t_date)
    assert rec1.date == "2026-10-07"
    assert rec1.mood == "senang dan berenergi"
    assert rec1.energy == "high"
    assert len(rec1.schedule) == 7
    mock_gemini.generate.assert_called_once()

    # Verify persisted in database
    db_rec = await temp_store.get_daily_life("2026-10-07")
    assert db_rec is not None
    assert db_rec.mood == "senang dan berenergi"

    # 2. Second call without force -> reuses record, does not call Gemini
    rec2 = await sim.generate_daily_life(target_date=t_date, force=False)
    assert rec2.mood == rec1.mood
    assert mock_gemini.generate.call_count == 1

    # 3. Third call with force=True -> re-rolls
    rec3 = await sim.generate_daily_life(target_date=t_date, force=True)
    assert mock_gemini.generate.call_count == 2


@pytest.mark.asyncio
async def test_life_simulator_fallback_on_gemini_error(
    temp_store: MemoryStore, persona, prompt_dir
):
    failing_gemini = MagicMock(spec=GeminiClient)
    failing_gemini.generate = AsyncMock(side_effect=GeminiError("API rate limit"))

    sim = LifeSimulator(
        gemini=failing_gemini,
        store=temp_store,
        persona=persona,
        prompts_dir=prompt_dir,
        llm_cfg=LLMSection(chat_model="test", utility_model="test-lite"),
        tz=WIB,
    )

    rec = await sim.generate_daily_life(target_date=date(2026, 10, 7))
    assert rec is not None
    assert rec.date == "2026-10-07"
    assert len(rec.schedule) >= 5

    # Saved to store even on fallback
    saved = await temp_store.get_daily_life("2026-10-07")
    assert saved is not None
    assert len(saved.schedule) == len(rec.schedule)


@pytest.mark.asyncio
async def test_prompt_builder_integrates_daily_life(
    temp_store: MemoryStore, persona, prompt_dir
):
    # Store daily life
    schedule = [
        {"from": "09:00", "to": "12:00", "activity": "Lagi menggambar ilustrasi poster"},
    ]
    await temp_store.save_daily_life(
        date="2026-10-07",
        schedule=schedule,
        mood="semangat",
        energy="high",
        notes="Kopi susu tadi enak banget",
    )

    builder = PromptBuilder(prompt_dir, persona, WIB)
    # Check at 10:00 WIB
    dt = datetime(2026, 10, 7, 10, 0, tzinfo=WIB)

    rendered = await builder.build_system_prompt_from_store(temp_store, dt=dt)
    assert "Lagi menggambar ilustrasi poster" in rendered
    assert "Kopi susu tadi enak banget" in rendered
    assert "semangat" in rendered
