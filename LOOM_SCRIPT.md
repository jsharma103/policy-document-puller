# Loom script — Policy Document Puller (~3 min)

URL: http://167.172.137.214:8000 · Order: **State Farm → Lemonade → Goodcover**

Each section is **[DO]** = what to click, **[SAY]** = narration. Keep the demo
running while you talk over it.

---

## 0:00 — Intro (~15s)

**[SAY]**
> This is a hosted FastAPI + Playwright app that logs into three insurance
> carriers — handling password and MFA — and pulls the policy document back,
> with a hard budget of **8 seconds from MFA submit to the PDF on screen**.
> One constraint up front: **no credentials are ever stored** — they're typed
> in live and used in-memory, nothing persisted, nothing logged.

> I'll start with the hardest carrier, State Farm, because it drove the whole
> architecture.

---

## 0:15 — State Farm: the architecture (~70s)

**[DO]** Select **State Farm**. Type the username, tab out, type the password.
(Narrate while the prewarm runs invisibly.)

**[SAY] — the problem**
> State Farm is the hard one: real bot detection, **AWS WAF Bot Control** on the
> login POST, and a multi-domain **Okta Identity Engine** flow. A naïve headful
> browser login was about **17 seconds** just to the MFA prompt — way over budget.

**[SAY] — the hybrid (the key idea)**
> So I reverse-engineered it into a **hybrid**. The WAF only gates one thing — a
> JavaScript-computed token on the password step. So a **brief headless browser
> mints just that one token**, and then the *entire* rest of login — the whole
> Okta flow — runs as plain HTTP using `curl_cffi`, which impersonates Chrome's
> TLS fingerprint. No login SPA, no page renders.

**[SAY] — prewarm (why the click is fast)**
> And the slow browser part is moved **off the clock**. The moment I pick the
> carrier, it mints the token and runs the steps that don't need credentials.
> When I leave the username field, it runs the username-only steps. So by the
> time I actually click **Log in**, all that's left is password plus the OTP
> request — fast HTTP.

**[DO]** Click **Log in** → MFA prompt appears (~1.8s). Point at the latency line.

**[SAY]**
> Login to MFA prompt — under two seconds.

**[DO]** Grab the OTP, enter it, **Submit code** → PDF renders. Point at latency.

**[SAY]**
> MFA submit to document — about three seconds, comfortably under the eight-second
> budget. And the document fetch is a direct Bearer-authed API call, no proxy.

**[SAY] — the fallback (resilience)**
> One thing I want to call out: this is **reverse-engineered**, so it's fragile by
> nature — if State Farm changes the WAF or the Okta flow, the API path breaks.
> So there's an **automatic fallback**: if any API step fails, login transparently
> drops back to the original full Playwright browser flow. Slower, but it still
> works. Fast path by default, proven path as a safety net.

---

## 1:25 — Lemonade: fully browser-free (~35s)

**[DO]** Switch to **Lemonade**. Note the password field disappears (email + OTP only).

**[SAY]**
> Lemonade I took all the way — **no browser at all**. I reverse-engineered the
> whole login as HTTP: scrape the CSRF token, trigger the email OTP, sign in,
> follow the OAuth redirect chain, hit the policies API, and pull the PDF — all
> `curl_cffi` impersonating Chrome, which clears their Cloudflare check at the
> network layer.

**[DO]** Enter email → **Log in** → OTP → document. Point at latency (~3s end-to-end).

**[SAY]**
> No Chromium launched at all — login to document in about three seconds, and far
> lighter on the host. Same browser fallback here too, for robustness.

---

## 2:00 — Goodcover: browser by choice (~25s)

**[DO]** Switch to **Goodcover**. Log in (no MFA) → document.

**[SAY]**
> Goodcover is the interesting counter-example. It's the *simplest* carrier —
> email, password, no MFA — but I deliberately **kept it on the browser**. Its
> login is protobuf-encoded, persisted-GraphQL behind Cloudflare: brittle to
> hand-roll and tightly coupled to their frontend build. It's already fast with
> no MFA, so the right engineering call was *not* to reimplement it. The
> optimization here was the judgment call.

---

## 2:25 — Close (~20s)

**[SAY]**
> So the guiding principle across all three: **use a real browser only where the
> carrier's bot defenses force it, run everything else as plain HTTP, and pre-warm
> the browser-dependent work off the critical path.** All three land well under
> the eight-second budget, it's deployed and hosted, and credentials are never
> persisted. Thanks for watching.

---

### Cheat sheet (numbers to point at)
| Carrier | Login → MFA | MFA → document | Approach |
|---|---|---|---|
| State Farm | ~1.8s | ~3.0s | Hybrid: browser mints WAF token → HTTP Okta + direct docs |
| Lemonade | — (email+OTP) | ~3s total | Pure-API (`curl_cffi`) |
| Goodcover | — (no MFA) | fast | Browser (deliberate) |

**If something flakes mid-record:** State Farm may fall back to the browser
(slower, ~10s) — that's the fallback *working*; either call it out as a feature
or just re-record that carrier.
