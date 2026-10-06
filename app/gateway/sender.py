"""Bubble splitting and typing delay logic (PRD §4.1 FR-1.4, FR-1.5).

Splits bot replies into short chat-like bubbles and sends them with a realistic
typing indicator and delay proportional to bubble length.
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from telegram.constants import ChatAction

from app.config import TypingDelay

log = logging.getLogger(__name__)

# Strip common AI persona prefixes e.g. "Nara: ", "Bot: ", "Aku: "
_PREFIX_PATTERN = re.compile(r"^(?:[A-Za-z0-9_-]+|Aku|Bot|Nara)\s*:\s*", re.IGNORECASE)
_DELIMITER_PATTERN = re.compile(r"\n?\s*---\s*\n?")
_SENTENCE_SPLIT_PATTERN = re.compile(r"(?<=[.!?…])\s+")


def calculate_typing_delay(
    text: str,
    min_seconds: float = 1.0,
    max_seconds: float = 6.0,
    chars_per_second: float = 25.0,
) -> float:
    """Calculate natural typing delay in seconds, bounded by min and max."""
    clean_text = text.strip()
    chars = len(clean_text)
    rate = max(chars_per_second, 1.0)
    calculated = chars / rate
    return max(min_seconds, min(calculated, max_seconds))


def _clean_chunk(chunk: str) -> str:
    """Clean a single text chunk: remove quotes, prefixes, list bullets."""
    text = chunk.strip()
    text = _PREFIX_PATTERN.sub("", text).strip()
    # Strip markdown list markers if model added them
    text = re.sub(r"^[*\-•]\s+", "", text)
    # Strip enclosing quotation marks
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        text = text[1:-1].strip()
    return text


def split_bubbles(text: str, max_bubbles: int = 3, max_chars: int = 280) -> list[str]:
    """Split model response into 1-3 short chat bubbles.

    Prioritizes explicit '---' delimiters from the prompt, falls back to
    paragraph/newline splits, and finally sentence boundaries if too long.
    """
    raw = text.strip()
    if not raw:
        return []

    # 1. Check for explicit prompt delimiter '---'
    if re.search(_DELIMITER_PATTERN, raw):
        parts = _DELIMITER_PATTERN.split(raw)
    else:
        # 2. Check for paragraph splits
        if "\n\n" in raw:
            parts = raw.split("\n\n")
        elif "\n" in raw:
            parts = [line for line in raw.split("\n") if line.strip()]
        else:
            parts = [raw]

    # Clean and flatten
    bubbles: list[str] = []
    for part in parts:
        cleaned = _clean_chunk(part)
        if not cleaned:
            continue

        # If a single part exceeds max_chars, split on sentence boundaries
        if len(cleaned) > max_chars:
            sentences = _SENTENCE_SPLIT_PATTERN.split(cleaned)
            current = ""
            for s in sentences:
                s = s.strip()
                if not s:
                    continue
                if not current:
                    current = s
                elif len(current) + len(s) + 1 <= max_chars:
                    current = f"{current} {s}"
                else:
                    bubbles.append(current)
                    current = s
            if current:
                bubbles.append(current)
        else:
            bubbles.append(cleaned)

    if not bubbles:
        return [raw[:max_chars]]

    # 3. Cap at max_bubbles
    if len(bubbles) > max_bubbles:
        # Keep first (max_bubbles - 1), combine remainder if fits or take slice
        head = bubbles[: max_bubbles - 1]
        tail = " ".join(bubbles[max_bubbles - 1 :])
        if len(tail) <= max_chars:
            bubbles = head + [tail]
        else:
            bubbles = bubbles[:max_bubbles]

    return bubbles


async def send_bubbles(
    bot: Any,
    chat_id: int,
    bubbles: list[str],
    typing_cfg: TypingDelay,
    simulate_typing: bool = True,
) -> list[int]:
    """Send bubbles one by one with typing indicator and delay.

    Returns the list of sent Telegram message IDs.
    """
    sent_ids: list[int] = []

    for i, bubble in enumerate(bubbles):
        delay = calculate_typing_delay(
            bubble,
            min_seconds=typing_cfg.min_seconds,
            max_seconds=typing_cfg.max_seconds,
            chars_per_second=typing_cfg.chars_per_second,
        )

        if simulate_typing:
            try:
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            except Exception as exc:
                log.warning("failed_to_send_chat_action error=%r", exc)

            await asyncio.sleep(delay)

        try:
            msg = await bot.send_message(chat_id=chat_id, text=bubble)
            sent_ids.append(msg.message_id)
            log.info("sent_bubble index=%d/%d chars=%d delay=%.2fs", i + 1, len(bubbles), len(bubble), delay)
        except Exception as exc:
            log.error("failed_to_send_bubble index=%d error=%r", i, exc)
            raise

    return sent_ids
