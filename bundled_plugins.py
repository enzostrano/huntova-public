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
import urllib.parse


# a285 fix: webhook plugins follow user-configured URLs. The default
# urlopen handler enables HTTPRedirectHandler, which lets an attacker-
# controlled webhook respond with `302 Location: http://169.254.169.254/`
# (or any internal-IP target), bypassing the _safe_outbound_url SSRF
# guard that ran on the original URL. Build an opener with NO redirect
# handler so 30x responses just return the redirect status code instead
# of being followed. Defends every webhook + dispatch site below.
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse to follow any redirect."""
    def http_error_301(self, req, fp, code, msg, hdrs): return None
    def http_error_302(self, req, fp, code, msg, hdrs): return None
    def http_error_303(self, req, fp, code, msg, hdrs): return None
    def http_error_307(self, req, fp, code, msg, hdrs): return None
    def http_error_308(self, req, fp, code, msg, hdrs): return None


_safe_opener = urllib.request.build_opener(_NoRedirect())


def _safe_urlopen(req, timeout=8):
    """SSRF-safe urlopen: refuses redirects so an attacker can't bounce
    us to internal IPs after _safe_outbound_url cleared the original
    host. Plus a306 hardening: closes the DNS-rebind TOCTOU between
    `_safe_outbound_url`'s getaddrinfo and urllib's separate resolution
    via a thread-local socket.getaddrinfo monkey-patch that returns
    ONLY the validated IPs for the duration of the request. Without
    this, an attacker with TTL=0 DNS could return public IP to our
    validator and private IP (169.254.169.254 / metadata service) to
    urllib's resolver. Now: we resolve once, validate, pin.

    Use this everywhere instead of urllib.request.urlopen()."""
    import socket as _sock
    import threading as _thr
    from urllib.parse import urlparse as _up

    # Pull the URL out of req (str or Request)
    target_url = req.full_url if hasattr(req, "full_url") else str(req)
    try:
        parsed = _up(target_url)
    except Exception:
        # bad URL — let urllib raise its own error normally
        return _safe_opener.open(req, timeout=timeout)
    target_host = (parsed.hostname or "").lower().lstrip(".")
    if not target_host:
        return _safe_opener.open(req, timeout=timeout)

    # Re-validate + capture the IP set we trust. _safe_outbound_url
    # already ran from the caller's perspective, but we re-resolve
    # here so the IPs we pin are the ones we just validated (closing
    # the TOCTOU window).
    if not _safe_outbound_url(target_url):
        raise ValueError(f"_safe_urlopen: URL failed safety check: {target_host!r}")
    try:
        infos = _sock.getaddrinfo(target_host, None)
    except Exception:
        # DNS broke between safety check and pin — refuse.
        raise ValueError(f"_safe_urlopen: DNS resolution failed for {target_host!r}")
    pinned_ips = sorted({info[4][0] for info in infos if info[4]})
    if not pinned_ips:
        raise ValueError(f"_safe_urlopen: no addresses for {target_host!r}")

    # Thread-local override of getaddrinfo. Each thread holds its own
    # pinned set; concurrent requests across threads don't interfere.
    _patch_state = getattr(_safe_urlopen, "_patch_state", None)
    if _patch_state is None:
        _patch_state = _thr.local()
        _safe_urlopen._patch_state = _patch_state  # type: ignore[attr-defined]
    _orig_getaddrinfo = _sock.getaddrinfo

    def _pinned_getaddrinfo(host, port, *args, **kwargs):
        # Only override resolution for OUR target host. Other DNS lookups
        # (CRL endpoints during TLS, etc.) pass through normally.
        if isinstance(host, str) and host.lower().lstrip(".") == target_host:
            results = []
            for ip in pinned_ips:
                if ":" in ip:
                    results.append((_sock.AF_INET6, _sock.SOCK_STREAM, 6, "",
                                    (ip, port or 0, 0, 0)))
                else:
                    results.append((_sock.AF_INET, _sock.SOCK_STREAM, 6, "",
                                    (ip, port or 0)))
            return results
        return _orig_getaddrinfo(host, port, *args, **kwargs)

    # CPython doesn't have per-thread function dispatch for module
    # globals, so this monkey-patch IS process-wide for the duration
    # of the request. Two concurrent _safe_urlopen calls from
    # different threads briefly race; the loser sees the OTHER call's
    # pinned set if the host happens to match. Acceptable trade — IPs
    # for the same hostname are equivalent in practice, and our
    # validator already cleared all of them. The lock below makes
    # the windows discrete.
    _lock = getattr(_safe_urlopen, "_lock", None)
    if _lock is None:
        _lock = _thr.Lock()
        _safe_urlopen._lock = _lock  # type: ignore[attr-defined]

    with _lock:
        _sock.getaddrinfo = _pinned_getaddrinfo
        try:
            return _safe_opener.open(req, timeout=timeout)
        finally:
            _sock.getaddrinfo = _orig_getaddrinfo
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse as _urlparse


def _safe_outbound_url(url: str) -> bool:
    """Validate a user-configured webhook URL before POSTing.

    a276 hardening: a security agent flagged the prior implementation
    as missing IPv6 loopback (`::1`), IPv4-mapped IPv6 (`::ffff:127.0.0.1`),
    link-local (`fe80::/10`), unique-local (`fc00::/7`), `0.0.0.0`,
    octal/hex/decimal-int IP forms (`http://017700000001/`,
    `http://2130706433/`), DNS rebinding (a hostname that resolves to
    127.0.0.1 at request time), and cloud-metadata-by-name
    (`metadata.google.internal`, `instance-data`).

    New approach: resolve the host via socket.getaddrinfo, then check
    EVERY returned address through `ipaddress.ip_address()` — its
    `is_private`, `is_loopback`, `is_link_local`, `is_reserved`,
    `is_unspecified` properties cover IPv4 + IPv6 + the numeric-form
    edge cases (Python's `ipaddress` parser canonicalises octal/hex/
    decimal-int forms before classification). Plus a small named-host
    blocklist for cloud-metadata-by-name.
    """
    if not url:
        return False
    try:
        u = _urlparse(url)
    except Exception:
        return False
    if u.scheme not in ("http", "https"):
        return False
    host = (u.hostname or "").lower().strip("[]")
    if not host:
        return False
    # Named cloud-metadata + obvious local synonyms — block by name even
    # if DNS would resolve them to a public IP (paranoid).
    _BLOCKED_HOSTS = {
        "localhost", "ip6-localhost", "ip6-loopback",
        "metadata.google.internal", "instance-data",
        "metadata", "metadata.amazonaws.com", "metadata.azure.com",
    }
    if host in _BLOCKED_HOSTS:
        return False
    # Resolve + check every returned address against ipaddress's
    # classifiers. This catches numeric IP forms (octal/hex/decimal-int),
    # IPv4-mapped IPv6, loopback, link-local, unique-local, reserved,
    # multicast, and DNS rebinding (a name that resolves to a private
    # range at request time).
    import ipaddress as _ip
    import socket as _sock
    try:
        infos = _sock.getaddrinfo(host, None)
    except Exception:
        # If we can't resolve, refuse — the request would also fail,
        # but a transient DNS hiccup shouldn't be the only thing
        # standing between an attacker and 169.254.169.254.
        return False
    for info in infos:
        addr = info[4][0]
        try:
            ip = _ip.ip_address(addr)
        except ValueError:
            return False
        if (ip.is_private or ip.is_loopback or ip.is_link_local
                or ip.is_reserved or ip.is_unspecified or ip.is_multicast):
            return False
        # a294 hardening: catch ranges the six classifiers above MISS —
        # CGNAT (100.64.0.0/10), Alibaba metadata (100.100.100.200),
        # other "not private but not globally routable" addresses. The
        # `is_global` property is the most conservative SSRF gate
        # available; `not is_global` catches everything that isn't
        # globally routable. Defense in depth on top of the named
        # blocklist + private-range checks.
        if not ip.is_global:
            return False
        # IPv4-mapped IPv6: ::ffff:127.0.0.1 has is_loopback=False but
        # the IPv4 portion is loopback. Check the v4-mapped form too.
        if isinstance(ip, _ip.IPv6Address) and ip.ipv4_mapped:
            v4 = ip.ipv4_mapped
            if (v4.is_private or v4.is_loopback or v4.is_link_local
                    or v4.is_reserved or v4.is_unspecified or v4.is_multicast
                    or not v4.is_global):
                return False
    # NOTE on residual TOCTOU: urllib's urlopen does its own DNS
    # resolution, so an attacker with TTL=0 DNS could theoretically
    # return a public IP to our getaddrinfo above and a private IP
    # to urlopen's resolution. Mitigated by the `_NoRedirect` opener
    # at module top — even if rebind succeeds, no body data is read
    # back to the attacker. Full fix (resolve once + connect to the
    # IP literal with Host header) is invasive enough to defer.
    return True


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
        host = url.split("://", 1)[1].split("/", 1)[0].lower()
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
        # Reject any URL that isn't http(s) — defends against a hostile
        # ctx.settings injecting file://, smb://, gopher://, etc., which
        # urllib.request.urlopen() would otherwise honor and trigger
        # local file reads or network share access.
        if not _safe_outbound_url(url):
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
            with _safe_urlopen(req, timeout=4) as _:
                pass
        except Exception:
            # Webhook failures are silent by design — never break a hunt
            # because Slack returned 500.
            pass


# ── discord-ping ────────────────────────────────────────────────────


class DiscordPingPlugin:
    """a249: POST a brief embed to a Discord incoming webhook on each saved
    lead. Mirrors the Slack-ping shape but uses Discord's webhook payload.

    Config: settings.discord_webhook_url (set in Settings → Integrations)
    Or env: HV_DISCORD_WEBHOOK_URL
    No-op if neither is set.
    """
    name = "discord-ping"
    version = "1.0.0"
    capabilities = ["network"]

    def _webhook(self, ctx) -> str:
        return (
            ctx.settings.get("discord_webhook_url")
            or os.environ.get("HV_DISCORD_WEBHOOK_URL")
            or ""
        ).strip()

    def post_save(self, ctx, lead: dict[str, Any]) -> None:
        url = self._webhook(ctx)
        if not _safe_outbound_url(url):
            return
        org = (lead.get("org_name") or "(unknown)")[:60]
        fit = lead.get("fit_score", "?")
        country = lead.get("country") or ""
        why = (lead.get("why_fit") or "")[:200]
        site = lead.get("org_website") or lead.get("url") or ""
        embed = {
            "title": f"🎯 {org} · fit {fit}/10",
            "description": (why or country)[:400],
            "url": site or None,
            "color": 0x36DFC4,
        }
        body = {"embeds": [embed]}
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _safe_urlopen(req, timeout=4) as _:
                pass
        except Exception:
            pass


# ── telegram-ping ───────────────────────────────────────────────────


class TelegramPingPlugin:
    """a249: send a short message to a Telegram chat via bot API on each
    saved lead. Requires both `telegram_bot_token` and `telegram_chat_id`
    (Settings → Integrations) or the equivalent env vars HV_TELEGRAM_TOKEN
    / HV_TELEGRAM_CHAT_ID.
    """
    name = "telegram-ping"
    version = "1.0.0"
    capabilities = ["network"]

    def _config(self, ctx) -> tuple[str, str]:
        tok = (ctx.settings.get("telegram_bot_token")
               or os.environ.get("HV_TELEGRAM_TOKEN") or "").strip()
        chat = (ctx.settings.get("telegram_chat_id")
                or os.environ.get("HV_TELEGRAM_CHAT_ID") or "").strip()
        return tok, chat

    def post_save(self, ctx, lead: dict[str, Any]) -> None:
        tok, chat = self._config(ctx)
        if not tok or not chat:
            return
        org = (lead.get("org_name") or "(unknown)")[:60]
        fit = lead.get("fit_score", "?")
        country = lead.get("country") or ""
        site = lead.get("org_website") or lead.get("url") or ""
        text = f"🎯 {org} · fit {fit}/10 · {country}"
        if site:
            text += f"\n{site}"
        url = f"https://api.telegram.org/bot{tok}/sendMessage"
        body = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
        req = urllib.request.Request(
            url,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with _safe_urlopen(req, timeout=4) as _:
                pass
        except Exception:
            pass


# ── whatsapp-ping (Twilio) ──────────────────────────────────────────


class WhatsAppPingPlugin:
    """a257: send a WhatsApp message via Twilio's API on each saved lead.
    Requires:
      - twilio_account_sid
      - twilio_auth_token
      - twilio_whatsapp_from (e.g. whatsapp:+14155238886 — Twilio's sandbox
        number, or a real Business API sender)
      - whatsapp_to (e.g. whatsapp:+44…)

    All four can also be set via env: HV_TWILIO_SID / HV_TWILIO_TOKEN /
    HV_TWILIO_WHATSAPP_FROM / HV_WHATSAPP_TO.
    """
    name = "whatsapp-ping"
    version = "1.0.0"
    capabilities = ["network"]

    def _config(self, ctx) -> tuple[str, str, str, str]:
        sid = (ctx.settings.get("twilio_account_sid")
               or os.environ.get("HV_TWILIO_SID") or "").strip()
        tok = (ctx.settings.get("twilio_auth_token")
               or os.environ.get("HV_TWILIO_TOKEN") or "").strip()
        sender = (ctx.settings.get("twilio_whatsapp_from")
                  or os.environ.get("HV_TWILIO_WHATSAPP_FROM") or "").strip()
        to = (ctx.settings.get("whatsapp_to")
              or os.environ.get("HV_WHATSAPP_TO") or "").strip()
        return sid, tok, sender, to

    def post_save(self, ctx, lead: dict[str, Any]) -> None:
        sid, tok, sender, to = self._config(ctx)
        if not (sid and tok and sender and to):
            return
        # Twilio requires whatsapp: prefix on phone numbers
        if not sender.startswith("whatsapp:"):
            sender = "whatsapp:" + sender
        if not to.startswith("whatsapp:"):
            to = "whatsapp:" + to
        org = (lead.get("org_name") or "(unknown)")[:60]
        fit = lead.get("fit_score", "?")
        country = lead.get("country") or ""
        site = lead.get("org_website") or lead.get("url") or ""
        text = f"🎯 {org} · fit {fit}/10"
        if country: text += f" · {country}"
        if site: text += f"\n{site}"
        url = f"https://api.twilio.com/2010-04-01/Accounts/{sid}/Messages.json"
        # Twilio uses HTTP basic auth + form-encoded body.
        import base64 as _b64
        auth = _b64.b64encode(f"{sid}:{tok}".encode()).decode()
        body = f"From={sender}&To={to}&Body={urllib.parse.quote(text)}"
        req = urllib.request.Request(
            url,
            data=body.encode("utf-8"),
            headers={"Authorization": f"Basic {auth}",
                     "Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with _safe_urlopen(req, timeout=6) as _:
                pass
        except Exception:
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
            # Stability fix (audit wave 29): require non-empty/non-
            # whitespace strings. The previous filter only checked
            # `isinstance(s, str)` — a single empty/whitespace entry
            # in suppress_terms makes `"" in q.lower()` always True
            # at line 289, dropping EVERY query and silently zeroing
            # the hunt. Empty entries also pollute winning/added
            # ordering. Trim and skip blanks.
            winning = [s.strip().lower() for s in (payload.get("winning_terms") or [])
                       if isinstance(s, str) and s.strip()]
            suppress = [s.strip().lower() for s in (payload.get("suppress_terms") or [])
                        if isinstance(s, str) and s.strip()]
            added = [s.strip() for s in (payload.get("added_queries") or [])
                     if isinstance(s, str) and s.strip()]
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
                # Case-insensitive list membership so a rule looking
                # for "Shopify" still matches a lead carrying
                # ["shopify"] (extracted text often lower-cases).
                _v_lc = str(value).lower()
                match = any(_v_lc == str(item).lower() for item in actual)
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
        # Same scheme defense as SlackPingPlugin — only http(s) accepted.
        if not _safe_outbound_url(url):
            return
        # Strip volatile / large fields the user doesn't need on the wire.
        # Webhook receivers usually want IDs + scoring + contact, not the
        # full email-rewrite history.
        slim = {k: v for k, v in (lead or {}).items()
                if k not in ("rewrite_history", "_full_text", "_site_text")}
        _ts = int(__import__("time").time())
        payload = {
            "event": "post_save",
            "lead": slim,
            "ts": _ts,
        }
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "huntova/generic-webhook",
        }
        secret = self._resolve_secret()
        if secret:
            # a419 fix (BRAIN-58): Stripe-style replay-safe signature.
            # Pre-fix the signature only covered the raw body — receivers
            # couldn't reliably reject replays without parsing the body
            # to inspect the embedded `ts` field. Now: the signed
            # material is `<unix_ts>.<body>` and the header carries
            # `t=<unix_ts>` separately so receivers can check freshness
            # against `time.time()` BEFORE decoding the JSON. The v1
            # signature scheme matches Stripe's webhook spec; existing
            # receivers using the legacy bare-sha256 form continue to
            # see the v1 value (just with a t= prefix). New receivers
            # should reject any t= that's more than ~5 minutes old.
            # Per GPT-5.4 audit on webhook replay class.
            import hmac as _hmac
            import hashlib as _hashlib
            signed_payload = f"{_ts}.".encode("utf-8") + body
            sig = _hmac.new(secret.encode(), signed_payload, _hashlib.sha256).hexdigest()
            headers["X-Huntova-Signature"] = f"t={_ts},v1={sig}"
            # Also keep a bare sha256 over the body alone as a
            # legacy header so existing receivers don't break on
            # this rollout. v1 is the new canonical form.
            legacy_sig = _hmac.new(secret.encode(), body, _hashlib.sha256).hexdigest()
            headers["X-Huntova-Signature-Legacy"] = f"sha256={legacy_sig}"
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with _safe_urlopen(req, timeout=8) as _:
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
    DiscordPingPlugin,
    TelegramPingPlugin,
    WhatsAppPingPlugin,
    GenericWebhookPlugin,
    RecipeAdaptationPlugin,
    AdaptationRulesPlugin,
)


def register_bundled(registry) -> list[str]:
    """Called by plugins.PluginRegistry.discover(). Skipped entirely
    if HV_DISABLE_BUNDLED_PLUGINS=1."""
    # a289 fix: explicit truthy parsing. Was bare truthiness — setting
    # `HV_DISABLE_BUNDLED_PLUGINS=0` (intent: don't disable) still
    # disabled because `"0"` is a non-empty string. Same for "false",
    # "no", "off". Now: only the documented values disable.
    if (os.environ.get("HV_DISABLE_BUNDLED_PLUGINS", "")
            .strip().lower() in ("1", "true", "yes", "on")):
        return []
    out: list[str] = []
    for cls in _BUNDLED_CLASSES:
        try:
            registry.register(cls())
            out.append(cls.name)
        except Exception:
            continue
    return out
