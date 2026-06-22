# Policy Document Puller

A hosted web app that logs into a user's insurance carrier portals — handling
login **and MFA** — then finds and renders their policy documents. Three carriers,
end to end: **State Farm**, **Lemonade**, and **Goodcover**.

The interesting part isn't the UI. It's that these portals run real bot
detection and are nothing alike, so the speed comes from a deliberate choice:

> **Talk to each carrier's backend APIs directly — for both login and documents —
> and only use a browser where bot defenses force it.**

Getting there meant reverse-engineering each carrier's auth flow. The full story
is in **[JOURNEY.md](JOURNEY.md)** (plain language) and the latency work in
**[OPTIMIZATIONS.md](OPTIMIZATIONS.md)**.

---

## The three carriers

- **Lemonade** — fully reverse-engineered. The entire login (emailed OTP) and
  document fetch run as direct HTTP. **No browser at all.**
- **State Farm** — the hard one. Real bot detection and a multi-step Okta login.
  Reverse-engineered into direct HTTP, except for one step the bot defense guards
  with a browser-only puzzle — so a brief browser mints **just that token** and
  everything else is direct. Login routes through a mobile proxy (State Farm
  rejects datacenter IPs); the document fetch goes direct. Automatic fallback to a
  full browser flow if the API path ever breaks.
- **Goodcover** — the simplest (email/password, no MFA), but its API is brittle
  and tightly coupled to its frontend, so it's **deliberately kept on the
  browser**. Already fast; not worth reverse-engineering.

## Latency (budget: MFA submit → document on screen, < 8s)

Measured on the hosted DigitalOcean VM (4 GB). All three land under budget:

| Carrier | Login → document | Notes |
|---|---|---|
| Lemonade | ~3–4s | Pure HTTP, no browser |
| Goodcover | ~5–6s | Browser, no MFA |
| State Farm | ~5–8s | Browser mints one token; login proxied, docs direct |

Per-optimization breakdown in **[OPTIMIZATIONS.md](OPTIMIZATIONS.md)**.

---

## Quick start

### Docker (easiest)

```bash
docker build -t policy-puller .
docker run --rm -p 8000:8000 policy-puller
#   add -e SOAX_USER=… -e SOAX_PASS=…  for State Farm from a datacenter/server host
```

Open http://localhost:8000 → pick a carrier → enter credentials → enter the MFA
code when prompted → the document renders. Xvfb (for State Farm's browser) and the
stealth Chromium are baked into the image.

### Local (Python 3.12)

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/patchright install chromium          # stealth browser for State Farm
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Notes:
- **No `.env` required.** The only setting is `SOAX_*` (the mobile proxy), and it's
  only needed for State Farm *from a datacenter/server IP* — on a residential
  connection State Farm goes direct, and Lemonade/Goodcover never use it.
- You supply credentials at runtime — typed in, used in memory, **never written to
  disk or logged** (the MFA code too). To exercise a carrier you need an account on
  it; the **Loom** shows a full run on real policies.

---

## Architecture

```
frontend/index.html      bare HTML/JS: carrier dropdown, cred fields,
                         MFA prompt, PDF <iframe>
        │  JSON / PDF only
        ▼
app/main.py              FastAPI: /api/carriers, /api/prewarm, /api/login,
                         /api/mfa, /api/documents, /api/documents/{id}/pdf
app/session.py           SessionStore — holds each user's session warm across
                         login → MFA → fetch
app/browser.py           launches the right transport per carrier's LaunchSpec
app/carriers/base.py     CarrierAdapter interface + LaunchSpec
app/carriers/_api.py     ApiSession — the HTTP transport (curl_cffi, no browser)
app/carriers/_sf_idx.py  State Farm's Okta login, reverse-engineered as HTTP
app/carriers/{lemonade,statefarm,goodcover}.py
```

Every carrier maps onto the same lifecycle:

```
start_login → submit_mfa → list_documents → fetch_pdf
```

Each adapter declares a `LaunchSpec` with a **`transport`** — `"browser"`
(Playwright) or `"api"` (curl_cffi, no browser) — and the same adapter code runs
over either, dispatching on the session type. State Farm uses this to fall back
from API to browser automatically. Adding a carrier = writing one adapter;
nothing else changes.

**Two invariants enforced everywhere:** credentials are never persisted or logged,
and document links are **always discovered at runtime** from the carrier's API,
never hardcoded (they're session-scoped and would break).

---

## Reliability & session reuse

`SessionStore` keeps each user's authenticated session warm between `login → MFA →
fetch`, so it survives the human MFA wait and repeated fetches. Sessions are reaped
after a TTL of inactivity (`SESSION_TTL`, default 600s) and on shutdown. Nothing is
persisted; PDFs stream straight to the client.

For State Farm, a **prewarm** step runs the slow browser token-mint in the
background the moment you pick the carrier and type your username — so the "log in"
click only pays for the fast HTTP steps. (See [OPTIMIZATIONS.md](OPTIMIZATIONS.md).)

## Hosting

Deployed on a DigitalOcean droplet (Ubuntu, 4 GB) via the Docker image above on
port 8000. The app and the stealth browser are co-located so browser actions stay
local and fast; State Farm's browser runs under Xvfb inside the container.

## Security & credential handling

- Credentials are typed into the UI, used in memory, and **never** written to disk,
  logged, or persisted. The MFA code is the user's, supplied at runtime.
- Each session is ephemeral — no profile reuse, no stored cookies.
- PDFs stream to the client, never saved server-side.
- The only on-disk secret is the proxy credential in `.env` (gitignored).
- The demo binds publicly for convenience; production would put it behind
  auth/TLS and lock the port down.

## Known limitations / next steps

- **Reverse-engineered paths are fragile.** If a carrier changes its login or an
  endpoint, the fast path can break — hence State Farm's automatic browser
  fallback. The per-carrier adapter is the blast radius: a change touches one file.
- **Document scope.** We pull what the carrier's API currently exposes. State Farm
  surfaces documents by year; an aged-out binder needs a wider date range.
- **Proxy rotation / pooling** for State Farm at scale (per-user sticky IPs).
