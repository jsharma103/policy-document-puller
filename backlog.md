# Backlog

Deferred for the take-home deadline — not blocking the required deliverables
(working code + README, two carriers, hosted, Loom, session links). Captured so
they're not lost.

---

## Public hosting (clean HTTPS / stable URL)

**Status:** deferred. App is live on the droplet at `http://167.172.137.214:8000`
(HTTP, raw IP), which satisfies "hosted somewhere real."

**Tried + reverted:** a Cloudflare quick tunnel (`cloudflared tunnel --url`) gave
an HTTPS URL with zero setup, but Chrome flags every `*.trycloudflare.com` as
"Dangerous" (Safe Browsing) — worse on camera than the raw IP's "Not Secure".
Removed it; demo on the raw IP.

**The real path to clean HTTPS** (if ever wanted): a *named* Cloudflare tunnel
or Caddy/nginx + a **real domain** (no Safe Browsing flag, proper TLS).

**Caveats:**
- Infer can't exercise it anyway — the flow needs the user's own portal
  credentials + phone for MFA. The Loom is what demonstrates the real run.
- It's an open credential-handling endpoint. **Shut it down / firewall after the eval.**

---

## Containerize (Dockerfile)

**Status:** deferred. A clean README + venv covers "run locally."

**What it'd add:** reproducible `docker run` boot — no pyenv / Playwright-install
friction on the reviewer's machine. A real "production ready" signal (criterion
#1).

**Sketch:**
- Base on `mcr.microsoft.com/playwright/python` (browser system deps already in).
- `pip install -r requirements.txt`; `patchright install chromium`.
- Add **Xvfb** in the image (State Farm is headful) and launch uvicorn under
  `xvfb-run`.
- Pass `SOAX_USER`/`SOAX_PASS` as env at `docker run`.

**Caveats:**
- Lemonade runs cleanly in-container (headless). State Farm in-container still
  needs the SOAX proxy + the user's credentials, so it mainly proves the server
  *boots* — not a full document pull for the reviewer.
- ~30–45 min to write and test properly.

---

## Trusted-device persistence (skip MFA on repeat runs)

**Status:** deferred — and deliberately *not* wanted for the take-home.

**What it is:** State Farm runs on Okta. After the first MFA, Okta drops a
long-lived device cookie (the "DT" device token) into the browser profile;
later logins from that trusted device skip the SMS step. Our app uses a fresh
ephemeral context per run, so that cookie is discarded and State Farm asks for
MFA every time (this is also why a real Chrome profile stops asking but
Playwright doesn't).

**How we'd do it:** persist `storage_state` (or a `user-data-dir`), opt into
"Remember me," and reload it on subsequent runs so the DT cookie carries over —
Okta then skips MFA. A natural extension of session reuse (criterion #5):
returning users skip the code.

**Why deferred / not for the demo:**
- The assignment is *about* handling MFA (flow step 4) — skipping it would mean
  not exercising the graded feature. "Always prompts for MFA" is the correct
  demo behavior.
- It writes a device token to disk, which cuts against the "nothing about my
  accounts is persisted" stance we hold for the submission.

---

## Other deferred (see README "Known limitations")

- State Farm latency: resource-blocking (drop images/ads/analytics) to cut
  requests through the mobile proxy. Gated/opt-in today because it can interfere
  with the SPA/anti-bot; would need careful testing before enabling.
- Multi-account document enumeration (`getAccounts` + wider date range) so older
  documents (e.g. an aged-out policy binder) surface.
- Per-user sticky proxy IP pooling for State Farm at scale.
- Auth + rate limiting on the API for any real public deployment.
