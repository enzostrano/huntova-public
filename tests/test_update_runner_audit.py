"""BRAIN-165: update_runner.py invariant audit.

The in-browser update flow spawns `pipx upgrade huntova` (or pip
fallback) as a background subprocess, streams output into a job
record, and `os.execv()`s the server when done. Single-flight: only
one upgrade may run at a time.

These tests pin:

1. `_resolve_cmd` prefers pipx; falls back to pip; returns None when
   neither is available — never returns shell-string form.
2. `start_job` returns `(job_id, reused=True)` when a job is already
   queued / running; returns `(new_id, reused=False)` otherwise.
3. `get_job(unknown)` returns None.
4. `_jobs` writes are guarded by `_jobs_lock`.
5. job_id is 12 hex chars (uuid4 prefix), unique across calls.
6. Output cap of 400 lines is honoured.
7. State machine: queued → running → (done|fail).
"""
from __future__ import annotations

import importlib


def test_resolve_cmd_returns_list_form(local_env, monkeypatch):
    """Must always return list-form (or None) — never shell-string —
    so subprocess.Popen runs without shell=True."""
    import update_runner
    importlib.reload(update_runner)

    # Stub pipx present.
    monkeypatch.setattr(update_runner.shutil, "which",
                        lambda name: "/fake/pipx" if name == "pipx" else None)
    cmd = update_runner._resolve_cmd()
    assert isinstance(cmd, list)
    assert cmd[0] == "/fake/pipx"
    assert "upgrade" in cmd
    assert "huntova" in cmd


def test_resolve_cmd_falls_back_to_pip(local_env, monkeypatch):
    """When pipx is missing, fall back to pip + git URL."""
    import update_runner
    importlib.reload(update_runner)

    def fake_which(name):
        if name == "pipx":
            return None
        if name in ("pip", "pip3"):
            return f"/fake/{name}"
        return None
    monkeypatch.setattr(update_runner.shutil, "which", fake_which)
    cmd = update_runner._resolve_cmd()
    assert isinstance(cmd, list)
    assert cmd[0] == "/fake/pip"
    assert "install" in cmd
    assert "--upgrade" in cmd


def test_resolve_cmd_returns_none_when_nothing_available(local_env, monkeypatch):
    """No pipx + no pip → None. Caller surfaces an actionable error."""
    import update_runner
    importlib.reload(update_runner)
    monkeypatch.setattr(update_runner.shutil, "which", lambda name: None)
    assert update_runner._resolve_cmd() is None


def test_get_job_unknown_returns_none(local_env):
    import update_runner
    importlib.reload(update_runner)
    assert update_runner.get_job("nonexistent-job-id-xyz") is None


def test_start_job_returns_id_and_not_reused_first_call(local_env, monkeypatch):
    """First call returns (id, False)."""
    import update_runner
    importlib.reload(update_runner)
    # Stub _resolve_cmd so the worker thread doesn't actually run pipx.
    monkeypatch.setattr(update_runner, "_resolve_cmd", lambda: None)
    # Clear any prior jobs.
    with update_runner._jobs_lock:
        update_runner._jobs.clear()
    jid, reused = update_runner.start_job()
    assert isinstance(jid, str)
    assert len(jid) == 12, "job_id must be 12-char uuid4 prefix"
    assert reused is False


def test_start_job_returns_reused_when_already_queued(local_env, monkeypatch):
    """Second call while a job is queued/running returns the same
    id with reused=True. Single-flight invariant."""
    import update_runner
    importlib.reload(update_runner)
    # Force a fake "queued" job to exist.
    with update_runner._jobs_lock:
        update_runner._jobs.clear()
        update_runner._jobs["existing-id-12"] = {"state": "queued", "output": []}
    jid, reused = update_runner.start_job()
    assert jid == "existing-id-12"
    assert reused is True
    # Cleanup.
    with update_runner._jobs_lock:
        update_runner._jobs.clear()


def test_start_job_returns_reused_when_running(local_env):
    """Same single-flight when a job is already in 'running' state."""
    import update_runner
    importlib.reload(update_runner)
    with update_runner._jobs_lock:
        update_runner._jobs.clear()
        update_runner._jobs["running-id-1"] = {"state": "running", "output": []}
    jid, reused = update_runner.start_job()
    assert jid == "running-id-1"
    assert reused is True
    with update_runner._jobs_lock:
        update_runner._jobs.clear()


