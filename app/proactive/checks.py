"""Pre-send checks and backoff logic.

Ensures the bot does not send proactive messages during quiet hours, when
paused, if the user chatted recently, when the daily cap is reached, or
if the user has ignored too many previous proactive attempts.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.config import ProactiveSection
from app.memory.store import MemoryStore
from app.utils.time import is_quiet_hours, now_utc

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PreSendResult:
    can_send: bool
    reason: str | None = None


def frequency_profile(freq: str | None, daily_cap: int) -> tuple[int, float]:
    """Return (effective_daily_cap, chance_multiplier) for a /frequency setting."""
    freq = (freq or "normal").lower()
    if freq == "low":
        return max(1, daily_cap // 2), 0.6
    if freq == "high":
        return daily_cap + 1, 1.2
    return daily_cap, 1.0


def is_in_backoff(ignored_count: int, backoff_threshold: int) -> bool:
    """Return True if ignored_count reached the adaptive backoff threshold."""
    return ignored_count >= backoff_threshold


async def run_presend_checks(
    store: MemoryStore,
    proactive_cfg: ProactiveSection,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> PreSendResult:
    """Evaluate all pre-send conditions before initiating a proactive message."""
    now_dt = now or now_utc()
    if now_dt.tzinfo is None:
        now_dt = now_dt.replace(tzinfo=timezone.utc)

    local_dt = now_dt.astimezone(tz)
    today_date = local_dt.strftime("%Y-%m-%d")

    # 1. Quiet hours check
    if is_quiet_hours(local_dt, proactive_cfg.quiet_hours.start, proactive_cfg.quiet_hours.end):
        log.info("presend_skipped reason=quiet_hours time=%s", local_dt.strftime("%H:%M"))
        return PreSendResult(can_send=False, reason="quiet_hours")

    # 2. Pause check
    paused_until = await store.get_paused_until()
    if paused_until is not None and now_dt < paused_until:
        log.info("presend_skipped reason=paused until=%s", paused_until.isoformat())
        return PreSendResult(can_send=False, reason="paused")

    # 3. User recent activity check
    last_user_msg = await store.get_last_user_message_time()
    if last_user_msg is not None:
        elapsed_seconds = (now_dt - last_user_msg).total_seconds()
        threshold_seconds = proactive_cfg.skip_if_user_active_within_minutes * 60
        if 0 <= elapsed_seconds < threshold_seconds:
            log.info(
                "presend_skipped reason=user_recently_active elapsed_min=%.1f threshold_min=%d",
                elapsed_seconds / 60,
                proactive_cfg.skip_if_user_active_within_minutes,
            )
            return PreSendResult(can_send=False, reason="user_recently_active")

    # 4. Daily cap check
    freq = await store.get_state("proactive_frequency", default="normal")
    effective_cap, _ = frequency_profile(freq, proactive_cfg.daily_cap)
    sent_count = await store.count_sent_proactive_for_date(today_date)
    if sent_count >= effective_cap:
        log.info(
            "presend_skipped reason=daily_cap_reached sent=%d cap=%d date=%s",
            sent_count,
            effective_cap,
            today_date,
        )
        return PreSendResult(can_send=False, reason="daily_cap_reached")

    # 5. Ignored backoff check
    ignored_count = await store.get_ignored_count()
    if ignored_count >= proactive_cfg.stop_after_ignored:
        log.info(
            "presend_skipped reason=stopped_after_ignored ignored_count=%d threshold=%d",
            ignored_count,
            proactive_cfg.stop_after_ignored,
        )
        return PreSendResult(can_send=False, reason="stopped_after_ignored")

    return PreSendResult(can_send=True)
