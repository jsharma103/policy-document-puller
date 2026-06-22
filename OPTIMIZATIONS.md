# Latency optimizations

Guiding principle: **use a real browser only where the carrier's bot defenses
force it, run everything else as plain HTTP, and pre-warm any browser-dependent
step off the critical path.** Numbers are measured on the hosted DigitalOcean VM
(4 GB, mobile-proxy egress where required).

Two metrics:
- **Login → MFA prompt** — clicking Log in until the code prompt appears.
- **MFA submit → document** — submitting the code until the PDF renders (the 8s budget).

Each carrier below: **current** architecture and latency first, then what was
tried before it, newest to oldest, with the latency at each stage.

---

## State Farm

### The problem — why it can't be pure HTTP

The whole goal is to skip the browser and drive the login as plain HTTP. State
Farm makes that impossible for one step: its login POST is gated by **AWS WAF Bot
Control**, which requires an `aws-waf-token` that's **computed by JavaScript
running in a real browser**. There's no way to mint that token from HTTP alone —
headless is detected and gets no token, and without it the credential POST is
silently rejected. So a pure-`curl_cffi` login can't get past the password step.
Everything *else* in the flow (the Okta IDX exchange, the document APIs) is
plain HTTP — only the token itself needs a browser.

### Current — hybrid, ~4.8s combined (~5–8s typical with proxy variance)

The hybrid answers exactly that constraint: a brief stealth browser mints **only**
the one JS-computed `aws-waf-token` the WAF requires, and the entire Okta Identity
Engine (IDX) login and the Document Center fetch then run as `curl_cffi` HTTP.
Login egresses through a mobile proxy (State Farm rejects datacenter IPs); the
Bearer-authed document APIs are IP-agnostic, so they're fetched direct from the VM.
The token-mint plus the credential-free login steps are pre-warmed off the click,
and there's an automatic fallback to the full browser flow if any API step fails.

- **Login → MFA prompt: ~1.8s** · **MFA submit → document: ~3.0s**

### How we got here (newest → oldest)

- **Before 2-stage prewarm (`identify` on username-blur):** the Log in click still
  ran the username-only IDX steps. → Login → MFA **2.91s**.
- **Before pre-warming `interact` / `introspect`:** the IDX bootstrap ran at login
  instead of at carrier-select. → Login → MFA **4.90s**.
- **Before direct document fetch:** documents were pulled through the mobile proxy,
  like login. → MFA → document **6.22s** (first working hybrid, ~11.5s combined).
- **Before the hybrid (original):** a full headful Playwright login driving the
  real SPA. → **~17s just to the MFA prompt** — well over budget, and the reason
  for the rewrite.

---

## Lemonade

### Current — pure-API, ~3–4s login → document

No browser at all. The whole flow is reverse-engineered HTTP over `curl_cffi`
(impersonating Chrome, which clears Lemonade's Cloudflare check at the network
layer): `GET /login` (scrape CSRF) → `presign` (sends the OTP) → `signin` → follow
the OAuth redirect chain → `policies` API → fetch the policy `form_url` PDF. A
Playwright fallback covers an API failure (robustness, not speed).

- **Login → document: ~3–4s**

### How we got here (newest → oldest)

- **Before pure-API:** a headless Playwright browser flow — launch Chromium, render
  the login SPA, drive it, read the dashboard. → **~5.7s**, and far heavier on the
  2-vCPU host. Removing the browser launch + SPA render is the entire win.

---

## Goodcover

### Current — browser, ~5–6s (no MFA)

The simplest carrier (email/password, no MFA), kept on a headless browser on a
direct datacenter IP. The PDF is the "Download Policy" link's `href`, fetched
through the authenticated context.

- **Login → document: ~5–6s**

### What was tried before (newest → oldest)

- **Pure-API — evaluated and rejected.** Its login is protobuf-encoded,
  persisted-GraphQL behind Cloudflare: brittle to hand-roll and tightly coupled to
  their frontend build. It was never built — the optimization here was the
  **judgment call** to not reimplement a fragile path for no real gain on an
  already-fast, MFA-free carrier.

---

## Summary

| Carrier | Approach | Login → MFA | MFA → document |
|---|---|---|---|
| State Farm | Hybrid (browser mints WAF token → HTTP IDX + direct docs) | ~1.8s | ~3.0s |
| Lemonade | Pure-API (`curl_cffi`), browser fallback | — (email + OTP) | ~3–4s end-to-end |
| Goodcover | Browser (no MFA) | — | ~5–6s end-to-end |

The same prewarm pattern underlies the fast carriers: the moment a carrier is
selected (and, for State Farm, the moment the username is entered), the slow,
browser-dependent work runs in the background so the user-facing clicks pay only
for fast HTTP.
