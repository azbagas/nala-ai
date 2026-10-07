"""Her simulated daily activity and mood generator (PRD §4.5, §8.5, §9.5).

Generates a realistic daily schedule, mood, and energy level for the persona
each morning using Gemini. Persists the schedule to SQLite and resolves her
current activity based on the time of day to keep her simulated life consistent.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import LLMSection, Persona
from app.engine.gemini_client import GeminiClient, GeminiError
from app.memory.store import DailyLifeRecord, MemoryStore, ScheduleBlock
from app.utils.time import day_name_id, in_time_range, now_local, now_utc, parse_hhmm, to_utc_iso

log = logging.getLogger(__name__)

_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


def parse_daily_life_json(text: str) -> dict[str, Any]:
    """Parse JSON output from Gemini, handling markdown code fences."""
    cleaned = text.strip()
    match = _JSON_BLOCK_RE.search(cleaned)
    raw_json = match.group(1).strip() if match else cleaned

    # If braces are wrapped with extraneous text, attempt to isolate the outermost {}
    start = raw_json.find("{")
    end = raw_json.rfind("}")
    if start != -1 and end != -1 and end > start:
        raw_json = raw_json[start : end + 1]

    data = json.loads(raw_json)
    if not isinstance(data, dict):
        raise ValueError("Expected top-level JSON object")
    return data


def format_schedule_summary(schedule: list[ScheduleBlock]) -> str:
    """Format full schedule into a readable Indonesian summary."""
    if not schedule:
        return "Tidak ada jadwal khusus, santai di rumah"
    return "; ".join(f"{b.from_time}-{b.to_time} {b.activity}" for b in schedule)


def resolve_current_activity(schedule: list[ScheduleBlock], dt_local: datetime) -> str:
    """Resolve what she is doing at a given local time from her schedule blocks."""
    if not schedule:
        return "Lagi santai di rumah, lagi senggang"

    curr_t = dt_local.time()

    for block in schedule:
        try:
            start_t = parse_hhmm(block.from_time)
            end_t = parse_hhmm(block.to_time)
            if in_time_range(curr_t, start_t, end_t):
                return block.activity
        except Exception:
            continue

    # If before the first scheduled activity
    try:
        first_start = parse_hhmm(schedule[0].from_time)
        if curr_t < first_start:
            return "Baru bangun tidur, lagi ngumpulin nyawa & santai pagi"
    except Exception:
        pass

    # If after the last scheduled activity
    try:
        last_end = parse_hhmm(schedule[-1].to_time)
        if curr_t >= last_end or curr_t < time(5, 0):
            return "Istirahat di kasur / santai sebelum tidur"
    except Exception:
        pass

    # In between blocks or fallback
    return "Lagi santai di sela-sela kegiatan"


def get_fallback_daily_life(target_date: date, persona: Persona) -> DailyLifeRecord:
    """Provide a reliable, persona-consistent fallback schedule."""
    schedule = [
        ScheduleBlock("07:00", "08:30", "Bangun tidur, bikin kopi, kasih makan Mochi"),
        ScheduleBlock("08:30", "12:00", "Kerja desain freelance di meja kerja"),
        ScheduleBlock("12:00", "13:30", "Makan siang & santai scroll sosmed"),
        ScheduleBlock("13:30", "17:30", "Lanjut ngerjain revisi desain klien"),
        ScheduleBlock("17:30", "19:00", "Beres-beres rumah & main sama Mochi"),
        ScheduleBlock("19:00", "20:30", "Makan malam & ngobrol santai"),
        ScheduleBlock("20:30", "22:30", "Nonton series / dengerin musik indie"),
        ScheduleBlock("22:30", "23:30", "Rebahan, skincare-an, siap-siap tidur"),
    ]
    return DailyLifeRecord(
        date=target_date.strftime("%Y-%m-%d"),
        schedule=schedule,
        mood="santai dan ramah",
        energy="medium",
        notes="Hari ini rencananya mau santai sambil beresin kerjaan.",
        created_at=to_utc_iso(now_utc()),
    )


class LifeSimulator:
    """Simulates her daily schedule, mood, and activities (PRD §4.5, §8.5)."""

    def __init__(
        self,
        gemini: GeminiClient,
        store: MemoryStore,
        persona: Persona,
        prompts_dir: Path,
        llm_cfg: LLMSection,
        tz: ZoneInfo,
    ) -> None:
        self.gemini = gemini
        self.store = store
        self.persona = persona
        self.prompts_dir = prompts_dir
        self.llm_cfg = llm_cfg
        self.tz = tz
        self._template_cache: str | None = None

    def set_persona(self, persona: Persona) -> None:
        """Update persona reference on reload."""
        self.persona = persona

    def _read_template(self) -> str:
        if self._template_cache is None:
            path = self.prompts_dir / "daily_life.id.md"
            if not path.is_file():
                raise FileNotFoundError(f"Daily life prompt template not found: {path}")
            self._template_cache = path.read_text(encoding="utf-8")
        return self._template_cache

    def _render_prompt(
        self,
        target_date: date,
        day_name: str,
        yesterday_mood: str,
        recent_notes: str,
    ) -> str:
        template = self._read_template()
        p = self.persona

        # Build persona summary
        traits_str = ", ".join(p.traits) if p.traits else "ramah"
        interests_str = ", ".join(p.interests) if p.interests else "kopi, santai"
        summary = (
            f"{p.name}, umur {p.age or 24}. {p.backstory.strip()} "
            f"Sifat: {traits_str}. Minat: {interests_str}."
        )

        date_str = f"{target_date.day} {target_date.strftime('%B %Y')}"
        replacements = {
            "{{persona.name}}": p.name,
            "{{day_name}}": day_name,
            "{{date}}": date_str,
            "{{persona.summary}}": summary,
            "{{recent_life_notes}}": recent_notes or "(tidak ada kejadian khusus)",
            "{{yesterday_mood}}": yesterday_mood or "santai dan baik-baik saja",
        }

        rendered = template
        for k, v in replacements.items():
            rendered = rendered.replace(k, str(v))
        return rendered

    async def generate_daily_life(
        self,
        target_date: date | None = None,
        force: bool = False,
    ) -> DailyLifeRecord:
        """Generate and store her daily schedule and mood (PRD §8.5)."""
        now_dt = now_local(self.tz)
        t_date = target_date or now_dt.date()
        date_str = t_date.strftime("%Y-%m-%d")

        # 1. Return existing record if already generated (unless forced)
        if not force:
            existing = await self.store.get_daily_life(date_str)
            if existing:
                log.info("daily_life_already_exists date=%s", date_str)
                return existing

        # 2. Gather context from yesterday or recent daily life
        yesterday = t_date - timedelta(days=1)
        yesterday_str = yesterday.strftime("%Y-%m-%d")
        yesterday_rec = await self.store.get_daily_life(yesterday_str)

        yesterday_mood = yesterday_rec.mood if yesterday_rec else "santai dan baik"
        recent_notes = yesterday_rec.notes if (yesterday_rec and yesterday_rec.notes) else "(tidak ada catatan khusus)"

        # 3. Render prompt
        day_name = day_name_id(datetime.combine(t_date, time(12, 0), tzinfo=self.tz))
        prompt_text = self._render_prompt(
            target_date=t_date,
            day_name=day_name,
            yesterday_mood=yesterday_mood,
            recent_notes=recent_notes,
        )

        # 4. Call Gemini
        try:
            result = await self.gemini.generate(
                contents=prompt_text,
                model=self.llm_cfg.utility_model,
                temperature=self.llm_cfg.temperature_utility,
                purpose="daily_life",
            )
            data = parse_daily_life_json(result.text)

            mood = str(data.get("mood", "santai")).strip()
            energy = str(data.get("energy", "medium")).strip()
            notes = data.get("notes")
            if notes is not None:
                notes = str(notes).strip() or None

            raw_blocks = data.get("schedule", [])
            schedule_blocks: list[ScheduleBlock] = []
            for b in raw_blocks:
                if isinstance(b, dict) and "from" in b and "to" in b and "activity" in b:
                    schedule_blocks.append(
                        ScheduleBlock(
                            from_time=str(b["from"]).strip(),
                            to_time=str(b["to"]).strip(),
                            activity=str(b["activity"]).strip(),
                        )
                    )

            if len(schedule_blocks) < 3:
                raise ValueError(f"Schedule too short ({len(schedule_blocks)} blocks)")

            await self.store.save_daily_life(
                date=date_str,
                schedule=schedule_blocks,
                mood=mood,
                energy=energy,
                notes=notes,
            )

            rec = await self.store.get_daily_life(date_str)
            if rec:
                log.info(
                    "daily_life_generated date=%s blocks=%d mood=%s energy=%s",
                    date_str,
                    len(rec.schedule),
                    rec.mood,
                    rec.energy,
                )
                return rec

        except Exception as exc:
            log.warning("daily_life_generation_failed date=%s error=%r -> using fallback", date_str, exc)

        # 5. Fallback if generation failed
        fallback = get_fallback_daily_life(t_date, self.persona)
        await self.store.save_daily_life(
            date=date_str,
            schedule=fallback.schedule,
            mood=fallback.mood,
            energy=fallback.energy,
            notes=fallback.notes,
        )
        return fallback

    async def get_or_generate_today(self, dt: datetime | None = None) -> DailyLifeRecord:
        """Fetch today's simulated life record, generating it on the fly if missing."""
        local_dt = dt or now_local(self.tz)
        today_date = local_dt.date()
        date_str = today_date.strftime("%Y-%m-%d")

        existing = await self.store.get_daily_life(date_str)
        if existing:
            return existing
        return await self.generate_daily_life(target_date=today_date)
