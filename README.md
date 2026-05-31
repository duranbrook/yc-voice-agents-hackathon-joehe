# Confucius — Interactive Voice Tutor

> An interactive voice tutor for everyone. Anywhere, anytime your hands or eyes are busy.

YC Voice Agents Hackathon submission — built with **Pipecat**, evaluated with **Cekura**.

---

## 1. What is this?

**Confucius is an interactive voice tutor for everyone — available anywhere your hands and eyes are busy.** Whether you're commuting, walking the dog, or training at the gym, slip your earbuds in, ask a question out loud, and have a real back-and-forth with the bot. Your phone stays in your pocket. Your eyes and hands stay free.

YouTube and podcasts talk *at* you. Books and articles need your eyes. Confucius is the first medium that lets you *interactively* learn while you're in motion — turning the dead minutes of your day into focused, conversational learning.

Under the hood it's a Pipecat voice agent with a 5-phase session state machine — **Opening → Scoping → Teaching → Recap → Closing** — and five structured tools (`set_topic`, `add_concept_covered`, `mark_for_later`, `recap_session`, `end_session`) that turn every spoken session into queryable data. That structured trail is what makes the next step — observability and evaluation with Cekura — possible.

## 2. Demo video (<60s)

**🎬 [Watch the 60-second demo](https://drive.google.com/file/d/150KIve1JKUN_98mDb12p2A8shQh69b1P/view?usp=drive_link)**

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

**Stack:** Pipecat 1.3.0 · OpenAI GPT-4.1 (Responses API) · Gradium STT/TTS · Daily WebRTC · Pipecat Cloud. Deployed as a second agent (`learn-bot`) alongside the starter `flower-bot`.

**What Pipecat solved for us:** voice agents normally need weeks of WebRTC, VAD, turn-taking, STT→LLM→TTS streaming, barge-in handling, and deployment plumbing. Pipecat collapsed all of that into one Python file + one TOML. We built the tutor logic in an afternoon — not because the logic is small, but because everything *under* it was already solved. **Stripe-for-voice-infra is the right analogy.**

### Cekura — evaluating and improving the tutor

We used Cekura as the **evaluation + observability** layer for tutoring sessions. The five Pipecat tools emit structured events (`set_topic`, `add_concept_covered`, `mark_for_later`, `recap_session`, `end_session`) that map directly onto the kind of data Cekura is built to observe — turning each free-form spoken conversation into queryable, comparable data:

- **Concepts covered per session** — what the user actually got, not what they were exposed to.
- **Time-to-understanding** — measured between when a topic is set and when the user signals comprehension (e.g. asking the next follow-up).
- **Marked-for-later trail** — what users wanted to come back to but didn't have time for. Surfaces real demand.
- **Trending topics across users** — what the whole user base is learning this week.

**What we were trying to accomplish:** turn "I had a 10-minute chat with my bot" into something a product team can actually look at. Did the user learn? How fast? What did they bounce off of?

**How we used the evaluator:** we ran `/cekura-report` against `learn-bot` with 10+ generated scenarios (curious-novice, expert-pushing-deeper, distracted-walker, topic-jumper, give-up-early). The early run surfaced two clear failure modes that the prompt didn't address: (1) the bot sometimes stayed in **Scoping** forever instead of committing to a topic when the user was vague, and (2) it skipped **Recap** when the user said "I have to go." We tightened the system prompt around both — explicit "commit to a topic within 2 turns" and "if the user signals end, run `recap_session` *before* `end_session`" — and the next Cekura run showed both behaviors land cleanly. That's the loop the platform makes easy: run, look at where it fails, fix the prompt, re-run.

### Nemotron — staged, not shipped

We did not swap the LLM to Nemotron in v1. The repo includes a Nemotron variant (`server/bot-nemotron.py`) that we plan to test side-by-side post-hackathon: same prompt, same tools, swap GPT-4.1 for Nemotron 3 Super 120B, compare in Cekura. Honest answer for now — we wanted to ship one solid agent, not two half-finished ones.

## 4. What we built during the hackathon

Forked from the public Pipecat starter at [pipecat-ai/yc-voice-agents-hackathon](https://github.com/pipecat-ai/yc-voice-agents-hackathon). Everything below is **new** for this hackathon:

| File | What it is |
|---|---|
| `server/bot-learn.py` | New Pipecat agent. Started as a copy of `bot-gpt.py`, then ripped out the flower-shop tools, swapped in our tutor tools, and replaced the system prompt with the 5-phase Confucius pedagogy. |
| `server/learn_backend.py` | New module. `SessionState` dataclass + `make_tools()` factory + in-memory `_SESSIONS` store. ~150 lines, no dependencies outside Pipecat. |
| `server/llm_context.md` | Curated context appended to the system prompt at startup — what the tutor knows about its own pedagogy, tools, and constraints. |
| `server/cekura_client.py` | Thin client that ships our Pipecat transcripts to Cekura with the right `transcript_type` and flattened metadata. |
| `server/Dockerfile` (modified) | Repointed at `bot-learn.py` + `learn_backend.py`; copies `cekura_client.py` into the deploy image. |
| `server/pcc-deploy.toml` (modified) | New agent name `learn-bot`, new secrets set `learn-bot-secrets`. |
| `docs/plans/2026-05-30-confucius-learning-bot-design.md` | Design doc — architecture, 5-phase state machine rationale, what's in/out of v1. |
| `docs/plans/2026-05-30-confucius-learning-bot-plan.md` | 10-task implementation plan executed during the hackathon. |
| `docs/plans/2026-05-30-confucius-cekura-dashboard.html` | Mock of the Cekura dashboard we wired up for tutoring metrics. |
| `docs/plans/2026-05-30-confucius-demo-video-script.html` | Demo video script + storyboard. |
| `docs/plans/2026-05-30-confucius-pitch-deck.html` | Pitch deck for judging. |

**Not new (borrowed from the starter):** Pipecat pipeline scaffolding, VAD/turn-detection, Daily transport setup, Gradium STT/TTS wiring, the Twilio configuration block in [`docs/STARTER.md`](docs/STARTER.md). We left the original `bot-gpt.py` and `bot-nemotron.py` untouched.

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
- Tool-call docstrings leak into the model's view of *parameters* — we had a `run_llm` mention in `end_session`'s docstring and the LLM started passing `run_llm=True` as if it were an argument. Once we trimmed the docstring, it stopped. Worth calling out in the docs.

### Cekura

**What worked:**
- The MCP + skills setup via `/plugin install cekura@cekura-skills` was the fastest tool onboarding of the day. Slash commands meant we never left Claude Code.
- `/cekura-report` against a Pipecat-typed agent is the right shape for a self-improvement loop: one command runs 10+ generated scenarios, you read the failure transcripts, edit the prompt, re-run. The whole iteration is ~5 minutes.
- Mapping our 5 Pipecat tool events onto Cekura's observability model was intuitive — the data model "just fit."

**What we'd improve — biggest ask:**
- **Custom product metrics with trend views.** Cekura's eval metrics are great for "did the agent do the task," but Confucius cares about *product* metrics — concepts-per-session, time-to-understanding, marked-for-later rate. We can log these as custom fields, but there's no first-class way to **plot a single metric's trend over time across runs** so we can see if last night's prompt change actually moved the needle. The shape we want: "show me `concepts_per_session` p50 by day for the last week, broken down by agent version." Right now we'd have to export and graph this ourselves. A self-improvement loop is only as good as the dashboard you trust to tell you it's working — that's the gap.

### Nemotron

We didn't run Nemotron in v1, so we don't have substantive feedback. Planned for the side-by-side eval post-hackathon: same prompt, swap LLMs, compare in Cekura. Anything we have to say now would be guessing — and the judges asked for honest feedback, so we'll send real notes after the eval.

## 6. Live link

The deployed `learn-bot` runs on Pipecat Cloud. To try it, follow the local-run steps in [`docs/STARTER.md`](docs/STARTER.md) (substitute `bot-learn.py` for `bot-gpt.py`) — best with earbuds + iOS Safari. Tap **Connect**, grant mic, ask anything. ~8-minute sessions are the default.

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

Full setup, deployment, and Twilio wiring details: [`docs/STARTER.md`](docs/STARTER.md).

## Credits

Built on **[Pipecat](https://pipecat.ai)** · evaluated with **[Cekura](https://cekura.com)** · STT/TTS by **[Gradium](https://gradium.ai)** · transport by **[Daily](https://daily.co)** · hackathon hosted by **YC**.
