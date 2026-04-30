#!/usr/bin/env python3
"""
Smoke test: local-CLI mode boots cleanly and exposes the right shape.

Run via:
    APP_MODE=local DATABASE_URL= HV_GEMINI_KEY=fake \\
        uv run --python 3.13 --no-project --with-requirements requirements.txt \\
        python tools/smoke_test_local.py

Or after `pipx install -e .`:
    APP_MODE=local DATABASE_URL= HV_GEMINI_KEY=fake python tools/smoke_test_local.py

Exits 0 on success, prints failed assertions and exits non-zero on
the first regression. Designed to be cheap (~1s) so it can run in CI
on every commit.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path


def main() -> int:
    # Make the project root importable regardless of where the test is
    # run from (CI may invoke `python tools/smoke_test_local.py`).
    repo_root = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(repo_root))

    # Sandbox the SQLite file so we don't pollute the user's real db.
    tmp_db = Path(tempfile.gettempdir()) / "huntova_smoke.sqlite"
    if tmp_db.exists():
        tmp_db.unlink()
    os.environ["HUNTOVA_DB_PATH"] = str(tmp_db)
    os.environ.pop("DATABASE_URL", None)
    os.environ["APP_MODE"] = "local"
    os.environ.setdefault("HV_GEMINI_KEY", "fake-smoke-key")

    fails: list[str] = []

    def check(label: str, cond: bool, detail: str = "") -> None:
        if cond:
            print(f"  ✓ {label}")
        else:
            print(f"  ✗ {label}{('  — ' + detail) if detail else ''}")
            fails.append(label)

    print("\n[smoke] runtime capabilities")
    from runtime import CAPABILITIES
    r = CAPABILITIES.to_dict()
    check("mode == local", r["mode"] == "local")
    check("billing disabled", r["billing_enabled"] is False)
    check("auth disabled", r["auth_enabled"] is False)
    check("single_user_mode on", r["single_user_mode"] is True)

    print("\n[smoke] policy")
    from policy import policy
    check("policy.name == local", policy.name == "local")
    check("show_billing_ui False", policy.show_billing_ui() is False)
    check("cost_per_lead == 0", policy.cost_per_lead({}) == 0)
    check("can_run_agent allowed", policy.can_run_agent({"id": 1})[0] is True)

    print("\n[smoke] db driver + schema")
    import db
    db.init_db_sync()
    check("driver is sqlite", db._is_sqlite() is True)
    check("sqlite path exists", tmp_db.exists())

    print("\n[smoke] FastAPI routes")
    import server  # noqa: F401  — registers all routes
    from fastapi.testclient import TestClient
    client = TestClient(server.app)

    r1 = client.get("/api/runtime")
    check("/api/runtime 200", r1.status_code == 200, str(r1.status_code))
    rt = r1.json().get("runtime", {})
    check("/api/runtime.mode == local", rt.get("mode") == "local")
    check("/api/runtime.billing_enabled False", rt.get("billing_enabled") is False)

    r2 = client.get("/api/account")
    check("/api/account 200 (auto-login)", r2.status_code == 200, str(r2.status_code))
    j = r2.json() if r2.status_code == 200 else {}
    check("auto-bootstrapped local user", (j.get("user") or {}).get("email") == "local@huntova.app")
    feats = j.get("features") or {}
    check("ai_chat unlocked", feats.get("ai_chat") is True)
    check("research unlocked", feats.get("research") is True)
    check("export_json unlocked", feats.get("export_json") is True)

    r3 = client.get("/download")
    check("/download 200", r3.status_code == 200)
    check("/download has install command", "pipx install huntova" in r3.text)
    check("/download mentions BYOK", "BYOK" in r3.text or "bring your own key" in r3.text.lower())

    r4 = client.get("/api/health")
    check("/api/health 200", r4.status_code == 200)

    r5 = client.get("/install.sh")
    check("/install.sh 200", r5.status_code == 200)
    check("/install.sh is bash", r5.text.startswith("#!/usr/bin/env bash"))
    check("/install.sh mentions pipx", "pipx install huntova" in r5.text)

    print("")
    if fails:
        print(f"[smoke] FAILED — {len(fails)} check(s) failed:")
        for f in fails:
            print(f"        · {f}")
        return 1
    print("[smoke] all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
