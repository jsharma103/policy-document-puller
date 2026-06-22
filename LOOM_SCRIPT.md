# Loom script — Policy Document Puller (~2.5 min)

URL: http://167.172.137.214:8000 · Order: **State Farm → Lemonade → Goodcover**

**[DO]** = what to click, **[SAY]** = narration. Talk over the live demo.

---

## 0:00 — Intro + the approach (~25s)

**[SAY]**
> This is a hosted app that logs into three insurance carriers — handling
> password and MFA — and pulls the policy document back, with a hard budget of
> **8 seconds from MFA submit to the PDF**. Credentials are never stored — typed
> in live, used in-memory, nothing persisted.

> The approach: instead of automating a browser clicking through their UI, I
> went **straight at each carrier's backend APIs** — for both login and pulling
> documents. The work was reverse-engineering their auth to do that. I'll start
> with State Farm, the hardest one.

---

## 0:25 — State Farm (~45s)

**[DO]** Select **State Farm**. Type username, tab out, type password.

**[SAY]**
> State Farm has the most involved login, and real bot protection. I
> reverse-engineered its auth and document APIs so I can hit them **directly over
> HTTP** — no browser driving the UI. The one exception: their bot defense needs
> a token only a real browser can produce, so a brief browser mints **just that
> token** in the background, and everything else is direct API calls.

> That background step runs the moment I pick the carrier and leave the username
> field — so the **Log in** click only pays for the password and the OTP.

**[DO]** Click **Log in** → MFA prompt (~1.8s). Point at latency.

**[SAY]**
> Login to prompt — under two seconds.

**[DO]** Enter OTP → **Submit code** → PDF renders. Point at latency.

**[SAY]**
> MFA submit to document — about three seconds, well under budget, straight from
> their API. And because this is reverse-engineered, there's an **automatic
> fallback** to a full browser flow if anything breaks.

---

## 1:10 — Lemonade (~25s)

**[DO]** Switch to **Lemonade** — password field disappears (email + OTP).

**[SAY]**
> Lemonade I took all the way — **no browser at all**. Same approach: I
> reverse-engineered the login and document APIs, so the whole thing is direct
> HTTP, email plus a one-time code.

**[DO]** Enter email → **Log in** → OTP → document. Point at latency (~3s).

**[SAY]**
> Login to document in about three seconds, nothing launched. Same browser
> fallback if the API path ever fails.

---

## 1:35 — Goodcover (~20s)

**[DO]** Switch to **Goodcover**. Log in (no MFA) → document.

**[SAY]**
> Goodcover is the counter-example. Simplest carrier — no MFA — but I deliberately
> **kept it on the browser**: its API is too tangled to reverse cleanly, and it's
> already fast. The right call was not to over-engineer it. Knowing when *not* to
> was part of the work.

---

## 1:55 — Close (~15s)

**[SAY]**
> So the approach across all three: **go straight at the APIs where it's worth
> it, fall back to a browser where it isn't or where bot defenses force it.** All
> three are well under the eight-second budget, deployed and hosted, with no
> credentials stored. Thanks for watching.

---

### Cheat sheet (numbers to point at)
| Carrier | Login → MFA | MFA → document | Approach |
|---|---|---|---|
| State Farm | ~1.8s | ~3.0s | Direct API; browser mints only the bot-defense token |
| Lemonade | — (email+OTP) | ~3s total | Fully direct API, no browser |
| Goodcover | — (no MFA) | fast | Browser (deliberate) |

**If State Farm flakes:** it may fall back to the browser (slower) — that's the
fallback *working*; call it out or re-record that carrier.
