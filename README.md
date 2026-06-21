# Policy Document Puller

A hosted web app that logs into a user's insurance carrier portals — handling
login **and MFA** — then discovers and renders their policy documents. Three
carriers, end to end: **Lemonade**, **State Farm**, and **Goodcover**.

The interesting half of this problem isn't the UI; it's that real carrier
portals run real bot detection, and the carriers are nothing alike — the
architecture exists to contain those differences:
- **Lemonade** — a modern passwordless SPA, emailed OTP, happy to be automated
  headlessly from a datacenter; docs via its own API.
- **State Farm** — an Okta-based portal with active fingerprinting, a disguised
  risk-rejection, SMS MFA, and a three-domain SSO chain (the hard one).
- **Goodcover** — a simple two-step email/password login, no MFA, headless on a
  datacenter IP; the fast, reliable case.

---

## Quick start (local)

Requires Python 3.12.

```bash
python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/patchright install chromium          # the stealth browser binary
.venv/bin/uvicorn app.main:app --reload --port 8000
```

Open http://localhost:8000 → pick a carrier → enter portal credentials → enter
the MFA code when prompted → the policy document renders.

Notes:
- **No `.env` is required to run.** The only setting is `SOAX_*` and it's
  optional — it's the mobile proxy State Farm needs *from a datacenter/server IP*.
  Unset, State Farm just goes direct (fine on a residential connection), and
  Lemonade/Goodcover never use it. So `git clone` → the four commands above →
  it boots.
- **Lemonade** and **Goodcover** run headless and need no configuration.
- **State Farm** runs **headful** (it detects headless — see below), so a Chrome
  window opens locally. Residential IP → no proxy; datacenter/server → set the
  SOAX proxy (`cp .env.example .env`, fill `SOAX_USER`/`SOAX_PASS`,
  `set -a; . .env; set +a`).
- You supply carrier credentials at runtime — typed into the browser, used in
  memory, never written to disk or logged (the MFA code too). To exercise a
  carrier you need an account on it; the **Loom** shows a full run on real policies.

---

## Architecture

```
frontend/index.html      bare HTML/JS: carrier dropdown, cred fields,
                         MFA prompt (only when asked), PDF <iframe>
        │  JSON / PDF only
        ▼
app/main.py              FastAPI: /api/carriers, /api/login, /api/mfa,
                         /api/documents, /api/documents/{id}/pdf
app/session.py           SessionStore — holds each user's live browser warm
                         across login → MFA → fetch (and re-runs)
app/browser.py           launches a browser per carrier's LaunchSpec; resolves
                         proxy secrets from env so adapters never touch them
app/carriers/base.py     the CarrierAdapter interface + LaunchSpec
app/carriers/lemonade.py
app/carriers/statefarm.py
```

Every carrier maps onto the same four-step lifecycle:

```
start_login → submit_mfa → list_documents → fetch_pdf
```

…but *how* each step works (browser mode, egress, login shape, SSO timing, which
document is "the" document) lives entirely inside that carrier's adapter, behind
a `LaunchSpec` that declares how its browser must run. Adding a carrier =
writing one adapter; nothing else changes. The frontend only ever speaks to the
JSON/PDF API, so front and back are cleanly separable.

**Two principles enforced everywhere:** credentials are never persisted or
logged, and document IDs/links are **always discovered at runtime** from the
carrier's own API — never hardcoded (they're session-scoped and would break).

---

## The three carriers

**Lemonade (passwordless + API).** The user enters their email, Lemonade emails
a 6-digit code. The dashboard calls its own API
(`/web_dashboard/accounts/home/policies`); each policy carries a `form_url` that
*is* the PDF, regenerated per session — so we read it from the response instead
of hardcoding. Headless, direct datacenter IP, single domain.

**State Farm (the hard one).** Username + password + SMS MFA, behind real bot
detection, then a multi-domain SSO chain. See below.

