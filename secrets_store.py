"""
Local secret storage for Huntova CLI (BYOK API keys).

Two-tier resolution:
1. OS keychain via the `keyring` package (macOS Keychain, Windows
   Credential Manager, Linux Secret Service / kwallet) — preferred.
2. Encrypted-file fallback at ~/.config/huntova/secrets.enc using
   Fernet symmetric encryption with a key derived from machine-stable
   identifiers (so the file is non-portable — copy it to another
   machine and it can't be decrypted).

Both `keyring` and `cryptography` are optional deps. If neither is
installed the store falls back to plaintext at the same path with a
0600 permission lock and a stern warning. That last fallback exists so
the CLI never silently fails to start; users with no security stack at
all still get something usable, just clearly less safe.

Public API:
    set_secret(name, value) -> None
    get_secret(name) -> str | None
    delete_secret(name) -> None
    list_secret_names() -> list[str]

Names by convention: HV_GEMINI_KEY, HV_ANTHROPIC_KEY, HV_OPENAI_KEY.
"""
from __future__ import annotations

import json
import os
import platform
from pathlib import Path
from typing import Any

_APP_NAME = "huntova"
_INDEX_KEY = "__hv_secret_index__"


def _config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    p = Path(base) / "huntova"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _enc_path() -> Path:
    return _config_dir() / "secrets.enc"


def _plain_path() -> Path:
    return _config_dir() / "secrets.json"


# ── Keyring backend (preferred) ─────────────────────────────────────


def _try_keyring() -> Any | None:
    try:
        import keyring  # type: ignore[import-not-found]
        # keyring may install but have no usable backend (e.g.
        # headless Linux with no Secret Service). The get_keyring()
        # call returns a real backend; if it's the FailKeyring we
        # don't want to use it.
        kr_class = type(keyring.get_keyring()).__name__
        if "Fail" in kr_class:
            return None
        return keyring
    except Exception:
        return None


def _kr_index_read(kr: Any) -> list[str]:
    raw = kr.get_password(_APP_NAME, _INDEX_KEY) or "[]"
    try:
        names = json.loads(raw)
        return list(names) if isinstance(names, list) else []
    except Exception:
        return []


def _kr_index_write(kr: Any, names: list[str]) -> None:
    kr.set_password(_APP_NAME, _INDEX_KEY, json.dumps(sorted(set(names))))


# ── Fernet-encrypted file backend ───────────────────────────────────


def _try_fernet() -> Any | None:
    try:
        from cryptography.fernet import Fernet  # type: ignore[import-not-found]
        return Fernet
    except Exception:
        return None


def _derive_key() -> bytes:
    """Machine-stable Fernet key.

    Note: this is deliberately NOT a strong secret. An attacker with
    file access AND the platform identifiers can reconstruct it. The
    point is to prevent casual leakage if the file gets attached to a
    Slack message or backed up to cloud storage. For real protection
    install `keyring`.
    """
    import base64
    from cryptography.hazmat.primitives import hashes  # type: ignore[import-not-found]
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # type: ignore[import-not-found]
    salt_src = (platform.node() + platform.machine() + platform.system()).encode()
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=base64.urlsafe_b64encode(salt_src.ljust(16, b"_"))[:16],
        iterations=100_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(b"huntova-local-secrets-v1"))


def _fernet_read() -> dict[str, str]:
    Fernet = _try_fernet()
    if not Fernet:
        return {}
    p = _enc_path()
    if not p.exists():
        return {}
    try:
        f = Fernet(_derive_key())
        return json.loads(f.decrypt(p.read_bytes()).decode())
    except Exception:
        return {}


def _fernet_write(data: dict[str, str]) -> None:
    Fernet = _try_fernet()
    if not Fernet:
        raise RuntimeError("cryptography not installed; cannot encrypt secrets")
    p = _enc_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    f = Fernet(_derive_key())
    p.write_bytes(f.encrypt(json.dumps(data).encode()))
    _harden_perms(p)


# ── Plaintext last-ditch backend ────────────────────────────────────


def _plain_read() -> dict[str, str]:
    p = _plain_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _plain_write(data: dict[str, str]) -> None:
    p = _plain_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=2))
    _harden_perms(p)


def _harden_perms(p: Path) -> None:
    if os.name != "nt":
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass


# ── Public API ──────────────────────────────────────────────────────


def _backend_label() -> str:
    if _try_keyring():
        return "keyring"
    if _try_fernet():
        return "encrypted-file"
    return "plaintext-file"


def set_secret(name: str, value: str) -> None:
    kr = _try_keyring()
    if kr is not None:
        kr.set_password(_APP_NAME, name, value)
        idx = _kr_index_read(kr)
        if name not in idx:
            idx.append(name)
            _kr_index_write(kr, idx)
        return
    if _try_fernet():
        data = _fernet_read()
        data[name] = value
        _fernet_write(data)
        return
    # Last-ditch: plaintext with permission lock + warning.
    print("[secrets] warning: storing key in plaintext (install `keyring` or `cryptography`)")
    data = _plain_read()
    data[name] = value
    _plain_write(data)


def get_secret(name: str) -> str | None:
    kr = _try_keyring()
    if kr is not None:
        try:
            return kr.get_password(_APP_NAME, name)
        except Exception:
            return None
    if _try_fernet():
        return _fernet_read().get(name)
    return _plain_read().get(name)


def delete_secret(name: str) -> None:
    kr = _try_keyring()
    if kr is not None:
        try:
            kr.delete_password(_APP_NAME, name)
        except Exception:
            pass
        idx = _kr_index_read(kr)
        if name in idx:
            idx.remove(name)
            _kr_index_write(kr, idx)
        return
    if _try_fernet():
        data = _fernet_read()
        data.pop(name, None)
        _fernet_write(data)
        return
    data = _plain_read()
    data.pop(name, None)
    _plain_write(data)


def list_secret_names() -> list[str]:
    kr = _try_keyring()
    if kr is not None:
        return _kr_index_read(kr)
    if _try_fernet():
        return sorted(_fernet_read().keys())
    return sorted(_plain_read().keys())
