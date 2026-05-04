"""BRAIN-182: cli_approve.py pending-detection + score-band audit.

`_is_pending` controls who enters the manual approval queue. A
regression here either floods the queue with already-emailed leads
or silently locks legitimate pending leads out.

Pinned invariants:

1. Already-sent leads (`email_status` in sent set) are NOT pending.
2. Leads with `status` = approved/rejected are NOT pending.
3. `awaiting_approval` status requires contact_email to be pending
   (audit-wave-28 fix — no email = no send target = filter out).
4. fit_score ≥ 8 + has email + new status → pending.
5. fit_score < 8 + new status → NOT pending (only auto-flag strong fits).
6. Missing contact_email always blocks pending state.
7. `_score_band` boundaries (8/6/0).
8. `_safe_int` defenses match cli_memory's (None / non-numeric / float).
"""
from __future__ import annotations


def test_already_sent_not_pending():
    from cli_approve import _is_pending
    for st in ("email_sent", "followed_up", "replied",
                "meeting_booked", "won"):
        lead = {"email_status": st, "fit_score": 9,
                "contact_email": "x@y.com"}
        assert _is_pending(lead) is False, f"already-sent ({st}) leaked into pending"


def test_approved_status_not_pending():
    from cli_approve import _is_pending
    lead = {"status": "approved", "fit_score": 9,
            "contact_email": "x@y.com"}
    assert _is_pending(lead) is False


def test_rejected_status_not_pending():
    from cli_approve import _is_pending
    lead = {"status": "rejected", "fit_score": 9,
            "contact_email": "x@y.com"}
    assert _is_pending(lead) is False


def test_awaiting_approval_with_email_is_pending():
    from cli_approve import _is_pending
    lead = {"status": "awaiting_approval", "fit_score": 5,
            "contact_email": "x@y.com"}
    # awaiting_approval is the explicit-flag path; fit_score doesn't matter.
    assert _is_pending(lead) is True


def test_awaiting_approval_without_email_not_pending():
    """Audit-wave-28 fix: even an explicitly-flagged lead with no
    email cannot be in the approval queue (nothing to send)."""
    from cli_approve import _is_pending
    lead = {"status": "awaiting_approval", "fit_score": 5,
            "contact_email": ""}
    assert _is_pending(lead) is False


def test_high_fit_score_with_email_pending():
    from cli_approve import _is_pending
    lead = {"fit_score": 9, "contact_email": "x@y.com"}
    assert _is_pending(lead) is True


def test_high_fit_score_at_boundary_pending():
    """fit_score = 8 (boundary) + email → pending."""
    from cli_approve import _is_pending
    lead = {"fit_score": 8, "contact_email": "x@y.com"}
    assert _is_pending(lead) is True


def test_low_fit_score_not_pending():
    """fit_score = 7 → NOT auto-flagged."""
    from cli_approve import _is_pending
    lead = {"fit_score": 7, "contact_email": "x@y.com"}
    assert _is_pending(lead) is False


def test_high_fit_no_email_not_pending():
    """Audit-wave-28 fix: high fit but no contact email = no pending."""
    from cli_approve import _is_pending
    lead = {"fit_score": 10, "contact_email": ""}
    assert _is_pending(lead) is False


def test_high_fit_whitespace_email_not_pending():
    """Whitespace-only email = no email."""
    from cli_approve import _is_pending
    lead = {"fit_score": 10, "contact_email": "   "}
    assert _is_pending(lead) is False


def test_high_fit_none_email_not_pending():
    from cli_approve import _is_pending
    lead = {"fit_score": 10, "contact_email": None}
    assert _is_pending(lead) is False


def test_score_band_high():
    from cli_approve import _score_band
    assert _score_band(8) == "high"
    assert _score_band(10) == "high"
    assert _score_band(15) == "high"  # >10 still high


def test_score_band_medium():
    from cli_approve import _score_band
    assert _score_band(6) == "medium"
    assert _score_band(7) == "medium"


def test_score_band_low():
    from cli_approve import _score_band
    assert _score_band(0) == "low"
    assert _score_band(5) == "low"
    assert _score_band(-1) == "low"


def test_score_band_handles_none():
    from cli_approve import _score_band
    assert _score_band(None) == "low"


def test_score_band_handles_non_numeric():
    from cli_approve import _score_band
    assert _score_band("not-a-number") == "low"
    assert _score_band({}) == "low"


def test_safe_int_defenses_match_pattern():
    from cli_approve import _safe_int
    assert _safe_int(None) == 0
    assert _safe_int("") == 0
    assert _safe_int("abc") == 0
    assert _safe_int(7) == 7
    assert _safe_int("7") == 7


def test_status_field_takes_priority_over_score():
    """status=approved overrides high fit_score."""
    from cli_approve import _is_pending
    lead = {"status": "approved", "fit_score": 10,
            "contact_email": "x@y.com"}
    assert _is_pending(lead) is False
