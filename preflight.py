"""
Centralized preflight checks for Huntova features.

This module exists because the same UX bug appeared three times across
the codebase:

  1. User runs `huntova hunt` with no AI provider key — silent crash
     deep inside the agent loop, no actionable message.
  2. User triggers "Run hunt" from the chat — start_hunt returns
     `wizard_missing` action, but only for ICP, not for AI provider
     or SMTP.
  3. User sends `huntova outreach send` with no SMTP — fails late at
     send time, after the prompt has already burned tokens.

Each entry point grew its own bespoke check, with inconsistent UX.
This module replaces all of that with one source of truth:

  validate_action(user_id, "hunt") -> {
      "ok": False,
      "missing": [
          {
              "key": "ai_provider",
              "label": "AI provider key",
              "reason": "No API key configured for any provider.",
              "nav_url": "/setup#providers",
              "nav_label": "Add provider key",
          },
          ...
      ],
  }

Both the chat dispatcher (server.py /api/chat) and every CLI entry
point (cli.py cmd_hunt, cmd_research, cmd_outreach, cli_inbox.cmd_watch
etc.) call this before doing any work. On non-empty `missing`, the
chat UI renders clickable cards and the CLI prints a list with
fill-in URLs.

Add a new feature? Add one line to FEATURE_REQUIREMENTS and you're
done — preflight is automatic.
"""
from __future__ import annotations

import os
from enum import Enum
from typing import Any, Callable


class RequirementKey(str, Enum):
    """Discrete things a Huntova feature can require."""

    # AI provider with a configured key (any of the 13 providers, or
    # an unkeyed local server like Ollama — providers.py handles the
    # priority resolution).
    AI_PROVIDER = "ai_provider"

    # ICP profile saved (target customer profile + scoring axes). Set
    # by the onboard wizard; without it the Hunter has nothing to
    # search for.
    ICP_PROFILE = "icp_profile"

    # SMTP credentials (host, port, user, password, from_email).
    # Required for any feature that actually *sends* email.
    SMTP = "smtp"

    # IMAP credentials (host, port, user, password). Required for
    # inbox watch / reply classification.
    IMAP = "imap"

    # Sending domain configured (used by `huntova doctor --email` to
    # check SPF/DKIM/DMARC). Optional for outreach but required for
    # the deliverability pre-flight feature.
    SENDER_DOMAIN = "sender_domain"

    # At least one lead in the local DB. Required for sequence_run,
    # research-by-id, outreach send (you can't send to nobody).
    LEADS_EXIST = "leads_exist"


# ── Per-feature requirements registry ─────────────────────────────────────
#
# Adding a new feature: add one line. Action slugs match what the chat
# dispatcher uses (`server.py /api/chat`) and what the CLI subcommands
# pass when calling validate_action() at startup.
#
# `dry_run=True` is a hint some features use to relax requirements. For
# example, `outreach send --dry-run` doesn't need SMTP because nothing
# is actually sent. Each feature decides which requirements to drop in
# dry-run mode via the dynamic builder below.

_BASE_REQUIREMENTS: dict[str, list[RequirementKey]] = {
    # Prospect discovery — needs an AI provider to write queries + score
    # results, and an ICP profile to know what "good" looks like.
    "hunt": [RequirementKey.AI_PROVIDER, RequirementKey.ICP_PROFILE],

    # Deep research on a specific lead — needs an AI provider to
    # summarize crawled content and draft openers.
    "research": [RequirementKey.AI_PROVIDER],

    # Compose drafts for a lead — same as research.
    "compose": [RequirementKey.AI_PROVIDER],

    # Cold email cadence runner. Live sends need SMTP + leads. Dry-run
    # variants relax SMTP via the dynamic builder.
    "sequence_run": [RequirementKey.SMTP, RequirementKey.LEADS_EXIST],

    # One-shot send. Same shape as sequence_run.
    "outreach_send": [RequirementKey.SMTP, RequirementKey.LEADS_EXIST],

    # Reply classification — needs IMAP creds + an AI provider to
    # classify each reply.
    "inbox_check": [RequirementKey.IMAP, RequirementKey.AI_PROVIDER],
    "inbox_watch": [RequirementKey.IMAP, RequirementKey.AI_PROVIDER],

    # Deliverability pre-flight — needs the sending domain.
    "doctor_email": [RequirementKey.SENDER_DOMAIN],

    # Self-coaching pulse — needs leads to summarize over.
    "pulse": [RequirementKey.LEADS_EXIST, RequirementKey.AI_PROVIDER],

    # Teach (mark good/bad) — needs leads and an AI provider to
    # re-fit the ICP scorer.
    "teach": [RequirementKey.LEADS_EXIST, RequirementKey.AI_PROVIDER],

    # Plain chat — only needs an AI provider; no business state.
    "chat": [RequirementKey.AI_PROVIDER],
}


