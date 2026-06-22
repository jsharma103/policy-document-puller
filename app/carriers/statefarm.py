"""State Farm adapter — the hard carrier, now hybrid.

State Farm's login is an Okta IDX JSON API gated by AWS WAF Bot Control on the
credential step. Two transports behind one adapter:

  * API (default, fast): a brief stealth browser mints the one `aws-waf-token`
    the WAF demands (~2-3s), then the whole Okta IDX flow + document APIs run over
    curl_cffi (see _sf_idx). MFA→document lands ~1-2s. Mobile/4G egress because
    State Farm fake-rejects datacenter IPs.
  * Browser (fallback): the original full Playwright login (headful under Xvfb +
    mobile proxy), used automatically if the API path fails (main.py retries it).

After auth, documents are pulled straight from the Document Center proxy/blob
APIs with the captured OAuth Bearer — no SPA. Doc ids/links discovered at runtime.

Each lifecycle method dispatches on the transport it's handed (ApiSession vs a
Playwright page/context).
"""
import re

from playwright.async_api import BrowserContext, Page

from .. import config
from . import _sf_idx
from ._api import ApiSession
from ._util import click_first_visible
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
# Bearer token for the doc APIs, keyed by the live session object (Playwright
# context for the browser path, ApiSession for the API path).
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
SUBMIT_SELS = ("input[type=submit]", "button[type=submit]",
               "button:has-text('Continue')", "button:has-text('Verify')",
               "button:has-text('Log in')", "button:has-text('Submit')")


