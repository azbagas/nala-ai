"""Message debouncer.

Buffers rapid consecutive user messages within a configurable debounce window
(e.g., 3-5 seconds) and dispatches once with the combined input.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)

FlushCallback = Callable[[int, int, str], Awaitable[None]]


class MessageDebouncer:
    """Collects messages from a user and flushes them together after silence."""

    def __init__(self, debounce_seconds: float, on_flush: FlushCallback) -> None:
        self.debounce_seconds = debounce_seconds
        self.on_flush = on_flush
        self._buffers: dict[int, list[str]] = {}
        self._tasks: dict[int, asyncio.Task[Any]] = {}
        self._chat_ids: dict[int, int] = {}

    async def add_message(self, user_id: int, chat_id: int, text: str) -> None:
        """Add a message to the debounce buffer for this user."""
        if not text.strip():
            return

        self._chat_ids[user_id] = chat_id
        if user_id not in self._buffers:
            self._buffers[user_id] = []
        self._buffers[user_id].append(text.strip())

        # If debounce is 0 or disabled, flush immediately
        if self.debounce_seconds <= 0:
            await self._flush_user(user_id)
            return

        # Cancel any pending timer task for this user
        existing_task = self._tasks.get(user_id)
        if existing_task and not existing_task.done():
            existing_task.cancel()

        # Start a new timer task
        self._tasks[user_id] = asyncio.create_task(self._wait_and_flush(user_id))

    async def _wait_and_flush(self, user_id: int) -> None:
        try:
            await asyncio.sleep(self.debounce_seconds)
            await self._flush_user(user_id)
        except asyncio.CancelledError:
            # Another message arrived before timer expired; expected behavior
            pass
        except Exception as exc:
            log.exception("debouncer_error user_id=%s error=%r", user_id, exc)

    async def _flush_user(self, user_id: int) -> None:
        texts = self._buffers.pop(user_id, [])
        chat_id = self._chat_ids.pop(user_id, user_id)
        self._tasks.pop(user_id, None)

        if not texts:
            return

        combined = "\n".join(texts)
        log.info(
            "debouncer_flushed user_id=%s chat_id=%s message_count=%d total_chars=%d",
            user_id,
            chat_id,
            len(texts),
            len(combined),
        )
        try:
            await self.on_flush(chat_id, user_id, combined)
        except Exception as exc:
            log.exception("debouncer_on_flush_failed user_id=%s error=%r", user_id, exc)

    def cancel_all(self) -> None:
        """Cancel all pending debounce tasks (e.g. on shutdown)."""
        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        self._tasks.clear()
        self._buffers.clear()
        self._chat_ids.clear()
