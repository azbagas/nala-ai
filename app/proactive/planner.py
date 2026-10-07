"""Daily proactive planner (PRD §4.4, §8.2).

Creates the day's proactive send times based on configurable time windows,
probabilities, quiet hours, and daily cap. Persisted idempotently in SQLite
so bot restarts do not re-roll or duplicate messages.
"""

from __future__ import annotations

import logging
import random
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.config import ProactiveSection
from app.memory.store import MemoryStore, PlanRecord
from app.utils.time import is_quiet_hours, now_utc, parse_hhmm, to_utc_iso

log = logging.getLogger(__name__)


async def plan_day(
    store: MemoryStore,
    proactive_cfg: ProactiveSection,
    tz: ZoneInfo,
    target_date: date | None = None,
    now: datetime | None = None,
    rng: random.Random | None = None,
) -> list[PlanRecord]:
    """Generate or retrieve the proactive schedule for a given date.

    If a plan for the date already exists in the database, it is returned
    as-is without re-rolling (idempotency, FR-4.5).
    """
    _rng = rng or random.Random()
    now_dt = now or now_utc()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    local_now = now_dt.astimezone(tz)
    plan_date = target_date or local_now.date()
    date_str = plan_date.strftime("%Y-%m-%d")

    # 1. Check existing plan in database (idempotency)
    existing_plans = await store.get_plans_for_date(date_str)
    if existing_plans:
        log.info(
            "proactive_plan_reused date=%s count=%d pending=%d",
            date_str,
            len(existing_plans),
            sum(1 for p in existing_plans if p.status == "planned"),
        )
        return existing_plans

    # 2. Check frequency setting from state (PRD §4.6)
    freq = (await store.get_state("proactive_frequency", default="normal") or "normal").lower()
    if freq == "low":
        effective_cap = max(1, proactive_cfg.daily_cap // 2)
        chance_mult = 0.6
    elif freq == "high":
        effective_cap = proactive_cfg.daily_cap + 1
        chance_mult = 1.2
    else:
        effective_cap = proactive_cfg.daily_cap
        chance_mult = 1.0

    candidates: list[tuple[str, datetime]] = []

    for window in proactive_cfg.windows:
        # Roll against probability chance scaled by frequency
        eff_chance = min(1.0, window.chance * chance_mult)
        if _rng.random() > eff_chance:
            continue

        start_time = parse_hhmm(window.start)
        end_time = parse_hhmm(window.end)

        start_dt = datetime.combine(plan_date, start_time, tzinfo=tz)
        end_dt = datetime.combine(plan_date, end_time, tzinfo=tz)

        # If scheduling for today, must be in the future (at least now + 1 min)
        if plan_date == local_now.date():
            earliest = max(start_dt, local_now + timedelta(minutes=1))
        else:
            earliest = start_dt

        if earliest >= end_dt:
            # Window has already passed
            continue

        # Uniform random timestamp between earliest and end_dt
        span_seconds = int((end_dt - earliest).total_seconds())
        if span_seconds <= 0:
            continue

        offset = _rng.randint(0, span_seconds)
        planned_dt = earliest + timedelta(seconds=offset)

        # Check quiet hours (FR-4.4)
        if is_quiet_hours(planned_dt, proactive_cfg.quiet_hours.start, proactive_cfg.quiet_hours.end):
            continue

        candidates.append((window.name, planned_dt))

    # 3. Roll for bonus spontaneous message (FR-4.9)
    eff_bonus_chance = min(1.0, proactive_cfg.bonus.chance * chance_mult)
    if proactive_cfg.bonus.max_per_day > 0 and _rng.random() <= eff_bonus_chance:
        bonus_start = parse_hhmm(proactive_cfg.bonus.awake_start)
        bonus_end = parse_hhmm(proactive_cfg.bonus.awake_end)

        b_start_dt = datetime.combine(plan_date, bonus_start, tzinfo=tz)
        b_end_dt = datetime.combine(plan_date, bonus_end, tzinfo=tz)

        if plan_date == local_now.date():
            b_earliest = max(b_start_dt, local_now + timedelta(minutes=1))
        else:
            b_earliest = b_start_dt

        if b_earliest < b_end_dt:
            b_span = int((b_end_dt - b_earliest).total_seconds())
            if b_span > 0:
                b_offset = _rng.randint(0, b_span)
                b_planned_dt = b_earliest + timedelta(seconds=b_offset)
                if not is_quiet_hours(b_planned_dt, proactive_cfg.quiet_hours.start, proactive_cfg.quiet_hours.end):
                    candidates.append(("bonus", b_planned_dt))

    # 4. Enforce daily cap (FR-4.4, §8.2)
    # Sort chronologically
    candidates.sort(key=lambda c: c[1])
    if len(candidates) > effective_cap:
        # Keep up to effective_cap entries
        candidates = candidates[: effective_cap]

    # 5. Persist candidates to database
    for window_name, planned_dt in candidates:
        utc_iso = to_utc_iso(planned_dt)
        await store.create_plan(date=date_str, window_name=window_name, planned_at=utc_iso)

    new_plans = await store.get_plans_for_date(date_str)
    log.info(
        "proactive_plan_created date=%s total_planned=%d windows=%s",
        date_str,
        len(new_plans),
        [p.window_name for p in new_plans],
    )
    return new_plans
