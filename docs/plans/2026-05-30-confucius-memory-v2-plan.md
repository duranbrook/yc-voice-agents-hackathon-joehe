# Confucius Memory v2 — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make `learn-bot` remember individual learners across sessions via Supabase + a custom Google-Sign-In web client, enabling continuity ("welcome back") and pickup-where-you-left UX.

**Architecture:** Replace the Pipecat sandbox URL with a custom client (Google Sign-In → Pipecat Cloud session-mint with user_id in body). Bot reads `runner_args.body.user_id`, loads memory from Supabase (sessions + learner_summary tables), injects a memory-aware block into `system_instruction`. At session end, persist to Supabase fire-and-forget alongside the existing Cekura POST.

**Tech Stack:** Python 3.13 + `uv`, Pipecat 1.3.0, `supabase-py>=2.0`, Postgres on Supabase, vanilla HTML+JS client with Google Identity Services + `@daily-co/daily-js`, Vercel for client hosting.

**Design doc:** `docs/plans/2026-05-30-confucius-memory-v2-design.md`

**Scope explicitly OUT of this plan:**
- Server-side JWT verification (v3 — v2 trusts the client claim)
- Cross-reference during a session
- Personalization (depth/pace adaptation)
- Native iOS app
- Memory-deletion UI / "forget me" flow
- RLS policies on bot-only tables (client never touches them)
- Multi-device concurrent session handoff
- Unit tests on `supabase_client.py` (trivial CRUD)

**Working directory:** `/Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe`

**Conventions:**
- All `pc cloud` commands prefixed with `env -u GH_TOKEN -u GITHUB_TOKEN` (env tokens override gh keyring).
- All `.env` writes go through Python helper (direct shell writes blocked by permission policy).
- Commit after every task; conventional commits.
- "Human-gated" tasks marked clearly — they require browser auth or external dashboards.

---

## Task 1: Supabase project setup (HUMAN-GATED)

**Files:** none in repo; output goes into `.env` via Task 4.

**Step 1: Create Supabase project**

Open `https://supabase.com/dashboard` → **New Project**.
- Org: any (your default is fine)
- Name: `confucius-memory`
- Database password: generate + save somewhere safe
- Region: closest to Pipecat Cloud's `us-west` (e.g. `us-west-1`)

Click **Create**. Wait ~2 minutes for provisioning.

**Step 2: Enable Google OAuth provider**

In the new project: **Authentication → Providers → Google → Enable**.

You'll need a Google `client_id` + `client_secret` — get those from Task 2. Come back here once Task 2 produces them.

**Step 3: Run schema SQL**

In the new project: **SQL Editor → New query**. Paste:

```sql
-- Per-session detail
create table sessions (
    id            uuid primary key default gen_random_uuid(),
    user_id       text not null,
    started_at    timestamptz not null,
    ended_at      timestamptz,
    payload       jsonb not null,
    created_at    timestamptz default now()
);
create index idx_sessions_user_started on sessions (user_id, started_at desc);

-- Rolled-up per-user summary
create table learner_summary (
    user_id              text primary key,
    email                text,
    name                 text,
    last_session_at      timestamptz,
    last_phase_reached   text,
    total_sessions       int not null default 0,
    total_minutes        int not null default 0,
    concepts_lifetime    jsonb not null default '[]'::jsonb,
    marked_for_later     jsonb not null default '[]'::jsonb,
    depth_preference     text,
    updated_at           timestamptz default now()
);
```

Click **Run**. Expected: `Success. No rows returned.`

**Step 4: Collect connection details**

From **Project Settings → API**:
- Copy `URL` → this will be `SUPABASE_URL`
- Copy `service_role` key (under "Project API keys") → this will be `SUPABASE_SERVICE_KEY`

⚠️ The `service_role` key bypasses RLS. Treat it like a database password — never commit, never expose to client JS.

**Output**: `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` strings, ready for `.env`.

No commit (manual setup).

---

## Task 2: Google OAuth client setup (HUMAN-GATED)

**Files:** none in repo; output goes into `.env` via Task 4.

**Step 1: Create OAuth client in Google Cloud Console**

Open `https://console.cloud.google.com/apis/credentials`.

- Select or create a project (e.g., `confucius-tutor`)
- Click **Create Credentials → OAuth client ID**
- Application type: **Web application**
- Name: `Confucius Voice Tutor`
- Authorized JavaScript origins:
  - `http://localhost:3000` (for local client dev)
  - `https://<your-vercel-domain>.vercel.app` (add after Task 12 deploy)
- Authorized redirect URIs:
  - `http://localhost:3000`
  - `https://<your-vercel-domain>.vercel.app`
  - `https://<your-supabase-project>.supabase.co/auth/v1/callback` (Supabase needs this for OAuth)

Click **Create**.

**Step 2: Collect credentials**

Copy:
- `Client ID` → this will be `GOOGLE_CLIENT_ID` (safe to expose in client JS)
- `Client secret` → save for Supabase Google provider config (paste back into Task 1 Step 2)

**Step 3: Back to Supabase**

Return to Supabase **Authentication → Providers → Google** and paste:
- `Client ID` (from above)
- `Client secret` (from above)
- Click **Save**

Supabase Google OAuth is now configured.

**Output**: `GOOGLE_CLIENT_ID` string, ready for `.env`.

No commit (manual setup).

---

## Task 3: Add new keys to `.env`

**Files:**
- Modify: `server/.env` (gitignored)

