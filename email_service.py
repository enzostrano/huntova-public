"""
Huntova SaaS — Email Service
Transactional emails via SMTP. All emails use the Huntova brand template.
"""
import asyncio
import os
import html as _html
import smtplib
import ssl
import threading as _threading
import time as _time
from collections import deque as _deque
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart


# a291 fix: per-hour outbound SMTP rate limit. Without one, a runaway
# `huntova sequence run` against thousands of leads fires them in a
# tight loop (~5/sec) and torches the user's domain reputation in a
# single run. Daily cap (`daily_send_cap` in settings) bounds a 24h
# window but doesn't pace within an hour. Default 30/hour matches the
# Google Workspace per-user soft cap; user can override via
# `smtp_hourly_send_cap` setting. Also enforces a min inter-message
# sleep so we don't accidentally fire 10 in 200ms.
_DEFAULT_HOURLY_CAP = 30
_DEFAULT_MIN_INTERVAL_S = 1.0  # 1s between messages
_SMTP_SEND_TIMES: _deque = _deque(maxlen=10000)
_SMTP_SEND_LOCK = _threading.Lock()


class SMTPRateLimitedError(RuntimeError):
    """Raised when the per-hour SMTP cap or min-interval gate triggers.
    Callers (cli_sequence, cli outreach send) should persist a "rate-
    limited, retry next window" status on the lead instead of marking
    the row as `errored` (which would imply a real send failure)."""


def _check_smtp_rate(settings: dict) -> None:
    """Block until safe to send — raise SMTPRateLimitedError if the
    hourly cap is hit. Called from `_send_email_sync` before SMTP
    connect so we don't waste handshake time on rejected sends."""
    try:
        cap = int(settings.get("smtp_hourly_send_cap") or _DEFAULT_HOURLY_CAP)
    except (TypeError, ValueError):
        cap = _DEFAULT_HOURLY_CAP
    if cap <= 0:
        cap = _DEFAULT_HOURLY_CAP
    try:
        interval = float(settings.get("smtp_min_send_interval_s") or _DEFAULT_MIN_INTERVAL_S)
    except (TypeError, ValueError):
        interval = _DEFAULT_MIN_INTERVAL_S
    now = _time.monotonic()
    with _SMTP_SEND_LOCK:
        # Drop sends older than 1 hour
        while _SMTP_SEND_TIMES and (now - _SMTP_SEND_TIMES[0]) > 3600:
            _SMTP_SEND_TIMES.popleft()
        if len(_SMTP_SEND_TIMES) >= cap:
            oldest = _SMTP_SEND_TIMES[0]
            wait = max(0, 3600 - (now - oldest))
            raise SMTPRateLimitedError(
                f"hourly SMTP cap reached ({cap}/h) — retry in ~{int(wait)}s")
        # Min-interval gate (in-process). Effective for one cli_sequence
        # run; doesn't bind across separate CLI invocations (those each
        # get a fresh _SMTP_SEND_TIMES). Acceptable: cron-driven runs
        # don't need pacing within a single batch unless > cap/hour.
        if _SMTP_SEND_TIMES and interval > 0:
            since_last = now - _SMTP_SEND_TIMES[-1]
            if since_last < interval:
                # Sleep just enough to clear the gap. Do this OUTSIDE
                # the lock so other threads aren't blocked.
                _wait = interval - since_last
                # release lock by appending the placeholder we WILL hit
                _SMTP_SEND_TIMES.append(now + _wait)
                _release = True
            else:
                _SMTP_SEND_TIMES.append(now)
                _release = False
        else:
            _SMTP_SEND_TIMES.append(now)
            _release = False
    # Sleep outside the lock so peers can probe the cap.
    if _release:
        _time.sleep(_wait)


