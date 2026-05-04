"""Regression test for BRAIN-51 (a412): /api/update/run + /api/update/restart
must be local-mode only. Pre-fix any signed-in user in cloud mode could
trigger pipx upgrade on the production server (and execv it).

Per GPT-5.4 audit on update-flow command-injection / unsafe-self-update.
"""
from __future__ import annotations
import inspect


def test_update_run_gates_on_local_mode():
    from server import api_update_run
    src = inspect.getsource(api_update_run)
    assert 'CAPABILITIES' in src and '"local"' in src, (
        "BRAIN-51 regression: /api/update/run must check "
        "CAPABILITIES.mode == 'local' before kicking off pipx upgrade. "
        "Cloud mode users could otherwise trigger upgrades on the "
        "production server."
    )


def test_update_restart_gates_on_local_mode():
    from server import api_update_restart
    src = inspect.getsource(api_update_restart)
    assert 'CAPABILITIES' in src and '"local"' in src, (
        "BRAIN-51 regression: /api/update/restart must check "
        "CAPABILITIES.mode == 'local' before scheduling execv. "
        "Cloud-mode restart kills every other user mid-request."
    )


def test_update_runner_uses_list_form_subprocess():
    """Defence-in-depth: the update_runner module must use list-form
    Popen (no shell=True) and hardcoded command tuples — not
    user/env-influenced strings."""
    import update_runner
    src = inspect.getsource(update_runner)
    # No shell=True in actual subprocess call (docstring mention is fine)
    # Look for the bug pattern: shell=True passed to Popen kwargs.
    assert 'subprocess.Popen' in src
    # If shell=True appears, it must NOT be on a Popen line — but
    # simplest invariant: search for the bad pattern as a kwarg.
    bad_pattern = 'Popen(' in src and ', shell=True' in src
    assert not bad_pattern, (
        "update_runner must not pass shell=True to Popen — command "
        "injection risk."
    )
    # Hardcoded tuples
    assert '_UPGRADE_CMD_PIPX' in src, "expected hardcoded pipx command tuple"
    assert '_UPGRADE_CMD_PIP' in src, "expected hardcoded pip fallback tuple"
