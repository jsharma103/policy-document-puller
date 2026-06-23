"""State Farm Okta IDX parsing + PKCE encoding. These pick apart the IDX JSON
to drive the reverse-engineered HTTP login; an Okta response-shape change would
surface here. Pure functions — no browser, no network.
"""
import base64

from app.carriers import _sf_idx as idx


def test_b64u_urlsafe_and_unpadded():
    assert idx._b64u(b"\xff\xff\xff") == "____"   # urlsafe ('/' -> '_'), no '='
    out = idx._b64u(b"\x00")                       # std b64 would pad to "AA=="
    assert "=" not in out and "/" not in out and "+" not in out
    pad = "=" * (-len(out) % 4)                    # round-trips back to the bytes
    assert base64.urlsafe_b64decode(out + pad) == b"\x00"


def test_find_password_id():
    j = {"remediation": {"value": [{"f": [{"type": "password", "id": "aut123"}]}]}}
    assert idx._find_password_id(j) == "aut123"
    # an id that doesn't look like an Okta authenticator id is ignored
    assert idx._find_password_id({"type": "password", "id": "nope"}) is None


def test_find_email_authenticator_extracts_form_values():
    j = {"remediation": {"value": [
        {"name": "select-authenticator-authenticate", "value": [
            {"name": "authenticator", "options": [
                {"label": "Email", "value": {"form": {"value": [
                    {"name": "id", "value": "emailId"},
                    {"name": "methodType", "value": "email"},
                    {"name": "noValueField"},          # no "value" -> omitted
                ]}}},
                {"label": "Phone", "value": {"form": {"value": [
                    {"name": "id", "value": "phoneId"}]}}},
            ]},
        ]},
    ]}}
    assert idx._find_email_authenticator(j) == {"id": "emailId", "methodType": "email"}
    assert idx._find_email_authenticator({"remediation": {"value": []}}) is None


def test_find_interaction_code():
    assert idx._find_interaction_code(
        {"a": {"name": "interaction_code", "value": "CODE"}}) == "CODE"
    assert idx._find_interaction_code({"a": {"name": "other", "value": "x"}}) is None


def test_messages():
    j = {"messages": {"value": [{"message": "bad creds"}, {"message": "locked"}]}}
    assert idx._messages(j) == ["bad creds", "locked"]
    assert idx._messages({}) == []
