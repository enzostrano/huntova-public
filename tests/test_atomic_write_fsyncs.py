"""Regression test for BRAIN-52 (a413): _atomic_write must fsync the
tmp file before the atomic rename so a crash between rename + disk
flush can't leave the renamed file pointing to unwritten inode
blocks.

Per GPT-5.4 audit on db.py / journal-replay class.
"""
from __future__ import annotations
import inspect


def test_atomic_write_fsyncs_before_rename():
    import app
    src = inspect.getsource(app._atomic_write)
    # Must call fsync somewhere
    assert "fsync" in src, (
        "BRAIN-52 regression: _atomic_write must fsync the tmp file "
        "before os.replace. POSIX atomic-rename only guarantees the "
        "directory entry is atomic — NOT that the inode's data blocks "
        "are persisted. Standard durable-write recipe."
    )
    # fsync must come BEFORE os.replace
    fsync_idx = src.find("fsync")
    replace_idx = src.find("os.replace")
    assert fsync_idx < replace_idx, (
        "BRAIN-52 regression: fsync must run BEFORE os.replace, not "
        "after. Otherwise the rename can succeed before the data is "
        "durably on disk."
    )


def test_atomic_write_round_trip(tmp_path):
    """Behavioural sanity: small write+read round-trip works."""
    from app import _atomic_write
    p = str(tmp_path / "x.json")
    _atomic_write(p, {"k": "v"})
    import json
    with open(p) as f:
        assert json.load(f) == {"k": "v"}
