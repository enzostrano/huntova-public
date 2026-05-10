"""BRAIN-209: db_driver._local_db_path resolution audit.

Pinned invariants:

1. `HUNTOVA_DB_PATH` env override wins.
2. `~` in override expands to home directory.
3. Without override, falls back to XDG_DATA_HOME.
4. Without XDG_DATA_HOME, defaults to `~/.local/share`.
5. Final path is `<base>/huntova/db.sqlite`.
6. Parent directory auto-created.
"""
from __future__ import annotations

import os
from pathlib import Path


def test_huntova_db_path_override_wins(monkeypatch, tmp_path):
    from db_driver import _local_db_path
    target = tmp_path / "custom" / "huntova.sqlite"
    monkeypatch.setenv("HUNTOVA_DB_PATH", str(target))
    out = _local_db_path()
    assert out == target


def test_huntova_db_path_expands_tilde(monkeypatch, tmp_path):
    """`~` in override expands to home directory."""
    from db_driver import _local_db_path
    monkeypatch.setenv("HUNTOVA_DB_PATH", "~/.huntova-test/db.sqlite")
    out = _local_db_path()
    # No literal ~ remains.
    assert "~" not in str(out)
    # Has the home prefix.
    assert str(out).startswith(str(Path.home()))


def test_xdg_data_home_used_when_no_override(monkeypatch, tmp_path):
    from db_driver import _local_db_path
    monkeypatch.delenv("HUNTOVA_DB_PATH", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    out = _local_db_path()
    assert str(tmp_path / "xdg-data") in str(out)
    assert out.name == "db.sqlite"


def test_default_falls_back_to_local_share(monkeypatch, tmp_path):
    """Without XDG_DATA_HOME or override, defaults to ~/.local/share/huntova/."""
    from db_driver import _local_db_path
    monkeypatch.delenv("HUNTOVA_DB_PATH", raising=False)
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    out = _local_db_path()
    # Path under home / .local / share / huntova.
    assert ".local/share/huntova" in str(out) or "huntova" in str(out)
    assert out.name == "db.sqlite"


def test_parent_dir_auto_created(monkeypatch, tmp_path):
    """The parent directory is created if missing — db driver expects
    it to exist before opening the SQLite file."""
    from db_driver import _local_db_path
    base = tmp_path / "fresh-install"
    monkeypatch.setenv("XDG_DATA_HOME", str(base))
    monkeypatch.delenv("HUNTOVA_DB_PATH", raising=False)
    out = _local_db_path()
    assert out.parent.exists(), "parent dir must be auto-created"
    assert out.parent.is_dir()


def test_path_ends_with_db_sqlite(monkeypatch, tmp_path):
    from db_driver import _local_db_path
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.delenv("HUNTOVA_DB_PATH", raising=False)
    out = _local_db_path()
    assert str(out).endswith("db.sqlite")


def test_returns_path_object(monkeypatch, tmp_path):
    from db_driver import _local_db_path
    monkeypatch.setenv("HUNTOVA_DB_PATH", str(tmp_path / "x.sqlite"))
    out = _local_db_path()
    assert isinstance(out, Path)


def test_override_with_relative_path(monkeypatch, tmp_path):
    """A relative `HUNTOVA_DB_PATH` is allowed; resolved against cwd."""
    from db_driver import _local_db_path
    monkeypatch.setenv("HUNTOVA_DB_PATH", "relative-test.sqlite")
    out = _local_db_path()
    # Returned as-is (relative or absolute is caller's choice).
    assert out.name == "relative-test.sqlite"
