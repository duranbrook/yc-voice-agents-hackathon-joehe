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
HTTP_TIMEOUT_SECONDS = 5.0


def _api_key() -> str:
    """Read CEKURA_API_KEY lazily so .env loaded after import still works."""
    return os.environ.get("CEKURA_API_KEY", "")


def _headers() -> dict:
    return {
        "Content-Type": "application/json",
        "X-CEKURA-API-KEY": _api_key(),
        "User-Agent": "learn-bot-cekura-client/1.0",
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
    if not _api_key():
        logger.warning("[cekura] CEKURA_API_KEY not set — skipping send")
        return
    try:
        payload = build_payload(state)
    except Exception as e:
        logger.error(f"[cekura] payload build failed for {state.session_id}: {e!r}")
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
    except Exception as e:
        logger.error(f"[cekura] unexpected error for {state.session_id}: {e!r}")
