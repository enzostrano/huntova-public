"""BRAIN-181: cli_deliverability.py SPF/DMARC parser invariant audit.

Pure helpers in `huntova doctor --email` flow. No DNS / network
needed for these — they take pre-parsed TXT records.

Pinned invariants:

1. `_check_spf` returns "fail" on no record, multiple records (RFC
   7208 violation).
2. `_check_spf` warns on `+all` (open relay), `?all` (neutral),
   missing `-all`/`~all`.
3. `_check_spf` accepts `-all` or `~all` as ok.
4. `_check_dmarc` returns "fail" on no record.
5. `_check_dmarc` warns on `p=none` and `p=quarantine`.
6. `_check_dmarc` returns "ok" on `p=reject`.
7. Parsers handle case-insensitively.
8. Parsers handle empty / non-relevant TXT records (mixed in).
"""
from __future__ import annotations


def test_spf_no_record_is_fail():
    from cli_deliverability import _check_spf
    status, _ = _check_spf([])
    assert status == "fail"


def test_spf_no_relevant_record_is_fail():
    """Non-SPF TXT records mixed in — must still report 'fail' on
    no SPF."""
    from cli_deliverability import _check_spf
    status, _ = _check_spf(["google-site-verification=xyz", "MS=ms123"])
    assert status == "fail"


def test_spf_multiple_records_fail():
    """RFC 7208 forbids multiple SPF records."""
    from cli_deliverability import _check_spf
    records = ["v=spf1 include:_spf.google.com -all",
               "v=spf1 a mx -all"]
    status, msg = _check_spf(records)
    assert status == "fail"
    assert "multiple" in msg.lower() or "rfc" in msg.lower()


def test_spf_plus_all_is_warn():
    """`+all` accepts mail from anywhere — open-relay risk."""
    from cli_deliverability import _check_spf
    status, _ = _check_spf(["v=spf1 +all"])
    assert status == "warn"


def test_spf_neutral_all_is_warn():
    """`?all` is neutral — mailbox providers don't trust it."""
    from cli_deliverability import _check_spf
    status, _ = _check_spf(["v=spf1 ?all"])
    assert status == "warn"


def test_spf_missing_all_is_warn():
    """SPF without -all or ~all has no policy."""
    from cli_deliverability import _check_spf
    status, _ = _check_spf(["v=spf1 include:_spf.google.com"])
    assert status == "warn"


def test_spf_hard_fail_is_ok():
    """`-all` is the strict policy — best practice."""
    from cli_deliverability import _check_spf
    status, _ = _check_spf(["v=spf1 include:_spf.google.com -all"])
    assert status == "ok"


def test_spf_soft_fail_is_ok():
    """`~all` is the soft policy — also acceptable."""
    from cli_deliverability import _check_spf
    status, _ = _check_spf(["v=spf1 include:_spf.google.com ~all"])
    assert status == "ok"


def test_spf_case_insensitive():
    """SPF parsing should match `V=SPF1` as well as `v=spf1`."""
    from cli_deliverability import _check_spf
    status, _ = _check_spf(["V=SPF1 include:_spf.google.com -ALL"])
    # Mixed case still matches.
    assert status == "ok"


def test_dmarc_no_record_fail():
    from cli_deliverability import _check_dmarc
    status, _ = _check_dmarc([])
    assert status == "fail"


def test_dmarc_no_relevant_record_fail():
    from cli_deliverability import _check_dmarc
    status, _ = _check_dmarc(["v=spf1 -all", "google-site-verification=x"])
    assert status == "fail"


def test_dmarc_p_none_warn():
    """`p=none` = reporting only, no enforcement."""
    from cli_deliverability import _check_dmarc
    status, _ = _check_dmarc(["v=DMARC1; p=none; rua=mailto:dmarc@example.com"])
    assert status == "warn"


def test_dmarc_p_quarantine_warn():
    """`p=quarantine` = soft policy (junk folder, not reject)."""
    from cli_deliverability import _check_dmarc
    status, _ = _check_dmarc(["v=DMARC1; p=quarantine; rua=mailto:x@y.com"])
    assert status == "warn"


def test_dmarc_p_reject_ok():
    """`p=reject` = strict policy — best practice."""
    from cli_deliverability import _check_dmarc
    status, _ = _check_dmarc(["v=DMARC1; p=reject; rua=mailto:x@y.com"])
    assert status == "ok"


def test_dmarc_case_insensitive():
    from cli_deliverability import _check_dmarc
    status, _ = _check_dmarc(["V=DMARC1; P=REJECT;"])
    assert status == "ok"


def test_dmarc_unparsable_warn():
    """A DMARC record without a `p=...` directive — return warn, not crash."""
    from cli_deliverability import _check_dmarc
    status, _ = _check_dmarc(["v=DMARC1; rua=mailto:x@y.com"])
    assert status == "warn"


def test_spf_fail_on_unrelated_records_only():
    """List with non-SPF v=spf2 garbage doesn't false-positive."""
    from cli_deliverability import _check_spf
    status, _ = _check_spf(["v=spf2 something else"])
    # v=spf2 doesn't start with v=spf1 → no SPF detected.
    assert status == "fail"
