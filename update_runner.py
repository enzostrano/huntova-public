"""In-browser one-click update flow for huntova.

Spawns `pipx upgrade huntova` as a background subprocess, streams its
stdout into a job record so the frontend can render live progress, and
exposes helpers the FastAPI routes wire up. Self-restart of the server
happens via `os.execv()` once the upgrade succeeds — see
`schedule_self_restart`.

a324: factored out of server.py so the subprocess machinery lives in
its own module. Uses the safe list-form Popen (NOT shell=True) so the
arguments aren't interpolated into a shell string.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import uuid


_UPGRADE_CMD_PIPX = ("upgrade", "huntova")
_UPGRADE_CMD_PIP = ("install", "--user", "--upgrade",
                    "git+https://github.com/enzostrano/huntova-public.git")

_jobs: dict = {}
_jobs_lock = threading.Lock()


def _resolve_cmd() -> list[str] | None:
    """Build the list-form argv for the upgrade. Prefers pipx; falls back
    to pip if pipx isn't on PATH (e.g. user installed via git+pip)."""
    pipx = shutil.which("pipx")
    if pipx:
        return [pipx, *_UPGRADE_CMD_PIPX]
    pip = shutil.which("pip") or shutil.which("pip3")
    if pip:
        return [pip, *_UPGRADE_CMD_PIP]
    return None


def _run(job_id: str) -> None:
    """Background-thread runner. Streams output line-by-line into the
    job record. Caps captured output at 400 lines to bound memory."""
    cmd = _resolve_cmd()
    if cmd is None:
        with _jobs_lock:
            j = _jobs.get(job_id) or {}
            j.update({"state": "fail",
                      "error": "Neither pipx nor pip found on PATH",
                      "exit_code": -1})
            _jobs[job_id] = j
        return
    try:
        # List-form Popen is execve-style — no shell, no string parsing,
        # no injection surface. Args are baked-in constants above.
        proc = subprocess.Popen(  # noqa: S603 — list-form, hardcoded args
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        with _jobs_lock:
            j = _jobs.get(job_id) or {}
            j["state"] = "running"
            j["pid"] = proc.pid
            j["cmd"] = " ".join(cmd)
            _jobs[job_id] = j
        out_lines: list = []
        for line in proc.stdout or []:
            out_lines.append(line.rstrip("\n"))
            with _jobs_lock:
                j = _jobs.get(job_id) or {}
                j["output"] = out_lines[-400:]
                _jobs[job_id] = j
        proc.wait(timeout=300)
        with _jobs_lock:
            j = _jobs.get(job_id) or {}
            j["exit_code"] = proc.returncode
            j["state"] = "done" if proc.returncode == 0 else "fail"
            if proc.returncode != 0:
                j["error"] = f"upgrade command exited {proc.returncode}"
            _jobs[job_id] = j
    except Exception as exc:
        with _jobs_lock:
            j = _jobs.get(job_id) or {}
            j.update({"state": "fail", "error": str(exc), "exit_code": -1})
            _jobs[job_id] = j


def start_job() -> tuple[str, bool]:
    """Start a new upgrade job (single-flight). Returns (job_id, reused).
    If an upgrade is already running, returns the existing id with
    reused=True instead of spawning a duplicate."""
    with _jobs_lock:
        for jid, j in _jobs.items():
            if j.get("state") in ("queued", "running"):
                return jid, True
        job_id = uuid.uuid4().hex[:12]
        _jobs[job_id] = {"state": "queued", "output": []}
    t = threading.Thread(target=_run, args=(job_id,), daemon=True)
    t.start()
    return job_id, False


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        j = _jobs.get(job_id)
        return dict(j) if j else None


def schedule_self_restart(delay_seconds: float = 1.0) -> None:
    """Replace the running process with a fresh `os.execv` so newly-
    upgraded code on disk is loaded. Schedules via threading.Timer so
    the HTTP response that triggered this can be sent first.

    After execv we ARE the new process — control never returns from
    that call. uvicorn's listening socket is closed by the kernel as
    part of the exec; the new process re-binds to the same port."""

    def _exec_now() -> None:
        try:
            os.execv(sys.executable, [sys.executable] + sys.argv)
        except Exception as exc:
            print(f"[update] execv failed: {exc}")

    t = threading.Timer(delay_seconds, _exec_now)
    t.daemon = True
    t.start()
