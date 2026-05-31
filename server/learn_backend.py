# server/learn_backend.py
"""In-memory backing store + tutor tool implementations for Confucius bot.

v1 stores SessionState in a process-local dict keyed by session_id. State is
lost on process recycle. v2 will swap this for a persistent KV store keyed by
user_id; the tool signatures will not change.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

from loguru import logger
from pipecat.frames.frames import EndTaskFrame, FunctionCallResultProperties
from pipecat.processors.frame_processor import FrameDirection
from pipecat.services.llm_service import FunctionCallParams


# ---------- Data model ---------------------------------------------------


@dataclass
class ConceptCovered:
    concept: str
    brief: str
    started_at: datetime
    ended_at: datetime | None = None


@dataclass
class MarkedForLater:
    item: str
    reason: str


@dataclass
class TranscriptTurn:
    role: str               # "user" | "assistant"
    content: str
    timestamp: datetime


@dataclass
class SessionState:
    session_id: str
    started_at: datetime
    user_id: str = "default_user"
    ended_at: datetime | None = None
    topic: Optional[str] = None
    depth: Optional[str] = None  # "overview" | "deep" | "unknown"
    starting_level: Optional[str] = None  # "novice" | "some_background" | "expert" | "unknown"
    concepts_covered: list[ConceptCovered] = field(default_factory=list)
    marked_for_later: list[MarkedForLater] = field(default_factory=list)
    transcript: list[TranscriptTurn] = field(default_factory=list)
    phase_reached: str = "opening"           # opening | scoping | teaching | recap | closing
    end_reason: str | None = None            # "user_goodbye" | "client_disconnect" | "error"
    sent_to_cekura: bool = False             # idempotency latch


# Process-local store. Keyed by an opaque session_id we generate per run_bot call.
_SESSIONS: dict[str, SessionState] = {}


def get_or_create_session(session_id: str, user_id: str = "default_user") -> SessionState:
    """Return the state for session_id, creating it if missing. If creating,
    associate the new state with `user_id` (from runner_args.body in v2, or
    'default_user' for anonymous v1 sessions)."""
    state = _SESSIONS.get(session_id)
    if state is None:
        state = SessionState(
            session_id=session_id,
            started_at=datetime.now(timezone.utc),
            user_id=user_id,
        )
        _SESSIONS[session_id] = state
    return state


def _duration_minutes(state: SessionState) -> float:
    return (datetime.now(timezone.utc) - state.started_at).total_seconds() / 60.0


def build_memory_prompt_block(memory: dict) -> str:
    """Compose the markdown block appended to system_instruction when a returning
    user is detected. The LLM uses this to shape its opening turn.

    `memory` is the dict returned by supabase_client.load_memory():
      {"recency_bucket": str, "summary": dict|None, "last_session": dict|None}
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

    concepts_short = ", ".join(
        c.get("concept", "?") for c in (last_concepts[:3] if isinstance(last_concepts, list) else [])
    ) or "no concepts yet"
    marked_short = ", ".join(
        m.get("item", "?") for m in (marked_for_later[:2] if isinstance(marked_for_later, list) else [])
    ) or "nothing"

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
        state.phase_reached = "teaching"
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

    async def end_session(params: FunctionCallParams) -> None:
        """End the session. Only call this AFTER you have said goodbye to the
        learner in the same turn. The pipeline will flush any queued speech and
        then hang up."""
        # NOTE: do NOT mention `run_llm` in the docstring above — Pipecat exposes
        # docstrings to the LLM and the model will mistake `run_llm` for a tool
        # parameter and call end_session(run_llm=False), causing a TypeError.
        # The run_llm=False below is for FunctionCallResultProperties; it
        # prevents an LLM follow-up response after this tool returns.
        import cekura_client  # function-level to avoid module-load-time circular import concern
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
            import supabase_client
            asyncio.create_task(supabase_client.persist_session(state))
        logger.info(f"[learn] end_session for {session_id}")
        await params.llm.push_frame(EndTaskFrame(), FrameDirection.UPSTREAM)
        await params.result_callback(
            {"ok": True}, properties=FunctionCallResultProperties(run_llm=False)
        )

    return [
        set_topic,
        add_concept_covered,
        mark_for_later,
        recap_session,
        end_session,
    ]
