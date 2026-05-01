"""
Huntova `logs` command — unified log viewer for debugging a hunt.
Pulls from agent_runs / agent_run_logs / lead_actions tables plus the
daemon stdout/stderr files. Pattern adapted from OpenClaw's
`openclaw logs` / `openclaw tail`; independent Python implementation.
Wired in cli.py via `register(sub)`.

Subcommands: `tail [--follow] [--since 1h]` (cross-source feed, DESC),
`hunt <run_id>` (one-run lifecycle), `daemon` (tail launchd/systemd
logs), `filter --level {error,warn,info,debug}` (severity gate).
All support `--json`. `--follow` polls every 2s; Ctrl+C exits cleanly.
"""
from __future__ import annotations

import argparse
import json as _json
import os
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── tui colors (reuse existing palette) ─────────────────────────────

try:
    from tui import bold, dim, red, yellow, cyan
except Exception:  # pragma: no cover — tui import must not break boot
    bold = dim = red = yellow = cyan = (lambda s: s)


_LEVELS = ("error", "warn", "info", "debug")
_LEVEL_RANK = {lv: i for i, lv in enumerate(_LEVELS)}
_ERR_RE = re.compile(r"\b(error|exception|traceback|fail\w*|crashed)\b", re.I)
_WARN_RE = re.compile(r"\b(warn|warning)\b", re.I)


def _level_of(text: str, status: str = "") -> str:
    if (status or "").lower() in ("error", "crashed", "failed"): return "error"
    t = text or ""
    if _ERR_RE.search(t): return "error"
    if _WARN_RE.search(t): return "warn"
    return "info"


def _level_chip(lv: str) -> str:
    fn = {"error": red, "warn": yellow, "info": cyan, "debug": dim}.get(lv, dim)
    return fn(f"{lv.upper():<5}")


def _parse_since(spec: str) -> datetime | None:
    """Accepts '1h' / '30m' / '2d' / '90s'. Returns UTC cutoff or None."""
    if not spec: return None
    m = re.fullmatch(r"\s*(\d+)\s*([smhd])\s*", spec.lower())
    if not m: return None
    unit_map = {"s": "seconds", "m": "minutes", "h": "hours", "d": "days"}
    return datetime.now(timezone.utc) - timedelta(
        **{unit_map[m.group(2)]: int(m.group(1))})


def _ts_short(t: str) -> str: return (t or "")[:19].replace("T", " ")


def _fetchall(drv, sql: str, params: tuple) -> list:
    conn = drv.get_conn()
    try:
        cur = conn.cursor()
        cur.execute(drv.translate_sql(sql), params)
        return [dict(r) for r in cur.fetchall()]
    finally: drv.put_conn(conn)


# ── source loaders ─────────────────────────────────────────────────


def _load_runs(drv, user_id: int, cutoff: str | None) -> list[dict]:
    sql = ("SELECT id, status, leads_found, started_at, ended_at, "
           "queries_done, queries_total FROM agent_runs WHERE user_id = %s")
    params: tuple = (user_id,)
    if cutoff: sql += " AND started_at >= %s"; params = (user_id, cutoff)
    sql += " ORDER BY started_at DESC LIMIT 200"
    out = []
    for r in _fetchall(drv, sql, params):
        status = (r.get("status") or "").lower()
        msg = (f"hunt #{r.get('id')} {status} — {r.get('leads_found', 0)} "
               f"lead(s), {r.get('queries_done', 0)}/{r.get('queries_total', 0)} queries")
        out.append({"ts": r.get("ended_at") or r.get("started_at") or "",
                    "source": "hunt", "level": _level_of("", status),
                    "run_id": r.get("id"), "message": msg})
    return out


def _load_run_logs(drv, user_id: int, cutoff: str | None,
                   run_id: int | None = None) -> list[dict]:
    sql = ("SELECT id, run_id, log_text, leads_found, queries_run, urls_checked, "
           "created_at FROM agent_run_logs WHERE user_id = %s")
    params: list = [user_id]
    if run_id is not None: sql += " AND run_id = %s"; params.append(run_id)
    if cutoff: sql += " AND created_at >= %s"; params.append(cutoff)
    sql += " ORDER BY created_at DESC LIMIT 500"
    out = []
    for r in _fetchall(drv, sql, tuple(params)):
        text = (r.get("log_text") or "").strip()
        out.append({"ts": r.get("created_at") or "", "source": "log",
                    "level": _level_of(text), "run_id": r.get("run_id"),
                    "message": text or f"run #{r.get('run_id')} progress"})
    return out


def _load_actions(drv, user_id: int, cutoff: str | None) -> list[dict]:
    sql = ("SELECT lead_id, action_type, score_band, meta, created_at "
           "FROM lead_actions WHERE user_id = %s")
    params: tuple = (user_id,)
    if cutoff: sql += " AND created_at >= %s"; params = (user_id, cutoff)
    sql += " ORDER BY created_at DESC LIMIT 300"
    out = []
    for r in _fetchall(drv, sql, params):
        atype = r.get("action_type") or "?"
        msg = (f"lead {r.get('lead_id', '?')} → {atype} "
               f"(band={r.get('score_band') or '?'})")
        out.append({"ts": r.get("created_at") or "", "source": "action",
                    "level": _level_of(atype),
                    "lead_id": r.get("lead_id"), "message": msg})
    return out


