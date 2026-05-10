"""Regression test for BRAIN-53 (a414): _send_email_sync must
reject CRLF in the configured from_email setting. Pre-fix the
recipient `to` was scrubbed but `from_email` flowed unchecked
into From, List-Unsubscribe mailto, Message-ID domain, and SMTP
envelope sender.

Per GPT-5.4 audit on email_service.py SMTP header injection class.
"""
from __future__ import annotations
from unittest import mock


def _bad_settings(from_email: str):
    return {
        "host": "smtp.example.com",
        "port": 587,
        "user": "u",
        "password": "p",
        "from_email": from_email,
        "from_name": "Test",
    }


def test_from_email_with_crlf_rejected():
    from email_service import _send_email_sync
    bad = _bad_settings("ok@example.com\r\nBcc: attacker@evil.com")
    with mock.patch("email_service._smtp_settings", return_value=bad), \
         mock.patch("email_service._check_smtp_rate"):
        try:
            _send_email_sync("recipient@example.com", "Subj", "<b>hi</b>")
        except ValueError as e:
            assert "from_email" in str(e).lower()
            return
        raise AssertionError(
            "BRAIN-53: from_email with CRLF must raise ValueError before SMTP send"
        )


def test_from_email_with_lf_only_rejected():
    """CR alone is rare; LF alone is common in the SMTP-injection
    attack pattern. Reject either."""
    from email_service import _send_email_sync
    bad = _bad_settings("ok@example.com\nBcc: x@evil.com")
    with mock.patch("email_service._smtp_settings", return_value=bad), \
         mock.patch("email_service._check_smtp_rate"):
        try:
            _send_email_sync("recipient@example.com", "Subj", "<b>hi</b>")
        except ValueError:
            return
        raise AssertionError("BRAIN-53: LF-only injection must also be rejected")


def test_clean_from_email_passes_validation_layer():
    """Sanity: clean inputs don't trip the new validator. We mock
    smtplib so no actual network call happens."""
    from email_service import _send_email_sync
    good = _bad_settings("ok@example.com")
    with mock.patch("email_service._smtp_settings", return_value=good), \
         mock.patch("email_service._check_smtp_rate"), \
         mock.patch("email_service.smtplib") as _smtp:
        _smtp.SMTP.return_value.__enter__.return_value = mock.MagicMock()
        try:
            _send_email_sync("recipient@example.com", "Subj", "<b>hi</b>")
        except ValueError:
            raise AssertionError("BRAIN-53: clean from_email must not be rejected")
        # No network assertion — just that ValueError didn't fire.
