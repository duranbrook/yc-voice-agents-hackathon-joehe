# Confucius Learning Bot — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build and deploy a v1 voice learning tutor named "Confucius" to Pipecat Cloud, as a fork of the existing flower-bot, with a 5-phase session state machine encoded in the system prompt.

**Architecture:** Reuse the deployed flower-bot stack (Pipecat + Daily + OpenAI GPT-4.1 + Gradium STT/TTS) by adding a sibling `bot-learn.py` + `learn_backend.py` in `server/`. No new vendors, no new transports, no database. State is in-memory only. Deploy as a second Pipecat Cloud agent named `learn-bot`.

**Tech Stack:** Python 3.13, `uv`, Pipecat 1.3.0, OpenAI Responses API (GPT-4.1), Gradium STT + TTS, Pipecat Cloud, Daily WebRTC.

**Design doc:** `docs/plans/2026-05-30-confucius-learning-bot-design.md`

**Scope explicitly OUT:** Cekura integration (v1.5), persistent memory (v2), iOS app (v2), 3rd-party content (v2), unit tests on `learn_backend.py` (per design Section 7).

**Working directory:** `/Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server`

**Conventions:**
- All `pc cloud` commands must run with `GH_TOKEN` and `GITHUB_TOKEN` unset (env may inject DoorDash tokens). Prefix every CLI invocation with `env -u GH_TOKEN -u GITHUB_TOKEN`.
- All `.env` writes go through Python because shell writes are blocked by permission policy.
- Commit after every task. Use conventional commits (`feat:`, `chore:`, `test:`).

---

## Task 1: Scaffold `bot-learn.py` as a literal copy of `bot-gpt.py`

**Files:**
- Create: `server/bot-learn.py` (initially identical to `server/bot-gpt.py`)

**Step 1: Copy the file**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
cp bot-gpt.py bot-learn.py
```

**Step 2: Verify the copy is valid Python**

```bash
uv run python -c "import ast; ast.parse(open('bot-learn.py').read()); print('OK')"
```

Expected output: `OK`

**Step 3: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/bot-learn.py
git commit -m "chore: scaffold bot-learn.py as copy of bot-gpt.py"
```

---

## Task 2: Create `learn_backend.py` with `SessionState` + 5 tool stubs

**Files:**
- Create: `server/learn_backend.py`

**Step 1: Write the file**

