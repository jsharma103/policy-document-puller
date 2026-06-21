"""State Farm adapter — the hard carrier.

Real bot detection → patchright + real Chrome (channel="chrome", no_viewport),
headful (run under Xvfb on the server). Mobile/4G egress (SOAX) because State
Farm fake-rejects datacenter IPs as "incorrect user ID or password". After the
SMS code there's a multi-domain OAuth/SSO chain before documents are reachable,
and the Document Center is a separate SPA with its own handshake.

Doc fetch: the Document Center's `customerMetadata` API lists docs (we pick the
policy BINDER, not the legacy notification). Deep-link the viewer once the SPA
is warm; the page lands on the `documentinformationservice` URL, which we fetch
through the live authed context (the bare blob endpoint 401s).

See the carrier-flow findings for the full reasoning.
"""
import re

from playwright.async_api import BrowserContext, Page

from .base import Credentials, DocMeta, Egress, LaunchSpec, MfaPrompt

LOGIN_URL = ("https://auth.proofing.statefarm.com/login-ui/login"
             "?goto=https%3A%2F%2Fmy.statefarm.com%2F")

# Document Center SPA (internal k8s host under statefarm.com): load the root —
# waiting for its customerMetadata call is the SPA-warm signal — then deep-link
# the viewer with the binder's documentId + commId.
_DC = ("https://documentcenterui-prod-custdocmgmtweb.apps.gdrosa.redk8s."
       "statefarm.com/DocumentCenterUI")
DOC_CENTER_URL = _DC + "/"
VIEWER_URL = _DC + "/document?documentId={documentId}&commId={commId}"

# Direct PDF path (skips both viewer SPAs): the proxy endpoint trades a
# documentId+commId for the doc's custIndexId + a one-time authToken; the blob
# endpoint then serves the PDF given those.
_DC_PROXY = ("https://documentcenterproxyv1-prod-custdocmgmtweb.apps.gdrosa."
             "redk8s.statefarm.com/DocumentCenterProxyV1")
_DOC_INFO_SVC = ("https://documentinformationservice-prod-custdocmgmtweb.apps."
                 "gdrosa.redk8s.statefarm.com/DocumentInformationService")
# Bearer token the SPA sends to the doc APIs, captured per browser context from
# its own customerMetadata request and reused for the direct PDF fetch.
_ctx_auth: dict[int, str] = {}

USER_SELS = ("#input", "input[autocomplete~='username']",
             "input[autocomplete*='username' i]",
             "input[placeholder='User ID' i]", "input[placeholder*='user id' i]",
             "input[aria-label*='user id' i]", "input[name*='user' i]",
             "input#username")
PASS_SELS = ("input[placeholder='Password' i]", "input[type='password']",
             "input[aria-label*='password' i]", "input[name*='pass' i]")
OTP_SELS = ("input[autocomplete='one-time-code']", "input[name*='code' i]",
            "input[id*='code' i]", "input[aria-label*='code' i]",
            "input[inputmode='numeric']", "input[type='tel']",
            "input[maxlength='6']", "input[maxlength]")
# State Farm's Verify control is an input[type=submit], NOT a button — match
# both so we don't fall back to the flaky Enter key.
SUBMIT_SELS = ("input[type=submit]", "button[type=submit]",
               "button:has-text('Continue')", "button:has-text('Verify')",
               "button:has-text('Log in')", "button:has-text('Submit')")


