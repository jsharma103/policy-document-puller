"""Launch a browser + context per carrier LaunchSpec.

One Playwright instance per engine (started lazily, reused across sessions).
Each session gets its own browser + a fresh ephemeral context (nothing
persisted). Mode / channel / viewport / egress all come from the carrier's
LaunchSpec; proxy secrets are resolved from the environment here so adapters
never touch them.
"""
from dataclasses import dataclass

from playwright.async_api import Browser as PWBrowser
from playwright.async_api import BrowserContext, Page

from . import config
from .carriers.base import Egress, LaunchSpec

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
    pw_browser: PWBrowser | None     # None for a persistent context (closing it stops the browser)
    context: BrowserContext
    page: Page

    async def aclose(self) -> None:
        try:
            await self.context.close()
        finally:
            if self.pw_browser is not None:
                await self.pw_browser.close()


async def launch(spec: LaunchSpec, storage_state: str | None = None,
                 user_data_dir: str | None = None) -> Browser:
    """Launch a browser + context for a carrier. `user_data_dir` uses a PERSISTENT
    profile — cookies + localStorage + IndexedDB + fingerprint all survive across
    runs, like a real browser — so Okta device trust holds for opt-in remember-
    this-device (plain `storage_state` cookies alone aren't enough for Okta)."""
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

    if user_data_dir:
        # Persistent profile: the whole browser state persists to user_data_dir,
        # so a trusted device stays trusted across logins (Okta device cookie +
        # whatever else it binds to). First login writes it; later ones reuse it.
        context = await pw.chromium.launch_persistent_context(
            user_data_dir, accept_downloads=True, **launch_kwargs)
        await context.add_init_script(_WEBDRIVER_MASK)
        page = context.pages[0] if context.pages else await context.new_page()
        return Browser(pw_browser=None, context=context, page=page)

    browser = await pw.chromium.launch(**launch_kwargs)
    ctx_kwargs: dict = {"accept_downloads": True}
    if storage_state:                     # restore a saved authed session (remember-this-device)
        ctx_kwargs["storage_state"] = storage_state
    context = await browser.new_context(**ctx_kwargs)
    await context.add_init_script(_WEBDRIVER_MASK)
    page = await context.new_page()
    return Browser(pw_browser=browser, context=context, page=page)


async def shutdown() -> None:
    global _pw
    if _pw is not None:
        await _pw.stop()
        _pw = None