```python
# server/learn_backend.py
"""In-memory backing store + tutor tool implementations for Confucius bot.

v1 stores SessionState in a process-local dict keyed by session_id. State is
lost on process recycle. v2 will swap this for a persistent KV store keyed by
user_id; the tool signatures will not change.
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from pipecat.frames.frames import EndTaskFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallParams


# ---------- Data model ---------------------------------------------------


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
    topic: Optional[str] = None
    depth: Optional[str] = None  # "overview" | "deep" | "unknown"
    starting_level: Optional[str] = None  # "novice" | "some_background" | "expert" | "unknown"
    concepts_covered: list[ConceptCovered] = field(default_factory=list)
    marked_for_later: list[MarkedForLater] = field(default_factory=list)


# Process-local store. Keyed by an opaque session_id we generate per run_bot call.
_SESSIONS: dict[str, SessionState] = {}


def get_or_create_session(session_id: str) -> SessionState:
    """Return the state for session_id, creating it if missing."""
    state = _SESSIONS.get(session_id)
    if state is None:
        state = SessionState(session_id=session_id, started_at=datetime.now(timezone.utc))
        _SESSIONS[session_id] = state
    return state


def _duration_minutes(state: SessionState) -> float:
    return (datetime.now(timezone.utc) - state.started_at).total_seconds() / 60.0


# ---------- Tool factory ------------------------------------------------
# Each tool closes over the session_id so the LLM never has to pass it.


def make_tools(session_id: str) -> list:
    """Build the 5 tutor tools for a specific session.

    Returns a list of async tool functions ready to be passed to
    llm.register_direct_function and packed into a ToolsSchema.
    """

    async def set_topic(
        params: FunctionCallParams,
        topic: str,
        depth: str,
        starting_level: str,
    ) -> None:
        """Record the topic + depth + starting knowledge level. Phase 2 → 3.

        Args:
            topic: Plain-English subject. E.g. "quantum mechanics".
            depth: "overview" for big-picture, "deep" for how-it-works,
                "unknown" if the user did not specify.
            starting_level: "novice" | "some_background" | "expert" | "unknown".
        """
        state = get_or_create_session(session_id)
        state.topic = topic
        state.depth = depth
        state.starting_level = starting_level
        logger.info(f"[learn] set_topic: {topic} ({depth}, {starting_level})")
        await params.result_callback(
            f"Topic set: {topic} ({depth}, {starting_level}). Begin teaching."
        )

    async def add_concept_covered(
        params: FunctionCallParams,
        concept: str,
        brief: str,
    ) -> None:
        """Log a concept that was just explained. Phase 3. Silent ack.

        Args:
            concept: Short name. E.g. "superposition".
            brief: 1-sentence summary of what was explained.
        """
        state = get_or_create_session(session_id)
        state.concepts_covered.append(ConceptCovered(concept=concept, brief=brief))
        logger.info(f"[learn] add_concept_covered: {concept}")
        await params.result_callback("Logged.")

    async def mark_for_later(
        params: FunctionCallParams,
        item: str,
        reason: str,
    ) -> None:
        """Flag something to revisit next session. Phase 3. Silent ack.

        Args:
            item: What to come back to. E.g. "quantum entanglement".
            reason: Why it was deferred. E.g. "user wanted to finish current topic first".
        """
        state = get_or_create_session(session_id)
        state.marked_for_later.append(MarkedForLater(item=item, reason=reason))
        logger.info(f"[learn] mark_for_later: {item}")
        await params.result_callback("Noted for next session.")

    async def recap_session(params: FunctionCallParams) -> None:
        """Return structured session state for the LLM to verbalize. Phase 3 → 4."""
        state = get_or_create_session(session_id)
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

    async def end_session(params: FunctionCallParams) -> None:
        """Close the call cleanly. Phase 5. Same pattern as flower-bot's end_call."""
        logger.info(f"[learn] end_session for {session_id}")
        await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
        await params.result_callback("Ending session.")

    return [
        set_topic,
        add_concept_covered,
        mark_for_later,
        recap_session,
        end_session,
    ]
```

**Step 2: Verify it imports cleanly**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "from learn_backend import make_tools, SessionState, get_or_create_session; tools = make_tools('test-session'); assert len(tools) == 5; names = [t.__name__ for t in tools]; print(names)"
```

Expected output: `['set_topic', 'add_concept_covered', 'mark_for_later', 'recap_session', 'end_session']`

**Step 3: Smoke-check state mutation (no test file needed)**

```bash
uv run python -c "
import asyncio
from unittest.mock import AsyncMock, MagicMock
from learn_backend import make_tools, get_or_create_session

async def main():
    sid = 'smoke-test'
    tools = make_tools(sid)
    set_topic = tools[0]
    params = MagicMock()
    params.result_callback = AsyncMock()
    await set_topic(params, topic='quantum mechanics', depth='overview', starting_level='novice')
    state = get_or_create_session(sid)
    assert state.topic == 'quantum mechanics', f'got {state.topic}'
    assert state.depth == 'overview'
    print('OK')

asyncio.run(main())
"
```

Expected output: `OK`

**Step 4: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/learn_backend.py
git commit -m "feat(learn-bot): add learn_backend with SessionState + 5 tutor tools"
```

---

## Task 3: Swap tool list in `bot-learn.py` (drop flower tools, wire in learn tools)

**Files:**
- Modify: `server/bot-learn.py`

**Step 1: Replace `mock_backend` import with `learn_backend`**

In `server/bot-learn.py`, find the line:

```python
from mock_backend import BOUQUETS, KNOWN_CUSTOMERS
```

Replace with:

```python
import uuid
from learn_backend import make_tools, get_or_create_session
```