def _daemon_log_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(base) / "huntova" / "logs"


# Keyed on (name, st_ino) so file-rotation (delete+recreate, fresh
# inode) is detected and we re-tail from byte 0. Round-10 finding #4.
_DAEMON_LAST_POS: dict[tuple, int] = {}


def reset_daemon_state() -> None:
    """Clear cached last-pos. Round-10 finding #3: one-shot
    `huntova logs daemon` and `--follow` invocations within the same
    Python process were sharing the dict, so a second call would skip
    everything emitted between the two reads. Callers (the follow-loop
    setup and the one-shot dispatcher) reset before reading."""
    _DAEMON_LAST_POS.clear()


def _load_daemon() -> list[dict]:
    """Read tail of daemon.out + daemon.err with stable per-line keys.

    Round-8 finding #5 introduced byte-offset keys to stop dedupe from
    collapsing repeating lines. Round-9 finding #1 caught the
    tail-relative-index regression. Round-10 findings #3 + #4 fixed:
    state-leak across one-shot/follow invocations, and rotation
    detection (delete+recreate gives a fresh inode and resets the
    cached pos to 0).
    """
    import os as _os
    out = []
    for name in ("daemon.out", "daemon.err"):
        p = _daemon_log_dir() / name
        if not p.exists(): continue
        try:
            st = _os.stat(p)
        except OSError:
            continue
        key = (name, st.st_ino)
        try:
            with open(p, "rb") as f:
                f.seek(0, 2)
                end = f.tell()
                start = _DAEMON_LAST_POS.get(key, 0)
                # On first read EVER (no entries in _DAEMON_LAST_POS),
                # seek to last ~64KB so we don't dump the whole file.
                # On a fresh inode mid-poll (rotation just happened —
                # a213 added 10MB rotation to agent.log, so this is now
                # a real path), the new inode key isn't in the dict
                # but `_DAEMON_LAST_POS` itself is non-empty. Reading
                # from `end - 64KB` would silently DROP the first up-
                # to-64KB of post-rotation content if the agent wrote
                # ≥64KB before our next 2s tick. Distinguish "first
                # read ever" (apply tail) from "first read of this
                # inode after rotation" (read from byte 0 — we know
                # the file is freshly rotated).
                if start == 0:
                    if not _DAEMON_LAST_POS:
                        # First poll of the process — apply 64KB tail
                        # so we don't flood with stale history.
                        start = max(0, end - 64 * 1024)
                    # else: rotation produced a fresh inode — keep
                    # start=0 to read the whole new file from byte 0.
                if start > end:
                    # File was truncated in place (without rotation —
                    # same inode). Reset.
                    start = 0
                f.seek(start)
                chunk = f.read(end - start).decode(errors="ignore")
                _DAEMON_LAST_POS[key] = end
        except Exception:
            continue
        offset = start
        for line in chunk.splitlines(keepends=True):
            line_start = offset
            offset += len(line.encode(errors="ignore"))
            stripped = line.rstrip("\r\n")
            if not stripped.strip():
                continue
            lv = _level_of(stripped, "error" if name.endswith(".err") else "")
            out.append({"ts": f"{name}@{line_start}",
                        "source": f"daemon:{name}",
                        "level": lv, "message": stripped})
    return out


# ── render + follow loop ───────────────────────────────────────────


def _emit(events: list[dict], level_min: str | None, as_json: bool,
          header: str = "") -> int:
    if level_min:
        rank = _LEVEL_RANK.get(level_min, 99)
        events = [e for e in events if _LEVEL_RANK.get(e["level"], 99) <= rank]
    events.sort(key=lambda e: e.get("ts") or "", reverse=True)
    if as_json:
        print(_json.dumps(events, indent=2, default=str)); return 0
    if header: print(f"\n{bold(header)}\n")
    if not events:
        print(f"  {dim('— no log events found')}\n"); return 0
    for e in events:
        print(f"  {dim(_ts_short(e.get('ts') or '')):<19}  "
              f"{_level_chip(e['level'])}  {dim(e.get('source', '?')):<12}  "
              f"{e.get('message', '')}")
    print("")
    return 0


def _follow(gather_fn, args: argparse.Namespace) -> int:
    """Shared --follow loop: polls every 2s, dedupes by (source,ts,message).

    Round-8 audit findings #3 + #5: bounded the dedupe set (was unbounded
    OOM on long-running follow sessions) and used a more discriminating
    key for daemon entries (was collapsing identical lines because daemon
    `ts` is empty, so two distinct repeating log lines hashed identically).
    Now: (source, ts, message[:200], idx-mod-cap) — `idx` is a counter
    that lets identical messages within the same poll be distinct.
    """
    from collections import deque
    _MAX_SEEN = 5000
    seen: set = set()
    seen_order: deque = deque()
    try:
        while True:
            ev = gather_fn()
            fresh = []
            for idx, e in enumerate(ev):
                # Daemon entries have empty `ts`; use the file:line key
                # we now emit (`name:lineno`) so distinct lines stay
                # distinct. For DB-backed events, ts+message is enough.
                msg = (e.get("message") or "")[:200]
                key = (e.get("source"), e.get("ts") or "", msg)
                if key not in seen:
                    fresh.append(e)
                    seen.add(key)
                    seen_order.append(key)
                    while len(seen_order) > _MAX_SEEN:
                        old = seen_order.popleft()
                        seen.discard(old)
            if fresh: _emit(fresh, args.level, args.json)
            time.sleep(2)
    except KeyboardInterrupt:
        print(dim("\n[huntova] follow stopped.")); return 0


