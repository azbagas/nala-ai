from __future__ import annotations

from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from app.config import Persona, Pronouns, SpeakingStyle
from app.engine.prompt_builder import PromptBuilder

ROOT = Path(__file__).resolve().parent.parent
PROMPTS_DIR = ROOT / "prompts"
WIB = ZoneInfo("Asia/Jakarta")


def test_prompt_builder_renders_all_fields():
    persona = Persona(
        name="Nara",
        age=24,
        user_nickname="kamu",
        pronouns=Pronouns(self="aku", you="kamu"),
        backstory="Desainer freelance",
        traits=["ceria", "perhatian"],
        interests=["kopi", "kucing"],
        dislikes=["macet"],
        speaking_style=SpeakingStyle(register="casual", emoji_level="medium", slang=["wkwk", "sih"]),
        boundaries=["tidak merendahkan"],
    )

    builder = PromptBuilder(PROMPTS_DIR, persona, WIB)
    dt = datetime(2026, 10, 6, 14, 30, tzinfo=WIB)
    rendered = builder.build_system_prompt(
        dt=dt,
        activity="Lagi minum kopi",
        mood="senang",
        schedule="Santai di kafe",
        facts=["Suka musik indie"],
        open_threads=["Ujian besok"],
        latest_summary="Kemarin ngobrol soal liburan",
    )

    assert "Nara" in rendered
    assert "24" in rendered
    assert "14:30" in rendered
    assert "Selasa" in rendered
    assert "Lagi minum kopi" in rendered
    assert "senang" in rendered
    assert "Santai di kafe" in rendered
    assert "Suka musik indie" in rendered
    assert "Ujian besok" in rendered
    assert "Kemarin ngobrol soal liburan" in rendered
    # Ensure no unresolved {{variables}} remain
    assert "{{" not in rendered
    assert "}}" not in rendered


def test_prompt_builder_set_persona():
    p1 = Persona(name="Nara", traits=["ceria"])
    p2 = Persona(name="Maya", traits=["kalem"])

    builder = PromptBuilder(PROMPTS_DIR, p1, WIB)
    assert "Nara" in builder.build_system_prompt()

    builder.set_persona(p2)
    assert "Maya" in builder.build_system_prompt()
    assert "Nara" not in builder.build_system_prompt()


def test_prompt_builder_missing_template(tmp_path):
    p = Persona(name="Test")
    builder = PromptBuilder(tmp_path, p, WIB)
    with pytest.raises(FileNotFoundError):
        builder.build_system_prompt()