**Note:** Leave `KNOWN_CUSTOMERS` references inline as dead code per design Section 2 (the `from_number` path is dormant in WebRTC mode). Replace `KNOWN_CUSTOMERS.get(...)` with `{}.get(...)` so the import is gone but the conditional still falls through gracefully. Actually, simpler: just declare a local empty dict.

After the imports block, add:

```python
KNOWN_CUSTOMERS: dict = {}  # placeholder for v2 returning-learner recognition
```

And remove the import of `KNOWN_CUSTOMERS` from `learn_backend` (we did not export it).

**Step 2: Delete the 7 flower tool function definitions**

In `bot-learn.py`, find and **delete entirely** these 7 nested `async def` functions inside `run_bot`:

- `list_bouquets`
- `check_availability`
- `add_to_order`
- `get_order_summary`
- `set_delivery_details`
- `place_order`
- `end_call`

They start with `async def list_bouquets(` near line ~130 and end before the `tool_functions = [` block. The whole block can be removed.

Also delete the `BOUQUETS` references that were inside those functions.

**Step 3: Replace the `tool_functions` definition**

Find:

```python
tool_functions = [
    list_bouquets,
    check_availability,
    add_to_order,
    get_order_summary,
    set_delivery_details,
    place_order,
    end_call,
]
```

Replace with:

```python
session_id = str(uuid.uuid4())
tool_functions = make_tools(session_id)
# Ensure SessionState row exists from the start so recap_session works even
# if the LLM hallucinates calling it pre-Phase-2.
get_or_create_session(session_id)
```

**Step 4: Verify the file still parses and imports**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
import ast
src = open('bot-learn.py').read()
ast.parse(src)
# Confirm BOUQUETS and the deleted flower tool names are gone:
for name in ['BOUQUETS', 'list_bouquets', 'check_availability', 'add_to_order', 'get_order_summary', 'set_delivery_details', 'place_order', 'end_call']:
    assert name not in src, f'still contains {name}'
print('OK')
"
```

Expected output: `OK`

**Step 5: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/bot-learn.py
git commit -m "feat(learn-bot): wire learn_backend tools, drop flower tool functions"
```

---

## Task 4: Rewrite the `system_instruction` in `bot-learn.py`

**Files:**
- Modify: `server/bot-learn.py` (the `system_instruction = (...)` block; original is at line ~316 of `bot-gpt.py`)

**Background:** Pipecat appends the ✓/○/◐ turn-completion framework automatically when `FilterIncompleteUserTurnStrategies` is enabled (which `bot-gpt.py` already does). The bot's `system_instruction` only contains the task-specific persona + style + phase rules. **Do not copy the turn-completion block into the prompt.**

**Step 1: Replace the entire `system_instruction = (...)` literal**

In `server/bot-learn.py`, find the `system_instruction = (` line and the multi-line string that follows, ending at the closing `)`.

Replace the entire block with:

