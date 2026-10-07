"""Prompt builder (PRD §6.2, §9.1).

Renders prompt templates with persona definitions, real-time context (WIB),
simulated life state, and memory.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from app.config import Persona
from app.utils.time import day_name_id, format_local_id, now_local

log = logging.getLogger(__name__)

_VAR_PATTERN = re.compile(r"\{\{\s*([a-zA-Z0-9_.]+)\s*\}\}")


class PromptBuilder:
    """Renders system persona and proactive prompt templates."""

    def __init__(self, prompts_dir: Path, persona: Persona, tz: ZoneInfo) -> None:
        self.prompts_dir = prompts_dir
        self.persona = persona
        self.tz = tz
        self._template_cache: dict[str, str] = {}

    def set_persona(self, persona: Persona) -> None:
        """Update persona reference on reload."""
        self.persona = persona

    def _read_template(self, filename: str) -> str:
        if filename not in self._template_cache:
            path = self.prompts_dir / filename
            if not path.is_file():
                raise FileNotFoundError(f"Prompt template not found: {path}")
            self._template_cache[filename] = path.read_text(encoding="utf-8")
        return self._template_cache[filename]

    def _render(self, template_str: str, context: dict[str, Any]) -> str:
        def _replace(match: re.Match[str]) -> str:
            key = match.group(1).strip()
            val = context.get(key)
            if val is None:
                return ""
            return str(val)

        return _VAR_PATTERN.sub(_replace, template_str)

    def build_system_prompt(
        self,
        *,
        dt: datetime | None = None,
        activity: str | None = None,
        mood: str | None = None,
        schedule: str | None = None,
        facts: list[str] | None = None,
        open_threads: list[str] | None = None,
        latest_summary: str | None = None,
    ) -> str:
        """Build the complete system instruction prompt for reactive chat."""
        template = self._read_template("system_persona.id.md")
        now_dt = dt or now_local(self.tz)

        # Persona fields
        p = self.persona
        traits_str = "\n".join(f"- {t}" for t in p.traits) if p.traits else "- ramah dan perhatian"
        boundaries_str = "\n".join(f"- {b}" for b in p.boundaries) if p.boundaries else "- menjaga rasa hormat dan empati"
        slang_str = ", ".join(p.speaking_style.slang) if p.speaking_style.slang else "santai, wkwk, yaudah"

        # Memory fields (defaults for M1 until M2 memory store is connected)
        facts_str = "\n".join(f"- {f}" for f in facts) if facts else "(Belum ada catatan khusus tentang kamu)"
        threads_str = "\n".join(f"- {t}" for t in open_threads) if open_threads else "(Tidak ada topik yang sedang ditindaklanjuti)"
        summary_str = latest_summary or "(Percakapan baru dimulai)"

        context: dict[str, Any] = {
            "persona.name": p.name,
            "persona.age": p.age if p.age is not None else 24,
            "user.nickname": p.user_nickname,
            "persona.traits": traits_str,
            "persona.backstory": p.backstory.strip(),
            "persona.pronoun_self": p.pronouns.self_,
            "persona.pronoun_you": p.pronouns.you,
            "persona.slang": slang_str,
            "persona.boundaries": boundaries_str,
            "now_local": f"{now_dt:%H:%M}",
            "day_name": day_name_id(now_dt),
            "her_current_activity": activity or "Lagi santai di rumah, lagi senggang",
            "her_mood": mood or "santai dan ceria",
            "her_schedule_today": schedule or "Tidak ada jadwal khusus, santai di rumah",
            "facts_list": facts_str,
            "open_threads": threads_str,
            "latest_summary": summary_str,
        }

        return self._render(template, context)

    async def build_system_prompt_from_store(
        self,
        store: Any,
        *,
        dt: datetime | None = None,
        activity: str | None = None,
        mood: str | None = None,
        schedule: str | None = None,
    ) -> str:
        """Fetch active facts, pending threads, and latest summary from store, then render prompt."""
        active_facts = await store.get_active_facts()
        pending_threads = await store.get_pending_threads()
        latest_summary_rec = await store.get_latest_summary()

        facts_list = [f"[{f.category}] {f.content}" for f in active_facts] if active_facts else None
        threads_list = [
            f"{t.topic}" + (f" (target: {t.target_date})" if t.target_date else "")
            for t in pending_threads
        ] if pending_threads else None
        summary_text = latest_summary_rec.content if latest_summary_rec else None

        return self.build_system_prompt(
            dt=dt,
            activity=activity,
            mood=mood,
            schedule=schedule,
            facts=facts_list,
            open_threads=threads_list,
            latest_summary=summary_text,
        )

    def build_proactive_prompt(
        self,
        *,
        window_name: str,
        message_type: str,
        time_since_last_chat: str,
        due_thread: str | None = None,
        recent_proactive: list[str] | None = None,
        recent_messages: list[str] | None = None,
        dt: datetime | None = None,
    ) -> str:
        """Render the proactive message prompt template (prompts/proactive.id.md)."""
        template = self._read_template("proactive.id.md")
        now_dt = dt or now_local(self.tz)

        proactive_str = (
            "\n".join(f"- {p}" for p in recent_proactive)
            if recent_proactive
            else "(Belum ada pesan proaktif sebelumnya)"
        )
        recent_msgs_str = (
            "\n".join(recent_messages)
            if recent_messages
            else "(Belum ada riwayat percakapan)"
        )

        context: dict[str, Any] = {
            "user.nickname": self.persona.user_nickname,
            "now_local": f"{now_dt:%H:%M}",
            "window_name": window_name,
            "time_since_last_chat": time_since_last_chat,
            "message_type": message_type,
            "due_thread_or_none": due_thread or "(Tidak ada hal tertunda khusus)",
            "recent_proactive": proactive_str,
            "recent_messages": recent_msgs_str,
        }

        return self._render(template, context)


def format_time_elapsed_id(seconds: float) -> str:
    """Format elapsed seconds into natural Indonesian description."""
    if seconds < 60:
        return "baru saja"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} menit"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours} jam"
    days = int(hours // 24)
    if days < 7:
        return f"{days} hari"
    weeks = int(days // 7)
    return f"{weeks} minggu"

