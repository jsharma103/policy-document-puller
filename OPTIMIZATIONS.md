# Latency optimizations

Guiding principle: **use a real browser only where the carrier's bot defenses
force it, and run everything else as plain HTTP — pre-warming every
browser-dependent step off the critical path.** All numbers below are measured on
the hosted VM (DigitalOcean droplet, mobile-proxy egress where required).

The two metrics:
- **Login → MFA prompt** — clicking Log in until the code prompt appears.
- **MFA submit → document** — submitting the code until the PDF renders (the 8s budget).

---

## State Farm — the hard carrier (11.5s → ~4.8s combined)

Real bot detection + **AWS WAF Bot Control** on the credential step + a
multi-domain **Okta Identity Engine (IDX)** flow. It began as a full headful
browser login (~17s just to the MFA prompt). It was rebuilt into a hybrid —
a brief stealth browser mints *only* the one token the WAF requires, and the
entire Okta IDX flow + document fetch then run as `curl_cffi` HTTP — and four
optimizations were stacked on top:

| # | Optimization | What changed | Impact |
|---|---|---|---|
| 1 | **Hybrid: browser-minted `aws-waf-token` → `curl_cffi` Okta IDX** | Reverse-engineered the IDX API. The WAF gates only the password POST behind a JS-computed token, so a brief browser mints just that token; the rest of login is fast HTTP, not the login SPA. | Replaced the ~17s SPA login; integrated baseline **11.5s** combined |
| 2 | **Direct (no-proxy) document fetch** | The Document Center APIs are Bearer-authed and IP-agnostic, so they don't need the mobile proxy — fetched straight from the VM. | MFA→doc **6.22s → 3.43s** |
| 3 | **Prewarm `interact` / `introspect`** | These IDX bootstrap calls need no username, so they run at carrier-select (overlapping typing) instead of at login. | Login→MFA **4.90s → 2.91s** |
| 4 | **2-stage prewarm: `identify` on username-blur** | `identify` + select-password need only the username, so they fire when the username field loses focus (overlapping password typing). Login then performs only password + OTP request. | Login→MFA **2.91s → 1.89s** |

Supporting changes:
- **WAF token minted during prewarm**, in an off-screen window locally / under
  Xvfb on the server — so it's windowless and entirely off the measured login path.
- **`customerMetadata?year=<current>`** instead of `year=0` — surfaces the policy
  **binder** (the `year=0` "Currently Active" view returns only billing docs).
- **Automatic browser fallback** — if any API step fails (WAF change, expired
  interaction), login transparently drops to the original full Playwright flow.

**Result:** Login→MFA **~1.8s** + MFA→doc **~3.0s** = **~4.8s combined**, windowless,
pure-HTTP after the hidden mint.

---

## Lemonade — fully browser-free (~3s login → document)

The easy carrier; previously a headless browser flow. One optimization:

- **Pure-API (no browser at all).** Reverse-engineered the login:
  `GET /login` (scrape CSRF) → `presign` (sends the OTP) → `signin` → follow the
  OAuth redirect chain → dashboard `policies` API → fetch the policy `form_url`
  PDF — all over `curl_cffi` impersonating Chrome (which clears Lemonade's
  Cloudflare check at the network layer). **No Chromium launched.**
  - **Impact:** removes the headless-browser launch + SPA render entirely →
    login → document in **~3s**, and far lighter on a 2-vCPU host.
- **Playwright fallback** (robustness, not speed): if the API path fails, it
  drops to the proven browser flow.

---

## Goodcover — kept the browser (a deliberate call)

The simplest carrier (email/password, no MFA). Pure-API was evaluated and
rejected: its login is **protobuf-encoded, persisted-GraphQL behind Cloudflare** —
brittle to hand-roll and tightly coupled to their frontend build.

- **Decision: stay on the browser.** It's already fast (no MFA; near-instant
  login → dashboard), and the PDF is the "Download Policy" link's `href`, fetched
  directly through the authenticated context.
- The optimization here was the **judgment call** — confirming a browser is the
  right tool rather than maintaining a fragile reimplementation for no real gain.

---

## Summary

| Carrier | Approach | Login → MFA | MFA → document |
|---|---|---|---|
| State Farm | Hybrid (browser mints WAF token → HTTP IDX + direct docs) | ~1.8s | ~3.0s |
| Lemonade | Pure-API (`curl_cffi`), browser fallback | — (email + OTP) | ~3s end-to-end |
| Goodcover | Browser (no MFA) | — | fast (no MFA) |

The same prewarm pattern underlies all of them: the moment a carrier is selected
(and, for State Farm, the moment the username is entered), the slow,
browser-dependent work runs in the background so the user-facing clicks pay only
for fast HTTP.
