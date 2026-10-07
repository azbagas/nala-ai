from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import pytest

from app.config import Bonus, ProactiveSection, TimeRange, Window
from app.memory.store import MemoryStore
from app.proactive.checks import is_in_backoff, run_presend_checks


@pytest.fixture
async def temp_store(tmp_path):
    db_file = tmp_path / "test_checks.db"
    store = MemoryStore(db_file)
    await store.init_db()
    return store


@pytest.fixture
def proactive_cfg() -> ProactiveSection:
    return ProactiveSection(
        daily_cap=4,
        quiet_hours=TimeRange(start="22:00", end="06:00"),
        windows=[
            Window(name="morning", start="06:00", end="08:00", chance=0.8),
            Window(name="midday", start="12:00", end="14:00", chance=0.7),
        ],
        bonus=Bonus(),
        skip_if_user_active_within_minutes=20,
        backoff_after_ignored=2,
        stop_after_ignored=4,
        message_type_weights={"checkin": 3, "followup": 4},
    )


@pytest.mark.asyncio
async def test_all_checks_pass(temp_store: MemoryStore, proactive_cfg: ProactiveSection):
    tz = ZoneInfo("Asia/Jakarta")
    # 10:00 WIB is 03:00 UTC, daytime, outside quiet hours
    now = datetime(2026, 10, 7, 3, 0, 0, tzinfo=timezone.utc)

    result = await run_presend_checks(temp_store, proactive_cfg, tz, now=now)
    assert result.can_send is True
    assert result.reason is None


@pytest.mark.asyncio
async def test_quiet_hours_check(temp_store: MemoryStore, proactive_cfg: ProactiveSection):
    tz = ZoneInfo("Asia/Jakarta")
    # 23:00 WIB is 16:00 UTC (quiet hours 22:00 - 06:00)
    now = datetime(2026, 10, 7, 16, 0, 0, tzinfo=timezone.utc)

    result = await run_presend_checks(temp_store, proactive_cfg, tz, now=now)
    assert result.can_send is False
    assert result.reason == "quiet_hours"


@pytest.mark.asyncio
async def test_paused_check(temp_store: MemoryStore, proactive_cfg: ProactiveSection):
    tz = ZoneInfo("Asia/Jakarta")
    now = datetime(2026, 10, 7, 3, 0, 0, tzinfo=timezone.utc)

    # Paused until 1 hour from now
    await temp_store.set_paused_until(now + timedelta(hours=1))
    result = await run_presend_checks(temp_store, proactive_cfg, tz, now=now)
    assert result.can_send is False
    assert result.reason == "paused"

    # Paused until 1 hour in the past (expired pause)
    await temp_store.set_paused_until(now - timedelta(hours=1))
    result2 = await run_presend_checks(temp_store, proactive_cfg, tz, now=now)
    assert result2.can_send is True


@pytest.mark.asyncio
async def test_user_active_recently(temp_store: MemoryStore, proactive_cfg: ProactiveSection):
    tz = ZoneInfo("Asia/Jakarta")
    now = datetime(2026, 10, 7, 3, 0, 0, tzinfo=timezone.utc)

    # User messaged 5 minutes ago (< 20 min threshold)
    await temp_store.update_last_user_message_time(now - timedelta(minutes=5))
    result = await run_presend_checks(temp_store, proactive_cfg, tz, now=now)
    assert result.can_send is False
    assert result.reason == "user_recently_active"

    # User messaged 25 minutes ago (> 20 min threshold)
    await temp_store.update_last_user_message_time(now - timedelta(minutes=25))
    result2 = await run_presend_checks(temp_store, proactive_cfg, tz, now=now)
    assert result2.can_send is True


@pytest.mark.asyncio
async def test_daily_cap_reached(temp_store: MemoryStore, proactive_cfg: ProactiveSection):
    tz = ZoneInfo("Asia/Jakarta")
    now = datetime(2026, 10, 7, 3, 0, 0, tzinfo=timezone.utc)
    # Local date: 2026-10-07

    # Create 4 sent plans for today
    for i in range(4):
        p_id = await temp_store.create_plan("2026-10-07", f"win_{i}", f"2026-10-07T0{i}:00:00Z")
        m_id = await temp_store.save_message("bot", f"Msg {i}", origin="proactive")
        await temp_store.update_plan_status(p_id, "sent", message_id=m_id)

    result = await run_presend_checks(temp_store, proactive_cfg, tz, now=now)
    assert result.can_send is False
    assert result.reason == "daily_cap_reached"


@pytest.mark.asyncio
async def test_stopped_after_ignored(temp_store: MemoryStore, proactive_cfg: ProactiveSection):
    tz = ZoneInfo("Asia/Jakarta")
    now = datetime(2026, 10, 7, 3, 0, 0, tzinfo=timezone.utc)

    for _ in range(4):
        await temp_store.increment_ignored_count()

    assert await temp_store.get_ignored_count() == 4
    result = await run_presend_checks(temp_store, proactive_cfg, tz, now=now)
    assert result.can_send is False
    assert result.reason == "stopped_after_ignored"


def test_is_in_backoff():
    assert is_in_backoff(1, backoff_threshold=2) is False
    assert is_in_backoff(2, backoff_threshold=2) is True
    assert is_in_backoff(3, backoff_threshold=2) is True
