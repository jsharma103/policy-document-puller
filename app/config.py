"""App settings. All secrets come from the environment — never code or disk."""
import os

SESSION_TTL = int(os.environ.get("SESSION_TTL", "600"))  # inactivity seconds

# Override a carrier's headless default (e.g. watch a run locally). Adapters set
# their own default; this wins if set to "0"/"1".
HEADLESS_OVERRIDE = os.environ.get("HEADLESS")


def soax_proxy() -> dict | None:
    """SOAX mobile proxy for carriers that need mobile egress (State Farm).
    Returns a Playwright proxy dict, or None if not configured. Creds are env-
    only; rotation params live in SOAX_USER (…-sessionid-<id>-sessionlength-…)."""
    user = os.environ.get("SOAX_USER")
    password = os.environ.get("SOAX_PASS")
    if not (user and password):
        return None
    host = os.environ.get("SOAX_HOST", "proxy.soax.com")
    port = os.environ.get("SOAX_PORT", "5000")
    return {"server": f"http://{host}:{port}", "username": user, "password": password}
