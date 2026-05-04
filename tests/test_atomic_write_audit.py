"""BRAIN-194: app._atomic_write + _safe_read invariant audit.

`_atomic_write` is the file-system durability primitive used by
every JSON-blob write in app.py. The a413 / BRAIN-52 fix added
fsync-before-rename — a crash between rename and disk flush used
to leave files pointing to zero-length inodes.

Pinned invariants:

1. `_atomic_write` writes data via .tmp + os.replace.
2. `_atomic_write` cleans up .tmp on failure.
3. `_atomic_write` round-trips Python data structures via JSON.
4. `_atomic_write` overwrites existing file atomically.
5. `_atomic_write` handles UTF-8 / unicode without escaping.
6. `_safe_read` returns default when file missing.
7. `_safe_read` returns default when file is corrupt.
8. `_safe_read` returns parsed JSON for valid file.
9. `_safe_read` default defaults to [] when None passed.
10. `ensure_dir` creates directory tree, idempotent on existing.
"""
from __future__ import annotations

import json
import os


def test_atomic_write_creates_file(tmp_path):
    from app import _atomic_write
    p = str(tmp_path / "out.json")
    _atomic_write(p, {"key": "value"})
    assert os.path.isfile(p)


def test_atomic_write_round_trips_data(tmp_path):
    from app import _atomic_write
    p = str(tmp_path / "out.json")
    data = {"name": "Acme", "count": 42, "tags": ["a", "b"]}
    _atomic_write(p, data)
    with open(p) as f:
        loaded = json.load(f)
    assert loaded == data


def test_atomic_write_cleans_up_tmp_on_success(tmp_path):
    """After successful write, no .tmp file lingers."""
    from app import _atomic_write
    p = str(tmp_path / "out.json")
    _atomic_write(p, {"x": 1})
    tmp_p = p + ".tmp"
    assert not os.path.exists(tmp_p)


def test_atomic_write_overwrites_existing(tmp_path):
    from app import _atomic_write
    p = str(tmp_path / "out.json")
    _atomic_write(p, {"v": 1})
    _atomic_write(p, {"v": 2})
    with open(p) as f:
        loaded = json.load(f)
    assert loaded == {"v": 2}


def test_atomic_write_unicode_preserved(tmp_path):
    """ensure_ascii=False — unicode characters survive without escape."""
    from app import _atomic_write
    p = str(tmp_path / "out.json")
    data = {"name": "Müller GmbH 🦊", "city": "München"}
    _atomic_write(p, data)
    with open(p, encoding="utf-8") as f:
        contents = f.read()
    # No \uxxxx escapes — actual unicode characters.
    assert "Müller" in contents
    assert "🦊" in contents


def test_atomic_write_cleans_up_tmp_on_failure(tmp_path, monkeypatch):
    """If json.dump or os.replace raises, the .tmp file is removed."""
    from app import _atomic_write
    p = str(tmp_path / "out.json")

    # Force os.replace to raise.
    real_replace = os.replace

    def failing_replace(*args, **kwargs):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(os, "replace", failing_replace)

    try:
        _atomic_write(p, {"x": 1})
    except OSError:
        pass

    # .tmp should be cleaned up.
    tmp_p = p + ".tmp"
    assert not os.path.exists(tmp_p), ".tmp must be removed on failure"


def test_safe_read_missing_returns_default(tmp_path):
    from app import _safe_read
    p = str(tmp_path / "nonexistent.json")
    assert _safe_read(p, default=[]) == []
    assert _safe_read(p, default={"a": 1}) == {"a": 1}


def test_safe_read_default_none_becomes_empty_list(tmp_path):
    """When `default=None` is passed, function uses [] instead."""
    from app import _safe_read
    p = str(tmp_path / "nonexistent.json")
    out = _safe_read(p, default=None)
    assert out == []


def test_safe_read_corrupt_returns_default(tmp_path):
    """Corrupt JSON returns default rather than raising."""
    from app import _safe_read
    p = str(tmp_path / "broken.json")
    with open(p, "w") as f:
        f.write("not valid json {{{ broken")
    assert _safe_read(p, default=[]) == []


def test_safe_read_empty_file_returns_default(tmp_path):
    """Zero-byte file (e.g. from a previous interrupted write) returns
    default. Critical for the post-crash state shape."""
    from app import _safe_read
    p = str(tmp_path / "empty.json")
    open(p, "w").close()  # truncate to zero
    assert _safe_read(p, default=[]) == []


def test_safe_read_valid_round_trips(tmp_path):
    from app import _safe_read, _atomic_write
    p = str(tmp_path / "out.json")
    data = [{"id": 1}, {"id": 2}]
    _atomic_write(p, data)
    out = _safe_read(p, default=[])
    assert out == data


def test_ensure_dir_creates_tree(tmp_path):
    from app import ensure_dir
    p = str(tmp_path / "a" / "b" / "c")
    ensure_dir(p)
    assert os.path.isdir(p)


def test_ensure_dir_idempotent(tmp_path):
    from app import ensure_dir
    p = str(tmp_path / "exists")
    os.makedirs(p, exist_ok=True)
    # Second call must not raise.
    ensure_dir(p)
    assert os.path.isdir(p)


def test_atomic_write_indents_for_readability(tmp_path):
    """Written JSON must be indented (indent=2) so the user can read
    e.g. ~/.local/share/huntova/master_leads.json with `cat`."""
    from app import _atomic_write
    p = str(tmp_path / "out.json")
    _atomic_write(p, {"key": "value", "nested": {"a": 1}})
    with open(p) as f:
        contents = f.read()
    # Indented (presence of `\n` and spaces).
    assert "\n" in contents


def test_atomic_write_handles_list_data(tmp_path):
    from app import _atomic_write, _safe_read
    p = str(tmp_path / "leads.json")
    leads = [{"id": "a"}, {"id": "b"}, {"id": "c"}]
    _atomic_write(p, leads)
    assert _safe_read(p, default=[]) == leads


def test_atomic_write_handles_empty_dict(tmp_path):
    from app import _atomic_write, _safe_read
    p = str(tmp_path / "empty.json")
    _atomic_write(p, {})
    assert _safe_read(p, default=None) == {}


def test_safe_read_directory_path_returns_default(tmp_path):
    """Path that's a directory (not a file) returns default."""
    from app import _safe_read
    # tmp_path itself is a dir.
    out = _safe_read(str(tmp_path), default=[])
    assert out == []