# ── subcommand handlers ────────────────────────────────────────────


def _cmd_tail(drv, user_id: int, args: argparse.Namespace) -> int:
    dt = _parse_since(args.since or "")
    cutoff = dt.isoformat() if dt else None
    gather = lambda: (_load_runs(drv, user_id, cutoff)
                      + _load_run_logs(drv, user_id, cutoff)
                      + _load_actions(drv, user_id, cutoff))
    if args.follow: return _follow(gather, args)
    return _emit(gather(), args.level, args.json,
                 header=f"Last events ({args.since or 'all time'})")


def _cmd_hunt(drv, user_id: int, args: argparse.Namespace) -> int:
    try: rid_i = int(args.run_id)
    except (TypeError, ValueError):
        print(f"[huntova] run_id must be an integer, got {args.run_id!r}",
              file=sys.stderr); return 1
    runs = [r for r in _load_runs(drv, user_id, None) if r.get("run_id") == rid_i]
    logs = _load_run_logs(drv, user_id, None, run_id=rid_i)
    if not runs and not logs:
        print(f"[huntova] no events for hunt #{rid_i}.", file=sys.stderr); return 1
    return _emit(runs + logs, args.level, args.json,
                 header=f"Hunt #{rid_i} timeline")


def _cmd_daemon(args: argparse.Namespace) -> int:
    # Reset cached last-pos so this invocation starts from the
    # 64KB-tail rather than wherever a prior call in the same process
    # left off. Round-10 finding #3.
    reset_daemon_state()
    if args.follow: return _follow(_load_daemon, args)
    return _emit(_load_daemon(), args.level, args.json,
                 header=f"Daemon logs ({_daemon_log_dir()})")


# ── public dispatcher + argparse wiring ────────────────────────────


def cmd_logs(args: argparse.Namespace) -> int:
    """Dispatcher: huntova logs {tail|hunt|daemon|filter}."""
    sub = (getattr(args, "logs_cmd", None) or "tail").strip().lower()
    if sub == "daemon": return _cmd_daemon(args)
    from cli import _bootstrap_local_env
    user_id = _bootstrap_local_env()
    if user_id is None: return 1
    from db_driver import get_driver
    drv = get_driver()
    if sub == "tail":   return _cmd_tail(drv, user_id, args)
    if sub == "hunt":   return _cmd_hunt(drv, user_id, args)
    if sub == "filter":
        if not args.level:
            print("[huntova] usage: huntova logs filter --level "
                  "{error,warn,info,debug}", file=sys.stderr); return 1
        return _cmd_tail(drv, user_id, args)
    print(f"[huntova] unknown logs subcommand {sub!r} — "
          "try tail/hunt/daemon/filter", file=sys.stderr)
    return 1


def _add_common(p: argparse.ArgumentParser, *, level_required: bool = False,
                with_follow: bool = True, with_since: bool = True) -> None:
    if with_follow:
        p.add_argument("--follow", action="store_true",
                       help="Poll every 2s for new events (Ctrl+C to exit)")
    if with_since:
        p.add_argument("--since", default="", metavar="DURATION",
                       help="e.g. 1h, 30m, 2d, 90s")
    p.add_argument("--level", choices=_LEVELS, default=None,
                   required=level_required,
                   help="Filter by minimum severity")
    p.add_argument("--json", action="store_true", help="Emit JSON")


def register(sub) -> None:
    """Attach `logs` subparser to cli.py's argparse tree."""
    p = sub.add_parser("logs",
        help="Unified log viewer (hunts + agent logs + actions + daemon)",
        description="Cross-source log feed for debugging a hunt. Sources: "
                    "agent_runs (lifecycle), agent_run_logs (per-run text), "
                    "lead_actions (scoring/email outcomes), daemon stdout/err.")
    sp = p.add_subparsers(dest="logs_cmd")
    _add_common(sp.add_parser("tail",
        help="Recent events across all sources, DESC"))
    h = sp.add_parser("hunt", help="Every event for one hunt run")
    h.add_argument("run_id", help="Numeric agent_runs.id")
    _add_common(h, with_follow=False, with_since=False)
    _add_common(sp.add_parser("daemon",
        help="Tail ~/.local/share/huntova/logs/daemon.{out,err}"))
    _add_common(sp.add_parser("filter",
        help="Filter cross-source feed by level"), level_required=True)
    p.set_defaults(func=cmd_logs)
