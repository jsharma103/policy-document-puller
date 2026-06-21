"""Lemonade adapter — the easy carrier.

Passwordless: the user enters their email, Lemonade emails a 6-digit code
(6 OTP boxes — REAL keystrokes, not .fill()). The dashboard's own API returns
each policy's `form_url`, which IS the PDF (regenerated per session, so we
discover it rather than hardcode). Runs headless on a direct datacenter IP; no
proxy needed.
"""
from playwright.async_api import BrowserContext, Page

from .base import Credentials, DocMeta, Egress, LaunchSpec, MfaPrompt

LOGIN_URL = "https://www.lemonade.com/login"
POLICIES_API = "https://my.lemonade.com/api/v1/web_dashboard/accounts/home/policies"

_EMAIL_SELS = ("input[type='email']", "input[name*='email' i]",
               "input[autocomplete='email']", "input[placeholder*='email' i]")
_OTP_SELS = ("input[autocomplete='one-time-code']", "input[inputmode='numeric']",
             "input[type='tel']", "input[name*='code' i]")
_SUBMIT_SELS = ("button[type=submit]", "button:has-text('Log in')",
                "button:has-text('Continue')", "button:has-text('Submit')")


class LemonadeAdapter:
    name = "lemonade"
    launch = LaunchSpec(engine="patchright", headless=True, egress=Egress.DIRECT)

    async def prewarm(self, page: Page) -> None:
        """Optional speedup: load the login page ahead of time so start_login can
        fill the email immediately. Best-effort (Lemonade is already quick, so
        this gain is small — mostly here so pre-warm is a generic capability)."""
        await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        try:
            await page.locator(", ".join(_EMAIL_SELS)).first.wait_for(
                state="visible", timeout=20000)
        except Exception:
            pass

    async def start_login(self, page: Page, creds: Credentials) -> MfaPrompt:
        if "lemonade.com/login" not in page.url:      # skip if prewarm already navigated
            await page.goto(LOGIN_URL, wait_until="domcontentloaded")
        email = page.locator(", ".join(_EMAIL_SELS)).first
        await email.wait_for(state="visible", timeout=20000)
        await email.click()
        await email.fill(creds.username)
        await _submit(page)
        try:
            await page.locator(", ".join(_OTP_SELS)).first.wait_for(
                state="visible", timeout=20000)
        except Exception as e:
            raise RuntimeError(f"did not reach the code screen: {e}")
        return MfaPrompt(required=True, kind="email_code",
                         message="Enter the 6-digit code Lemonade emailed you.")

    async def submit_mfa(self, page: Page, code: str) -> None:
        box = page.locator(", ".join(_OTP_SELS)).first
        await box.wait_for(state="visible", timeout=10000)
        await box.click()
        await page.keyboard.type(code, delay=120)   # real keystrokes across 6 boxes
        await _submit(page)
        # The code field detaches once Lemonade accepts the code.
        try:
            await page.locator(_OTP_SELS[0]).first.wait_for(state="detached", timeout=10000)
        except Exception:
            await page.wait_for_timeout(2000)

    async def list_documents(self, context: BrowserContext, page: Page) -> list[DocMeta]:
        r = await context.request.get(POLICIES_API)
        if not r.ok:
            raise RuntimeError(f"policies API returned {r.status}")
        docs = _extract_docs(await r.json())
        if not docs:
            raise RuntimeError("no policy documents found in /policies response")
        return docs

    async def fetch_pdf(self, context: BrowserContext, page: Page, doc: DocMeta) -> bytes:
        r = await context.request.get(doc.extra["form_url"])
        if not r.ok:
            raise RuntimeError(f"form_url returned {r.status}")
        body = await r.body()
        if body[:5] != b"%PDF-":
            raise RuntimeError("form_url did not return a PDF")
        return body


async def _submit(page: Page) -> None:
    for sel in _SUBMIT_SELS:
        loc = page.locator(sel).first
        try:
            if await loc.is_visible():
                await loc.click(timeout=2000)
                return
        except Exception:
            continue
    await page.keyboard.press("Enter")


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
