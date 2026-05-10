"""Shared pytest fixtures for Huntova local-mode tests.

Each test runs in a clean APP_MODE=local environment with an isolated
SQLite database in tmp_path so parallel runs don't collide.
"""
from __future__ import annotations

import os
import sys
import importlib
from pathlib import Path

import pytest

# Make the repo root importable when pytest is invoked from anywhere.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


@pytest.fixture
def local_env(tmp_path, monkeypatch):
    """Sandbox each test in APP_MODE=local with a fresh SQLite path."""
    db_path = tmp_path / "huntova.sqlite"
    monkeypatch.setenv("APP_MODE", "local")
    monkeypatch.setenv("HUNTOVA_DB_PATH", str(db_path))
    monkeypatch.setenv("HV_GEMINI_KEY", "test-gemini-key")
    monkeypatch.delenv("DATABASE_URL", raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    # Reload modules that resolve env at import time so each test
    # sees its own sandbox. Order matters — db depends on db_driver.
    for mod_name in ("runtime", "db_driver", "db", "policy"):
        if mod_name in sys.modules:
            importlib.reload(sys.modules[mod_name])
    yield {
        "db_path": db_path,
        "config_dir": tmp_path / "config",
        "data_dir": tmp_path / "data",
    }
