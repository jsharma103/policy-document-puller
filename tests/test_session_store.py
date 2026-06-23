"""SessionStore — holds each user's authenticated session warm across
login -> MFA -> fetch, and reaps it on inactivity/close (eval criterion #5).
Uses a fake browser so there's no real Chromium/HTTP; we only assert lifecycle.
"""
import time

from app import config
from app.session import SessionStore


class FakeBrowser:
    """Stands in for app.browser.Browser — only needs an async aclose()."""
    def __init__(self):
        self.closed = False

    async def aclose(self):
        self.closed = True


def test_create_and_get_roundtrip():
    store = SessionStore()
    s = store.create("lemonade", object(), FakeBrowser())
    assert s.id and store.get(s.id) is s
    assert store.get("does-not-exist") is None


def test_get_refreshes_last_used():
    store = SessionStore()
    s = store.create("lemonade", object(), FakeBrowser())
    s.last_used = time.time() - 5
    before = s.last_used
    assert store.get(s.id) is s
    assert s.last_used > before          # access slides the TTL window forward


def test_get_returns_none_when_expired():
    store = SessionStore()
    s = store.create("lemonade", object(), FakeBrowser())
    s.last_used = time.time() - (config.SESSION_TTL + 10)
    assert store.get(s.id) is None


async def test_close_tears_down_browser_and_is_idempotent():
    store = SessionStore()
    br = FakeBrowser()
    s = store.create("lemonade", object(), br)
    await store.close(s.id)
    assert br.closed is True
    assert store.get(s.id) is None
    await store.close(s.id)              # second close must not raise


async def test_sweep_reaps_only_stale():
    store = SessionStore()
    fresh_br, stale_br = FakeBrowser(), FakeBrowser()
    fresh = store.create("lemonade", object(), fresh_br)
    stale = store.create("statefarm", object(), stale_br)
    stale.last_used = time.time() - (config.SESSION_TTL + 10)
    await store.sweep()
    assert stale_br.closed is True
    assert fresh_br.closed is False
    assert store.get(fresh.id) is fresh


async def test_close_all():
    store = SessionStore()
    brs = [FakeBrowser() for _ in range(3)]
    for b in brs:
        store.create("lemonade", object(), b)
    await store.close_all()
    assert all(b.closed for b in brs)