def _smtp_settings():
    """Read SMTP env at call time so dashboard-saved settings (which
    are hydrated into env right before the call by cli.py / server.py)
    take effect even though `config.py` froze its module-level values
    at first import.
    """
    return {
        "host": os.environ.get("SMTP_HOST", ""),
        "port": int(os.environ.get("SMTP_PORT") or 587),
        "user": os.environ.get("SMTP_USER") or os.environ.get("HV_SMTP_USER", ""),
        # Accept both forms: HV_-prefixed (canonical for keychain bridge)
        # and bare (legacy, also what generic SMTP tooling uses).
        "password": (os.environ.get("SMTP_PASSWORD")
                     or os.environ.get("HV_SMTP_PASSWORD", "")),
        "from_email": os.environ.get("SMTP_FROM_EMAIL", "noreply@huntova.com"),
        "from_name": os.environ.get("SMTP_FROM_NAME", "Huntova"),
    }


def _esc(s: str) -> str:
    """Escape HTML entities to prevent injection in email templates."""
    return _html.escape(str(s)) if s else ""


def is_email_configured() -> bool:
    s = _smtp_settings()
    return bool(s["host"] and s["user"] and s["password"])


def _scrub_header(value: str, max_len: int = 998) -> str:
    """a289 fix + a292: strip CR/LF + control bytes from any string
    headed for an email header. Defends against header injection —
    AI-generated subjects (and worse, AI scrapings of hostile pages)
    used to land verbatim in `Subject:` / `To:` / `Reply-To:`. A
    subject containing `Hello\\r\\nBcc: attacker@x.com` would be
    folded into the SMTP DATA stream as a separate Bcc header and
    silently exfiltrate copies of every cold email to the attacker.
    Python's `email.message` library folds long lines but does NOT
    reject embedded CRLF.

    a292 fix: byte-cap not codepoint-cap. RFC 5322's 998-octet limit
    is bytes, not Python str codepoints. A multi-byte UTF-8 subject
    (Müller GmbH, 你好, emoji-heavy) sliced at codepoint 998 could
    encode to >998 bytes and be re-folded by `email.message` into a
    multi-line header — defeating the cap. Now: encode → byte-cap →
    decode with errors='ignore' (drops the partial trailing
    multi-byte sequence cleanly).
    """
    if value is None:
        return ""
    s = str(value)
    s = s.replace("\r", " ").replace("\n", " ")
    # strip C0 controls (0x00-0x1F) except TAB (0x09)
    s = "".join(c for c in s if c == "\t" or ord(c) >= 0x20)
    # a292: byte-cap (RFC 5322 is in octets). encode → slice → decode
    # with errors='ignore' so a partial multi-byte sequence at the
    # boundary just gets dropped rather than producing a UnicodeError.
    encoded = s.encode("utf-8")
    if len(encoded) > max_len:
        encoded = encoded[:max_len]
        s = encoded.decode("utf-8", errors="ignore")
    return s


