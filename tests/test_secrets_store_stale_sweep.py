"""BRAIN-163: secrets_store stale-tier sweep on set_secret.

When a higher-tier backend (`keyring`) writes a fresh secret, lower
tiers (Fernet file, plaintext file) used to keep their pre-rotation
copy. If the higher tier later broke (libsecret daemon dies, user
uninstalls the keyring package), `get_secret` would fall through and
silently return the STALE pre-rotation value — the user thought they
rotated their API key but the agent kept using the old one.

Fix mirrors the `delete_secret` a289+a291 multi-tier sweep pattern:
on every `set_secret` to a higher tier, sweep the lower tiers.

These tests pin the new behaviour using the plaintext-only path
(no `keyring` / no `cryptography`) which is the most testable tier.
"""
from __future__ import annotations

from pathlib import Path


def test_set_secret_sweeps_plaintext_when_fernet_active(local_env, monkeypatch, tmp_path):
    """When Fernet is the active tier, set_secret must clear any
    stale plaintext copy with the same name."""
    import importlib
    import secrets_store
    importlib.reload(secrets_store)

    # Force keyring off, leave Fernet on (default if cryptography is
    # available — local_env fixture sets XDG_CONFIG_HOME).
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)

    # Pre-seed plaintext with a stale value.
    plain_path = secrets_store._plain_path()
    plain_path.parent.mkdir(parents=True, exist_ok=True)
    secrets_store._plain_write({"HV_TEST_KEY": "stale-value"})
    assert "HV_TEST_KEY" in secrets_store._plain_read()

    if not secrets_store._try_fernet():
        # Without cryptography, Fernet path won't activate; skip.
        import pytest
        pytest.skip("cryptography not available")

    secrets_store.set_secret("HV_TEST_KEY", "fresh-value")

    # Stale plaintext must be gone.
    assert "HV_TEST_KEY" not in secrets_store._plain_read(), (
        "set_secret on Fernet tier must sweep stale plaintext copy"
    )


def test_set_secret_keyring_path_does_not_crash_without_fernet(local_env, monkeypatch):
    """The keyring branch's _sweep_lower_tiers call must not raise
    even when the Fernet backend is unavailable."""
    import importlib
    import secrets_store
    importlib.reload(secrets_store)

    # Stub keyring with an in-memory dict.
    class _FakeKeyring:
        def __init__(self):
            self.store = {}
        def set_password(self, app, name, value):
            self.store[(app, name)] = value
        def get_password(self, app, name):
            return self.store.get((app, name))
        def delete_password(self, app, name):
            self.store.pop((app, name), None)

    fake = _FakeKeyring()
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: fake)
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    # Must not raise.
    secrets_store.set_secret("HV_K1", "v1")
    assert fake.store[(secrets_store._APP_NAME, "HV_K1")] == "v1"


def test_set_secret_keyring_sweeps_plaintext_stale_copy(local_env, monkeypatch):
    """When keyring is active, set_secret must clear any stale
    plaintext-tier copy."""
    import importlib
    import secrets_store
    importlib.reload(secrets_store)

    class _FakeKeyring:
        def __init__(self):
            self.store = {}
            self.pwds = {}
        def set_password(self, app, name, value):
            self.pwds[(app, name)] = value
        def get_password(self, app, name):
            return self.pwds.get((app, name))
        def delete_password(self, app, name):
            self.pwds.pop((app, name), None)

    fake = _FakeKeyring()
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: fake)
    # Fernet off so plaintext is the only lower tier.
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)

    # Pre-seed plaintext with stale value.
    secrets_store._plain_write({"HV_TEST": "stale-pw"})
    assert "HV_TEST" in secrets_store._plain_read()

    secrets_store.set_secret("HV_TEST", "new-pw")

    # Stale plaintext sweept.
    assert "HV_TEST" not in secrets_store._plain_read()
    # Keyring has the new value.
    assert fake.pwds[(secrets_store._APP_NAME, "HV_TEST")] == "new-pw"


def test_set_secret_does_not_touch_other_names_in_plaintext(local_env, monkeypatch):
    """The sweep must only target the specific name being set —
    other secrets in plaintext stay intact."""
    import importlib
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

    # Two secrets in plaintext, only one being rotated.
    secrets_store._plain_write({
        "HV_TEST_A": "stale-A",
        "HV_TEST_B": "keep-B",
    })

    secrets_store.set_secret("HV_TEST_A", "new-A")

    plain = secrets_store._plain_read()
    assert "HV_TEST_A" not in plain, "rotated secret swept"
    assert plain.get("HV_TEST_B") == "keep-B", "other secrets untouched"


def test_set_secret_sweep_skips_missing_plaintext(local_env, monkeypatch):
    """When plaintext file doesn't exist (fresh install), the sweep
    is a no-op and must not raise."""
    import importlib
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

    # No plaintext file exists.
    plain_path = secrets_store._plain_path()
    if plain_path.exists():
        plain_path.unlink()

    # Must not raise.
    secrets_store.set_secret("HV_FRESH", "value")
    assert fake.pwds[(secrets_store._APP_NAME, "HV_FRESH")] == "value"
