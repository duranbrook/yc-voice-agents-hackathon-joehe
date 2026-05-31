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

## 3. How we used Cekura and Pipecat

### Pipecat — the whole thing rests on it

We forked the flower-bot starter (`server/bot-gpt.py`) into a new `server/bot-learn.py` and built `server/learn_backend.py` with:

- A `SessionState` dataclass holding `topic`, `depth`, `starting_level`, `concepts_covered`, `marked_for_later`, and a wall-clock `started_at`.
- A `make_tools(session_id)` factory that closes over the session ID and produces five async tool functions wired into Pipecat's LLM service via `register_direct_function`.
- A process-local `_SESSIONS` dict keyed by an opaque session ID minted per `run_bot` call.

The 5-phase pedagogy is encoded entirely in the **system prompt**, not in code — the LLM tracks its own phase and calls the tools at the right moments. The prompt deliberately avoids "Confucius says…" aphorism style; the bot is a modern voice tutor, not a fortune cookie.

**Stack:** Pipecat 1.3.0 · OpenAI GPT-4.1 (Responses API) · Gradium STT/TTS · Daily WebRTC · Pipecat Cloud. Deployed as a second agent (`learn-bot`) alongside the starter `flower-bot`.

**What Pipecat solved for us:** voice agents normally need weeks of WebRTC, VAD, turn-taking, STT→LLM→TTS streaming, barge-in handling, and deployment plumbing. Pipecat collapsed all of that into one Python file + one TOML. We built the tutor logic in an afternoon — not because the logic is small, but because everything *under* it was already solved. **Stripe-for-voice-infra is the right analogy.**

### Cekura — measuring the tutor

We use Cekura to evaluate how well the tutor actually teaches. Three core metrics:

- **Comprehension rate** — share of sessions where the learner clearly understands the topic by the end of the call.
- **Time-to-grasp** — average seconds it takes a learner to signal understanding, broken down by topic.
- **Trending topics** — what learners are most curious about right now, ranked across all sessions.

## 4. What we built during the hackathon

A learn-on-the-go tutor for the moments your hands and eyes are occupied.

## 5. Feedback on the tools

### Cekura

**What we'd improve — biggest ask:**
- **Custom product metrics with trend views.** Cekura's eval metrics are great for "did the agent do the task," but Confucius cares about *product* metrics — concepts-per-session, time-to-understanding, marked-for-later rate. We can log these as custom fields, but there's no first-class way to **plot a single metric's trend over time across runs** so we can see if last night's prompt change actually moved the needle. The shape we want: "show me `concepts_per_session` p50 by day for the last week, broken down by agent version." Right now we'd have to export and graph this ourselves. A self-improvement loop is only as good as the dashboard you trust to tell you it's working — that's the gap.

## 6. Live link

https://pipecat.daily.co/0530id/agents/learn-bot/sandbox

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
