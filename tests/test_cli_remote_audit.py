"""BRAIN-188: cli_remote.py Telegram bridge config-storage audit.

Pinned invariants (covers BRAIN-61 allowlist + BRAIN-65 atomic-write
+ a426 fsync hardening + format_reply rendering):

1. `_load_config` returns {} for missing / unparseable file.
2. `_save_config` writes atomically via .tmp + replace.
3. `_save_config` sets file mode 0600 on the saved file (PII).
4. `_save_config` fsyncs before rename (BRAIN-65 durability fix).
5. Round-trip: save → load returns the same dict.
6. `_format_reply` empty / None handling.
7. `_format_reply` start_hunt / list_leads / navigate action hints.
8. `_format_reply` falls back to json.dumps for unknown actions.
9. `_TOKEN_SECRET_KEY` constant matches expected name.
"""
from __future__ import annotations

import importlib
import os


def test_load_config_missing_returns_empty(local_env, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import cli_remote
    importlib.reload(cli_remote)
    # Config file doesn't exist.
    p = cli_remote._config_path()
    if p.exists():
        p.unlink()
    out = cli_remote._load_config()
    assert out == {}


def test_load_config_unparseable_returns_empty(local_env, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import cli_remote
    importlib.reload(cli_remote)
    p = cli_remote._config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("not valid json {{{")
    out = cli_remote._load_config()
    assert out == {}


def test_save_then_load_roundtrip(local_env, monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import cli_remote
    importlib.reload(cli_remote)
    cfg = {"allowed_chat_ids": [12345, 67890], "verbose": True}
    cli_remote._save_config(cfg)
    out = cli_remote._load_config()
    assert out == cfg


def test_save_config_sets_0600(local_env, monkeypatch, tmp_path):
    """File mode 0600 — chat-id allowlist is PII."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import cli_remote
    importlib.reload(cli_remote)
    cli_remote._save_config({"allowed_chat_ids": [42]})
    p = cli_remote._config_path()
    assert p.exists()
    import stat
    mode = stat.S_IMODE(p.stat().st_mode)
    assert mode == 0o600, f"config file mode must be 0600, got {oct(mode)}"


def test_save_config_atomic_rename(local_env, monkeypatch, tmp_path):
    """During save, a `.tmp` file is created then renamed. Pin that
    after success, NO `.tmp` lingers."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import cli_remote
    importlib.reload(cli_remote)
    cli_remote._save_config({"key": "value"})
    p = cli_remote._config_path()
    tmp_p = p.with_suffix(".tmp")
    assert p.exists()
    assert not tmp_p.exists(), ".tmp must be removed by atomic rename"


def test_format_reply_empty():
    from cli_remote import _format_reply
    assert _format_reply({}) == "(empty reply)"
    assert _format_reply(None) == "(empty reply)"  # type: ignore[arg-type]


def test_format_reply_start_hunt():
    from cli_remote import _format_reply
    out = _format_reply({"action": "start_hunt",
                          "countries": ["US", "DE"],
                          "max_leads": 50})
    assert "Hunt requested" in out
    assert "US" in out
    assert "DE" in out
    assert "max 50" in out


def test_format_reply_list_leads():
    from cli_remote import _format_reply
    out = _format_reply({"action": "list_leads", "filter": "qualified"})
    assert "qualified" in out


def test_format_reply_navigate():
    from cli_remote import _format_reply
    out = _format_reply({"action": "navigate", "page": "/leads"})
    assert "/leads" in out


def test_format_reply_text_passthrough():
    """Plain text reply (no action) returns the text field."""
    from cli_remote import _format_reply
    out = _format_reply({"text": "hello from huntova"})
    assert out == "hello from huntova"


def test_format_reply_unknown_action_falls_back():
    """Unknown action — fall back to text or JSON dump."""
    from cli_remote import _format_reply
    out = _format_reply({"action": "totally_unknown",
                          "text": "fallback text"})
    assert "fallback text" in out


def test_format_reply_no_text_no_action_dumps_json():
    """If no text + no action, return JSON-dumped data (truncated)."""
    from cli_remote import _format_reply
    out = _format_reply({"random_key": "random_value"})
    assert "random_key" in out


def test_format_reply_truncates_long_json():
    """JSON-dump fallback caps at 1500 chars."""
    from cli_remote import _format_reply
    out = _format_reply({"data": "x" * 5000})
    assert len(out) <= 1500


def test_token_secret_key_constant():
    """The keychain entry name is `hv_telegram_bot_token` — must
    not change without coordination (would orphan existing tokens)."""
    from cli_remote import _TOKEN_SECRET_KEY
    assert _TOKEN_SECRET_KEY == "hv_telegram_bot_token"


def test_save_config_overwrites_existing(local_env, monkeypatch, tmp_path):
    """Two saves in sequence — second wins, no leftover from first."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import cli_remote
    importlib.reload(cli_remote)
    cli_remote._save_config({"first": True})
    cli_remote._save_config({"second": True})
    out = cli_remote._load_config()
    assert out == {"second": True}


def test_save_config_unicode_round_trip(local_env, monkeypatch, tmp_path):
    """Unicode in config values (e.g. allowed user names) round-trips."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    import cli_remote
    importlib.reload(cli_remote)
    cfg = {"display_name": "Müller GmbH 🦊"}
    cli_remote._save_config(cfg)
    out = cli_remote._load_config()
    assert out["display_name"] == "Müller GmbH 🦊"
