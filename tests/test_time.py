from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from app.utils.time import day_name_id, format_local_id, from_utc_iso, is_quiet_hours, to_utc_iso

WIB = ZoneInfo("Asia/Jakarta")


def _wib(h: int, m: int = 0) -> datetime:
    return datetime(2026, 10, 6, h, m, tzinfo=WIB)


def test_quiet_hours_crossing_midnight():
    assert is_quiet_hours(_wib(23), "22:00", "06:00")
    assert is_quiet_hours(_wib(2), "22:00", "06:00")
    assert is_quiet_hours(_wib(22, 0), "22:00", "06:00")
    assert not is_quiet_hours(_wib(6, 0), "22:00", "06:00")
    assert not is_quiet_hours(_wib(12), "22:00", "06:00")


def test_quiet_hours_same_day_range():
    assert is_quiet_hours(_wib(13), "12:00", "14:00")
    assert not is_quiet_hours(_wib(14), "12:00", "14:00")


def test_utc_roundtrip():
    local = _wib(7, 30)
    iso = to_utc_iso(local)
    assert iso == "2026-10-06T00:30:00+00:00"
    assert from_utc_iso(iso) == local
    assert from_utc_iso("2026-10-06T00:30:00").tzinfo == timezone.utc


def test_indonesian_names():
    assert day_name_id(_wib(10)) == "Selasa"
    assert format_local_id(_wib(16, 15)) == "Selasa, 6 Oktober 2026 16:15"
