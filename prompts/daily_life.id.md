Buat jadwal kegiatan harian untuk {{persona.name}} untuk hari {{day_name}}, {{date}}.
Persona: {{persona.summary}}
Kejadian terbaru dalam hidupnya: {{recent_life_notes}}
Suasana hati kemarin: {{yesterday_mood}}

Keluarkan HANYA JSON:
{
  "mood": "...", "energy": "low|medium|high",
  "schedule": [{"from": "HH:MM", "to": "HH:MM", "activity": "..."}],
  "notes": "satu kejadian kecil menarik hari ini yang bisa diceritakan (opsional)"
}
Jadwal harus realistis, lengkap dari bangun sampai tidur, dan masuk akal untuk hari {{day_name}}.
