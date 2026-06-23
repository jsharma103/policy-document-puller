"""Settings: env reading (quote-tolerant) and proxy resolution. Secrets only
ever come from the environment, so this guards that contract.
"""
from app import config


def test_env_strips_quotes_and_whitespace(monkeypatch):
    monkeypatch.setenv("X_TEST", '"hello"')
    assert config._env("X_TEST") == "hello"
    monkeypatch.setenv("X_TEST", "  spaced  ")
    assert config._env("X_TEST") == "spaced"


def test_env_default_when_missing(monkeypatch):
    monkeypatch.delenv("X_MISSING", raising=False)
    assert config._env("X_MISSING") is None
    assert config._env("X_MISSING", "fallback") == "fallback"


def test_soax_proxy_none_when_unset(monkeypatch):
    monkeypatch.delenv("SOAX_USER", raising=False)
    monkeypatch.delenv("SOAX_PASS", raising=False)
    assert config.soax_proxy() is None


def test_soax_proxy_builds_dict(monkeypatch):
    monkeypatch.setenv("SOAX_USER", "u")
    monkeypatch.setenv("SOAX_PASS", "p")
    monkeypatch.setenv("SOAX_HOST", "h.example.com")
    monkeypatch.setenv("SOAX_PORT", "1234")
    assert config.soax_proxy() == {
        "server": "http://h.example.com:1234", "username": "u", "password": "p"}


def test_soax_proxy_host_port_defaults(monkeypatch):
    monkeypatch.setenv("SOAX_USER", "u")
    monkeypatch.setenv("SOAX_PASS", "p")
    monkeypatch.delenv("SOAX_HOST", raising=False)
    monkeypatch.delenv("SOAX_PORT", raising=False)
    assert config.soax_proxy()["server"] == "http://proxy.soax.com:5000"
