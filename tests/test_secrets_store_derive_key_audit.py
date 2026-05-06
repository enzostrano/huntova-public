"""BRAIN-174: secrets_store._derive_key invariant audit.

`_derive_key` produces the Fernet key used to encrypt the user's
saved secrets when keyring isn't available. Critical contract:

1. Determinism — same install + same salt → same key (otherwise
   ciphertexts written yesterday don't decrypt today).
2. PBKDF2 iterations match OWASP 2024+ recommendation (≥600_000 for
   SHA256). Lowering this is a security regression.
3. Output is a valid 32-byte URL-safe base64 Fernet key.
4. Salt file (`.salt`) gets persisted with mode 0600 on first call.
5. Legacy fallback fires when salt-persist fails (read-only fs).
6. Legacy fallback warning fires only once per process (a303 fix).
7. Existing salt file with wrong length triggers legacy fallback
   rather than rotating to a fresh-random salt (a303 fix —
   preserves prior Fernet ciphertexts on disk).
"""
from __future__ import annotations

import importlib
import pytest


def _have_cryptography() -> bool:
    try:
        import cryptography  # noqa: F401
        return True
    except ImportError:
        return False


@pytest.mark.skipif(not _have_cryptography(), reason="cryptography not installed")
def test_derive_key_deterministic_same_call(local_env):
    """Two calls in the same process must return the same key."""
    import secrets_store
    importlib.reload(secrets_store)
    # Reset the warning-once sentinel.
    if hasattr(secrets_store._derive_key, "_legacy_warned"):
        delattr(secrets_store._derive_key, "_legacy_warned")
    k1 = secrets_store._derive_key()
    k2 = secrets_store._derive_key()
    assert k1 == k2


@pytest.mark.skipif(not _have_cryptography(), reason="cryptography not installed")
def test_derive_key_returns_fernet_format(local_env):
    """Output must be 44-byte URL-safe base64 (Fernet key shape)."""
    import secrets_store
    importlib.reload(secrets_store)
    key = secrets_store._derive_key()
    assert isinstance(key, bytes)
    # Fernet keys are 32 raw bytes, base64-urlsafe-encoded → 44 chars.
    assert len(key) == 44
    # Must be valid base64 and reversible.
    import base64
    raw = base64.urlsafe_b64decode(key)
    assert len(raw) == 32


@pytest.mark.skipif(not _have_cryptography(), reason="cryptography not installed")
def test_derive_key_pbkdf2_iterations_at_owasp_minimum(local_env):
    """The iteration count must be ≥ 600_000 per OWASP 2024 SHA256
    recommendation. Lowering this is a security regression."""
    import secrets_store
    # Read the source — the number is a literal in _derive_key.
    src = open(secrets_store.__file__).read()
    # The function uses iterations=600_000 (or higher).
    import re
    m = re.search(r"iterations\s*=\s*(\d[\d_]*)", src)
    assert m, "iterations= literal not found in secrets_store.py"
    iters = int(m.group(1).replace("_", ""))
    assert iters >= 600_000, (
        f"PBKDF2 iterations {iters} below OWASP 2024 SHA256 minimum 600_000"
    )


@pytest.mark.skipif(not _have_cryptography(), reason="cryptography not installed")
def test_derive_key_creates_salt_file_on_first_use(local_env, tmp_path, monkeypatch):
    """First _derive_key call in a fresh install creates a 16-byte
    salt file at ~/.config/huntova/.salt with mode 0600."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import secrets_store
    importlib.reload(secrets_store)
    if hasattr(secrets_store._derive_key, "_legacy_warned"):
        delattr(secrets_store._derive_key, "_legacy_warned")

    salt_path = secrets_store._enc_path().with_name(".salt")
    if salt_path.exists():
        salt_path.unlink()

    secrets_store._derive_key()

    assert salt_path.exists(), "salt file must be created on first derive"
    salt_bytes = salt_path.read_bytes()
    assert len(salt_bytes) == 16, "salt must be 16 bytes"
    # Mode 0600.
    import stat
    mode = stat.S_IMODE(salt_path.stat().st_mode)
    # 0o600 = read+write owner only.
    assert mode == 0o600, f"salt file mode must be 0600, got {oct(mode)}"


@pytest.mark.skipif(not _have_cryptography(), reason="cryptography not installed")
def test_derive_key_reuses_persisted_salt(local_env, tmp_path, monkeypatch):
    """Subsequent calls (in fresh process simulated by reload) must
    read the existing salt file rather than generating a new one."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import secrets_store
    importlib.reload(secrets_store)

    # First call creates salt.
    salt_path = secrets_store._enc_path().with_name(".salt")
    if salt_path.exists():
        salt_path.unlink()
    k1 = secrets_store._derive_key()
    salt1 = salt_path.read_bytes()

    # Reload module — simulates fresh process. Salt file persists.
    importlib.reload(secrets_store)
    if hasattr(secrets_store._derive_key, "_legacy_warned"):
        delattr(secrets_store._derive_key, "_legacy_warned")

    k2 = secrets_store._derive_key()
    salt2 = salt_path.read_bytes()

    assert salt1 == salt2, "salt file must be reused across reloads"
    assert k1 == k2, "key must be deterministic across reloads"


@pytest.mark.skipif(not _have_cryptography(), reason="cryptography not installed")
def test_derive_key_wrong_length_salt_triggers_legacy_fallback(local_env, tmp_path, monkeypatch, capsys):
    """a303 fix: an existing salt file with wrong length must NOT
    rotate to a fresh-random salt (would orphan prior ciphertexts).
    Falls back to legacy public salt instead."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import secrets_store
    importlib.reload(secrets_store)

    salt_path = secrets_store._enc_path().with_name(".salt")
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    # Write a salt with wrong length (5 bytes instead of 16).
    salt_path.write_bytes(b"short")

    # Should not raise, should not rewrite the file.
    if hasattr(secrets_store._derive_key, "_legacy_warned"):
        delattr(secrets_store._derive_key, "_legacy_warned")
    k = secrets_store._derive_key()
    assert isinstance(k, bytes)
    # Salt file unchanged (a303 fix).
    assert salt_path.read_bytes() == b"short"


@pytest.mark.skipif(not _have_cryptography(), reason="cryptography not installed")
def test_derive_key_legacy_warning_only_once_per_process(local_env, tmp_path, monkeypatch, capsys):
    """a303 fix: the legacy-fallback WARN must fire at most once per
    process — even if _derive_key is called 100x."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import secrets_store
    importlib.reload(secrets_store)

    # Force legacy fallback by setting a wrong-length salt.
    salt_path = secrets_store._enc_path().with_name(".salt")
    salt_path.parent.mkdir(parents=True, exist_ok=True)
    salt_path.write_bytes(b"xx")

    # Reset sentinel.
    if hasattr(secrets_store._derive_key, "_legacy_warned"):
        delattr(secrets_store._derive_key, "_legacy_warned")

    # Multiple calls.
    for _ in range(5):
        secrets_store._derive_key()

    err = capsys.readouterr().err
    # Count occurrences of the WARN line.
    warn_count = err.count("[secrets] WARN: salt persist failed")
    assert warn_count <= 1, (
        f"legacy WARN must fire ≤1 time per process, got {warn_count}"
    )
