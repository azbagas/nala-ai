from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock
from zoneinfo import ZoneInfo

import pytest

from app.config import LLMSection
from app.engine.gemini_client import GeminiResult
from app.memory.extractor import MemoryExtractor, parse_extraction_json
from app.memory.store import MemoryStore

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
WIB = ZoneInfo("Asia/Jakarta")


def test_parse_extraction_json_clean():
    raw = """{
      "new_facts": [{"category": "work", "content": "Kerja di Google", "confidence": 0.9}],
      "updated_facts": [{"id": 1, "new_content": "Pindah ke Tokyo"}],
      "new_threads": [{"topic": "Trip ke Tokyo", "detail": "Beli tiket", "target_date": "2026-11-01", "followup_after": null}],
      "resolved_thread_ids": [5],
      "mood_note": "Ceria dan antusias"
    }"""
    payload = parse_extraction_json(raw)
    assert len(payload.new_facts) == 1
    assert payload.new_facts[0].content == "Kerja di Google"
    assert len(payload.updated_facts) == 1
    assert payload.updated_facts[0].id == 1
    assert len(payload.new_threads) == 1
    assert payload.resolved_thread_ids == [5]
    assert payload.mood_note == "Ceria dan antusias"


def test_parse_extraction_json_markdown_block():
    raw = """Berikut hasilnya:
```json
{
  "new_facts": [{"category": "hobby", "content": "Suka kopi latte", "confidence": 0.85}],
  "updated_facts": [],
  "new_threads": [],
  "resolved_thread_ids": [],
  "mood_note": null
}
```
"""
    payload = parse_extraction_json(raw)
    assert len(payload.new_facts) == 1
    assert payload.new_facts[0].category == "hobby"


@pytest.mark.asyncio
async def test_extract_new_memories(tmp_path):
    store = MemoryStore(tmp_path / "test.db")
    await store.init_db()

    # Pre-populate an old fact and an old thread
    old_f_id = await store.add_fact("work", "Junior Engineer")
    old_t_id = await store.add_thread("Wawancara kerja")

    # Add conversation messages
    await store.save_message("user", "Aku baru aja dipromosikan jadi Senior Engineer!")
    await store.save_message("bot", "Wah selamat ya!")
    await store.save_message("user", "Oh ya wawancara yang kemarin udah selesai dan lolos.")

    mock_gemini = AsyncMock()
    json_response = f"""{{
      "new_facts": [{{"category": "work", "content": "Posisi saat ini Senior Engineer", "confidence": 0.95}}],
      "updated_facts": [{{"id": {old_f_id}, "new_content": "Sudah bukan Junior, sekarang Senior"}}],
      "new_threads": [{{"topic": "Rayain promosi jabatan", "detail": "Makan bareng", "target_date": "2026-10-12"}}],
      "resolved_thread_ids": [{old_t_id}],
      "mood_note": "Sangat senang"
    }}"""
    mock_gemini.generate.return_value = GeminiResult(
        text=json_response,
        model="gemini-test",
        input_tokens=200,
        output_tokens=100,
        latency_ms=300,
    )

    extractor = MemoryExtractor(
        gemini=mock_gemini,
        store=store,
        prompts_dir=PROMPTS_DIR,
        llm_cfg=LLMSection(chat_model="test", utility_model="test-lite"),
        tz=WIB,
    )

    summary = await extractor.extract_new_memories()

    assert summary.processed_messages == 3
    assert summary.new_facts_count == 1
    assert summary.updated_facts_count == 1
    assert summary.new_threads_count == 1
    assert summary.resolved_threads_count == 1

    # Check store state
    active_facts = await store.get_active_facts()
    # The old fact was superseded, so only 2 active facts remain (the superseded one + the brand new one)
    assert len(active_facts) == 2
    contents = [f.content for f in active_facts]
    assert "Posisi saat ini Senior Engineer" in contents

    # Check thread resolution
    pending_threads = await store.get_pending_threads()
    assert len(pending_threads) == 1
    assert pending_threads[0].topic == "Rayain promosi jabatan"

    # Check pointer was updated
    last_id = await store.get_state("last_extracted_message_id")
    assert last_id == "3"
