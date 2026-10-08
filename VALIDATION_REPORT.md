# Validation Report: Proactive AI Companion Bot

| | |
|---|---|
| **Date** | 2026-10-08 |
| **Reference** | [PRD_AI_Companion_Bot.md](PRD_AI_Companion_Bot.md) v1.0 |
| **Method** | Static review of `app/`, `config/`, `prompts/`, `deploy/`, `tests/`, plus a test run (`pytest`) |
| **Scope limit** | The tail of the PRD (lines 800-863) was not read. I did not run live Telegram/Gemini calls, so runtime behaviour is unverified. |

## 1. Verdict

**The project mostly matches the PRD. All P0 requirements are implemented. Code quality is good, with a few real gaps and bugs listed below.**

- 95 of 96 tests pass. The one failure is a test bug, not a product bug (see 4.1).
- The code is modular and follows the PRD's suggested structure.
- The remaining issues are mostly edge-case robustness and wellbeing features. None of them stop the bot from working.
- I would not call it fully production-ready until the High items in section 5 are fixed.

## 2. Requirements Coverage

Legend: ✅ done, ⚠️ partial, ❌ missing

### 2.1 Core chat (§4.1)
| ID | Pri | Status | Evidence / Notes |
|---|---|---|---|
| FR-1.1 | P0 | ✅ | `ConversationEngine.reply` calls Gemini (`engine/conversation.py`). |
| FR-1.2 | P0 | ✅ | `_is_owner` check in every handler. If no owner is configured, all messages are ignored. |
| FR-1.3 | P0 | ✅ | Persona prompt plus English and assistant-voice check, with one retry. |
| FR-1.4 | P0 | ✅ | `split_bubbles` splits on `---`, paragraphs and sentences, capped at 3 bubbles and 280 characters. |
| FR-1.5 | P0 | ✅ | `send_bubbles` sends the typing action, then a delay proportional to length (1-6 s). |
| FR-1.6 | P1 | ✅ | `MessageDebouncer` (4 s). |
| FR-1.7 | P2 | ❌ | No delayed reply when she is "busy". |
| FR-1.8 | P2 | ❌ | No Telegram reactions or emoji-only replies. |

### 2.2 Persona (§4.2)
| ID | Pri | Status | Notes |
|---|---|---|---|
| FR-2.1 | P0 | ✅ | `config/persona.yaml`. |
| FR-2.2 | P0 | ✅ | System instruction is rebuilt on every call, for both chat and proactive. |
| FR-2.3 | P1 | ✅ | `/reload_persona`. This also updates the summarizer and life simulator. |
| FR-2.4 | P0 | ⚠️ | The honesty-about-AI rule is in the prompt only. Nothing enforces it. Separately, `sanitize_text` deletes phrases such as "sebagai AI" from replies (see 5, Medium). That can mangle an honest "aku AI" answer. |
| FR-2.5 | P2 | ❌ | No `/persona <name>` presets. |

### 2.3 Memory (§4.3)
| ID | Pri | Status | Notes |
|---|---|---|---|
| FR-3.1 | P0 | ⚠️ | Messages are stored, but only after a successful reply (see 5, High). |
| FR-3.2 | P0 | ✅ | Nightly extractor with Pydantic validation. |
| FR-3.3 | P0 | ✅ | Open threads with status and target date. |
| FR-3.4 | P0 | ✅ | Daily summary plus rolling summary (`summarizer.py`). |
| FR-3.5 | P0 | ✅ | `build_system_prompt_from_store` combines facts, threads, summary and recent messages. |
| FR-3.6 | P0 | ✅ | Nightly job at 23:30, tracked with a `last_extracted_message_id` pointer. |
| FR-3.7 | P1 | ✅ | `/memory`, `/forget <id>`, `/reset_memory confirm`. Confirmation is by argument. |
| FR-3.8 | P2 | ❌ | No embeddings (out of v1 scope). |
| FR-3.9 | P1 | ✅ | `supersede_fact` keeps the old fact, marked inactive. |

### 2.4 Proactive messaging (§4.4)
| ID | Pri | Status | Notes |
|---|---|---|---|
| FR-4.1 | P0 | ✅ | |
| FR-4.2 | P0 | ✅ | Cron at 00:05 and at startup (`_post_init`). |
| FR-4.3 | P0 | ✅ | Per-window chance and random time (`planner.py`). |
| FR-4.4 | P0 | ✅ | Quiet hours checked at planning time and at send time. |
| FR-4.5 | P0 | ✅ | Existing plan is reused, not re-rolled. `UNIQUE(date, window_name)`. |
| FR-4.6 | P0 | ✅ | `run_presend_checks` covers pause, quiet hours, recent activity, daily cap and ignored count. |
| FR-4.7 | P0 | ✅ | Prompt includes window, activity, thread, recent messages and elapsed time. |
| FR-4.8 | P0 | ✅ | Weighted types that exclude the last 2. A due thread forces `followup`. |
| FR-4.9 | P1 | ✅ | Bonus message. |
| FR-4.10 | P1 | ✅ | Backoff prompt after 2 ignored messages. Stops after 4. |
| FR-4.11 | P1 | ❌ | No weekend/weekday profiles. The planner uses one window set. |
| FR-4.12 | P2 | ❌ | No learned preferences. |

