"""Memory extraction engine.

Extracts durable facts about the user and follow-up threads from raw conversations
using structured JSON output and validates it with Pydantic.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field

from app.config import LLMSection
from app.engine.gemini_client import GeminiClient, GeminiError
from app.memory.store import FactRecord, MemoryStore, MessageRecord, ThreadRecord
from app.utils.time import now_local

log = logging.getLogger(__name__)

_JSON_BLOCK_PATTERN = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```")


class ExtractedFact(BaseModel):
    category: str = "other"
    content: str
    confidence: float = Field(0.8, ge=0.0, le=1.0)


class UpdatedFact(BaseModel):
    id: int
    new_content: str


class ExtractedThread(BaseModel):
    topic: str
    detail: str | None = None
    target_date: str | None = None
    followup_after: str | None = None


class ExtractionPayload(BaseModel):
    new_facts: list[ExtractedFact] = []
    updated_facts: list[UpdatedFact] = []
    new_threads: list[ExtractedThread] = []
    resolved_thread_ids: list[int] = []
    mood_note: str | None = None


@dataclass
class ExtractionSummary:
    processed_messages: int
    new_facts_count: int
    updated_facts_count: int
    new_threads_count: int
    resolved_threads_count: int
    mood_note: str | None = None


def parse_extraction_json(raw_text: str) -> ExtractionPayload:
    """Extract and validate JSON payload from LLM response text."""
    text = raw_text.strip()
    match = _JSON_BLOCK_PATTERN.search(text)
    if match:
        text = match.group(1).strip()
    data = json.loads(text)
    return ExtractionPayload.model_validate(data)


class MemoryExtractor:
    """Processes messages to extract facts, follow-ups, and thread status."""

    def __init__(
        self,
        gemini: GeminiClient,
        store: MemoryStore,
        prompts_dir: Path,
        llm_cfg: LLMSection,
        tz: ZoneInfo,
    ) -> None:
        self.gemini = gemini
        self.store = store
        self.prompts_dir = prompts_dir
        self.llm_cfg = llm_cfg
        self.tz = tz
        self._template: str | None = None

    def _get_template(self) -> str:
        if self._template is None:
            path = self.prompts_dir / "memory_extract.id.md"
            self._template = path.read_text(encoding="utf-8")
        return self._template

    def _format_facts(self, facts: list[FactRecord]) -> str:
        if not facts:
            return "(Belum ada fakta tercatat)"
        return "\n".join(f"[#{f.id}] [{f.category}] {f.content}" for f in facts)

    def _format_threads(self, threads: list[ThreadRecord]) -> str:
        if not threads:
            return "(Belum ada thread terbuka)"
        lines: list[str] = []
        for t in threads:
            extra = f" (target: {t.target_date})" if t.target_date else ""
            lines.append(f"[#{t.id}] {t.topic}{extra}")
        return "\n".join(lines)

    def _format_messages(self, messages: list[MessageRecord]) -> str:
        lines: list[str] = []
        for m in messages:
            speaker = "User" if m.role == "user" else "Nala"
            lines.append(f"{speaker}: {m.content}")
        return "\n".join(lines)

    async def extract_new_memories(self, since_id: int | None = None) -> ExtractionSummary:
        """Fetch unextracted messages, run extraction prompt, and persist facts/threads."""
        if since_id is None:
            last_extracted_str = await self.store.get_state("last_extracted_message_id", "0")
            since_id = int(last_extracted_str or "0")

        messages = await self.store.get_messages_since_id(since_id)
        if not messages:
            log.info("memory_extraction_skipped reason=no_new_messages since_id=%d", since_id)
            return ExtractionSummary(0, 0, 0, 0, 0)

        existing_facts = await self.store.get_active_facts()
        existing_threads = await self.store.get_pending_threads()

        today_str = f"{now_local(self.tz):%Y-%m-%d}"
        template = self._get_template()

        prompt = template
        prompt = prompt.replace("{{today}}", today_str)
        prompt = prompt.replace("{{existing_facts}}", self._format_facts(existing_facts))
        prompt = prompt.replace("{{existing_threads}}", self._format_threads(existing_threads))
        prompt = prompt.replace("{{messages}}", self._format_messages(messages))

        try:
            result = await self.gemini.generate(
                contents=prompt,
                model=self.llm_cfg.utility_model,
                temperature=self.llm_cfg.temperature_utility,
                response_mime_type="application/json",
                purpose="extract_memory",
            )
            payload = parse_extraction_json(result.text)
        except (GeminiError, Exception) as exc:
            log.error("memory_extraction_failed error=%r", exc)
            return ExtractionSummary(len(messages), 0, 0, 0, 0)

        # Apply changes to MemoryStore
        for f in payload.new_facts:
            await self.store.add_fact(category=f.category, content=f.content, confidence=f.confidence)

        for u in payload.updated_facts:
            await self.store.supersede_fact(old_fact_id=u.id, new_content=u.new_content)

        for t in payload.new_threads:
            await self.store.add_thread(
                topic=t.topic,
                detail=t.detail,
                target_date=t.target_date,
                followup_after=t.followup_after,
            )

        for tid in payload.resolved_thread_ids:
            await self.store.resolve_thread(thread_id=tid)

        # Update last extracted message pointer
        max_msg_id = max(m.id for m in messages)
        await self.store.set_state("last_extracted_message_id", str(max_msg_id))

        summary = ExtractionSummary(
            processed_messages=len(messages),
            new_facts_count=len(payload.new_facts),
            updated_facts_count=len(payload.updated_facts),
            new_threads_count=len(payload.new_threads),
            resolved_threads_count=len(payload.resolved_thread_ids),
            mood_note=payload.mood_note,
        )
        log.info(
            "memory_extraction_completed messages=%d new_facts=%d updated_facts=%d new_threads=%d resolved_threads=%d",
            summary.processed_messages,
            summary.new_facts_count,
            summary.updated_facts_count,
            summary.new_threads_count,
            summary.resolved_threads_count,
        )
        return summary