**Step 1: Append the 3 new keys**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
python3 -c "
import os
p = os.path.join(os.getcwd(), '.env')
with open(p) as f: txt = f.read()
additions = []
if 'SUPABASE_URL=' not in txt: additions.append('SUPABASE_URL=<PASTE_FROM_TASK_1_STEP_4>')
if 'SUPABASE_SERVICE_KEY=' not in txt: additions.append('SUPABASE_SERVICE_KEY=<PASTE_FROM_TASK_1_STEP_4>')
if 'GOOGLE_CLIENT_ID=' not in txt: additions.append('GOOGLE_CLIENT_ID=<PASTE_FROM_TASK_2_STEP_2>')
if additions:
    if not txt.endswith('\n'): txt += '\n'
    txt += '\n# Memory v2 — Supabase + Google OAuth\n' + '\n'.join(additions) + '\n'
    with open(p, 'w') as f: f.write(txt)
    print('appended:', additions)
else:
    print('all 3 keys already present')
"
```

**Step 2: Replace placeholders with real values**

The implementer will need to manually edit `.env` (via Python helper) to replace the 3 `<PASTE_FROM_…>` placeholders with the real values from Tasks 1 and 2.

```bash
python3 -c "
import os, re
p = '.env'
with open(p) as f: txt = f.read()
# Example substitutions — implementer fills these in:
# txt = re.sub(r'^SUPABASE_URL=.*\$', 'SUPABASE_URL=https://abc.supabase.co', txt, flags=re.M)
# txt = re.sub(r'^SUPABASE_SERVICE_KEY=.*\$', 'SUPABASE_SERVICE_KEY=eyJ...', txt, flags=re.M)
# txt = re.sub(r'^GOOGLE_CLIENT_ID=.*\$', 'GOOGLE_CLIENT_ID=123-abc.apps.googleusercontent.com', txt, flags=re.M)
print('NOTE: subagent should pause here and request the actual values from the controller')
"
```

**The implementer subagent must NOT proceed past this step without the actual values from the controller.** The controller (Claude) will provide the values out-of-band.

**Step 3: Verify**

```bash
uv run python -c "
from pathlib import Path
import re
env = Path('.env').read_text()
for k in ['SUPABASE_URL', 'SUPABASE_SERVICE_KEY', 'GOOGLE_CLIENT_ID']:
    m = re.search(rf'^{k}=(.+)\$', env, re.M)
    assert m, f'{k} missing'
    v = m.group(1).strip()
    assert v and not v.startswith('<PASTE'), f'{k} still placeholder'
    print(f'{k}: <set, {len(v)} chars>')
"
```

Expected: 3 lines each `<set, N chars>` with N reasonable (URL ~40 chars, key ~200 chars, client_id ~70 chars).

No commit (`.env` is gitignored).

---

## Task 4: Add `supabase` Python dep

**Files:**
- Modify: `server/pyproject.toml`
- Modify: `server/uv.lock` (auto-regenerated)

**Step 1: Add `supabase` to dependencies**

Edit `server/pyproject.toml`. The current `dependencies` block is:

```toml
dependencies = [
    "pipecat-ai[daily,gradium,openai,runner,silero,webrtc,websocket]>=1.3.0",
    "pipecatcloud>=0.7.1",
]
```

Change to:

```toml
dependencies = [
    "pipecat-ai[daily,gradium,openai,runner,silero,webrtc,websocket]>=1.3.0",
    "pipecatcloud>=0.7.1",
    "supabase>=2.0",
]
```

**Step 2: Update lock file**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv sync 2>&1 | tail -5
```

Expected: output mentions `+ supabase==2.x.x` (or similar) in the install list.

**Step 3: Verify import**

```bash
uv run python -c "from supabase import create_client; print('OK: supabase imports cleanly')"
```

Expected: `OK: supabase imports cleanly`

**Step 4: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/pyproject.toml server/uv.lock
git commit -m "chore(memory-v2): add supabase>=2.0 python dep"
```

---

## Task 5: Create `server/supabase_client.py`

**Files:**
- Create: `server/supabase_client.py`

**Step 1: Write the file**

```python
# server/supabase_client.py
"""Supabase client for memory v2.

Persists per-session detail to `sessions` table and rolls up per-user stats
in `learner_summary`. Loaded by `learn_backend.py` from `bot-learn.py` at
session start (read) and at session end (write).

All Supabase operations are best-effort: failures log warnings but never
raise. Memory is non-critical-path; the bot must function with or without it.
"""

from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime, timezone, timedelta
from typing import TYPE_CHECKING, Any, Optional

from loguru import logger

if TYPE_CHECKING:
    from learn_backend import SessionState

try:
    from supabase import create_client, Client
    _SUPABASE_AVAILABLE = True
except ImportError:
    _SUPABASE_AVAILABLE = False


_client: Optional["Client"] = None


def _get_client() -> Optional["Client"]:
    """Return a memoized supabase client, or None if not configured."""
    global _client
    if _client is not None:
        return _client
    if not _SUPABASE_AVAILABLE:
        logger.warning("[memory] supabase-py not installed — memory disabled")
        return None
    url = os.environ.get("SUPABASE_URL", "").strip()
    key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
    if not url or not key:
        logger.warning("[memory] SUPABASE_URL / SUPABASE_SERVICE_KEY not set — memory disabled")
        return None
    try:
        _client = create_client(url, key)
        return _client
    except Exception as e:
        logger.error(f"[memory] failed to init Supabase client: {e!r}")
        return None


# ---------- Read path: load_memory ---------------------------------------


def _classify_recency(last_session_at: Optional[datetime], last_phase: Optional[str]) -> str:
    """Determine the recency_bucket for prompt-shaping."""
    if not last_session_at:
        return "new"
    now = datetime.now(timezone.utc)
    elapsed = now - last_session_at
    if last_phase in ("recap", "closing"):
        if elapsed < timedelta(days=7):
            return "clean_recent"
        return "clean_stale"
    if last_phase in ("teaching", "scoping"):
        if elapsed < timedelta(hours=24):
            return "mid_topic_recent"
        if elapsed < timedelta(days=7):
            return "mid_topic_stale"
    return "clean_stale"