class StateFarmAdapter:
    name = "statefarm"
    # API-default hybrid; headful + mobile proxy kept on the spec so the browser
    # FALLBACK (main.py relaunches with transport="browser") runs the proven flow.
    launch = LaunchSpec(headless=False, egress=Egress.MOBILE_PROXY, transport="api")

    # ---- transport dispatch ---- #
    async def prewarm(self, page) -> None:
        if isinstance(page, ApiSession):
            try:
                await self._ensure_waf_token(page)            # mint the WAF token
                await _sf_idx.preauth(page.http, page.data)   # + interact/introspect, overlapping typing
            except Exception:
                pass                                          # best-effort; start_login redoes it
            return
        await self._browser_prewarm(page)

    async def start_login(self, page, creds: Credentials) -> MfaPrompt:
        if isinstance(page, ApiSession):
            return await self._api_start_login(page, creds)
        return await self._browser_start_login(page, creds)

    async def submit_mfa(self, page, code: str) -> None:
        if isinstance(page, ApiSession):
            return await self._api_submit_mfa(page, code)
        return await self._browser_submit_mfa(page, code)

    async def list_documents(self, context, page) -> list[DocMeta]:
        if isinstance(context, ApiSession):
            return await self._api_list_documents(context)
        return await self._browser_list_documents(context, page)

    async def fetch_pdf(self, context, page, doc: DocMeta) -> bytes:
        if isinstance(context, ApiSession):
            return await self._api_fetch_pdf(context, doc)
        return await self._browser_fetch_pdf(context, page, doc)

    # ---- API transport (default): browser-minted WAF token + curl_cffi IDX ---- #
    async def _ensure_waf_token(self, api: ApiSession) -> None:
        """Mint the AWS WAF token via a brief stealth browser and set it on the
        session. Idempotent: prewarm calls it at carrier-select so the ~2-3s mint
        overlaps credential typing, and start_login then finds it already done."""
        if api.data.get("waf_minted"):
            return
        token = await _sf_idx.mint_waf_token(config.soax_proxy())   # None locally -> direct
        if not token:
            raise RuntimeError("could not mint State Farm WAF token")   # -> browser fallback
        api.http.cookies.set("aws-waf-token", token, domain=".statefarm.com")
        api.data["aws_waf_token"] = token        # reused by the direct doc-fetch session
        api.data["waf_minted"] = True

    async def _api_start_login(self, api: ApiSession, creds: Credentials) -> MfaPrompt:
        await self._ensure_waf_token(api)            # no-op if prewarm already minted it
        pw = creds.password or ""
        try:
            if not api.data.get("preauth_done"):     # prewarm usually did interact/introspect
                await _sf_idx.preauth(api.http, api.data)
            await _sf_idx.resume(api.http, api.data, creds.username, pw)   # identify → password → OTP
        except _sf_idx.StaleStateError:              # prewarmed interaction expired — redo fresh, retry once
            await _sf_idx.preauth(api.http, api.data)
            await _sf_idx.resume(api.http, api.data, creds.username, pw)
        print("  [sf] API login: password accepted, email OTP requested", flush=True)
        return MfaPrompt(required=True, message="Enter the code State Farm emailed you.")

    async def _api_submit_mfa(self, api: ApiSession, code: str) -> None:
        tok = await _sf_idx.finish(api.http, api.data, code)
        _ctx_auth[id(api)] = f"Bearer {tok}"
        print("  [sf] API login complete (OAuth token captured)", flush=True)

    def _doc_session(self, api: ApiSession):
        """Non-proxied curl_cffi session for the document APIs. They're Bearer-
        authed and accept the VM's datacenter IP directly (verified), so skipping
        the proxy here cuts MFA->document from ~6s to ~1s on the server. Reused
        across list + fetch; closed by ApiSession.close()."""
        d = api.data.get("doc_http")
        if d is None:
            from curl_cffi.requests import AsyncSession
            d = AsyncSession(impersonate="chrome")
            tok = api.data.get("aws_waf_token")
            if tok:
                d.cookies.set("aws-waf-token", tok, domain=".statefarm.com")
            api.data["doc_http"] = d
        return d

    async def _api_list_documents(self, api: ApiSession) -> list[DocMeta]:
        auth = _ctx_auth.get(id(api))
        if not auth:
            raise RuntimeError("no OAuth token for this session")
        http = self._doc_session(api)
        r = await http.get(f"{_DC_PROXY}/customerMetadata?year=0",
                           headers={"authorization": auth}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"customerMetadata -> {r.status_code}")
        docs = _extract_docs(r.json())
        if not docs:
            raise RuntimeError("no documents found in customerMetadata")
        print("  [sf] list_documents via direct API (no proxy, no browser)", flush=True)
        return docs

    async def _api_fetch_pdf(self, api: ApiSession, doc: DocMeta) -> bytes:
        auth = _ctx_auth.get(id(api))
        headers = {"authorization": auth}
        http = self._doc_session(api)
        r = await http.get(
            f"{_DC_PROXY}/document?documentId={doc.extra['documentId']}&commId={doc.extra['commId']}",
            headers=headers, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"proxy /document -> {r.status_code}")
        meta = r.json()
        cust = _deep_find(meta, ("custindexid",))
        token = _deep_find(meta, ("authtoken",))
        if not (cust and token):
            raise RuntimeError("no custIndexId/authToken in proxy JSON")
        blob_url = (f"{_DOC_INFO_SVC}/blob/{cust}?filter[authToken]={token}"
                    f"&filter[consumerIdentification]=DocumentCenterUI"
                    f"&filter[cachePrefix]=docproxy:doc:")
        rb = await http.get(blob_url, timeout=30)        # one-time authToken in query
        body = rb.content
        if body[:5] != b"%PDF-":
            rb = await http.get(blob_url, headers=headers, timeout=30)
            body = rb.content
        if body[:5] != b"%PDF-":
            raise RuntimeError(f"blob -> {rb.status_code}, not a PDF")
        return body

    # ---- Browser transport (fallback): the original full Playwright login ---- #
    async def _browser_prewarm(self, page: Page) -> None:
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        try:
            await page.locator(
                "#input, input[placeholder='User ID' i], input[type='password']"
            ).first.wait_for(state="visible", timeout=15000)
        except Exception:
            pass

    async def _browser_start_login(self, page: Page, creds: Credentials) -> MfaPrompt:
        ok_user = ok_pass = False
        nav_err: Exception | None = None
        for attempt in range(3):
            try:
                if attempt > 0 or "login-ui/login" not in page.url:
                    await page.goto(LOGIN_URL, wait_until="domcontentloaded")
            except Exception as e:
                nav_err = e
                await page.wait_for_timeout(2000)
                continue
            try:
                await page.locator(
                    "input[placeholder='User ID' i], input[type='password']"
                ).first.wait_for(state="visible", timeout=25000)
            except Exception:
                pass
            ok_user = await _fill(page, USER_SELS, creds.username, "User ID")
            if not await page.locator("input[type='password']").count():
                await click_first_visible(page, SUBMIT_SELS)
                await page.wait_for_timeout(1500)
            ok_pass = await _fill(page, PASS_SELS, creds.password or "", "password")
            if ok_user and ok_pass:
                break
        if not (ok_user and ok_pass):
            if nav_err and "login-ui/login" not in page.url:
                raise RuntimeError("could not reach State Farm through the mobile "
                                   f"proxy (transient exit-node drop): {nav_err}")
            try:
                await page.screenshot(path="/tmp/sf_login_fail.png", full_page=True)
            except Exception:
                pass
            raise RuntimeError("login form did not populate (User ID / Password) "
                               "— see /tmp/sf_login_fail.png")

        await click_first_visible(page, SUBMIT_SELS)
        picked = False
        for _ in range(40):
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
                picked = True
                await _pick_code_method(page)
        try:
            await page.screenshot(path="/tmp/sf_stepup_fail.png", full_page=True)
        except Exception:
            pass
        raise RuntimeError("did not reach the SMS prompt "
                           "(bot reject disguised as bad creds, or wrong creds) "
                           "— see /tmp/sf_stepup_fail.png")

    async def _browser_submit_mfa(self, page: Page, code: str) -> None:
        await _enter_otp(page, code)
        try:
            async with page.expect_response(
                    lambda r: "/v1/token" in r.url.lower() and r.request.method == "POST",
                    timeout=15000) as info:
                await click_first_visible(page, SUBMIT_SELS)
            data = await (await info.value).json()
            tok = data.get("access_token")
            if tok:
                _ctx_auth[id(page.context)] = f"Bearer {tok}"
                return
        except Exception:
            pass
        await self._complete_sso(page)

    async def _complete_sso(self, page: Page) -> None:
        if "my.statefarm.com" in page.url:
            return
        for _ in range(90):
            await page.wait_for_timeout(200)
            await _dismiss_passkey(page)
            if "my.statefarm.com" in page.url:
                return
        raise RuntimeError("did not reach my.statefarm.com after MFA (SSO stalled)")

    async def _browser_list_documents(self, context: BrowserContext, page: Page) -> list[DocMeta]:
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

    async def _browser_fetch_pdf(self, context: BrowserContext, page: Page, doc: DocMeta) -> bytes:
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
        rb = await context.request.get(blob_url)
        body = await rb.body()
        if body[:5] != b"%PDF-":
            rb = await context.request.get(blob_url, headers=headers)
            body = await rb.body()
        if body[:5] != b"%PDF-":
            raise RuntimeError(f"blob -> {rb.status}, not a PDF")
        return body

    async def _fetch_pdf_via_viewer(self, context: BrowserContext, page: Page, doc: DocMeta) -> bytes:
        await self._complete_sso(page)
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
        _pat = re.compile(r"documentinformationservice")
        cid = id(page.context)

        def _grab_auth(req):
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
                        await page.wait_for_timeout(3000)
                        continue
                    break
            raise RuntimeError(f"could not load the Document Center: {last_err}")
        finally:
            page.remove_listener("request", _grab_auth)
            await page.unroute(_pat, _block)


# --------------------------------------------------------------------------- #
async def _fill(page: Page, selectors, value: str, label: str) -> bool:
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


async def _pick_code_method(page: Page) -> bool:
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
        if clicked:
            break
    await page.wait_for_timeout(400)
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
    try:
        return await page.evaluate("""() => {
            const els=[...document.querySelectorAll('button,input[type=submit],a,[role=button]')];
            const el=els.find(e=>/send\\s*code/i.test(e.innerText||e.value||''));
            if(el){el.click();return true} return false; }""")
    except Exception:
        return False


async def _enter_otp(page: Page, code: str) -> None:
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
    await page.keyboard.type(code, delay=120)


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
    policy = [d for d in docs if _doc_rank(d) < 3]
    return policy or docs


def _doc_rank(d: DocMeta) -> int:
    s = f"{d.title} {d.category}".lower()
    if "binder" in s:
        return 0
    if any(k in s for k in ("declaration", "dec page", "policy", "coverage")):
        return 1
    if any(k in s for k in ("payment", "receipt", "billing", "invoice", "bill")):
        return 3
    return 2
