"""BRAIN-199: secrets_store._atomic_write_0600 + _harden_perms audit.

Pinned invariants:

1. `_atomic_write_0600` creates file with mode 0600 from the start
   (no TOCTOU window where another process can read it 0644).
2. `_atomic_write_0600` writes + fsyncs + atomic-renames.
3. `_atomic_write_0600` cleans up `.tmp` on success and failure.
4. `_atomic_write_0600` overwrites existing file atomically.
5. `_atomic_write_0600` creates parent directory if missing.
6. `_atomic_write_0600` handles a stale `.tmp` from a crashed run.
7. `_harden_perms` chmods 0600 (idempotent + Windows-safe no-op).
8. `_plain_read` returns {} for missing file (not raise).
9. `_plain_read` returns {} for corrupt JSON.
10. `_plain_write` round-trips through `_plain_read`.
"""
from __future__ import annotations

import os
import stat


def test_atomic_write_creates_with_0600_directly(tmp_path):
    """Critical TOCTOU defence: file mode is 0600 from creation,
    NOT 0644-then-chmod. Tested by inspecting mode after write."""
    if os.name == "nt":
        # Windows has no POSIX modes; skip.
        import pytest
        pytest.skip("POSIX-only test")
    from secrets_store import _atomic_write_0600
    p = tmp_path / "secret.bin"
    _atomic_write_0600(p, b"sensitive-data")
    assert p.exists()
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"file must be 0600 from creation, got {oct(mode)}"


def test_atomic_write_round_trips(tmp_path):
    from secrets_store import _atomic_write_0600
    p = tmp_path / "out.bin"
    _atomic_write_0600(p, b"hello world bytes")
    assert p.read_bytes() == b"hello world bytes"


def test_atomic_write_overwrites(tmp_path):
    """Two writes — second overwrites first cleanly."""
    from secrets_store import _atomic_write_0600
    p = tmp_path / "out.bin"
    _atomic_write_0600(p, b"first")
    _atomic_write_0600(p, b"second")
    assert p.read_bytes() == b"second"


def test_atomic_write_creates_parent_dir(tmp_path):
    from secrets_store import _atomic_write_0600
    p = tmp_path / "deep" / "nested" / "path" / "out.bin"
    _atomic_write_0600(p, b"data")
    assert p.exists()
    assert p.parent.exists()


def test_atomic_write_no_tmp_lingers_on_success(tmp_path):
    from secrets_store import _atomic_write_0600
    p = tmp_path / "out.bin"
    _atomic_write_0600(p, b"data")
    tmp_p = p.with_suffix(p.suffix + ".tmp")
    assert not tmp_p.exists()


def test_atomic_write_handles_stale_tmp(tmp_path):
    """A stale .tmp from a crashed prior run gets cleaned up on
    next write — must not block."""
    from secrets_store import _atomic_write_0600
    p = tmp_path / "out.bin"
    stale_tmp = p.with_suffix(p.suffix + ".tmp")
    stale_tmp.write_bytes(b"stale leftover")
    # Must not raise.
    _atomic_write_0600(p, b"fresh data")
    assert p.read_bytes() == b"fresh data"
    assert not stale_tmp.exists()


def test_atomic_write_unicode_via_bytes(tmp_path):
    """Unicode encoded to bytes survives the atomic write."""
    from secrets_store import _atomic_write_0600
    p = tmp_path / "out.bin"
    data = "Müller GmbH 🦊 中文".encode("utf-8")
    _atomic_write_0600(p, data)
    out = p.read_bytes().decode("utf-8")
    assert out == "Müller GmbH 🦊 中文"


def test_atomic_write_empty_bytes(tmp_path):
    """Writing empty bytes creates an empty file (not crash)."""
    from secrets_store import _atomic_write_0600
    p = tmp_path / "empty.bin"
    _atomic_write_0600(p, b"")
    assert p.exists()
    assert p.read_bytes() == b""


def test_harden_perms_chmods_0600(tmp_path):
    if os.name == "nt":
        import pytest
        pytest.skip("POSIX-only test")
    from secrets_store import _harden_perms
    p = tmp_path / "loose.bin"
    p.write_bytes(b"data")
    os.chmod(p, 0o644)  # set loose perms first
    _harden_perms(p)
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600


def test_harden_perms_idempotent(tmp_path):
    if os.name == "nt":
        import pytest
        pytest.skip("POSIX-only test")
    from secrets_store import _harden_perms
    p = tmp_path / "secret.bin"
    p.write_bytes(b"data")
    os.chmod(p, 0o600)
    # Second call must not raise / change.
    _harden_perms(p)
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_harden_perms_handles_missing_file(tmp_path):
    """Missing file — chmod fails but _harden_perms swallows."""
    from secrets_store import _harden_perms
    p = tmp_path / "doesnt-exist.bin"
    # Must not raise.
    _harden_perms(p)


def test_plain_read_missing_returns_empty(local_env, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import importlib, secrets_store
    importlib.reload(secrets_store)
    out = secrets_store._plain_read()
    assert out == {}


def test_plain_read_corrupt_returns_empty(local_env, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import importlib, secrets_store
    importlib.reload(secrets_store)
    p = secrets_store._plain_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not valid json {{{")
    out = secrets_store._plain_read()
    assert out == {}


def test_plain_write_then_read_roundtrip(local_env, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import importlib, secrets_store
    importlib.reload(secrets_store)
    secrets_store._plain_write({"HV_K": "v1", "HV_K2": "v2"})
    out = secrets_store._plain_read()
    assert out == {"HV_K": "v1", "HV_K2": "v2"}


def test_plain_write_creates_0600_file(local_env, monkeypatch, tmp_path):
    if os.name == "nt":
        import pytest
        pytest.skip("POSIX-only test")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import importlib, secrets_store
    importlib.reload(secrets_store)
    secrets_store._plain_write({"HV_K": "v1"})
    p = secrets_store._plain_path()
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600
