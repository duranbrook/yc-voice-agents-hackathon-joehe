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
from typing import TYPE_CHECKING, Optional

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
    if url.startswith("<") or key.startswith("<"):
        logger.warning("[memory] SUPABASE_* env vars still contain placeholders — memory disabled")
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
        sr = client.table("learner_summary").select("*").eq("user_id", user_id).execute()
        summary = sr.data[0] if sr.data else None

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
        client.table("sessions").insert({
            "user_id": state.user_id,
            "started_at": payload["started_at"],
            "ended_at": payload["ended_at"],
            "payload": payload,
        }).execute()

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
