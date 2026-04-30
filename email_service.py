"""
Huntova SaaS — Email Service
Transactional emails via SMTP. All emails use the Huntova brand template.
"""
import asyncio
import html as _html
import smtplib
import ssl
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, SMTP_FROM_EMAIL, SMTP_FROM_NAME


def _esc(s: str) -> str:
    """Escape HTML entities to prevent injection in email templates."""
    return _html.escape(str(s)) if s else ""


def is_email_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def _send_email_sync(to: str, subject: str, html_body: str, plain_body: str = ""):
    """Send email via SMTP with HTML + plain text parts. Blocking — call via asyncio.to_thread."""
    msg = MIMEMultipart("alternative")
    msg["From"] = f"{SMTP_FROM_NAME} <{SMTP_FROM_EMAIL}>"
    msg["To"] = to
    msg["Subject"] = subject
    # List-Unsubscribe headers — RFC 2369 + RFC 8058. Gmail / Outlook
    # treat missing headers as a spam signal on bulk/transactional senders.
    # The mailto target is the From address so a reply reaches support;
    # List-Unsubscribe-Post announces one-click unsubscribe so clients
    # can surface a native 'Unsubscribe' button without the user typing.
    msg["List-Unsubscribe"] = f"<mailto:{SMTP_FROM_EMAIL}?subject=unsubscribe>"
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
    with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as server:
        server.starttls(context=_tls_context)
        server.login(SMTP_USER, SMTP_PASSWORD)
        server.sendmail(SMTP_FROM_EMAIL, to, msg.as_string())


async def send_email(to: str, subject: str, html_body: str, plain_body: str = ""):
    await asyncio.to_thread(_send_email_sync, to, subject, html_body, plain_body)


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