class StateFarmAdapter:
    name = "statefarm"
    # Match the proven prototype exactly: patchright's bundled Chromium (NO
    # channel="chrome") at the default 1280x720 viewport (NO no_viewport). The
    # earlier channel/no_viewport overrides changed SF's responsive layout and
    # broke the step-up clicks.
    launch = LaunchSpec(headless=False, egress=Egress.MOBILE_PROXY)

    async def prewarm(self, page: Page) -> None:
        """Optional speedup: navigate to the login page (the slow part — loading
        State Farm's login + stealth settle through the mobile proxy) ahead of
        time, while the user is still typing credentials. start_login then skips
        the nav and fills creds immediately. Best-effort; failure just means
        login navigates fresh."""
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        try:
            await page.locator(
                "#input, input[placeholder='User ID' i], input[type='password']"
            ).first.wait_for(state="visible", timeout=15000)
        except Exception:
            pass

    async def start_login(self, page: Page, creds: Credentials) -> MfaPrompt:
        # SF's first load on a fresh mobile IP is slow/flaky, so try the
        # navigate-and-fill up to twice (reload on the retry).
        ok_user = ok_pass = False
        for attempt in range(2):
            if attempt > 0 or "login-ui/login" not in page.url:   # prewarm may have navigated
                await page.goto(LOGIN_URL, wait_until="domcontentloaded")
            try:
                await page.locator(
                    "input[placeholder='User ID' i], input[type='password']"
                ).first.wait_for(state="visible", timeout=25000)
            except Exception:
                pass
            ok_user = await _fill(page, USER_SELS, creds.username, "User ID")
            # Two-step form: User ID + Continue, then the password screen renders.
            if not await page.locator("input[type='password']").count():
                await _submit(page)
                await page.wait_for_timeout(1500)
            ok_pass = await _fill(page, PASS_SELS, creds.password or "", "password")
            if ok_user and ok_pass:
                break
        if not (ok_user and ok_pass):
            try:                                       # capture what SF actually served
                await page.screenshot(path="/tmp/sf_login_fail.png", full_page=True)
            except Exception:
                pass
            raise RuntimeError("login form did not populate (User ID / Password) "
                               "— see /tmp/sf_login_fail.png")

        await _submit(page)
        # Prototype pattern: detect the choose-method screen by its body text and
        # fire the pick EXACTLY ONCE (set the flag before calling, so a re-click
        # never toggles the radio back off). OTP screen reached = otp box present
        # or we're on /login-ui/vc.
        picked = False
        for _ in range(40):                       # ~20s through step-up -> OTP
            await page.wait_for_timeout(500)
            await _dismiss_passkey(page)
            if await _otp_present(page) or "login-ui/vc" in page.url.lower():
                return MfaPrompt(required=True,
                                 message="Enter the code State Farm texted you.")
            try:
                body = (await page.inner_text("body")).lower()
            except Exception:
                body = ""
            if not picked and any(k in body for k in (
                    "how do you want", "where should we", "send a code",
                    "send you a code", "receive a code", "get a code",
                    "text message", "verification method", "choose how")):
                picked = True                     # attempt exactly once
                await _pick_code_method(page)
        try:
            await page.screenshot(path="/tmp/sf_stepup_fail.png", full_page=True)
        except Exception:
            pass
        raise RuntimeError("did not reach the SMS prompt "
                           "(bot reject disguised as bad creds, or wrong creds) "
                           "— see /tmp/sf_stepup_fail.png")

    async def submit_mfa(self, page: Page, code: str) -> None:
        await _enter_otp(page, code)                # prototype's enter_otp logic
        # Capture the OAuth access token from the post-MFA token exchange so the
        # doc APIs can be called directly (skip the Document Center SPA).
        try:
            async with page.expect_response(
                    lambda r: "/v1/token" in r.url.lower() and r.request.method == "POST",
                    timeout=15000) as info:
                await _submit(page)
            data = await (await info.value).json()
            tok = data.get("access_token")
            if tok:
                _ctx_auth[id(page.context)] = f"Bearer {tok}"
                return   # token in hand — skip the ~4s passkey/callback/landing
                         # chain. list_documents fires the pure-API doc flow
                         # immediately and completes the SSO only if that 401s.
        except Exception:
            pass   # not captured -> drive the SSO so the SPA path works

        await self._complete_sso(page)

    async def _complete_sso(self, page: Page) -> None:
        """Dismiss the passkey-enroll screen and wait to land on my.statefarm.com
        (the OAuth callback that sets the session cookies). Needed only when the
        pure-API doc flow can't run on the captured token alone."""
        if "my.statefarm.com" in page.url:
            return
        for _ in range(90):                         # up to ~18s through redirects
            await page.wait_for_timeout(200)
            await _dismiss_passkey(page)
            if "my.statefarm.com" in page.url:
                return
        raise RuntimeError("did not reach my.statefarm.com after MFA (SSO stalled)")

    async def list_documents(self, context: BrowserContext, page: Page) -> list[DocMeta]:
        # Fast path: call customerMetadata directly with the OAuth token captured
        # at MFA — skips both the ~4s SSO tail (submit_mfa returned early) AND the
        # Document Center SPA's ~12 requests. If the token alone isn't enough (the
        # proxy needs the landing's session cookies), complete the SSO then load
        # the SPA (which also re-captures the token for fetch_pdf).
        try:
            data = await self._customer_metadata_direct(context)
            docs = _extract_docs(data)
            if docs:
                print("  [sf] list_documents via direct API (no SPA, no SSO wait)", flush=True)
                return docs
        except Exception as e:
            print(f"  [sf] direct customerMetadata failed ({e}); completing SSO + SPA", flush=True)
        await self._complete_sso(page)
        data = await self._load_doc_center(page)
        docs = _extract_docs(data)
        if not docs:
            raise RuntimeError("no documents found in customerMetadata")
        return docs

    async def _customer_metadata_direct(self, context: BrowserContext):
        auth = _ctx_auth.get(id(context))
        if not auth:
            raise RuntimeError("no OAuth token captured")
        r = await context.request.get(f"{_DC_PROXY}/customerMetadata?year=0",
                                      headers={"authorization": auth})
        if not r.ok:
            raise RuntimeError(f"customerMetadata -> {r.status}")
        return await r.json()

    async def fetch_pdf(self, context: BrowserContext, page: Page, doc: DocMeta) -> bytes:
        # Fast path: two API calls, no SPA. Falls back to driving the viewer if
        # the direct path ever fails (e.g. SF changes the proxy contract).
        try:
            return await self._fetch_pdf_direct(context, doc)
        except Exception as e:
            print(f"  [sf] direct PDF fetch failed ({e}); falling back to viewer", flush=True)
            return await self._fetch_pdf_via_viewer(context, page, doc)

    async def _fetch_pdf_direct(self, context: BrowserContext, doc: DocMeta) -> bytes:
        auth = _ctx_auth.get(id(context))
        if not auth:
            raise RuntimeError("no captured Bearer token for this session")
        headers = {"authorization": auth}
        proxy_url = (f"{_DC_PROXY}/document?documentId={doc.extra['documentId']}"
                     f"&commId={doc.extra['commId']}")
        r = await context.request.get(proxy_url, headers=headers)
        if not r.ok:
            raise RuntimeError(f"proxy /document -> {r.status}")
        meta = await r.json()
        cust = _deep_find(meta, ("custindexid",))
        token = _deep_find(meta, ("authtoken",))
        if not (cust and token):
            raise RuntimeError("no custIndexId/authToken in proxy JSON")
        blob_url = (f"{_DOC_INFO_SVC}/blob/{cust}"
                    f"?filter[authToken]={token}"
                    f"&filter[consumerIdentification]=DocumentCenterUI"
                    f"&filter[cachePrefix]=docproxy:doc:")
        # The blob is on a different domain; its auth is the one-time authToken in
        # the query, NOT the proxy's Bearer. Try query-only first, then Bearer.
        rb = await context.request.get(blob_url)
        body = await rb.body()
        if body[:5] != b"%PDF-":
            rb = await context.request.get(blob_url, headers=headers)
            body = await rb.body()
        if body[:5] != b"%PDF-":
            raise RuntimeError(f"blob -> {rb.status}, not a PDF")
        return body

    async def _fetch_pdf_via_viewer(self, context: BrowserContext, page: Page, doc: DocMeta) -> bytes:
        await self._complete_sso(page)   # the viewer needs the landed session
        url = VIEWER_URL.format(documentId=doc.extra["documentId"],
                                commId=doc.extra["commId"])
        await page.goto(url, wait_until="commit")
        for _ in range(75):
            if "documentinformationservice" in page.url.lower():
                break
            await page.wait_for_timeout(300)
        r = await context.request.get(page.url)
        body = await r.body()
        if body[:5] != b"%PDF-":
            raise RuntimeError(f"expected PDF, got {r.status} at {page.url[:90]}")
        return body

    async def _load_doc_center(self, page: Page):
        """Load the Document Center and return its customerMetadata JSON (the
        SPA-warm signal). Retry once if State Farm bounces us back to login
        before the cross-domain session is fully established."""
        # Block the per-document preview fetches the Document Center SPA fires on
        # load — we only need its customerMetadata JSON. Previewing every doc is
        # slow and pulls all the PDFs; fetch_pdf gets the one we want afterward.
        # (customerMetadata + the SSO handshake are different endpoints, unaffected.)
        _pat = re.compile(r"documentinformationservice")
        cid = id(page.context)

        def _grab_auth(req):
            # Capture the Bearer token the SPA puts on its doc-API calls so
            # fetch_pdf can hit the proxy/blob endpoints directly.
            if "documentcenterproxyv1" in req.url or "documentinformationservice" in req.url:
                a = (req.headers or {}).get("authorization")
                if a:
                    _ctx_auth[cid] = a

        async def _block(route):
            try:
                await route.abort()
            except Exception:
                pass

        page.on("request", _grab_auth)
        await page.route(_pat, _block)
        try:
            last_err: Exception | None = None
            for attempt in range(2):
                try:
                    async with page.expect_response(
                        lambda r: "customermetadata" in r.url.lower(), timeout=20000
                    ) as info:
                        await page.goto(DOC_CENTER_URL, wait_until="commit")
                    resp = await info.value
                    return await resp.json()
                except Exception as e:
                    last_err = e
                    if attempt == 0 and "login-ui/login" in page.url:
                        await page.goto("https://my.statefarm.com/", wait_until="domcontentloaded")
                        await page.wait_for_timeout(3000)   # re-warm the session
                        continue
                    break
            raise RuntimeError(f"could not load the Document Center: {last_err}")
        finally:
            page.remove_listener("request", _grab_auth)
            await page.unroute(_pat, _block)


