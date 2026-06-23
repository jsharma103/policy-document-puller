"""Document discovery/selection — the brittle, reverse-engineered parsers that
turn each carrier's API JSON into the policy document(s). Pure functions, no
network: this is where a carrier-side response shape change would bite first.
"""
from app.carriers import lemonade as lem
from app.carriers import statefarm as sf
from app.carriers.base import DocMeta


# --- State Farm: customerMetadata -> ranked policy docs --------------------- #
def test_sf_extract_orders_and_filters_payments():
    data = {"documents": [
        {"documentId": "1", "communicationId": "c1", "type": "Payment Receipt", "category": "Billing"},
        {"documentId": "2", "communicationId": "c2", "type": "Policy Binder", "category": "Auto"},
        {"documentId": "3", "communicationId": "c3", "type": "Declarations Page", "category": "Auto"},
    ]}
    docs = sf._extract_docs(data)
    # binder first, dec page next; the payment doc is filtered out as non-policy
    assert [d.doc_id for d in docs] == ["2", "3"]
    assert docs[0].extra == {"documentId": "2", "commId": "c2"}


def test_sf_extract_commid_alias_and_skips_incomplete():
    data = {"x": [
        {"documentId": "9", "commId": "c9", "type": "Invoice"},   # commId alias; payment-type
        {"documentId": "nope"},                                   # no comm id -> not a document
    ]}
    docs = sf._extract_docs(data)
    # everything ranked as payment -> fall back to returning all (never empty)
    assert [d.doc_id for d in docs] == ["9"]
    assert docs[0].extra["commId"] == "c9"


def test_sf_doc_rank_priority():
    mk = lambda t, c="": DocMeta(doc_id="x", title=t, category=c)
    assert sf._doc_rank(mk("Policy Binder")) == 0
    assert sf._doc_rank(mk("Declarations Page")) == 1
    assert sf._doc_rank(mk("Auto ID Card")) == 2
    assert sf._doc_rank(mk("Payment Receipt")) == 3


def test_sf_deep_find_case_insensitive_nested():
    assert sf._deep_find({"custIndexId": 123}, ("custindexid",)) == "123"
    assert sf._deep_find({"a": {"authToken": "xyz"}}, ("authtoken",)) == "xyz"
    assert sf._deep_find({"x": 1}, ("authtoken",)) is None


# --- Lemonade: /policies -> form_url PDFs ----------------------------------- #
def test_lemonade_extract_dedups_and_skips_non_pdf():
    data = {"policies": [
        {"id": "p1", "policy_type": "Renters", "form_url": "https://x/a.pdf", "category": "home"},
        {"id": "p2", "type": "Auto", "form_url": "https://x/b.pdf"},
        {"id": "dup", "form_url": "https://x/a.pdf"},     # duplicate url -> skipped
        {"id": "rel", "form_url": "/relative.pdf"},       # not absolute http -> skipped
        {"id": "none"},                                   # no form_url -> skipped
    ]}
    docs = lem._extract_docs(data)
    assert [d.doc_id for d in docs] == ["p1", "p2"]
    assert docs[0].title == "Renters"
    assert docs[0].extra["form_url"] == "https://x/a.pdf"
    assert docs[1].title == "Auto"
