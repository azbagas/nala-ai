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
    msg_id1 = await temp_store.save_message(role="user", content="Halo Nala")
    msg_id2 = await temp_store.save_message(role="bot", content="Hai juga!")

    assert msg_id1 > 0
    assert msg_id2 > msg_id1

    messages = await temp_store.get_recent_messages(limit=10)
    assert len(messages) == 2
    assert messages[0].role == "user"
    assert messages[0].content == "Halo Nala"
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


@pytest.mark.asyncio
async def test_proactive_plans_and_state(temp_store: MemoryStore):
    # Test plans
    p1_id = await temp_store.create_plan("2026-10-07", "morning", "2026-10-07T07:15:00Z")
    p2_id = await temp_store.create_plan("2026-10-07", "midday", "2026-10-07T12:30:00Z")
    assert p1_id > 0
    assert p2_id > p1_id

    # Duplicate creation should be idempotent
    dup_id = await temp_store.create_plan("2026-10-07", "morning", "2026-10-07T07:15:00Z")
    assert dup_id == p1_id

    plans = await temp_store.get_plans_for_date("2026-10-07")
    assert len(plans) == 2
    assert plans[0].window_name == "morning"
    assert plans[1].window_name == "midday"

    # Status update with foreign key to messages
    msg_id = await temp_store.save_message(role="bot", content="Halo pagi!", origin="proactive", proactive_type="checkin")
    updated = await temp_store.update_plan_status(p1_id, "sent", message_id=msg_id)
    assert updated is True

    p1 = await temp_store.get_plan_by_id(p1_id)
    assert p1 is not None
    assert p1.status == "sent"
    assert p1.message_id == msg_id

    sent_count = await temp_store.count_sent_proactive_for_date("2026-10-07")
    assert sent_count == 1

    pending = await temp_store.get_pending_plans()
    assert len(pending) == 1
    assert pending[0].id == p2_id

    # State helpers: ignored count
    assert await temp_store.get_ignored_count() == 0
    await temp_store.increment_ignored_count()
    await temp_store.increment_ignored_count()
    assert await temp_store.get_ignored_count() == 2
    await temp_store.reset_ignored_count()
    assert await temp_store.get_ignored_count() == 0

    # State helpers: last user message & pause
    from datetime import datetime, timezone
    now = datetime(2026, 10, 7, 10, 0, 0, tzinfo=timezone.utc)
    await temp_store.update_last_user_message_time(now)
    last_msg = await temp_store.get_last_user_message_time()
    assert last_msg == now

    await temp_store.set_paused_until(now)
    assert await temp_store.get_paused_until() == now
    await temp_store.set_paused_until(None)
    assert await temp_store.get_paused_until() is None

    # Owner chat id
    await temp_store.set_owner_chat_id(123456)
    assert await temp_store.get_owner_chat_id() == 123456


@pytest.mark.asyncio
async def test_daily_life_storage(temp_store: MemoryStore):
    schedule = [
        {"from": "07:00", "to": "08:00", "activity": "Sarapan & bikin kopi"},
        {"from": "09:00", "to": "12:00", "activity": "Kerja desain freelance"},
        {"from": "12:00", "to": "13:00", "activity": "Makan siang & istirahat"},
    ]

    await temp_store.save_daily_life(
        date="2026-10-07",
        schedule=schedule,
        mood="semangat dan ceria",
        energy="high",
        notes="Mochi numpahin air tapi lucu",
    )

    rec = await temp_store.get_daily_life("2026-10-07")
    assert rec is not None
    assert rec.date == "2026-10-07"
    assert rec.mood == "semangat dan ceria"
    assert rec.energy == "high"
    assert rec.notes == "Mochi numpahin air tapi lucu"
    assert len(rec.schedule) == 3
    assert rec.schedule[0].from_time == "07:00"
    assert rec.schedule[0].activity == "Sarapan & bikin kopi"

    # Upsert on conflict
    await temp_store.save_daily_life(
        date="2026-10-07",
        schedule=schedule,
        mood="agak ngantuk",
        energy="low",
        notes="Mau tidur siang",
    )
    updated_rec = await temp_store.get_daily_life("2026-10-07")
    assert updated_rec is not None
    assert updated_rec.mood == "agak ngantuk"
    assert updated_rec.energy == "low"

    # Recent daily life
    await temp_store.save_daily_life(
        date="2026-10-06",
        schedule=schedule[:1],
        mood="santai",
        energy="medium",
    )
    recents = await temp_store.get_recent_daily_life(limit=5)
    assert len(recents) == 2
    assert recents[0].date == "2026-10-07"
    assert recents[1].date == "2026-10-06"


@pytest.mark.asyncio
async def test_get_today_llm_stats(temp_store: MemoryStore):
    from datetime import datetime, timezone

    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    # Initially 0 stats
    stats = await temp_store.get_today_llm_stats(today)
    assert stats["total_calls"] == 0
    assert stats["total_input_tokens"] == 0
    assert stats["total_output_tokens"] == 0
    assert stats["avg_latency_ms"] == 0
    assert stats["success_rate"] == 1.0

    # Insert multiple calls
    await temp_store.log_llm_call(
        purpose="chat",
        model="gemini-2.5-flash",
        input_tokens=100,
        output_tokens=50,
        latency_ms=200,
        success=True,
    )
    await temp_store.log_llm_call(
        purpose="proactive",
        model="gemini-2.5-flash",
        input_tokens=150,
        output_tokens=50,
        latency_ms=400,
        success=False,
    )

    stats = await temp_store.get_today_llm_stats(today)
    assert stats["total_calls"] == 2
    assert stats["total_input_tokens"] == 250
    assert stats["total_output_tokens"] == 100
    assert stats["avg_latency_ms"] == 300
    assert stats["success_rate"] == 0.5


@pytest.mark.asyncio
async def test_dynamic_user_name(temp_store: MemoryStore):
    assert await temp_store.get_user_name() is None

    await temp_store.set_user_name("Bagas")
    assert await temp_store.get_user_name() == "Bagas"

    facts = await temp_store.get_active_facts()
    identity_facts = [f for f in facts if f.category == "identity"]
    assert len(identity_facts) == 1
    assert "Bagas" in identity_facts[0].content

    await temp_store.set_user_name("Azhar")
    assert await temp_store.get_user_name() == "Azhar"

    await temp_store.reset_memory()
    assert await temp_store.get_user_name() is None