### 2.5 Her life (§4.5)
| ID | Pri | Status | Notes |
|---|---|---|---|
| FR-5.1, 5.2 | P0 | ✅ | `life/simulator.py`. Runs at startup and at 05:30. Injected into prompts. `/life` command added. |
| FR-5.3 | P1 | ✅ | Mood and energy are stored and injected. |
| FR-5.4, 5.5 | P1 | ⚠️ | Recent life notes feed the next day's generation. There is no dedicated "her life facts" store. |

### 2.6 Commands (§4.6)
| Command | Status | Notes |
|---|---|---|
| `/start` | ✅ | Stores the owner chat ID. Also helps first-run owner ID discovery. |
| `/pause`, `/resume`, `/status`, `/memory`, `/forget`, `/reset_memory`, `/reload_persona` | ✅ | |
| `/frequency` | ✅ | Affects planning only (see 5, Medium). |
| Natural-language pause | ✅ | `engine/intent.py`, rule-based rather than Gemini structured output. The PRD allows "lightweight rules". |
| Extras | ➕ | `/ping`, `/clear`, `/run_nightly`, `/test_proactive`, `/life`, `/backup`. |

### 2.7 Time awareness (§4.7)
| ID | Pri | Status |
|---|---|---|
| FR-6.1 | P0 | ✅ |
| FR-6.2 | P1 | ✅ (`format_time_elapsed_id`) |
| FR-6.3 | P2 | ❌ |
| FR-6.4 | P2 | ❌ |

### 2.8 Non-functional requirements (§5)
| ID | Status | Notes |
|---|---|---|
| NFR-1 Availability | ✅ | Dockerfile, docker-compose and a systemd unit (`deploy/companion.service`). |
| NFR-2 Latency | ✅ | 30 s timeout. Typing delay is capped at 6 s. |
| NFR-3 Durability | ✅ | WAL, daily backup at 03:00 with `keep_days`, integrity verification, `deploy/restore.py`, and tests. |
| NFR-4 Idempotency | ⚠️ | Plans are idempotent, but there is no missed-job handling (see 5, High). |
| NFR-5 Cost | ⚠️ | Chat and utility models are configurable but both are set to the same value. |
| NFR-6 Privacy | ✅ | Secrets are in `.env`, which is git-ignored. `data/*` is ignored. Restrictive file permissions on the DB are not set. |
| NFR-7 Observability | ✅ | Structured log lines, `llm_calls` table, and token stats in `/status`. |
| NFR-8 Rate limits | ⚠️ | Gemini retries with exponential backoff. Telegram sends have no retry. |
| NFR-9 Maintainability | ✅ | YAML config and prompts stored as files. |
| NFR-10 Timezone | ✅ | UTC storage, `Asia/Jakarta` for scheduling. |

### 2.9 Quality rules and wellbeing (§10, §11)
| Item | Status | Notes |
|---|---|---|
| Indonesian enforcement | ⚠️ | The detector only matches a handful of fixed English phrases. It is not real language detection. |
| Anti-repetition | ✅ | Word-overlap similarity plus opening-words check, with one regeneration (proactive only). |
| No assistant voice | ✅ | Phrase list, regenerate, then sanitize. |
| Question quota (30-40%) | ❌ | Not implemented. |
| Emotional attunement | ⚠️ | Prompt-level only. No mood detection. |
| No guilt-tripping | ⚠️ | Prompt-level only. The PRD asks for a regeneration check, which is missing. |
| **Distress / crisis handling (§11.1)** | ❌ | No crisis detection, no configurable resources list. This is the most significant wellbeing gap. |

### 2.10 Error handling (§13)
| Scenario | Status |
|---|---|
| Gemini retries and fallback text for chat | ✅ |
| Empty/blocked content never sent | ✅ (empty raises `GeminiError`) |
| Proactive skip on Gemini failure | ✅ (plan marked `failed`) |
| Telegram blocked-by-user handling | ❌ |
| Telegram send retry | ❌ |
| Missed job (under 30 min late) | ❌ |
| Non-text messages | ✅ |
| Invalid extraction JSON: retry once | ⚠️ Logged and skipped. The pointer is not advanced, so the next run retries. No immediate retry. |
| Duplicate `update_id` de-dup | ❌ (`drop_pending_updates=True` partly mitigates this) |

## 3. Code Quality Assessment

**Strengths**
- Clear module boundaries (gateway, engine, memory, proactive, life) that mirror the PRD.
- Pure functions are separated from I/O (`choose_message_type`, `is_too_similar`, `split_bubbles`). The planner and sender accept `now` and `rng` for deterministic tests.
- Good test coverage: 96 tests across 17 files, covering planner, checks, store, sender, intent and backup.
- Config validated at load time and a `--check` self-test command.
- Defensive logging and a global error handler.
- Backup and restore are complete.

