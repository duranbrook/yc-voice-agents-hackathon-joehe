# Confucius client

Static web app: **Google Sign-In → Pipecat Cloud session-mint → Daily WebRTC join**.

This replaces the Pipecat sandbox URL as the entry point for memory v2, so the bot can identify returning learners and remember what they covered.

## Architecture

```
User → index.html → Google Sign-In → POST /v1/public/learn-bot/start
                                      with { data: { user_id, email, name } }
                  → daily.join(room_url, token)
                  → live WebRTC session with learn-bot
```

The bot reads `user_id` from `runner_args.body` and loads memory from Supabase.

## Configure

Edit `index.html` and replace `REPLACE_WITH_GOOGLE_CLIENT_ID` with the OAuth client ID from Google Cloud Console.

The Pipecat public API key (`pk_…`) is already embedded in `app.js` — safe to expose.

## Run locally

```bash
cd client
python3 -m http.server 3000
# then open http://localhost:3000
```

Note: Google Sign-In's `data-client_id` must include `http://localhost:3000` (and your Vercel domain) in the OAuth client's authorized origins / redirect URIs in Google Cloud Console.

## Deploy

```bash
npm install -g vercel    # if not installed
cd client
vercel --prod
```

Vercel deploys; outputs a URL like `https://confucius-xxx.vercel.app`. Add that URL to:

1. **Google Cloud Console → OAuth Client → Authorized JavaScript origins + Authorized redirect URIs**
2. **Supabase → Authentication → Providers → Google → Authorized redirect URLs** (`https://<project>.supabase.co/auth/v1/callback` is automatic)

## Trust model (v2)

The Google JWT is NOT verified server-side. We decode the payload client-side and pass the `sub` claim to the bot as `user_id`. Spoofable, but the worst case is reading/writing another user's learning history (no payment data, no PII beyond email).

v3 will pass the full `id_token` and have the bot verify via `supabase.auth.getUser(id_token)`.
