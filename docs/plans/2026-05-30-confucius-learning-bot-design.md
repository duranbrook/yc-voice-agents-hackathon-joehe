# Confucius — Interactive Voice Learning Bot (Design)

**Date:** 2026-05-30
**Status:** Approved for implementation
**Author:** zhengyijoe.he
**Base template:** `bot-gpt.py` (Field & Flower flower-bot) in this repo

---

## 1. Overview & goals

**What we're building**

A voice-first learning bot you talk to on your commute, using earbuds and a phone browser. The bot is a structured tutor that:

1. Greets you and asks what you want to learn today
2. Briefly scopes the session (depth, starting knowledge level)
3. Teaches reactively — you ask, it explains pedagogically
4. Wraps with a recap before disconnect

Tech stack is identical to the deployed `flower-bot`: Pipecat orchestrator + Daily transport + OpenAI GPT-4.1 (LLM) + Gradium (STT + TTS), running on Pipecat Cloud. The only client is the **Pipecat Cloud sandbox URL** opened on a phone browser.

**v1 success criteria**

1. User can walk around with earbuds, open one URL, and have a 5–10 minute structured learning conversation about any topic
2. The bot feels like a tutor, not generic ChatGPT — it scopes the topic, checks understanding, recaps at the end
3. Each session produces a structured log (topic, concepts covered, things marked for follow-up) — v1 doesn't read it back, but v2 (memory) will

**v1 explicit non-goals**

- No persistent memory between sessions (logs are in-memory only; lost on disconnect)
- No 3rd-party content platform integration (Khan Academy, Coursera, etc.)
- No native iOS app — phone browser is enough
- No multi-user accounts / login
- No "save my progress" UI
- No automated test harness (Cekura integration is a separate v1.5 workstream)

**Future iteration hooks designed-for (not built)**

- Memory: `SessionState` shape persists to a KV store keyed by `user_id`
- 3rd-party platforms: `fetch_lesson(source, topic)` tool slot
- iOS app: same bot, different client SDK (`pipecat-client-ios`)
- Cekura integration: structured session data is the assertion target

---

## 2. Architecture

**Stack reuse — no new layers, no new vendors.**

```
   📱 iPhone Safari (Pipecat sandbox UI)
            ↕  WebRTC (Opus / UDP)
   📞 Daily (managed transport)
            ↕  Audio frames via Pipecat runner
   ☁️ Pipecat Cloud — Python process running bot.py
       │
       ├─→ 🎧 Gradium STT     (speech → text)
       ├─→ 🧠 OpenAI Responses (GPT-4.1)
       └─→ 🗣️ Gradium TTS     (text → speech)
```

**Repo layout**

New sibling files. The existing flower-bot is **not modified** — both deployments coexist on the same Pipecat Cloud org.

```
server/
├── bot-gpt.py          # existing flower-bot (untouched)
├── bot-nemotron.py     # existing (untouched)
├── mock_backend.py     # existing (untouched)
├── bot-learn.py        # NEW — fork of bot-gpt.py
├── learn_backend.py    # NEW — in-memory SessionState + tool helpers
├── Dockerfile          # MODIFIED — COPY bot-learn.py as bot.py
├── pcc-deploy.toml     # MODIFIED — agent_name "learn-bot", secret set "learn-bot-secrets"
└── .env                # MODIFIED — same keys, possibly different GRADIUM_VOICE_ID later
```

**What changes in `bot-learn.py` vs `bot-gpt.py`**

| Block | Change |
|---|---|
| System prompt | Full rewrite — tutor persona + 5-phase state machine (Section 5) |
| Tools (`tool_functions` list) | Drop 7 flower tools; add 5 tutor tools (Section 4) |
| Mock backend import | Replace `from mock_backend import …` with imports from `learn_backend` |
| Caller context (`KNOWN_CUSTOMERS`) | Leave untouched — dormant in WebRTC path; becomes template for "returning learner" in v2 memory |
| Pipeline assembly | Unchanged — same `transport → STT → user_aggregator → LLM → TTS → output` |
| Transport switch (Daily / SmallWebRTC / WebSocket cases) | Unchanged |
| Voice ID | Reuse `YTpq7expH9539ERJ` (same as flower-bot for v1) |

**Deployment**

```bash
pc cloud secrets set learn-bot-secrets --file .env --skip
pc cloud deploy --yes
```