**Weaknesses**
- Handlers use `from datetime import ...` and imports inside function bodies in a few places (minor style).
- `gemini_client.py` uses `Any` in a type hint without importing it. It works only because of `from __future__ import annotations`.
- `main.py`: `run_backup_job` annotates `Path` without importing it (same reason).
- The persona name "Nala" is hard-coded in `extractor._format_messages` and in `sanitize_text`. Other personas will break.
- `except (GeminiError, Exception)` is redundant in the extractor.
- Shared in-memory `_history` duplicates DB history.
- The model names in `config.yaml` could not be verified. Run `python -m app.main --check`.
- The extractor does not de-duplicate facts in code. It relies on the prompt.
- `pytest` shows only a third-party deprecation warning, no lint config.

## 4. Test Results

`pytest -q`: **95 passed, 1 failed**.

### 4.1 Failure analysis
`tests/test_store.py::test_get_today_llm_stats` hard-codes `today = "2026-10-07"`, but `log_llm_call` stamps rows with the real current time. The test only passes on 2026-10-07 and fails on any other day. **This is a test bug.** Fix: use the current UTC date in the test.

## 5. Issues Found (prioritized)

### High
1. **User message lost if Gemini fails.** In `ConversationEngine.reply`, the user message is saved only after a successful reply. On `GeminiError` it returns `FALLBACK_REPLY` early, so the user's text is never stored. This violates FR-3.1 and §8.1 step 2. It also means `last_user_msg_at` and `ignored_count` are not updated while Gemini is slow or down.
2. **`last_user_msg_at` is updated late.** It is updated only after the Gemini reply. A proactive job that fires during the generation window will not see recent user activity.
3. **No missed-job handling (§13).** `schedule_day_proactive_jobs` registers only future jobs. A plan whose time passed during downtime stays `planned` forever, and is never sent or marked `skipped (missed)`.
4. **No distress/crisis handling (§11.1).** No detection, no tone switch and no resource list.
5. **Telegram "bot blocked" is not handled.** A `Forbidden` error is just logged as a failed plan. The PRD wants all proactive jobs paused.

### Medium
6. **`daily_cap` ignores `/frequency`.** The planner uses the adjusted cap, but `run_presend_checks` compares against the configured `daily_cap`. With `high` (cap 5), the 5th message is skipped as `daily_cap_reached`.
7. **`sanitize_text` / `_clean_chunk` are too aggressive.** The speaker-prefix regex `^[A-Za-z0-9_-]+\s*:` strips the first word of any reply that is followed by a colon, for example "Note: ..." or "Jam 5: ...". The phrase removal also edits mid-sentence text and can break FR-2.4 honest-AI answers.
8. **Ignored-count semantics.** `ignored_count` is incremented on every proactive send and reset on any user message, including `non_text_message`. This is fine, but `/pause` and natural pause do not reset it.
9. **Weekend/weekday profiles missing (FR-4.11, P1).**
10. **Telegram retry/backoff missing (NFR-8).**
11. **Update de-dup (§13)** not implemented.
12. **Single model for chat and utility.** This does not follow NFR-5 intent.

### Low
13. FR-1.7, FR-1.8, FR-2.5, FR-3.8, FR-4.12, FR-6.3 and FR-6.4 (all P2) are not implemented. This is acceptable for v1.
14. Language detection is weak (fixed English phrase list).
15. Question-quota and guilt-trip regeneration checks are missing.
16. Hard-coded "Nala" in extractor and sanitizer.
17. Database file permissions are not restricted (NFR-6).
18. `README` and PRD default model names may drift. Verify with `--check`.
19. The `.env` file is present in the repo folder. It is git-ignored, so this is fine. Make sure it has never been committed (`git log -- .env`).

## 6. Recommended Next Steps
1. Save the user message first, before calling Gemini, and call `update_last_user_message_time` immediately (issues 1 and 2).
2. On startup, mark overdue `planned` rows `skipped (missed)`. Send those under 30 minutes late (issue 3).
3. Add a distress keyword check that switches to an empathetic prompt and appends the configured resource list (issue 4).
4. Catch `telegram.error.Forbidden` and set a long pause (issue 5).
5. Use the effective cap (with frequency) in `run_presend_checks` (issue 6).
6. Narrow the prefix regex to known speaker names and stop deleting phrases mid-sentence (issue 7).
7. Fix the date in `test_get_today_llm_stats`.
8. Run `python -m app.main --check` and do the manual evaluation in PRD §14.2.

## 7. Summary Table

| Area | Score |
|---|---|
| P0 requirements | 100% implemented; FR-2.4 and FR-3.1 have caveats |
| P1 requirements | ~85% (missing FR-4.11 weekend profiles) |
| P2 requirements | ~10% |
| Non-functional | ~85% |
| Safety / wellbeing | ~60% |
| Tests | 95/96 pass |
| Code quality | Good |
