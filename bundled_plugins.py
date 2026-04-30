"""
Huntova reference plugins — Round-68 brainstorm / Tab 2 (Kimi) pick.

Three plugins ship inside the wheel as a bootstrap of the marketplace.
All three:
  · Are no-ops when their config is absent (don't surprise the user)
  · Have zero net-new dependencies (stdlib only)
  · Demonstrate the canonical usage of one or more lifecycle hooks
  · Are <60 lines so they read as documentation

Loaded automatically by the registry — see plugins.PluginRegistry.
Users who don't want them can set `HV_DISABLE_BUNDLED_PLUGINS=1`.

Configuration paths:
  ~/.config/huntova/config.toml — [csv_sink] / [dedup] / [slack_ping]
  Or env vars: HV_CSV_SINK_PATH, HV_SLACK_WEBHOOK_URL
"""
from __future__ import annotations

import csv
import json
import os
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ── csv-sink ────────────────────────────────────────────────────────


class CSVSinkPlugin:
    """Append every saved lead to a local CSV file. Pipe Huntova into
    your existing spreadsheet / CRM / warehouse without writing code.

    Config: ~/.config/huntova/config.toml -> [csv_sink] path="..."
    Or env: HV_CSV_SINK_PATH
    No-op if neither is set.
    """
    name = "csv-sink"
    version = "1.0.0"
    capabilities = ["filesystem_write"]

    _FIELDS = [
        "lead_id", "org_name", "fit_score", "country", "city",
        "contact_email", "contact_name", "contact_role",
        "org_website", "url", "why_fit", "saved_at",
    ]

    def _resolve_path(self, ctx) -> Path | None:
        raw = (
            (ctx.settings.get("csv_sink") or {}).get("path")
            or os.environ.get("HV_CSV_SINK_PATH")
        )
        if not raw:
            return None
        return Path(str(raw)).expanduser()

    def post_save(self, ctx, lead: dict[str, Any]) -> None:
        path = self._resolve_path(ctx)
        if not path:
            return  # no-op — user hasn't configured a CSV path
        path.parent.mkdir(parents=True, exist_ok=True)
        row = {f: (lead.get(f) or "") for f in self._FIELDS}
        row["saved_at"] = datetime.now(timezone.utc).isoformat()
        existed = path.exists() and path.stat().st_size > 0
        with open(path, "a", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=self._FIELDS, quoting=csv.QUOTE_MINIMAL)
            if not existed:
                w.writeheader()
            w.writerow(row)


# ── dedup-by-domain ─────────────────────────────────────────────────


class DedupByDomainPlugin:
    """Suppress search results from domains already seen in the last
    N days. Eliminates the #1 noise source in multi-query hunts: the
    same domain reappearing across different queries.

    Config: [dedup] window_days = 30  (default 30)
    State: ~/.local/share/huntova/seen_domains.jsonl
    """
    name = "dedup-by-domain"
    version = "1.0.0"
    capabilities = ["filesystem_write"]

    def __init__(self) -> None:
        self._state = Path(
            os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        ) / "huntova" / "seen_domains.jsonl"
        self._loaded = False
        self._seen: set[str] = set()
        self._window_days = 30

    def _ensure_loaded(self, ctx) -> None:
        if self._loaded:
            return
        cfg = (ctx.settings.get("dedup") or {})
        try:
            self._window_days = int(cfg.get("window_days", 30))
        except (TypeError, ValueError):
            self._window_days = 30
        cutoff = time.time() - (self._window_days * 86400)
        if self._state.exists():
            try:
                with self._state.open("r", encoding="utf-8") as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            rec = json.loads(line)
                        except Exception:
                            continue
                        if (rec.get("ts") or 0) > cutoff:
                            self._seen.add(str(rec.get("domain") or ""))
            except OSError:
                pass
        self._loaded = True

    def _domain_of(self, item: dict[str, Any]) -> str:
        url = item.get("url") or item.get("link") or ""
        if "://" not in url:
            return ""
        host = url.split("://", 1)[1].split("/", 1)[0]
        return host.removeprefix("www.")

    def post_search(self, ctx, results: list[dict[str, Any]]) -> list[dict[str, Any]]:
        self._ensure_loaded(ctx)
        out: list[dict[str, Any]] = []
        new_domains: list[str] = []
        for r in results:
            d = self._domain_of(r)
            if not d:
                out.append(r)
                continue
            if d in self._seen:
                continue
            # Add to in-memory seen set IMMEDIATELY so duplicates
            # within the same batch get dropped too. Persistence
            # happens once at the end so we make a single fd open.
            self._seen.add(d)
            out.append(r)
            new_domains.append(d)
        if new_domains:
            self._state.parent.mkdir(parents=True, exist_ok=True)
            try:
                with self._state.open("a", encoding="utf-8") as fh:
                    now = time.time()
                    for d in new_domains:
                        fh.write(json.dumps({"domain": d, "ts": now}) + "\n")
            except OSError:
                pass  # state-write failure is not fatal
        return out


