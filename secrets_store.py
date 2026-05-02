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

    a289 fix: previous derivation used `platform.node() + machine() +
    system()` as the salt — every Mac on the same hostname collides,
    and the hostname is publicly leaked via mDNS / git config / ssh
    banners. Plus iterations were 100_000 (OWASP 2024 recommends
    ≥600_000 for SHA256-PBKDF2). Now: random 16-byte salt persisted to
    `~/.config/huntova/.salt` (mode 0600), 600_000 iterations.
    """
    import base64
    import secrets as _sec
    from cryptography.hazmat.primitives import hashes  # type: ignore[import-not-found]
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC  # type: ignore[import-not-found]

    # a289: per-install random salt. First call generates + persists;
    # subsequent calls read from disk. This makes offline attacks
    # infeasible by adding 128 bits of unknown-to-attacker entropy
    # without any user-facing setup step. Falling back to the legacy
    # public derivation only when the salt file can't be created
    # (read-only filesystem) — better that than refuse to start.
    # a293 fix: previous version generated a fresh in-memory random
    # salt when the salt file couldn't be persisted (read-only fs,
    # sandboxed environment, corporate-locked install). Every process
    # then derived a different key → Fernet ciphertext written by
    # run #1 became unreadable by run #2 → user's stored API keys
    # silently became "no provider configured" errors. Now: if we
    # can't persist a fresh random salt, fall back to the legacy
    # PUBLIC derivation across ALL runs so the same install keeps
    # producing the same key. That weakens security on read-only
    # installs (back to the a289 pre-fix posture for those edge cases)
    # but preserves usability — and the user's keys actually work.
    salt_path = _enc_path().with_name(".salt")
    salt = b""
    legacy_fallback = False
    try:
        if salt_path.exists():
            salt = salt_path.read_bytes()
            if len(salt) != 16:
                salt = b""
        if not salt:
            new_salt = _sec.token_bytes(16)
            try:
                salt_path.parent.mkdir(parents=True, exist_ok=True)
                # Write atomically + restrict perms.
                tmp = salt_path.with_name(".salt.tmp")
                tmp.write_bytes(new_salt)
                try: os.chmod(tmp, 0o600)
                except Exception: pass
                tmp.replace(salt_path)
                try: os.chmod(salt_path, 0o600)
                except Exception: pass
                salt = new_salt
            except Exception:
                # Persist failed — use legacy derivation for cross-run
                # determinism. Strictly worse than random salt but
                # better than user keys becoming unreadable.
                legacy_fallback = True
    except Exception:
        legacy_fallback = True
    if legacy_fallback or not salt:
        if legacy_fallback:
            print("[secrets] WARN: salt persist failed — falling back to "
                  "legacy public derivation (security degraded; install "
                  "to a writable home dir for full random-salt protection)",
                  file=__import__("sys").stderr)
        salt_src = (platform.node() + platform.machine() + platform.system()).encode()
        salt = base64.urlsafe_b64encode(salt_src.ljust(16, b"_"))[:16]
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,
    )
    return base64.urlsafe_b64encode(kdf.derive(b"huntova-local-secrets-v1"))


class _FernetUnreadable(RuntimeError):
    """Encrypted file exists but can't be decrypted (key derivation
    drift, file corruption, version mismatch). Distinguished from
    'file missing' so callers don't silently clobber it on the next
    write — that path used to wipe every previously-stored key when
    a single decrypt failed (e.g. macOS hostname change rotated the
    derivation salt). Audit wave 26 fix."""


def _fernet_read() -> dict[str, str]:
    Fernet = _try_fernet()
    if not Fernet:
        return {}
    p = _enc_path()
    if not p.exists():
        return {}  # genuinely empty store — safe to fall through
    try:
        f = Fernet(_derive_key())
        decoded = f.decrypt(p.read_bytes()).decode()
    except Exception as e:
        raise _FernetUnreadable(
            f"existing {p.name} cannot be decrypted ({type(e).__name__}); "
            "refusing to overwrite. Recover the file or delete it manually "
            "to start over."
        ) from e
    try:
        return json.loads(decoded)
    except Exception as e:
        raise _FernetUnreadable(
            f"existing {p.name} decrypted but is not valid JSON ({e}); "
            "refusing to overwrite."
        ) from e


def _atomic_write_0600(p: Path, data: bytes) -> None:
    """Create-or-replace `p` with `data`, mode 0600, atomically.

    Prevents the TOCTOU window where p.write_text/p.write_bytes
    creates the file with default umask (typically 0644 on Unix —
    world-readable) and only THEN os.chmod tightens it. A reader
    racing in between can slurp the secrets in plaintext.

    Strategy: open with O_CREAT|O_EXCL|O_WRONLY|0600 on a temp
    sibling, write+fsync, atomic rename over the target. On Windows
    (no chmod 0600 anyway) fall back to plain write_bytes.
    """
    p.parent.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        p.write_bytes(data)
        return
    tmp = p.with_suffix(p.suffix + ".tmp")
    # If a stale tmp exists from a crashed prior run, drop it.
    try:
        tmp.unlink()
    except FileNotFoundError:
        pass
    fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            try:
                os.fsync(fh.fileno())
            except OSError:
                pass
    except Exception:
        try:
            os.unlink(str(tmp))
        except OSError:
            pass
        raise
    os.replace(str(tmp), str(p))


def _fernet_write(data: dict[str, str]) -> None:
    Fernet = _try_fernet()
    if not Fernet:
        raise RuntimeError("cryptography not installed; cannot encrypt secrets")
    p = _enc_path()
    f = Fernet(_derive_key())
    _atomic_write_0600(p, f.encrypt(json.dumps(data).encode()))


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
    _atomic_write_0600(_plain_path(), json.dumps(data, indent=2).encode())


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
        # _fernet_read now raises _FernetUnreadable if the existing
        # file can't be decrypted — propagating instead of silently
        # turning into an empty dict that we then overwrite. Caller
        # sees a clear error and can recover the file or remove it.
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
            value = kr.get_password(_APP_NAME, name)
        except Exception:
            return None
        if value is not None:
            # Successful keychain read — clear the cli.py warning sentinel
            # so the warning can fire again if the keychain breaks later.
            # Best-effort: any failure here is silent (sentinel just stays
            # for a bit longer until the next successful read).
            try:
                base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
                sentinel = Path(base) / "huntova" / ".keychain_warned"
                if sentinel.exists():
                    sentinel.unlink()
            except OSError:
                pass
        return value
    if _try_fernet():
        # On read, prefer "no key found" over a hard error — the
        # caller (e.g. provider key lookup) can fall back to env or
        # config. Wrapped here, NOT inside _fernet_read, so the
        # write paths still see the unreadable-file signal.
        try:
            return _fernet_read().get(name)
        except _FernetUnreadable:
            return None
    return _plain_read().get(name)


def delete_secret(name: str) -> None:
    """a289 fix: sweep ALL THREE tiers, not just the active one. The
    previous version returned after deleting from whichever tier was
    active, leaving stale rows in the lower tiers. Concretely: a user
    who first ran without `keyring` (secret landed in Fernet file) and
    later installed `keyring` would see `delete_secret` only touch
    keychain — the original Fernet-encrypted copy stayed on disk
    forever even though the user thought they'd revoked the key. Now
    we attempt removal from every tier; failures in lower tiers are
    swallowed (they may not exist) but logged."""
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
    # Always sweep Fernet + plaintext too — they may carry a stale
    # copy from an earlier tier that was active when the secret was
    # first written.
    # a291 hotfix: log warnings instead of silently swallowing.
    # a289's "swallowed but logged" claim wasn't actually backed by
    # logging code — partial-revoke incidents went invisible. Now
    # operators can grep for these.
    import sys as _sys
    if _try_fernet():
        try:
            data = _fernet_read()
            if name in data:
                data.pop(name, None)
                _fernet_write(data)
        except _FernetUnreadable:
            # Existing Fernet file is corrupted; don't touch it. The
            # legitimate-decrypt user will see _FernetUnreadable on
            # the next get/set and can recover manually.
            print(f"[secrets] WARN: skipping Fernet sweep for {name!r} — "
                  f"existing file is unreadable", file=_sys.stderr)
        except Exception as _e:
            print(f"[secrets] WARN: Fernet delete sweep failed for {name!r}: "
                  f"{type(_e).__name__}: {_e}", file=_sys.stderr)
    try:
        data = _plain_read()
        if name in data:
            data.pop(name, None)
            _plain_write(data)
    except Exception as _e:
        print(f"[secrets] WARN: plaintext delete sweep failed for {name!r}: "
              f"{type(_e).__name__}: {_e}", file=_sys.stderr)


def list_secret_names() -> list[str]:
    kr = _try_keyring()
    if kr is not None:
        return _kr_index_read(kr)
    if _try_fernet():
        try:
            return sorted(_fernet_read().keys())
        except _FernetUnreadable:
            return []
    return sorted(_plain_read().keys())
