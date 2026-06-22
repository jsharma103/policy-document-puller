"""Browser-free HTTP transport for carriers whose login is (mostly) a clean API.

A `curl_cffi` AsyncSession impersonating Chrome — enough to satisfy carriers'
network-layer bot checks without launching Chromium. Lemonade runs entirely on
it; State Farm runs on it too, after a brief browser mints the one token its WAF
demands (see statefarm._sf_idx). Optionally routes through the mobile proxy.

`ApiSession` mirrors the Playwright `Browser`'s role: the live per-user session
held across login → MFA → document fetch. Adapters stash scratch in `.data` and
make requests through `.http`. It's passed everywhere a page/context would be,
so the session store, endpoints, and teardown are unchanged.
"""
from curl_cffi.requests import AsyncSession


class ApiSession:
    def __init__(self, proxy: dict | None = None) -> None:
        kwargs: dict = {"impersonate": "chrome"}
        if proxy:                                  # playwright-style {server, username, password}
            host = proxy["server"].split("://", 1)[-1]
            url = f"http://{proxy['username']}:{proxy['password']}@{host}"
            kwargs["proxies"] = {"http": url, "https": url}
        self.http = AsyncSession(**kwargs)
        self.data: dict = {}     # carrier scratch: CSRF / stateHandle / code_verifier

    async def close(self) -> None:
        try:
            await self.http.close()
        except Exception:
            pass
