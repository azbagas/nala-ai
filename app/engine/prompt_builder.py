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
