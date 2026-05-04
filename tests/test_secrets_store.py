"""Local secret storage — keychain → encrypted file → plaintext."""
from __future__ import annotations


def test_set_get_delete_round_trip(local_env, monkeypatch):
    # Force the encrypted-file backend so the test doesn't pollute the
    # developer's real OS keychain. Patch _try_keyring to None.
    import secrets_store
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)
    secrets_store.set_secret("HV_TEST_KEY", "abc-123")
    assert secrets_store.get_secret("HV_TEST_KEY") == "abc-123"
    names = secrets_store.list_secret_names()
    assert "HV_TEST_KEY" in names
    secrets_store.delete_secret("HV_TEST_KEY")
    assert secrets_store.get_secret("HV_TEST_KEY") is None


def test_get_secret_missing_returns_none(local_env, monkeypatch):
    import secrets_store
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)
    assert secrets_store.get_secret("HV_NEVER_SET") is None


def test_backend_label_matches_available_backends(local_env, monkeypatch):
    import secrets_store
    # Force fallback to plaintext by stubbing both keyring and Fernet
    monkeypatch.setattr(secrets_store, "_try_keyring", lambda: None)
    monkeypatch.setattr(secrets_store, "_try_fernet", lambda: None)
    assert secrets_store._backend_label() == "plaintext-file"
