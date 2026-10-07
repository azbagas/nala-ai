from __future__ import annotations

import random
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
import pytest

from app.config import Bonus, ProactiveSection, TimeRange, Window
from app.memory.store import MemoryStore
from app.proactive.planner import plan_day
from app.utils.time import from_utc_iso


@pytest.fixture
async def temp_store(tmp_path):
    db_file = tmp_path / "test_planner.db"
    store = MemoryStore(db_file)
    await store.init_db()
    return store


@pytest.fixture
def proactive_cfg() -> ProactiveSection:
    return ProactiveSection(
        daily_cap=4,
        quiet_hours=TimeRange(start="22:00", end="06:00"),
        windows=[
            Window(name="morning", start="06:00", end="08:00", chance=1.0),
            Window(name="midday", start="12:00", end="14:00", chance=1.0),
            Window(name="afternoon", start="15:30", end="17:30", chance=1.0),
            Window(name="evening", start="19:00", end="21:00", chance=1.0),
        ],
        bonus=Bonus(chance=0.0, max_per_day=0),
        skip_if_user_active_within_minutes=20,
        backoff_after_ignored=2,
        stop_after_ignored=4,
        message_type_weights={"checkin": 3, "followup": 4},
    )


@pytest.mark.asyncio
async def test_plan_day_generation(temp_store: MemoryStore, proactive_cfg: ProactiveSection):
    tz = ZoneInfo("Asia/Jakarta")
    # Start at 05:00 WIB (22:00 UTC previous day), before morning window
    now = datetime(2026, 10, 6, 22, 0, 0, tzinfo=timezone.utc)
    rng = random.Random(42)

    plans = await plan_day(temp_store, proactive_cfg, tz, target_date=date(2026, 10, 7), now=now, rng=rng)

    assert len(plans) == 4
    window_names = [p.window_name for p in plans]
    assert window_names == ["morning", "midday", "afternoon", "evening"]

    # Verify each planned time falls within its local window
    for p in plans:
        planned_dt = from_utc_iso(p.planned_at).astimezone(tz)
        assert planned_dt.date() == date(2026, 10, 7)
        if p.window_name == "morning":
            assert 6 <= planned_dt.hour < 8 or (planned_dt.hour == 8 and planned_dt.minute == 0)
        elif p.window_name == "midday":
            assert 12 <= planned_dt.hour < 14 or (planned_dt.hour == 14 and planned_dt.minute == 0)


@pytest.mark.asyncio
async def test_plan_day_idempotency(temp_store: MemoryStore, proactive_cfg: ProactiveSection):
    tz = ZoneInfo("Asia/Jakarta")
    now = datetime(2026, 10, 6, 22, 0, 0, tzinfo=timezone.utc)
    rng = random.Random(42)

    # First run
    plans1 = await plan_day(temp_store, proactive_cfg, tz, target_date=date(2026, 10, 7), now=now, rng=rng)
    assert len(plans1) == 4

    # Second run with different RNG
    rng2 = random.Random(999)
    plans2 = await plan_day(temp_store, proactive_cfg, tz, target_date=date(2026, 10, 7), now=now, rng=rng2)

    # Must be completely identical
    assert len(plans2) == len(plans1)
    for p1, p2 in zip(plans1, plans2):
        assert p1.id == p2.id
        assert p1.window_name == p2.window_name
        assert p1.planned_at == p2.planned_at


@pytest.mark.asyncio
async def test_plan_day_cap_enforcement(temp_store: MemoryStore):
    tz = ZoneInfo("Asia/Jakarta")
    now = datetime(2026, 10, 6, 22, 0, 0, tzinfo=timezone.utc)
    rng = random.Random(42)

    cfg = ProactiveSection(
        daily_cap=2,  # Cap at 2
        quiet_hours=TimeRange(start="22:00", end="06:00"),
        windows=[
            Window(name="morning", start="06:00", end="08:00", chance=1.0),
            Window(name="midday", start="12:00", end="14:00", chance=1.0),
            Window(name="evening", start="19:00", end="21:00", chance=1.0),
        ],
        bonus=Bonus(chance=0.0, max_per_day=0),
        skip_if_user_active_within_minutes=20,
        backoff_after_ignored=2,
        stop_after_ignored=4,
        message_type_weights={"checkin": 3},
    )

    plans = await plan_day(temp_store, cfg, tz, target_date=date(2026, 10, 7), now=now, rng=rng)
    assert len(plans) == 2


@pytest.mark.asyncio
async def test_plan_day_past_windows_skipped(temp_store: MemoryStore, proactive_cfg: ProactiveSection):
    tz = ZoneInfo("Asia/Jakarta")
    # Current time is 13:00 WIB (06:00 UTC) on 2026-10-07
    now = datetime(2026, 10, 7, 6, 0, 0, tzinfo=timezone.utc)
    rng = random.Random(42)

    plans = await plan_day(temp_store, proactive_cfg, tz, target_date=date(2026, 10, 7), now=now, rng=rng)

    # Morning (06:00 - 08:00) has already passed, so it should not be scheduled
    window_names = [p.window_name for p in plans]
    assert "morning" not in window_names
    assert "midday" in window_names or "afternoon" in window_names or "evening" in window_names


@pytest.mark.asyncio
async def test_plan_day_bonus_window(temp_store: MemoryStore):
    tz = ZoneInfo("Asia/Jakarta")
    now = datetime(2026, 10, 6, 22, 0, 0, tzinfo=timezone.utc)
    rng = random.Random(42)

    cfg = ProactiveSection(
        daily_cap=5,
        quiet_hours=TimeRange(start="22:00", end="06:00"),
        windows=[
            Window(name="morning", start="06:00", end="08:00", chance=1.0),
        ],
        bonus=Bonus(chance=1.0, max_per_day=1, awake_start="10:00", awake_end="11:00"),
        skip_if_user_active_within_minutes=20,
        backoff_after_ignored=2,
        stop_after_ignored=4,
        message_type_weights={"checkin": 3},
    )

    plans = await plan_day(temp_store, cfg, tz, target_date=date(2026, 10, 7), now=now, rng=rng)
    assert len(plans) == 2
    window_names = [p.window_name for p in plans]
    assert "morning" in window_names
    assert "bonus" in window_names
