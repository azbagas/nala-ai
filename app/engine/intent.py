"""Natural-language intent detection for pause, sleep, and resume (PRD §4.6, §8.1).

Detects conversational control statements from the user (e.g., "mau tidur",
"jangan chat dulu hari ini", "lagi sibuk banget", "udah bangun nih") and
computes the appropriate pause duration without requiring explicit slash commands.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from app.utils.time import now_local

# Regex patterns for natural language control
_SLEEP_PATTERN = re.compile(
    r"\b(mau tidur|mo tidur|tidur dulu|bobo dulu|mo bobo|ngantuk mau tidur|good night|selamat tidur|selamat malam mau tidur)\b",
    re.IGNORECASE,
)

_PAUSE_TODAY_PATTERN = re.compile(
    r"\b(jangan chat dulu hari ini|jangan wa dulu hari ini|jangan ganggu hari ini|stop chat hari ini|stop chat dulu hari ini|libur chat hari ini)\b",
    re.IGNORECASE,
)

_PAUSE_TIMED_PATTERN = re.compile(
    r"\b(?:jangan chat|pause|sibuk|repot)(?: dulu)?\s+(\d+)\s*(jam|menit|hari)\b",
    re.IGNORECASE,
)

_PAUSE_BUSY_PATTERN = re.compile(
    r"\b(jangan chat dulu|lagi sibuk banget|lagi repot jangan chat|fokus kerja dulu|jangan ganggu dulu|nanti dulu ngobrolnya|jangan diganggu dulu)\b",
    re.IGNORECASE,
)

_RESUME_PATTERN = re.compile(
    r"\b(udah bangun nih|udah bangun|udah selesai|udah senggang|bisa ngobrol lagi|lanjut ngobrol|halo lagi)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class IntentResult:
    is_pause: bool = False
    is_resume: bool = False
    pause_until: datetime | None = None
    reason: str | None = None


def detect_natural_control_intent(
    text: str,
    tz: ZoneInfo,
    now: datetime | None = None,
) -> IntentResult:
    """Analyze incoming user text for control intents (pause/resume)."""
    clean_text = text.strip().lower()
    now_dt = now or now_local(tz)

    # 1. Resume intent
    if _RESUME_PATTERN.search(clean_text):
        return IntentResult(is_resume=True, reason="user_resumed")

    # 2. Sleep intent: pause until morning (default 07:00 tomorrow or +8h)
    if _SLEEP_PATTERN.search(clean_text):
        # Calculate next morning 07:00
        wake_date = now_dt.date() + timedelta(days=1)
        morning_target = datetime.combine(wake_date, time(7, 0), tzinfo=tz)
        # Ensure at least 6 hours
        min_sleep = now_dt + timedelta(hours=6)
        pause_until = max(morning_target, min_sleep)
        return IntentResult(is_pause=True, pause_until=pause_until, reason="sleep")

    # 3. Pause today: pause until 23:59:59 tonight
    if _PAUSE_TODAY_PATTERN.search(clean_text):
        pause_until = datetime.combine(now_dt.date(), time(23, 59, 59), tzinfo=tz)
        return IntentResult(is_pause=True, pause_until=pause_until, reason="pause_today")

    # 4. Timed pause: "jangan chat dulu 2 jam", "sibuk 30 menit"
    timed_match = _PAUSE_TIMED_PATTERN.search(clean_text)
    if timed_match:
        val = int(timed_match.group(1))
        unit = timed_match.group(2).lower()
        if "menit" in unit:
            pause_until = now_dt + timedelta(minutes=val)
        elif "jam" in unit:
            pause_until = now_dt + timedelta(hours=val)
        elif "hari" in unit:
            pause_until = now_dt + timedelta(days=val)
        else:
            pause_until = now_dt + timedelta(hours=3)
        return IntentResult(is_pause=True, pause_until=pause_until, reason="pause_timed")

    # 5. General busy / don't disturb: default 3 hours
    if _PAUSE_BUSY_PATTERN.search(clean_text):
        pause_until = now_dt + timedelta(hours=3)
        return IntentResult(is_pause=True, pause_until=pause_until, reason="pause_busy")

    return IntentResult()