async def load_memory(user_id: str) -> dict:
    """Fetch the learner_summary row + most recent session for this user.

    Returns:
        {
            "recency_bucket": "new" | "clean_recent" | "clean_stale" |
                              "mid_topic_recent" | "mid_topic_stale",
            "summary": dict | None,
            "last_session": dict | None,
        }
    """
    client = _get_client()
    if client is None or not user_id:
        return {"recency_bucket": "new", "summary": None, "last_session": None}

    try:
        # Fetch summary
        sr = client.table("learner_summary").select("*").eq("user_id", user_id).execute()
        summary = sr.data[0] if sr.data else None

        # Fetch most recent session
        lr = (
            client.table("sessions")
            .select("*")
            .eq("user_id", user_id)
            .order("started_at", desc=True)
            .limit(1)
            .execute()
        )
        last = lr.data[0] if lr.data else None

        last_at = None
        last_phase = None
        if last:
            # started_at + ended_at come back as ISO strings from Postgrest
            iso = last.get("ended_at") or last.get("started_at")
            if iso:
                last_at = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            last_phase = (last.get("payload") or {}).get("phase_reached")

        return {
            "recency_bucket": _classify_recency(last_at, last_phase),
            "summary": summary,
            "last_session": last,
        }
    except Exception as e:
        logger.warning(f"[memory] load_memory failed for {user_id}: {e!r}")
        return {"recency_bucket": "new", "summary": None, "last_session": None}


# ---------- Write path: persist_session ----------------------------------


def _session_to_payload(state: "SessionState") -> dict:
    """Serialize SessionState into a JSON-safe dict for the sessions.payload column."""

    def iso(dt: Optional[datetime]) -> Optional[str]:
        return dt.isoformat() if dt else None

    concepts = []
    for c in state.concepts_covered:
        concepts.append({
            "concept": c.concept,
            "brief": c.brief,
            "started_at": iso(c.started_at),
            "ended_at": iso(c.ended_at),
        })
    return {
        "session_id": state.session_id,
        "started_at": iso(state.started_at),
        "ended_at": iso(state.ended_at),
        "topic": state.topic,
        "depth": state.depth,
        "starting_level": state.starting_level,
        "concepts_covered": concepts,
        "marked_for_later": [asdict(m) for m in state.marked_for_later],
        "transcript": [
            {"role": t.role, "content": t.content, "timestamp": iso(t.timestamp)}
            for t in state.transcript
        ],
        "phase_reached": state.phase_reached,
        "end_reason": state.end_reason,
    }


def _merge_concepts_lifetime(existing: list, new: list) -> list:
    """Append new concepts (by `concept` name) that aren't already in existing."""
    seen = {item["concept"] for item in existing if isinstance(item, dict) and "concept" in item}
    merged = list(existing)
    for c in new:
        name = c.get("concept")
        if name and name not in seen:
            merged.append({
                "concept": name,
                "brief": c.get("brief", ""),
                "first_seen_at": c.get("started_at"),
            })
            seen.add(name)
    return merged


async def persist_session(state: "SessionState") -> None:
    """INSERT into sessions; UPSERT learner_summary. Never raises."""
    client = _get_client()
    if client is None:
        return
    if not state.user_id or state.user_id == "default_user":
        logger.info(f"[memory] skipping persist for unauthenticated session {state.session_id}")
        return

    try:
        payload = _session_to_payload(state)
        # 1. Insert per-session row
        client.table("sessions").insert({
            "user_id": state.user_id,
            "started_at": payload["started_at"],
            "ended_at": payload["ended_at"],
            "payload": payload,
        }).execute()

        # 2. Upsert summary
        # Fetch current to merge concepts_lifetime correctly
        sr = client.table("learner_summary").select("*").eq("user_id", state.user_id).execute()
        existing = sr.data[0] if sr.data else None

        duration_minutes = 0
        if state.ended_at and state.started_at:
            duration_minutes = int((state.ended_at - state.started_at).total_seconds() / 60)

        if existing:
            merged_concepts = _merge_concepts_lifetime(
                existing.get("concepts_lifetime") or [],
                payload["concepts_covered"],
            )
            client.table("learner_summary").update({
                "last_session_at": payload["ended_at"] or payload["started_at"],
                "last_phase_reached": state.phase_reached,
                "total_sessions": (existing.get("total_sessions") or 0) + 1,
                "total_minutes": (existing.get("total_minutes") or 0) + duration_minutes,
                "concepts_lifetime": merged_concepts,
                "marked_for_later": payload["marked_for_later"],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }).eq("user_id", state.user_id).execute()
        else:
            client.table("learner_summary").insert({
                "user_id": state.user_id,
                "last_session_at": payload["ended_at"] or payload["started_at"],
                "last_phase_reached": state.phase_reached,
                "total_sessions": 1,
                "total_minutes": duration_minutes,
                "concepts_lifetime": _merge_concepts_lifetime([], payload["concepts_covered"]),
                "marked_for_later": payload["marked_for_later"],
            }).execute()

        logger.info(f"[memory] persisted session {state.session_id} for user {state.user_id}")
    except Exception as e:
        logger.warning(f"[memory] persist_session failed for {state.session_id}: {e!r}")
```

**Step 2: Smoke-check import**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
from supabase_client import load_memory, persist_session, _classify_recency
from datetime import datetime, timezone, timedelta
# Test the recency classifier with no external calls
now = datetime.now(timezone.utc)
assert _classify_recency(None, None) == 'new'
assert _classify_recency(now - timedelta(hours=1), 'recap') == 'clean_recent'
assert _classify_recency(now - timedelta(days=10), 'recap') == 'clean_stale'
assert _classify_recency(now - timedelta(hours=2), 'teaching') == 'mid_topic_recent'
assert _classify_recency(now - timedelta(days=3), 'teaching') == 'mid_topic_stale'
print('OK: supabase_client + recency classification verified')
"
```