# --------------------------------------------------------------------------- #
async def _fill(page: Page, selectors, value: str, label: str) -> bool:
    """Fill + VERIFY (React inputs drop programmatic values); retype as real
    keystrokes if needed."""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            await loc.wait_for(state="visible", timeout=4000)
        except Exception:
            continue
        try:
            await loc.click(timeout=2000)
            await loc.fill("")
            await loc.fill(value)
            if await loc.input_value() != value:
                await loc.click()
                await loc.press_sequentially(value, delay=60)
            return await loc.input_value() == value
        except Exception:
            continue
    return False


async def _submit(page: Page) -> None:
    for sel in SUBMIT_SELS:
        loc = page.locator(sel).first
        try:
            if await loc.is_visible():
                await loc.click(timeout=2000)
                return
        except Exception:
            continue
    await page.keyboard.press("Enter")


async def _pick_code_method(page: Page) -> bool:
    """SF step-up 'select a verification method': pick the phone option, then
    click Send Code (a <button> OR an <input type=submit>) — with force + JS
    fallbacks for overlay/actionability quirks."""
    clicked = False
    for t in ("Mobile Phone", "Phone", "Text"):
        for sel in (f"label:has-text('{t}')", f"text={t}"):
            loc = page.locator(sel).first
            try:
                if await loc.count() and await loc.is_visible():
                    await loc.click(timeout=2000)
                    clicked = True
                    break
            except Exception:
                continue
        if clicked:                               # pick ONE method, not all three
            break
    await page.wait_for_timeout(400)
    # accessible-name role match first (catches button OR input[type=submit]), then
    # CSS, then force, then a JS click.
    candidates = [page.get_by_role("button", name=re.compile("send", re.I)).first]
    for sel in ("input[type=submit][value*='Send' i]",
                "button:has-text('Send Code')", "button:has-text('Send')",
                "[role=button]:has-text('Send')", "a:has-text('Send Code')"):
        candidates.append(page.locator(sel).first)
    for loc in candidates:
        try:
            if await loc.count():
                await loc.scroll_into_view_if_needed(timeout=1500)
                await loc.click(timeout=3000)
                return True
        except Exception:
            try:
                await loc.click(timeout=1500, force=True)
                return True
            except Exception:
                continue
    try:  # last resort: JS-click anything that says "send code"
        return await page.evaluate("""() => {
            const els=[...document.querySelectorAll('button,input[type=submit],a,[role=button]')];
            const el=els.find(e=>/send\\s*code/i.test(e.innerText||e.value||''));
            if(el){el.click();return true} return false; }""")
    except Exception:
        return False