**Goodcover (the fast one).** A two-step email/password login, **no MFA**, behind
Cloudflare bot management that doesn't challenge patchright. Headless on a direct
datacenter IP — no proxy, no Xvfb — so it hosts as fast as it runs locally. The
policy PDF is the "Download Policy" link's `href`, discovered at runtime and
fetched through the authed context (same pattern as Lemonade's `form_url`).
Demonstrates that the MFA prompt only appears when a carrier actually asks.

---

## Anti-bot & fingerprinting (criterion #6)

State Farm is where the real work is. What we found, with evidence:

- **Plain Playwright is silently blocked** at the login button. Fix:
  [`patchright`](https://github.com/Kaliiiiiiiiii-Vinyzu/patchright) (drop-in
  stealth Playwright) using its **bundled Chromium** at the **default 1280×720
  viewport**. (Forcing real Chrome via `channel="chrome"` or `no_viewport`
  changes the responsive layout enough to break the MFA-method screen — the
  defaults are the proven config.)

- **Headless is detected — even with stealth, even from a residential IP — and
  disguised as a bad password.** Same IP, same credentials: headful reached MFA,
  headless got *"You've entered an incorrect user ID or password."* That's why
  State Farm runs **headful under Xvfb** on the server. (Lemonade tolerates
  headless fine.)

- **Datacenter IPs are risk-rejected, also disguised as a bad password.** Proven
  by running the *same* credentials through a residential IP, which worked.

- **Residential proxies don't help** — commercial residential providers block
  insurer domains (IPRoyal returns HTTP 403 on the CONNECT to
  `auth.proofing.statefarm.com`, while neutral sites work through the same
  proxy). What works is a **mobile/4G proxy** (SOAX): Okta accepts a real carrier
  IP. Sticky sessions rotate ~every 5 min; a flagged IP re-triggers the fake
  "bad password," so rotate the `sessionid`.

- **A remote-browser service (Browserbase) cleared the bot check** but added 20s+
  of round-trips per action — over the latency budget. So the browser is
  **co-located with the app** on one box instead.

- **Post-MFA SSO** spans three domains: `auth.proofing` → token → passkey
  interstitial (auto-dismissed) → callback to `my.statefarm.com` → the Document
  Center (its own SSO handshake) → viewer. We must let the session **settle on
  `my.statefarm.com`** before touching the Document Center, or it de-auths and
  bounces to login; and we must wait for the Document Center SPA to be **warm**
  (its `customerMetadata` call returns) before deep-linking a document, or the
  SPA redirects to its default route.

**Tradeoff, stated plainly:** portal automation like this needs upkeep when a
carrier redesigns its login or rotates an endpoint. The per-carrier adapter is
the blast radius — a redesign touches one file, not the app.

---

## Latency (criterion #4: MFA submit → document on screen)

- **Goodcover: ~4–5s** — comfortably under budget, *hosted included* (headless,
  datacenter, no proxy, single domain).
- **Lemonade: ~5.7s** — meets the 8s budget. One domain, one API hop.
- **State Farm: ~9–20s** — does not, and that's structural, not unoptimized. We
  cut it from **41.5s → 9.15s** (on a direct residential connection) by replacing
  fixed sleeps with event-driven waits, dropping a `networkidle` settle that
  always timed out, and fetching the document's landed URL instead of waiting on
  a stream that never fired. The remaining floor is real: a three-domain SSO with
  mandatory redirect hops, run headful, over a mobile proxy — and hosted it's
  higher still (every request crosses the proxy). The single-domain carriers
  simply have far less to do.

This is the honest shape of the problem: the budget is reachable for modern
single-domain carriers (Goodcover, Lemonade) and not for a legacy multi-domain
one whose own anti-bot forces the slow, proxied, headful path.

---

## Reliability & session reuse (criterion #5)

`SessionStore` keeps each user's authenticated browser (context + page) warm
between `login → MFA → fetch`, so the session survives the human MFA wait and
repeated document fetches. Sessions are reaped after a TTL of inactivity
(`SESSION_TTL`, default 600s) and on shutdown. Nothing is persisted; PDFs are
streamed straight to the client.

---

## Hosting (criterion #3)

Deployed on a DigitalOcean droplet (Ubuntu, 4 GB). The app and the stealth
browser are co-located (keeps actions local and fast). State Farm's headful
browser runs under **Xvfb**:

```bash
SOAX_USER=… SOAX_PASS=… \
  xvfb-run -a -s "-screen 0 1920x1080x24" \
  .venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000
```

---

## Security & credential handling

- Carrier credentials are typed into the UI, used immediately to drive the
  browser, and **never** written to disk, logged, or persisted. The MFA code is
  the user's, supplied at runtime.
- Each browser context is ephemeral (no profile reuse, no stored cookies).
- PDFs are streamed to the client and never saved server-side.
- The only on-disk secret is the proxy credential in `.env` (gitignored).
- The demo deployment binds publicly for convenience; a real deployment would
  put it behind auth/TLS and lock the port down.

---

## Known limitations / production next steps

- **Document scope.** We pull what the carrier's API currently exposes. State
  Farm's Document Center surfaces *current* documents, so an older policy binder
  that has aged out won't appear (we hit exactly this). Production: enumerate all
  accounts via `getAccounts` and widen the date range.
- **Proxy rotation / pooling** for State Farm at scale (per-user sticky IPs).
- **Login-shape drift.** Carriers A/B-test login UIs (State Farm serves both a
  two-step and a combined form); the adapter handles both, but new variants need
  adapter updates — contained to one file.
