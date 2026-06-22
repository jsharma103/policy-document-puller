"""Lemonade adapter — the easy carrier, with two transports behind one adapter.

  * API (default): Lemonade's login is a clean JSON flow — GET /login (scrape
    CSRF) -> /login/presign (sends a one-time code) -> /login/signin -> follow
    the OAuth redirect chain -> the dashboard API returns each policy's
    `form_url` (the PDF). Run browser-free over curl_cffi (Chrome-impersonated,
    so Cloudflare's risk check passes from a datacenter IP).
  * Browser (fallback): the original Playwright flow, used automatically if the
    API path fails (see main.py). Headless on a direct datacenter IP; no proxy.

Each lifecycle method dispatches on the transport it's handed (ApiSession vs a
Playwright page/context). Document ids/links are always discovered at runtime.
"""
import re

from playwright.async_api import BrowserContext, Page

from ._api import ApiSession
from ._util import click_first_visible, pdf_via_request
from .base import Credentials, DocMeta, Egress, LaunchSpec, MfaPrompt

LOGIN_URL = "https://www.lemonade.com/login"
PRESIGN_URL = "https://www.lemonade.com/login/presign"
SIGNIN_URL = "https://www.lemonade.com/login/signin"
POLICIES_API = "https://my.lemonade.com/api/v1/web_dashboard/accounts/home/policies"

_CSRF_RE = re.compile(r"""csrf[-_]?token["':=\s]+([A-Za-z0-9_\-]{24,40})""", re.I)
_CSRF_RE_JSON = re.compile(r'"csrf"\s*:\s*"([A-Za-z0-9_\-]{24,40})"')

_EMAIL_SELS = ("input[type='email']", "input[name*='email' i]",
               "input[autocomplete='email']", "input[placeholder*='email' i]")
_OTP_SELS = ("input[autocomplete='one-time-code']", "input[inputmode='numeric']",
             "input[type='tel']", "input[name*='code' i]")
_SUBMIT_SELS = ("button[type=submit]", "button:has-text('Log in')",
                "button:has-text('Continue')", "button:has-text('Submit')")


