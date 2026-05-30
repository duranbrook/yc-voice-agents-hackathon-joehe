# Cekura Observability for learn-bot (Design)

**Date:** 2026-05-30
**Status:** Approved for implementation
**Author:** zhengyijoe.he
**Base:** the deployed `learn-bot` (Confucius) on Pipecat Cloud (see `2026-05-30-confucius-learning-bot-design.md`)

---

## 1. Overview & goals

**What we're building**

A Cekura observability integration for `learn-bot`. Every completed session POSTs to Cekura's `/observability/v1/observe/` endpoint with:

1. The full conversation transcript (so Cekura's per-call view works)
2. A structured "learning timeline" — topic + concepts with timestamps from the bot's tool calls

After deployment, we define 2–3 natural-language metrics in Cekura's dashboard so each call gets tagged with `topic`, `recap_delivered`, and `concept_count`. Trending = filtering/sorting the resulting calls list.

**v1 success criteria**

1. After every completed learn-bot session, a record appears in Cekura's calls list within ~30 s
2. Each record shows topic + per-concept timing from our structured data (not LLM-extracted)
3. Cekura metrics tag each call with topic + recap-delivered + concept-count so the list view can be filtered/sorted for trending
4. No crashes or hangs in the bot if Cekura is slow or down (fire-and-forget)

**v1 explicit non-goals**

- No real-time / streaming metrics — batch at session end
- No local charts dashboard (Cekura's list view is the dashboard)
- No multi-user identity (placeholder `user_id="default_user"`)
- No retries / local queue / backoff
- No alerts ("user finished in <2 min", etc.) — observation only
- No backfill of historical learn-bot sessions
- No SDK / wrapper — direct HTTP POST

**Future iteration hooks**

- v1.5 — `asyncio.shield()` or short `wait_for` to reduce dropped sends if teardown races become a problem
- v1.5 — local SQLite fallback for failed POSTs + replay job
- v2 — Real `user_id` from sandbox query param / cookie → trending across actual users
- v2 — Cekura alerts on metrics (session <2 min = abandonment signal, etc.)
- v2 — Local chart dashboard (Option C from brainstorming) if Cekura's list view becomes limiting

---

## 2. Architecture

**Where the integration lives:** entirely on the bot side. No Cekura-side configuration beyond an API key + metric definitions in the dashboard.

### Data flow

```
   Session opens                    Session is live                   Session ends
   ─────────────                    ───────────────                   ────────────
   on_client_connected              add_concept_covered tool          end_session tool
   → SessionState created           → ConceptCovered { name, brief,   → asyncio.create_task(
   → started_at = now()                started_at = now(),                cekura_client.send_session(state))
                                       ended_at = (filled by next     → push EndTaskFrame
                                       concept or end)                ↓ (parallel)
                                     }                                ┌─────────────────────────────────┐
                                                                      │  POST https://api.cekura.ai/    │
                                                                      │  observability/v1/observe/      │
                                                                      │  Headers:                       │
                                                                      │    X-CEKURA-API-KEY: <key>      │
                                                                      │  Body: see Section 3            │
                                                                      └─────────────────────────────────┘
```

### Repo layout (changes)

```
server/
├── bot-learn.py          # MODIFIED — hook session-end + on_client_disconnected
├── learn_backend.py      # MODIFIED — extended SessionState + per-concept timestamps + idempotency latch
├── cekura_client.py      # NEW — HTTP client + payload builder + auth header
├── .env                  # MODIFIED — add CEKURA_API_KEY
└── pcc-deploy.toml       # UNCHANGED
```

### What changes vs. what stays

| Component | Change |
|---|---|
| `SessionState` | Add `user_id`, `ended_at`, `transcript`, `phase_reached`, `end_reason`, `sent_to_cekura` |
| `ConceptCovered` | Add `started_at`, `ended_at` |
| Tool implementations | `set_topic` → `phase_reached="teaching"`; `add_concept_covered` → timestamp + close prior concept; `recap_session` → `phase_reached="recap"` + close current concept; `end_session` → trigger Cekura send |
| Transcript capture | New: hook user/assistant aggregator events; append `TranscriptTurn` to `SessionState.transcript` |
| `bot-learn.py` event handlers | `on_client_disconnected` triggers fallback Cekura send |
| `cekura_client.py` | New file: payload builder, auth header, `aiohttp` POST with 5 s timeout, warning-on-failure |

### Networking

- POST from Pipecat Cloud worker → `api.cekura.ai` (standard outbound HTTPS, no firewall config)
- Local dev (`uv run bot-learn.py`) → same endpoint
- Cekura auto-creates the `agent` on first POST with that name

### Failure mode

If Cekura is down or slow:
- Bot does NOT block on the POST. Fired via `asyncio.create_task(...)` BEFORE `EndTaskFrame`
- Failed POST logs a warning and is lost. v1 has no retry / local queue
- The user's session is unaffected — full conversation regardless of Cekura status

---

## 3. Data model

### Updated dataclasses in `learn_backend.py`

```python
@dataclass
class ConceptCovered:
    concept: str
    brief: str
    started_at: datetime
    ended_at: datetime | None = None


@dataclass
class TranscriptTurn:
    role: str                            # "user" | "assistant"
    content: str
    timestamp: datetime


@dataclass
class SessionState:
    session_id: str                      # opaque uuid; also our call_id
    user_id: str = "default_user"        # multi-user hook
    started_at: datetime
    ended_at: datetime | None = None
    topic: str | None = None
    depth: str | None = None
    starting_level: str | None = None
    concepts_covered: list[ConceptCovered] = field(default_factory=list)
    marked_for_later: list[MarkedForLater] = field(default_factory=list)
    transcript: list[TranscriptTurn] = field(default_factory=list)
    phase_reached: str = "opening"       # opening | scoping | teaching | recap | closing
    end_reason: str | None = None        # "user_goodbye" | "client_disconnect" | "error"
    sent_to_cekura: bool = False         # idempotency latch
```

### How `ConceptCovered.ended_at` gets filled

- `add_concept_covered` called: `started_at = now()`, `ended_at = None`. **Also**: if prior concept's `ended_at` is None, set it to `now()`.
- `recap_session` or `end_session` called: close any open concept with `ended_at = now()`.

Gives non-overlapping concept windows; sum ≈ active teaching time.

### How `transcript` gets captured

Two candidate Pipecat sources:
1. Subscribe to `LLMUserAggregator` / `LLMAssistantAggregator` output frames
2. Hook into Pipecat's built-in `TranscriptProcessor` (if version supports)

We pick one during implementation. Each completed user/assistant turn appends a `TranscriptTurn`.

### `phase_reached` tracking

The state machine is implicit in the prompt; we observe phase transitions from tool calls:
- `set_topic` called → `phase_reached = "teaching"`
- `recap_session` called → `phase_reached = "recap"`
- `end_session` called → `phase_reached = "closing"`

If dropped via `on_client_disconnected` mid-session, `phase_reached` stays at whatever it last was.

### Cekura wire format

```json
{
  "agent": "learn-bot",
  "call_id": "<session_id, uuid4>",
  "voice_recording_url": "",
  "transcript_type": "custom",
  "transcript_json": {
    "turns": [
      {"role": "assistant", "content": "Hi, I'm Confucius...", "timestamp": "2026-05-30T19:47:10Z"},
      {"role": "user",      "content": "I want to learn WebRTC",  "timestamp": "2026-05-30T19:47:14Z"}
    ],
    "metadata": {
      "user_id": "default_user",
      "topic": "WebRTC",
      "depth": "overview",
      "starting_level": "unknown",
      "session_started_at": "2026-05-30T19:47:10Z",
      "session_ended_at":   "2026-05-30T19:54:23Z",
      "session_duration_seconds": 433,
      "concepts": [
        {"concept": "transport", "brief": "...", "started_at": "...", "ended_at": "...", "duration_seconds": 87}
      ],
      "marked_for_later": [{"item": "SDP", "reason": "user wants to finish current topic first"}],
      "phase_reached": "recap"
    }
  },
  "call_ended_reason": "user_goodbye"
}
```

`transcript_type: "custom"` allows arbitrary `transcript_json` shape. Standard turns go under `"turns"`, structured metrics under `"metadata"`.

---

## 4. Send lifecycle

### Three ways a session can end

| Path | Trigger | Coverage |
|---|---|---|
| A — Clean exit | LLM calls `end_session` after Phase 4 recap | Most common when bot works correctly |
| B — Client drop | User closes browser, network blip, walk-out-of-range | Catches anything not covered by A |
| C — Process recycle | Pipecat Cloud autoscaler kills the worker | Unhandleable in v1 — data lost |

### Send rule (fire-and-forget)

```python
def trigger_cekura_send(state):
    if state.sent_to_cekura:
        return
    state.sent_to_cekura = True
    # close any open concept
    if state.concepts_covered and state.concepts_covered[-1].ended_at is None:
        state.concepts_covered[-1].ended_at = now()
    state.ended_at = now()
    asyncio.create_task(cekura_client.send_session(state))
```

### In `end_session` tool

```python
async def end_session(params):
    state = get_or_create_session(session_id)
    state.end_reason = "user_goodbye"
    state.phase_reached = "closing"
    trigger_cekura_send(state)
    await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
    await params.result_callback({"ok": True}, properties=FunctionCallResultProperties(run_llm=False))
```

### In `on_client_disconnected` handler

```python
@transport.event_handler("on_client_disconnected")
async def on_client_disconnected(transport, client):
    state = get_or_create_session(session_id)
    if not state.sent_to_cekura:
        state.end_reason = "client_disconnect"
        trigger_cekura_send(state)
```

### Why fire-and-forget, not synchronous

| | Fire-and-forget | Synchronous (rejected) |
|---|---|---|
| Tool latency | +1 ms | +200–500 ms |
| Pipeline teardown delay | None | +200–500 ms |
| Lost logs | ~0–5% if Cekura is slow + worker dies fast | None (when Cekura healthy) |
| User-visible | 0 ms | 0 ms |
| Aligns with industry observability SDKs (Sentry, Datadog, OTEL) | ✅ | ❌ |

**Observability is intentionally lossy.** Treating it as critical-path is over-engineering. If teardown races prove problematic during real use, add `asyncio.shield()` as a v1.5 fix.

### Idempotency

| Scenario | end_session? | on_client_disconnected? | Outcome |
|---|---|---|---|
| Happy path | ✅ | ✅ (right after) | 1 POST (latch prevents disconnect handler from re-sending) |
| User closes mid-teaching | ❌ | ✅ | 1 POST with `phase_reached="teaching"`, `end_reason="client_disconnect"` |
| Network blip + reconnect | ❌ | ✅ → ✅ on new session | Each session is its own UUID → independent records |
| Process recycle | ❌ | ❌ | No POST. Data lost. Acceptable for v1. |

---

## 5. Auth & secrets

| Step | Where | How |
|---|---|---|
| Get key | Cekura dashboard → API Key (project-level, NOT private/org key) | One-time |
| Local dev | `server/.env` adds `CEKURA_API_KEY=<key>` | Python-helper write (direct `.env` writes blocked by policy) |
| Pipecat Cloud | `pc cloud secrets set learn-bot-secrets --file .env --skip` | Re-upload after adding the key |
| Bot reads | `os.environ["CEKURA_API_KEY"]` in `cekura_client.py` | Standard `dotenv` (already wired) |

### Missing-key behavior (safe)

```python
async def send_session(state):
    if not CEKURA_API_KEY:
        logger.warning("[cekura] CEKURA_API_KEY not set — skipping send")
        return
    # ... POST
```

Bot works normally without the key; observation just disabled with one warning per session.

### Multi-environment

- Local and Pipecat Cloud both POST as `agent="learn-bot"` for v1 simplicity
- If isolation matters later, use `agent="learn-bot-dev"` locally

---

## 6. Error handling

| Failure | Detection | Bot behavior |
|---|---|---|
| `CEKURA_API_KEY` missing | At send time | warning, skip |
| Cekura 401/403 (bad key) | HTTP response | warning |
| Cekura 5xx | HTTP response | warning |
| Cekura 4xx (bad payload) | HTTP response | **error** (signals code bug; investigate) |
| Network timeout (>5 s) | `asyncio.TimeoutError` | warning |
| DNS / TCP failure | `aiohttp.ClientError` | warning |
| JSON serialization failure | `TypeError` | **error** (code bug) |
| `asyncio.create_task` cancelled by teardown | task just doesn't finish | nothing logged; lost log |

No retries, no local queue, no backoff. Observation never blocks the product.

### `cekura_client.py` skeleton

```python
async def send_session(state):
    if not CEKURA_API_KEY:
        logger.warning("[cekura] CEKURA_API_KEY not set — skipping send")
        return
    try:
        payload = build_payload(state)
    except (TypeError, ValueError) as e:
        logger.error(f"[cekura] payload build failed for {state.session_id}: {e}")
        return
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5.0)) as s:
            async with s.post(CEKURA_URL, json=payload, headers=_headers()) as resp:
                if resp.status >= 500:
                    body = await resp.text()
                    logger.warning(f"[cekura] server error {resp.status}: {body[:200]}")
                elif resp.status >= 400:
                    body = await resp.text()
                    logger.error(f"[cekura] client error {resp.status}: {body[:500]}")
                else:
                    logger.info(f"[cekura] sent {state.session_id} ({resp.status})")
    except asyncio.TimeoutError:
        logger.warning(f"[cekura] timeout for {state.session_id}")
    except aiohttp.ClientError as e:
        logger.warning(f"[cekura] connection error: {e}")
```

---

## 7. Testing & validation

### Layer 1 — Local smoke (before redeploy)

**Step 1: Payload-build sanity (no network)**

```bash
cd server
uv run python -c "
import asyncio
from datetime import datetime, timezone, timedelta
from learn_backend import SessionState, ConceptCovered, MarkedForLater, TranscriptTurn
from cekura_client import build_payload

state = SessionState(
    session_id='smoke-001', user_id='default_user',
    started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    ended_at=datetime.now(timezone.utc),
    topic='WebRTC', depth='overview', starting_level='novice',
    concepts_covered=[ConceptCovered('transport', 'opus over udp...',
        started_at=datetime.now(timezone.utc) - timedelta(minutes=4),
        ended_at=datetime.now(timezone.utc) - timedelta(minutes=2))],
    marked_for_later=[MarkedForLater('SDP', 'after STUN/TURN')],
    transcript=[TranscriptTurn('assistant', 'Hi, I am Confucius.', datetime.now(timezone.utc))],
    phase_reached='recap', end_reason='user_goodbye',
)
import json
print(json.dumps(build_payload(state), indent=2)[:1000])
"
```

Expected: clean JSON output matching Section 3's wire format.

**Step 2: Live session against Cekura (real POST)**

Run `uv run bot-learn.py`, open `localhost:7860/client/`, have ~2-min conversation, say "let's wrap up".

Pass criteria:
- `[cekura] sent <session_id> (200)` log line within ~1 s of goodbye
- New call entry in Cekura dashboard for `agent=learn-bot`
- Click in: transcript + custom metadata both visible (probably as JSON until metrics are defined)

**Step 3: Failure-path smoke**

```bash
CEKURA_API_KEY="" uv run bot-learn.py     # expect: [cekura] CEKURA_API_KEY not set
CEKURA_API_KEY="wrong" uv run bot-learn.py # expect: [cekura] client error 401
# Edit URL to api.cekura.invalid → expect: [cekura] connection error
```

All three: conversation must be functionally normal.

### Layer 2 — Deployed smoke

After `pc cloud deploy --yes`:

1. Open sandbox URL on phone
2. `pc cloud agent logs learn-bot --follow` in parallel
3. Have ~3-min conversation, end with "let's wrap up"
4. Verify in logs: `[cekura] sent <session_id> (200)`
5. Verify in dashboard: entry appears within 30 s
6. Click in: transcript + structured metadata both visible

### Pass / fail for v1 ship

- ✅ 3+ consecutive sessions visible in Cekura calls list
- ✅ Each has correct topic field (matches what user said)
- ✅ Concept count + total duration ≈ what's estimable from transcript
- ✅ Zero `[cekura] payload rejected` errors

### Out of test scope

| Type | Why deferred |
|---|---|
| Unit tests on `cekura_client.py` | Small functions; smoke tests cover them |
| Load testing Cekura's intake | Not our problem |
| Multi-user aggregation | All v1 records have `user_id="default_user"` |
| Cekura UI regression tests | Their UI is their problem |
| Trending-extraction quality | Subjective; observe-and-iterate in real use |

---

## 8. Implementation phasing

Ballpark **3–5 hours total**, plus ~1 hour of post-deploy Cekura metric configuration.

| # | Step | Time |
|---|---|---|
| 1 | Add `CEKURA_API_KEY` to `.env` (Python-helper write) | 5 min |
| 2 | Create `server/cekura_client.py` (payload builder, send function, auth header) | 45 min |
| 3 | Extend dataclasses in `learn_backend.py` (`user_id`, timestamps, transcript, `sent_to_cekura`, `phase_reached`, `end_reason`) | 30 min |
| 4 | Update tool implementations (`set_topic`, `add_concept_covered`, `recap_session`, `end_session`) to populate new fields + trigger send | 45 min |
| 5 | Wire transcript capture in `bot-learn.py` (subscribe to aggregator events) | 30 min |
| 6 | Hook `on_client_disconnected` as fallback Cekura trigger | 10 min |
| 7 | Layer 1 smoke: payload build + live session + 3 failure-path checks | 30 min |
| 8 | Re-upload secrets (`pc cloud secrets set learn-bot-secrets --file .env --skip`) | 1 min |
| 9 | Redeploy (`pc cloud deploy --yes`) | 3 min build |
| 10 | Layer 2 phone smoke (3 sessions, all show up in Cekura, no 4xx errors) | 20 min |
| 11 | Define 2-3 Cekura metrics in dashboard UI (topic extractor, recap-delivered, concept-count) | 1 hr |
| 12 | Verify metrics tag the recent sessions in Cekura's calls list view | 10 min |

Steps 2–6 are the substance. Steps 7–10 are validation + ship. Steps 11–12 unlock list-view trending.

### Definition of done for v1

After 3 consecutive learn-bot sessions (any combination of local + sandbox):
- 3 records visible in Cekura's calls list at `dashboard.cekura.ai/<org>/<project>/observability/calls`
- Each has correct `topic` (matches what was said)
- Each has concept list with timestamps in metadata
- Transcript visible
- Cekura metrics (defined in step 11) tag each row with topic, recap status, concept count
- Zero `[cekura] payload rejected` errors in any session log

---

## 9. Visualizing the data in Cekura

Cekura's UI is **list-shaped, not chart-shaped.** Expect a filterable/sortable table of calls, not a Grafana dashboard.

### Where to look

| View | URL pattern | What you see |
|---|---|---|
| Calls list | `dashboard.cekura.ai/<org>/<project>/observability/calls` | One row per POSTed session. Filterable by agent, time range, metric values. |
| Call detail | `.../observability/calls/<call_id>` | Full transcript replay + voice recording (if URL set) + metric verdicts + raw `transcript_json.metadata` (as JSON) |
| Metrics | `.../observability/metrics` | Where the metrics defined in step 11 are configured |

### Trending = filtering the calls list

Pick "topic" column → group/sort → see frequency by inspection. Filter `recap=✗` → see incomplete sessions. Filter time range → see distribution this week.

### What Cekura does NOT natively give

- 📊 Bar chart of "top topics this month"
- 📈 Line chart of "average duration over time"
- 🌡️ Heatmap of "which concepts take longest"

If those become essential, the v2 ladder includes a local FastAPI + recharts dashboard that pulls from Cekura's API. Not in v1 scope.

---

## Appendix A — Cekura metric definitions for step 11

Defined in Cekura UI via natural language (boolean by default).

| # | Metric name | Description (in Cekura UI) | Output |
|---|---|---|---|
| 1 | **session_topic** | "Extract the single topic this session was about. Return as a short noun phrase, e.g. 'quantum mechanics', 'WebRTC', 'Roman empire'. Use the topic from `metadata.topic` if present, otherwise infer from transcript." | text |
| 2 | **recap_delivered** | "Did the bot deliver a recap summarizing what was covered before saying goodbye? Set true only if `metadata.phase_reached == 'recap'` or `'closing'`, or if a clear recap is visible in the transcript." | boolean |
| 3 | **concept_count_ge_3** | "Did this session cover at least 3 distinct concepts? Check `metadata.concepts` array length, or count distinct concept-introductions in the transcript." | boolean |

These three are enough to make the calls list filterable for v1. Add more metrics if observation suggests new dimensions matter.

---

## Appendix B — File reference after this design

```
server/
├── bot-gpt.py            # unchanged (flower-bot)
├── bot-nemotron.py       # unchanged
├── bot-learn.py          # MODIFIED — fallback Cekura trigger in on_client_disconnected
├── learn_backend.py      # MODIFIED — extended dataclasses + timestamps + idempotency latch
├── cekura_client.py      # NEW
├── Dockerfile            # unchanged (already COPYs bot-learn.py + learn_backend.py)
├── pcc-deploy.toml       # unchanged
└── .env                  # MODIFIED (CEKURA_API_KEY added)
```
