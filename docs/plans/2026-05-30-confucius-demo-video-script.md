# Confucius — 1-Minute Demo Video Script

**Length:** 60 seconds
**Format:** Vertical (9:16) for IG/TikTok/Shorts, with a horizontal (16:9) re-cut for the YC submission
**Voiceover:** Narrator (you) + raw Confucius bot audio captured live
**Aspect:** Show, don't tell — every claim ("interactive", "voice-only", "memory") is demonstrated, not narrated

---

## The pitch in one line

> **YouTube and podcasts talk *at* you. Confucius talks *with* you — so the eight minutes between gym sets becomes a tutoring session that actually sticks.**

---

## Beat-by-beat storyboard

| Time | Shot | On-screen text | Audio (VO / bot / sfx) |
|---|---|---|---|
| **0:00–0:04** | Cold open: phone clipped to runner's armband, AirPods in, jogging through a park at golden hour. No UI visible. | (none) | **VO:** "You've got eight minutes between sets. Or a twenty-minute commute. Or a run." |
| **0:04–0:10** | Quick cut montage: someone trying to read a textbook on a treadmill, someone squinting at a YouTube lecture on a bumpy bus, someone scrubbing a podcast trying to find a part they missed. Each shot ~2s. | "Textbooks? No." → "YouTube? One-way." → "Podcasts? Can't ask questions." | **VO:** "Textbooks don't fit. YouTube talks *at* you. Podcasts can't answer back." |
| **0:10–0:14** | Cut to runner. They tap an AirPod. Screen overlay shows a minimal "Confucius — connected" indicator (no UI to stare at). | "meet **Confucius**" | **sfx:** soft connect chime. **Bot (clear):** "Hi, I'm Confucius. What do you want to learn about today?" |
| **0:14–0:22** | Runner still running. We hear the conversation. Optional caption track at the bottom for accessibility — but NEVER cut to the phone screen; the point is they aren't looking. | Captions only (live transcript) | **Runner:** "Quantum entanglement, but I'm a total novice." **Bot:** "Cool — want the big-picture intuition or how-it-actually-works deep dive?" **Runner:** "Big picture." |
| **0:22–0:32** | Cutaway: runner now mid-sprint, breathing harder. Bot teaches with an analogy. Show one of the actual `add_concept_covered` log lines pop in as a subtle ghost overlay — proving the bot is silently structuring the session. | tiny overlay: `[learn] add_concept_covered: "spooky action"` | **Bot:** "Imagine two coins, flipped on opposite sides of the planet. Somehow they always land the same way. That's the *vibe* of entanglement — does that land?" **Runner (between breaths):** "Yeah. But pause — remind me to come back to *why* later." **Bot:** "Noted for next session." |
| **0:32–0:42** | Runner slows to a walk, hands on hips, cooling down. Time elapsed indicator: "7:12". | "**memory →** structured recap" | **Bot:** "We've got about a minute — want me to recap before you head out?" **Runner:** "Yeah." **Bot:** "OK quick recap. We covered superposition, entanglement, and the coin analogy. You wanted to come back to *why* next time. Sound right?" |
| **0:42–0:50** | Hard cut: same conversation, replayed as a dashboard view (Cekura-style). Concepts covered as chips. "Marked for later" as a stack. A "time-to-understanding" sparkline ticking up. Trending topics across users blurring in the background. | "**observability** — coming soon with Cekura" | **VO (low, confident):** "And every session is measured. How long until *you* get a concept. What everyone's learning this week. Tutoring that gets better the more it teaches." |
| **0:50–0:56** | Back to runner walking, smiling, pulling AirPods out, slipping the phone back into the armband — phone screen never came on. | "voice-first. eyes-free. on the go." | **Bot (fading out):** "Cool. Have a good one." |
| **0:56–1:00** | End card. Logo + URL. | **Confucius**<br>your eight minutes, taught.<br>`confucius.app` (or your URL) | **VO:** "Confucius. Your eight minutes, taught." |

---

## Why each beat earns its seconds

- **0:00–0:10 (problem):** establishes the *exact* dead time slot — gym sets, commute, run — and rules out the existing options *by demonstration* (squinting, scrubbing). No talking heads.
- **0:10–0:22 (interactive proof):** the runner *interrupts the bot* — that's the moment a podcast can't match. We let the bot's reply be the demo, not the voiceover.
- **0:22–0:32 (voice-only proof):** the camera never shows the phone screen. The ghost log overlay proves the system is doing real work without demanding attention.
- **0:32–0:42 (memory proof):** "remind me to come back to *why* later" → recap names it back. This is the single most demo-able feature; give it the most screen time.
- **0:42–0:50 (observability):** label it "coming soon with Cekura" so you're not over-claiming. Show the *kind* of thing it measures: time-to-understanding, trending topics. Don't fake a real dashboard — use a clean mockup.
- **0:50–1:00 (close):** the phone never lit up. That's the whole pitch.

---

## Production notes

**Capture the bot audio for real.** Run the deployed `learn-bot` (Task 8 of [`2026-05-30-confucius-learning-bot-plan.md`](2026-05-30-confucius-learning-bot-plan.md)) on a phone in Safari, record the system audio + a lavalier on the runner, and edit the conversation down to fit. Scripted lines above are *targets* — let real Confucius output drive the final cut. If a real take has a better line, use it.

**Don't fake the dashboard.** The Cekura panel is the only mocked shot. Build it as a still frame in Figma — concept chips, a "marked for later" stack, a sparkline labeled `time-to-understanding (min)`, and a small "trending this week" word cloud. Stamp it `mockup` in 8pt grey in the corner. Honesty reads as confidence.

**Captions.** Burn-in captions for the runner's lines and the bot's lines using two different colors. Most people watch on mute first.

**Music.** None during the demo conversation — let the bot's voice carry. Add a soft pad under the VO bookends only.

**Pacing.** Cut hard. 8 distinct shots minimum. If a shot feels long, it is long.

**One thing to cut if you're over time.** The dashboard beat (0:42–0:50). The memory recap (0:32–0:42) is non-negotiable; observability can become its own follow-up post once Cekura ships.

---

## Shot list (for the shoot day)

1. Phone on armband, runner in motion — golden hour, park trail
2. Treadmill + textbook (B-roll, can be stock)
3. Bus window + YouTube on a phone (B-roll)
4. Hands scrubbing podcast app on a phone (B-roll)
5. AirPod tap, "Connected" indicator on a watch *or* a clean phone lock screen
6. Runner running, bot audio over the top — no phone in frame
7. Runner mid-sprint, hands on knees, *still talking to the bot*
8. Runner slowing to a walk, pulling out earbuds
9. End card (animate in After Effects, 4 seconds)

Plus: screen-recording of the actual `learn-bot` sandbox session for the ghost log overlay (so the log line is real, not faked).

---

## Talking points you do *not* mention in the video (save for the deck)

- Pipecat Cloud, Daily WebRTC, Gradium STT/TTS, OpenAI GPT-4.1 — infra is invisible to the user, keep it invisible in the demo
- The five-phase state machine — the recap moment *shows* it; don't name it
- "Powered by AI" — every demo says that; ours doesn't need to
