"""State Farm hybrid login over Okta Identity Engine (IDX).

State Farm's login is an Okta IDX JSON API. Every step is plain HTTP *except* the
password submit, which AWS WAF Bot Control gates behind a JS-computed token. So:
a brief stealth browser mints that `aws-waf-token` (~2-3s — it does only that, not
the slow full login SPA), then the whole IDX flow runs over curl_cffi:

    interact → introspect → identify → challenge(password) → answer(password)
    → select email authenticator → (OTP emailed) → answer(otp) → /v1/token

Far faster than driving the login SPA (MFA→document ends up ~1-2s). The browser
adapter remains the fallback if any of this changes.
"""
import base64
import hashlib
import json
import secrets

BASE = "https://auth.proofing.statefarm.com"
AUTHSRV = "aus2clrnwwWY9kBbc4h7"
CLIENT = "0oa4pcvub7hZEnzGr4h7"
SCOPE = ("openid offline_access user:profile.read user:profile.write "
         "okta.myAccount.authenticators.manage")
REDIRECT = BASE + "/login-ui/callback"
LOGIN_URL = BASE + "/login-ui/login?goto=https%3A%2F%2Fmy.statefarm.com%2F"
_PW_AUTH_FALLBACK = "autsnlmehkesloK2V4h6"   # org password authenticator (parsed live, this is a backstop)

_H = {"x-okta-user-agent-extended": "okta-auth-js/7.14.1 @okta/okta-vue/5.9.0",
      "accept": "application/json", "origin": BASE, "referer": BASE + "/login-ui/login"}


def _b64u(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


async def mint_waf_token(proxy: dict | None) -> str | None:
    """Launch a brief stealth (headful — awswaf detects headless) browser, let the
    AWS WAF JS challenge run, and return the `aws-waf-token` cookie value."""
    from patchright.async_api import async_playwright
    pw = await async_playwright().start()
    kw: dict = {"headless": False, "args": ["--disable-blink-features=AutomationControlled"]}
    if proxy:
        kw["proxy"] = proxy
    browser = await pw.chromium.launch(**kw)
    ctx = await browser.new_context()
    page = await ctx.new_page()
    token = None
    try:
        await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=60000)
        for _ in range(40):                       # patient — the awswaf JS is slow via proxy
            await page.wait_for_timeout(1000)
            hit = [c for c in await ctx.cookies() if c["name"] == "aws-waf-token"]
            if hit:
                token = hit[0]["value"]
                break
    finally:
        try:
            await browser.close()
        finally:
            await pw.stop()
    return token


async def _post(http, path, payload, form=False, ion=False):
    ct = ("application/x-www-form-urlencoded" if form else
          "application/ion+json; okta-version=1.0.0" if ion else "application/json")
    headers = dict(_H)
    headers["content-type"] = ct
    body = payload if form else json.dumps(payload)
    return await http.post(BASE + path, data=body, headers=headers, timeout=30)


def _find_password_id(j) -> str | None:
    out = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("type") == "password" and str(o.get("id", "")).startswith("aut"):
                out.append(o["id"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(j)
    return out[0] if out else None


def _find_email_authenticator(j) -> dict | None:
    for rem in j.get("remediation", {}).get("value", []):
        if rem.get("name") == "select-authenticator-authenticate":
            for field in rem.get("value", []):
                if field.get("name") == "authenticator":
                    for opt in field.get("options", []):
                        if opt.get("label") == "Email":
                            d = {}
                            for ff in opt.get("value", {}).get("form", {}).get("value", []):
                                if "value" in ff:
                                    d[ff["name"]] = ff["value"]
                            return d
    return None


def _find_interaction_code(j) -> str | None:
    out = []

    def walk(o):
        if isinstance(o, dict):
            if o.get("name") == "interaction_code" and isinstance(o.get("value"), str):
                out.append(o["value"])
            for v in o.values():
                walk(v)
        elif isinstance(o, list):
            for v in o:
                walk(v)

    walk(j)
    return out[0] if out else None


def _messages(j) -> list:
    return [m.get("message") for m in (j.get("messages") or {}).get("value", [])]


async def start(http, data: dict, username: str, password: str) -> None:
    """interact → introspect → identify → password → request the email OTP.
    Stashes `code_verifier` + `stateHandle` in `data`. Raises on reject/WAF block."""
    verifier = _b64u(secrets.token_bytes(32))
    challenge = _b64u(hashlib.sha256(verifier.encode()).digest())
    data["code_verifier"] = verifier
    ih = (await _post(http, f"/api/oauth2/{AUTHSRV}/v1/interact",
                      {"client_id": CLIENT, "scope": SCOPE, "redirect_uri": REDIRECT,
                       "code_challenge": challenge, "code_challenge_method": "S256",
                       "state": secrets.token_hex(16), "nonce": secrets.token_hex(16)},
                      form=True)).json()["interaction_handle"]
    sh = (await _post(http, "/api/idp/idx/introspect",
                      {"interactionHandle": ih}, ion=True)).json()["stateHandle"]
    j = (await _post(http, "/api/idp/idx/identify",
                     {"identifier": username, "stateHandle": sh})).json()
    sh = j["stateHandle"]
    pw_id = _find_password_id(j) or _PW_AUTH_FALLBACK
    sh = (await _post(http, "/api/idp/idx/challenge",
                      {"authenticator": {"id": pw_id}, "stateHandle": sh})).json()["stateHandle"]
    r = await _post(http, "/api/idp/idx/challenge/answer",
                    {"credentials": {"passcode": password}, "stateHandle": sh, "uid": username})
    if "json" not in r.headers.get("content-type", ""):
        raise RuntimeError("password POST was intercepted (WAF token invalid/expired)")
    j = r.json()
    sh = j.get("stateHandle")
    msgs = _messages(j)
    if msgs:
        raise RuntimeError(f"State Farm rejected the credentials: {msgs}")
    if not sh:
        raise RuntimeError("password step returned no stateHandle")
    email = _find_email_authenticator(j)
    if not email:
        raise RuntimeError("no email MFA option offered after password")
    auth = {"id": email["id"]}
    if email.get("methodType"):
        auth["methodType"] = email["methodType"]
    if email.get("enrollmentId"):
        auth["enrollmentId"] = email["enrollmentId"]
    sh = (await _post(http, "/api/idp/idx/challenge",
                      {"authenticator": auth, "stateHandle": sh})).json()["stateHandle"]
    data["stateHandle"] = sh


async def finish(http, data: dict, code: str) -> str:
    """answer(otp) → interaction_code → /v1/token → access_token."""
    j = (await _post(http, "/api/idp/idx/challenge/answer",
                     {"credentials": {"passcode": code}, "stateHandle": data["stateHandle"]})).json()
    ic = _find_interaction_code(j)
    if not ic:
        raise RuntimeError(f"OTP not accepted: {_messages(j) or 'no interaction_code'}")
    r = await _post(http, f"/api/oauth2/{AUTHSRV}/v1/token",
                    {"client_id": CLIENT, "redirect_uri": REDIRECT,
                     "grant_type": "interaction_code", "code_verifier": data["code_verifier"],
                     "interaction_code": ic}, form=True)
    tok = r.json().get("access_token")
    if not tok:
        raise RuntimeError(f"token exchange failed: {r.status_code}")
    return tok