async def _enter_otp(page: Page, code: str) -> None:
    """Port of the prototype's enter_otp: try the OTP selectors in order, type
    real keystrokes into the first VISIBLE one; fall back to the first visible
    text-like input, then to the focused element."""
    for sel in OTP_SELS:
        loc = page.locator(sel).first
        try:
            if await loc.count() and await loc.is_visible():
                await loc.click(timeout=2000)
                await page.keyboard.type(code, delay=120)
                return
        except Exception:
            continue
    inputs = page.locator("input:visible")
    try:
        for i in range(await inputs.count()):
            el = inputs.nth(i)
            t = (await el.get_attribute("type") or "text").lower()
            if t in ("text", "tel", "number", ""):
                await el.click(timeout=1500)
                await page.keyboard.type(code, delay=120)
                return
    except Exception:
        pass
    await page.keyboard.type(code, delay=120)       # last resort: focused element


async def _otp_present(page: Page) -> bool:
    for sel in OTP_SELS:
        try:
            if await page.locator(sel).count():
                return True
        except Exception:
            pass
    return False


async def _dismiss_passkey(page: Page) -> None:
    if "enroll-passkey" not in page.url:
        return
    for sel in ("button:has-text('Not now')", "a:has-text('Not now')", "text=Not now"):
        loc = page.locator(sel).first
        try:
            if await loc.is_visible():
                await loc.click(timeout=2000)
                return
        except Exception:
            continue


