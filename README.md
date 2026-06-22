# Policy Document Puller

A hosted web app that logs into a user's insurance carrier portals — handling
login **and MFA** — then finds and renders their policy documents. Three carriers
end to end: **State Farm**, **Lemonade**, **Goodcover**.

The speed comes from one choice:

> **Talk to each carrier's backend APIs directly — for both login and documents —
> and only use a browser where bot defenses force it.**

Getting there meant reverse-engineering each carrier's auth flow — the story is in
**[JOURNEY.md](JOURNEY.md)**, the latency work in **[OPTIMIZATIONS.md](OPTIMIZATIONS.md)**.

## Carriers & latency

Budget: MFA submit → document on screen, **< 8s**. Measured on the hosted
DigitalOcean VM (4 GB) — all three land under it:

| Carrier | Login → doc | Approach |
|---|---|---|
| Lemonade | ~3–4s | Fully reverse-engineered. Emailed OTP. **No browser at all.** |
| Goodcover | ~5–6s | No MFA; API too brittle to reverse, so **deliberately kept on the browser.** |
| State Farm | ~5–8s | Hard one. A brief browser mints **one** bot-defense token; the rest of the Okta login + the docs run as direct HTTP. Login proxied (it rejects datacenter IPs), docs direct. Auto-fallback to a full browser flow if the API path breaks. |

## Quick start

```bash
docker build -t policy-puller .
docker run --rm -p 8000:8000 policy-puller
#   add -e SOAX_USER=… -e SOAX_PASS=…  for State Farm from a datacenter/server host
```

Or locally (Python 3.12): `pip install -r requirements.txt` → `patchright install
chromium` → `uvicorn app.main:app --port 8000`.

Open http://localhost:8000 → pick a carrier → enter credentials → enter the MFA
code → the document renders.

- **No `.env` required.** The only setting is `SOAX_*` (mobile proxy), needed only
  for State Farm *from a datacenter IP*; on a residential connection it goes
  direct, and Lemonade/Goodcover never use it.
- Credentials are typed in at runtime, used in memory, **never written to disk or
  logged**. You need your own account per carrier; the **Loom** shows a full run.

## Architecture

```
frontend/index.html      HTML/JS: carrier dropdown, creds, MFA prompt, PDF iframe
        │  JSON / PDF only
        ▼
app/main.py              FastAPI: /api/{carriers,prewarm,login,mfa,documents,...}
app/session.py           SessionStore — holds each session warm across login→MFA→fetch
app/browser.py           launches the transport per carrier's LaunchSpec
app/carriers/base.py     CarrierAdapter interface + LaunchSpec
app/carriers/_api.py     ApiSession — the HTTP transport (curl_cffi, no browser)
app/carriers/_sf_idx.py  State Farm's Okta login, reverse-engineered as HTTP
app/carriers/{lemonade,statefarm,goodcover}.py
```

Every carrier implements the same lifecycle — `start_login → submit_mfa →
list_documents → fetch_pdf` — and declares a `LaunchSpec` whose **`transport`** is
`"browser"` (Playwright) or `"api"` (curl_cffi). The same adapter code runs over
either, dispatching on session type; State Farm uses this to fall back from API to
browser automatically. Adding a carrier = one new adapter.

**Invariants:** credentials are never persisted or logged, and document links are
always discovered at runtime from the carrier's API (session-scoped, never hardcoded).

## Sessions, hosting & security

- **`SessionStore`** keeps each authenticated session warm across login → MFA →
  fetch, so it survives the human MFA wait and repeat fetches. Reaped after
  inactivity (`SESSION_TTL`, default 600s) and on shutdown. Nothing persisted; PDFs
  returned directly in the response, never written to disk.
- **Prewarm:** State Farm runs the slow browser token-mint in the background as you
  pick the carrier and type your username, so the "log in" click pays only for the
  fast HTTP steps.
- **Hosting:** DigitalOcean droplet (Ubuntu, 4 GB), Docker on port 8000; app and
  browser co-located, State Farm's browser under Xvfb in the container.
- The only on-disk secret is the proxy credential in `.env` (gitignored). The demo
  binds publicly; production would add auth/TLS and lock the port down.

## Limitations / next steps

- Reverse-engineered paths are fragile — a login/endpoint change can break the fast
  path (hence State Farm's browser fallback). The blast radius is one adapter file.
- Document scope is what the carrier's API exposes (State Farm is by year; an
  aged-out binder needs a wider range).
- Proxy rotation / pooling for State Farm at scale (per-user sticky IPs).