# ── slack-ping ──────────────────────────────────────────────────────


class SlackPingPlugin:
    """POST a small message to a Slack incoming webhook on each saved
    lead. Demonstrates external HTTP integration with perfect
    fire-and-forget semantics.

    Config: [slack_ping] webhook_url="https://hooks.slack.com/services/..."
    Or env: HV_SLACK_WEBHOOK_URL
    No-op if neither is set.
    """
    name = "slack-ping"
    version = "1.0.0"
    capabilities = ["network"]

    def _webhook(self, ctx) -> str:
        return (
            (ctx.settings.get("slack_ping") or {}).get("webhook_url")
            or os.environ.get("HV_SLACK_WEBHOOK_URL")
            or ""
        ).strip()

    def post_save(self, ctx, lead: dict[str, Any]) -> None:
        url = self._webhook(ctx)
        if not url:
            return
        org = (lead.get("org_name") or "(unknown)")[:60]
        fit = lead.get("fit_score", "?")
        country = lead.get("country") or ""
        why = (lead.get("why_fit") or "")[:160]
        site = lead.get("org_website") or lead.get("url") or ""
        text = f":bell: *{org}* — fit {fit}/10 · {country}"
        body = {
            "text": text,
            "blocks": [
                {"type": "section", "text": {"type": "mrkdwn", "text": text}},
                *([{"type": "section", "text": {"type": "mrkdwn", "text": why}}] if why else []),
                *([{"type": "context", "elements": [{"type": "mrkdwn", "text": f"<{site}|{site}>"}]}] if site else []),
            ],
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=4) as _:
                pass
        except Exception:
            # Webhook failures are silent by design — never break a hunt
            # because Slack returned 500.
            pass


# ── recipe-adaptation ───────────────────────────────────────────────


class RecipeAdaptationPlugin:
    """Reads HV_RECIPE_ADAPTATION (set by `huntova recipe run` when a
    recipe has an AI-generated adaptation card) and applies its
    boost/suppress logic to the query list before SearXNG runs.

    Boost: queries that contain `winning_terms` move to the front so
    the agent burns AI tokens on them first.
    Suppress: queries that contain any `suppress_terms` substring are
    dropped before search.
    Add: `added_queries` are inserted at the head if not already
    present (the recipe-run path also pre-appends them; this is a
    safety net).

    No config, no network, no secrets — purely an in-memory query
    rewrite based on env. Demonstrates the canonical pre_search hook.
    """
    name = "recipe-adapter"
    version = "1.0.0"
    capabilities: list = []  # purely in-memory

    def pre_search(self, ctx, queries: list[str]) -> list[str]:
        raw = os.environ.get("HV_RECIPE_ADAPTATION") or ""
        if not raw:
            return queries
        try:
            payload = json.loads(raw)
            winning = [s.lower() for s in (payload.get("winning_terms") or []) if isinstance(s, str)]
            suppress = [s.lower() for s in (payload.get("suppress_terms") or []) if isinstance(s, str)]
            added = [s for s in (payload.get("added_queries") or []) if isinstance(s, str)]
        except Exception:
            return queries
        if not (winning or suppress or added):
            return queries
        # Drop suppressed
        filtered = [q for q in queries if not any(s in q.lower() for s in suppress)]
        # Boost winning to the front (stable order within tier)
        boost: list[str] = []
        rest: list[str] = []
        for q in filtered:
            ql = q.lower()
            if any(w in ql for w in winning):
                boost.append(q)
            else:
                rest.append(q)
        # Add new queries at the very front (highest priority — they
        # came from the AI's analysis of what's been working)
        seen = {q.lower() for q in (boost + rest)}
        prepend = [q for q in added if q.lower() not in seen]
        return prepend + boost + rest


