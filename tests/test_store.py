from __future__ import annotations

import pytest

from app.memory.store import MemoryStore


@pytest.fixture
async def temp_store(tmp_path):
    db_file = tmp_path / "test_companion.db"
    store = MemoryStore(db_file)
    await store.init_db()
    return store


@pytest.mark.asyncio
async def test_message_persistence(temp_store: MemoryStore):
    msg_id1 = await temp_store.save_message(role="user", content="Halo Nara")
    msg_id2 = await temp_store.save_message(role="bot", content="Hai juga!")

    assert msg_id1 > 0
    assert msg_id2 > msg_id1

    messages = await temp_store.get_recent_messages(limit=10)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Halo Nara"
    assert messages[1].role == "bot"
    assert messages[1].content == "Hai juga!"


@pytest.mark.asyncio
async def test_facts_and_supersede(temp_store: MemoryStore):
    # 1. Add initial fact
    f1_id = await temp_store.add_fact(category="work", content="Bekerja di Startup A", confidence=0.9)
    active_facts = await temp_store.get_active_facts()
    assert len(active_facts) == 1
    assert active_facts[0].id == f1_id
    assert active_facts[0].content == "Bekerja di Startup A"

    # 2. Supersede fact (FR-3.9)
    f2_id = await temp_store.supersede_fact(old_fact_id=f1_id, new_content="Pindah kerja ke Tech Corp B")
    active_facts = await temp_store.get_active_facts()
    assert len(active_facts) == 1
    assert active_facts[0].id == f2_id
    assert active_facts[0].content == "Pindah kerja ke Tech Corp B"

    # 3. Deactivate fact
    deactivated = await temp_store.deactivate_fact(f2_id)
    assert deactivated is True
    assert len(await temp_store.get_active_facts()) == 0


@pytest.mark.asyncio
async def test_open_threads(temp_store: MemoryStore):
    # 1. Add threads
    t1_id = await temp_store.add_thread(topic="Presentasi penting", target_date="2026-10-10")
    t2_id = await temp_store.add_thread(topic="Jalan-jalan ke Bali", target_date="2026-09-01")

    pending = await temp_store.get_pending_threads()
    assert len(pending) == 2

    # 2. Resolve thread
    await temp_store.resolve_thread(t1_id)
    pending_after_resolve = await temp_store.get_pending_threads()
    assert len(pending_after_resolve) == 1
    assert pending_after_resolve[0].id == t2_id

    # 3. Expire stale threads
    expired_count = await temp_store.expire_threads_before("2026-10-01")
    assert expired_count == 1
    assert len(await temp_store.get_pending_threads()) == 0


@pytest.mark.asyncio
async def test_summaries(temp_store: MemoryStore):
    await temp_store.add_summary(
        scope="daily",
        period_start="2026-10-05T00:00:00",
        period_end="2026-10-05T23:59:59",
        content="Ringkasan hari kemarin",
    )
    await temp_store.add_summary(
        scope="daily",
        period_start="2026-10-06T00:00:00",
        period_end="2026-10-06T23:59:59",
        content="Ringkasan hari ini",
    )

    latest = await temp_store.get_latest_summary()
    assert latest is not None
    assert latest.content == "Ringkasan hari ini"

    dailies = await temp_store.get_recent_daily_summaries(limit=5)
    assert len(dailies) == 2


@pytest.mark.asyncio
async def test_state_and_llm_logging(temp_store: MemoryStore):
    await temp_store.set_state("test_key", "value_123")
    assert await temp_store.get_state("test_key") == "value_123"

    # Overwrite state
    await temp_store.set_state("test_key", "value_456")
    assert await temp_store.get_state("test_key") == "value_456"

    # LLM logging
    await temp_store.log_llm_call(
        purpose="chat",
        model="gemini-test",
        input_tokens=100,
        output_tokens=25,
        latency_ms=300,
        success=True,
    )


@pytest.mark.asyncio
async def test_reset_memory(temp_store: MemoryStore):
    await temp_store.save_message("user", "test")
    await temp_store.add_fact("work", "dev")
    await temp_store.add_thread("meeting")
    await temp_store.add_summary("daily", "2026-10-06", "2026-10-06", "summary")
    await temp_store.set_state("last_extracted_message_id", "1")

    await temp_store.reset_memory()

    assert len(await temp_store.get_recent_messages()) == 0
    assert len(await temp_store.get_active_facts()) == 0
    assert len(await temp_store.get_pending_threads()) == 0
    assert await temp_store.get_latest_summary() is None
    assert await temp_store.get_state("last_extracted_message_id") is None
