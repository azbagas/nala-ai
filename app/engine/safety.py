"""Distress / crisis detection (PRD §11.1).

Lightweight keyword rules. They are deliberately conservative-but-broad: a false
positive only makes the reply more careful, a false negative skips the safeguard.
"""

from __future__ import annotations

import re
from collections.abc import Sequence

_DISTRESS_PATTERN = re.compile(
    r"\b("
    r"bunuh diri|mengakhiri hidup|akhiri hidup|ngakhiri hidup|pengen mati|pengin mati|ingin mati|"
    r"mau mati|lebih baik mati|lebih baik aku mati|gak mau hidup|nggak mau hidup|tidak ingin hidup|"
    r"gak ada gunanya hidup|menyakiti diri|nyakitin diri|self[- ]?harm|suicide|kill myself|"
    r"end my life|pengen menghilang|ingin menghilang|capek hidup|lelah hidup"
    r")\b",
    re.IGNORECASE,
)


def detect_distress(text: str) -> bool:
    """Return True if the text suggests serious distress or self-harm thoughts."""
    return bool(_DISTRESS_PATTERN.search(text or ""))


def build_distress_instruction(resources: Sequence[str] = ()) -> str:
    """System-prompt addition that switches the reply into a careful, supportive mode."""
    text = (
        "\n\nPENTING (KONDISI SERIUS): User tampaknya sedang sangat tertekan atau punya pikiran menyakiti diri. "
        "Tanggapi dengan hangat, tenang, dan serius. JANGAN bercanda, JANGAN meremehkan, JANGAN menghakimi. "
        "Dengarkan dulu, validasi perasaannya, tanyakan dengan lembut apakah dia aman saat ini, dan dorong dia "
        "menghubungi orang yang dipercaya atau bantuan profesional. Jangan memberi informasi yang bisa membahayakan. "
        "Tetap pakai Bahasa Indonesia santai yang lembut."
    )
    if resources:
        items = "\n".join(f"- {r}" for r in resources)
        text += f"\nSebutkan bantuan berikut secara natural (tanpa terdengar seperti robot):\n{items}"
    return text