```python
    system_instruction = (
        "You are Confucius, a voice tutor for someone learning on their commute. "
        "You sound like a wise teacher who is good at explaining things — not a "
        "fortune cookie. Your job is to make eight minutes on the bus feel like a "
        "tutoring session that actually sticks.\n\n"
        "How you talk (you are HEARD, not read):\n"
        "- Keep responses to 1–3 short sentences. Longer only when an analogy genuinely needs setup.\n"
        "- Use analogies and concrete examples. Avoid jargon unless you immediately ground it.\n"
        "- No bullet points. No markdown. No \"Firstly… Secondly…\".\n"
        "- Read numbers in words (\"about sixty percent\", not \"60%\").\n"
        "- Use contractions. Fragments are fine.\n"
        "- End most teaching turns with a check: \"does that land?\" or \"want to go deeper or move on?\".\n"
        "- Don't restate what the user just said.\n"
        "- Skip filler openers like \"Great question!\", \"Absolutely!\", \"I'd be happy to\" — go straight to the point.\n"
        "- IMPORTANT: do NOT lean into \"Confucius says…\" aphorism style. Be a wise teacher, not a "
        "fortune cookie. Use modern, plain English.\n\n"
        "Session phases — you move through five phases. Track your own phase. No machine forces it; "
        "be honest with yourself about where you are.\n\n"
        "Phase 1 — Opening. Greet and invite a topic. Say something like \"Hi, I'm Confucius. "
        "What do you want to learn about today?\" Move on when the user names a topic. "
        "If the user says \"I don't know\", suggest two or three prompts and let them pick.\n\n"
        "Phase 2 — Scoping. Calibrate depth and starting knowledge with at most two quick questions, "
        "then call set_topic. If the user wants to dive straight in, call set_topic with "
        "depth=\"unknown\" and starting_level=\"unknown\" and move on.\n\n"
        "Phase 3 — Teaching. Answer questions pedagogically. After every substantive explanation, "
        "call add_concept_covered(concept, brief) silently — the user does not need to know. "
        "If the user says \"remind me to come back to that\", call mark_for_later(item, reason). "
        "If they go off-topic, gently anchor back. If you're uncertain about a fact, say so — "
        "do not confabulate. Procedural questions (\"can you repeat that?\") do not get logged.\n\n"
        "Move on to Phase 4 when the user says \"wrap up\" / \"gotta go\" / \"let's stop\". "
        "Also: around the seven-minute mark, proactively offer recap: \"we've got about a minute — "
        "want me to recap before you have to go?\"\n\n"
        "If the user changes topics mid-session: call recap_session for the old topic first, "
        "then call set_topic again for the new one. Do not merge topics.\n\n"
        "Phase 4 — Recap. Call recap_session once. It returns the structured topic, "
        "concepts_covered, marked_for_later, and duration_minutes. Read that back as a short "
        "spoken summary: \"OK quick recap. We covered X, Y, and Z. You wanted to come back to W "
        "next time. Sound right?\" If the user corrects or adds something, update accordingly and re-recap.\n\n"
        "Phase 5 — Closing. Say a short goodbye (\"Cool. Have a good one.\") AND call end_session in "
        "the same turn. Never call end_session without saying goodbye first.\n\n"
        "Tool decision rules:\n"
        "- set_topic: ONCE per topic. Phase 2 → 3 transition.\n"
        "- add_concept_covered: after every explanation that introduces a real concept. NOT for chitchat.\n"
        "- mark_for_later: when the user says \"come back to that\" or expresses interest in a skipped tangent.\n"
        "- recap_session: ONCE before saying goodbye. Never twice.\n"
        "- end_session: ONLY after delivering a goodbye line.\n\n"
        "Default session is eight minutes. Around the seven-minute mark, proactively offer recap. "
        "Not rigid — if the user wants more time, give them more time.\n\n"
        f"Today is {date.today().strftime('%A, %B %d, %Y')}."
    )
```

**Note:** the original `caller_context` interpolation at the end of `bot-gpt.py`'s system_instruction (line 349) is **removed** — we don't surface it for the tutor bot. (The variable can stay assigned-but-unused in the function; leave it dead per design Section 2.)

**Step 2: Verify the file still parses**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "import ast; ast.parse(open('bot-learn.py').read()); print('OK')"
```

Expected output: `OK`

**Step 3: Verify the prompt is the new one (sanity check)**

```bash
grep -c "Confucius" bot-learn.py
grep -c "Field & Flower" bot-learn.py
```

Expected output:
- `Confucius`: ≥ 4 (multiple references in the new prompt)
- `Field & Flower`: 0 (all flower-bot copy is gone)

**Step 4: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/bot-learn.py
git commit -m "feat(learn-bot): replace system prompt with 5-phase Confucius tutor"
```

---

## Task 5: Local smoke test (Layer 1 from design Section 7)

**Files:**
- Run: `server/bot-learn.py`

**Step 1: Kill any process bound to port 7860**

```bash
lsof -ti tcp:7860 | xargs -r kill -9 2>/dev/null
```

