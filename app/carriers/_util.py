"""Small async helpers shared across carrier adapters.

Deliberately carrier-agnostic — each adapter passes in its own selector list or
URL, so all carrier-specific knowledge stays inside that adapter.
"""
from playwright.async_api import BrowserContext, Page


async def click_first_visible(page: Page, selectors, timeout: int = 2000) -> None:
    """Click the first visible element among `selectors` (tried in order); fall
    back to pressing Enter if none is visible. Carriers' continue/submit controls
    differ, so each passes its own selector list (and timeout where it matters)."""
    for sel in selectors:
        loc = page.locator(sel).first
        try:
            if await loc.is_visible():
                await loc.click(timeout=timeout)
                return
        except Exception:
            continue
    await page.keyboard.press("Enter")


async def pdf_via_request(context: BrowserContext, url: str, label: str) -> bytes:
    """GET `url` through the authed browser context and return the PDF bytes,
    validating the response really is a PDF. `label` names the source so a
    failure points at the right carrier link."""
    r = await context.request.get(url)
    if not r.ok:
        raise RuntimeError(f"{label} returned {r.status}")
    body = await r.body()
    if body[:5] != b"%PDF-":
        raise RuntimeError(f"{label} did not return a PDF")
    return body