Expected: `OK: supabase_client + recency classification verified`

**Step 3: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/supabase_client.py
git commit -m "feat(memory-v2): add supabase_client with load_memory + persist_session"
```

---

## Task 6: Add `user_id` field to `SessionState` (Task 3 backfill)

**Files:**
- Modify: `server/learn_backend.py`

**Step 1: Verify `user_id` field**

The v1 dataclass already includes `user_id: str = "default_user"` per the Cekura design. Verify:

```bash
grep -n "user_id" /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server/learn_backend.py | head -5
```

Expected: `user_id: str = "default_user"` line present in `SessionState`.

If missing, add it as a default-`"default_user"` field on `SessionState`.

**Step 2: Update `get_or_create_session` to accept an optional `user_id`**

Find the function `get_or_create_session(session_id)` in `learn_backend.py`. Change signature to:

```python
def get_or_create_session(session_id: str, user_id: str = "default_user") -> SessionState:
    """Return the state for session_id, creating it if missing. If creating,
    associate with user_id (from runner_args.body or 'default_user' for anon)."""
    state = _SESSIONS.get(session_id)
    if state is None:
        state = SessionState(
            session_id=session_id,
            user_id=user_id,
            started_at=datetime.now(timezone.utc),
        )
        _SESSIONS[session_id] = state
    return state
```

**Step 3: Verify**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
from learn_backend import get_or_create_session
s = get_or_create_session('t1', user_id='google-sub-123')
assert s.user_id == 'google-sub-123'
s2 = get_or_create_session('t2')
assert s2.user_id == 'default_user'
print('OK: user_id wiring verified')
"
```

Expected: `OK: user_id wiring verified`

**Step 4: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/learn_backend.py
git commit -m "feat(memory-v2): get_or_create_session accepts user_id"
```

---

## Task 7: Add `load_memory` + `build_memory_prompt_block` to `learn_backend.py`

**Files:**
- Modify: `server/learn_backend.py`

**Step 1: Add memory-prompt helper**

In `server/learn_backend.py`, add this helper function (placement: after the dataclasses, before `make_tools`):

```python
def build_memory_prompt_block(memory: dict) -> str:
    """Compose the markdown block that gets appended to system_instruction
    when a returning user is detected. The LLM uses this to shape its opening turn.

    `memory` is the dict returned by supabase_client.load_memory().
    """
    bucket = memory.get("recency_bucket", "new")
    summary = memory.get("summary") or {}
    last = memory.get("last_session") or {}
    last_payload = (last.get("payload") or {}) if last else {}

    name = summary.get("name") or "the learner"
    last_topic = last_payload.get("topic") or "an unspecified topic"
    last_phase = last_payload.get("phase_reached") or "unknown"
    concepts_lifetime = summary.get("concepts_lifetime") or []
    marked_for_later = summary.get("marked_for_later") or []
    last_concepts = last_payload.get("concepts_covered") or []

    # Compose human-readable bullets for the LLM
    concepts_short = ", ".join(
        c.get("concept", "?") for c in (last_concepts[:3] if isinstance(last_concepts, list) else [])
    ) or "no concepts yet"
    marked_short = ", ".join(
        m.get("item", "?") for m in (marked_for_later[:2] if isinstance(marked_for_later, list) else [])
    ) or "nothing"

    # Find any concept whose ended_at is None (the "open" concept in mid-topic disconnects)
    open_concept = None
    if isinstance(last_concepts, list):
        for c in reversed(last_concepts):
            if c.get("ended_at") is None:
                open_concept = c.get("concept")
                break

    rule_map = {
        "new": (
            "Standard opening — no memory context. Say: "
            "\"Hi, I'm Confucius. What do you want to learn about today?\""
        ),
        "clean_recent": (
            f"Last session was clean (recap delivered) and recent. Open with: "
            f"\"Welcome back. Last time we covered {concepts_short}; you wanted to come back to "
            f"{marked_short}. Pick up there, or something new?\""
        ),
        "clean_stale": (
            f"Last session was clean but >= 7 days ago. Open lightly: "
            f"\"Welcome back — it's been a minute. Want to revisit {last_topic} or pick something new?\""
        ),
        "mid_topic_recent": (
            f"Last session disconnected mid-topic ({last_topic}, phase={last_phase}) within 24h. Open with: "
            f"\"Welcome back. We were just getting to {open_concept or last_topic} — want to keep going?\""
        ),
        "mid_topic_stale": (
            f"Last session disconnected mid-topic ({last_topic}) more than a day ago. Open with: "
            f"\"Welcome back. We were on {last_topic} last time but didn't finish "
            f"{open_concept or 'it'}. Resume or new topic?\""
        ),
    }
    opening_rule = rule_map.get(bucket, rule_map["new"])

    return (
        "## Memory context\n\n"
        f"You are talking to **{name}** (returning learner).\n\n"
        f"**Recency bucket**: `{bucket}`\n"
        f"- Total prior sessions: {summary.get('total_sessions', 0)}\n"
        f"- Total time learning with you: {summary.get('total_minutes', 0)} minutes\n"
        f"- Last topic: {last_topic} (phase reached: {last_phase})\n"
        f"- Concepts covered last time: {concepts_short}\n"
        f"- Marked for later: {marked_short}\n"
        f"- Lifetime concepts: {len(concepts_lifetime)}\n\n"
        f"**Opening rule (CRITICAL — use this for your first turn)**: {opening_rule}\n\n"
        "Use the structured data above to fill any placeholders. Speak the opening line naturally — "
        "do NOT read out the bracketed labels. After the opening, proceed through the 5-phase tutor flow "
        "as usual.\n"
    )
