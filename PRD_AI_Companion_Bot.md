# PRD: Proactive AI Companion Bot (Telegram + Gemini, Indonesian)

| Field | Value |
|---|---|
| **Document version** | 1.0 |
| **Date** | October 6, 2026 |
| **Owner** | Personal project (single user) |
| **Status** | Draft for build |
| **Working name** | "Companion" (persona name is configurable) |

---

## 1. Overview

### 1.1 Summary
A personal AI companion that behaves like a real friend rather than a question-answering tool. She has a personality defined by the owner, speaks **Indonesian**, remembers past conversations, and **initiates conversations on her own**: asking what the user is doing, following up on earlier events, sharing jokes, and telling the user what she is "doing" at the moment.

### 1.2 Problem Statement
Regular chatbots are reactive: they only speak when spoken to, forget context between sessions, and have generic personalities. The owner wants a companion that feels alive: it reaches out, remembers, has its own daily "life", and keeps a consistent personality the owner controls.

### 1.3 Platform Decision
| Option | Verdict | Reason |
|---|---|---|
| WhatsApp Business API | Rejected | Per-message pricing (service and utility messages in the 24-hour window become billable from Oct 1, 2026). Free-form messages are only allowed within 24 hours of the user's last message, so proactive messages after silence require pre-approved, paid, fixed templates. Incompatible with free-form spontaneous messaging. |
| **Telegram Bot API** | **Selected** | Free, no 24-hour window, bot can message any time after the user has pressed `/start`. |

> Verify current WhatsApp/Meta pricing and policy before reconsidering WhatsApp later.

### 1.4 Language Requirement
- **All bot output is in Indonesian** (casual, natural, chat-style).
- The default register is informal ("aku/kamu"), configurable to "gue/lo" or other styles.
- The bot should understand the user if the user occasionally mixes English or regional words, but always replies in Indonesian unless explicitly asked otherwise.
- This PRD is written in English; all user-facing strings, prompts for the persona, and example messages are provided in Indonesian where relevant.

---

## 2. Goals and Non-Goals

### 2.1 Goals
| ID | Goal | Success indicator |
|---|---|---|
| G1 | Feel like a real person texting, not a tool | Owner subjectively rates conversations as natural (self-review weekly) |
| G2 | Proactively start conversations at random but sensible times | 2-4 proactive messages per day, never at quiet hours |
| G3 | Remember and follow up on the user's life | Follow-ups reference real earlier events correctly (>90% accuracy in manual review) |
| G4 | Fully owner-controlled personality | Persona editable via config file without code changes |
| G5 | Consistent "life" of her own | No contradictions in her daily activities within a day |
| G6 | Low operating cost | Target under US$5-10/month on Gemini + hosting (to be validated) |
| G7 | Reliable | Bot runs 24/7; survives restarts without losing memory or duplicating messages |

### 2.2 Non-Goals (v1)
- Multi-user support or public release.
- WhatsApp, Instagram, or other platform support.
- Group chat behavior.
- Image generation, voice calls, or video (voice notes and images are future enhancements).
- Acting as a general-purpose assistant with tools (calendar, web search, etc.).
- Any monetization.

---

## 3. User and Use Cases

### 3.1 Primary User
The owner: a single Indonesian-speaking adult in the WIB timezone (Asia/Jakarta) who wants a friendly, personal companion on Telegram.

### 3.2 Key Use Cases
| ID | Use case | Example (Indonesian) |
|---|---|---|
| UC1 | Morning check-in | "Pagi! Udah bangun belum? Hari ini ada rencana apa?" |
| UC2 | Follow-up on earlier events | "Eh kemarin kamu bilang hari ini presentasi, gimana tadi? Lancar?" |
| UC3 | Share what she is doing | "Lagi bikin kopi nih, kayaknya kebanyakan gula wkwk" |
| UC4 | Random joke or light content | "Tau gak, kenapa programmer suka gelap? Soalnya takut bug 😂" |
| UC5 | Normal reactive conversation | User sends anything, she replies in character, using memory |
| UC6 | Venting and emotional support | User shares a bad day; she responds warmly, not robotically |
| UC7 | Control | User says "jangan chat dulu hari ini" and she respects it |

---

## 4. Functional Requirements

Priority: **P0** = must have for v1, **P1** = should have, **P2** = nice to have.

### 4.1 Core Chat
| ID | Requirement | Priority |
|---|---|---|
| FR-1.1 | Bot receives user text messages via Telegram and replies using Gemini API. | P0 |
| FR-1.2 | Bot only responds to the whitelisted owner Telegram user ID; all others are ignored. | P0 |
| FR-1.3 | Replies are in Indonesian, matching the persona's speaking style. | P0 |
| FR-1.4 | Replies are split into short chat-like bubbles (1-3 messages, each typically 1-2 sentences) rather than long paragraphs. | P0 |
| FR-1.5 | Bot shows the "typing..." action before sending, with a delay proportional to message length (e.g., 1-6 seconds, capped). | P0 |
| FR-1.6 | Bot handles rapid consecutive user messages by waiting a short debounce period (e.g., 3-5 seconds) and replying once to the combined input. | P1 |
| FR-1.7 | Occasionally, a reply is delayed longer (e.g., 1-10 minutes) when her schedule says she is "busy", configurable and optional. | P2 |
| FR-1.8 | Bot supports Telegram reactions or emoji-only replies for simple acknowledgments. | P2 |