def _send_email_sync(to: str, subject: str, html_body: str, plain_body: str = ""):
    """Send email via SMTP with HTML + plain text parts. Blocking — call via asyncio.to_thread.

    Returns the Message-ID we generated so callers (huntova outreach
    send) can persist it on the lead row. Reply-detection
    (`huntova inbox watch`) joins on this ID via the recipient's
    `In-Reply-To` / `References` headers.
    """
    s = _smtp_settings()
    # a291 fix: per-hour rate limit + min-interval gate. Raises
    # SMTPRateLimitedError when the cap is hit; callers should
    # persist a "rate-limited" status on affected leads.
    _check_smtp_rate(s)
    # a289 fix: body size cap. AI runaway / oversized HTML template
    # could produce a multi-megabyte body that the receiving MTA
    # rejects with 552 (sender reputation hit) or that we cram into
    # memory unbounded. 750 KB is well under the 10 MB MTA ceiling and
    # ample for any cold outreach.
    _BODY_CAP = 750_000
    if html_body and len(html_body) > _BODY_CAP:
        raise ValueError(f"HTML body exceeds {_BODY_CAP} bytes — refuse to send.")
    if plain_body and len(plain_body) > _BODY_CAP:
        raise ValueError(f"Plain body exceeds {_BODY_CAP} bytes — refuse to send.")
    # a289 fix: validate `to` address shape — `parseaddr` rejects
    # CRLF embedded in the local-part, returns ('','') for unparseable.
    from email.utils import formataddr, parseaddr
    _to_name, _to_addr = parseaddr(to or "")
    if not _to_addr or "\r" in _to_addr or "\n" in _to_addr:
        raise ValueError(f"Invalid recipient address: {to!r}")
    msg = MIMEMultipart("alternative")
    # Use formataddr so display names with commas / quotes / non-ASCII
    # ("Smith, John", "Müller GmbH") become RFC 2822-compliant headers
    # automatically. Manual `f'{name} <{addr}>'` interpolation breaks
    # both Outlook and Gmail when the name contains punctuation.
    msg["From"] = formataddr((_scrub_header(s.get("from_name") or "", 200),
                              s.get("from_email") or ""))
    # a289 fix: scrub To + Subject for header injection. AI-generated
    # subjects + lead-derived recipients used to land raw.
    msg["To"] = formataddr((_scrub_header(_to_name, 200), _to_addr))
    msg["Subject"] = _scrub_header(subject, 998)
    # Generate our own Message-ID so we can persist it before send.
    # `email.utils.make_msgid` produces an RFC-2822 compliant ID with
    # a hostname derived from the From address — replies reference
    # this verbatim in their `In-Reply-To` header.
    from email.utils import make_msgid
    _domain = (s.get("from_email") or "huntova.local").split("@")[-1] or "huntova.local"
    msg_id = make_msgid(domain=_domain)
    msg["Message-ID"] = msg_id
    # List-Unsubscribe headers — RFC 2369 + RFC 8058. Gmail / Outlook
    # treat missing headers as a spam signal on bulk/transactional senders.
    msg["List-Unsubscribe"] = f"<mailto:{s['from_email']}?subject=unsubscribe>"
    msg["List-Unsubscribe-Post"] = "List-Unsubscribe=One-Click"
    # Plain text first (fallback), HTML second (preferred)
    if plain_body:
        msg.attach(MIMEText(plain_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))
    # Stability fix (Perplexity bug #56): starttls() without an
    # explicit SSL context relied on Python's stdlib defaults which
    # don't reliably verify the SMTP server cert/hostname. A MITM on
    # the SMTP path could intercept SMTP_USER + SMTP_PASSWORD on
    # login. ssl.create_default_context() loads the system trust
    # store and enforces cert + hostname verification.
    _tls_context = ssl.create_default_context()
    # a292: surface SMTP error codes so callers can persist
    # `bounced=true` / `smtp_error_code=5xx` on the lead row instead
    # of treating every failure as a generic exception. Bounces (5xx)
    # mark the lead permanently; deferrals (4xx) are retryable.
    try:
        if int(s["port"]) == 465:
            with smtplib.SMTP_SSL(s["host"], s["port"], context=_tls_context, timeout=15) as server:
                server.login(s["user"], s["password"])
                server.sendmail(s["from_email"], to, msg.as_string())
        else:
            with smtplib.SMTP(s["host"], s["port"], timeout=15) as server:
                server.starttls(context=_tls_context)
                server.login(s["user"], s["password"])
                server.sendmail(s["from_email"], to, msg.as_string())
    except smtplib.SMTPRecipientsRefused as e:
        # All recipients rejected — pull the first error code/msg.
        first = next(iter(e.recipients.values()), (0, b""))
        code, smsg = (first[0] if isinstance(first, tuple) else 0,
                      (first[1] if isinstance(first, tuple) else b"").decode("utf-8", "replace"))
        raise SMTPDeliveryError(code, smsg, permanent=(500 <= code < 600)) from e
    except smtplib.SMTPDataError as e:
        # 5xx after DATA — typically a content-policy reject.
        raise SMTPDeliveryError(getattr(e, "smtp_code", 0),
                                (getattr(e, "smtp_error", b"") or b"").decode("utf-8", "replace"),
                                permanent=True) from e
    except smtplib.SMTPSenderRefused as e:
        # From: rejected — sender reputation, SPF, etc.
        raise SMTPDeliveryError(getattr(e, "smtp_code", 0),
                                (getattr(e, "smtp_error", b"") or b"").decode("utf-8", "replace"),
                                permanent=True) from e
    return msg_id