def _requirements_for(action: str, *, dry_run: bool = False) -> list[RequirementKey]:
    """Return the requirement list for an action, with dry-run relaxations
    applied. New features that need different dry-run logic add a branch
    here rather than maintaining two parallel registries."""
    base = list(_BASE_REQUIREMENTS.get(action, []))
    if dry_run:
        # Dry-run sends and sequence runs don't actually touch SMTP, so
        # we can safely skip that check. They still need leads to draft
        # against, so LEADS_EXIST stays.
        if action in ("sequence_run", "outreach_send"):
            base = [r for r in base if r != RequirementKey.SMTP]
    return base


# ── Per-requirement metadata + probe functions ────────────────────────────
#
# A "probe" answers the question "is requirement X satisfied for user U?"
# It returns either None (satisfied) or a string explaining why not.
# Probes are pure-read — never mutate state.
#
# `nav_url` is the dashboard route that lets the user fix the gap. The
# chat UI uses it for clickable cards; the CLI prints it as a hyperlink.

_REQUIREMENT_META: dict[RequirementKey, dict[str, str]] = {
    RequirementKey.AI_PROVIDER: {
        "label": "AI provider key",
        "nav_url": "/setup#providers",
        "nav_label": "Add provider key",
    },
    RequirementKey.ICP_PROFILE: {
        "label": "Target customer profile (ICP)",
        "nav_url": "/setup#icp",
        "nav_label": "Set up ICP",
    },
    RequirementKey.SMTP: {
        "label": "SMTP credentials",
        "nav_url": "/setup#smtp",
        "nav_label": "Connect outbound email",
    },
    RequirementKey.IMAP: {
        "label": "IMAP credentials",
        "nav_url": "/setup#imap",
        "nav_label": "Connect inbox",
    },
    RequirementKey.SENDER_DOMAIN: {
        "label": "Sending domain",
        "nav_url": "/setup#smtp",
        "nav_label": "Set sender domain",
    },
    RequirementKey.LEADS_EXIST: {
        "label": "At least one lead in your database",
        "nav_url": "/leads",
        "nav_label": "Run a hunt to add leads",
    },
}


def _probe_ai_provider(user_id: int | None) -> str | None:
    """None if any provider has a usable key (or an unkeyed local
    provider override). Returns a human-readable reason otherwise."""
    try:
        from providers import list_available_providers, _LOCAL_PROVIDERS
        # Pull user settings if available — preserves cloud-mode
        # per-user config. None means providers.py falls back to env +
        # secrets store, which is correct for CLI invocations.
        settings = _user_settings_safe(user_id)
        configured = list_available_providers(settings)
        if configured:
            return None
        # No keyed provider — but a local server (Ollama / LM Studio /
        # llamafile) being up counts too. Detect via the same probe
        # the wizard uses; this is now sub-100ms thanks to the cache
        # in providers.detect_local_servers().
        try:
            from providers import detect_local_servers
            detected = detect_local_servers()
            if any(info.get("available") for info in detected.values()):
                return None
        except Exception:
            pass
        return ("No AI provider configured. Add a key (Claude, OpenAI, "
                "Gemini, OpenRouter, etc.) or start a local server "
                "(Ollama, LM Studio, llamafile).")
    except Exception as e:
        # Never let preflight itself raise — that would block features
        # whose requirement check is broken, which is worse than letting
        # them try and fail naturally.
        return f"AI provider check failed: {type(e).__name__}"


def _probe_icp_profile(user_id: int | None) -> str | None:
    settings = _user_settings_safe(user_id)
    if not settings:
        return "No ICP profile saved. Run the onboard wizard."
    icp = settings.get("icp") or settings.get("icp_profile") or {}
    if isinstance(icp, dict) and (icp.get("description") or icp.get("axes")):
        return None
    return "No ICP profile saved. Set your target customer profile."


def _probe_smtp(user_id: int | None) -> str | None:
    settings = _user_settings_safe(user_id)
    smtp = (settings or {}).get("smtp") or {}
    # Required fields per email_service.py / cli_outreach.py.
    required = ("host", "user", "password", "from_email")
    missing = [f for f in required if not smtp.get(f)]
    if not missing:
        return None
    # Env-var fallback — same priority order as the real send path.
    if os.environ.get("HV_SMTP_HOST") and os.environ.get("HV_SMTP_USER"):
        return None
    return f"SMTP not configured. Missing: {', '.join(missing)}."


def _probe_imap(user_id: int | None) -> str | None:
    settings = _user_settings_safe(user_id)
    imap = (settings or {}).get("imap") or {}
    required = ("host", "user", "password")
    missing = [f for f in required if not imap.get(f)]
    if not missing:
        return None
    if os.environ.get("HV_IMAP_HOST") and os.environ.get("HV_IMAP_USER"):
        return None
    return f"IMAP not configured. Missing: {', '.join(missing)}."


