// app.js — Confucius voice tutor client (memory v2)
//
// Flow:
//   1. User signs in with Google (GIS library)
//   2. We extract sub/email/name from the credential JWT
//   3. POST to Pipecat Cloud's session-mint endpoint with that user data
//   4. Join the returned Daily room
//
// JWT is NOT verified server-side in v2 — trust model documented in design §3.

const PIPECAT_API_KEY = "pk_7678dd2b-dec7-4b68-966c-4d7509916ce7";
const PIPECAT_START_URL = "https://api.pipecat.daily.co/v1/public/learn-bot/start";

let userData = null;
let dailyCall = null;

window.onGoogleSignIn = (response) => {
  // The JWT credential is in response.credential. Decode the payload only.
  // (Not cryptographic — v2 trusts the claim. v3 will verify server-side.)
  try {
    const payload = JSON.parse(atob(response.credential.split(".")[1]));
    userData = {
      user_id: payload.sub,
      email: payload.email,
      name: payload.name,
    };
    document.getElementById("user-name").textContent = userData.name;
    document.getElementById("signin").hidden = true;
    document.getElementById("ready").hidden = false;
  } catch (e) {
    alert("Sign-in failed: " + e.message);
  }
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
    if (!r.ok) {
      throw new Error(`Pipecat session-mint failed: ${r.status} ${await r.text()}`);
    }
    const { room_url, token } = await r.json();

    dailyCall = window.DailyIframe.createFrame(document.getElementById("daily-frame"), {
      iframeStyle: { width: "100%", height: "500px", border: 0, borderRadius: "10px" },
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
    try {
      await dailyCall.leave();
      dailyCall.destroy();
    } catch (_) {
      // ignore — disconnect race conditions are fine
    }
    dailyCall = null;
  }
  document.getElementById("call").hidden = true;
  document.getElementById("ready").hidden = false;
  const sb = document.getElementById("start-btn");
  sb.disabled = false;
  sb.textContent = "Start Learning";
});