class SMTPDeliveryError(RuntimeError):
    """a292: typed SMTP failure carrying a code + permanent flag so
    callers can decide whether to mark the lead bounced / retry /
    skip. `code` is the SMTP numeric reply (e.g. 550); `permanent` is
    True for 5xx (don't retry) and False for 4xx (defer)."""
    def __init__(self, code: int, message: str, permanent: bool):
        super().__init__(f"SMTP {code}: {message}")
        self.code = code
        self.message = message
        self.permanent = permanent


async def send_email(to: str, subject: str, html_body: str, plain_body: str = ""):
    return await asyncio.to_thread(_send_email_sync, to, subject, html_body, plain_body)


# ── Brand Template ──

def _template(title: str, preheader: str, body_html: str, button_text: str = "", button_url: str = "") -> str:
    """Generate branded HTML email. Inline CSS for maximum email client compatibility."""
    btn = ""
    if button_text and button_url:
        btn = f'''<table role="presentation" style="margin:28px auto 0"><tr><td>
            <a href="{button_url}" style="display:inline-block;padding:14px 36px;background:#36dfc4;color:#070a0e;border-radius:8px;font-weight:700;font-size:14px;text-decoration:none;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif">{button_text}</a>
        </td></tr></table>'''

    return f'''<!DOCTYPE html>
<html lang="en">
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="dark"><meta name="supported-color-schemes" content="dark">
<!--[if mso]><style>*{{font-family:Arial,sans-serif!important}}</style><![endif]-->
</head>
<body style="margin:0;padding:0;background:#07080c;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;-webkit-text-size-adjust:none">
<!-- Preheader (hidden preview text) -->
<div style="display:none;max-height:0;overflow:hidden;mso-hide:all">{preheader}</div>
<table role="presentation" width="100%" style="background:#07080c;padding:40px 16px">
<tr><td align="center">
<table role="presentation" width="480" style="max-width:480px;width:100%;background:#0d0f15;border:1px solid rgba(120,140,220,.06);border-radius:12px;overflow:hidden">

<!-- Logo -->
<tr><td style="padding:32px 32px 0;text-align:center">
    <span style="font-family:'Courier New',monospace;font-size:20px;font-weight:700;color:#eceef6;letter-spacing:-0.5px">Hunt</span><span style="font-family:'Courier New',monospace;font-size:20px;font-weight:700;color:#36dfc4;letter-spacing:-0.5px">ova</span>
</td></tr>

<!-- Title -->
<tr><td style="padding:24px 32px 0;text-align:center">
    <h1 style="margin:0;font-size:22px;font-weight:700;color:#eceef6;line-height:1.3;letter-spacing:-0.3px">{title}</h1>
</td></tr>

<!-- Body -->
<tr><td style="padding:20px 32px 0;text-align:center;color:#7d86a8;font-size:15px;line-height:1.7">
    {body_html}
</td></tr>

<!-- Button -->
<tr><td style="padding:0 32px 32px;text-align:center">
    {btn}
</td></tr>

<!-- Footer -->
<tr><td style="padding:20px 32px;border-top:1px solid rgba(120,140,220,.06);text-align:center">
    <p style="margin:0;font-size:11px;color:#4a5272;line-height:1.6">
        Huntova &mdash; AI-powered B2B lead generation<br>
        This is a transactional email from your Huntova account.
    </p>
</td></tr>

</table>
</td></tr></table>
</body></html>'''


def _plain(title: str, body: str, button_text: str = "", button_url: str = "") -> str:
    """Generate plain text version of the email."""
    lines = [f"HUNTOVA\n", f"{title}\n", body]
    if button_text and button_url:
        lines.append(f"\n{button_text}: {button_url}")
    lines.append("\n---\nHuntova — AI-powered B2B lead generation")
    return "\n".join(lines)


# ── Transactional Emails ──