```

**Step 2: Smoke-test all 5 recency buckets**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
from learn_backend import build_memory_prompt_block

# New user
b = build_memory_prompt_block({'recency_bucket': 'new'})
assert 'Standard opening' in b and 'no memory context' in b

# Clean recent
b = build_memory_prompt_block({
    'recency_bucket': 'clean_recent',
    'summary': {'name': 'Joe', 'total_sessions': 3, 'total_minutes': 20,
                'concepts_lifetime': [{'concept': 'WebRTC'}],
                'marked_for_later': [{'item': 'SDP details'}]},
    'last_session': {'payload': {'topic': 'WebRTC', 'phase_reached': 'recap',
                                  'concepts_covered': [{'concept': 'WebRTC overview'}]}},
})
assert 'Welcome back' in b and 'WebRTC overview' in b and 'SDP details' in b and 'Joe' in b

# Mid-topic recent
b = build_memory_prompt_block({
    'recency_bucket': 'mid_topic_recent',
    'summary': {'name': 'Joe', 'total_sessions': 1, 'total_minutes': 5},
    'last_session': {'payload': {'topic': 'quantum mechanics', 'phase_reached': 'teaching',
                                  'concepts_covered': [
                                      {'concept': 'superposition', 'ended_at': '2026-05-30T...'},
                                      {'concept': 'wavefunction', 'ended_at': None}
                                  ]}},
})
assert 'wavefunction' in b and 'keep going' in b

print('OK: build_memory_prompt_block verified for all buckets')
"
```

Expected: `OK: build_memory_prompt_block verified for all buckets`

**Step 3: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/learn_backend.py
git commit -m "feat(memory-v2): add build_memory_prompt_block for recency-aware openings"
```

---

## Task 8: Wire memory load + persist in `bot-learn.py`

**Files:**
- Modify: `server/bot-learn.py`

**Step 1: Read `user_id`/`email`/`name` from `runner_args.body`**

At the entry of `run_bot` (line ~120 of `bot-learn.py`), add:

```python
async def run_bot(
    transport: BaseTransport,
    from_number: str | None = None,
    runner_args: RunnerArguments | None = None,  # NEW param
    **transport_overrides: dict,
):
    ...
```

Wait — `run_bot` is called from the per-transport `case` blocks inside the `bot()` function (around line 449). The transport switch is where `runner_args` lives. Pass `user_id` through explicitly instead of mutating the signature.

Better approach: extract `user_id`/`email`/`name` inside the `bot()` function (where `runner_args` is in scope) and pass to `run_bot` as kwargs.

Find the `async def bot(runner_args: RunnerArguments)` function. Near the top (line ~437), add:

```python
async def bot(runner_args: RunnerArguments):
    """Main bot entry point."""
    user_id = None
    email = None
    name = None
    if hasattr(runner_args, "body") and isinstance(runner_args.body, dict):
        user_id = runner_args.body.get("user_id")
        email = runner_args.body.get("email")
        name = runner_args.body.get("name")
    # ... existing code continues
```

Then at the end of `bot()` where `run_bot(transport, ...)` is called, pass these through:

```python
await run_bot(
    transport,
    from_number=from_number,
    user_id=user_id,
    email=email,
    name=name,
    **transport_overrides,
)
```

And update `run_bot`'s signature:

```python
async def run_bot(
    transport: BaseTransport,
    from_number: str | None = None,
    user_id: str | None = None,
    email: str | None = None,
    name: str | None = None,
    **transport_overrides,
):
    ...
```

**Step 2: Use `user_id` when creating SessionState**

Inside `run_bot`, find the existing session_id generation:

```python
session_id = str(uuid.uuid4())
tool_functions = make_tools(session_id)
get_or_create_session(session_id)
```

Replace with:

```python
session_id = str(uuid.uuid4())
tool_functions = make_tools(session_id)
get_or_create_session(session_id, user_id=user_id or "default_user")
```

**Step 3: Load memory + inject prompt block**

Right after `system_instruction = (...)` (the existing prompt) is built and just before the `llm = OpenAIResponsesLLMService(...)` instantiation, add:

```python
# Memory v2: load returning-user context and append memory block to prompt
if user_id and user_id != "default_user":
    from learn_backend import build_memory_prompt_block
    import supabase_client
    try:
        memory = await supabase_client.load_memory(user_id)
        # Persist email/name into the summary for future welcome-backs
        state_for_user = get_or_create_session(session_id)
        state_for_user.user_id = user_id  # idempotent
        memory_block = build_memory_prompt_block(memory)
        system_instruction = system_instruction + "\n\n" + memory_block
        logger.info(f"[memory] loaded for user {user_id}: bucket={memory.get('recency_bucket')}")
    except Exception as e:
        logger.warning(f"[memory] load failed for {user_id}: {e!r} — proceeding with v1 prompt")
```

This must be BEFORE the LLM is instantiated so the memory block is part of the system prompt.

**Step 4: Add persist_session to the fire-and-forget triggers**

`end_session` and `on_client_disconnected` already kick off `cekura_client.send_session(state)`. Find both call sites in `learn_backend.py` (the `end_session` tool body) and `bot-learn.py` (the `on_client_disconnected` handler).

For each, add a parallel persist call right next to the `cekura_client.send_session` line:

```python
asyncio.create_task(cekura_client.send_session(state))
import supabase_client
asyncio.create_task(supabase_client.persist_session(state))   # NEW
```

**Step 5: Verify**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
import ast
ast.parse(open('bot-learn.py').read())
ast.parse(open('learn_backend.py').read())
import importlib.util
spec = importlib.util.spec_from_file_location('botlearn', 'bot-learn.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
print('OK: bot-learn + learn_backend import cleanly')
"
```

