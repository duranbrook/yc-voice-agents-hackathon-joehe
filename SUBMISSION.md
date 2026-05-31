# Confucius — Interactive Voice Tutor

> An interactive voice tutor for everyone. Anywhere, anytime your hands or eyes are busy.

## 1. What is this?

**Confucius** is a voice-first tutor for the time you already lose every day — walking, training at the gym, commuting. You put your earbuds in, ask a question out loud, and have a real back-and-forth with the bot. The phone stays in your pocket. Your eyes and hands stay free.

YouTube and podcasts talk *at* you. Books need your eyes. Confucius is the first medium that lets you *interactively* learn while you're in motion.

Under the hood it's a Pipecat voice agent with a 5-phase session state machine — **Opening → Scoping → Teaching → Recap → Closing** — and five structured tools (`set_topic`, `add_concept_covered`, `mark_for_later`, `recap_session`, `end_session`) that turn every spoken session into queryable data. That structured trail is what makes the next step — observability with Cekura — possible.

## 2. Demo video (<60s)

<!-- TODO: replace with final hosted video URL -->
**🎬 [Demo video link — TBD]**

Script + storyboard: [`docs/plans/2026-05-30-confucius-demo-video-script.html`](docs/plans/2026-05-30-confucius-demo-video-script.html)
Pitch deck: [`docs/plans/2026-05-30-confucius-pitch-deck.html`](docs/plans/2026-05-30-confucius-pitch-deck.html)

**60-second structure:**

| Time | Beat | What you see / hear |
|---|---|---|
| 0:00 – 0:05 | What | Tagline over a dim slow-mo of someone walking with earbuds |
| 0:05 – 0:32 | How | Live recording of Confucius answering "What problem does Pipecat actually solve?" — dogfooded: the Pipecat-built bot teaches the viewer about Pipecat |
| 0:32 – 0:45 | Dashboard | Cekura panel — concepts learned, time-to-understanding, trending topics |
| 0:45 – 1:00 | Why | Punch ("you lose hours") + close ("everyone can learn anywhere, anytime") |

## 3. How we used Cekura, Nemotron, and Pipecat

### Pipecat — the whole thing rests on it

We forked the flower-bot starter (`server/bot-gpt.py`) into a new `server/bot-learn.py` and built `server/learn_backend.py` with:

- A `SessionState` dataclass holding `topic`, `depth`, `starting_level`, `concepts_covered`, `marked_for_later`, and a wall-clock `started_at`.
- A `make_tools(session_id)` factory that closes over the session ID and produces five async tool functions wired into Pipecat's LLM service via `register_direct_function`.
- A process-local `_SESSIONS` dict keyed by an opaque session ID minted per `run_bot` call.

The 5-phase pedagogy is encoded entirely in the **system prompt**, not in code — the LLM tracks its own phase and calls the tools at the right moments. The prompt deliberately avoids "Confucius says…" aphorism style; the bot is a modern voice tutor, not a fortune cookie.

Stack: Pipecat 1.3.0 · OpenAI GPT-4.1 (Responses API) · Gradium STT/TTS · Daily WebRTC · Pipecat Cloud. Deployed as a second agent (`learn-bot`) alongside the starter `flower-bot`.

**What Pipecat solved for us:** voice agents normally need weeks of WebRTC, VAD, turn-taking, STT→LLM→TTS streaming, barge-in handling, and deployment plumbing. Pipecat collapsed all of that into one Python file + one TOML. We built the tutor logic in an afternoon — not because the logic is small, but because everything *under* it was already solved. **Stripe-for-voice-infra is the right analogy.**

### Cekura — observability for what users actually learn

<!-- TODO: confirm + expand this section with concrete details -->
We used Cekura as the observability layer for tutoring sessions. The 5 Pipecat tools emit structured events (`set_topic`, `add_concept_covered`, `mark_for_later`, `recap_session`, `end_session`) that map directly onto the kind of data Cekura is built to observe — turning each free-form spoken conversation into queryable, comparable data:

- **Concepts covered per session** — what the user actually got, not what they were exposed to.
- **Time-to-understanding** — measured between when a topic is set and when the user signals comprehension (e.g. asking the next follow-up).
- **Marked-for-later trail** — what users wanted to come back to but didn't have time for. Surfaces real demand.
- **Trending topics across users** — what the whole user base is learning this week.

**What we were trying to accomplish:** turn "I had a 10-minute chat with my bot" into something a product team can actually look at. Did the user learn? How fast? What did they bounce off of?

<!-- TODO: if you have concrete improvement numbers from Cekura, fill in:
- Baseline metric you measured
- What changed in the prompt/tools after looking at Cekura data
- Improvement % or qualitative difference
-->

### Nemotron — not in this submission

We did not swap the LLM to Nemotron in v1. The repo includes a Nemotron variant (`server/bot-nemotron.py`) that we plan to test side-by-side post-hackathon: same prompt, same tools, swap GPT-4.1 for Nemotron 3 Super 120B, compare in Cekura. Honest answer for now — we wanted to ship one solid agent, not two half-finished ones.