**Step 2: Launch the bot in the background**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run bot-learn.py 2>&1
```

Run this as a background task. Capture the output file path.

**Step 3: Wait for "Uvicorn running on http://localhost:7860"**

Use an `until grep -q "Uvicorn running" <output-file>; do sleep 1; done` background task.

**Step 4: Open the playground UI**

```bash
open http://localhost:7860/client/
```

**Step 5: Manual smoke checklist (user runs this with mic + speakers)**

Walk through one full session out loud. Verify each of:

| # | Check | Pass criterion |
|---|---|---|
| 1 | Greeting fires within ~2 s of clicking Connect | Bot says "Hi, I'm Confucius…" |
| 2 | Scoping happens before teaching | Bot asks about depth or starting level |
| 3 | `set_topic` call appears in server log | `[learn] set_topic: <topic>` line visible |
| 4 | Teaching produces `add_concept_covered` calls | At least one `[learn] add_concept_covered:` per concept |
| 5 | Bot anchors off-topic detours | Say a random non-sequitur — bot redirects |
| 6 | Recap fires once on "wrap up" | `[learn] recap_session:` log + spoken summary |
| 7 | `end_session` closes the call | Connection drops; `[learn] end_session for` log |

**Step 6: Stop the bot**

Stop the background task (TaskStop or `lsof -ti tcp:7860 | xargs -r kill -9`).

**Step 7: If any check failed, iterate**

Loop back to Task 4 (prompt) or Task 2/3 (tools/wiring) until all 7 pass. Each iteration: edit, restart, retest. Do not deploy yet.

**Step 8: Commit any prompt fixes**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/bot-learn.py server/learn_backend.py
git commit -m "fix(learn-bot): prompt/tool fixes from local smoke test" \
  # only run this if there were actually changes; otherwise skip
```

---

## Task 6: Update `Dockerfile` + `pcc-deploy.toml` for `learn-bot`

**Files:**
- Modify: `server/Dockerfile`
- Modify: `server/pcc-deploy.toml`

**Step 1: Update `Dockerfile`**

Currently the Dockerfile is configured to deploy `bot-gpt.py` as `bot.py` (we changed this earlier in this session). Confirm the current state:

```bash
grep "COPY" /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server/Dockerfile
```

The current line is `COPY ./bot-gpt.py bot.py`. **We need to switch it to `bot-learn.py`, but only for the `learn-bot` deployment.** Since the Dockerfile is shared with `flower-bot`, we have two options:

- **Option A (recommended):** Edit the Dockerfile in-place for `learn-bot` deployment. Accept that this temporarily breaks redeploys of `flower-bot`. We are not actively iterating on flower-bot.
- Option B: Add a build-arg switch. Out of scope for v1; over-engineering.

Take Option A. Edit `server/Dockerfile`:

```
COPY ./bot-learn.py bot.py
COPY ./learn_backend.py learn_backend.py
COPY ./mock_backend.py mock_backend.py
```

The third line keeps the flower-bot file present in the image so the existing `flower-bot` deployment is unaffected if Pipecat Cloud reuses any cached layer (defensive; harmless).

Actually — keep this simple. Replace the two `COPY` lines with:

```
COPY ./bot-learn.py bot.py
COPY ./learn_backend.py learn_backend.py
```

`mock_backend.py` is not needed for learn-bot. Old flower-bot images on Pipecat Cloud remain unaffected because they were built from a prior commit's Dockerfile.

**Step 2: Update `pcc-deploy.toml`**

Replace the contents with:

```toml
agent_name = "learn-bot"
secret_set = "learn-bot-secrets"
agent_profile = "agent-1x"

[krisp_viva]
	audio_filter = "tel"

[scaling]
	min_agents = 1
```

The only changes from the current flower-bot config: `agent_name` and `secret_set`.

**Step 3: Verify**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
grep -E "(COPY|agent_name|secret_set)" Dockerfile pcc-deploy.toml
```

Expected output (order may vary):
```
Dockerfile:COPY ./bot-learn.py bot.py
Dockerfile:COPY ./learn_backend.py learn_backend.py
pcc-deploy.toml:agent_name = "learn-bot"
pcc-deploy.toml:secret_set = "learn-bot-secrets"
```

**Step 4: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/Dockerfile server/pcc-deploy.toml
git commit -m "chore(learn-bot): point Dockerfile + pcc-deploy.toml at learn-bot"
```