async def send_verification_email(to: str, token: str, base_url: str):
    url = f"{base_url}/auth/verify-email?token={token}"
    html = _template(
        "Verify your email",
        "Verify your email to activate your Huntova account",
        "Confirm your email address to start using Huntova. Your AI lead generation agent is ready to find qualified prospects for your business.",
        "Verify Email Address", url
    )
    plain = _plain(
        "Verify your email",
        "Confirm your email address to start using Huntova.",
        "Verify Email", url
    )
    await send_email(to, "Verify your Huntova account", html, plain)


async def send_password_reset_email(to: str, token: str, base_url: str):
    url = f"{base_url}/auth/reset-password?token={token}"
    html = _template(
        "Reset your password",
        "Reset your Huntova password — this link expires in 1 hour",
        "We received a request to reset your password. Click below to choose a new one. This link expires in 1 hour.<br><br>"
        "<span style='font-size:12px;color:#4a5272'>If you didn't request this, you can safely ignore this email.</span>",
        "Reset Password", url
    )
    plain = _plain(
        "Reset your password",
        "We received a request to reset your Huntova password.\nThis link expires in 1 hour. If you didn't request this, ignore this email.",
        "Reset Password", url
    )
    await send_email(to, "Reset your Huntova password", html, plain)


async def send_welcome_email(to: str, name: str, base_url: str):
    """Sent after first signup + verification."""
    safe_name = _esc(name) or "there"
    html = _template(
        f"Welcome to Huntova, {safe_name}",
        f"Welcome to Huntova — your AI lead agent is ready",
        f"You're in. Huntova is your AI-powered B2B lead generation agent.<br><br>"
        f"<b>Here's how to get started:</b><br><br>"
        f"<table role='presentation' style='text-align:left;margin:0 auto'>"
        f"<tr><td style='padding:6px 0;color:#7d86a8;font-size:14px'><span style='color:#36dfc4;font-weight:700'>1.</span> Complete the setup wizard — tell Huntova about your business</td></tr>"
        f"<tr><td style='padding:6px 0;color:#7d86a8;font-size:14px'><span style='color:#36dfc4;font-weight:700'>2.</span> Click Start — the agent searches the web for qualified prospects</td></tr>"
        f"<tr><td style='padding:6px 0;color:#7d86a8;font-size:14px'><span style='color:#36dfc4;font-weight:700'>3.</span> Review leads in your CRM — each comes with a personalized email draft</td></tr>"
        f"</table><br>"
        f"You have <b>5 free leads</b> to try it out. No credit card required.",
        "Open Dashboard", base_url
    )
    plain = _plain(
        f"Welcome to Huntova, {name}",
        "You're in. Here's how to get started:\n"
        "1. Complete the setup wizard — tell Huntova about your business\n"
        "2. Click Start — the agent searches the web for qualified prospects\n"
        "3. Review leads in your CRM — each comes with a personalized email draft\n\n"
        "You have 5 free leads to try it out.",
        "Open Dashboard", base_url
    )
    await send_email(to, "Welcome to Huntova", html, plain)


async def send_agent_complete_email(to: str, data: dict, base_url: str):
    """Sent when an agent run finishes."""
    leads = data.get("leads_found", 0)
    hot = data.get("hot_leads", 0)
    credits_remaining = data.get("credits_remaining", 0)

    if leads == 0:
        summary = "Your agent completed a run but didn't find any qualifying leads this time. Try adjusting your business profile or running again with different countries."
    else:
        summary = (
            f"Your agent just finished a run and found <b>{leads}</b> new lead{'s' if leads != 1 else ''}."
        )
        if hot > 0:
            summary += f" <span style='color:#36dfc4'>{hot} scored 9+</span> — check those first."
        summary += f"<br><br>You have <b>{credits_remaining}</b> credits remaining."

    html = _template(
        f"{leads} new lead{'s' if leads != 1 else ''} found",
        f"Huntova found {leads} new leads for you",
        summary,
        "View Leads", base_url
    )
    plain = _plain(
        f"{leads} new leads found",
        f"Your agent found {leads} new leads. You have {credits_remaining} credits remaining.",
        "View Leads", base_url
    )
    await send_email(to, f"Huntova: {leads} new lead{'s' if leads != 1 else ''} found", html, plain)


