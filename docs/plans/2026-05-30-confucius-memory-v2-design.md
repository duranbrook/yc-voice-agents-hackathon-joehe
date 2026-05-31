# Confucius — Memory v2 (Design)

**Date:** 2026-05-30
**Status:** Approved for implementation
**Author:** zhengyijoe.he
**Base:** v1 `learn-bot` on Pipecat Cloud (see `2026-05-30-confucius-learning-bot-design.md`) + Cekura observability (see `2026-05-30-cekura-observability-design.md`)

---

## 1. Overview & goals

A v2 of `learn-bot` that remembers individual learners across sessions. Two specific behaviors:

1. **Continuity** — When you reconnect after a clean exit, Confucius opens: *"Welcome back. Last time we covered superposition, the double-slit experiment, and entanglement. You wanted to come back to wavefunction collapse — pick up there, or something new?"*
2. **Pickup-where-you-left** — When you reconnect after a mid-topic disconnect, Confucius opens: *"Welcome back. We were just getting to the wavefunction collapse — want to keep going?"*

The bot's first turn changes based on persisted state, not just the process-local `SessionState` dict v1 uses.

### Stack additions over v1

- **Supabase** — Postgres for SessionState persistence + Google OAuth for identity
- **Custom client web page** — replaces the Pipecat sandbox URL as the entry point; handles Google Sign-In + Pipecat session-mint
- **Pipecat Cloud session-mint API** — POSTed from the client with `{ data: { user_id, ... } }` so the bot receives `user_id` in `runner_args.body`

### v2 success criteria

1. Same Google account, two separate sessions on different devices → second session opens with a memory-aware greeting
2. Disconnect mid-topic → reconnect within 24 hours → bot resumes that topic
3. `sessions` table accumulates one row per session; `learner_summary` table has exactly one row per Google-authenticated user
4. Existing v1 functionality (Cekura observability, 5-phase machine, all 5 tools) unchanged

### v2 explicit non-goals