## 4. What we built during the hackathon

Forked from the public Pipecat starter at [pipecat-ai/yc-voice-agents-hackathon](https://github.com/pipecat-ai/yc-voice-agents-hackathon). Everything below is **new** for this hackathon:

| File | What it is |
|---|---|
| `server/bot-learn.py` | New Pipecat agent. Started as a copy of `bot-gpt.py`, then ripped out the flower-shop tools, swapped in our tutor tools, and replaced the system prompt with the 5-phase Confucius pedagogy. |
| `server/learn_backend.py` | New module. `SessionState` dataclass + `make_tools()` factory + in-memory `_SESSIONS` store. ~150 lines, no dependencies outside Pipecat. |
| `server/Dockerfile` (modified) | Repointed at `bot-learn.py` + `learn_backend.py`. |
| `server/pcc-deploy.toml` (modified) | New agent name `learn-bot`, new secrets set `learn-bot-secrets`. |
| `docs/plans/2026-05-30-confucius-learning-bot-design.md` | Design doc — architecture, 5-phase state machine rationale, what's in/out of v1. |
| `docs/plans/2026-05-30-confucius-learning-bot-plan.md` | 10-task implementation plan executed during the hackathon. |
| `docs/plans/2026-05-30-confucius-demo-video-script.html` | Demo video script + storyboard. |
| `docs/plans/2026-05-30-confucius-pitch-deck.html` | Pitch deck for judging. |

**Not new (borrowed from the starter):** Pipecat pipeline scaffolding, VAD/turn-detection, Daily transport setup, Gradium STT/TTS wiring, the Twilio configuration block in the original README. We left the original `bot-gpt.py` and `bot-nemotron.py` untouched.

## 5. Feedback on the tools

### Pipecat

**What's great:**
- One-file agents are the right level of abstraction. The whole tutor — prompt, tools, lifecycle — fits in `bot-learn.py` and reads top-to-bottom.
- `register_direct_function` + `FunctionCallParams.result_callback` is a clean pattern for tool calls. No glue code to remember.
- `EndTaskFrame` + `FrameDirection.UPSTREAM` is the right way to end a call from inside a tool. Took ~30 seconds to find in the docs.
- Pipecat Cloud deploy was three commands: `pc cloud secrets set`, `pc cloud deploy`, `pc cloud agent status`. Worked on the first try once we got the Dockerfile right.

**Friction points:**
- The Dockerfile is shared across agents in the same repo — you can't deploy `flower-bot` and `learn-bot` from the same commit because the `COPY ./bot-X.py bot.py` line conflicts. We took the "edit in-place, accept that flower-bot redeploys break" tradeoff. A `--build-arg` switch in the starter would help.
- First-run VAD/turn-detection model download is ~20 s. Worth a banner in the playground UI so first-time users don't think it's broken.
- The interaction between `system_instruction` and the auto-appended turn-completion framework (✓/○/◐) is non-obvious — we wasted ~15 min adding it to our prompt before realizing Pipecat appends it for us when `FilterIncompleteUserTurnStrategies` is on.

### Cekura

<!-- TODO: replace with your actual experience. Sponsor specifically asks: "Did you find any bugs? Self-improvement loops?" -->

**What worked:**
- The MCP + skills setup via `/plugin install cekura@cekura-skills` was the fastest tool onboarding of the day. Slash commands meant we never left Claude Code.
- Mapping our 5 Pipecat tool events onto Cekura's observability model was intuitive — the data model "just fit."

**What we'd improve:**
- <!-- TODO: any bugs or rough edges you hit -->
- <!-- TODO: feedback on self-improvement loops if you tried that flow -->

### Nemotron

We didn't run Nemotron in v1, so we don't have substantive feedback. Planned for the side-by-side eval post-hackathon: same prompt, swap LLMs, compare in Cekura.

## 6. Live link

<!-- TODO: paste your Pipecat Cloud sandbox URL once deployed.
Example shape: https://pipecat.daily.co/<org-id>/agents/learn-bot/sandbox
-->

**🌐 [Try the bot — TBD]**

Best with earbuds + iOS Safari. Tap **Connect**, grant mic, ask anything. ~8-minute sessions are the default.

---

## Run it locally

```bash
git clone <this-repo>
cd yc-voice-agents-hackathon-joehe/server
cp .env.example .env  # add OPENAI_API_KEY, GRADIUM_API_KEY, GRADIUM_VOICE_ID
uv sync
uv run bot-learn.py
# open http://localhost:7860/client/ and click Connect
```

Full setup, deployment, and Twilio wiring details: [`docs/STARTER.md`](docs/STARTER.md) <!-- TODO: move the existing starter README content here -->

## Credits

Built on **[Pipecat](https://pipecat.ai)** · evaluated with **[Cekura](https://cekura.com)** · STT/TTS by **[Gradium](https://gradium.ai)** · transport by **[Daily](https://daily.co)** · hackathon hosted by **YC**.
