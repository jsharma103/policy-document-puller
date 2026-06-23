"""Adapter contract — every registered carrier implements the same lifecycle the
app driver depends on. Cheap guard against a half-wired adapter or a typo'd name.
"""
from app.carriers.base import LaunchSpec
from app.carriers.registry import carrier_names, get_adapter

LIFECYCLE = ("start_login", "submit_mfa", "list_documents", "fetch_pdf")


def test_registry_has_expected_carriers():
    assert set(carrier_names()) == {"lemonade", "goodcover", "statefarm"}


def test_every_adapter_conforms():
    for name in carrier_names():
        a = get_adapter(name)
        assert a.name == name
        assert isinstance(a.launch, LaunchSpec)
        for method in LIFECYCLE:
            assert callable(getattr(a, method)), f"{name}.{method} missing"


def test_unknown_carrier_is_none():
    assert get_adapter("not-a-carrier") is None
