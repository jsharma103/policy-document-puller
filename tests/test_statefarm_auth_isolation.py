"""Regression test for the per-session Bearer-token store (issue #1).

Tokens used to live in a module global keyed by id(session_object): never
cleaned up (leak) and vulnerable to id() reuse after GC (cross-session bleed).
They now live in a WeakKeyDictionary keyed by the session object itself. These
tests lock in both properties: isolation and automatic cleanup.
"""
import gc

from app.carriers import statefarm as sf
from app.carriers._api import ApiSession


class _Sess:
    """Minimal weakref-able stand-in for a per-session object."""


def test_tokens_are_isolated_per_session():
    a, b = _Sess(), _Sess()
    sf._set_auth(a, "Bearer A")
    sf._set_auth(b, "Bearer B")
    assert sf._get_auth(a) == "Bearer A"
    assert sf._get_auth(b) == "Bearer B"      # no bleed across sessions


def test_unknown_session_returns_none():
    assert sf._get_auth(_Sess()) is None       # never raises / never another's token


def test_entry_dropped_after_session_gc():
    s = _Sess()
    sf._set_auth(s, "Bearer X")
    before = len(sf._session_auth)
    del s
    gc.collect()
    assert len(sf._session_auth) == before - 1  # weak key -> no leak


async def test_real_apisession_supported():
    """The production API-path session type (ApiSession) works with the store."""
    api = ApiSession()
    try:
        sf._set_auth(api, "Bearer Z")
        assert sf._get_auth(api) == "Bearer Z"
    finally:
        await api.close()
