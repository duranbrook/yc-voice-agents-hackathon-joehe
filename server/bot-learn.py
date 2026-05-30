#
# Copyright (c) 2024–2026, Daily
#
# SPDX-License-Identifier: BSD 2-Clause License
#

"""Confucius — voice learning tutor (hackathon, v1).

Fork of bot-gpt.py. Implements a five-phase tutor session:
  1. Opening — greet + invite a topic
  2. Scoping — calibrate depth + starting level → set_topic
  3. Teaching — pedagogical Q&A → add_concept_covered / mark_for_later
  4. Recap — spoken summary via recap_session
  5. Closing — goodbye + end_session

Session state is in-memory only (see learn_backend.py). v2 will persist
to a KV store keyed by user_id without changing tool signatures.

Run locally: `uv run bot-learn.py`
"""

import os
import uuid
from datetime import date, datetime, timezone

import aiohttp
from dotenv import load_dotenv
from loguru import logger
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.frames.frames import (
    Frame,
    LLMFullResponseEndFrame,
    LLMFullResponseStartFrame,
    LLMRunFrame,
    LLMTextFrame,
    TranscriptionFrame,
)
from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.worker import PipelineParams, PipelineWorker
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.processors.aggregators.llm_response_universal import (
    LLMContextAggregatorPair,
    LLMUserAggregatorParams,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.runner.types import (
    DailyRunnerArguments,
    RunnerArguments,
    SmallWebRTCRunnerArguments,
    WebSocketRunnerArguments,
)
from pipecat.runner.utils import parse_telephony_websocket
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.services.gradium.stt import GradiumSTTService
from pipecat.services.gradium.tts import GradiumTTSService
from pipecat.services.openai.responses.llm import OpenAIResponsesLLMService
from pipecat.transcriptions.language import Language
from pipecat.transports.base_transport import BaseTransport, TransportParams
from pipecat.transports.daily.transport import DailyParams, DailyTransport
from pipecat.transports.smallwebrtc.connection import SmallWebRTCConnection
from pipecat.transports.smallwebrtc.transport import SmallWebRTCTransport
from pipecat.transports.websocket.fastapi import FastAPIWebsocketParams, FastAPIWebsocketTransport
from pipecat.turns.user_turn_strategies import FilterIncompleteUserTurnStrategies
from pipecat.workers.runner import WorkerRunner

from learn_backend import TranscriptTurn, get_or_create_session, make_tools

KNOWN_CUSTOMERS: dict = {}  # placeholder for v2 returning-learner recognition


# --- Transcript capture (Pipecat 1.3.0 has no TranscriptProcessor) ----------
# A pair of these is inserted into the pipeline to observe finalized user
# transcriptions (post-STT) and aggregated assistant text (between
# LLMFullResponseStartFrame and LLMFullResponseEndFrame, post-LLM). They are
# pure observers: every frame is passed through unchanged.


class _UserTranscriptCapture(FrameProcessor):
    """Append finalized TranscriptionFrames to SessionState.transcript."""

    def __init__(self, session_id: str):
        super().__init__()
        self._session_id = session_id

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        try:
            if (
                isinstance(frame, TranscriptionFrame)
                and direction == FrameDirection.DOWNSTREAM
                and (frame.text or "").strip()
            ):
                state = get_or_create_session(self._session_id)
                state.transcript.append(
                    TranscriptTurn(
                        role="user",
                        content=frame.text.strip(),
                        timestamp=datetime.now(timezone.utc),
                    )
                )
        except Exception as e:
            logger.warning(f"[transcript-capture] user capture failed: {e!r}")
        await self.push_frame(frame, direction)


class _AssistantTranscriptCapture(FrameProcessor):
    """Aggregate LLMTextFrames between LLMFullResponse{Start,End} into one turn."""

    def __init__(self, session_id: str):
        super().__init__()
        self._session_id = session_id
        self._buffer: list[str] = []
        self._capturing = False

    async def process_frame(self, frame: Frame, direction: FrameDirection):
        await super().process_frame(frame, direction)
        try:
            if direction == FrameDirection.DOWNSTREAM:
                if isinstance(frame, LLMFullResponseStartFrame):
                    self._buffer = []
                    self._capturing = True
                elif isinstance(frame, LLMTextFrame) and self._capturing:
                    if frame.text and not getattr(frame, "skip_tts", False):
                        self._buffer.append(frame.text)
                elif isinstance(frame, LLMFullResponseEndFrame) and self._capturing:
                    self._capturing = False
                    content = "".join(self._buffer).strip()
                    self._buffer = []
                    if content:
                        state = get_or_create_session(self._session_id)
                        state.transcript.append(
                            TranscriptTurn(
                                role="assistant",
                                content=content,
                                timestamp=datetime.now(timezone.utc),
                            )
                        )
        except Exception as e:
            logger.warning(f"[transcript-capture] assistant capture failed: {e!r}")
        await self.push_frame(frame, direction)

load_dotenv(override=True)


async def get_call_info(call_sid: str) -> dict:
    """Fetch call information from Twilio REST API using aiohttp.

    Args:
        call_sid: The Twilio call SID

    Returns:
        Dictionary containing call information including from_number, to_number, status, etc.
    """
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")

    if not account_sid or not auth_token:
        logger.warning("Missing Twilio credentials, cannot fetch call info")
        return {}

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Calls/{call_sid}.json"

    try:
        # Use HTTP Basic Auth with aiohttp
        auth = aiohttp.BasicAuth(account_sid, auth_token)

        async with aiohttp.ClientSession() as session:
            async with session.get(url, auth=auth) as response:
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Twilio API error ({response.status}): {error_text}")
                    return {}

                data = await response.json()

                call_info = {
                    "from_number": data.get("from"),
                    "to_number": data.get("to"),
                }

                return call_info

    except Exception as e:
        logger.error(f"Error fetching call info from Twilio: {e}")
        return {}


async def run_bot(
    transport: BaseTransport,
    from_number: str | None = None,
    audio_in_sample_rate: int = 16000,
    audio_out_sample_rate: int = 24000,
):
    """Main bot logic.

    Args:
        transport: The transport to use.
        from_number: Caller's phone number (Twilio path only) for known-customer lookup.
        audio_in_sample_rate: Input audio sample rate in Hz. Defaults to 16000 (WebRTC).
        audio_out_sample_rate: Output audio sample rate in Hz. Defaults to 24000 (WebRTC).
    """
    logger.info("Starting bot")

    # --- Tools the LLM can call ---------------------------------------------

    session_id = str(uuid.uuid4())
    tool_functions = make_tools(session_id)
    # Ensure SessionState row exists from the start so recap_session works even
    # if the LLM hallucinates calling it pre-Phase-2.
    get_or_create_session(session_id)
    tools = ToolsSchema(standard_tools=tool_functions)

    # --- System instruction (varies based on caller ID) ---------------------

    # NOTE: caller_context is currently dead — the tutor prompt does not interpolate it.
    # Kept here as scaffolding for v2 returning-learner recognition.
    customer = KNOWN_CUSTOMERS.get(from_number or "")
    if customer:
        caller_context = (
            f"This caller is a returning learner (caller ID matched). On file: "
            f"name {customer['name']}, last topic {customer.get('last_topic', 'unknown')}. "
            "Do not use their name in the greeting; let them lead."
        )
    else:
        caller_context = (
            "You're talking to a new learner. Greet them briefly and ask what they want to learn."
        )

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
        "Move on to Phase 4 when the user says \"wrap up\" / \"gotta go\" / \"let's stop\", "
        "or when they mention running out of time / needing to get off. "
        "If the user signals time pressure, proactively offer to recap.\n\n"
        "If the user changes topics mid-session: call recap_session for the old topic first, "
        "then call set_topic again for the new one. Do not merge topics.\n\n"
        "Phase 4 — Recap. Call recap_session once. It returns the structured topic, "
        "concepts_covered, marked_for_later, and duration_minutes. Read that back as a short "
        "spoken summary: \"OK quick recap. We covered X, Y, and Z. You wanted to come back to W "
        "next time. Sound right?\" If the user corrects or adds something, update accordingly and re-recap.\n\n"
        "Phase 5 — Closing. Say a short, natural goodbye (e.g. \"Cool, have a good one\" or \"Catch you next time\") AND call end_session in "
        "the same turn. Never call end_session without saying goodbye first.\n\n"
        "Tool decision rules:\n"
        "- set_topic: ONCE per topic. Phase 2 → 3 transition.\n"
        "- add_concept_covered: after every explanation that introduces a real concept. NOT for chitchat.\n"
        "- mark_for_later: when the user says \"come back to that\" or expresses interest in a skipped tangent.\n"
        "- recap_session: ONCE before saying goodbye. Never twice.\n"
        "- end_session: ONLY after delivering a goodbye line.\n\n"
        f"Today is {date.today().strftime('%A, %B %d, %Y')}."
    )

    # Append optional domain context from llm_context.md (transcription corrections,
    # glossary, etc.) — additive only; safe to edit/reload without touching code.
    _ctx_path = os.path.join(os.path.dirname(__file__), "llm_context.md")
    if os.path.exists(_ctx_path):
        with open(_ctx_path) as _ctx_file:
            system_instruction = system_instruction + "\n\n" + _ctx_file.read()

    # Speech-to-Text service
    stt = GradiumSTTService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumSTTService.Settings(
            language=Language.EN,
        ),
    )

    # LLM service
    llm = OpenAIResponsesLLMService(
        api_key=os.environ["OPENAI_API_KEY"],
        settings=OpenAIResponsesLLMService.Settings(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1"),
            system_instruction=system_instruction,
        ),
    )

    # Text-to-Speech service
    tts = GradiumTTSService(
        api_key=os.environ["GRADIUM_API_KEY"],
        settings=GradiumTTSService.Settings(
            voice=os.getenv("GRADIUM_VOICE_ID", "_6Aslh2DxfmnRLmP"),
        ),
    )

    # ToolsSchema describes the tools to the LLM; register_direct_function
    # wires the actual handlers the LLM will invoke. Both are required.
    for fn in tool_functions:
        llm.register_direct_function(fn)

    context = LLMContext(tools=tools)
    user_aggregator, assistant_aggregator = LLMContextAggregatorPair(
        context,
        user_params=LLMUserAggregatorParams(
            vad_analyzer=SileroVADAnalyzer(),
            user_turn_strategies=FilterIncompleteUserTurnStrategies(),
        ),
    )

    # Transcript capture taps — populate SessionState.transcript so
    # cekura_client.build_payload sees a real turn list at end_session time.
    user_transcript_capture = _UserTranscriptCapture(session_id)
    assistant_transcript_capture = _AssistantTranscriptCapture(session_id)

    # Pipeline - assembled from reusable components
    pipeline = Pipeline(
        [
            transport.input(),
            stt,
            user_transcript_capture,
            user_aggregator,
            llm,
            assistant_transcript_capture,
            tts,
            transport.output(),
            assistant_aggregator,
        ]
    )

    worker = PipelineWorker(
        pipeline,
        params=PipelineParams(
            enable_metrics=True,
            enable_usage_metrics=True,
            audio_in_sample_rate=audio_in_sample_rate,
            audio_out_sample_rate=audio_out_sample_rate,
        ),
    )

    @transport.event_handler("on_client_connected")
    async def on_client_connected(transport, client):
        logger.info("Client connected")
        # Kick off the conversation
        context.add_message(
            {
                "role": "user",
                "content": "A learner just connected. Greet them: \"Hi, I'm Confucius. What do you want to learn about today?\"",
            }
        )
        await worker.queue_frames([LLMRunFrame()])

    @transport.event_handler("on_client_disconnected")
    async def on_client_disconnected(transport, client):
        import asyncio
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
        await worker.cancel()

    runner = WorkerRunner(handle_sigint=False)

    await runner.add_workers(worker)
    await runner.run()


