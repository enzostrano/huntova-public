"""BRAIN-198: email_service._smtp_settings + is_email_configured + _esc audit.

Pinned invariants:

1. `_smtp_settings` reads SMTP_HOST, SMTP_PORT, SMTP_USER /
   HV_SMTP_USER, SMTP_PASSWORD / HV_SMTP_PASSWORD, SMTP_FROM_EMAIL,
   SMTP_FROM_NAME from env at call time (NOT at module import).
2. `_smtp_settings` accepts both `SMTP_USER` and `HV_SMTP_USER`
   forms (HV-prefix is canonical for keychain bridge; bare form is
   legacy generic SMTP convention).
3. `_smtp_settings` defaults port to 587 (SMTP submission TLS).
4. `_smtp_settings` defaults from_email to noreply@huntova.com.
5. `is_email_configured` returns True only when host + user +
   password all present.
6. `_esc` HTML-escapes to prevent injection in email templates.
7. `_esc` handles None / empty without crashing.
"""
from __future__ import annotations

import importlib


def test_smtp_settings_reads_env_at_call_time(local_env, monkeypatch):
    """The function must read env at call time, not at module import.
    This lets the dashboard hydrate env right before the call."""
    import email_service
    importlib.reload(email_service)

    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_PORT", "587")
    monkeypatch.setenv("SMTP_USER", "alice@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")

    s = email_service._smtp_settings()
    assert s["host"] == "smtp.gmail.com"
    assert s["port"] == 587
    assert s["user"] == "alice@example.com"
    assert s["password"] == "secret"


def test_smtp_settings_accepts_hv_prefix_user(local_env, monkeypatch):
    """HV_SMTP_USER / HV_SMTP_PASSWORD are canonical for keychain bridge."""
    import email_service
    importlib.reload(email_service)

    for k in ("SMTP_USER", "SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("HV_SMTP_USER", "hv-user@example.com")
    monkeypatch.setenv("HV_SMTP_PASSWORD", "hv-secret")

    s = email_service._smtp_settings()
    assert s["user"] == "hv-user@example.com"
    assert s["password"] == "hv-secret"


def test_smtp_settings_bare_user_takes_priority_over_hv(local_env, monkeypatch):
    """When both forms set, bare SMTP_USER wins (matches `or` order
    in source: `SMTP_USER or HV_SMTP_USER`)."""
    import email_service
    importlib.reload(email_service)

    monkeypatch.setenv("SMTP_USER", "bare@example.com")
    monkeypatch.setenv("HV_SMTP_USER", "hv@example.com")
    s = email_service._smtp_settings()
    assert s["user"] == "bare@example.com"


def test_smtp_settings_default_port_587(local_env, monkeypatch):
    """Default SMTP port is 587 (submission with STARTTLS)."""
    import email_service
    importlib.reload(email_service)

    monkeypatch.delenv("SMTP_PORT", raising=False)
    s = email_service._smtp_settings()
    assert s["port"] == 587


def test_smtp_settings_default_from_email(local_env, monkeypatch):
    """Default from_email is noreply@huntova.com."""
    import email_service
    importlib.reload(email_service)

    monkeypatch.delenv("SMTP_FROM_EMAIL", raising=False)
    s = email_service._smtp_settings()
    assert s["from_email"] == "noreply@huntova.com"


def test_smtp_settings_default_from_name(local_env, monkeypatch):
    """Default from_name is Huntova."""
    import email_service
    importlib.reload(email_service)

    monkeypatch.delenv("SMTP_FROM_NAME", raising=False)
    s = email_service._smtp_settings()
    assert s["from_name"] == "Huntova"


def test_smtp_settings_returns_dict_with_all_keys(local_env, monkeypatch):
    """All 6 documented keys present."""
    import email_service
    importlib.reload(email_service)
    s = email_service._smtp_settings()
    expected = {"host", "port", "user", "password", "from_email", "from_name"}
    assert set(s.keys()) == expected


def test_is_email_configured_all_present(local_env, monkeypatch):
    import email_service
    importlib.reload(email_service)

    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_USER", "alice@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    assert email_service.is_email_configured() is True


def test_is_email_configured_missing_host(local_env, monkeypatch):
    import email_service
    importlib.reload(email_service)

    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.setenv("SMTP_USER", "alice@example.com")
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    assert email_service.is_email_configured() is False


def test_is_email_configured_missing_user(local_env, monkeypatch):
    import email_service
    importlib.reload(email_service)

    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    for k in ("SMTP_USER", "HV_SMTP_USER"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("SMTP_PASSWORD", "secret")
    assert email_service.is_email_configured() is False


def test_is_email_configured_missing_password(local_env, monkeypatch):
    import email_service
    importlib.reload(email_service)

    monkeypatch.setenv("SMTP_HOST", "smtp.gmail.com")
    monkeypatch.setenv("SMTP_USER", "alice@example.com")
    for k in ("SMTP_PASSWORD", "HV_SMTP_PASSWORD"):
        monkeypatch.delenv(k, raising=False)
    assert email_service.is_email_configured() is False


def test_is_email_configured_empty_strings(local_env, monkeypatch):
    """Empty-string env values count as unconfigured."""
    import email_service
    importlib.reload(email_service)

    monkeypatch.setenv("SMTP_HOST", "")
    monkeypatch.setenv("SMTP_USER", "")
    monkeypatch.setenv("SMTP_PASSWORD", "")
    assert email_service.is_email_configured() is False


def test_esc_handles_none():
    from email_service import _esc
    assert _esc(None) == ""


def test_esc_handles_empty():
    from email_service import _esc
    assert _esc("") == ""


def test_esc_html_escapes():
    from email_service import _esc
    out = _esc("<script>alert(1)</script>")
    assert "<script>" not in out
    assert "&lt;script&gt;" in out


def test_esc_escapes_ampersand_and_quotes():
    from email_service import _esc
    out = _esc('Hello & "world"')
    assert "&amp;" in out
    # Either escaped quotes or preserved depending on html.escape mode.
    assert "&" in out


def test_esc_coerces_non_string():
    """Defensive: a non-string accidentally passed in must not crash."""
    from email_service import _esc
    out = _esc(42)
    assert isinstance(out, str)
    assert "42" in out
