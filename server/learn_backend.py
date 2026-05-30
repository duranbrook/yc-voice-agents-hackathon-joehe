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
