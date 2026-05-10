"""Test the per-account login lockout (a289 P1 + a291 hotfix).

a289 added `_login_record_failure` / `_login_is_locked` / `_login_clear_failures`
to defend against credential-stuffing across rotated IPs (per-IP rate
limit didn't bind). a291 hotfix scoped recording to real-user-wrong-
password only — a regression where the dummy-hash branch for unknown
users would lock victim accounts via attacker-controlled email.

a307 audit found the lockout logic had no tests. This file closes it.
"""
import time


def _reset_buckets():
    """Tests run with a fresh module-level dict to avoid cross-test
    interference. The implementation lives in `auth._LOGIN_FAIL_BUCKETS`."""
    from auth import _LOGIN_FAIL_BUCKETS
    _LOGIN_FAIL_BUCKETS.clear()


def test_lockout_record_increments_bucket():
    from auth import _login_record_failure, _login_is_locked, _LOGIN_FAIL_BUCKETS
    _reset_buckets()
    email = "user@example.com"
    assert not _login_is_locked(email)
    for _ in range(5):
        _login_record_failure(email)
    assert not _login_is_locked(email)  # 5 < 10 threshold


def test_lockout_fires_at_threshold():
    from auth import _login_record_failure, _login_is_locked, _LOGIN_FAIL_THRESHOLD
    _reset_buckets()
    email = "victim@example.com"
    for _ in range(_LOGIN_FAIL_THRESHOLD):
        _login_record_failure(email)
    assert _login_is_locked(email)


def test_lockout_clears_on_success():
    from auth import (_login_record_failure, _login_is_locked,
                       _login_clear_failures, _LOGIN_FAIL_THRESHOLD)
    _reset_buckets()
    email = "user@example.com"
    for _ in range(_LOGIN_FAIL_THRESHOLD):
        _login_record_failure(email)
    assert _login_is_locked(email)
    _login_clear_failures(email)
    assert not _login_is_locked(email)


def test_lockout_keys_are_case_insensitive():
    from auth import _login_record_failure, _login_is_locked, _LOGIN_FAIL_THRESHOLD
    _reset_buckets()
    # Mix of upper/lower — should land in same bucket.
    for _ in range(_LOGIN_FAIL_THRESHOLD):
        _login_record_failure("Victim@Example.COM")
    assert _login_is_locked("victim@example.com")
    assert _login_is_locked("VICTIM@EXAMPLE.COM")


def test_lockout_keys_strip_whitespace():
    from auth import _login_record_failure, _login_is_locked, _LOGIN_FAIL_THRESHOLD
    _reset_buckets()
    for _ in range(_LOGIN_FAIL_THRESHOLD):
        _login_record_failure("  victim@example.com  ")
    assert _login_is_locked("victim@example.com")


def test_lockout_empty_email_noop():
    from auth import _login_record_failure, _login_is_locked
    _reset_buckets()
    # Empty email should not be tracked + should not raise.
    assert _login_record_failure("") is False
    assert _login_record_failure(None) is False
    assert not _login_is_locked("")


def test_lockout_distinct_emails_dont_collide():
    """Locking out victim-A must not lock out victim-B."""
    from auth import _login_record_failure, _login_is_locked, _LOGIN_FAIL_THRESHOLD
    _reset_buckets()
    for _ in range(_LOGIN_FAIL_THRESHOLD):
        _login_record_failure("a@example.com")
    assert _login_is_locked("a@example.com")
    assert not _login_is_locked("b@example.com")
    # a@... still locked even after b probed
    _login_record_failure("b@example.com")
    assert _login_is_locked("a@example.com")