- No cross-reference *during* a session ("remember when we discussed entropy?") — v3
- No personalization of depth/pace based on history — v3
- No multi-device handoff *during* a single session
- No native iOS app
- No memory-deletion UI for the user (GDPR-ish "forget me" is post-v2)
- No transcript search
- No server-side JWT verification (we trust the client's Google identity claim)

### Future v3+ hooks the v2 design preserves

- `sessions.payload` is jsonb — schema evolution is free
- `learner_summary` has slots for `depth_preference` and `concepts_lifetime` — cross-reference and personalization plug in here without new tables
- iOS app: same Pipecat Cloud agent + same Supabase + different client SDK; zero bot changes

---

## 2. Architecture

```
   👤 Learner
       │
       ▼  opens https://<our-domain>/ (custom client)
   ┌──────────────────────────────────────┐
   │ Custom Client (static page on Vercel)│
   │                                      │
   │   1. Google Sign-In (GIS library)    │
   │      → gets Google `sub` + id_token  │
   │                                      │
   │   2. POST to Pipecat Cloud's         │
   │      /v1/public/learn-bot/start      │
   │      body: { data: { user_id, email, │
   │                       name } }       │
   │      → returns room_url + token      │
   │                                      │
   │   3. daily.join(room_url, token)     │
   └──────────────────────────────────────┘
        │
        ▼  WebRTC
   ┌─────────────────┐
   │ Daily (managed) │
   └─────────────────┘
        │
        ▼
   ┌────────────────────────────────────────┐
   │ Pipecat Cloud — learn-bot              │
   │                                        │
   │ At session open:                       │
   │   ① load_memory(user_id)               │
   │   ② inject memory block into prompt    │
   │   → bot's first turn is memory-aware   │
   │                                        │
   │ At session close (end_session or       │
   │  on_client_disconnected):              │
   │   ③ persist_session(state)             │
   │     → INSERT sessions + UPSERT summary │
   └────────────────────────────────────────┘
        │
        ├─→ Supabase (Postgres)
        ├─→ OpenAI Responses (unchanged)
        ├─→ Gradium STT + TTS (unchanged)
        └─→ Cekura observability (unchanged)
```

### Repo layout (changes)

```
server/
├── bot-learn.py            # MODIFIED — read user_id from runner_args.body;
│                           #            inject memory-aware greeting block
├── learn_backend.py        # MODIFIED — add load_memory + persist_session
├── supabase_client.py      # NEW — thin wrapper over supabase-py
├── cekura_client.py        # unchanged
├── llm_context.md          # unchanged
├── Dockerfile              # MODIFIED — COPY supabase_client.py
├── pcc-deploy.toml         # unchanged
└── .env                    # MODIFIED — adds SUPABASE_URL, SUPABASE_SERVICE_KEY,
                            #            GOOGLE_CLIENT_ID

client/                     # NEW directory
├── index.html              # Single-page web app
├── app.js                  # Auth + session-mint + Daily join
└── README.md               # How to run locally + deploy to Vercel

docs/plans/
└── 2026-05-30-confucius-memory-v2-design.md   # this design
```

### Stack additions / unchanged

| Component | New for v2? |
|---|---|
| Supabase Postgres + Google OAuth | ✅ |
| Custom client (HTML + JS) | ✅ |
| Pipecat Cloud session-mint API | ✅ (newly used) |
| `supabase-py` SDK | ✅ |
| `daily-js` in browser | ✅ |
| Google Identity Services | ✅ |
| Pipecat 5-phase machine | unchanged |
| 5 tutor tools (`set_topic` etc.) | unchanged |
| Cekura wire format / send rules | unchanged |
| `llm_context.md` glossary | unchanged |
| OpenAI Responses + Gradium STT/TTS | unchanged |

### Networking

- **Bot → Supabase**: HTTPS to `<project>.supabase.co`, using `SUPABASE_SERVICE_KEY` (bypasses RLS).
- **Client → Supabase**: only for Google OAuth (`supabase.auth.signInWithOAuth({ provider: "google" })`).
- **Client → Pipecat Cloud**: HTTPS with the existing public API key (`pk_…`).
- **Client → Daily**: WebRTC, via room URL + token from Pipecat Cloud.

### What we explicitly DON'T add

| Considered | Decision |
|---|---|
| Server-side session-mint proxy (FastAPI) | ❌ — Pipecat Cloud's endpoint is directly callable from the browser |
| RLS policies on `sessions` / `learner_summary` | ❌ — only the bot writes; client never touches these tables |
| Auto-delete old rows | ❌ — manual cleanup for v2 |
| Per-tenant Supabase project | ❌ — single project, all users |
| Server-side JWT verification | ❌ — v2 trusts the client claim; v3 can add `supabase.auth.getUser(id_token)` |

---

## 3. Auth flow + user identity

1. Custom client loads Google Identity Services (`gsi-client.js`)
2. User clicks "Sign in with Google" → GIS returns an `id_token` (JWT)
3. Client decodes the JWT and extracts `sub` (Google user ID), `email`, `name`
4. Client POSTs to `https://api.pipecat.daily.co/v1/public/learn-bot/start`
   - Headers: `Authorization: Bearer <pk_…>`
   - Body: `{ createDailyRoom: true, data: { user_id: sub, email, name } }`
5. Pipecat Cloud responds with `{ room_url, token }`
6. Client calls `daily.join(room_url, token)` → live WebRTC session
7. Inside the bot, `runner_args.body.user_id` is available at `run_bot` entry

### v2 trust model (and limits)

We do NOT verify Google's JWT server-side. The bot trusts `body.user_id` as-claimed.

**Worst-case abuse**: a malicious user crafts a request with someone else's Google `sub` and reads/writes their learner_summary. **Mitigation in v2**: none. **Why acceptable**: the data isn't sensitive (topic history, time spent), the product is hackathon-scope, and exploiting this requires the attacker to already know the target's Google `sub` (not a publicly leaked field).

**v3 fix path**: client passes `id_token` instead of pre-decoded `sub`; bot calls `supabase.auth.getUser(id_token)` to verify before trusting. ~10-line change. Designed-for now, deferred.

---

## 4. Storage schema

```sql
-- Per-session detail
create table sessions (
    id            uuid primary key default gen_random_uuid(),
    user_id       text not null,
    started_at    timestamptz not null,
    ended_at      timestamptz,
    payload       jsonb not null,           -- full SessionState as JSON
    created_at    timestamptz default now()
);
create index idx_sessions_user_started on sessions (user_id, started_at desc);

-- Rolled-up per-user summary
create table learner_summary (
    user_id              text primary key,
    email                text,
    name                 text,
    last_session_at      timestamptz,
    last_phase_reached   text,    -- closing | teaching | recap | scoping | opening
    total_sessions       int not null default 0,
    total_minutes        int not null default 0,
    concepts_lifetime    jsonb not null default '[]'::jsonb,
    marked_for_later     jsonb not null default '[]'::jsonb,
    depth_preference     text,    -- v3 hook
    updated_at           timestamptz default now()
);
```

### `sessions.payload` shape

Same `SessionState` JSON we already build for Cekura — reuses the `build_payload` shape minus the Cekura-specific wrapper fields. Specifically:

```json
{
  "session_id": "...",
  "started_at": "ISO",
  "ended_at": "ISO",
  "topic": "WebRTC",
  "depth": "overview",
  "starting_level": "novice",
  "concepts_covered": [...],
  "marked_for_later": [...],
  "transcript": [...],
  "phase_reached": "recap",
  "end_reason": "user_goodbye"
}
```

### `learner_summary` upsert logic

After each session insert:
- `last_session_at` = the new session's `ended_at`
- `last_phase_reached` = the new session's `phase_reached`
- `total_sessions` += 1
- `total_minutes` += round((`ended_at` - `started_at`).total_seconds() / 60)
- `concepts_lifetime`: append any new concepts not already in the array (dedup by `concept` name)
- `marked_for_later`: replace with the latest session's `marked_for_later` (current items only; satisfied items drop off)
- `depth_preference`: unchanged for v2 (v3 will derive)

### Indexes

Just one: `(user_id, started_at desc)` on `sessions`. Supports the bot's "fetch most recent session for user X" query at ~10ms even with 100k+ rows.

---

## 5. Bot-side memory integration

### `learn_backend.py` additions

```python
async def load_memory(user_id: str) -> dict:
    """Return {summary, last_session, recency_bucket}.

    recency_bucket is one of:
      - "new"                  — no prior sessions
      - "clean_recent"         — last session ended cleanly, < 7 days
      - "clean_stale"          — last session ended cleanly, >= 7 days
      - "mid_topic_recent"     — last session disconnected mid-topic, < 24h
      - "mid_topic_stale"      — last session disconnected mid-topic, 24h-7d
    """
    ...


async def persist_session(state: SessionState) -> None:
    """INSERT one row into sessions; UPSERT learner_summary. Fire-and-forget
    from the caller's perspective (caller wraps in asyncio.create_task)."""
    ...
```

Both go through `supabase_client.py`.

### `bot-learn.py` modifications

At entry of `run_bot`:
```python
user_id = runner_args.body.get("user_id") if hasattr(runner_args, "body") else None
email = runner_args.body.get("email") if user_id else None
name = runner_args.body.get("name") if user_id else None

memory = await load_memory(user_id) if user_id else {"recency_bucket": "new"}
```

Then inject a structured memory block into `system_instruction`:

```python
if user_id:
    memory_block = build_memory_prompt_block(memory)
    system_instruction = system_instruction + "\n\n" + memory_block
```

### `build_memory_prompt_block(memory)` shape

Returns a markdown chunk like:

```markdown
## Memory context

You are talking to {name} (returning learner).

**Recency bucket: clean_recent**
Last session: 2026-05-29 at 14:32 UTC, topic was "WebRTC", lasted 7 minutes.
Phase reached: recap (clean exit).

**Concepts covered (lifetime):**
- WebRTC overview (first seen 2026-05-29)
- Daily transport (first seen 2026-05-29)

**Marked for later:**
- SDP details (because user wanted to focus on STUN/TURN first)

**Opening rule for this recency_bucket**: see §7 of the design — the bucket is `clean_recent`, so:
"Welcome back. Last time we covered {concepts_short}; you wanted to come back to {marked_for_later[0]}. Pick up there or something new?"

Use the structured data above (NOT prose paraphrase) to fill the placeholders. Speak the opening line naturally.
```

### Persistence call sites

`end_session` and `on_client_disconnected` already call `cekura_client.send_session(state)` as a fire-and-forget task. Add `persist_session(state)` in the exact same places:

```python
if not state.sent_to_cekura:
    state.sent_to_cekura = True
    asyncio.create_task(cekura_client.send_session(state))
    asyncio.create_task(persist_session(state))   # NEW
```

Two independent tasks, both fire-and-forget, both safe to fail (warning log, no retry).

### Optional new tool: `recall_past_session(query)`

```python
async def recall_past_session(params, query: str) -> None:
    """Search past sessions for a topic/concept. Use if the learner asks
    'didn't we cover X already?' or 'remind me what we said about Y'."""
```

Backed by a Supabase SQL query against `sessions` for this user_id filtered by `payload->>'topic' ILIKE %query%`. Returns matching session summaries. v2 ships this; the LLM is told it exists in the memory block.

---

## 6. Custom client

Single-file approach: `client/index.html` + `client/app.js`. Vanilla JS, no framework, no build step.

### `client/index.html` (sketch)

```html
<!DOCTYPE html>
<html>
<head>
  <title>Confucius — Voice Tutor</title>
  <script src="https://accounts.google.com/gsi/client" async defer></script>
  <script src="https://unpkg.com/@daily-co/daily-js"></script>
  <link rel="stylesheet" href="style.css">
</head>
<body>
  <main>
    <h1>Confucius</h1>
    <p class="tagline">An interactive voice tutor for everyone. Anywhere, anytime your hands or eyes are busy.</p>

    <section id="signin">
      <div id="g_id_onload"
           data-client_id="<YOUR_GOOGLE_CLIENT_ID>"
           data-callback="onGoogleSignIn"></div>
      <div class="g_id_signin" data-type="standard"></div>
    </section>

    <section id="ready" hidden>
      <p>Hello, <span id="user-name"></span>. Ready when you are.</p>
      <button id="start-btn">Start Learning</button>
    </section>

    <section id="call" hidden>
      <div id="daily-frame"></div>
      <button id="end-btn">End Session</button>
    </section>
  </main>
  <script src="app.js"></script>
</body>
</html>
```

### `client/app.js` (sketch)

```js
const PIPECAT_API_KEY = "pk_7678dd2b-dec7-4b68-966c-4d7509916ce7";
const PIPECAT_START_URL = "https://api.pipecat.daily.co/v1/public/learn-bot/start";
let dailyCall;
let userData = null;

window.onGoogleSignIn = (resp) => {
  const payload = JSON.parse(atob(resp.credential.split(".")[1]));
  userData = { user_id: payload.sub, email: payload.email, name: payload.name };
  document.getElementById("user-name").textContent = userData.name;
  document.getElementById("signin").hidden = true;
  document.getElementById("ready").hidden = false;
};

document.getElementById("start-btn").addEventListener("click", async () => {
  const r = await fetch(PIPECAT_START_URL, {
    method: "POST",
    headers: { "Authorization": `Bearer ${PIPECAT_API_KEY}`, "Content-Type": "application/json" },
    body: JSON.stringify({ createDailyRoom: true, data: userData })
  });
  const { room_url, token } = await r.json();
  dailyCall = DailyIframe.createFrame(document.getElementById("daily-frame"));
  await dailyCall.join({ url: room_url, token });
  document.getElementById("ready").hidden = true;
  document.getElementById("call").hidden = false;
});

document.getElementById("end-btn").addEventListener("click", async () => {
  if (dailyCall) await dailyCall.leave();
  location.reload();
});
```

### Deploy

`vercel --prod` from `client/`. ~30 seconds. Set the Google OAuth redirect URL to the Vercel domain in Google Cloud Console.

---

## 7. Recency rules → opening prompts

| Last session | Time since | Opening (LLM produces from rule + structured data) |
|---|---|---|
| No prior sessions | — | *"Hi, I'm Confucius. What do you want to learn about today?"* (v1 default) |
| Clean exit (`recap`/`closing`) | < 7 days | *"Welcome back. Last time we covered {3 concepts}; you wanted to come back to {marked_for_later[0]}. Pick up there or something new?"* |
| Mid-topic disconnect | < 24 hours | *"Welcome back. We were just getting to {open_concept} — want to keep going?"* |
| Mid-topic disconnect | 24h – 7 days | *"Welcome back. We were on {topic} last time but didn't quite finish {open_concept}. Resume or new topic?"* |
| Stale (> 7 days) | — | *"Welcome back — it's been a minute. Want to revisit {last_topic} or pick something new?"* |

These rules are encoded in the memory block prepended to `system_instruction`, NOT in Python branching. The LLM reads structured memory data + the rule set and produces the opening line.

### Why rules in the prompt, not in code

The LLM is good at natural variation; Python would make every greeting identical. The prompt approach gives ~5 fixed "shapes" but each spoken sentence is freshly composed.

---

## 8. Testing & validation

### Layer 1 — local with mocked Supabase

```bash
cd server
uv run python -c "
import asyncio
from unittest.mock import AsyncMock, patch
from datetime import datetime, timezone, timedelta
from learn_backend import build_memory_prompt_block

memory = {
    'recency_bucket': 'clean_recent',
    'summary': {'name': 'Joe', 'last_session_at': datetime.now(timezone.utc), 'concepts_lifetime': [...], 'marked_for_later': [...]},
    'last_session': {...}
}
block = build_memory_prompt_block(memory)
assert 'Welcome back' not in block  # the LLM produces the welcome, prompt just gives the rule
assert 'clean_recent' in block
assert 'Joe' in block
print('OK: memory block constructed correctly')
"
```

Same shape of mocked test for each of the 5 recency buckets.

### Layer 2 — deployed end-to-end smoke

1. Open `https://<vercel-domain>/` on iPhone Safari
2. Sign in with Google
3. Click Start Learning → Daily joins → 1-minute session on topic A → say "let's wrap up" → "goodbye"
4. Reload page → sign in again → Start Learning → **verify bot opens with continuity greeting referencing topic A**
5. Click Start Learning a third time, this time disconnect mid-topic without saying goodbye
6. Reload page → sign in → Start Learning → **verify bot opens with "we were just getting to X — keep going?"**

### Layer 3 — Supabase verification

```sql
-- After 3 sessions on the same Google account:
select user_id, total_sessions, total_minutes, last_phase_reached, jsonb_array_length(concepts_lifetime) as n_concepts
from learner_summary
where user_id = '<your_google_sub>';
-- Expect: total_sessions=3, n_concepts > 0, last_phase_reached varies by session

select id, started_at, ended_at, payload->>'topic' as topic, payload->>'phase_reached' as phase
from sessions
where user_id = '<your_google_sub>'
order by started_at desc;
-- Expect: 3 rows, most recent first
```

### What we explicitly DON'T test for v2

| Test type | Why deferred |
|---|---|
| Unit tests on `supabase_client.py` | Trivial CRUD wrapper |
| Load testing | Out of scope; supabase free tier handles plenty |
| Concurrent same-user sessions on two devices | Edge case; v2 doesn't promise concurrent handling |
| JWT replay / spoofing tests | Trust model is documented (§3); v3 issue |

---

## 9. Implementation phasing

Ballpark **6–8 hours**.

| # | Step | Time | Notes |
|---|---|---|---|
| 1 | Create Supabase project; enable Google OAuth provider in Auth settings | 15 min | Note the project URL + service_role key |
| 2 | Run schema SQL (sessions + learner_summary) | 5 min | Via Supabase SQL editor or `psql` |
| 3 | Set up Google Cloud OAuth client → get `GOOGLE_CLIENT_ID` + add Vercel domain to authorized origins | 20 min | One-time Google Cloud Console setup |
| 4 | Add `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `GOOGLE_CLIENT_ID` to `.env` | 5 min | Python-helper write |
| 5 | Add `supabase` to `pipecat-ai` extras OR add `supabase>=2.0` as a direct dep | 5 min | `pyproject.toml` + `uv lock` |
| 6 | Create `server/supabase_client.py` (~80 lines): `init_client()`, `fetch_summary(user_id)`, `fetch_last_session(user_id)`, `persist_session(state)`, `upsert_summary(state)` | 1 hr | |
| 7 | `learn_backend.py` — add `load_memory()` (composes recency_bucket from fetched data) + `build_memory_prompt_block()` + wire `persist_session` into the existing fire-and-forget path | 1 hr | Mirrors how cekura_client is wired |
| 8 | `bot-learn.py` — read `user_id`/`email`/`name` from `runner_args.body`; load memory; inject block; pass user_id through to where session ends | 30 min | |
| 9 | Add optional `recall_past_session` tool to `make_tools` | 30 min | Skip if time-constrained |
| 10 | Update `Dockerfile` to COPY `supabase_client.py` | 5 min | |
| 11 | Create `client/index.html` + `client/app.js` + `client/style.css` + `client/README.md` | 1.5 hr | The sketches in §6 are starting points |
| 12 | Deploy client to Vercel; configure Google OAuth callback to include Vercel URL | 30 min | |
| 13 | Layer 1 smoke (mocked Supabase) | 30 min | |
| 14 | Re-upload secrets to Pipecat Cloud | 1 min | |
| 15 | Redeploy bot | 3 min | |
| 16 | Layer 2 end-to-end smoke — 3 sessions covering recency buckets new/clean_recent/mid_topic_recent | 1 hr | |
| 17 | Layer 3 SQL verification in Supabase | 15 min | |

### Definition of done

- Custom client deployed to Vercel; Google Sign-In works
- Same Google account → 2 sessions → second session has a continuity greeting referencing concepts from the first
- Mid-topic disconnect → reconnect within 24h → pickup greeting works
- Supabase `sessions` has one row per session; `learner_summary` has one row per user
- Existing v1 behavior (Cekura POST, 5-phase machine) unchanged
- No `[memory]` errors in deployed cloud logs

### Out of scope (do NOT do)

| Item | Status |
|---|---|
| Cross-reference during a session | v3 |
| Personalization (depth/pace adaptation) | v3 |
| Native iOS app | v3 |
| User-facing memory-deletion UI | post-v2 |
| Server-side JWT verification | v3 (trust client claim for v2) |
| Multi-device concurrent sessions | edge case; v3 |
| Transcript full-text search | v3 |
| RLS policies on bot-only tables | v3 if client gets a history view |

---

## Appendix A — `.env` additions

```
# Supabase
SUPABASE_URL=https://<your-project>.supabase.co
SUPABASE_SERVICE_KEY=<service_role_key_from_supabase_settings>

# Google OAuth (client-only; safe to expose but env-managed for portability)
GOOGLE_CLIENT_ID=<from_google_cloud_console>.apps.googleusercontent.com
```

## Appendix B — File reference after v2

```
server/
├── bot-gpt.py            # unchanged
├── bot-nemotron.py       # unchanged
├── bot-learn.py          # MODIFIED — user_id flow + memory injection
├── learn_backend.py      # MODIFIED — load_memory + persist_session + recall_past_session
├── supabase_client.py    # NEW
├── cekura_client.py      # unchanged
├── llm_context.md        # unchanged
├── Dockerfile            # MODIFIED — COPY supabase_client.py
└── .env                  # MODIFIED — adds 3 keys

client/                   # NEW
├── index.html
├── app.js
├── style.css
└── README.md

docs/plans/
└── 2026-05-30-confucius-memory-v2-design.md   # this design
```