async def send_credits_low_email(to: str, credits_remaining: int, tier: str, base_url: str):
    """Sent when credits drop below 20% of tier allocation."""
    html = _template(
        "Credits running low",
        f"You have {credits_remaining} Huntova credits remaining",
        f"You have <b>{credits_remaining}</b> lead credits remaining on your <b>{tier.title()}</b> plan.<br><br>"
        f"When credits run out, your agent pauses until they refill next month or you top up.<br><br>"
        f"Top up now to keep your pipeline running without interruption.",
        "Top Up Credits", f"{base_url}/#pricing"
    )
    plain = _plain(
        "Credits running low",
        f"You have {credits_remaining} lead credits remaining on your {tier.title()} plan.\n"
        f"Top up to keep your pipeline running.",
        "Top Up Credits", f"{base_url}/#pricing"
    )
    await send_email(to, f"Huntova: {credits_remaining} credits remaining", html, plain)


async def send_subscription_confirmed_email(to: str, tier: str, credits: int, base_url: str):
    """Sent after successful subscription checkout."""
    html = _template(
        f"You're on the {tier.title()} plan",
        f"Your Huntova {tier.title()} subscription is active",
        f"Your <b>{tier.title()}</b> plan is now active. Here's what you get:<br><br>"
        f"<table role='presentation' style='text-align:left;margin:0 auto'>"
        f"<tr><td style='padding:4px 0;color:#7d86a8;font-size:14px'><span style='color:#36dfc4'>&#10003;</span> <b>{credits}</b> leads per month</td></tr>"
        f"<tr><td style='padding:4px 0;color:#7d86a8;font-size:14px'><span style='color:#36dfc4'>&#10003;</span> {'Gemini Pro AI (premium scoring)' if tier == 'agency' else 'AI-powered lead scoring'}</td></tr>"
        f"<tr><td style='padding:4px 0;color:#7d86a8;font-size:14px'><span style='color:#36dfc4'>&#10003;</span> Personalized email drafts</td></tr>"
        f"<tr><td style='padding:4px 0;color:#7d86a8;font-size:14px'><span style='color:#36dfc4'>&#10003;</span> {'Deep Research' if tier == 'agency' else 'AI Chat + Email Rewrite'}</td></tr>"
        f"</table><br>"
        f"Your credits refill automatically each billing cycle.",
        "Start Hunting", base_url
    )
    plain = _plain(
        f"You're on the {tier.title()} plan",
        f"Your {tier.title()} plan is active with {credits} leads per month.",
        "Start Hunting", base_url
    )
    await send_email(to, f"Huntova {tier.title()} plan activated", html, plain)


async def send_weekly_summary(to: str, data: dict, base_url: str):
    """Weekly pipeline summary email."""
    new_leads = data.get("new_leads", 0)
    action_count = data.get("action_count", 0)
    credits = data.get("credits", 0)

    parts = []
    if new_leads > 0:
        parts.append(f"<b>{new_leads}</b> new leads were found this week.")
    else:
        parts.append("No new leads this week. Run your agent to keep your pipeline flowing.")

    if action_count > 0:
        parts.append(f"You have <b>{action_count}</b> leads waiting for action — hot leads to contact and follow-ups to send.")

    if credits <= 3:
        parts.append(f"You have <b>{credits}</b> credits remaining. <a href='{base_url}/#pricing' style='color:#36dfc4'>Top up</a> to keep your pipeline running.")

    body_text = "<br><br>".join(parts)

    html = _template(
        "Your weekly pipeline update",
        f"{new_leads} new leads this week on Huntova",
        body_text,
        "Open Dashboard", base_url
    )
    plain = _plain(
        "Your weekly pipeline update",
        f"{'%d new leads this week.' % new_leads if new_leads else 'No new leads this week.'}\n"
        f"{'%d leads waiting for action.' % action_count if action_count else ''}\n"
        f"Credits remaining: {credits}",
        "Open Dashboard", base_url
    )
    await send_email(to, f"Huntova — {new_leads} new leads this week", html, plain)