async def bot(runner_args: RunnerArguments):
    """Main bot entry point."""

    from_number: str | None = None
    transport_overrides: dict = {}

    # Krisp is available when deployed to Pipecat Cloud
    if os.environ.get("ENV") != "local":
        from pipecat.audio.filters.krisp_viva_filter import KrispVivaFilter

        krisp_filter = KrispVivaFilter()
    else:
        krisp_filter = None

    match runner_args:
        case DailyRunnerArguments():
            transport = DailyTransport(
                runner_args.room_url,
                runner_args.token,
                "Confucius Tutor Bot",
                params=DailyParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                    transcription_enabled=False,
                ),
            )
        case SmallWebRTCRunnerArguments():
            webrtc_connection: SmallWebRTCConnection = runner_args.webrtc_connection

            transport = SmallWebRTCTransport(
                webrtc_connection=webrtc_connection,
                params=TransportParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                ),
            )
        case WebSocketRunnerArguments():
            # Twilio media streams are 8 kHz μ-law in both directions.
            # This overrides the default sample rates: 16 kHz in / 24 kHz out.
            transport_overrides["audio_in_sample_rate"] = 8000
            transport_overrides["audio_out_sample_rate"] = 8000

            # Parse Twilio websocket and fetch call information
            _, call_data = await parse_telephony_websocket(runner_args.websocket)

            # Fetch call information from Twilio REST API so we can personalize
            # the bot for known customers (see KNOWN_CUSTOMERS).
            call_info = await get_call_info(call_data["call_id"])
            if call_info:
                from_number = call_info.get("from_number")
                logger.info(f"Call from: {from_number} to: {call_info.get('to_number')}")

            serializer = TwilioFrameSerializer(
                stream_sid=call_data["stream_id"],
                call_sid=call_data["call_id"],
                account_sid=os.getenv("TWILIO_ACCOUNT_SID", ""),
                auth_token=os.getenv("TWILIO_AUTH_TOKEN", ""),
            )

            transport = FastAPIWebsocketTransport(
                websocket=runner_args.websocket,
                params=FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_in_filter=krisp_filter,
                    audio_out_enabled=True,
                    add_wav_header=False,
                    serializer=serializer,
                ),
            )
        case _:
            logger.error(f"Unsupported runner arguments type: {type(runner_args)}")
            return

    await run_bot(transport, from_number=from_number, **transport_overrides)


if __name__ == "__main__":
    from pipecat.runner.run import main

    main()
