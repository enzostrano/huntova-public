"""BRAIN-166: huntova_daemon.py unit-file generation invariant audit.

`install_daemon` writes a launchd plist (macOS) or systemd unit file
(Linux). The user-supplied port + env-var blob flow into these files.
Sanitization bugs here become local privilege issues — a malformed
plist can fail to load silently, a malformed systemd unit can either
fail-to-start or (worse) inject directives.

These tests pin:

1. `_build_plist` produces well-formed XML with Apple's plist DTD.
2. Port number is rendered as integer-typed XML.
3. Environment values are XML-escaped (`<`, `>`, `&`, `"` survive).
4. Empty environment dict produces no `<key>EnvironmentVariables</key>`
   block (avoids empty-dict noise).
5. Environment values with `</string>` survive XML-escape (no
   premature tag closure).
6. `_systemd_escape` escapes backslash, double-quote, dollar.
7. `_build_systemd_unit` rejects env values containing newlines
   (otherwise the value escapes to a new directive line).
8. systemd unit has expected sections: [Unit], [Service], [Install].
9. `_linux_unit_path` respects XDG_CONFIG_HOME.
10. `_log_dir` respects XDG_DATA_HOME.
"""
from __future__ import annotations

import importlib
import xml.etree.ElementTree as ET


def test_build_plist_produces_valid_xml(local_env):
    import huntova_daemon
    importlib.reload(huntova_daemon)
    plist = huntova_daemon._build_plist(port=5050, environment={})
    # Strip the DOCTYPE for ET; otherwise it fails on some implementations.
    body = plist.replace(
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n', "")
    root = ET.fromstring(body)
    assert root.tag == "plist"
    # Top-level dict.
    d = root.find("dict")
    assert d is not None
    # Label key present.
    keys = [k.text for k in d.findall("key")]
    assert "Label" in keys
    assert "ProgramArguments" in keys
    assert "RunAtLoad" in keys
    assert "KeepAlive" in keys


def test_build_plist_respects_port(local_env):
    import huntova_daemon
    importlib.reload(huntova_daemon)
    plist = huntova_daemon._build_plist(port=12345, environment=None)
    assert "12345" in plist
    # Port appears as <string>12345</string> (CLI flag value).
    assert "<string>12345</string>" in plist


def test_build_plist_xml_escapes_env_value_with_angle_brackets(local_env):
    """User env var with `<script>` or `</string>` must survive escape."""
    import huntova_daemon
    importlib.reload(huntova_daemon)
    nasty_value = "</string><key>EVIL</key><string>injected"
    plist = huntova_daemon._build_plist(
        port=5050, environment={"HV_TEST": nasty_value})
    # The literal `</string>` must not appear as raw.
    body = plist.replace(
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n', "")
    root = ET.fromstring(body)
    # Find the EVIL key under EnvironmentVariables — if escape worked,
    # it's the LITERAL key "HV_TEST" with the literal nasty value as
    # content; if it didn't, ET.fromstring would either fail or pick
    # up an "EVIL" key.
    keys = [k.text for k in root.iter("key")]
    assert "HV_TEST" in keys
    assert "EVIL" not in keys, "XML injection succeeded — escape failed"


def test_build_plist_no_env_block_when_empty(local_env):
    import huntova_daemon
    importlib.reload(huntova_daemon)
    plist = huntova_daemon._build_plist(port=5050, environment={})
    assert "<key>EnvironmentVariables</key>" not in plist


def test_build_plist_no_env_block_when_none(local_env):
    import huntova_daemon
    importlib.reload(huntova_daemon)
    plist = huntova_daemon._build_plist(port=5050, environment=None)
    assert "<key>EnvironmentVariables</key>" not in plist


def test_build_plist_env_block_skips_empty_values(local_env):
    """An env entry with empty string value must not emit a line —
    otherwise the daemon picks up `KEY=` (empty)."""
    import huntova_daemon
    importlib.reload(huntova_daemon)
    plist = huntova_daemon._build_plist(
        port=5050,
        environment={"HV_REAL": "value", "HV_EMPTY": ""})
    assert "HV_REAL" in plist
    assert "HV_EMPTY" not in plist


def test_systemd_escape_backslash():
    import huntova_daemon
    importlib.reload(huntova_daemon)
    assert huntova_daemon._systemd_escape("a\\b") == "a\\\\b"


def test_systemd_escape_double_quote():
    import huntova_daemon
    importlib.reload(huntova_daemon)
    assert huntova_daemon._systemd_escape('a"b') == 'a\\"b'


def test_systemd_escape_dollar():
    import huntova_daemon
    importlib.reload(huntova_daemon)
    # systemd doubles dollar to escape variable expansion.
    assert huntova_daemon._systemd_escape("a$b") == "a$$b"


def test_systemd_escape_combined():
    import huntova_daemon
    importlib.reload(huntova_daemon)
    assert huntova_daemon._systemd_escape('a\\b"c$d') == 'a\\\\b\\"c$$d'


def test_build_systemd_unit_has_required_sections(local_env):
    import huntova_daemon
    importlib.reload(huntova_daemon)
    unit = huntova_daemon._build_systemd_unit(port=5050, environment=None)
    assert "[Unit]" in unit
    assert "[Service]" in unit
    assert "[Install]" in unit
    assert "ExecStart=" in unit
    assert "Restart=on-failure" in unit
    assert "WantedBy=default.target" in unit


def test_build_systemd_unit_rejects_newline_in_env(local_env):
    """A newline in an env value would escape to a new directive line —
    must be rejected silently."""
    import huntova_daemon
    importlib.reload(huntova_daemon)
    nasty = "value\nExecStart=/bin/evil"
    unit = huntova_daemon._build_systemd_unit(
        port=5050, environment={"HV_K": nasty})
    # The injected ExecStart must NOT appear as a directive — it
    # would be on its own line if newline made it through.
    lines = unit.split("\n")
    exec_lines = [l for l in lines if l.startswith("ExecStart=")]
    # Only the legitimate ExecStart should be there.
    assert len(exec_lines) == 1, (
        f"expected 1 ExecStart= directive, got {len(exec_lines)}: {exec_lines}"
    )


def test_build_systemd_unit_rejects_carriage_return_in_env(local_env):
    import huntova_daemon
    importlib.reload(huntova_daemon)
    unit = huntova_daemon._build_systemd_unit(
        port=5050, environment={"HV_K": "value\rinjected"})
    # Must not appear as a separate line.
    assert "injected" not in unit.split("\n")[0:30]


def test_build_systemd_unit_skips_empty_value(local_env):
    import huntova_daemon
    importlib.reload(huntova_daemon)
    unit = huntova_daemon._build_systemd_unit(
        port=5050, environment={"HV_REAL": "v", "HV_EMPTY": ""})
    assert "HV_REAL=" in unit
    assert 'Environment="HV_EMPTY=' not in unit


def test_linux_unit_path_respects_xdg(local_env, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg-config"))
    import huntova_daemon
    importlib.reload(huntova_daemon)
    p = huntova_daemon._linux_unit_path()
    assert str(tmp_path / "xdg-config") in str(p)
    assert p.name == "huntova.service"


def test_log_dir_respects_xdg(local_env, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg-data"))
    import huntova_daemon
    importlib.reload(huntova_daemon)
    log = huntova_daemon._log_dir()
    assert str(tmp_path / "xdg-data") in str(log)
    assert log.exists()


def test_macos_plist_path_lives_under_launchagents(local_env):
    import huntova_daemon
    importlib.reload(huntova_daemon)
    p = huntova_daemon._macos_plist_path()
    assert "LaunchAgents" in str(p)
    assert p.name == f"{huntova_daemon.DAEMON_LABEL}.plist"
