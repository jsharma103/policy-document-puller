"""Browser-free HTTP transport for carriers whose login is a clean HTTP API.

Some carriers (Lemonade) don't need a real browser at all — their login is a
handful of JSON calls. A `curl_cffi` AsyncSession impersonating Chrome is enough
to satisfy the carrier's Cloudflare risk check from a datacenter IP (verified),
without the cost of launching Chromium.

`ApiSession` deliberately mirrors the Playwright `Browser`'s role: it's the live,
per-user session object held across login → MFA → document fetch. The adapter
stashes carrier scratch (CSRF token, etc.) in `.data` and makes requests through
`.http`. It's passed everywhere the app would pass a page/context, so the session
store, endpoints, and teardown are unchanged.
"""
from curl_cffi.requests import AsyncSession


class ApiSession:
    def __init__(self) -> None:
        self.http = AsyncSession(impersonate="chrome")
        self.data: dict = {}     # carrier scratch: CSRF token, etc.

    async def close(self) -> None:
        try:
            await self.http.close()
        except Exception:
            pass