Result: a second agent `learn-bot` next to `flower-bot`. Sandbox URL:

```
https://pipecat.daily.co/0530id/agents/learn-bot/sandbox
```

**Explicit non-additions**

- No database (in-memory only — non-goal)
- No new external services (no RAG, no vector store, no curriculum fetch)
- No new transports (no Twilio for v1)
- No frontend code (sandbox UI is the client)
- No CI/CD changes

---

## 3. Session phases (5-phase state machine)

**Design choice:** the state machine lives in the prompt, not in code. The LLM tracks phase via conversation history + tool calls. No Python state machine library.

```
   [1. Opening]  →  [2. Scoping]  →  [3. Teaching]  →  [4. Recap]  →  [5. Closing]
       │              │               │ ↺ (loop)        │              │
       │              │               │                 │              ▼
       │              │               │                 │           end_session()
   greeting,      ask depth +     reactive Q&A      summarize
   "what do       starting       "explain" /      concepts
    you want      knowledge      "go deeper"      covered +
    to learn?"    level          (most time)      next time
```

### Phase 1 — Opening (~5 sec target)

| | |
|---|---|
| Bot goal | Greet + invite a topic |
| Bot says | "Hi, I'm Confucius. What do you want to learn about today?" |
| Tools | none |
| Transition out | User names a topic |
| Edge cases | User says "I don't know" → bot suggests 2–3 prompts |

### Phase 2 — Scoping (~15 sec target)

| | |
|---|---|
| Bot goal | Calibrate depth + starting knowledge with ≤2 questions, then call `set_topic` |
| Bot says | "Quantum mechanics, cool. Done any physics, or starting fresh? And do you want the big-picture, or the 'how it actually works' version?" |
| Tools | `set_topic(topic, depth, starting_level)` |
| Transition out | `set_topic` called → Teaching |
| Edge cases | User wants to dive straight in → bot calls `set_topic` with `depth="unknown"` and moves on |

### Phase 3 — Teaching (open-ended; bulk of session)

| | |
|---|---|
| Bot goal | Answer questions pedagogically; track concepts covered |
| Bot says | Concise explanations (1–3 sentences), analogies, ends each turn with "does that land?" or "want to go deeper or move on?" |
| Tools | `add_concept_covered(concept, brief)` after every substantive explanation; `mark_for_later(item, reason)` when something deserves follow-up |
| Transition out | User says "wrap up" / "gotta go" / "let's stop" → Recap. Also: at ~7 min, bot proactively offers recap |
| Edge cases | Off-topic → gentle anchor; uncertain → honest, no confabulation |

### Phase 4 — Recap (~30 sec target)

| | |
|---|---|
| Bot goal | Spoken summary of what was covered + what was marked for later |
| Bot says | "OK quick recap. We covered superposition, the double-slit experiment, and why measurement collapses the wavefunction. You wanted to come back to entanglement next time. Sound right?" |
| Tools | `recap_session()` — returns structured `SessionState` for the LLM to verbalize |
| Transition out | User confirms → Closing |
| Edge cases | Want to keep going → back to Teaching; want to correct → update + re-recap |

### Phase 5 — Closing (~5 sec target)

| | |
|---|---|
| Bot goal | Short goodbye + clean disconnect |
| Bot says | "Cool. Have a good one." |
| Tools | `end_session()` |
| Transition out | Call ends |

### Why this shape matters

Generic voice ChatGPT skips phases 1, 2, and 4. The **bookends are the differentiator**, not the middle.

---

## 4. Tools

5 tools, all in `learn_backend.py`. Same `async`/`FunctionCallParams` shape as flower-bot tools.

### Data model

```python
# learn_backend.py
@dataclass
class ConceptCovered:
    concept: str
    brief: str

@dataclass
class MarkedForLater:
    item: str
    reason: str

@dataclass
class SessionState:
    session_id: str
    started_at: datetime
    topic: str | None = None
    depth: str | None = None              # "overview" | "deep" | "unknown"
    starting_level: str | None = None     # "novice" | "some_background" | "expert" | "unknown"
    concepts_covered: list[ConceptCovered] = field(default_factory=list)
    marked_for_later: list[MarkedForLater] = field(default_factory=list)

# In-memory storage keyed by session_id; lost on process recycle
_SESSIONS: dict[str, SessionState] = {}
```

### Tool table

