from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import pytest

from app.engine.intent import detect_natural_control_intent

WIB = ZoneInfo("Asia/Jakarta")


def test_detect_sleep_intent():
    # 23:00 WIB (16:00 UTC)
    now = datetime(2026, 10, 7, 23, 0, 0, tzinfo=WIB)

    res1 = detect_natural_control_intent("aku mau tidur dulu ya, ngantuk", tz=WIB, now=now)
    assert res1.is_pause is True
    assert res1.reason == "sleep"
    assert res1.pause_until is not None
    # Wake date should be tomorrow morning
    assert res1.pause_until.date() > now.date() or res1.pause_until.hour >= 7

    res2 = detect_natural_control_intent("good night Nala! bobo dulu ya", tz=WIB, now=now)
    assert res2.is_pause is True
    assert res2.reason == "sleep"


def test_detect_pause_today_intent():
    now = datetime(2026, 10, 7, 10, 0, 0, tzinfo=WIB)

    res = detect_natural_control_intent("jangan chat dulu hari ini, lagi ada acara keluarga", tz=WIB, now=now)
    assert res.is_pause is True
    assert res.reason == "pause_today"
    assert res.pause_until is not None
    assert res.pause_until.hour == 23
    assert res.pause_until.minute == 59


def test_detect_timed_pause_intent():
    now = datetime(2026, 10, 7, 10, 0, 0, tzinfo=WIB)

    res = detect_natural_control_intent("jangan chat dulu 2 jam ya, lagi rapat", tz=WIB, now=now)
    assert res.is_pause is True
    assert res.reason == "pause_timed"
    assert res.pause_until == datetime(2026, 10, 7, 12, 0, 0, tzinfo=WIB)


def test_detect_busy_pause_intent():
    now = datetime(2026, 10, 7, 10, 0, 0, tzinfo=WIB)

    res = detect_natural_control_intent("lagi sibuk banget nih di kantor", tz=WIB, now=now)
    assert res.is_pause is True
    assert res.reason == "pause_busy"
    assert res.pause_until is not None


def test_detect_resume_intent():
    now = datetime(2026, 10, 7, 8, 0, 0, tzinfo=WIB)

    res = detect_natural_control_intent("pagi! udah bangun nih aku", tz=WIB, now=now)
    assert res.is_resume is True
    assert res.reason == "user_resumed"


def test_no_intent_on_normal_chat():
    now = datetime(2026, 10, 7, 10, 0, 0, tzinfo=WIB)

    res = detect_natural_control_intent("halo Nala, lagi ngapain nih?", tz=WIB, now=now)
    assert res.is_pause is False
    assert res.is_resume is False
    assert res.pause_until is None
