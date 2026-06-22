# Loom script — Policy Document Puller (~3 min)

URL: http://167.172.137.214:8000 · Order: **State Farm → Lemonade → Goodcover**

**[DO]** = what to click, **[SAY]** = narration. Keep the demo running while you talk.

---

## 0:00 — Intro + the core idea (~25s)

**[SAY]**
> This is a hosted app that logs into three insurance carriers — handling
> password and MFA — and pulls the policy document back, with a hard budget of
> **8 seconds from MFA submit to the PDF on screen**. Credentials are never
> stored — typed in live, used in-memory, nothing persisted or logged.

> The core design decision: instead of driving a browser and clicking through
> their UI, I wanted to **talk to each carrier's backend APIs directly** — both
> for login and for fetching the documents. That's the fast, clean path. The
> work was **reverse-engineering how their authentication and document APIs
> actually work** — and that's most of what I want to walk through.

---

## 0:25 — State Farm: the hard one (~75s)

**[DO]** Select **State Farm**. Type the username, tab out, type the password.

**[SAY] — what I reverse-engineered**
> State Farm was the deepest. Login isn't a simple form post — it's a multi-step
> **Okta Identity Engine** flow across a couple of domains. I traced the whole
> thing and rebuilt it as **direct HTTP calls** using `curl_cffi`, which
> impersonates Chrome's TLS fingerprint: bootstrap the flow, submit the password,
> request the email OTP, exchange the code for an access token. Then the
> **Document Center APIs** — I reverse-engineered those too: they're Bearer-
> authed, so once I have the token I pull the policy binder straight from the API.

**[SAY] — the one place a browser is needed**
> There's exactly one wrinkle. State Farm puts **AWS WAF Bot Control** on the
> login step — it needs a token that's computed by JavaScript in a real browser.
> So a **brief headless browser mints just that one token**, hands it to the HTTP
> client, and everything else — the entire Okta flow and all the document calls —
> is pure HTTP. Browser only where the bot defense literally forces it.

**[SAY] — prewarm (why the click is instant)**
> And that token mint plus the credential-free setup steps run the moment I pick
> the carrier and leave the username field — **before I ever click Log in**. So
> the actual login click only pays for password plus the OTP request.

**[DO]** Click **Log in** → MFA prompt (~1.8s). Point at the latency line.

**[SAY]**
> Login to MFA prompt — under two seconds.

**[DO]** Enter the OTP, **Submit code** → PDF renders. Point at latency.

**[SAY]**
> MFA submit to document — about three seconds, well under the eight-second budget,
> pulled straight from the document API.

**[SAY] — fallback**
> Since this is reverse-engineered, it's inherently fragile — if they change the
> auth flow it breaks. So there's an **automatic fallback**: if any API step
> fails, login transparently drops to a full browser flow that drives the real
> UI. Direct-API fast path by default, browser as the safety net.

---

## 1:40 — Lemonade: no browser at all (~30s)

**[DO]** Switch to **Lemonade** — password field disappears (email + OTP only).

**[SAY]**
> Lemonade I took all the way to **zero browser**. I reverse-engineered the full
> login as HTTP — grab the CSRF token, trigger the email OTP, sign in, follow the
> OAuth redirect chain, hit the policies API, and pull the PDF — all `curl_cffi`
> impersonating Chrome, which clears their bot check at the network layer.

**[DO]** Enter email → **Log in** → OTP → document. Point at latency (~3s).

**[SAY]**
> No browser launched at all — login to document in about three seconds. Same
> browser fallback here if the API path ever fails.

---

## 2:10 — Goodcover: browser by choice (~25s)

**[DO]** Switch to **Goodcover**. Log in (no MFA) → document.

**[SAY]**
> Goodcover is the counter-example. It's the simplest carrier — email, password,
> no MFA — but I deliberately **kept it on the browser**. Its login is
> protobuf-encoded, persisted-GraphQL behind Cloudflare: brittle to hand-roll and
> tightly coupled to their frontend build. It's already fast with no MFA, so the
> right call was *not* to reverse-engineer it. Knowing when not to was part of
> the work.

---

## 2:35 — Close (~15s)

**[SAY]**
> So across all three: **hit the backend APIs directly wherever I could
> reverse-engineer them, and only fall back to a browser where bot defenses force
> it.** All three land well under the eight-second budget, it's deployed and
> hosted, and credentials are never persisted. Thanks for watching.

---

### Cheat sheet (numbers to point at)
| Carrier | Login → MFA | MFA → document | Approach |
|---|---|---|---|
| State Farm | ~1.8s | ~3.0s | Direct-API auth + docs; browser mints only the WAF token |
| Lemonade | — (email+OTP) | ~3s total | Fully direct-API (`curl_cffi`), no browser |
| Goodcover | — (no MFA) | fast | Browser (deliberate — too brittle to reverse) |

**If State Farm flakes mid-record:** it may fall back to the browser (slower) —
that's the fallback *working*; call it out as a feature or re-record that carrier.
