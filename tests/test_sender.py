from __future__ import annotations

import pytest

from app.config import TypingDelay
from app.gateway.sender import calculate_typing_delay, split_bubbles, send_bubbles


def test_calculate_typing_delay_bounds():
    # Min bound (short text)
    assert calculate_typing_delay("halo", min_seconds=1.0, max_seconds=6.0, chars_per_second=25.0) == 1.0

    # Max bound (very long text)
    long_text = "a" * 300
    assert calculate_typing_delay(long_text, min_seconds=1.0, max_seconds=6.0, chars_per_second=25.0) == 6.0

    # Intermediate calculation: 50 chars at 25 cps = 2.0s
    mid_text = "a" * 50
    assert calculate_typing_delay(mid_text, min_seconds=1.0, max_seconds=6.0, chars_per_second=25.0) == 2.0


def test_split_bubbles_explicit_delimiter():
    raw = "Hai! Lagi apa nih?\n---\nAku baru selesai bikin kopi wkwk"
    bubbles = split_bubbles(raw, max_bubbles=3, max_chars=280)
    assert len(bubbles) == 2
    assert bubbles[0] == "Hai! Lagi apa nih?"
    assert bubbles[1] == "Aku baru selesai bikin kopi wkwk"


def test_split_bubbles_paragraphs():
    raw = "Hai kamu!\n\nLagi sibuk ya hari ini?"
    bubbles = split_bubbles(raw, max_bubbles=3, max_chars=280)
    assert len(bubbles) == 2
    assert bubbles[0] == "Hai kamu!"
    assert bubbles[1] == "Lagi sibuk ya hari ini?"


def test_split_bubbles_sentence_boundary_split():
    long_sentence_1 = "Hari ini seru banget deh! " * 5  # ~130 chars
    long_sentence_2 = "Tadi aku jalan-jalan ke taman dan liat banyak kucing lucu. " * 3  # ~177 chars
    combined = long_sentence_1 + long_sentence_2
    bubbles = split_bubbles(combined, max_bubbles=3, max_chars=200)
    assert len(bubbles) >= 2
    for b in bubbles:
        assert len(b) <= 200


def test_split_bubbles_max_bubbles_cap():
    raw = "Pesan 1\n---\nPesan 2\n---\nPesan 3\n---\nPesan 4\n---\nPesan 5"
    bubbles = split_bubbles(raw, max_bubbles=3, max_chars=280)
    assert len(bubbles) <= 3


def test_split_bubbles_clean_artifacts():
    raw = 'Nara: "Hai, lagi apa?"'
    bubbles = split_bubbles(raw, max_bubbles=3, max_chars=280, speaker_names=("Nara",))
    assert bubbles == ["Hai, lagi apa?"]

    # Only known speaker labels are stripped, not any word before a colon
    assert split_bubbles("Catatan: besok libur") == ["Catatan: besok libur"]

    raw_bullet = "- Ini poin satu\n- Ini poin dua"
    bubbles = split_bubbles(raw_bullet, max_bubbles=3, max_chars=280)
    assert bubbles == ["Ini poin satu", "Ini poin dua"]


@pytest.mark.asyncio
async def test_send_bubbles_mock():
    class DummyMsg:
        def __init__(self, msg_id: int):
            self.message_id = msg_id

    class DummyBot:
        def __init__(self):
            self.actions: list[str] = []
            self.messages: list[str] = []

        async def send_chat_action(self, chat_id: int, action: str):
            self.actions.append(action)

        async def send_message(self, chat_id: int, text: str):
            self.messages.append(text)
            return DummyMsg(len(self.messages))

    bot = DummyBot()
    cfg = TypingDelay(min_seconds=0.01, max_seconds=0.05, chars_per_second=25.0)
    sent_ids = await send_bubbles(
        bot,
        chat_id=123,
        bubbles=["Halo", "Apa kabar?"],
        typing_cfg=cfg,
        simulate_typing=True,
    )

    assert sent_ids == [1, 2]
    assert bot.messages == ["Halo", "Apa kabar?"]
    assert len(bot.actions) == 2
