"""BRAIN-169: auth.py token + password helper invariant audit.

Pins the security contracts on:

1. `hash_password` — bcrypt-format output starting with $2.
2. `verify_password` — true on match, false on mismatch, false on
   empty inputs (no exception).
3. `generate_token` — 48-byte URL-safe → ≥64 chars, unique per call.
4. `generate_verification_token` — bug #72 fix: dict payload with
   email + uid when uid given; legacy string payload when uid omitted.
5. `verify_verification_token` — round-trips both shapes; returns
   None for tampered or expired tokens.
6. `_password_hash_fingerprint` — deterministic 16-hex-char output;
   different hashes → different fingerprints; same hash → same fp.
7. `generate_reset_token` — bug #70 fix: dict with email + pwf.
8. `verify_reset_token` — returns (email, pwf), legacy compat for
   string tokens, None for bad/expired.
"""
from __future__ import annotations


def test_hash_password_returns_bcrypt_format(local_env):
    from auth import hash_password
    h = hash_password("hunter2")
    assert h.startswith("$2"), f"expected bcrypt format, got {h[:5]!r}"
    # bcrypt hashes are typically 60 chars.
    assert len(h) >= 50


def test_hash_password_uses_random_salt(local_env):
    """Two hashes of the same password must differ (random salt)."""
    from auth import hash_password
    h1 = hash_password("hunter2")
    h2 = hash_password("hunter2")
    assert h1 != h2


def test_verify_password_true_on_match(local_env):
    from auth import hash_password, verify_password
    pw = "correct horse battery staple"
    h = hash_password(pw)
    assert verify_password(pw, h) is True


def test_verify_password_false_on_mismatch(local_env):
    from auth import hash_password, verify_password
    h = hash_password("right")
    assert verify_password("wrong", h) is False


def test_verify_password_false_on_empty_inputs(local_env):
    """Defensive: empty strings must return False, not raise."""
    from auth import verify_password
    assert verify_password("", "") is False
    assert verify_password("password", "") is False
    assert verify_password("", "hash") is False


def test_generate_token_unique(local_env):
    """Two generate_token calls must produce different tokens."""
    from auth import generate_token
    seen = {generate_token() for _ in range(50)}
    assert len(seen) == 50, "generate_token returned duplicates"


def test_generate_token_url_safe_length(local_env):
    """secrets.token_urlsafe(48) → ~64 chars (base64url-encoded)."""
    from auth import generate_token
    t = generate_token()
    assert len(t) >= 60
    # URL-safe → only [A-Za-z0-9_-].
    import re
    assert re.fullmatch(r"[A-Za-z0-9_\-]+", t), (
        f"token {t!r} contains non-url-safe chars"
    )


def test_verification_token_with_user_id_round_trips(local_env, monkeypatch):
    monkeypatch.setenv("HV_SECRET_KEY", "test-secret-xyz")
    import importlib, config
    importlib.reload(config)
    import auth
    importlib.reload(auth)
    tok = auth.generate_verification_token("alice@example.com", user_id=42)
    result = auth.verify_verification_token(tok)
    assert result is not None
    email, uid = result
    assert email == "alice@example.com"
    assert uid == 42


def test_verification_token_legacy_email_only_round_trips(local_env, monkeypatch):
    """Legacy token (no uid) must still verify with uid=0."""
    monkeypatch.setenv("HV_SECRET_KEY", "test-secret-xyz")
    import importlib, config
    importlib.reload(config)
    import auth
    importlib.reload(auth)
    tok = auth.generate_verification_token("bob@example.com", user_id=0)
    result = auth.verify_verification_token(tok)
    assert result is not None
    email, uid = result
    assert email == "bob@example.com"
    assert uid == 0


def test_verification_token_returns_none_on_garbage(local_env, monkeypatch):
    monkeypatch.setenv("HV_SECRET_KEY", "test-secret-xyz")
    import importlib, config
    importlib.reload(config)
    import auth
    importlib.reload(auth)
    assert auth.verify_verification_token("not-a-real-token") is None
    assert auth.verify_verification_token("") is None


def test_password_hash_fingerprint_deterministic(local_env):
    from auth import _password_hash_fingerprint
    h = "$2b$12$abcdefghijklmnop"
    f1 = _password_hash_fingerprint(h)
    f2 = _password_hash_fingerprint(h)
    assert f1 == f2
    assert len(f1) == 16
    # Hex.
    int(f1, 16)


def test_password_hash_fingerprint_changes_with_hash(local_env):
    from auth import _password_hash_fingerprint
    a = _password_hash_fingerprint("$2b$12$AAAAAAAAAAAAAA")
    b = _password_hash_fingerprint("$2b$12$BBBBBBBBBBBBBB")
    assert a != b


def test_password_hash_fingerprint_handles_empty(local_env):
    """Defensive: empty string must return a stable fingerprint, not
    raise. Legacy reset tokens have empty pwf."""
    from auth import _password_hash_fingerprint
    f = _password_hash_fingerprint("")
    assert isinstance(f, str)
    assert len(f) == 16


def test_reset_token_bound_to_password_hash(local_env, monkeypatch):
    """Bug #70 fix: reset token's pwf must change when the user's
    password_hash changes — invalidating prior tokens after a reset."""
    monkeypatch.setenv("HV_SECRET_KEY", "test-secret-xyz")
    import importlib, config
    importlib.reload(config)
    import auth
    importlib.reload(auth)
    tok_old = auth.generate_reset_token("alice@example.com",
                                         password_hash="$2b$old-hash")
    tok_new = auth.generate_reset_token("alice@example.com",
                                         password_hash="$2b$new-hash")
    r_old = auth.verify_reset_token(tok_old)
    r_new = auth.verify_reset_token(tok_new)
    assert r_old is not None and r_new is not None
    _, pwf_old = r_old
    _, pwf_new = r_new
    assert pwf_old != pwf_new, (
        "pwf must differ between password_hashes — bug #70 fix"
    )


def test_reset_token_round_trips_email(local_env, monkeypatch):
    monkeypatch.setenv("HV_SECRET_KEY", "test-secret-xyz")
    import importlib, config
    importlib.reload(config)
    import auth
    importlib.reload(auth)
    tok = auth.generate_reset_token("eve@example.com",
                                     password_hash="$2b$test-hash")
    result = auth.verify_reset_token(tok)
    assert result is not None
    email, pwf = result
    assert email == "eve@example.com"
    assert len(pwf) == 16


def test_reset_token_returns_none_on_garbage(local_env, monkeypatch):
    monkeypatch.setenv("HV_SECRET_KEY", "test-secret-xyz")
    import importlib, config
    importlib.reload(config)
    import auth
    importlib.reload(auth)
    assert auth.verify_reset_token("garbage-token") is None
    assert auth.verify_reset_token("") is None


def test_reset_token_secret_key_bound(local_env, monkeypatch):
    """Tokens minted with one SECRET_KEY must NOT verify under
    a different SECRET_KEY."""
    monkeypatch.setenv("HV_SECRET_KEY", "secret-A")
    import importlib, config
    importlib.reload(config)
    import auth
    importlib.reload(auth)
    tok = auth.generate_reset_token("user@example.com", "$2b$h")

    # Now switch SECRET_KEY and reload — old token must fail to verify.
    monkeypatch.setenv("HV_SECRET_KEY", "secret-B")
    importlib.reload(config)
    importlib.reload(auth)
    assert auth.verify_reset_token(tok) is None