class AdaptationRulesPlugin:
    """Reads `scoring_rules` from HV_RECIPE_ADAPTATION (set by `huntova
    recipe run` from the AI-generated adaptation card) and reweights
    fit_score in post_score. The companion to recipe-adapter — that
    plugin owns query rewriting (pre_search), this one owns score
    adjustment (post_score).

    Without this plugin, the adaptation card's `scoring_rules` are
    just documentation. With it, every recipe-tuned hunt automatically
    boosts/penalises leads based on what's been working.

    Each rule is a dict:
      {"field": "tech_signals", "op": "contains", "value": "shopify", "delta": 1.5}
      {"field": "event_name",   "op": "contains", "value": "hiring",  "delta": 0.5}
      {"field": "event_name",   "op": "contains", "value": "fired",   "delta": -3.0}

    Supported ops: contains (str/list), eq, gt. Score is clamped 0-10
    after each rule. _score_trace is appended to the lead so the user
    can see why the score moved.
    """
    name = "adaptation-rules"
    version = "1.0.0"
    capabilities: list = []  # purely in-memory

    def post_score(self, ctx, lead: dict, score: float) -> tuple[dict, float]:
        raw = os.environ.get("HV_RECIPE_ADAPTATION") or ""
        if not raw:
            return lead, score
        try:
            payload = json.loads(raw)
            rules = payload.get("scoring_rules") or []
        except Exception:
            return lead, score
        if not isinstance(rules, list) or not rules:
            return lead, score
        applied: list[str] = []
        for rule in rules:
            if not isinstance(rule, dict):
                continue
            field = rule.get("field")
            op = (rule.get("op") or "contains").lower()
            value = rule.get("value")
            try:
                delta = float(rule.get("delta") or 0.0)
            except (TypeError, ValueError):
                continue
            if not field or value is None or delta == 0.0:
                continue
            actual = lead.get(field)
            match = False
            if op == "contains" and isinstance(actual, list):
                match = value in actual
            elif op == "contains" and isinstance(actual, str):
                match = str(value).lower() in actual.lower()
            elif op == "eq":
                match = actual == value
            elif op == "gt":
                try:
                    match = float(actual) > float(value)
                except (TypeError, ValueError):
                    match = False
            if match:
                score += delta
                applied.append(f"{field} {op} {value!r} → {delta:+.1f}")
        # Clamp 0-10 and trace the adjustments for the user
        score = max(0.0, min(10.0, score))
        if applied:
            existing = lead.get("_score_trace")
            if not isinstance(existing, list):
                existing = []
            lead["_score_trace"] = existing + applied
        return lead, score


# ── generic-webhook ─────────────────────────────────────────────────


class GenericWebhookPlugin:
    """POST a JSON payload to a user-configured webhook on each saved
    lead. Driven by Settings → Webhooks (top-level `webhook_url` in
    user_settings). HMAC-SHA256-signs the body with the
    `HV_WEBHOOK_SECRET` keychain value if present; signature lands in
    `X-Huntova-Signature: sha256=<hex>`.

    Config:
      - settings.webhook_url        (top-level, set via dashboard)
      - secrets_store HV_WEBHOOK_SECRET  (set via dashboard)
      - or env HV_WEBHOOK_URL / HV_WEBHOOK_SECRET (CLI users)

    Pre-a44 the dashboard's Webhooks tab was a phantom — the URL was
    saved but nothing read it. This plugin closes that loop.
    """
    name = "generic-webhook"
    version = "1.0.0"
    capabilities = ["network", "secrets"]

    def _resolve_url(self, ctx) -> str:
        return (
            ctx.settings.get("webhook_url")
            or os.environ.get("HV_WEBHOOK_URL")
            or ""
        ).strip()

    def _resolve_secret(self) -> str:
        # Prefer keychain (dashboard-saved), fall back to env (CLI users).
        try:
            from secrets_store import get_secret
            kc = get_secret("HV_WEBHOOK_SECRET")
            if kc:
                return kc
        except Exception:
            pass
        return os.environ.get("HV_WEBHOOK_SECRET", "")

    def post_save(self, ctx, lead: dict[str, Any]) -> None:
        url = self._resolve_url(ctx)
        if not url:
            return
        # Strip volatile / large fields the user doesn't need on the wire.
        # Webhook receivers usually want IDs + scoring + contact, not the
        # full email-rewrite history.
        slim = {k: v for k, v in (lead or {}).items()
                if k not in ("rewrite_history", "_full_text", "_site_text")}
        payload = {
            "event": "post_save",
            "lead": slim,
            "ts": int(__import__("time").time()),
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "huntova/generic-webhook",
        }
        secret = self._resolve_secret()
        if secret:
            import hmac as _hmac
            import hashlib as _hashlib
            sig = _hmac.new(secret.encode(), body, _hashlib.sha256).hexdigest()
            headers["X-Huntova-Signature"] = f"sha256={sig}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=8) as _:
                pass
        except Exception:
            # Webhook failures are silent — never break a hunt because
            # the user's receiver returned 500.
            pass


# ── Auto-registration ───────────────────────────────────────────────


_BUNDLED_CLASSES = (
    CSVSinkPlugin,
    DedupByDomainPlugin,
    SlackPingPlugin,
    GenericWebhookPlugin,
    RecipeAdaptationPlugin,
    AdaptationRulesPlugin,
)


def register_bundled(registry) -> list[str]:
    """Called by plugins.PluginRegistry.discover(). Skipped entirely
    if HV_DISABLE_BUNDLED_PLUGINS=1."""
    if os.environ.get("HV_DISABLE_BUNDLED_PLUGINS"):
        return []
    out: list[str] = []
    for cls in _BUNDLED_CLASSES:
        try:
            registry.register(cls())
            out.append(cls.name)
        except Exception:
            continue
    return out
