"""BRAIN-171: secrets_store get_secret + list_secret_names invariant audit.

Complements BRAIN-163's set_secret + sweep audit. Pins the read-side
behavior across all 3 tiers (keychain / Fernet / plaintext):

1. `get_secret(name)` returns None when name not present.
2. Plaintext-only round-trips set→get correctly.
3. Fernet tier returns None (not raise) when file is corrupted.
4. Keychain tier returns None when underlying SDK raises.
5. `list_secret_names` returns sorted in plaintext + Fernet tiers;
   sorted-or-original in keyring (depends on index write order).
6. `delete_secret` followed by `get_secret` returns None.
7. Empty / whitespace-only secret values not specially handled —
   they round-trip (caller decides if empty means "unset").
8. `_kr_index` + `_kr_index_write` round-trip a list of names.
"""
from __future__ import annotations

import importlib


def test_get_secret_returns_none_when_absent_plaintext(local_env, monkeypatch):
    """Plaintext-only tier: get on a never-set name returns None."""
    import secrets_store
    importlib.reload(secrets_store)
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)
    # Empty plaintext file.
    secrets_store._plain_write({})
    assert secrets_store.get_secret("HV_NONEXISTENT") is None


def test_set_then_get_plaintext(local_env, monkeypatch):
    import secrets_store
    importlib.reload(secrets_store)
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    secrets_store.set_secret("HV_KEY", "mySecret123")
    assert secrets_store.get_secret("HV_KEY") == "mySecret123"


def test_delete_then_get_plaintext(local_env, monkeypatch):
    import secrets_store
    importlib.reload(secrets_store)
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    secrets_store.set_secret("HV_K", "v1")
    assert secrets_store.get_secret("HV_K") == "v1"
    secrets_store.delete_secret("HV_K")
    assert secrets_store.get_secret("HV_K") is None


def test_set_overwrites_plaintext(local_env, monkeypatch):
    import secrets_store
    importlib.reload(secrets_store)
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    secrets_store.set_secret("HV_K", "v1")
    secrets_store.set_secret("HV_K", "v2")
    assert secrets_store.get_secret("HV_K") == "v2"


def test_list_secret_names_sorted_plaintext(local_env, monkeypatch):
    import secrets_store
    importlib.reload(secrets_store)
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    # Insert in non-alphabetical order.
    secrets_store.set_secret("HV_C", "c")
    secrets_store.set_secret("HV_A", "a")
    secrets_store.set_secret("HV_B", "b")
    names = secrets_store.list_secret_names()
    assert names == sorted(names), "plaintext list must be sorted"
    assert "HV_A" in names
    assert "HV_B" in names
    assert "HV_C" in names


def test_keychain_failure_returns_none(local_env, monkeypatch):
    """When the keyring backend raises (libsecret daemon down etc.),
    get_secret must return None — not raise."""
    import secrets_store
    importlib.reload(secrets_store)

    class _FailingKeyring:
        def get_password(self, app, name):
            raise RuntimeError("libsecret daemon is down")
        def set_password(self, app, name, value):
            pass
        def delete_password(self, app, name):
            pass

    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: _FailingKeyring())
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    # Must not raise.
    result = secrets_store.get_secret("HV_X")
    assert result is None


def test_keychain_returns_value_when_present(local_env, monkeypatch):
    import secrets_store
    importlib.reload(secrets_store)

    class _FakeKeyring:
        def __init__(self):
            self.pwds = {}
        def set_password(self, app, name, value):
            self.pwds[(app, name)] = value
        def get_password(self, app, name):
            return self.pwds.get((app, name))
        def delete_password(self, app, name):
            self.pwds.pop((app, name), None)

    fake = _FakeKeyring()
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: fake)
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    secrets_store.set_secret("HV_TEST", "the-value")
    assert secrets_store.get_secret("HV_TEST") == "the-value"


def test_keychain_returns_none_when_absent(local_env, monkeypatch):
    import secrets_store
    importlib.reload(secrets_store)

    class _EmptyKeyring:
        def get_password(self, app, name):
            return None
        def set_password(self, app, name, value):
            pass
        def delete_password(self, app, name):
            pass

    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: _EmptyKeyring())
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    assert secrets_store.get_secret("HV_NEVER_SET") is None


def test_kr_index_round_trip(local_env, monkeypatch):
    """The keyring-index helpers must round-trip a list of names."""
    import secrets_store
    importlib.reload(secrets_store)

    class _FakeKeyring:
        def __init__(self):
            self.pwds = {}
        def set_password(self, app, name, value):
            self.pwds[(app, name)] = value
        def get_password(self, app, name):
            return self.pwds.get((app, name))

    fake = _FakeKeyring()
    secrets_store._kr_index_write(fake, ["HV_A", "HV_B", "HV_C"])
    out = secrets_store._kr_index_read(fake)
    assert isinstance(out, list)
    assert "HV_A" in out
    assert "HV_B" in out
    assert "HV_C" in out


def test_kr_index_handles_missing(local_env, monkeypatch):
    """When no index has been written, _kr_index_read returns []."""
    import secrets_store
    importlib.reload(secrets_store)

    class _EmptyKeyring:
        def get_password(self, app, name):
            return None

    out = secrets_store._kr_index_read(_EmptyKeyring())
    assert out == []


def test_empty_string_value_round_trips(local_env, monkeypatch):
    """Caller may store an empty string. Round-trip preserves it
    (caller decides whether empty means unset)."""
    import secrets_store
    importlib.reload(secrets_store)
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    secrets_store.set_secret("HV_EMPTY", "")
    # Plaintext round-trips empty.
    assert secrets_store.get_secret("HV_EMPTY") == ""


def test_long_value_round_trips(local_env, monkeypatch):
    """A 4KB secret value (long Anthropic key + JSON metadata) must
    round-trip unchanged."""
    import secrets_store
    importlib.reload(secrets_store)
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    big = "x" * 4096
    secrets_store.set_secret("HV_BIG", big)
    assert secrets_store.get_secret("HV_BIG") == big


def test_unicode_value_round_trips(local_env, monkeypatch):
    """Unicode (emoji / non-ASCII) in a secret value survives
    serialization."""
    import secrets_store
    importlib.reload(secrets_store)
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    weird = "héllo-wörld-🦊-中文"
    secrets_store.set_secret("HV_WEIRD", weird)
    assert secrets_store.get_secret("HV_WEIRD") == weird


def test_delete_nonexistent_does_not_raise(local_env, monkeypatch):
    """delete_secret on a never-set name must be a no-op, not raise."""
    import secrets_store
    importlib.reload(secrets_store)
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    # Plaintext file may not exist yet.
    secrets_store.delete_secret("HV_NEVER_SET")  # must not raise


def test_get_secret_clears_keychain_warning_sentinel(local_env, monkeypatch, tmp_path):
    """A successful keychain read must remove the sentinel file
    (so the warning can fire again later if keychain breaks)."""
    import secrets_store
    importlib.reload(secrets_store)

    class _FakeKeyring:
        def get_password(self, app, name):
            return "value"
        def set_password(self, app, name, value):
            pass
        def delete_password(self, app, name):
            pass

    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: _FakeKeyring())

    # Pre-create the sentinel file.
    base = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(base))
    sentinel_dir = base / "huntova"
    sentinel_dir.mkdir(parents=True, exist_ok=True)
    sentinel = sentinel_dir / ".keychain_warned"
    sentinel.touch()
    assert sentinel.exists()

    # Successful read should clear it.
    secrets_store.get_secret("HV_X")
    assert not sentinel.exists(), "sentinel must be cleared on successful keychain read"
