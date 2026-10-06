"""Conversation summarizer (PRD §4.3 FR-3.4, §8.4, §9.4).

Generates daily summaries of user conversations and compresses them into
rolling summaries to keep prompt context bounded.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from app.config import LLMSection, Persona
from app.engine.gemini_client import GeminiClient, GeminiError
from app.memory.store import MemoryStore, MessageRecord, SummaryRecord
from app.utils.time import now_local, now_utc, to_utc_iso

log = logging.getLogger(__name__)


class ConversationSummarizer:
    """Creates daily and rolling compressed summaries."""

    def __init__(
        self,
        gemini: GeminiClient,
        store: MemoryStore,
        prompts_dir: Path,
        persona: Persona,
        llm_cfg: LLMSection,
        tz: ZoneInfo,
    ) -> None:
        self.gemini = gemini
        self.store = store
        self.prompts_dir = prompts_dir
        self.persona = persona
        self.llm_cfg = llm_cfg
        self.tz = tz
        self._template: str | None = None

    def set_persona(self, persona: Persona) -> None:
        self.persona = persona

    def _get_template(self) -> str:
        if self._template is None:
            path = self.prompts_dir / "summarize.id.md"
            self._template = path.read_text(encoding="utf-8")
        return self._template

    def _format_messages(self, messages: list[MessageRecord]) -> str:
        lines: list[str] = []
        for m in messages:
            speaker = "User" if m.role == "user" else self.persona.name
            lines.append(f"{speaker}: {m.content}")
        return "\n".join(lines)

    async def summarize_day(self, dt: datetime | None = None) -> SummaryRecord | None:
        """Create a daily summary for today's conversations."""
        local_now = dt or now_local(self.tz)
        today_date = f"{local_now:%Y-%m-%d}"

        # Fetch messages from the last 24 hours or since last daily summary
        recent_messages = await self.store.get_recent_messages(limit=100)
        if len(recent_messages) < 2:
            log.info("daily_summary_skipped reason=insufficient_messages count=%d", len(recent_messages))
            return None

        # Build prompt from template
        template = self._get_template()
        prompt = template.replace("{{persona.name}}", self.persona.name)
        prompt = prompt.replace("{{user.nickname}}", self.persona.user_nickname)
        prompt = prompt.replace("{{messages}}", self._format_messages(recent_messages))

        try:
            result = await self.gemini.generate(
                contents=prompt,
                model=self.llm_cfg.utility_model,
                temperature=self.llm_cfg.temperature_utility,
                purpose="summarize_daily",
            )
            summary_text = result.text.strip()
        except GeminiError as exc:
            log.error("daily_summary_failed error=%r", exc)
            return None

        summary_id = await self.store.add_summary(
            scope="daily",
            period_start=f"{today_date}T00:00:00",
            period_end=f"{today_date}T23:59:59",
            content=summary_text,
        )
        log.info("daily_summary_saved id=%d chars=%d", summary_id, len(summary_text))
        return await self.store.get_latest_summary()

    async def check_and_create_rolling_summary(self, every_days: int = 7) -> SummaryRecord | None:
        """Compress multiple daily summaries into a rolling summary."""
        daily_summaries = await self.store.get_recent_daily_summaries(limit=every_days)
        if len(daily_summaries) < every_days:
            return None

        combined_dailies = "\n\n".join(
            f"[{s.period_start[:10]}]\n{s.content}" for s in daily_summaries
        )

        prompt = (
            f"Kamu adalah modul pembuat ringkasan memori jangka panjang untuk {self.persona.name}.\n"
            f"Berikut adalah ringkasan harian obrolan dengan {self.persona.user_nickname} selama {len(daily_summaries)} hari terakhir:\n\n"
            f"{combined_dailies}\n\n"
            "Tugasmu: Gabungkan dan padatkan informasi di atas menjadi satu ringkasan bergulir (rolling summary) "
            "dalam 5-8 kalimat Bahasa Indonesia. Pertahankan fakta penting tentang user, perkembangan topik, "
            "dan hubungan pertemanan mereka. Jangan gunakan format markdown rumit."
        )

        try:
            result = await self.gemini.generate(
                contents=prompt,
                model=self.llm_cfg.utility_model,
                temperature=self.llm_cfg.temperature_utility,
                purpose="summarize_rolling",
            )
            rolling_text = result.text.strip()
        except GeminiError as exc:
            log.error("rolling_summary_failed error=%r", exc)
            return None

        period_start = daily_summaries[0].period_start
        period_end = daily_summaries[-1].period_end
        summary_id = await self.store.add_summary(
            scope="rolling",
            period_start=period_start,
            period_end=period_end,
            content=rolling_text,
        )
        log.info("rolling_summary_saved id=%d chars=%d", summary_id, len(rolling_text))
        return await self.store.get_latest_summary()
