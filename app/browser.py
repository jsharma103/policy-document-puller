"""Launch a browser + context per carrier LaunchSpec.

One Playwright instance per engine (started lazily, reused across sessions).
Each session gets its own browser + a fresh ephemeral context (nothing
persisted). Mode / channel / viewport / egress all come from the carrier's
LaunchSpec; proxy secrets are resolved from the environment here so adapters
never touch them.
"""
from dataclasses import dataclass
from typing import TYPE_CHECKING

from playwright.async_api import Browser as PWBrowser
from playwright.async_api import BrowserContext, Page

from . import config
from .carriers.base import Egress, LaunchSpec

if TYPE_CHECKING:
    from .carriers._api import ApiSession

_WEBDRIVER_MASK = "Object.defineProperty(navigator,'webdriver',{get:()=>undefined});"
_pw = None  # patchright Playwright, started once and reused across sessions


async def _patchright():
    global _pw
    if _pw is None:
        from patchright.async_api import async_playwright
        _pw = await async_playwright().start()
    return _pw


@dataclass
class Browser:
    # For the "api" transport there's no Playwright browser: pw_browser is None and
    # context/page both hold the ApiSession (curl_cffi). Closing it closes the HTTP
    # session; the rest of the app treats it exactly like a browser-backed session.
    pw_browser: "PWBrowser | None"
    context: "BrowserContext | ApiSession"
    page: "Page | ApiSession"

    async def aclose(self) -> None:
        try:
            await self.context.close()
        finally:
            if self.pw_browser is not None:
                await self.pw_browser.close()


async def launch(spec: LaunchSpec) -> Browser:
    if spec.transport == "api":
        # API-transport carrier: a curl_cffi session stands in for the page/context
        # everywhere downstream — no Chromium for the request flow. Routed through
        # the mobile proxy when the carrier needs it (State Farm), direct otherwise
        # (Lemonade). State Farm still mints one WAF token via a brief browser
        # inside its adapter; that's separate from this transport.
        from .carriers._api import ApiSession
        proxy = config.soax_proxy() if spec.egress == Egress.MOBILE_PROXY else None
        api = ApiSession(proxy=proxy)
        return Browser(pw_browser=None, context=api, page=api)

    pw = await _patchright()
    headless = spec.headless
    if config.HEADLESS_OVERRIDE in ("0", "1"):
        headless = config.HEADLESS_OVERRIDE == "1"

    launch_kwargs: dict = {
        "headless": headless,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if spec.egress == Egress.MOBILE_PROXY:
        proxy = config.soax_proxy()
        if proxy is not None:
            launch_kwargs["proxy"] = proxy
        else:
            # No proxy configured → go direct. Fine on a residential IP (local
            # dev); from a datacenter IP State Farm fake-rejects as bad creds.
            print("[browser] no SOAX proxy set — running DIRECT egress "
                  "(ok locally on a residential IP, not from the droplet)")

    browser = await pw.chromium.launch(**launch_kwargs)
    context = await browser.new_context(accept_downloads=True)
    await context.add_init_script(_WEBDRIVER_MASK)
    page = await context.new_page()
    return Browser(pw_browser=browser, context=context, page=page)


async def shutdown() -> None:
    global _pw
    if _pw is not None:
        await _pw.stop()
        _pw = None
