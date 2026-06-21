"""App settings. All secrets come from the environment — never code or disk."""
import os


def _env(key: str, default: str | None = None) -> str | None:
    """Read an env var, tolerating surrounding quotes. `docker --env-file` passes
    `KEY="val"` through literally (unlike shell `source`, which strips quotes), so
    stripping here makes a quoted .env work the same either way."""
    v = os.environ.get(key)
    if v is None:
        return default
    return v.strip().strip('"').strip("'")


SESSION_TTL = int(_env("SESSION_TTL", "600"))  # inactivity seconds

# Where opt-in "remember this device" persists a carrier's storage_state (the
# device-trust cookie). Ephemeral by default; mount a volume for true persistence.
STATE_DIR = _env("STATE_DIR", "/tmp/pdp_state")

# Override a carrier's headless default (e.g. watch a run locally). Adapters set
# their own default; this wins if set to "0"/"1".
HEADLESS_OVERRIDE = _env("HEADLESS")


def soax_proxy() -> dict | None:
    """Mobile proxy for carriers that need mobile egress (State Farm). Returns a
    Playwright proxy dict, or None if not configured. Creds are env-only; the
    SOAX_ prefix is historical — any HTTP proxy provider works (SOAX, Decodo, …),
    and rotation/session params live inside SOAX_USER."""
    user = _env("SOAX_USER")
    password = _env("SOAX_PASS")
    if not (user and password):
        return None
    host = _env("SOAX_HOST", "proxy.soax.com")
    port = _env("SOAX_PORT", "5000")
    return {"server": f"http://{host}:{port}", "username": user, "password": password}