def _deep_find(obj, keys: tuple) -> str | None:
    """First scalar value whose (lowercased) key is in `keys`, searched
    recursively — robust to where SF nests custIndexId / authToken in the JSON."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in keys and isinstance(v, (str, int)):
                return str(v)
        for v in obj.values():
            r = _deep_find(v, keys)
            if r:
                return r
    elif isinstance(obj, list):
        for v in obj:
            r = _deep_find(v, keys)
            if r:
                return r
    return None


def _extract_docs(data) -> list[DocMeta]:
    """Pull doc entries (documentId + communicationId) from customerMetadata and
    sort the policy BINDER first (the real document)."""
    found: list[dict] = []

    def walk(o):
        if isinstance(o, dict):
            if "documentId" in o and ("communicationId" in o or "commId" in o):
                found.append(o)
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)

    docs: list[DocMeta] = []
    for o in found:
        did = str(o.get("documentId"))
        commid = str(o.get("communicationId") or o.get("commId"))
        title = str(o.get("type") or o.get("category") or "Document")
        docs.append(DocMeta(
            doc_id=did, title=title, category=str(o.get("category", "")),
            extra={"documentId": did, "commId": commid}))
    docs.sort(key=_doc_rank)
    print("  [sf] discovered docs (sorted): " +
          " | ".join(f"{d.title}/{d.category}" for d in docs), flush=True)
    # Show only policy documents — drop billing/payment receipts (rank 3), which
    # aren't policy docs. Fall back to all if filtering would empty the list.
    policy = [d for d in docs if _doc_rank(d) < 3]
    return policy or docs


def _doc_rank(d: DocMeta) -> int:
    """Policy binder first; billing/payment docs last (so the UI auto-renders the
    actual policy, not a payment receipt)."""
    s = f"{d.title} {d.category}".lower()
    if "binder" in s:
        return 0
    if any(k in s for k in ("declaration", "dec page", "policy", "coverage")):
        return 1
    if any(k in s for k in ("payment", "receipt", "billing", "invoice", "bill")):
        return 3
    return 2
