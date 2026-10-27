"""Prompt builder.

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


def _format_life(life: dict[str, Any] | None) -> str:
    """Format life elements (pets, friends, family, job) into clean readable lines."""
    if not life:
        return "(tidak ada catatan kehidupan khusus)"
    labels = {
        "job": "Pekerjaan",
        "pets": "Hewan peliharaan",
        "friends": "Teman/Sahabat",
        "family": "Keluarga",
    }
    lines: list[str] = []
    for key, val in life.items():
        label = labels.get(key.lower(), key.replace("_", " ").capitalize())
        if isinstance(val, list):
            val_str = ", ".join(str(v) for v in val)
        else:
            val_str = str(val)
        lines.append(f"- {label}: {val_str}")
    return "\n".join(lines) if lines else "(tidak ada catatan kehidupan khusus)"


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
        user_name: str | None = None,
    ) -> str:
        """Build the complete system instruction prompt for reactive chat."""
        template = self._read_template("system_persona.id.md")
        now_dt = dt or now_local(self.tz)

        # Persona fields
        p = self.persona
        traits_str = "\n".join(f"- {t}" for t in p.traits) if p.traits else "- ramah dan perhatian"
        boundaries_str = "\n".join(f"- {b}" for b in p.boundaries) if p.boundaries else "- menjaga rasa hormat dan empati"
        slang_str = ", ".join(p.speaking_style.slang) if p.speaking_style.slang else "santai, wkwk, yaudah"
        interests_str = "\n".join(f"- {i}" for i in p.interests) if p.interests else "(tidak ada catatan minat khusus)"
        dislikes_str = "\n".join(f"- {d}" for d in p.dislikes) if p.dislikes else "(tidak ada catatan hal yang tidak disukai)"
        life_str = _format_life(p.life)
        register_str = (p.speaking_style.register_ or "casual").lower()

        # Dynamic emoji level instruction
        emoji_level = (p.speaking_style.emoji_level or "medium").lower()
        if emoji_level == "low":
            emoji_instruction = "Penggunaan emoji (level LOW): Sangat jarang pakai emoji. Lebih sering chat tanpa emoji sama sekali, atau maksimal 1 emoji sesekali saja bila benar-benar perlu."
        elif emoji_level == "high":
            emoji_instruction = "Penggunaan emoji (level HIGH): Sering dan ekspresif menggunakan emoji (1-2 emoji seperti 😭, 🥹, 😩, atau perpaduan 'hehe 😭') untuk mempertegas nuansa emosional."
        else:
            emoji_instruction = "Penggunaan emoji (level MEDIUM): Gunakan emoji secukupnya (cukup 1 emoji per pesan) sebagai intonasi santai, jangan berlebihan."

        # Dynamic user name & relationship context
        known_name = user_name or (p.user_nickname if p.user_nickname and p.user_nickname.lower() != "kamu" else None)
        if known_name:
            effective_nickname = known_name
            rel_context = (
                f"- Kamu sudah tahu nama user adalah {known_name}.\n"
                f"- Panggil user dengan nama \"{known_name}\" atau sesekali \"kamu\".\n"
                "- Sesuaikan tingkat keakraban secara alami mengikuti nada dan alur percakapan yang sedang berjalan."
            )
        else:
            effective_nickname = p.pronouns.you or "kamu"
            rel_context = (
                "- Kamu dan user BARU PERTAMA KALI MENGOBROL / belum saling kenal (seperti dua orang asing yang baru bertemu).\n"
                "- Kamu BELUM TAHU siapa nama user. Jangan pernah menebak atau mengarang nama user.\n"
                "- Panggil user secara netral dengan sebutan \"kamu\".\n"
                "- Jangan bersikap sok tahu tentang kehidupan, pekerjaan, atau masa lalu user.\n"
                "- Bersikaplah ramah, santai, dan terbuka untuk kenalan. Kamu boleh tanyakan namanya dengan santai atau tunggu dia mengenalkan dirinya."
            )

        # Memory fields (defaults for M1 until M2 memory store is connected)
        if facts:
            facts_str = "\n".join(f"- {f}" for f in facts)
        elif known_name:
            facts_str = "(Belum ada catatan khusus tentang kamu)"
        else:
            facts_str = "(Belum ada catatan khusus karena kalian baru saling kenal)"

        threads_str = "\n".join(f"- {t}" for t in open_threads) if open_threads else "(Tidak ada topik yang sedang ditindaklanjuti)"
        summary_str = latest_summary or "(Percakapan baru dimulai)"

        context: dict[str, Any] = {
            "persona.name": p.name,
            "persona.age": p.age if p.age is not None else 24,
            "user.nickname": effective_nickname,
            "relationship_context": rel_context,
            "persona.traits": traits_str,
            "persona.backstory": p.backstory.strip(),
            "persona.interests": interests_str,
            "persona.dislikes": dislikes_str,
            "persona.life": life_str,
            "persona.register": register_str,
            "persona.pronoun_self": p.pronouns.self_,
            "persona.pronoun_you": p.pronouns.you,
            "persona.slang": slang_str,
            "persona.emoji_instruction": emoji_instruction,
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

    async def build_system_prompt_from_store(
        self,
        store: Any,
        *,
        dt: datetime | None = None,
        activity: str | None = None,
        mood: str | None = None,
        schedule: str | None = None,
    ) -> str:
        """Fetch active facts, pending threads, latest summary, user name, and today's daily life from store, then render prompt."""
        active_facts = await store.get_active_facts()
        pending_threads = await store.get_pending_threads()
        latest_summary_rec = await store.get_latest_summary()

        facts_list = [f"[{f.category}] {f.content}" for f in active_facts] if active_facts else None
        threads_list = [
            f"{t.topic}" + (f" (target: {t.target_date})" if t.target_date else "")
            for t in pending_threads
        ] if pending_threads else None
        summary_text = latest_summary_rec.content if latest_summary_rec else None

        user_name: str | None = None
        if hasattr(store, "get_user_name"):
            try:
                user_name = await store.get_user_name()
            except Exception as exc:
                log.warning("failed_to_get_user_name error=%r", exc)

        now_dt = dt or now_local(self.tz)
        today_str = now_dt.strftime("%Y-%m-%d")

        # Automatically resolve simulated daily life if not explicitly overridden
        if (activity is None or mood is None or schedule is None) and hasattr(store, "get_daily_life"):
            try:
                daily_rec = await store.get_daily_life(today_str)
                if daily_rec:
                    from app.life.simulator import format_schedule_summary, resolve_current_activity

                    if activity is None:
                        act = resolve_current_activity(daily_rec.schedule, now_dt)
                        if daily_rec.notes:
                            activity = f"{act} (catatan hari ini: {daily_rec.notes})"
                        else:
                            activity = act
                    if mood is None:
                        mood = f"{daily_rec.mood} (tingkat energi: {daily_rec.energy})"
                    if schedule is None:
                        schedule = format_schedule_summary(daily_rec.schedule)
            except Exception as exc:
                log.warning("failed_to_resolve_daily_life_in_prompt error=%r", exc)

        return self.build_system_prompt(
            dt=now_dt,
            activity=activity,
            mood=mood,
            schedule=schedule,
            facts=facts_list,
            open_threads=threads_list,
            latest_summary=summary_text,
            user_name=user_name,
        )

    def build_proactive_prompt(
        self,
        *,
        window_name: str,
        message_type: str,
        time_since_last_chat: str,
        due_thread: str | None = None,
        recent_proactive: list[str] | None = None,
        recent_messages: list[str] | None = None,
        user_name: str | None = None,
        dt: datetime | None = None,
    ) -> str:
        """Render the proactive message prompt template (prompts/proactive.id.md)."""
        template = self._read_template("proactive.id.md")
        now_dt = dt or now_local(self.tz)

        effective_nickname = user_name or (self.persona.user_nickname if self.persona.user_nickname and self.persona.user_nickname.lower() != "kamu" else (self.persona.pronouns.you or "kamu"))

        proactive_str = (
            "\n".join(f"- {p}" for p in recent_proactive)
            if recent_proactive
            else "(Belum ada pesan proaktif sebelumnya)"
        )
        recent_msgs_str = (
            "\n".join(recent_messages)
            if recent_messages
            else "(Belum ada riwayat percakapan)"
        )

        context: dict[str, Any] = {
            "user.nickname": effective_nickname,
            "now_local": f"{now_dt:%H:%M}",
            "window_name": window_name,
            "time_since_last_chat": time_since_last_chat,
            "message_type": message_type,
            "due_thread_or_none": due_thread or "(Tidak ada hal tertunda khusus)",
            "recent_proactive": proactive_str,
            "recent_messages": recent_msgs_str,
        }

        return self._render(template, context)


def format_time_elapsed_id(seconds: float) -> str:
    """Format elapsed seconds into natural Indonesian description."""
    if seconds < 60:
        return "baru saja"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes} menit"
    hours = int(minutes // 60)
    if hours < 24:
        return f"{hours} jam"
    days = int(hours // 24)
    if days < 7:
        return f"{days} hari"
    weeks = int(days // 7)
    return f"{weeks} minggu"