def _probe_sender_domain(user_id: int | None) -> str | None:
    settings = _user_settings_safe(user_id)
    smtp = (settings or {}).get("smtp") or {}
    from_email = smtp.get("from_email") or os.environ.get("HV_FROM_EMAIL") or ""
    if "@" in from_email:
        return None
    return "No sending domain set (need a from_email in SMTP config)."


def _probe_leads_exist(user_id: int | None) -> str | None:
    """Pure DB read — count leads in the user's local database."""
    try:
        # Lazy import — db module may not be available in all contexts
        # (e.g. when validate_action is called from a unit test that
        # imported preflight standalone).
        import db
        n = db.count_leads(user_id) if user_id is not None else db.count_leads()
        if n and n > 0:
            return None
        return "No leads in your database yet. Run a hunt first."
    except Exception:
        # If the db module doesn't expose count_leads (older shape) or
        # import fails, fall through with a permissive default —
        # better to let downstream code complain than to block on a
        # broken probe.
        return None


_PROBES: dict[RequirementKey, Callable[[int | None], str | None]] = {
    RequirementKey.AI_PROVIDER:    _probe_ai_provider,
    RequirementKey.ICP_PROFILE:    _probe_icp_profile,
    RequirementKey.SMTP:           _probe_smtp,
    RequirementKey.IMAP:           _probe_imap,
    RequirementKey.SENDER_DOMAIN:  _probe_sender_domain,
    RequirementKey.LEADS_EXIST:    _probe_leads_exist,
}


# ── User settings cache — single read per validate_action() call ──────────


def _user_settings_safe(user_id: int | None) -> dict[str, Any]:
    """Best-effort read of the user's settings dict. Returns {} on any
    failure so probes degrade to a safe "missing" verdict rather than
    raising."""
    try:
        if user_id is None:
            # CLI / local-mode path — let providers.py read from
            # config.toml + env directly. Returning {} here means the
            # downstream probes use env-var fallbacks.
            return {}
        import db
        settings = db.get_user_settings(user_id) if hasattr(db, "get_user_settings") else None
        if isinstance(settings, dict):
            return settings
        return {}
    except Exception:
        return {}


# ── Public API ────────────────────────────────────────────────────────────


def validate_action(
    action: str,
    *,
    user_id: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Check whether `action` can run for `user_id` right now.

    Returns:
        {
            "ok": True | False,
            "action": <action>,
            "missing": [
                {"key": str, "label": str, "reason": str,
                 "nav_url": str, "nav_label": str},
                ...
            ],  # empty when ok=True
        }

    Unknown action slugs return {ok: True, missing: []} — preflight
    fails open. The expectation is that adding a new feature also adds
    its requirement list to FEATURE_REQUIREMENTS; until then, the
    feature runs without preflight rather than being silently blocked.

    `dry_run=True` relaxes specific requirements (e.g. `outreach_send`
    doesn't need SMTP if nothing is actually sent). The relaxations
    are encoded in `_requirements_for()`.

    Pure read — never mutates state. Safe to call on every chat
    message and every CLI invocation.
    """
    requirements = _requirements_for(action, dry_run=dry_run)
    missing: list[dict[str, Any]] = []
    for req in requirements:
        probe = _PROBES.get(req)
        if probe is None:
            # No probe wired for this requirement — register a clear
            # error so the developer adding the requirement sees it
            # in dev rather than silently passing in prod.
            missing.append({
                "key": req.value,
                "label": req.value,
                "reason": f"no probe registered for requirement '{req.value}' — bug in preflight.py",
                "nav_url": "/setup",
                "nav_label": "Open setup",
            })
            continue
        reason = probe(user_id)
        if reason is None:
            continue
        meta = _REQUIREMENT_META.get(req, {})
        missing.append({
            "key": req.value,
            "label": meta.get("label") or req.value,
            "reason": reason,
            "nav_url": meta.get("nav_url") or "/setup",
            "nav_label": meta.get("nav_label") or "Open setup",
        })
    return {
        "ok": len(missing) == 0,
        "action": action,
        "missing": missing,
    }


def format_missing_for_cli(missing: list[dict[str, Any]], dashboard_url: str = "http://localhost:5050") -> str:
    """Render `missing` as a multi-line string suitable for stderr in
    CLI commands. CLI callers print this and exit non-zero when
    validate_action() returned ok=False."""
    if not missing:
        return ""
    lines = ["", "Can't run this yet — the following settings are missing:", ""]
    for m in missing:
        lines.append(f"  • {m['label']}")
        lines.append(f"      {m['reason']}")
        lines.append(f"      Fix: {dashboard_url}{m['nav_url']}")
        lines.append("")
    lines.append(f"Open the dashboard ({dashboard_url}) and complete the items above, then re-run.")
    lines.append("")
    return "\n".join(lines)
