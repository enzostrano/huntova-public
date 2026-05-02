"""Test the Fernet salt persistence + legacy fallback.

a289 introduced random per-install salt at `~/.config/huntova/.salt`.
a293 made the persist-failed path deterministic (legacy public-derived
salt instead of fresh-random per process — ciphertexts stayed readable).
a303 fixed corrupted-salt-file path: on existing-file with wrong length,
use legacy fallback instead of regenerating fresh-random (which would
orphan all existing ciphertexts).

a307 audit found ZERO coverage for any of this. Closes that gap.
"""


def test_derive_key_creates_salt_file_on_first_call(tmp_path, monkeypatch):
    """First call generates + persists a 16-byte salt."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib
    import secrets_store
    importlib.reload(secrets_store)
    if hasattr(secrets_store._derive_key, "_legacy_warned"):
        delattr(secrets_store._derive_key, "_legacy_warned")
    secrets_store._derive_key()
    salt_path = secrets_store._enc_path().with_name(".salt")
    assert salt_path.exists()
    assert len(salt_path.read_bytes()) == 16


def test_derive_key_is_deterministic_across_calls(tmp_path, monkeypatch):
    """Second call reads the persisted salt + returns the same key."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib
    import secrets_store
    importlib.reload(secrets_store)
    if hasattr(secrets_store._derive_key, "_legacy_warned"):
        delattr(secrets_store._derive_key, "_legacy_warned")
    key1 = secrets_store._derive_key()
    key2 = secrets_store._derive_key()
    assert key1 == key2


def test_derive_key_corrupted_salt_uses_legacy_fallback(tmp_path, monkeypatch):
    """a303: corrupted-length salt file → legacy public derivation,
    NOT fresh-random rotation. Existing ciphertexts encrypted with the
    legacy salt stay readable; fresh-random rotation would orphan them.
    """
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib
    import secrets_store
    importlib.reload(secrets_store)
    if hasattr(secrets_store._derive_key, "_legacy_warned"):
        delattr(secrets_store._derive_key, "_legacy_warned")
    secrets_store._derive_key()
    salt_path = secrets_store._enc_path().with_name(".salt")
    # Corrupt the salt file (12 bytes instead of 16).
    salt_path.write_bytes(b"\x00" * 12)
    secrets_store._derive_key()
    # Verify the bad salt file is NOT rotated to a fresh random one.
    assert salt_path.read_bytes() == b"\x00" * 12


def test_derive_key_warns_only_once_per_process(tmp_path, monkeypatch, capfd):
    """a303: the salt-persist-failed WARN should fire ONCE, not on
    every _derive_key call (which is every get_secret/set_secret)."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("HOME", str(tmp_path))
    import importlib
    import secrets_store
    importlib.reload(secrets_store)
    if hasattr(secrets_store._derive_key, "_legacy_warned"):
        delattr(secrets_store._derive_key, "_legacy_warned")
    # Set up a corrupt existing salt to force the legacy-fallback path
    salt_path = secrets_store._enc_path().with_name(".salt")
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt_path.write_bytes(b"\x00" * 8)
    capfd.readouterr()  # drain prior output
    for _ in range(5):
        secrets_store._derive_key()
    out, err = capfd.readouterr()
    warn_count = err.count("salt persist failed")
    assert warn_count <= 1, f"warning fired {warn_count} times, expected ≤1"
