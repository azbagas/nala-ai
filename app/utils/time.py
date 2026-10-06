"""Time helpers (PRD NFR-10): store UTC, schedule/display in Asia/Jakarta."""

from __future__ import annotations

from datetime import datetime, time, timezone
from zoneinfo import ZoneInfo

DAY_NAMES_ID = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
MONTH_NAMES_ID = [
    "Januari", "Februari", "Maret", "April", "Mei", "Juni",
    "Juli", "Agustus", "September", "Oktober", "November", "Desember",
]


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def now_local(tz: ZoneInfo) -> datetime:
    return now_utc().astimezone(tz)


def to_utc_iso(dt: datetime) -> str:
    """Serialize an aware datetime as UTC ISO-8601 for storage."""
    if dt.tzinfo is None:
        raise ValueError("naive datetime; attach a timezone first")
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def from_utc_iso(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def in_time_range(t: time, start: time, end: time) -> bool:
    """True if t is in [start, end). Handles ranges crossing midnight (e.g. 22:00-06:00)."""
    if start == end:
        return False
    if start < end:
        return start <= t < end
    return t >= start or t < end


def is_quiet_hours(dt_local: datetime, start: str, end: str) -> bool:
    return in_time_range(dt_local.time(), parse_hhmm(start), parse_hhmm(end))


def day_name_id(dt: datetime) -> str:
    return DAY_NAMES_ID[dt.weekday()]


def format_local_id(dt_local: datetime) -> str:
    """e.g. 'Selasa, 6 Oktober 2026 16:15'."""
    return (
        f"{day_name_id(dt_local)}, {dt_local.day} {MONTH_NAMES_ID[dt_local.month - 1]} "
        f"{dt_local.year} {dt_local:%H:%M}"
    )