class LemonadeAdapter:
    name = "lemonade"
    # API-default: Lemonade's login is a clean JSON API, so we run it browser-free
    # (curl_cffi). The Playwright path stays as a fallback (main.py retries on it).
    launch = LaunchSpec(headless=True, egress=Egress.DIRECT, transport="api")

    async def prewarm(self, page) -> None:
        if isinstance(page, ApiSession):
            try:
                await self._api_prepare(page)
            except Exception:
                pass
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

    # ------------------------- API transport (default) ------------------------ #
    async def _api_prepare(self, api: ApiSession) -> None:
        """GET the login page once for the CSRF token + cookies. Idempotent
        (safe to call from both prewarm and start_login)."""
        if api.data.get("csrf"):
            return
        r = await api.http.get(LOGIN_URL, timeout=30)
        m = _CSRF_RE.search(r.text) or _CSRF_RE_JSON.search(r.text)
        if not m:
            raise RuntimeError("could not scrape Lemonade CSRF token")
        api.data["csrf"] = m.group(1)

    def _api_headers(self, api: ApiSession) -> dict:
        return {"x-csrf-token": api.data.get("csrf", ""),
                "content-type": "application/json",
                "origin": "https://www.lemonade.com",
                "referer": LOGIN_URL, "accept": "application/json"}

    async def _api_start_login(self, api: ApiSession, creds: Credentials) -> MfaPrompt:
        await self._api_prepare(api)
        r = await api.http.post(
            PRESIGN_URL, json={"email": creds.username, "preferred_channel": "email"},
            headers=self._api_headers(api), timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"presign -> {r.status_code}")
        return MfaPrompt(required=True,
                         message="Enter the 6-digit code Lemonade sent you.")

    async def _api_submit_mfa(self, api: ApiSession, code: str) -> None:
        r = await api.http.post(
            SIGNIN_URL, json={"code": code, "consentReceived": False},
            headers=self._api_headers(api), timeout=30)
        if r.status_code not in (200, 201):
            raise RuntimeError(f"signin -> {r.status_code}")
        try:
            redirect = (r.json() or {}).get("redirectTo")
        except Exception:
            redirect = None
        if redirect:        # the OAuth redirect chain sets the customer_session cookies
            await api.http.get(redirect, timeout=30, allow_redirects=True)

    async def _api_list_documents(self, api: ApiSession) -> list[DocMeta]:
        r = await api.http.get(
            POLICIES_API, headers={"x-csrf-token": api.data.get("csrf", "")}, timeout=30)
        if r.status_code != 200:
            raise RuntimeError(f"policies API -> {r.status_code}")
        docs = _extract_docs(r.json())
        if not docs:
            raise RuntimeError("no policy documents found in /policies response")
        return docs

    async def _api_fetch_pdf(self, api: ApiSession, doc: DocMeta) -> bytes:
        r = await api.http.get(doc.extra["form_url"], timeout=30)
        body = r.content
        if body[:5] != b"%PDF-":
            raise RuntimeError("form_url did not return a PDF")
        return body

    # ------------------------ Browser transport (fallback) -------------------- #
    async def _browser_prewarm(self, page: Page) -> None:
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        try:
            await page.locator(", ".join(_EMAIL_SELS)).first.wait_for(
                state="visible", timeout=20000)
        except Exception:
            pass

    async def _browser_start_login(self, page: Page, creds: Credentials) -> MfaPrompt:
        if "lemonade.com/login" not in page.url:
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        email = page.locator(", ".join(_EMAIL_SELS)).first
        await email.wait_for(state="visible", timeout=20000)
        await email.click()
        await email.fill(creds.username)
        await click_first_visible(page, _SUBMIT_SELS)
        try:
            await page.locator(", ".join(_OTP_SELS)).first.wait_for(
                state="visible", timeout=20000)
        except Exception as e:
            raise RuntimeError(f"did not reach the code screen: {e}")
        return MfaPrompt(required=True,
                         message="Enter the 6-digit code Lemonade emailed you.")

    async def _browser_submit_mfa(self, page: Page, code: str) -> None:
        box = page.locator(", ".join(_OTP_SELS)).first
        await box.wait_for(state="visible", timeout=10000)
        await box.click()
        await page.keyboard.type(code, delay=120)   # real keystrokes across 6 boxes
        await click_first_visible(page, _SUBMIT_SELS)
        try:
            await page.locator(_OTP_SELS[0]).first.wait_for(state="detached", timeout=10000)
        except Exception:
            await page.wait_for_timeout(2000)

    async def _browser_list_documents(self, context: BrowserContext, page: Page) -> list[DocMeta]:
        r = await context.request.get(POLICIES_API)
        if not r.ok:
            raise RuntimeError(f"policies API returned {r.status}")
        docs = _extract_docs(await r.json())
        if not docs:
            raise RuntimeError("no policy documents found in /policies response")
        return docs

    async def _browser_fetch_pdf(self, context: BrowserContext, page: Page, doc: DocMeta) -> bytes:
        return await pdf_via_request(context, doc.extra["form_url"], "form_url")


def _extract_docs(data) -> list[DocMeta]:
    """Walk the policies JSON for objects carrying a `form_url` (the PDF link)."""
    docs: list[DocMeta] = []
    seen: set[str] = set()

    def walk(o):
        if isinstance(o, dict):
            fu = o.get("form_url")
            if isinstance(fu, str) and fu.startswith("http") and fu not in seen:
                seen.add(fu)
                title = str(o.get("policy_type") or o.get("type") or o.get("name")
                            or o.get("policy_number") or "Policy")
                did = str(o.get("id") or o.get("public_id")
                          or o.get("policy_number") or len(docs))
                docs.append(DocMeta(doc_id=did, title=title,
                                    category=str(o.get("category", "")),
                                    extra={"form_url": fu}))
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(data)
    return docs
