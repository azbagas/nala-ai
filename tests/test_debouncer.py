from __future__ import annotations

import asyncio
import pytest

from app.gateway.debouncer import MessageDebouncer


@pytest.mark.asyncio
async def test_debouncer_single_message():
    flushed: list[tuple[int, int, str]] = []

    async def on_flush(chat_id: int, user_id: int, text: str):
        flushed.append((chat_id, user_id, text))

    debouncer = MessageDebouncer(debounce_seconds=0.05, on_flush=on_flush)
    await debouncer.add_message(user_id=1, chat_id=10, text="Halo Nala")

    # Before delay expires, nothing is flushed yet
    assert len(flushed) == 0

    await asyncio.sleep(0.08)
    assert len(flushed) == 1
    assert flushed[0] == (10, 1, "Halo Nala")


@pytest.mark.asyncio
async def test_debouncer_multiple_messages_combined():
    flushed: list[tuple[int, int, str]] = []

    async def on_flush(chat_id: int, user_id: int, text: str):
        flushed.append((chat_id, user_id, text))

    debouncer = MessageDebouncer(debounce_seconds=0.08, on_flush=on_flush)

    # User sends 3 messages quickly
    await debouncer.add_message(user_id=1, chat_id=10, text="eh")
    await asyncio.sleep(0.03)
    await debouncer.add_message(user_id=1, chat_id=10, text="kamu tau gak")
    await asyncio.sleep(0.03)
    await debouncer.add_message(user_id=1, chat_id=10, text="ada kejadian lucu tadi")

    # Total elapsed so far: ~0.06s. Debounce reset each time, so not yet flushed.
    assert len(flushed) == 0

    # Wait for the last debounce timer to fire
    await asyncio.sleep(0.12)
    assert len(flushed) == 1
    chat_id, user_id, combined = flushed[0]
    assert chat_id == 10
    assert user_id == 1
    assert combined == "eh\nkamu tau gak\nada kejadian lucu tadi"


@pytest.mark.asyncio
async def test_debouncer_zero_delay():
    flushed: list[tuple[int, int, str]] = []

    async def on_flush(chat_id: int, user_id: int, text: str):
        flushed.append((chat_id, user_id, text))

    debouncer = MessageDebouncer(debounce_seconds=0, on_flush=on_flush)
    await debouncer.add_message(user_id=2, chat_id=20, text="Langsung kirim")

    # Flushed immediately without waiting
    assert len(flushed) == 1
    assert flushed[0] == (20, 2, "Langsung kirim")


@pytest.mark.asyncio
async def test_debouncer_cancel_all():
    flushed: list[tuple[int, int, str]] = []

    async def on_flush(chat_id: int, user_id: int, text: str):
        flushed.append((chat_id, user_id, text))

    debouncer = MessageDebouncer(debounce_seconds=0.1, on_flush=on_flush)
    await debouncer.add_message(user_id=3, chat_id=30, text="Jangan kirim ini")
    debouncer.cancel_all()

    await asyncio.sleep(0.15)
    assert len(flushed) == 0