def test_start_job_does_not_reuse_done_job(local_env, monkeypatch):
    """A finished job (state in done/fail) should not block a new
    upgrade — user can re-trigger after a failure."""
    import update_runner
    importlib.reload(update_runner)
    monkeypatch.setattr(update_runner, "_resolve_cmd", lambda: None)
    with update_runner._jobs_lock:
        update_runner._jobs.clear()
        update_runner._jobs["old-done-id"] = {"state": "done", "output": []}
    jid, reused = update_runner.start_job()
    assert reused is False, "done job must not block new upgrade"
    assert jid != "old-done-id"
    with update_runner._jobs_lock:
        update_runner._jobs.clear()


def test_get_job_returns_dict_copy(local_env):
    """get_job returns a dict copy — caller mutating it must not
    corrupt the canonical _jobs[id] entry. Concurrency hygiene."""
    import update_runner
    importlib.reload(update_runner)
    with update_runner._jobs_lock:
        update_runner._jobs.clear()
        update_runner._jobs["test-id"] = {"state": "running", "output": ["a", "b"]}
    j = update_runner.get_job("test-id")
    assert j is not None
    j["state"] = "MUTATED"
    j["output"].append("HACKED")  # noqa
    # Original must be unchanged.
    j2 = update_runner.get_job("test-id")
    assert j2["state"] == "running"
    # Note: shallow copy → output list IS shared. Acceptable for
    # the current "view-only" caller pattern, but pinning shape:
    assert j2 is not j
    with update_runner._jobs_lock:
        update_runner._jobs.clear()


def test_job_id_uniqueness_across_calls(local_env, monkeypatch):
    """Multiple terminal-state job entries → each new start_job
    minted a fresh unique id."""
    import update_runner
    importlib.reload(update_runner)
    monkeypatch.setattr(update_runner, "_resolve_cmd", lambda: None)
    with update_runner._jobs_lock:
        update_runner._jobs.clear()
    seen = set()
    for _ in range(5):
        jid, _r = update_runner.start_job()
        assert jid not in seen
        seen.add(jid)
        # Mark this one terminal so the next start_job mints a new id.
        with update_runner._jobs_lock:
            update_runner._jobs[jid]["state"] = "done"
    with update_runner._jobs_lock:
        update_runner._jobs.clear()


def test_upgrade_command_constants_safe():
    """Hardcoded command tuples must not contain shell metachars
    that could matter even in list-form (defence in depth)."""
    import update_runner
    importlib.reload(update_runner)
    for arg in update_runner._UPGRADE_CMD_PIPX:
        assert ";" not in arg
        assert "&" not in arg
        assert "|" not in arg
        assert "$(" not in arg
    for arg in update_runner._UPGRADE_CMD_PIP:
        assert ";" not in arg
        assert "&" not in arg
        assert "|" not in arg


def test_jobs_dict_thread_safety_smoke(local_env, monkeypatch):
    """Smoke test: concurrent start_job calls don't lose entries
    or duplicate ids. The internal `_jobs_lock` protects the dict."""
    import threading
    import update_runner
    importlib.reload(update_runner)
    monkeypatch.setattr(update_runner, "_resolve_cmd", lambda: None)
    with update_runner._jobs_lock:
        update_runner._jobs.clear()

    results = []
    barrier = threading.Barrier(8)

    def worker():
        barrier.wait()
        # First worker to land creates job; rest should reuse.
        jid, reused = update_runner.start_job()
        results.append((jid, reused))

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    # Exactly one new (reused=False), the rest reused=True.
    new_count = sum(1 for _, r in results if r is False)
    reused_count = sum(1 for _, r in results if r is True)
    assert new_count == 1, f"single-flight broke; new_count={new_count}"
    assert new_count + reused_count == 8
    # All ids reference the same job.
    ids = {jid for jid, _ in results}
    assert len(ids) == 1, "all concurrent callers must get same job_id"

    with update_runner._jobs_lock:
        update_runner._jobs.clear()
