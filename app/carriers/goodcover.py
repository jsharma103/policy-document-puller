"""Goodcover adapter — the fast, simple carrier.

Two-step email/password login (email → Next → password → Log in), NO MFA,
behind Cloudflare bot management that doesn't challenge patchright. Runs
headless on a direct datacenter IP — no proxy, no Xvfb, so it hosts as fast as
it runs locally. The policy PDF is the "Download Policy" link's href (a
per-policy URL), discovered at runtime and fetched through the authed context
(same pattern as Lemonade's form_url).
"""
import re

from playwright.async_api import BrowserContext, Page

from .base import Credentials, DocMeta, Egress, LaunchSpec, MfaPrompt

LOGIN_URL = "https://app.goodcover.com/login?msg=auth&return_url=%2Fdashboard"

_EMAIL_SELS = ("input[type='email']", "input[autocomplete='username']")
_PASS_SELS = ("input[type='password']", "input[placeholder='Your password' i]")
_NEXT_SELS = ("button:has-text('Next')", "button[type=submit]")
_LOGIN_SELS = ("button:has-text('Log in')", "button:has-text('Login')", "button[type=submit]")


class GoodcoverAdapter:
    name = "goodcover"
    launch = LaunchSpec(engine="patchright", headless=True, egress=Egress.DIRECT)

    async def prewarm(self, page: Page) -> None:
        """Load the login page ahead of time so start_login fills immediately."""
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        try:
            await page.locator(", ".join(_EMAIL_SELS)).first.wait_for(
                state="visible", timeout=20000)
        except Exception:
            pass

    async def start_login(self, page: Page, creds: Credentials) -> MfaPrompt:
        if "goodcover.com/login" not in page.url:      # skip if prewarm navigated
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        # Step 1: email → Next
        email = page.locator(", ".join(_EMAIL_SELS)).first
        await email.wait_for(state="visible", timeout=20000)
        await email.click()
        await email.fill(creds.username)
        await _click(page, _NEXT_SELS)
        # Step 2: password → Log in
        pw = page.locator(", ".join(_PASS_SELS)).first
        await pw.wait_for(state="visible", timeout=15000)
        await pw.click()
        await pw.fill(creds.password or "")
        await _click(page, _LOGIN_SELS)
        # Logged in once we leave /login (return_url sends us to /dashboard).
        for _ in range(50):                            # up to ~20s
            if "/login" not in page.url:
                return MfaPrompt(required=False)       # Goodcover has no MFA
            await page.wait_for_timeout(400)
        raise RuntimeError("did not reach the dashboard "
                           "(bad creds, or a bot/IP block)")

    async def submit_mfa(self, page: Page, code: str) -> None:
        # No MFA — start_login returns required=False, so the app never calls
        # this. Defined for interface completeness.
        return

    async def list_documents(self, context: BrowserContext, page: Page) -> list[DocMeta]:
        # The dashboard may still be rendering right after login (slower on the
        # 2-vCPU VM than locally). Wait for the policy link, open it, then read
        # the Download-Policy anchor's href (the PDF URL).
        view = page.locator("a:has-text('View Policy'), a:has-text('View policy')").first
        try:
            await view.wait_for(state="visible", timeout=25000)
            await view.click(timeout=8000)
        except Exception:
            pass   # some accounts land straight on the policy page
        dl = page.locator("a:has-text('Download Policy')").first
        try:
            await dl.wait_for(state="visible", timeout=20000)
            href = await dl.evaluate("e => e.href")
        except Exception as e:
            raise RuntimeError(f"Download Policy link not found ({e})")
        if not href or ".pdf" not in href.lower():
            raise RuntimeError(f"Download Policy link is not a PDF: {href}")
        m = re.search(r"/policy-pdf/([^/]+)/", href) or re.search(r"Policy_([^/.]+)", href)
        pid = m.group(1) if m else "policy"
        return [DocMeta(doc_id=pid, title="Policy", category="Policy",
                        extra={"href": href})]

    async def fetch_pdf(self, context: BrowserContext, page: Page, doc: DocMeta) -> bytes:
        r = await context.request.get(doc.extra["href"])
        if not r.ok:
            raise RuntimeError(f"policy PDF link returned {r.status}")
        body = await r.body()
        if body[:5] != b"%PDF-":
            raise RuntimeError("Download Policy href did not return a PDF")
        return body


async def _click(page: Page, selectors) -> None:
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.is_visible():
                await loc.click(timeout=3000)
                return
        except Exception:
            continue
    await page.keyboard.press("Enter")