| # | Tool | Phase | What it does | Returns to LLM |
|---|---|---|---|---|
| 1 | `set_topic(topic, depth, starting_level)` | 2 → 3 | Records topic + depth + level on `SessionState` | `"Topic set: {topic} ({depth}, {starting_level}). Begin teaching."` |
| 2 | `add_concept_covered(concept, brief)` | 3 (every explanation) | Appends a `ConceptCovered` row | `"Logged."` (silent) |
| 3 | `mark_for_later(item, reason)` | 3 | Appends a `MarkedForLater` row | `"Noted for next session."` (silent) |
| 4 | `recap_session()` | 3 → 4 | Returns full structured `SessionState` so LLM can compose the spoken recap | `{topic, depth, starting_level, concepts_covered: [...], marked_for_later: [...], duration_minutes}` |
| 5 | `end_session()` | 5 | Pushes `EndTaskFrame` upstream (same pattern as flower-bot's `end_call`) | `"Ending session."` |

### Tool decision rules (in prompt)

- After every explanation that introduces a real concept → call `add_concept_covered`. Not for chitchat, transitions, or clarifications.
- Call `mark_for_later` when user says "remind me to come back to that" OR explicitly expresses interest in a tangent being skipped.
- Call `recap_session` ONCE before saying goodbye. Never twice in one session.
- Call `end_session` ONLY after delivering a goodbye line.
- If user changes topics mid-session: call `recap_session` for old topic, then `set_topic` again for new one.

### Graceful failure (no code-level state machine)

Tools always succeed; if called out of phase they return mostly-empty data. The LLM has to react in prose ("I don't think we got to anything concrete yet — want to try again?"). Acceptable for v1.

### Future-memory hooks

| v1 today | v2 with memory |
|---|---|
| `SessionState` in process-local dict; lost on disconnect | Same `SessionState`, serialized to KV store keyed by `user_id` |
| `set_topic` ignores prior sessions | `set_topic` queries past sessions: "you covered X about Y — want to build on that?" |
| `mark_for_later` only used in current session's recap | v2 surfaces it at the start of new sessions |
| No user ID | Sandbox adds auth; tools receive `user_id` |

**Tool shapes don't change between v1 and v2 — only storage backend.**

### Tools explicitly NOT in v1

| Tool | Why excluded |
|---|---|
| `suggest_next_question()` | LLM can suggest in prose; tool adds no value |
| `web_search(query)` | Out of scope — open-topic LLM-knowledge model |
| `get_session_state()` | Conversation history IS the LLM's state |
| `pause_session()` / `resume_session()` | Needs memory (v2) |
| `quiz_me_on(concept)` | Different interaction mode; v3+ |

---

## 5. System prompt structure

8 blocks. Approximate total: ~120 lines. Most blocks short.

| Block | Approx lines | Content |
|---|---|---|
| 1. Persona | ~4 | "You are Confucius, a voice tutor for someone learning on their commute. You sound like a wise teacher who's good at explaining things, not a fortune cookie." |
| 2. Spoken-style rules | ~12 | 1–3 short sentences. Analogies. No markdown/bullets. Numbers in words. Contractions. End teaching turns with a check. No filler openers. |
| 3. 5-phase session structure | ~30 | Condensed from Section 3 — phase, goal, what-you-say, transition trigger. "Track your own phase. There's no machine forcing it." |
| 4. Tool decision rules | ~7 | Verbatim from Section 4. |
| 5. Time budget | ~3 | Default 8 minutes. Around 7-min mark, proactively offer recap. Not rigid — extend if user wants. |
| 6. Edge cases | ~6 | Off-topic anchor; topic switch (recap → set_topic); honest uncertainty; silence → suggest 2 directions; procedural questions don't log. |
| 7. Turn-completion markers | ~60 (reused) | The `✓` / `○` / `◐` framework, copied verbatim from `bot-gpt.py`. |
| 8. Today's date | ~1 | "Today is Saturday, May 30, 2026." Refreshed at deploy time. |

### Persona safeguard (explicit)

The Confucius name carries a "Confucius says…" trope risk. The persona block **explicitly tells the LLM**: be a wise tutor, not a fortune cookie. No aphorisms, no stilted "ancient wisdom" affectations. Modern English, plain.

### What we DON'T put in the prompt

- Long example explanations — bloats prompt; trust pretraining + style rules
- Per-subject behavior overrides — premature
- User-name personalization — no user identity in v1
- Failure-mode scripts — wrong layer; Pipecat handles transport errors

---

## 6. Bot persona

| Property | Value |
|---|---|
| **Name** | Confucius |
| **Voice ID (Gradium)** | `YTpq7expH9539ERJ` (same as flower-bot for now) |
| **Greeting line** | "Hi, I'm Confucius. What do you want to learn about today?" |
| **Visual identity** | None — uses Pipecat Cloud sandbox UI as-is |

### What we explicitly DON'T do for persona

| Considered | Decision |
|---|---|
| Custom avatar / logo | ❌ — sandbox doesn't support it |
| Multiple personas (stern professor vs patient friend mode) | ❌ — premature |
| Voice cloning / pick-your-tutor-voice | ❌ — needs UI |
| Background music / SFX | ❌ — adds latency, distracts |
| Stylistic "Confucius says…" aphorisms | ❌ — explicit persona safeguard |

### Voice override path (no code change)

If the current voice doesn't feel right after testing, change `GRADIUM_VOICE_ID` in `.env`, re-upload secrets, redeploy. No code change. To find a different voice: browse `dashboard.gradium.ai` and copy a voice ID.

---

## 7. Testing & validation

Two layers. Cekura deferred to v1.5 (Section 9).

### Layer 1 — Local smoke test (before every deploy)

Run `uv run bot-learn.py` → open `localhost:7860` → walk through one full session out loud.

| Check | Pass criterion |
|---|---|
| Opening greeting fires within ~2s | Bot says "Hi, I'm Confucius…" |
| Scoping happens (≤2 calibration questions) | Bot asks about depth or level before teaching |
| `set_topic` tool fires once | Visible in server log |
| Teaching produces `add_concept_covered` calls | At least one tool call per substantive explanation |
| Bot self-anchors when you go off-topic | Try a random unrelated thing — bot pulls back |
| Recap is structured and recognizable | "We covered X, Y, Z. You wanted to come back to W." |
| `end_session` actually closes the call | Connection drops cleanly |

If any fail, fix locally first. Don't redeploy until they all pass.

### Layer 2 — Deployed sandbox smoke test (after deploy)

Open `https://pipecat.daily.co/0530id/agents/learn-bot/sandbox` on iPhone in Safari w/ earbuds, walk around the block, full 8-minute session.

| Check | Pass criterion |
|---|---|
| Sandbox UI usable on iOS Safari | Buttons tappable, mic permission grants OK |
| Audio in earbuds (AirPods or wired), both directions | No echo |
| Background noise doesn't break VAD | Krisp filter (already in deploy) handles street noise |
| Latency feels natural | First TTS audio within ~1s of user finishing |
| Full 5-phase flow completes | Same checklist as Layer 1, in the wild |
| No mid-session disconnects | Walk under bridges, into stores — connection survives |

### Observability during v1

`pc cloud agent logs learn-bot --follow` while testing. Pipecat Cloud's built-in logs show LLM input/output and tool calls. No custom metrics, no dashboards.

### Intentionally skipped for v1

| Test type | Why deferred |
|---|---|
| Unit tests on `learn_backend.py` | ~50 lines of dataclass mutation; would test Python itself |
| Multi-user concurrency | Pipecat Cloud handles isolation per instance |
| LLM correctness eval | No ground truth for open-topic model |
| Load testing | Trust Pipecat Cloud autoscaler |
| Cross-browser testing | iOS Safari is the only v1 target |
| Cekura scenarios | Separate workstream — v1.5 |

---

## 8. Implementation phasing

Ballpark **6–10 hours total**.

| # | Step | Time | Note |
|---|---|---|---|
| 1 | Copy `bot-gpt.py` → `bot-learn.py` | 5 min | Start from working baseline |
| 2 | Create `learn_backend.py` with `SessionState` + 5 tool stubs (placeholder returns) | 30 min | Get signatures right before prompt work |
| 3 | Swap tool list in `bot-learn.py` (drop 7 flower, add 5 learn) | 15 min | Mechanical |
| 4 | Rewrite system prompt (8 blocks from Section 5) | 1–2 hrs | Hardest step — iterate via local talking |
| 5 | Layer 1 smoke test | 30 min | Verify before deploying |
| 6 | Update `Dockerfile` (`COPY ./bot-learn.py bot.py`) + `pcc-deploy.toml` (agent_name=`learn-bot`, secret_set=`learn-bot-secrets`) | 5 min | Mechanical |
| 7 | `pc cloud secrets set learn-bot-secrets --file .env --skip` | 1 min | Reuses existing keys |
| 8 | `pc cloud deploy --yes` | ~3 min | First deploy; watch logs |
| 9 | Layer 2 smoke test on phone | 30 min | The proof |
| 10 | Prompt iteration loop (Step 4 → redeploy) | 1–3 hrs | Expected, not optional |

Steps 1–3 mechanical. Step 4 and 10 are 70% of the time. Steps 5–9 deterministic.

### Definition of done for v1

User can open sandbox URL on phone, have an 8-minute conversation about a topic of their choice with proper opening → scoping → teaching → recap → closing, and the structured session log reflects what was covered. **No memory across sessions; no other learners; no fact checks.**

---

## 9. Future iteration ladder

The v1 scaffolding preserves these without rewrites.

### v1.5 — Cekura integration (test harness + observability)

**Goal:** turn the in-session structured data into a regression suite that protects every future change.

**Scope:**

- Wire up Cekura's Pipecat integration against the deployed `learn-bot`
- Define ~5 scenarios that encode the bot's behavioral contract:

| # | Scenario | Assertion |
|---|---|---|
| 1 | Happy path | All 5 phases hit; tools fire in order; recap mentions what was actually covered |
| 2 | Mid-session topic switch | `recap_session` fires before second `set_topic`; bot doesn't merge topics |
| 3 | Persona + style audit | LLM-as-judge: stays Confucius-tone (wise, not aphoristic); responses < 3 sentences avg; no bullets/markdown |
| 4 | Time-budget close | At ~7-min mark, bot proactively offers recap; closes cleanly under 8.5 min total |
| 5 | Honest uncertainty | LLM-as-judge: when asked a niche factual question, bot says "not sure" — does not confabulate |

**What Cekura captures (relevant to this bot):**

- Full conversation transcripts
- Tool call trace (which fired, with what args, in what order)
- Per-turn latency (STT lag, LLM response, TTS time-to-first-audio)
- STT/LLM/TTS error rates
- Pass/fail per scenario based on assertions
- LLM-as-judge subjective scores (persona, style, factual honesty)
- Simulated user personas (cooperative, distracted, contrarian, novice, expert)

**Why this is v1.5 not v2:** it's purely additive — no bot code changes, no schema changes. The structured data v1 already produces *is* the assertion target. Run it once v1 is real, before any further iteration.

### v2 — Memory + 3rd-party content + iOS

| Iteration | Hook in v1 design |
|---|---|
| **Persistent memory** | `SessionState` persists to KV store keyed by `user_id`; `set_topic` and `mark_for_later` get cross-session read-back. **No tool signature changes — only storage backend swap.** |
| **3rd-party content** | Add `fetch_lesson(source, topic)` tool; prepend retrieved text as system context. New tool, no change to existing 5. |
| **Native iOS app** | `pipecat-client-ios` + Daily SDK; same deployed agent. No bot-side changes. |

### v3 — Pedagogy expansion

- Quiz mode / comprehension checks (new tools + new prompt phase)
- Adaptive difficulty (requires memory + user model from v2)
- Multi-tutor personalities (prompt templates + voice swaps)

---

## Appendix A — Deployment configuration

### `pcc-deploy.toml` (modified)

```toml
agent_name = "learn-bot"
secret_set = "learn-bot-secrets"
agent_profile = "agent-1x"

[krisp_viva]
	audio_filter = "tel"

[scaling]
	min_agents = 1
```

### `Dockerfile` (modified)

```dockerfile
FROM dailyco/pipecat-base:latest
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --locked --no-install-project --no-dev
COPY ./bot-learn.py bot.py
COPY ./learn_backend.py learn_backend.py
```

### `.env` (modified — only voice ID may change)

```
GRADIUM_API_KEY=<existing>
GRADIUM_VOICE_ID=YTpq7expH9539ERJ
OPENAI_API_KEY=<existing>
```

---

## Appendix B — Cost note

`min_agents=1` keeps one agent instance reserved continuously, billed per Pipecat Cloud pricing. To scale to zero between testing sessions:

```bash
pc cloud agent scale learn-bot --min 0
```

Or fully remove:

```bash
pc cloud agent delete learn-bot
```