---

## Task 7: Upload `learn-bot-secrets` to Pipecat Cloud

**Files:**
- Read: `server/.env`

**Step 1: Confirm `.env` has the three required keys with non-empty values**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
from pathlib import Path
import re
env = Path('.env').read_text()
required = ['OPENAI_API_KEY', 'GRADIUM_API_KEY', 'GRADIUM_VOICE_ID']
for k in required:
    m = re.search(rf'^{k}=(.+)$', env, re.M)
    assert m, f'{k} missing'
    assert m.group(1).strip(), f'{k} empty'
    print(f'{k}: <set, {len(m.group(1))} chars>')
"
```

Expected output: three lines, each `<key>: <set, N chars>` with N > 10.

If any are missing or empty, stop and fix `.env` (use the Python-helper pattern from earlier in this session, since direct writes to `.env` are blocked by policy).

**Step 2: Upload secrets to Pipecat Cloud**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud secrets set learn-bot-secrets --file .env --skip
```

Expected output: success banner with "Secret set 'learn-bot-secrets' created successfully in us-west".

**Step 3: Verify**

```bash
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud secrets list 2>&1 | grep learn-bot-secrets
```

Expected: a line containing `learn-bot-secrets`.

---

## Task 8: Deploy to Pipecat Cloud

**Files:**
- Uses: `server/Dockerfile`, `server/pcc-deploy.toml`, `server/bot-learn.py`, `server/learn_backend.py`

**Step 1: Trigger the cloud build + deploy**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud deploy --yes
```

Run in the background — cloud builds take ~2-3 minutes. Capture the output file path.

**Step 2: Wait for completion**

The bg task exits when the CLI returns (success or failure). Read its output and look for:

- ✅ Success: a banner with "Agent deployment 'learn-bot' is ready"
- ❌ Failure: a banner with "Build failed" and a build ID

**Step 3: On failure, fetch the build log**

```bash
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud build logs <build-id> 2>&1 | tail -80
```

Common failure modes from this session's earlier deploys:
- `COPY ./bot.py bot.py: not found` → wrong filename in Dockerfile
- `Empty key or value found in .env` → empty `.env` lines need stripping before secrets upload

Fix and re-run Step 1.

**Step 4: On success, verify agent status**

```bash
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud agent status learn-bot 2>&1 | head -25
```

Expected: a status panel with "Ready · Active · N agents".

**Step 5: Commit any deploy-config fixes** (skip if no edits were needed)

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add -A server/
git commit -m "fix(learn-bot): deploy config corrections"
```

---

## Task 9: Sandbox smoke test on phone (Layer 2 from design Section 7)

**Files:** none (manual test)

**Step 1: Open the sandbox URL on iPhone Safari**

```
https://pipecat.daily.co/0530id/agents/learn-bot/sandbox
```

(Replace `0530id` with the actual org ID from `pc cloud organizations list` if it differs.)

**Step 2: Connect with earbuds and mic permission granted**

Tap "Connect". Grant mic when prompted.

**Step 3: Run the 6-check manual smoke checklist (in motion)**

Walk around the block (or pace indoors). Full session ~8 minutes.

| # | Check | Pass criterion |
|---|---|---|
| 1 | Sandbox UI usable on iOS Safari | Buttons tappable; Connect works |
| 2 | Audio works both directions in earbuds | No echo |
| 3 | Background noise doesn't break VAD | Bot doesn't get "stuck" listening when you're not speaking |
| 4 | Time-to-first-audio ≤ ~1 s after you finish speaking | Feels natural, not laggy |
| 5 | Full 5-phase flow completes | Opening → Scoping → Teaching → Recap → Closing |
| 6 | No mid-session disconnects | Walk under bridges / into stores; connection survives |

**Step 4: Tail the deployed bot logs in parallel**

In a separate terminal, while testing:

```bash
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud agent logs learn-bot --follow
```

