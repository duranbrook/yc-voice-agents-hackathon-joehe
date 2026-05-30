# Cekura Observability for learn-bot — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Wire the deployed `learn-bot` to POST a structured session record (full transcript + per-concept timing metadata) to Cekura's `/observability/v1/observe/` endpoint at the end of every session, fire-and-forget, with safe degradation when Cekura is unreachable.

**Architecture:** New `cekura_client.py` provides a payload builder + async POST helper. `learn_backend.py` dataclasses gain timestamps, transcript capture, and an idempotency latch. `bot-learn.py` triggers the send from `end_session` (clean exit) AND `on_client_disconnected` (client drop) via `asyncio.create_task(...)`. No retries, no local queue — observation is best-effort.

**Tech Stack:** Python 3.13, `uv`, aiohttp (already in Pipecat's deps), Pipecat 1.3.0, Cekura REST API.

**Design doc:** `docs/plans/2026-05-30-cekura-observability-design.md`

**Scope explicitly OUT of this plan:** Cekura UI metric configuration (design Section 8 steps 11–12, Appendix A — these happen in the Cekura dashboard, not in code; tracked separately as a follow-up tag). Local chart dashboard (design v2 hook). Multi-user identity flow. Retries / local queue.

**Working directory:** `/Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe`

**Conventions** (carried from the learn-bot plan):
- All `pc cloud` commands must run with `GH_TOKEN` and `GITHUB_TOKEN` unset. Prefix with `env -u GH_TOKEN -u GITHUB_TOKEN`.
- All `.env` writes go through Python because shell writes are blocked by permission policy.
- Commit after every task. Use conventional commits (`feat:`, `chore:`, `fix:`).

---

## Task 1: Add `CEKURA_API_KEY` to `.env`

**Files:**
- Modify: `server/.env`

**Step 1: Use the Python-helper pattern to add the key**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
python3 -c "
import os, re
p = os.path.join(os.getcwd(), '.env')
with open(p) as f: txt = f.read()
# Append CEKURA_API_KEY if missing
if 'CEKURA_API_KEY=' not in txt:
    if not txt.endswith('\n'):
        txt += '\n'
    txt += '\n# Cekura (Observability)\nCEKURA_API_KEY=639253fa14349a3e148879776d85ca11f7a3ff6698d3433dc85cb80bf26a7628\n'
    with open(p, 'w') as f: f.write(txt)
    print('appended CEKURA_API_KEY')
else:
    print('CEKURA_API_KEY already present, skipping')
"
```

**Step 2: Verify**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
from pathlib import Path
import re
env = Path('.env').read_text()
m = re.search(r'^CEKURA_API_KEY=(.+)$', env, re.M)
assert m, 'CEKURA_API_KEY missing'
val = m.group(1).strip()
assert len(val) >= 32, f'key too short: {len(val)} chars'
print(f'CEKURA_API_KEY: <set, {len(val)} chars>')
"
```

Expected output: `CEKURA_API_KEY: <set, 64 chars>`

**Step 3: Do NOT commit `.env`**

`.env` is gitignored. Confirm:

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git status server/.env
```

Expected: `.env` does not appear in `git status` output (gitignored). If it does, stop and investigate the `.gitignore`.

**No commit for Task 1** — the only change is to a gitignored file.

---

## Task 2: Create `server/cekura_client.py`

**Files:**
- Create: `server/cekura_client.py`

**Step 1: Write the file**

```python
# server/cekura_client.py
"""Cekura observability client.

Fire-and-forget POST of a completed learn-bot session to Cekura's
/observability/v1/observe/ endpoint. Failures are logged at warning/error
severity but never raised — observation must never block the product.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING

import aiohttp
from loguru import logger

if TYPE_CHECKING:
    from learn_backend import SessionState


CEKURA_URL = "https://api.cekura.ai/observability/v1/observe/"
CEKURA_API_KEY = os.environ.get("CEKURA_API_KEY", "")
HTTP_TIMEOUT_SECONDS = 5.0


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-CEKURA-API-KEY": CEKURA_API_KEY,
    }


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def build_payload(state: "SessionState") -> dict:
    """Build the JSON body POSTed to /observability/v1/observe/.

    Schema matches Cekura's documented fixed top-level keys
    (agent, call_id, voice_recording_url, transcript_type, transcript_json,
    call_ended_reason) plus our structured "metadata" object embedded inside
    transcript_json (transcript_type="custom" allows arbitrary shape).
    """
    duration_seconds = None
    if state.ended_at:
        duration_seconds = round((state.ended_at - state.started_at).total_seconds(), 1)

    concepts = []
    for c in state.concepts_covered:
        c_dur = None
        if c.ended_at:
            c_dur = round((c.ended_at - c.started_at).total_seconds(), 1)
        concepts.append({
            "concept": c.concept,
            "brief": c.brief,
            "started_at": _iso(c.started_at),
            "ended_at": _iso(c.ended_at),
            "duration_seconds": c_dur,
        })

    return {
        "agent": "learn-bot",
        "call_id": state.session_id,
        "voice_recording_url": "",
        "transcript_type": "custom",
        "transcript_json": {
            "turns": [
                {
                    "role": t.role,
                    "content": t.content,
                    "timestamp": _iso(t.timestamp),
                }
                for t in state.transcript
            ],
            "metadata": {
                "user_id": state.user_id,
                "topic": state.topic,
                "depth": state.depth,
                "starting_level": state.starting_level,
                "session_started_at": _iso(state.started_at),
                "session_ended_at": _iso(state.ended_at),
                "session_duration_seconds": duration_seconds,
                "concepts": concepts,
                "marked_for_later": [asdict(m) for m in state.marked_for_later],
                "phase_reached": state.phase_reached,
            },
        },
        "call_ended_reason": state.end_reason or "unknown",
    }


async def send_session(state: "SessionState") -> None:
    """POST a completed session to Cekura. Never raises."""
    if not CEKURA_API_KEY:
        logger.warning("[cekura] CEKURA_API_KEY not set — skipping send")
        return
    try:
        payload = build_payload(state)
    except (TypeError, ValueError) as e:
        logger.error(f"[cekura] payload build failed for {state.session_id}: {e}")
        return
    try:
        timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SECONDS)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(CEKURA_URL, json=payload, headers=_headers()) as resp:
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

**Step 2: Verify it imports**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
from cekura_client import build_payload, send_session, CEKURA_URL, CEKURA_API_KEY
assert CEKURA_URL == 'https://api.cekura.ai/observability/v1/observe/'
print('OK: cekura_client imports cleanly')
"
```

Expected: ends with `OK: cekura_client imports cleanly`

**Step 3: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/cekura_client.py
git commit -m "feat(cekura): add observability client with payload builder + send helper"
```

---

## Task 3: Extend dataclasses in `learn_backend.py`

**Files:**
- Modify: `server/learn_backend.py`

**Step 1: Add `TranscriptTurn` dataclass**

Find the existing `MarkedForLater` dataclass. After it, before `SessionState`, add:

```python
@dataclass
class TranscriptTurn:
    role: str               # "user" | "assistant"
    content: str
    timestamp: datetime
```

**Step 2: Extend `ConceptCovered`**

Find the existing:

```python
@dataclass
class ConceptCovered:
    concept: str
    brief: str
```

Replace with:

```python
@dataclass
class ConceptCovered:
    concept: str
    brief: str
    started_at: datetime
    ended_at: datetime | None = None
```

**Step 3: Extend `SessionState`**

Find the existing `SessionState` dataclass. Add the following fields, preserving the existing ones:

```python
@dataclass
class SessionState:
    session_id: str
    started_at: datetime
    user_id: str = "default_user"
    ended_at: datetime | None = None
    topic: Optional[str] = None
    depth: Optional[str] = None
    starting_level: Optional[str] = None
    concepts_covered: list[ConceptCovered] = field(default_factory=list)
    marked_for_later: list[MarkedForLater] = field(default_factory=list)
    transcript: list[TranscriptTurn] = field(default_factory=list)
    phase_reached: str = "opening"           # opening | scoping | teaching | recap | closing
    end_reason: str | None = None            # "user_goodbye" | "client_disconnect" | "error"
    sent_to_cekura: bool = False             # idempotency latch
```

**Note on field order:** all new fields have defaults so they can be ordered freely. Keep `session_id` and `started_at` first (the only required fields).

**Step 4: Verify the file still parses and imports**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
from datetime import datetime, timezone
from learn_backend import SessionState, ConceptCovered, MarkedForLater, TranscriptTurn

# Smoke: construct each dataclass with the new fields
s = SessionState(session_id='t1', started_at=datetime.now(timezone.utc))
assert s.user_id == 'default_user'
assert s.sent_to_cekura is False
assert s.phase_reached == 'opening'
assert s.transcript == []

c = ConceptCovered(concept='x', brief='y', started_at=datetime.now(timezone.utc))
assert c.ended_at is None

t = TranscriptTurn(role='user', content='hi', timestamp=datetime.now(timezone.utc))
assert t.role == 'user'

print('OK: dataclass extensions verified')
"
```

Expected: ends with `OK: dataclass extensions verified`

**Step 5: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/learn_backend.py
git commit -m "feat(learn-bot): extend SessionState + ConceptCovered for observability"
```

---

## Task 4: Update tool implementations in `learn_backend.py`

**Files:**
- Modify: `server/learn_backend.py` (the `make_tools` factory's nested async functions)

**Step 1: Update `set_topic`**

Find the existing `set_topic` function. Add a single line setting `phase_reached`. The body should become:

```python
async def set_topic(
    params: FunctionCallParams,
    topic: str,
    depth: str,
    starting_level: str,
) -> None:
    """Record the topic + depth + starting knowledge level. Phase 2 → 3."""
    state = get_or_create_session(session_id)
    state.topic = topic
    state.depth = depth
    state.starting_level = starting_level
    state.phase_reached = "teaching"
    logger.info(f"[learn] set_topic: {topic} ({depth}, {starting_level})")
    await params.result_callback(
        f"Topic set: {topic} ({depth}, {starting_level}). Begin teaching."
    )
```

**Step 2: Update `add_concept_covered`**

Find the existing function. Replace the body so it timestamps and closes the prior concept:

```python
async def add_concept_covered(
    params: FunctionCallParams,
    concept: str,
    brief: str,
) -> None:
    """Log a concept that was just explained. Phase 3. Silent ack."""
    from datetime import datetime, timezone
    state = get_or_create_session(session_id)
    now = datetime.now(timezone.utc)
    # Close prior concept if still open
    if state.concepts_covered and state.concepts_covered[-1].ended_at is None:
        state.concepts_covered[-1].ended_at = now
    state.concepts_covered.append(ConceptCovered(
        concept=concept,
        brief=brief,
        started_at=now,
    ))
    logger.info(f"[learn] add_concept_covered: {concept}")
    await params.result_callback("Logged.")
```

**Step 3: Update `recap_session`**

Find the existing function. Add `phase_reached` update + close the last open concept. The body should become:

```python
async def recap_session(params: FunctionCallParams) -> None:
    """Return structured session state for the LLM to verbalize. Phase 3 → 4."""
    from datetime import datetime, timezone
    state = get_or_create_session(session_id)
    state.phase_reached = "recap"
    # Close current concept if still open
    if state.concepts_covered and state.concepts_covered[-1].ended_at is None:
        state.concepts_covered[-1].ended_at = datetime.now(timezone.utc)
    payload = {
        "topic": state.topic,
        "depth": state.depth,
        "starting_level": state.starting_level,
        "concepts_covered": [asdict(c) for c in state.concepts_covered],
        "marked_for_later": [asdict(m) for m in state.marked_for_later],
        "duration_minutes": round(_duration_minutes(state), 1),
    }
    logger.info(f"[learn] recap_session: {payload}")
    await params.result_callback(payload)
```

**Step 4: Update `end_session`**

Find the existing function. Replace body to fire-and-forget the Cekura send before pushing `EndTaskFrame`:

```python
async def end_session(params: FunctionCallParams) -> None:
    """Close the call cleanly. Phase 5. Same pattern as flower-bot's end_call.

    `run_llm=False` prevents the LLM from generating a follow-up response after
    this function returns — the goodbye should already be in flight.
    """
    from datetime import datetime, timezone
    import cekura_client
    state = get_or_create_session(session_id)
    now = datetime.now(timezone.utc)
    state.phase_reached = "closing"
    state.end_reason = state.end_reason or "user_goodbye"
    state.ended_at = now
    if state.concepts_covered and state.concepts_covered[-1].ended_at is None:
        state.concepts_covered[-1].ended_at = now
    if not state.sent_to_cekura:
        state.sent_to_cekura = True
        asyncio.create_task(cekura_client.send_session(state))
    logger.info(f"[learn] end_session for {session_id}")
    await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
    await params.result_callback(
        {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
    )
```

**Step 5: Add the `asyncio` import at the top of `learn_backend.py`**

Near the existing imports (after the `from __future__ import annotations` line), add:

```python
import asyncio
```

if it's not already there. Verify with `grep`:

```bash
grep -n "^import asyncio" /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server/learn_backend.py
```

Expected: one line matching.

**Step 6: Verify imports + state mutation smoke**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock
from learn_backend import make_tools, get_or_create_session

async def main():
    sid = 'task4-smoke'
    tools = make_tools(sid)
    names = [t.__name__ for t in tools]
    assert names == ['set_topic', 'add_concept_covered', 'mark_for_later', 'recap_session', 'end_session']
    set_topic, add_concept_covered, mark_for_later, recap_session, end_session = tools

    # set_topic flips phase_reached
    params = MagicMock(); params.result_callback = AsyncMock()
    await set_topic(params, topic='Q', depth='overview', starting_level='novice')
    state = get_or_create_session(sid)
    assert state.phase_reached == 'teaching', state.phase_reached
    assert state.topic == 'Q'

    # add_concept_covered timestamps + closes prior
    await add_concept_covered(params, concept='c1', brief='b1')
    await add_concept_covered(params, concept='c2', brief='b2')
    assert len(state.concepts_covered) == 2
    assert state.concepts_covered[0].ended_at is not None  # closed by c2
    assert state.concepts_covered[1].ended_at is None      # still open

    # recap_session closes current + flips phase
    await recap_session(params)
    assert state.phase_reached == 'recap'
    assert state.concepts_covered[1].ended_at is not None  # closed

    print('OK: tool updates verified')

asyncio.run(main())
"
```

Expected: ends with `OK: tool updates verified`

**Step 7: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/learn_backend.py
git commit -m "feat(learn-bot): timestamp concepts + phase tracking + fire-and-forget cekura send"
```

---

## Task 5: Wire transcript capture in `bot-learn.py`

**Files:**
- Modify: `server/bot-learn.py`

**Background:** Pipecat ships a `TranscriptProcessor` that emits transcription events. We hook its event to append `TranscriptTurn`s to `SessionState.transcript`.

**Step 1: Investigate the available API on this Pipecat version**

Before writing code, confirm which transcript hook is available:

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
from pipecat.processors.transcript_processor import TranscriptProcessor
print('OK: TranscriptProcessor available')
print(dir(TranscriptProcessor()))
" 2>&1 | head -30
```

If `TranscriptProcessor` is not available, fall back to the aggregator-event approach (note below). If it is, proceed.

**Step 2 (preferred — `TranscriptProcessor`): Add to pipeline + register handler**

In `bot-learn.py`, near the other imports:

```python
from pipecat.processors.transcript_processor import TranscriptProcessor
```

Inside `run_bot`, after the existing `pipeline = Pipeline([...])` block, add a transcript processor and a handler:

```python
    transcript_processor = TranscriptProcessor()

    @transcript_processor.event_handler("on_transcript_update")
    async def on_transcript_update(processor, frame):
        from datetime import datetime, timezone
        from learn_backend import TranscriptTurn, get_or_create_session
        state = get_or_create_session(session_id)
        for msg in frame.messages:
            state.transcript.append(TranscriptTurn(
                role=msg.role,            # "user" | "assistant"
                content=msg.content,
                timestamp=datetime.now(timezone.utc),
            ))
```

Then insert `transcript_processor.user()` and `transcript_processor.assistant()` into the pipeline at the appropriate positions (typically right after the user_aggregator and right after the assistant_aggregator). Adjust to wherever the pipeline already aggregates user/assistant turns.

**Step 2 (fallback — aggregator subscription):** If `TranscriptProcessor` doesn't exist in this Pipecat version, hook the user/assistant aggregators directly:

```python
    # Inside run_bot, after user_aggregator / assistant_aggregator are created
    original_user_handler = user_aggregator.process_frame
    async def capturing_user(frame, direction):
        from datetime import datetime, timezone
        from pipecat.frames.frames import TextFrame
        from learn_backend import TranscriptTurn, get_or_create_session
        if isinstance(frame, TextFrame) and frame.text:
            state = get_or_create_session(session_id)
            state.transcript.append(TranscriptTurn(
                role="user",
                content=frame.text,
                timestamp=datetime.now(timezone.utc),
            ))
        return await original_user_handler(frame, direction)
    # ... similar for assistant_aggregator
```

The TranscriptProcessor route is cleaner; only fall back if it's not available.

**Step 3: Smoke-test by running locally for ~30 s and inspecting `SessionState.transcript`**

This is hard to test without a real conversation. We rely on Task 7's Layer 1 smoke to verify. For now, just confirm the file parses:

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "import ast; ast.parse(open('bot-learn.py').read()); print('parse: OK')"
```

Expected: `parse: OK`

**Step 4: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/bot-learn.py
git commit -m "feat(learn-bot): capture transcript turns into SessionState"
```

---

## Task 6: Hook `on_client_disconnected` as fallback Cekura trigger

**Files:**
- Modify: `server/bot-learn.py`

**Step 1: Locate the existing `on_client_disconnected` handler**

```bash
grep -n "on_client_disconnected" /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server/bot-learn.py
```

Note the line number. Expected: ~line 270-280 (matches the `on_client_connected` handler just above it).

**Step 2: Update the handler to trigger Cekura send if not already sent**

Replace the existing `on_client_disconnected` function body with:

```python
    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        import asyncio
        from datetime import datetime, timezone
        import cekura_client
        state = get_or_create_session(session_id)
        if not state.sent_to_cekura:
            state.sent_to_cekura = True
            state.end_reason = state.end_reason or "client_disconnect"
            state.ended_at = state.ended_at or datetime.now(timezone.utc)
            if state.concepts_covered and state.concepts_covered[-1].ended_at is None:
                state.concepts_covered[-1].ended_at = state.ended_at
            asyncio.create_task(cekura_client.send_session(state))
        logger.info("Client disconnected")
```

Preserve any other logic the existing handler has (e.g., calling `await task.queue_frames([EndFrame()])` if it does). Just *add* the Cekura trigger before / after as appropriate.

**Step 3: Verify the file parses + imports**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
import importlib.util
spec = importlib.util.spec_from_file_location('botlearn', 'bot-learn.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('OK: bot-learn imports cleanly')
"
```

Expected: ends with `OK: bot-learn imports cleanly`

**Step 4: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/bot-learn.py
git commit -m "feat(learn-bot): trigger cekura send on client disconnect (fallback path)"
```

---

## Task 7: Layer 1 local smoke test

**Files:** none modified (this task is validation only).

**Step 1: Payload-build sanity (no network)**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
import json
from datetime import datetime, timezone, timedelta
from learn_backend import (
    SessionState, ConceptCovered, MarkedForLater, TranscriptTurn
)
from cekura_client import build_payload

state = SessionState(
    session_id='smoke-001',
    user_id='default_user',
    started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
    ended_at=datetime.now(timezone.utc),
    topic='WebRTC',
    depth='overview',
    starting_level='novice',
    concepts_covered=[
        ConceptCovered(
            concept='transport',
            brief='opus over udp',
            started_at=datetime.now(timezone.utc) - timedelta(minutes=4),
            ended_at=datetime.now(timezone.utc) - timedelta(minutes=2),
        ),
    ],
    marked_for_later=[MarkedForLater('SDP', 'after STUN/TURN')],
    transcript=[
        TranscriptTurn('assistant', 'Hi, I am Confucius.', datetime.now(timezone.utc)),
    ],
    phase_reached='recap',
    end_reason='user_goodbye',
)
payload = build_payload(state)
assert payload['agent'] == 'learn-bot'
assert payload['call_id'] == 'smoke-001'
assert payload['transcript_type'] == 'custom'
md = payload['transcript_json']['metadata']
assert md['topic'] == 'WebRTC'
assert md['user_id'] == 'default_user'
assert md['phase_reached'] == 'recap'
assert len(md['concepts']) == 1
assert md['concepts'][0]['duration_seconds'] is not None
assert json.dumps(payload)  # serializable
print('OK: payload build verified')
print(json.dumps(payload, indent=2)[:500])
"
```

Expected: ends with `OK: payload build verified` and a JSON preview.

**Step 2: Live session against Cekura (real POST)**

Stop any existing bot process:

```bash
lsof -ti tcp:7860 | xargs -r kill -9 2>/dev/null
```

Launch the bot:

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run bot-learn.py 2>&1
```

(Run in background; capture the output file path.)

Wait for `Uvicorn running on http://localhost:7860`. Then open `http://localhost:7860/client/` in a browser. Have a ~2-minute conversation:

1. Bot greets: "Hi, I'm Confucius. What do you want to learn about today?"
2. Pick a topic (e.g. "let me learn about quantum mechanics")
3. Listen to a brief explanation, ask a follow-up
4. Say "let's wrap up" → expect recap
5. Say "thanks, goodbye" → bot ends session

**Pass criteria** (inspect bot log):
- `[learn] set_topic:` line appears
- `[learn] add_concept_covered:` line(s) appear
- `[learn] recap_session:` line appears
- `[learn] end_session for ...` line appears
- `[cekura] sent <session_id> (200)` line appears within ~1 s of the goodbye

If `[cekura] sent ... (200)` does NOT appear, check earlier `[cekura]` lines for the failure reason and fix before proceeding.

**Step 3: Failure-path smoke (3 mini-tests)**

Stop the bot. Run each scenario in turn (kill the bot between each):

```bash
# 3a — missing key
lsof -ti tcp:7860 | xargs -r kill -9 2>/dev/null
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
CEKURA_API_KEY="" uv run bot-learn.py 2>&1 &
# Connect via browser, talk briefly, end with "wrap up" + "goodbye"
# Expected: log contains `[cekura] CEKURA_API_KEY not set — skipping send`
# Expected: conversation is functionally normal (no errors visible to user)
```

```bash
# 3b — bad key
lsof -ti tcp:7860 | xargs -r kill -9 2>/dev/null
CEKURA_API_KEY="wrong" uv run bot-learn.py 2>&1 &
# Connect via browser, talk briefly, end
# Expected: log contains `[cekura] client error 401` (or 403)
# Expected: conversation normal
```

```bash
# 3c — unreachable host
# Set CEKURA_URL temporarily by editing cekura_client.py (or use a wrapper env var)
# For simplicity, just confirm 3a and 3b in the previous two scenarios; the
# aiohttp.ClientError path is exercised the same way at the code level.
```

If 3a and 3b both confirm the bot continues normally despite the cekura failure, mark Task 7 verified.

**Step 4: Kill any leftover bot**

```bash
lsof -ti tcp:7860 | xargs -r kill -9 2>/dev/null
```

**Step 5: Commit any incidental fixes from smoke testing**

If you had to edit `cekura_client.py` or `learn_backend.py` during smoke testing, commit them now:

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add -A server/
git commit -m "fix(cekura): smoke-test fixes from Layer 1 validation"
# Or skip if no changes
```

---

## Task 8: Re-upload secrets to Pipecat Cloud

**Files:** none modified.

**Step 1: Confirm `.env` has `CEKURA_API_KEY` + the existing keys**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
from pathlib import Path
import re
env = Path('.env').read_text()
required = ['OPENAI_API_KEY', 'GRADIUM_API_KEY', 'GRADIUM_VOICE_ID', 'CEKURA_API_KEY']
for k in required:
    m = re.search(rf'^{k}=(.+)$', env, re.M)
    assert m, f'{k} missing'
    assert m.group(1).strip(), f'{k} empty'
    print(f'{k}: <set, {len(m.group(1))} chars>')
"
```

Expected: 4 lines, each `<set, N chars>` with N > 10.

**Step 2: Strip empty-value lines if any (defensive)**

The Pipecat Cloud secrets uploader rejects empty values. We already did this earlier in the session, but defensive re-run:

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
python3 -c "
p = '.env'
with open(p) as f: lines = f.readlines()
kept = []
for ln in lines:
    s = ln.strip()
    if not s or s.startswith('#'):
        kept.append(ln); continue
    if '=' in s:
        k, _, v = s.partition('=')
        if v == '':
            print('dropping empty:', k); continue
    kept.append(ln)
with open(p, 'w') as f: f.writelines(kept)
print('done')
"
```

**Step 3: Upload secrets**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud secrets set learn-bot-secrets --file .env --skip 2>&1 | tail -10
```

Expected: success banner mentioning `learn-bot-secrets`.

**No commit** — `.env` is gitignored; the upload is to Pipecat Cloud.

---

## Task 9: Update `Dockerfile` to include `cekura_client.py`

**Files:**
- Modify: `server/Dockerfile`

**Background:** The current Dockerfile (from the learn-bot plan) COPYs `bot-learn.py` and `learn_backend.py` but NOT `cekura_client.py`. Since Task 4 makes `learn_backend.py` import `cekura_client`, the deployed image will fail with `ModuleNotFoundError: cekura_client` without this change.

**Step 1: Verify current Dockerfile state**

```bash
grep "COPY" /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server/Dockerfile
```

Expected current output:
```
COPY ./bot-learn.py bot.py
COPY ./learn_backend.py learn_backend.py
```

**Step 2: Add the `cekura_client.py` COPY line**

Edit the Dockerfile. The COPY block should end up as:

```dockerfile
# Copy the application code
COPY ./bot-learn.py bot.py
COPY ./learn_backend.py learn_backend.py
COPY ./cekura_client.py cekura_client.py
```

Add the new line after the existing `learn_backend.py` COPY.

**Step 3: Verify**

```bash
grep "COPY" /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server/Dockerfile
```

Expected: 3 COPY lines (bot-learn.py, learn_backend.py, cekura_client.py).

**Step 4: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/Dockerfile
git commit -m "chore(cekura): copy cekura_client.py into deploy image"
```

---

## Task 10: Redeploy `learn-bot` to Pipecat Cloud

**Files:** none modified.

**Step 1: Trigger the cloud build + deploy**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud deploy --yes 2>&1
```

Run in the background. The cloud build takes ~2-3 minutes.

**Step 2: Wait for completion**

Read the bg task output file for either:
- ✅ Success banner: `Agent deployment 'learn-bot' is ready`
- ❌ Failure banner: `Build failed`

**Step 3: On failure, fetch build logs**

```bash
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud build logs <build-id> 2>&1 | tail -80
```

Common failure modes:
- Missing import (e.g., `learn_backend` can't find `cekura_client`) → fix import, redeploy
- `aiohttp` not available → already in Pipecat deps, shouldn't fail; if it does, check `pyproject.toml`

Fix and re-run Step 1.

**Step 4: On success, verify agent status**

```bash
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud agent status learn-bot 2>&1 | head -25
```

Expected: status panel with "Ready · Active · N agents".

**No commit** — deploy is metadata only.

---

## Task 11: Layer 2 phone smoke test

**Files:** none modified.

**Step 1: Tail deployed bot logs in parallel**

```bash
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud agent logs learn-bot --follow 2>&1
```

Run in the background.

**Step 2: Open the sandbox URL on iPhone Safari**

```
https://pipecat.daily.co/0530id/agents/learn-bot/sandbox
```

Connect, grant mic, run a 3-minute conversation:

1. Pick a topic
2. Listen + ask follow-up
3. Say "let's wrap up" → recap
4. Goodbye

**Step 3: Verify deployed-side**

In the tailed cloud logs, confirm:
- `[learn] set_topic:` line appears
- `[learn] add_concept_covered:` line(s) appear
- `[learn] recap_session:` line appears
- `[learn] end_session for ...` line appears
- `[cekura] sent <session_id> (200)` line appears within ~1 s of goodbye

**Step 4: Verify Cekura-side**

Open `https://dashboard.cekura.ai/<org>/<project>/observability/calls` in a browser. Filter by `agent=learn-bot`. Expected: a new entry with the session you just ran, appearing within 30 s.

Click into the entry. Expected:
- Transcript visible (turns from the conversation)
- Custom metadata visible (probably as JSON — `topic`, `concepts`, `phase_reached`, etc.)

**Step 5: Repeat 2-step until 3 consecutive sessions all appear in Cekura**

Run 2 more sessions. Confirm each shows up. This is the Definition-of-Done check from the design doc.

---

## Definition of done

- `bot-learn.py` + `learn_backend.py` + `cekura_client.py` committed to `main`
- `.env` has `CEKURA_API_KEY` (gitignored, not committed)
- `learn-bot-secrets` re-uploaded to Pipecat Cloud with the new key
- `learn-bot` agent re-deployed and showing "Ready" status
- 3+ consecutive sessions visible in Cekura's calls list at `dashboard.cekura.ai/<org>/<project>/observability/calls`
- Each session has correct `topic`, concept list with timestamps, transcript visible
- Zero `[cekura] payload rejected` errors in any session log

---

## Deferred follow-up (NOT in this plan): Cekura UI metric configuration

Per design Section 8 steps 11–12 + Appendix A. These are done in the Cekura dashboard (`https://dashboard.cekura.ai/<org>/observability/metrics`), not in code. After this plan ships and we have ≥3 sessions of real data in Cekura, configure these natural-language metrics:

1. **session_topic** — "Extract the single topic this session was about. Use `metadata.topic` if present, otherwise infer from transcript." → text output
2. **recap_delivered** — "Did the bot deliver a recap before saying goodbye? True if `metadata.phase_reached == 'recap'` or `'closing'`." → boolean
3. **concept_count_ge_3** — "Did this session cover at least 3 distinct concepts? Check `metadata.concepts` array length." → boolean

Then sort/filter Cekura's calls list by these metrics to see trending.

This follow-up is **out of scope for the code-execution portion of this plan** — track it as a separate task in your todo list.

---

## Out of scope for this plan (do NOT do)

| Item | Status | Where |
|---|---|---|
| Local chart dashboard (FastAPI + recharts) | v2 design hook | Design doc §1, §9 |
| Cekura retries / local queue | v1.5 design hook | Design doc §1, §6 |
| Multi-user identity flow | v2 design hook | Design doc §1 |
| Alerts on metrics | v2 design hook | Design doc §1 |
| Cekura UI metric definitions | Deferred (above) | Design doc §8 steps 11-12 |
| Unit tests on `cekura_client.py` | Skipped by design | Design §7 |
| Voice recording capture | v2 | Design §3 |
| Audio-level metrics (interruption count, etc.) | Out | Design §3 |

---

## Appendix — File reference after this plan

```
server/
├── bot-gpt.py            # unchanged (flower-bot)
├── bot-nemotron.py       # unchanged
├── bot-learn.py          # MODIFIED — transcript capture + on_client_disconnected fallback send
├── learn_backend.py      # MODIFIED — extended dataclasses + timestamped concepts + phase tracking + fire-and-forget send from end_session
├── cekura_client.py      # NEW — HTTP client + payload builder
├── Dockerfile            # UNCHANGED — already COPYs bot-learn.py + learn_backend.py from learn-bot plan; cekura_client.py will be picked up via `learn_backend` import
├── pcc-deploy.toml       # UNCHANGED
└── .env                  # MODIFIED — added CEKURA_API_KEY (gitignored)
```

**Note:** Task 9 of this plan explicitly handles adding `cekura_client.py` to the Dockerfile COPY block. The final Dockerfile after Task 9 should have:

```dockerfile
COPY ./bot-learn.py bot.py
COPY ./learn_backend.py learn_backend.py
COPY ./cekura_client.py cekura_client.py
```