**Step 6: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/bot-learn.py server/learn_backend.py
git commit -m "feat(memory-v2): read user_id from runner_args.body; inject memory block; persist on session end"
```

---

## Task 9: Update Dockerfile to COPY `supabase_client.py`

**Files:**
- Modify: `server/Dockerfile`

**Step 1: Add the COPY line**

Current Dockerfile ends with:

```
COPY ./bot-learn.py bot.py
COPY ./learn_backend.py learn_backend.py
COPY ./cekura_client.py cekura_client.py
COPY ./llm_context.md llm_context.md
```

Add:

```
COPY ./bot-learn.py bot.py
COPY ./learn_backend.py learn_backend.py
COPY ./cekura_client.py cekura_client.py
COPY ./supabase_client.py supabase_client.py
COPY ./llm_context.md llm_context.md
```

**Step 2: Verify**

```bash
grep "COPY" /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server/Dockerfile
```

Expected: 5 COPY lines including `supabase_client.py`.

**Step 3: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add server/Dockerfile
git commit -m "chore(memory-v2): copy supabase_client.py into deploy image"
```

---

## Task 10: Create custom client (`client/`)

**Files:**
- Create: `client/index.html`
- Create: `client/app.js`
- Create: `client/style.css`
- Create: `client/README.md`

**Step 1: `client/index.html`**

```html
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Confucius — Voice Tutor</title>
  <link rel="stylesheet" href="style.css">
  <script src="https://accounts.google.com/gsi/client" async defer></script>
  <script src="https://unpkg.com/@daily-co/daily-js"></script>
</head>
<body>
  <main>
    <h1>Confucius</h1>
    <p class="tagline">An interactive voice tutor for everyone. Anywhere, anytime your hands or eyes are busy.</p>

    <section id="signin">
      <p>Sign in to start a session. Confucius will remember you next time.</p>
      <div id="g_id_onload"
           data-client_id="REPLACE_WITH_GOOGLE_CLIENT_ID"
           data-callback="onGoogleSignIn"
           data-auto_prompt="false"></div>
      <div class="g_id_signin" data-type="standard" data-size="large"></div>
    </section>

    <section id="ready" hidden>
      <p>Hello, <span id="user-name"></span>. Ready when you are.</p>
      <button id="start-btn">Start Learning</button>
    </section>

    <section id="call" hidden>
      <div id="daily-frame"></div>
      <button id="end-btn">End Session</button>
    </section>

    <footer><small>YC Voice Agents Hackathon · built with Pipecat · memory v2</small></footer>
  </main>
  <script src="app.js"></script>
</body>
</html>
```

**Step 2: `client/app.js`**

```javascript
// app.js — Confucius voice tutor client (memory v2)

const PIPECAT_API_KEY = "pk_7678dd2b-dec7-4b68-966c-4d7509916ce7";
const PIPECAT_START_URL = "https://api.pipecat.daily.co/v1/public/learn-bot/start";

let userData = null;
let dailyCall = null;

window.onGoogleSignIn = (response) => {
  // The JWT credential is in response.credential. Decode the payload (NOT cryptographic;
  // v2 trusts the claim. v3 will verify server-side.)
  const payload = JSON.parse(atob(response.credential.split(".")[1]));
  userData = {
    user_id: payload.sub,
    email: payload.email,
    name: payload.name,
  };
  document.getElementById("user-name").textContent = userData.name;
  document.getElementById("signin").hidden = true;
  document.getElementById("ready").hidden = false;
};

document.getElementById("start-btn").addEventListener("click", async () => {
  const startBtn = document.getElementById("start-btn");
  startBtn.disabled = true;
  startBtn.textContent = "Starting…";

  try {
    const r = await fetch(PIPECAT_START_URL, {
      method: "POST",
      headers: {
        "Authorization": `Bearer ${PIPECAT_API_KEY}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({ createDailyRoom: true, data: userData }),
    });
    if (!r.ok) throw new Error(`Pipecat session-mint failed: ${r.status} ${await r.text()}`);
    const { room_url, token } = await r.json();

    dailyCall = window.DailyIframe.createFrame(document.getElementById("daily-frame"), {
      iframeStyle: { width: "100%", height: "500px", border: 0 },
      showLeaveButton: false,
    });
    await dailyCall.join({ url: room_url, token });

    document.getElementById("ready").hidden = true;
    document.getElementById("call").hidden = false;
  } catch (e) {
    alert("Could not start session: " + e.message);
    startBtn.disabled = false;
    startBtn.textContent = "Start Learning";
  }
});

