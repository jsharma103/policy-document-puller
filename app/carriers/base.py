"""Carrier adapter interface.

Each carrier maps onto the same four lifecycle steps:

    start_login → submit_mfa → list_documents → fetch_pdf

…but the *how* differs wildly (Lemonade is headless + direct datacenter IP and
single-domain; State Farm needs stealth + real Chrome + headful-under-Xvfb + a
mobile proxy + a 3-domain SSO settle). So each adapter also declares a
`LaunchSpec` describing how its browser must run. Everything carrier-specific —
browser mode, egress, login shape, SSO/settle timing, which document is "the"
document — lives inside that carrier's adapter and nowhere else.

Credentials are passed in, used immediately to drive the browser, and never
stored or logged. Document ids/links are always discovered at runtime from the
carrier's own API — never hardcoded. Proxy/secret values are resolved from the
environment by the browser layer, not embedded here.
"""
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol

from playwright.async_api import BrowserContext, Page


class Egress(str, Enum):
    DIRECT = "direct"          # plain datacenter IP — Lemonade is fine here
    MOBILE_PROXY = "mobile"    # SOAX mobile/4G — State Farm fake-rejects datacenter IPs


@dataclass
class LaunchSpec:
    """How a carrier's browser must be launched. Captured per-carrier because
    each tolerates different things (see carrier-flow findings)."""
    headless: bool = True             # State Farm must be headful (run under Xvfb on the server)
    egress: Egress = Egress.DIRECT    # State Farm: MOBILE_PROXY
    transport: str = "browser"        # "browser" (Playwright) or "api" (curl_cffi, no browser)


@dataclass
class Credentials:
    username: str                     # email for Lemonade, User ID for State Farm
    password: str | None = None       # None for passwordless carriers (Lemonade)


@dataclass
class MfaPrompt:
    """Returned by start_login — tells the UI whether/what to ask the user."""
    required: bool
    message: str = ""                 # human-facing hint for the prompt


@dataclass
class DocMeta:
    """A policy document discovered from the carrier's API (never hardcoded)."""
    doc_id: str                       # opaque id the adapter understands
    title: str
    category: str = ""
    extra: dict = field(default_factory=dict)  # carrier fetch hints (form_url, commId, viewer route…)


class CarrierAdapter(Protocol):
    """Implemented by each carrier. The app driver only ever talks to this."""

    name: str
    launch: LaunchSpec

    async def start_login(self, page: Page, creds: Credentials) -> MfaPrompt:
        """Drive the portal (handling this carrier's login shape) up to the
        point an MFA code is required; return what the UI should prompt for.
        Raises on a real auth failure."""
        ...

    async def submit_mfa(self, page: Page, code: str) -> None:
        """Enter the user-supplied code and complete any post-MFA SSO/redirect
        handshake so the authenticated session is ready for document fetch."""
        ...

    async def list_documents(self, context: BrowserContext, page: Page) -> list[DocMeta]:
        """Discover documents from the carrier's own API and select the real
        policy document(s)."""
        ...

    async def fetch_pdf(self, context: BrowserContext, page: Page, doc: DocMeta) -> bytes:
        """Return the PDF bytes for a document (streamed to the client, never
        written to disk)."""
        ...