Watch for the `[learn] set_topic`, `[learn] add_concept_covered`, `[learn] recap_session`, `[learn] end_session` lines that confirm the 5 tools fired.

**Step 5: If any check fails, decide**

- Prompt issue (bot didn't recap, skipped scoping, drifted to aphorism style) → loop to Task 10 (iteration)
- Infra issue (no audio, disconnects, no greeting) → check Pipecat Cloud logs; usually a secrets issue (`learn-bot-secrets` missing or wrong key)

---

## Task 10: Prompt iteration loop

**Files:**
- Modify: `server/bot-learn.py` (just the `system_instruction` block)

**Step 1: Identify the specific behavior to fix**

Common categories from prior bot-prompt iteration:

| Symptom | Likely fix |
|---|---|
| Bot is too verbose (4+ sentences) | Add "MAX 2 sentences" constraint to How-you-talk block |
| Bot drifts to "Confucius says…" tone | Strengthen the persona safeguard ("NEVER use ancient-sage diction or proverbs") |
| Bot forgets to call `add_concept_covered` | Move the tool-call instruction earlier in the prompt + add an explicit example |
| Bot recaps too early | Tighten Phase 3 → 4 trigger ("ONLY when user explicitly signals stop") |
| Bot calls `end_session` too eagerly | Add: "If you said goodbye and the user immediately responds, do NOT call end_session; resume teaching" |
| Bot is too formal | Add "Casual, like a smart friend" to persona |

**Step 2: Edit the prompt**

Make a targeted change to `system_instruction` in `bot-learn.py`. **Do not refactor the whole prompt; surgical edits only.**

**Step 3: Re-deploy**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud deploy --yes
```

(Background task; ~2-3 min build.)

**Step 4: Re-test (back to Task 9 Step 3)**

Run the same 6-check checklist again. If new regressions appear, undo and try a different fix.

**Step 5: Commit each kept change**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/bot-learn.py
git commit -m "tune(learn-bot): <specific change, e.g. tighten Phase 3 trigger>"
```

**Step 6: Stop iterating when**

All 7 Layer 1 checks AND all 6 Layer 2 checks pass on the same session. That is v1 done.

---

## Definition of done

- `bot-learn.py` + `learn_backend.py` committed to `main` branch
- `Dockerfile` + `pcc-deploy.toml` configured for `learn-bot`
- `learn-bot-secrets` uploaded to Pipecat Cloud
- `learn-bot` agent shows "Ready" status in `pc cloud agent status`
- A real 8-minute conversation completed on iPhone in Safari with earbuds, full 5-phase flow, structured tool calls visible in logs

---

## Out of scope (do NOT do in this plan)

| Item | Status | Where it lives |
|---|---|---|
| Cekura test scenarios | v1.5 separate plan | Design doc Section 9 |
| Persistent memory / KV store | v2 | Design doc Section 9 |
| iOS native client | v2 | Design doc Section 9 |
| 3rd-party content (Khan, Coursera) | v2 | Design doc Section 9 |
| Unit tests on `learn_backend.py` | Skipped by design | Design Section 7 |
| LLM correctness eval | Needs ground truth; deferred | Design Section 7 |
| Frontend customization | Sandbox UI is enough | Design Section 6 |
| Twilio phone number | Voice browser is enough for v1 | Design Section 1 |

---

## Appendix — File reference

After this plan, the repo state should be:

```
server/
├── bot-gpt.py          # unchanged
├── bot-nemotron.py     # unchanged
├── mock_backend.py     # unchanged
├── bot-learn.py        # NEW — fork of bot-gpt.py with tutor prompt + tools
├── learn_backend.py    # NEW — SessionState + 5 tools
├── Dockerfile          # MODIFIED — COPY bot-learn.py + learn_backend.py
├── pcc-deploy.toml     # MODIFIED — agent_name=learn-bot, secret_set=learn-bot-secrets
├── .env                # MODIFIED earlier — has OPENAI_API_KEY, GRADIUM_API_KEY, GRADIUM_VOICE_ID
└── pyproject.toml      # ALREADY MODIFIED — includes pipecat-ai[daily,...] extra
```
