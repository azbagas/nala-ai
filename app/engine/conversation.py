"""Conversation engine (PRD §6.2, §8.1).

Orchestrates multi-turn conversation flow: builds prompt, calls Gemini with
persona and history, validates output (Indonesian, no assistant voice), and
splits the result into chat bubbles.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from google.genai import types

from app.config import ConversationSection, LLMSection
from app.engine.gemini_client import GeminiClient, GeminiError
from app.engine.prompt_builder import PromptBuilder
from app.gateway.sender import split_bubbles

log = logging.getLogger(__name__)

_ASSISTANT_PHRASES = [
    "ada yang bisa saya bantu",
    "ada yang bisa dibantu",
    "ada yang bisa kubantu",
    "ada yang perlu dibantu",
    "bisa saya bantu",
    "sebagai ai",
    "sebagai sebuah ai",
    "sebagai asisten",
    "sebagai model bahasa",
    "sebagai kecerdasan buatan",
    "as an ai",
    "how can i help",
    "how can i assist",
]

# Very common English stopwords that rarely appear alone in Indonesian chat
_ENGLISH_INDICATORS = re.compile(
    r"\b(how can i assist|how can i help|as an ai|i am an ai|hello there|what can i do for you)\b",
    re.IGNORECASE,
)

FALLBACK_REPLY = "bentar ya, sinyalku lagi jelek nih 😅"


@dataclass
class ChatMessage:
    role: str  # "user" or "model"
    content: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def contains_assistant_voice(text: str) -> bool:
    lower = text.lower()
    return any(p in lower for p in _ASSISTANT_PHRASES)


def is_predominantly_english(text: str) -> bool:
    return bool(_ENGLISH_INDICATORS.search(text))


def sanitize_text(text: str) -> str:
    """Strip AI assistant artifacts, markdown headers, and speaker prefixes."""
    cleaned = text
    # Remove leading speaker prefixes like "Nara: ", "Bot: "
    cleaned = re.sub(r"^(?:[A-Za-z0-9_-]+|Aku|Bot|Nara)\s*:\s*", "", cleaned, flags=re.IGNORECASE)
    # Remove markdown headers like "### " or "## "
    cleaned = re.sub(r"^#{1,6}\s+", "", cleaned, flags=re.MULTILINE)
    # Remove forbidden assistant phrases if any slipped through
    for phrase in _ASSISTANT_PHRASES:
        cleaned = re.sub(re.escape(phrase), "", cleaned, flags=re.IGNORECASE)
    return cleaned.strip()


class ConversationEngine:
    """Manages chat turns, history, Gemini calls, and response validation."""

    def __init__(
        self,
        gemini: GeminiClient,
        prompt_builder: PromptBuilder,
        llm_cfg: LLMSection,
        conv_cfg: ConversationSection,
        store: Any = None,
    ) -> None:
        self.gemini = gemini
        self.prompt_builder = prompt_builder
        self.llm_cfg = llm_cfg
        self.conv_cfg = conv_cfg
        self.store = store
        self._history: list[ChatMessage] = []

    def set_store(self, store: Any) -> None:
        self.store = store

    def get_history(self) -> list[ChatMessage]:
        return list(self._history)

    def clear_history(self) -> None:
        """Clear recent in-memory conversation history."""
        count = len(self._history)
        self._history.clear()
        log.info("cleared_conversation_history count=%d", count)

    async def _build_contents(self, new_user_text: str) -> list[types.Content]:
        """Convert recent message history plus current user input into Gemini contents."""
        contents: list[types.Content] = []

        if self.store:
            try:
                db_messages = await self.store.get_recent_messages(limit=self.llm_cfg.history_window_messages)
                for m in db_messages:
                    role = "user" if m.role == "user" else "model"
                    contents.append(
                        types.Content(
                            role=role,
                            parts=[types.Part.from_text(text=m.content)],
                        )
                    )
            except Exception as exc:
                log.warning("failed_to_fetch_db_messages error=%r -> falling back to memory", exc)
                window = self._history[-self.llm_cfg.history_window_messages :]
                for msg in window:
                    contents.append(
                        types.Content(
                            role=msg.role,
                            parts=[types.Part.from_text(text=msg.content)],
                        )
                    )
        else:
            window = self._history[-self.llm_cfg.history_window_messages :]
            for msg in window:
                contents.append(
                    types.Content(
                        role=msg.role,
                        parts=[types.Part.from_text(text=msg.content)],
                    )
                )

        # Append current user turn
        contents.append(
            types.Content(
                role="user",
                parts=[types.Part.from_text(text=new_user_text)],
            )
        )
        return contents

    async def reply(self, user_text: str) -> list[str]:
        """Generate a validated list of chat bubbles for the user's message."""
        clean_user_text = user_text.strip()
        if not clean_user_text:
            return []

        if self.store:
            try:
                system_instruction = await self.prompt_builder.build_system_prompt_from_store(self.store)
            except Exception as exc:
                log.warning("failed_to_build_prompt_from_store error=%r -> fallback to basic", exc)
                system_instruction = self.prompt_builder.build_system_prompt()
        else:
            system_instruction = self.prompt_builder.build_system_prompt()

        contents = await self._build_contents(clean_user_text)

        # 1. First attempt to generate reply
        try:
            result = await self.gemini.generate(
                contents=contents,
                model=self.llm_cfg.chat_model,
                system_instruction=system_instruction,
                temperature=self.llm_cfg.temperature_chat,
                max_output_tokens=self.llm_cfg.max_output_tokens_chat,
                purpose="chat",
            )
            raw_reply = result.text
        except GeminiError as exc:
            log.error("conversation_gemini_error error=%r", exc)
            return [FALLBACK_REPLY]

        # 2. Quality rules validation (PRD §10)
        needs_retry = contains_assistant_voice(raw_reply) or is_predominantly_english(raw_reply)
        if needs_retry:
            log.warning("reply_quality_flagged raw_reply=%r -> retrying once", raw_reply[:80])
            softened_instruction = (
                f"{system_instruction}\n\n"
                "PENTING: Balas santai dan akrab dalam Bahasa Indonesia chat. "
                "JANGAN gunakan gaya asisten AI (seperti 'ada yang bisa dibantu' atau 'sebagai AI')."
            )
            try:
                retry_result = await self.gemini.generate(
                    contents=contents,
                    model=self.llm_cfg.chat_model,
                    system_instruction=softened_instruction,
                    temperature=self.llm_cfg.temperature_chat,
                    max_output_tokens=self.llm_cfg.max_output_tokens_chat,
                    purpose="chat_retry",
                )
                raw_reply = retry_result.text
            except GeminiError as exc:
                log.warning("retry_gemini_error error=%r -> using sanitized first reply", exc)

        # 3. Sanitize and split into bubbles
        cleaned_reply = sanitize_text(raw_reply)
        if not cleaned_reply:
            cleaned_reply = FALLBACK_REPLY

        bubbles = split_bubbles(
            cleaned_reply,
            max_bubbles=self.conv_cfg.max_bubbles,
            max_chars=self.conv_cfg.max_bubble_chars,
        )

        if not bubbles:
            bubbles = [FALLBACK_REPLY]

        # 4. Save to history (user message and bot's reply)
        bot_reply_content = "\n---\n".join(bubbles)
        self._history.append(ChatMessage(role="user", content=clean_user_text))
        self._history.append(ChatMessage(role="model", content=bot_reply_content))

        if self.store:
            try:
                await self.store.save_message(role="user", content=clean_user_text, origin="reply")
                await self.store.save_message(role="bot", content=bot_reply_content, origin="reply")
            except Exception as exc:
                log.error("failed_to_save_message_to_store error=%r", exc)

        # Enforce max history window
        max_keep = self.llm_cfg.history_window_messages * 2
        if len(self._history) > max_keep:
            self._history = self._history[-max_keep:]

        log.info(
            "conversation_reply_generated user_chars=%d bubble_count=%d total_reply_chars=%d",
            len(clean_user_text),
            len(bubbles),
            sum(len(b) for b in bubbles),
        )
        return bubbles