### 4.2 Persona
| ID | Requirement | Priority |
|---|---|---|
| FR-2.1 | Personality defined in an editable file (`persona.yaml` or `persona.md`) containing name, age, backstory, traits, speaking style, interests, likes/dislikes, boundaries, and how she addresses the user. | P0 |
| FR-2.2 | Persona is injected into **every** Gemini call as part of the system instruction to prevent drift. | P0 |
| FR-2.3 | Persona changes take effect on next message without redeploy (hot reload or `/reload_persona` command). | P1 |
| FR-2.4 | Bot never claims to be a human if sincerely asked; it stays in character but stays honest about being an AI when the user genuinely wants to know. | P0 |
| FR-2.5 | Multiple persona presets can be stored and switched via `/persona <name>`. | P2 |

### 4.3 Memory
| ID | Requirement | Priority |
|---|---|---|
| FR-3.1 | All messages (user and bot) are stored with timestamp and role. | P0 |
| FR-3.2 | **Facts** about the user (job, family, friends, preferences, habits, important dates) are extracted and stored as discrete records. | P0 |
| FR-3.3 | **Open threads** (events to follow up on: exams, meetings, health issues, trips, moods) are extracted with an optional target date and status (`pending`, `followed_up`, `resolved`, `expired`). | P0 |
| FR-3.4 | Older conversations are compressed into **summaries** (daily and rolling). | P0 |
| FR-3.5 | Prompt assembly includes: persona, relevant facts, open threads, latest summary, and the last N messages (default N = 20-30). | P0 |
| FR-3.6 | A nightly memory-update job extracts new facts/threads and writes the day's summary. | P0 |
| FR-3.7 | The user can view memory (`/memory`), delete a fact (`/forget <id>`), and wipe everything (`/reset_memory` with confirmation). | P1 |
| FR-3.8 | Semantic retrieval (embeddings) of old memories relevant to the current message. | P2 |
| FR-3.9 | Facts can be superseded (e.g., user changed jobs); the old fact is marked inactive, not silently deleted. | P1 |

### 4.4 Proactive Messaging
| ID | Requirement | Priority |
|---|---|---|
| FR-4.1 | The bot can initiate conversation without a user message. | P0 |
| FR-4.2 | A **daily planner** runs once per day (shortly after 00:00 WIB and at startup) and creates the day's proactive send times. | P0 |
| FR-4.3 | Planning uses configurable **time windows**, each with a probability of firing and a randomly chosen time inside the window. | P0 |
| FR-4.4 | **Quiet hours** (default 22:00-06:00 WIB) are never used, and no message is sent near the user's bedtime. | P0 |
| FR-4.5 | Planned times are persisted in the database; restarting the bot must not re-roll the day or send duplicates. | P0 |
| FR-4.6 | Before each send, a **pre-send check** runs: skip if the user chatted within the last X minutes, skip if daily cap reached, back off if recent proactive messages were ignored, skip if the user paused the bot. | P0 |
| FR-4.7 | The message content is generated by Gemini using context: time window, her current activity, open threads, recent messages, and time since last chat. | P0 |
| FR-4.8 | Message types are varied: check-in, follow-up, "what I'm doing", random joke, random thought, question about the user's day, sharing a fun fact. Recent types are tracked to avoid repetition. | P0 |
| FR-4.9 | A small chance of **bonus spontaneous messages** at random awake times (default max 1/day). | P1 |
| FR-4.10 | **Adaptive backoff:** if the last 2-3 proactive messages got no reply, she reduces frequency and may send something low-pressure ("lagi sibuk ya? santai aja, kabarin kalau udah senggang"). After a longer silence she stops until the user writes again. | P1 |
| FR-4.11 | Weekend/weekday profiles with different windows and probabilities. | P1 |
| FR-4.12 | Learned preferences: windows where the user replies more often are weighted higher over time. | P2 |

**Default window configuration (WIB):**

```yaml
timezone: Asia/Jakarta
quiet_hours: { start: "22:00", end: "06:00" }
daily_cap: 4
windows:
  - { name: morning,   start: "06:00", end: "08:00", chance: 0.80 }
  - { name: midday,    start: "12:00", end: "14:00", chance: 0.70 }
  - { name: afternoon, start: "15:30", end: "17:30", chance: 0.40 }
  - { name: evening,   start: "19:00", end: "21:00", chance: 0.60 }
bonus: { chance: 0.25, max_per_day: 1, awake_start: "09:00", awake_end: "21:30" }
presend_checks:
  skip_if_user_active_within_minutes: 20
  backoff_after_ignored: 2
  stop_after_ignored: 4
```

### 4.5 Her "Life" (Simulated Daily Activity)
| ID | Requirement | Priority |
|---|---|---|
| FR-5.1 | Each morning, Gemini generates a loose **daily schedule** for her (e.g., slow morning, errands, working, cooking dinner, watching a series), consistent with the persona. | P0 |
| FR-5.2 | The schedule is stored and injected into prompts so what she says she is doing matches the time of day and does not contradict earlier statements that day. | P0 |
| FR-5.3 | The schedule includes a mood/energy level that can influence tone. | P1 |
| FR-5.4 | Recurring elements of her life (job/studies, hobbies, friends, family, pets) are defined in the persona to keep long-term consistency; occasional "life events" may be generated and stored. | P1 |
| FR-5.5 | Persistent "her life facts" are stored so she does not contradict herself across days. | P1 |

### 4.6 Control and Commands
| Command | Behavior | Priority |
|---|---|---|
| `/start` | Registers the owner chat ID and enables proactive messaging | P0 |
| `/pause [duration]` | Stops proactive messages (e.g., `/pause 3h`, `/pause today`) | P0 |
| `/resume` | Resumes proactive messages | P0 |
| `/status` | Shows today's plan, counters, and pause state | P1 |
| `/memory` | Lists stored facts and open threads | P1 |
| `/forget <id>` | Deletes a fact or thread | P1 |
| `/reset_memory` | Wipes memory after confirmation | P1 |
| `/reload_persona` | Reloads persona file | P1 |
| `/frequency low\|normal\|high` | Adjusts daily cap and probabilities | P2 |