document.getElementById("end-btn").addEventListener("click", async () => {
  if (dailyCall) {
    await dailyCall.leave();
    dailyCall.destroy();
    dailyCall = null;
  }
  document.getElementById("call").hidden = true;
  document.getElementById("ready").hidden = false;
  const sb = document.getElementById("start-btn");
  sb.disabled = false;
  sb.textContent = "Start Learning";
});
```

**Step 3: Replace `REPLACE_WITH_GOOGLE_CLIENT_ID`**

Use the value from Task 2 Step 2. The implementer subagent should request this value from the controller.

**Step 4: `client/style.css`**

```css
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif;
  background: #fafaf7;
  color: #1a1a1a;
  margin: 0;
  padding: 0;
  min-height: 100vh;
}
main {
  max-width: 720px;
  margin: 0 auto;
  padding: 48px 24px;
}
h1 { font-size: 40px; letter-spacing: -0.02em; margin: 0 0 8px; }
.tagline { color: #6b6b6b; font-size: 17px; margin: 0 0 32px; }
section {
  background: #fff;
  border: 1px solid #e5e3dc;
  border-radius: 14px;
  padding: 24px;
  margin-bottom: 16px;
  box-shadow: 0 1px 2px rgba(0,0,0,.04);
}
button {
  background: #1a1a1a;
  color: #fff;
  border: 0;
  border-radius: 10px;
  padding: 12px 24px;
  font-size: 15px;
  font-weight: 500;
  cursor: pointer;
}
button:hover { background: #333; }
button:disabled { background: #888; cursor: not-allowed; }
#daily-frame { border-radius: 10px; overflow: hidden; }
footer { margin-top: 48px; color: #888; text-align: center; }
```

**Step 5: `client/README.md`**

```markdown
# Confucius client

Static web app: Google Sign-In + Pipecat Cloud session-mint + Daily WebRTC join.

## Run locally

```
cd client
python3 -m http.server 3000
```

Open `http://localhost:3000`.

## Deploy

```
npm i -g vercel    # if not installed
vercel --prod
```

After deploy, add the resulting Vercel URL to:
1. Google Cloud Console → OAuth Client → Authorized JavaScript origins + Authorized redirect URIs
2. (No Supabase change needed; the client doesn't hit Supabase directly)

## Config

`GOOGLE_CLIENT_ID` is embedded directly in `index.html`. Replace `REPLACE_WITH_GOOGLE_CLIENT_ID` with the OAuth client ID from Google Cloud Console.
```

**Step 6: Smoke-test locally**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/client
python3 -m http.server 3000 &
sleep 1
curl -s -o /dev/null -w "%{http_code}\n" http://localhost:3000/
kill %1 2>/dev/null
```

Expected: `200`.

**Step 7: Commit**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
git add client/
git commit -m "feat(memory-v2): add custom Google-Sign-In client for Confucius"
```

---

## Task 11: Deploy client to Vercel (HUMAN-GATED)

**Files:** none.

**Step 1: Install Vercel CLI if needed**

```bash
which vercel || npm install -g vercel
```

**Step 2: Deploy**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/client
vercel --prod
```

Follow prompts:
- Set up project? **Yes**
- Project name: `confucius` (or default)
- Directory: `./` (current)
- Override settings? **No**

Vercel deploys; outputs a URL like `https://confucius-xxx.vercel.app`.

**Step 3: Add the Vercel URL to Google OAuth**

Back in Google Cloud Console → OAuth Client → Authorized JavaScript origins:
- Add `https://confucius-xxx.vercel.app`

Authorized redirect URIs:
- Add `https://confucius-xxx.vercel.app`

Save.

**Step 4: Open the deployed URL**

```bash
open https://confucius-xxx.vercel.app
```

Verify:
- Page loads
- Google Sign-In button shows
- Clicking Sign-In opens Google's OAuth flow
- After signing in, the "Hello, <name>" + "Start Learning" UI appears

(Don't click Start Learning yet — that requires the bot to be redeployed with memory wiring; that's Task 13.)

No commit (deploy is external).

---

## Task 12: Re-upload secrets to Pipecat Cloud

**Files:** none modified.

**Step 1: Verify `.env` has all required keys**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
uv run python -c "
from pathlib import Path
import re
env = Path('.env').read_text()
required = ['OPENAI_API_KEY', 'GRADIUM_API_KEY', 'GRADIUM_VOICE_ID',
            'CEKURA_API_KEY', 'CEKURA_AGENT_ID',
            'SUPABASE_URL', 'SUPABASE_SERVICE_KEY']
# GOOGLE_CLIENT_ID is client-only — not uploaded to bot secrets
for k in required:
    m = re.search(rf'^{k}=(.+)\$', env, re.M)
    assert m, f'{k} missing'
    assert m.group(1).strip(), f'{k} empty'
    print(f'{k}: <set, {len(m.group(1))} chars>')
"
```

Expected: 7 lines, each `<set, N chars>` with N > 10.

**Step 2: Upload**

```bash
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud secrets set learn-bot-secrets --file .env --skip 2>&1 | tail -10
```

Expected: success banner with `Secret set 'learn-bot-secrets' modified successfully`.

No commit (secrets upload is external).

---

## Task 13: Redeploy `learn-bot` to Pipecat Cloud

**Files:** none modified.

**Step 1: Deploy**

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe/server
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud deploy --yes 2>&1
```

Run in background; ~2-3 min build.

**Step 2: Wait + verify**

On task completion, read the output. Expected: `Agent deployment 'learn-bot' is ready`.

If failure:
```bash
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud build logs <build-id> 2>&1 | tail -80
```

Common failure: `ModuleNotFoundError: No module named 'supabase_client'` → Task 9 Dockerfile COPY missing.

**Step 3: Check agent status**

```bash
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud agent status learn-bot 2>&1 | head -20
```

Expected: status panel with "Ready · Active · N agents". Note the new `Active Deployment ID` to confirm it changed.

No commit.

---

## Task 14: Layer 2 end-to-end smoke (HUMAN-GATED)

**Files:** none modified.

**Plan: 3 sessions covering 3 recency buckets.**

**Session 1 (recency=new)**:
1. Open `https://confucius-xxx.vercel.app/` (your Vercel URL)
2. Sign in with Google (use the account whose `sub` you want as test user)
3. Click Start Learning → Daily joins → bot greets
4. Expected greeting: *"Hi, I'm Confucius. What do you want to learn about today?"* (v1 default — no memory yet for this user)
5. Pick a topic (e.g. "WebRTC")
6. Have a 1–2 minute conversation; cover at least one concept
7. Say "let's wrap up" → recap → "goodbye"
8. Verify: bot exits cleanly. Reload page.

In parallel, tail cloud logs:
```bash
env -u GH_TOKEN -u GITHUB_TOKEN pc cloud agent logs learn-bot --limit 100 2>&1 | grep -E "\[learn\]|\[memory\]|\[cekura\]" | tail -20
```

Look for:
- `[memory] loaded for user google-sub-xxx: bucket=new`
- `[learn] set_topic: WebRTC`
- `[learn] add_concept_covered: ...`
- `[learn] recap_session: ...`
- `[learn] end_session for ...`
- `[memory] persisted session ... for user google-sub-xxx`

**Session 2 (recency=clean_recent)**:
1. Sign in again (same Google account)
2. Click Start Learning
3. Expected greeting: *"Welcome back. Last time we covered <concepts>; you wanted to come back to <marked_for_later or 'something new'>. Pick up there or something new?"*
4. Verify by listening — if greeting is generic, memory load failed (check `[memory]` logs)
5. Have a short session and disconnect WITHOUT saying goodbye (close the browser tab abruptly)

Tail logs again — look for:
- `[memory] loaded for user google-sub-xxx: bucket=clean_recent`
- `[memory] persisted session ... for user google-sub-xxx` (from on_client_disconnected fallback)

**Session 3 (recency=mid_topic_recent)**:
1. Sign in again (still within 24h of session 2)
2. Click Start Learning
3. Expected greeting: *"Welcome back. We were just getting to <open concept> — want to keep going?"*
4. Verify by listening
5. Have a normal session; end cleanly

**Definition of pass**:
- All 3 greetings match the expected shape for their recency bucket
- All sessions show `[memory] loaded` AND `[memory] persisted` log lines
- Zero `[memory] load failed` or `[memory] persist_session failed` errors

No commit.

---

## Task 15: Layer 3 — Supabase SQL verification (HUMAN-GATED)

**Files:** none modified.

**Step 1: Open Supabase SQL editor**

`https://supabase.com/dashboard/project/<your-project>/sql/new`

**Step 2: Run verification queries**

```sql
-- Check the user's summary
select
  user_id,
  name,
  total_sessions,
  total_minutes,
  last_session_at,
  last_phase_reached,
  jsonb_array_length(concepts_lifetime) as concept_count,
  jsonb_array_length(marked_for_later)  as marked_count
from learner_summary
where user_id = '<your_google_sub>';
```

After 3 sessions: expect 1 row with `total_sessions = 3`, `concept_count >= 1`.

```sql
-- Check per-session detail
select
  id,
  started_at,
  ended_at,
  payload->>'topic' as topic,
  payload->>'phase_reached' as phase,
  payload->>'end_reason' as end_reason,
  jsonb_array_length(payload->'concepts_covered') as concepts
from sessions
where user_id = '<your_google_sub>'
order by started_at desc
limit 10;
```

Expect 3 rows, newest first. Phase values should match what each session reached (`recap`/`closing` for cleanly-ended, `teaching` for mid-topic disconnect).

**Definition of pass**:
- Summary row has correct `total_sessions = 3`
- All 3 session rows visible
- Phase values match the test scenarios

If any value is unexpected, the memory write path has a bug — investigate `persist_session` in `supabase_client.py`.

No commit.

---

## Task 16: Push everything to GitHub

**Files:** none modified.

```bash
cd /Users/zhengyijoe.he/workspace/ai/yc-voice-agents-hackathon-joehe
env -u GH_TOKEN -u GITHUB_TOKEN git push origin main 2>&1 | tail -5
```

Expected: push succeeds, all memory-v2 commits land on `origin/main`.

---

## Definition of done

- All 9 code-side commits pushed to `origin/main`
- Vercel client deployed and reachable
- Google OAuth flow works end-to-end
- 3 consecutive sessions on the same Google account produce 3 distinct recency-bucket greetings (new → clean_recent → mid_topic_recent)
- Supabase has the expected rows (one summary row, three sessions rows)
- Zero `[memory]` errors in cloud logs

---

## Out of scope for this plan (do NOT do)

| Item | Status |
|---|---|
| Server-side JWT verification | v3 design hook (§3 of design) |
| Cross-reference during session | v3 design hook |
| Personalization (depth/pace) | v3 design hook |
| Native iOS app | v3 design hook |
| Memory-deletion UI / "forget me" | post-v2 |
| RLS policies on bot-only tables | v3 if client gets a history view |
| Multi-device concurrent session handoff | edge case; v3 |
| Transcript full-text search | v3 |
| Unit tests on `supabase_client.py` | Skipped by design — trivial CRUD |
| Migration from in-memory `_SESSIONS` to Supabase-as-primary | v3 — keep in-memory as session-scoped cache, Supabase as cross-session persistence |

---

## Appendix — File reference after this plan

```
server/
├── bot-gpt.py            # unchanged
├── bot-nemotron.py       # unchanged
├── bot-learn.py          # MODIFIED — user_id flow + memory injection
├── learn_backend.py      # MODIFIED — build_memory_prompt_block + persist on session-end
├── supabase_client.py    # NEW
├── cekura_client.py      # unchanged
├── llm_context.md        # unchanged
├── Dockerfile            # MODIFIED — COPY supabase_client.py
├── pcc-deploy.toml       # unchanged
├── pyproject.toml        # MODIFIED — supabase>=2.0 dep
├── uv.lock               # MODIFIED — supabase + transitive deps
└── .env                  # MODIFIED — adds SUPABASE_URL, SUPABASE_SERVICE_KEY, GOOGLE_CLIENT_ID

client/                   # NEW directory
├── index.html
├── app.js
├── style.css
└── README.md

docs/plans/
└── 2026-05-30-confucius-memory-v2-design.md   # design doc (already committed)
└── 2026-05-30-confucius-memory-v2-plan.md     # this plan
```
