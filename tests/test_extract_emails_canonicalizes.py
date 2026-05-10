"""Regression test for BRAIN-22 (a383): extract_emails_from_text
returned emails in their original case, even though validate_email
lowercases internally. So `Foo@Acme.com` and `foo@acme.com` both
passed validation and were kept as TWO separate entries — silent
duplication that downstream dedup didn't catch (string compare).
"""
from __future__ import annotations


def test_extract_emails_lowercases():
    from app import extract_emails_from_text
    text = "Contact Foo@Acme.COM or foo@acme.com for help."
    out = extract_emails_from_text(text)
    assert all(e == e.lower() for e in out), (
        f"BRAIN-22 regression: extract_emails_from_text must return "
        f"lowercased emails (validate_email lowercases internally). "
        f"Got: {out}"
    )


def test_extract_emails_dedupes_case_variants():
    from app import extract_emails_from_text
    text = "Email Foo@Acme.com, foo@ACME.com, FOO@acme.com today."
    out = extract_emails_from_text(text)
    assert len(out) == 1, (
        f"BRAIN-22 regression: case-variant duplicates must collapse "
        f"to one. Got: {out}"
    )
    assert out[0] == "foo@acme.com"


def test_extract_emails_preserves_distinct_addresses():
    """Don't regress: distinct emails should all survive."""
    from app import extract_emails_from_text
    text = "ping a@x.com and b@y.com"
    out = extract_emails_from_text(text)
    assert sorted(out) == ["a@x.com", "b@y.com"]