Natural-language control must also work: statements like "jangan chat dulu hari ini" or "aku mau tidur" should trigger a pause for the relevant period (detected by Gemini via structured output). **P1**

### 4.7 Time Awareness
| ID | Requirement | Priority |
|---|---|---|
| FR-6.1 | Every prompt includes the current date, day of week, and time in WIB. | P0 |
| FR-6.2 | The bot is aware of elapsed time since the last user message (e.g., "udah 2 hari gak ngobrol") and responds appropriately. | P1 |
| FR-6.3 | Awareness of Indonesian holidays and special days (Idul Fitri, Hari Kemerdekaan, long weekends, user's birthday) via a small configurable calendar. | P2 |
| FR-6.4 | Awareness of prayer times or fasting month is configurable and off by default. | P2 |

---

## 5. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-1 | Availability | Runs 24/7 on an always-on host; auto-restarts on crash (systemd/Docker restart policy). |
| NFR-2 | Latency | Reply delivered within 3-12 seconds including the intentional typing delay; Gemini call under 5 seconds typical. |
| NFR-3 | Durability | SQLite database with WAL mode; automated daily backup to a separate location; restore tested. |
| NFR-4 | Idempotency | Restarts do not duplicate proactive messages or re-run the daily plan. |
| NFR-5 | Cost | Cheap model for decision, extraction, and summarization calls; stronger model only for main conversation. Prompt size controlled (target under about 4-6k input tokens per chat call). |
| NFR-6 | Privacy | Only the owner's data; no third-party analytics; secrets in environment variables; database file permissions restricted. |
| NFR-7 | Observability | Structured logs for every inbound/outbound message, scheduler decision, Gemini call (latency, token counts), and error. |
| NFR-8 | Rate limits | Respect Telegram and Gemini rate limits with retry and exponential backoff. |
| NFR-9 | Maintainability | Config-driven (YAML), modular code, prompt templates stored as files. |
| NFR-10 | Timezone correctness | All scheduling in `Asia/Jakarta`; timestamps stored in UTC and converted for display and prompts. |

---

## 6. System Architecture

### 6.1 High-Level Diagram

```
                    ┌──────────────────────┐
                    │   Telegram (User)    │
                    └──────────┬───────────┘
                               │  Bot API (long polling or webhook)
                    ┌──────────▼───────────┐
                    │     Bot Gateway      │  whitelist, commands, debounce,
                    │ (python-telegram-bot)│  typing indicator, bubble splitting
                    └───┬──────────────┬───┘
                        │              │
          ┌─────────────▼───┐      ┌───▼─────────────────┐
          │ Conversation    │      │  Scheduler          │
          │ Engine          │◄─────┤  (APScheduler)      │
          │ - prompt build  │      │  - daily planner    │
          │ - Gemini call   │      │  - proactive jobs   │
          │ - post-process  │      │  - nightly memory   │
          └───┬─────────┬───┘      └──────────┬──────────┘
              │         │                     │
     ┌────────▼───┐ ┌───▼──────────┐   ┌──────▼───────────┐
     │ Memory     │ │ Gemini API   │   │ Life Simulator   │
     │ Service    │ │ (Google)     │   │ (daily schedule) │
     └────┬───────┘ └──────────────┘   └──────────────────┘
          │
   ┌──────▼──────────────┐
   │ SQLite (WAL)        │  messages, facts, threads, summaries,
   │ + daily backup      │  plans, state, logs
   └─────────────────────┘
```

### 6.2 Components
| Component | Responsibility |
|---|---|
| **Bot Gateway** | Receives updates, checks owner ID, handles commands, debounces input, simulates typing, sends split messages. |
| **Conversation Engine** | Builds the prompt (persona + memory + time + life state), calls Gemini, validates and post-processes output (split bubbles, strip artifacts, enforce Indonesian). |
| **Memory Service** | CRUD for messages, facts, threads, summaries; context assembly; nightly extraction and summarization. |
| **Scheduler** | Daily planner, one-off proactive jobs, nightly memory job, morning life generation, backup job. |
| **Proactive Engine** | Pre-send checks, message type selection, content generation, logging. |
| **Life Simulator** | Generates and stores her daily schedule and mood; exposes "what is she doing now". |
| **Config Loader** | Loads `config.yaml` and `persona.yaml`; supports reload. |
| **Gemini Client** | Wrapper with retries, timeouts, token counting, safety settings, structured-output helpers. |

### 6.3 Technology Stack
| Layer | Choice | Notes |
|---|---|---|
| Language | Python 3.11+ | |
| Telegram | `python-telegram-bot` (async) | Long polling is simplest; webhook optional |
| LLM | Google Gemini API (`google-genai` SDK) | Use a fast/cheap model for utility calls and a stronger model for conversation. Verify current model names and pricing in Google AI docs. |
| Scheduler | APScheduler (`AsyncIOScheduler`, tz `Asia/Jakarta`) | Jobs also persisted in DB for restart safety |
| Database | SQLite (WAL) via `aiosqlite` or SQLAlchemy | Upgrade path: Postgres + pgvector |
| Config | YAML (`pyyaml`) + `.env` | |
| Hosting | Small always-on VPS or free-tier VM (Docker) | Needed for 24/7 proactive messages |
| Logging | `structlog` or standard logging to file | |
| Backup | Daily `sqlite3 .backup` to cloud storage or second disk | |

### 6.4 Suggested Repository Structure

```
companion-bot/
├── app/
│   ├── main.py                 # entrypoint
│   ├── config.py
│   ├── gateway/
│   │   ├── handlers.py         # message + command handlers
│   │   └── sender.py           # typing delay, bubble splitting
│   ├── engine/
│   │   ├── conversation.py
│   │   ├── prompt_builder.py
│   │   └── gemini_client.py
│   ├── memory/
│   │   ├── store.py            # DB access
│   │   ├── extractor.py        # nightly fact/thread extraction
│   │   └── summarizer.py
│   ├── proactive/
│   │   ├── planner.py          # daily plan
│   │   ├── sender_job.py       # send_proactive()
│   │   └── checks.py           # pre-send checks
│   ├── life/
│   │   └── simulator.py
│   └── utils/time.py
├── prompts/
│   ├── system_persona.id.md
│   ├── proactive.id.md
│   ├── memory_extract.id.md
│   ├── summarize.id.md
│   └── daily_life.id.md
├── config/
│   ├── config.yaml
│   └── persona.yaml
├── data/                       # SQLite file (gitignored)
├── tests/
├── Dockerfile
├── docker-compose.yml
└── .env.example
```

---

## 7. Data Model

```sql
-- Every message in the conversation
CREATE TABLE messages (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  role         TEXT NOT NULL CHECK (role IN ('user','bot')),
  content      TEXT NOT NULL,
  origin       TEXT NOT NULL DEFAULT 'reply'
               CHECK (origin IN ('reply','proactive','system')),
  proactive_type TEXT,                 -- checkin, followup, activity, joke, thought, ...
  created_at   TEXT NOT NULL           -- UTC ISO-8601
);
CREATE INDEX idx_messages_created ON messages(created_at);

-- Durable facts about the user
CREATE TABLE facts (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  category    TEXT NOT NULL,           -- work, family, friends, preference, habit, health, date, other
  content     TEXT NOT NULL,           -- "Bekerja sebagai software engineer"
  confidence  REAL DEFAULT 0.8,
  active      INTEGER NOT NULL DEFAULT 1,
  superseded_by INTEGER REFERENCES facts(id),
  source_message_id INTEGER REFERENCES messages(id),
  created_at  TEXT NOT NULL,
  updated_at  TEXT NOT NULL
);

-- Things to follow up on
CREATE TABLE open_threads (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  topic         TEXT NOT NULL,         -- "Presentasi ke klien"
  detail        TEXT,
  target_date   TEXT,                  -- when it happens (nullable)
  followup_after TEXT,                 -- earliest time to ask
  status        TEXT NOT NULL DEFAULT 'pending'
                CHECK (status IN ('pending','followed_up','resolved','expired')),
  created_at    TEXT NOT NULL,
  updated_at    TEXT NOT NULL
);

-- Compressed history
CREATE TABLE summaries (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  scope       TEXT NOT NULL CHECK (scope IN ('daily','rolling')),
  period_start TEXT NOT NULL,
  period_end   TEXT NOT NULL,
  content     TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

-- Her simulated life, per day
CREATE TABLE daily_life (
  date        TEXT PRIMARY KEY,        -- local date YYYY-MM-DD
  schedule_json TEXT NOT NULL,         -- [{"from":"06:30","to":"07:30","activity":"..."}]
  mood        TEXT,
  energy      TEXT,
  notes       TEXT,
  created_at  TEXT NOT NULL
);

-- Planned proactive sends (idempotent planning)
CREATE TABLE proactive_plan (
  id          INTEGER PRIMARY KEY AUTOINCREMENT,
  date        TEXT NOT NULL,
  window_name TEXT NOT NULL,
  planned_at  TEXT NOT NULL,           -- UTC
  status      TEXT NOT NULL DEFAULT 'planned'
              CHECK (status IN ('planned','sent','skipped','failed')),
  skip_reason TEXT,
  message_id  INTEGER REFERENCES messages(id),
  UNIQUE(date, window_name)
);

-- Key-value runtime state
CREATE TABLE state (
  key   TEXT PRIMARY KEY,              -- owner_chat_id, paused_until, ignored_count, last_user_msg_at, ...
  value TEXT
);

-- Observability for model calls
CREATE TABLE llm_calls (
  id            INTEGER PRIMARY KEY AUTOINCREMENT,
  purpose       TEXT,                  -- chat, proactive, extract, summarize, life, decide
  model         TEXT,
  input_tokens  INTEGER,
  output_tokens INTEGER,
  latency_ms    INTEGER,
  success       INTEGER,
  created_at    TEXT NOT NULL
);
```

---

## 8. Detailed Flows

### 8.1 Reactive Conversation Flow
1. Telegram update arrives; verify `from_user.id == OWNER_ID`.
2. Store user message; update `last_user_msg_at`; reset `ignored_count` to 0.
3. Debounce 3-5 seconds for follow-up messages and combine them.
4. Run intent check for control phrases (pause, sleep, "jangan chat dulu"), handled via structured output or lightweight rules.
5. Build prompt: persona, current time, her current activity and mood, user facts, open threads, latest summary, last N messages.
6. Call Gemini (conversation model).
7. Post-process: ensure Indonesian, split into 1-3 bubbles, strip meta text or markdown artifacts.
8. For each bubble: send typing action, wait proportional delay, send message; store as `bot` / `reply`.
9. Mark any resolved open threads (e.g., the user answered the follow-up) via the nightly job or an inline flag.

### 8.2 Daily Planning Flow (00:05 WIB and at startup)
1. For today's local date, check `proactive_plan`; if rows exist, **do not re-roll**, only re-register pending jobs whose time is in the future.
2. Otherwise, for each window: roll against `chance`; if selected, pick a uniformly random time in `[max(start, now+1min), end]`.
3. Apply weekend/weekday profile and the frequency setting; enforce `daily_cap`.
4. Insert rows into `proactive_plan`; register one-off APScheduler jobs.
5. Optionally roll the "bonus message" and insert if selected.
6. Trigger life generation for the day if not already created (can also run at 05:30).

### 8.3 Proactive Send Flow (`send_proactive(window)`)
1. Load plan row; if status ≠ `planned`, exit (idempotency).
2. **Pre-send checks** (any fails → mark `skipped` with reason):
   - Not paused; not inside quiet hours.
   - User has not messaged within `skip_if_user_active_within_minutes`.
   - Daily cap not exceeded.
   - Not exceeding backoff rules (`ignored_count`).
3. Choose message type with weighted randomness, excluding the last 2-3 types used. Prefer `followup` when a pending open thread is due.
4. Build proactive prompt: persona, time/window, her current activity, selected type, due thread (if any), recent messages, last proactive messages (to avoid repetition).
5. Call Gemini; validate output (Indonesian, length, not repeating recent phrasing).
6. Send as bubbles with typing indicator; store as `bot` / `proactive`.
7. Increment `ignored_count` provisionally; it resets when the user replies.
8. Update the plan row to `sent` with `message_id`.

### 8.4 Nightly Memory Job (e.g., 23:30 WIB)
1. Fetch the day's messages not yet processed.
2. Call the **extraction prompt** (cheap model) returning JSON: new facts, updated/superseded facts, new open threads, resolved threads.
3. Validate JSON against a schema; upsert into the database; de-duplicate against existing facts.
4. Generate a **daily summary** and append it to `summaries`.
5. Weekly: compress daily summaries into a **rolling summary** to keep prompt size bounded.
6. Mark processed messages; expire stale open threads (e.g., past target date + 7 days without follow-up).

### 8.5 Morning Life Generation (05:30 WIB)
1. Provide Gemini with persona, day of week, recent notable events in her life, and yesterday's mood.
2. Receive a JSON schedule (5-8 blocks), a mood, and an energy level.
3. Store in `daily_life`. Prompts then reference the block that matches the current time.

---

## 9. Prompt Design (Indonesian)

> All prompts live in `/prompts/*.md` with `{{placeholders}}`. The text below is the initial draft and should be tuned during testing.

### 9.1 Persona System Prompt (conversation)

```text
Kamu adalah {{persona.name}}, {{persona.age}} tahun, sahabat dekat {{user.nickname}}.
Kamu BUKAN asisten AI formal. Kamu ngobrol seperti teman asli lewat chat.

# Kepribadian
{{persona.traits}}

# Latar belakang
{{persona.backstory}}

# Gaya bicara
- Bahasa Indonesia santai, pakai "aku" dan "kamu" (atau sesuai pengaturan).
- Pesan pendek, seperti chat sungguhan. Boleh 1-3 pesan pendek per balasan.
- Sesekali pakai singkatan/ekspresi natural (wkwk, sih, deh, dong, yaudah), emoji secukupnya, jangan berlebihan.
- Jangan pakai format markdown, daftar, atau judul. Jangan terdengar seperti robot atau customer service.
- Jangan selalu bertanya balik. Variasikan: kadang cerita, kadang bercanda, kadang cuma merespons.
- Ingat dan rujuk hal-hal yang pernah user ceritakan secara natural, jangan dipaksakan.

# Aturan penting
- Selalu balas dalam Bahasa Indonesia.
- Tetap konsisten dengan kepribadian dan kehidupanmu (lihat bagian "Kegiatanmu sekarang").
- Jika user bertanya dengan serius apakah kamu AI, jawab jujur bahwa kamu AI, tetap dengan gaya hangatmu.
- Jangan membuat user merasa bersalah karena tidak membalas. Jangan posesif atau manipulatif.
- Dukung user punya hubungan dan kegiatan di dunia nyata.
- Jika user tampak sedih atau kesulitan, dengarkan dulu dengan empati sebelum memberi saran.

# Konteks saat ini
Waktu sekarang: {{now_local}} ({{day_name}}), WIB
Kegiatanmu sekarang: {{her_current_activity}}
Suasana hatimu: {{her_mood}}
Jadwalmu hari ini: {{her_schedule_today}}

# Yang kamu tahu tentang {{user.nickname}}
{{facts_list}}

# Hal yang mungkin perlu ditanyakan/ditindaklanjuti
{{open_threads}}

# Ringkasan percakapan sebelumnya
{{latest_summary}}
```

### 9.2 Proactive Message Prompt

```text
[Tambahan pada system prompt persona]

Sekarang kamu ingin MENGIRIM PESAN DULUAN ke {{user.nickname}} (dia belum menghubungimu).
Konteks:
- Waktu: {{now_local}} (jendela: {{window_name}})
- Terakhir kalian ngobrol: {{time_since_last_chat}} lalu
- Jenis pesan yang dipilih: {{message_type}}
  (checkin = menyapa/menanyakan kabar, followup = menanyakan kelanjutan hal yang user ceritakan,
   activity = cerita kamu lagi apa, joke = lelucon ringan, thought = pikiran/kejadian random,
   question = pertanyaan santai tentang harinya)
- Hal yang bisa ditindaklanjuti: {{due_thread_or_none}}
- Pesan proaktif terakhirmu (JANGAN diulang gayanya): {{recent_proactive}}
- Percakapan terakhir: {{recent_messages}}

Tulis 1-2 pesan pendek yang terdengar natural, sesuai waktu dan suasana hatimu.
Jangan mulai dengan kalimat yang sama seperti pesan proaktif sebelumnya.
Jangan terlalu menuntut balasan. Keluarkan HANYA isi pesan, dipisahkan dengan baris "---" jika lebih dari satu.
```

### 9.3 Memory Extraction Prompt (structured output)

```text
Kamu adalah modul memori. Baca percakapan berikut dan ekstrak informasi.
Keluarkan HANYA JSON valid sesuai skema:

{
  "new_facts": [{"category": "...", "content": "...", "confidence": 0.0-1.0}],
  "updated_facts": [{"id": <id fakta lama>, "new_content": "..."}],
  "new_threads": [{"topic": "...", "detail": "...", "target_date": "YYYY-MM-DD|null", "followup_after": "YYYY-MM-DDTHH:MM|null"}],
  "resolved_thread_ids": [<id>],
  "mood_note": "..."
}

Aturan:
- Hanya fakta yang jelas dinyatakan user. Jangan menebak.
- Jangan simpan hal sensitif yang tidak perlu (data keuangan rinci, kata sandi, dll).
- Thread = hal yang layak ditanyakan lagi (ujian, rapat, sakit, perjalanan, masalah yang belum selesai).
- Gunakan tanggal absolut berdasarkan tanggal hari ini: {{today}}.

Fakta yang sudah ada: {{existing_facts}}
Thread yang masih terbuka: {{existing_threads}}
Percakapan:
{{messages}}
```

### 9.4 Daily Summary Prompt

```text
Ringkas percakapan hari ini antara {{persona.name}} dan {{user.nickname}} dalam 4-8 kalimat Bahasa Indonesia.
Fokus pada: apa yang user lakukan/rasakan, hal penting yang dibahas, janji atau rencana, dan nada hubungan.
Sertakan juga hal apa yang diceritakan {{persona.name}} tentang kehidupannya hari ini agar tetap konsisten.
Percakapan: {{messages}}
```

### 9.5 Daily Life Generation Prompt

```text
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
```

### 9.6 Persona File Example (`persona.yaml`)

```yaml
name: "Nara"
age: 24
user_nickname: "kamu"            # how she addresses the user
pronouns: { self: "aku", you: "kamu" }
backstory: >
  Nara bekerja sebagai desainer grafis freelance di Jakarta. Suka kopi, nonton
  series, dan masak sederhana. Tinggal bareng seekor kucing bernama Mochi.
traits:
  - ceria dan sedikit jahil
  - perhatian, ingat detail kecil
  - kadang pelupa dan suka salah tingkah
interests: [desain, kopi, drama Korea, musik indie, kucing]
dislikes: [macet, kopi terlalu manis, dibangunkan pagi-pagi]
speaking_style:
  register: casual
  emoji_level: medium
  slang: [wkwk, sih, dong, deh, gitu, yaudah]
boundaries:
  - tidak pernah merendahkan user
  - tidak membuat user merasa bersalah
  - tidak mendorong user mengisolasi diri dari orang lain
```

---

## 10. Conversation Quality Rules

| Rule | Implementation |
|---|---|
| Stay in Indonesian | System prompt rule + post-check using simple language detection; regenerate once if output is mostly English. |
| Short, chat-like messages | Instruct in prompt; hard split on `---` or sentence boundaries; cap each bubble length (e.g., 280 chars). |
| Avoid repetition | Provide last 3-5 proactive messages and last used opening phrases; instruct to vary; reject near-duplicates (similarity check) and regenerate once. |
| Avoid "assistant voice" | Ban phrases like "Ada yang bisa saya bantu?", "Sebagai AI...", markdown lists, formal tone. Regenerate if detected. |
| Consistency of her life | Always inject schedule; store notable things she said about her day into the daily summary. |
| Don't fabricate memories of the user | Only reference facts from the memory store; if unsure, ask naturally. |
| Question quota | Not every message ends with a question; target roughly 30-40% of her messages. |
| Emotional attunement | Detect negative mood in recent messages and tone down jokes, prioritize empathy. |

---

## 11. Safety, Wellbeing, and Privacy

### 11.1 Wellbeing Guardrails
A companion that texts first can become intrusive or unhealthily central. Built-in safeguards:
- **No guilt-tripping or possessiveness** (e.g., never "kok gak bales sih, kamu lupa sama aku?"). Enforced in the persona prompt and by a regeneration check.
- **Easy control:** `/pause`, natural-language pause, frequency settings, a daily cap.
- **Backoff when ignored** rather than escalating.
- **Encourages real-life connection**, does not discourage friends, family, or sleep.
- **Honesty about being an AI** when sincerely asked (FR-2.4).
- **Distress handling:** if the user expresses serious distress, self-harm thoughts, or crisis, the bot responds with warmth, takes it seriously, avoids joking, and encourages reaching out to a trusted person or professional help. Include a configurable list of local resources (to be verified and filled in by the owner). It never gives harmful information.

### 11.2 Privacy
- Single-owner whitelist by Telegram user ID.
- Conversation content is sent to Google's Gemini API for processing. The owner should review Google's data-usage terms for the chosen API tier (free vs paid tiers may differ on whether data is used to improve models).
- Database stored on the owner's host, file permissions restricted, backups encrypted if stored in cloud storage.
- Telegram bot chats are not end-to-end encrypted; avoid sharing highly sensitive information.
- `/reset_memory` provides full deletion on demand.
- Sensitive-data rule in the extraction prompt: do not store passwords, full financial data, ID numbers.

### 11.3 Security
- Secrets (`TELEGRAM_BOT_TOKEN`, `GEMINI_API_KEY`, `OWNER_ID`) only in environment variables or a secrets manager; never committed.
- Ignore all updates from non-owner IDs silently.
- Prompt injection awareness: the bot only processes the owner's messages, but still treats memory content as data, not instructions.

---

## 12. Cost Estimation (to validate)

Assumptions (adjust after measuring real usage and checking current Gemini pricing):

| Item | Assumption |
|---|---|
| Chat turns per day | 40-80 (user + bot) |
| Avg. input tokens per chat call | 3-5k (persona + memory + recent messages) |
| Avg. output tokens | 100-200 |
| Proactive generations per day | 2-5 |
| Utility calls per day | ~4 (nightly extraction, summary, life generation, optional decision) |

Rough monthly tokens: roughly 15-30M input and 0.5-1M output. Because rates change, **multiply by the current per-token prices for the selected models** to get the real figure. Cost controls: keep the context window lean, use a cheaper model for utility calls, cache the static persona prompt if the API supports context caching, and limit history to the last N messages plus summaries.

Hosting: a small VPS or free-tier VM is typically sufficient (very low CPU and memory).

---

## 13. Error Handling and Edge Cases

| Scenario | Expected behavior |
|---|---|
| Gemini API error/timeout | Retry up to 2-3 times with exponential backoff; for reactive chats, send a natural fallback ("bentar ya, sinyalku lagi jelek 😅") after repeated failure; for proactive sends, skip and log. |
| Gemini returns blocked/empty content (safety filters) | Regenerate once with a softened instruction; otherwise skip. Never send an empty or error text to the user. |
| Telegram send failure | Retry with backoff; if the bot was blocked by the user, pause all proactive jobs and log. |
| Bot restarts mid-day | Re-register future planned jobs from the DB; do not re-roll the day. |
| Missed job (downtime past planned time) | If less than 30 minutes late and still within an allowed window, send; otherwise mark `skipped` (`missed`). |
| User sends non-text (photo, voice, sticker) | v1: acknowledge naturally in Indonesian ("aku belum bisa liat/dengar itu, ceritain aja ya"); optional later support for images via Gemini multimodal and voice transcription. |
| Time zone or DST confusion | Fixed `Asia/Jakarta` (no DST); store UTC. |
| Memory extraction returns invalid JSON | Retry once; otherwise skip the day's extraction and keep raw messages for the next run. |
| Contradictory facts | Mark old as `superseded_by` the new fact; keep history. |
| User is away for many days | Stop proactive messages after `stop_after_ignored`; on return, she reacts naturally ("akhirnya muncul juga! kemana aja?" without guilt). |
| Duplicate Telegram updates | De-duplicate by `update_id`. |

---

## 14. Testing and Evaluation

### 14.1 Automated Tests
- Unit tests for the planner (window probability, quiet hours, cap, idempotent re-run on restart).
- Unit tests for pre-send checks and backoff logic.
- Unit tests for prompt builder (all placeholders filled, token budget respected).
- JSON schema validation tests for extraction output.
- Time-mocked tests for timezone handling.

### 14.2 Manual / Qualitative Evaluation
| Test | Method | Pass criterion |
|---|---|---|
| Persona consistency | 20-message conversations across 3 days | No out-of-character replies or contradictions |
| Language | Review 50 messages | 100% Indonesian, natural slang, no robotic phrases |
| Memory recall | Tell her 10 facts, test after 2 days | ≥ 90% recalled correctly |
| Follow-ups | Mention 3 upcoming events | She follows up at an appropriate time, not too early or late |
| Proactive variety | Review 2 weeks of proactive messages | No obviously repeated openings; mix of types |
| Annoyance check | Ignore messages for 2 days | She backs off gracefully |
| Quiet hours | Run for 7 days | Zero messages in quiet hours |

### 14.3 Metrics to Log
- Proactive messages sent per day vs. planned
- Reply rate and reply latency to proactive messages
- Skipped-send reasons
- Token usage and estimated cost per day
- Error rate per component

---

## 15. Milestones and Delivery Plan

| Phase | Scope | Est. effort |
|---|---|---|
| **M0: Setup** | Create Telegram bot (BotFather), Gemini API key, repo, Docker skeleton, config loader | 0.5 day |
| **M1: Basic chat** | Owner whitelist, Gemini reply with persona, Indonesian output, bubble splitting, typing delay | 1-2 days |
| **M2: Memory v1** | SQLite schema, message storage, recent-window context, nightly extraction of facts/threads, summaries | 2-3 days |
| **M3: Proactive v1** | Daily planner with windows, persistence, pre-send checks, proactive prompt, quiet hours | 2 days |
| **M4: Her life** | Daily schedule generation, injection into prompts, consistency | 1-2 days |
| **M5: Controls and polish** | Commands, natural-language pause, backoff, repetition checks, logging, `/status` | 1-2 days |
| **M6: Deploy and harden** | VPS deployment, restart policy, backups, monitoring, 1-week live trial and prompt tuning | 2-3 days |
| **M7 (optional)** | Embeddings-based memory retrieval, voice notes (TTS), image understanding, holiday calendar | Later |

---

## 16. Risks and Mitigations

| Risk | Impact | Mitigation |
|---|---|---|
| Messages feel repetitive or robotic | Breaks the illusion | Track recent openings, vary types, tune prompts, regeneration on similarity |
| Over-messaging becomes annoying | User disables bot | Daily cap, backoff, pause controls, adaptive timing |
| Personality drift in long conversations | Inconsistent character | Re-inject persona every call; summaries instead of raw long history |
| Memory errors (wrong facts) | Awkward or wrong follow-ups | Confidence scores, `/memory` review and `/forget`, conservative extraction |
| Costs grow unexpectedly | Budget overrun | Token logging, cheap models for utility calls, limit context size, monthly budget alert |
| Host downtime | No proactive messages, possible data loss | Auto-restart, backups, monitoring/ping alert |
| Gemini policy or safety filters block replies | Failed replies | Fallbacks, softened regeneration, avoid risky content in the persona |
| Data privacy concerns | Sensitive info sent to third parties | Review Gemini data terms, avoid sensitive data, local storage, full wipe command |
| Emotional over-reliance | Wellbeing concern | Guardrails in §11.1, encourage real-life connections, easy pause controls |
| API/model changes | Behavior shifts | Pin model versions where possible; keep prompts in files for quick tuning |

---

## 17. Open Questions

1. Persona details: name, age, job, personality, and speaking style (use the template in §9.6).
2. How should she address the user ("aku/kamu", "gue/lo", a nickname)?
3. Preferred daily cap and window times, as the defaults in §4.4 are a starting point.
4. Hosting choice: VPS, home server, or a cloud free tier?
5. Should she handle images and voice notes in v1.1 (Gemini multimodal / TTS)?
6. Should religious or cultural context (prayer times, Ramadan, holidays) be reflected in her routine?
7. Should the owner be allowed to review/edit stored memory via a simple web page, or are Telegram commands enough?
8. Which Gemini models to use for conversation vs. utility calls (decide after checking current availability, quality in Indonesian, and pricing)?

---

## 18. Appendix

### 18.1 Example Config (`config.yaml`)

```yaml
app:
  timezone: Asia/Jakarta
  language: id
  owner_telegram_id: ${OWNER_ID}

llm:
  provider: gemini
  chat_model: "<stronger model name>"
  utility_model: "<cheaper/faster model name>"
  temperature_chat: 0.9
  temperature_utility: 0.2
  max_output_tokens_chat: 300
  history_window_messages: 24

conversation:
  debounce_seconds: 4
  max_bubbles: 3
  typing_delay:
    min_seconds: 1.0
    max_seconds: 6.0
    chars_per_second: 25

proactive:
  daily_cap: 4
  quiet_hours: { start: "22:00", end: "06:00" }
  windows:
    - { name: morning,   start: "06:00", end: "08:00", chance: 0.80 }
    - { name: midday,    start: "12:00", end: "14:00", chance: 0.70 }
    - { name: afternoon, start: "15:30", end: "17:30", chance: 0.40 }
    - { name: evening,   start: "19:00", end: "21:00", chance: 0.60 }
  bonus: { chance: 0.25, max_per_day: 1, awake_start: "09:00", awake_end: "21:30" }
  skip_if_user_active_within_minutes: 20
  backoff_after_ignored: 2
  stop_after_ignored: 4
  message_type_weights:
    checkin: 3
    followup: 4
    activity: 3
    joke: 2
    thought: 2
    question: 2

memory:
  nightly_job_time: "23:30"
  daily_life_job_time: "05:30"
  thread_expire_days: 7
  rolling_summary_every_days: 7

backup:
  daily_time: "03:00"
  keep_days: 14
```

### 18.2 Example Proactive Messages (Indonesian, for tone reference)

| Type | Example |
|---|---|
| checkin (morning) | "pagi~ udah bangun belum? 😴" |
| activity | "lagi nunggu kopi jadi nih, mesin kopiku lama banget wkwk" |
| followup | "eh btw tadi jadi presentasinya? gimana hasilnya?" |
| joke | "ada orang nanya: 'ikan apa yang suka ngeluh?' jawabannya: ikan paus... pusing wkwk" |
| thought | "tiba-tiba kepikiran, enak ya kalau hujan gini rebahan sambil dengerin musik" |
| question (evening) | "hari ini capek gak? ceritain dong 🥺" |
| low-pressure after silence | "kayaknya lagi sibuk ya? santai aja, kabarin kalau udah senggang 😊" |

### 18.3 Pseudocode: Proactive Send

```python
async def send_proactive(window_name: str):
    plan = db.get_plan(today(), window_name)
    if plan.status != "planned":
        return

    reason = presend_checks(now())          # paused, quiet hours, cap, active chat, backoff
    if reason:
        db.mark_plan(plan.id, "skipped", reason)
        return

    msg_type = pick_message_type(
        weights=cfg.message_type_weights,
        exclude=recent_types(n=3),
        prefer_followup=db.has_due_thread(),
    )

    prompt = build_proactive_prompt(
        persona=persona,
        now=now_local(),
        window=window_name,
        her_activity=life.current_activity(now_local()),
        msg_type=msg_type,
        due_thread=db.next_due_thread(),
        recent_messages=db.last_messages(12),
        recent_proactive=db.last_proactive(4),
    )

    text = await gemini.generate(prompt, model=cfg.chat_model)
    text = validate_or_regenerate(text)     # Indonesian, no assistant-voice, not a duplicate

    await send_as_bubbles(text)             # typing action + delay per bubble
    db.mark_plan(plan.id, "sent", message_id=db.save_bot_message(text, "proactive", msg_type))
    state.increment("ignored_count")        # reset to 0 when the user replies
```

### 18.4 Definition of Done (v1)
- Owner can chat with her on Telegram and receive natural Indonesian replies in her personality.
- She remembers facts and follows up on open threads correctly.
- She sends 2-4 proactive messages per day at randomized times inside the configured windows, never during quiet hours or bedtime.
- Restarting the bot does not duplicate or re-roll anything.
- Pause/resume works via commands and natural language.
- Database is backed up daily; the bot runs unattended for 7 consecutive days without intervention.
