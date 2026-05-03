"""huntova inbox — IMAP reply detection.

Polls the user's IMAP mailbox and matches incoming messages to outbound
sends Huntova made via `huntova outreach send`. When a reply is detected,
the matching lead's `email_status` flips to `replied`, an entry is
appended to `status_history`, and the agent's adaptive-learning loop
treats it as positive feedback on whatever opener landed.

Wiring (from `cli.py`):
    from cli_inbox import register
    register(subparsers)

Subcommands:
    huntova inbox setup      # save IMAP creds to OS keychain
    huntova inbox check      # one-shot poll, print N replies found
    huntova inbox watch      # daemon loop, polls every --interval seconds

State on disk: nothing extra. Outbound message_id sits on the lead's
JSON `data` blob under `_message_id` and `_sent_at`. Reply matching
uses the recipient's `In-Reply-To` / `References` headers + a fallback
`Subject:` heuristic for clients that strip thread headers.
"""

from __future__ import annotations

import argparse
import asyncio
import imaplib
import os
import re
import ssl
import sys
import time
from datetime import datetime, timezone
from email import message_from_bytes
from email.utils import getaddresses, parseaddr


def _bold(s: str) -> str: return f"\033[1m{s}\033[0m"
def _dim(s: str) -> str: return f"\033[2m{s}\033[0m"
def _green(s: str) -> str: return f"\033[32m{s}\033[0m"
def _red(s: str) -> str: return f"\033[31m{s}\033[0m"
def _yellow(s: str) -> str: return f"\033[33m{s}\033[0m"


# ── credentials helpers ─────────────────────────────────────────────

_KEY_HOST = "HV_IMAP_HOST"
_KEY_PORT = "HV_IMAP_PORT"
_KEY_USER = "HV_IMAP_USER"
_KEY_PASS = "HV_IMAP_PASSWORD"


def _load_imap_settings() -> dict | None:
    """Resolve IMAP creds from env first, then OS keychain. None if
    nothing is configured — callers print a friendly setup hint."""
    host = os.environ.get(_KEY_HOST)
    user = os.environ.get(_KEY_USER)
    port = os.environ.get(_KEY_PORT)
    password = os.environ.get(_KEY_PASS)
    if not (host and user and password):
        try:
            from secrets_store import get_secret
            host = host or get_secret(_KEY_HOST)
            user = user or get_secret(_KEY_USER)
            port = port or get_secret(_KEY_PORT)
            password = password or get_secret(_KEY_PASS)
        except Exception:
            pass
    if not (host and user and password):
        return None
    return {
        "host": host,
        "port": int(port or 993),
        "user": user,
        "password": password,
    }


# ── IMAP connection ─────────────────────────────────────────────────

def _connect(s: dict) -> imaplib.IMAP4_SSL:
    ctx = ssl.create_default_context()
    cli = imaplib.IMAP4_SSL(s["host"], s["port"], ssl_context=ctx, timeout=20)
    try:
        cli.login(s["user"], s["password"])
    except imaplib.IMAP4.error as e:
        # imaplib.error includes the server's response which can hint at
        # the failure (LOGIN failed / Invalid credentials), but we don't
        # want a stack trace dumped at the user. Wrap into a friendly
        # message; the password isn't in `e` (only server text is) but
        # the trace would be alarming.
        raise RuntimeError(f"IMAP login failed: {str(e)[:120]}") from None
    return cli


# ── thread-header parsing ───────────────────────────────────────────

_MID_RE = re.compile(r"<([^>]+)>")


def _split_msgids(value: str) -> list[str]:
    """Pull every `<id>` from an `In-Reply-To` or `References` header.
    Both can hold multiple IDs; clients delimit by whitespace."""
    if not value:
        return []
    return [m.group(1).strip() for m in _MID_RE.finditer(value)]


# ── outbound index ──────────────────────────────────────────────────

async def _build_outbound_index(user_id: int) -> tuple[dict, dict]:
    """Walk the user's leads once, return:
    - {message_id: lead_id} for direct In-Reply-To matching
    - {sender_email_lower: lead_id} as a from-address fallback for
      clients that strip threading headers (a few mobile clients still
      do; "Reply" lands without In-Reply-To).
    """
    import db as _db
    leads = await _db.get_leads(user_id, limit=2000)
    by_msgid: dict[str, str] = {}
    by_email: dict[str, str] = {}
    for ld in leads or []:
        lid = ld.get("lead_id")
        mid = (ld.get("_message_id") or "").strip()
        em = (ld.get("contact_email") or "").strip().lower()
        if mid and lid:
            # `lstrip("<").rstrip(">")` is a character-set strip, not
            # a substring strip. `<a<b>` would land as `a<b`. The
            # canonical RFC-2822 message-id is `<local@host>` so a
            # set-strip of the literal angle-bracket pair is what we
            # want — but we use `.strip("<>")` to make that explicit.
            # RFC 2822 §3.6.4 treats the Message-ID's domain part as
            # case-insensitive; many clients preserve case exactly when
            # threading but a few normalise it. Lowercasing here +
            # at the lookup site avoids the threading miss.
            by_msgid[mid.strip("<>").lower()] = lid
        if em and lid and em not in by_email:
            by_email[em] = lid
    return by_msgid, by_email


# ── reply matcher ───────────────────────────────────────────────────

def _decode_payload(raw: bytes | None, declared_charset: str | None) -> str:
    """Best-effort body decode. Replies from European inboxes often arrive
    without a Content-Type charset header on a single-byte encoding (e.g.
    cp1252 / iso-8859-1). UTF-8 with errors='replace' renders accented
    characters as `�`, which then break the AI classifier. Try the
    declared charset first; if that fails or wasn't declared, fall back
    to cp1252 (covers most Western-European mail) and finally UTF-8.
    """
    if not raw:
        return ""
    candidates = []
    if declared_charset:
        candidates.append(declared_charset)
    candidates.extend(["utf-8", "cp1252", "latin-1"])
    seen: set[str] = set()
    for cs in candidates:
        cs_norm = (cs or "").lower()
        if not cs_norm or cs_norm in seen:
            continue
        seen.add(cs_norm)
        try:
            return raw.decode(cs_norm)
        except (UnicodeDecodeError, LookupError):
            continue
    # Final fallback — replace bad bytes; better than crashing the loop.
    return raw.decode("utf-8", errors="replace")


def _is_autoreply(subject: str, headers: list[tuple[str, str]]) -> bool:
    """RFC 3834 + common heuristic. We do NOT mark these as 'replied';
    they're noise."""
    s_lower = (subject or "").lower().strip()
    # Strip stacked Re:/Fwd: prefixes — Mailman / Outlook auto-responders
    # often reply with "Re: out of office" because they echo the original
    # subject which already carried the prefix. startswith would miss
    # those without this peel.
    while True:
        for _p in ("re:", "fwd:", "fw:", "re :", "fwd :"):
            if s_lower.startswith(_p):
                s_lower = s_lower[len(_p):].lstrip()
                break
        else:
            break
    if any(s_lower.startswith(p) for p in ("auto:", "automatic reply", "out of office",
                                            "out-of-office", "vacation:")):
        return True
    h = {k.lower(): v.lower() for k, v in headers}
    if h.get("auto-submitted") and h["auto-submitted"] != "no":
        return True
    if h.get("x-autoreply") or h.get("x-auto-response-suppress"):
        return True
    return False


# ── AI-driven reply classifier ──────────────────────────────────────

# Cheap regex-based first pass before we burn an AI call. Catches
# the vast majority of auto-replies and unsubscribe flags so the AI
# only sees genuine human responses worth classifying nuance on.

import re as _re

_HEURISTIC_OOO = _re.compile(
    # Stability fix (audit wave 27): the previous version included
    # `will be back on` as a standalone trigger, which matches every
    # idiomatic "I will be back on Monday with budget" reply too —
    # genuine interested replies got misclassified as OOO, the lead
    # was silently skipped (no status flip, no _reply_class saved,
    # no positive feedback signal), and the cadence kept emailing
    # them. The other patterns ("on vacation", "currently away",
    # "i'm away") already cover real OOO replies; tightening this
    # one to only trigger when paired with explicit OOO context
    # ("will be back on vacation/leave/holiday/Monday DD-Mon" etc.
    # look the same as a normal "back on Monday with thoughts" so we
    # just drop the standalone phrase).
    r"\b(out of office|on (annual )?leave|on vacation|on holiday|out of the office|"
    r"i'?m away|i am away|currently away)\b", _re.I)
_HEURISTIC_UNSUB = _re.compile(
    r"\b(unsubscribe|opt[ -]out|remove me|stop emailing|do not contact|"
    r"please remove|take me off)\b", _re.I)
_HEURISTIC_WRONG = _re.compile(
    r"\b(wrong person|not the right person|forwarded to|cc'?ing|"
    r"please contact|reach out to|i no longer (work|am)|i've left)\b", _re.I)

# Valid classes the rest of the code branches on. Anything else from
# the AI gets coerced to "interested" so we never silently drop a real
# reply.
_VALID_CLASSES = {
    "interested", "not_interested", "not_now",
    "out_of_office", "wrong_person", "unsubscribe",
}


def _heuristic_class(subject: str, body: str) -> str | None:
    """Cheap regex classification. Returns None when it can't tell —
    the AI fallback handles the harder cases."""
    body = (body or "")[:4000]
    if _HEURISTIC_OOO.search(body) or _HEURISTIC_OOO.search(subject or ""):
        return "out_of_office"
    if _HEURISTIC_UNSUB.search(body):
        return "unsubscribe"
    if _HEURISTIC_WRONG.search(body):
        return "wrong_person"
    return None


def _ai_classify_reply(subject: str, body: str) -> str:
    """Send the reply body to the configured AI provider and ask
    for one of the canonical classes. Falls back to 'interested' on
    any failure so we never lose a genuine reply."""
    body = (body or "").strip()[:3000]
    subject = (subject or "").strip()[:200]
    try:
        from providers import get_provider
        prov = get_provider()
    except Exception:
        # a275: was "interested" — provider misconfig (no key, no
        # network) silently labelled every scanned reply as positive,
        # flipping email_status to "replied" + writing a `good`
        # feedback signal. Net effect: a cold provider config poisoned
        # the learning loop with fake positive signals. Now returns
        # "unknown" so the caller knows it can't be trusted.
        return "unknown"
    prompt = (
        "Classify this email reply (one of):\n"
        "- interested        — wants to talk, asked a question, said yes\n"
        "- not_interested    — clear 'no thanks' / not a fit\n"
        "- not_now           — interested but timing is wrong, asked to follow up later\n"
        "- out_of_office     — auto-reply / vacation responder\n"
        "- wrong_person      — forwarded to someone else / not the right contact\n"
        "- unsubscribe       — explicit opt-out / 'stop emailing me'\n\n"
        f"Subject: {subject}\n\n"
        f"Body:\n{body}\n\n"
        'Reply with ONLY the single class label (e.g. "interested"). '
        "No other text, no quotes, no punctuation."
    )
    try:
        raw = prov.chat(
            messages=[
                {"role": "system",
                 "content": "You classify cold-email replies. Reply with one word from the allowed set."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=8, temperature=0.0, timeout_s=15.0,
        )
    except Exception:
        # a275: see the get_provider catch above — return "unknown"
        # rather than fake-positive "interested".
        return "unknown"
    label = (raw or "").strip().lower().strip("\"'.,!?")
    label = label.replace("-", "_").replace(" ", "_")
    if label in _VALID_CLASSES:
        return label
    # Tighter coercion: when the AI returns something we can't pin to
    # a canonical class, fall back to "not_now" instead of
    # "interested". `not_now` still pauses the cadence (good — we
    # don't keep mailing someone who replied) but doesn't promote a
    # potentially negative reply to a positive `good` feedback signal.
    # If the response actually contained "not" anywhere, lean negative.
    if "not" in label or "no thanks" in label or "decline" in label:
        return "not_interested"
    return "not_now"


def _classify_reply(subject: str, body: str) -> str:
    """Two-tier classifier: regex first (zero cost), AI for the rest."""
    h = _heuristic_class(subject, body)
    if h:
        return h
    return _ai_classify_reply(subject, body)


def _status_for_class(klass: str) -> str | None:
    """Map a reply class to the lead's `email_status`. Returns None
    when the class shouldn't change status (out_of_office)."""
    if klass == "out_of_office":
        return None  # don't flip to replied — it's not a real reply
    if klass == "not_interested":
        return "lost"
    if klass == "unsubscribe":
        # a275: was returning "ignored" which made the cli_sequence.py
        # `stop_on_unsubscribe` gate at line 268 inert (the gate checks
        # for `email_status == "unsubscribed"`). Users who replied STOP
        # were still receiving Day +4/+9 follow-ups — a real GDPR /
        # CAN-SPAM exposure. Now sets the canonical "unsubscribed"
        # status the cadence loop honours.
        return "unsubscribed"
    if klass == "wrong_person":
        return "ignored"
    # interested + not_now both flip to "replied" so the cadence pauses
    # and the user sees them in the dashboard.
    return "replied"


async def _scan_inbox(user_id: int, since_days: int = 14, dry_run: bool = False) -> dict:
    """Connect, fetch unseen messages from the last N days, match each
    to the outbound index, and update lead.email_status='replied' for
    matches. Returns a summary dict."""
    s = _load_imap_settings()
    if not s:
        return {"ok": False, "error": "IMAP not configured. Run `huntova inbox setup`."}

    by_msgid, by_email = await _build_outbound_index(user_id)
    if not by_msgid and not by_email:
        return {"ok": True, "matched": 0, "scanned": 0,
                "note": "No outbound messages tracked yet — send some first."}

    import db as _db
    matched, scanned, autoreplied = 0, 0, 0
    by_class: dict[str, int] = {}
    # IMAP RFC 3501 requires English month abbreviations. `strftime("%b")`
    # is locale-aware, so a non-en system would emit e.g. "мая" / "Mai" and
    # the SINCE filter would silently fall back to "all messages". Map
    # explicitly so the SINCE clause is portable.
    _MON_EN = ("Jan", "Feb", "Mar", "Apr", "May", "Jun",
               "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")
    _since_dt = datetime.now(timezone.utc) - _td(days=since_days)
    since = f"{_since_dt.day:02d}-{_MON_EN[_since_dt.month - 1]}-{_since_dt.year}"

    cli = _connect(s)
    try:
        cli.select("INBOX")
        # UNSEEN limits noise; SINCE bounds it. IMAP uses dd-Mon-yyyy.
        # Guard against `imaplib.IMAP4.abort` (server closed the
        # connection mid-scan, common on flaky home networks). The
        # daemon loop calls this every minute — one abort shouldn't
        # crash the whole watcher.
        try:
            typ, data = cli.search(None, f'(SINCE "{since}")')
        except (imaplib.IMAP4.abort, imaplib.IMAP4.error) as _e:
            return {"ok": False, "error": f"IMAP search failed: {type(_e).__name__}",
                    "matched": 0, "scanned": 0}
        ids = (data[0] or b"").split() if typ == "OK" else []
        scanned = len(ids)
        for raw_id in ids:
            try:
                typ, fetched = cli.fetch(raw_id, "(RFC822)")
            except imaplib.IMAP4.error:
                continue
            if typ != "OK" or not fetched or not fetched[0]:
                continue
            try:
                msg = message_from_bytes(fetched[0][1])
            except Exception:
                continue
            subj = (msg.get("Subject") or "").strip()
            if _is_autoreply(subj, list(msg.items())):
                autoreplied += 1
                continue
            in_reply = msg.get("In-Reply-To") or ""
            references = msg.get("References") or ""
            from_addr = (parseaddr(msg.get("From") or "")[1] or "").strip().lower()

            # a427 fix (BRAIN-66): primary match is In-Reply-To / References
            # binding (Message-ID is random+unguessable per-send, so a
            # match implies the sender actually received our outbound).
            lid = None
            for cand in _split_msgids(in_reply) + _split_msgids(references):
                _cand_lc = cand.lower()
                if _cand_lc in by_msgid:
                    lid = by_msgid[_cand_lc]
                    break
            # a427 fix (BRAIN-66): the prior version had a From-address
            # fallback that matched by_email[from_addr]. SMTP From is
            # trivially spoofable — anyone who knows a prospect's email
            # could send a fake "reply" that flips the lead's status to
            # "replied" / "won" / "lost" based on body classification,
            # poisoning the Pulse counter, the DNA generation feedback
            # loop, and the auto-advance sequence flow. Per GPT-5.4
            # senior-engineer audit (Perplexity, this session) on
            # email-spoofing-via-From class. Threading-header binding
            # is the only authenticated correlation; drop the fallback.
            # Edge case loss: a prospect who composes a fresh email
            # instead of hitting Reply won't auto-match. That's
            # acceptable — operator can manually update the lead status,
            # whereas a spoofed reply is silent corruption.
            if not lid:
                continue

            matched += 1
            # Pull the plain-text body for the classifier. Multipart
            # walks pick the first text/plain or text/html part. Cap
            # the body so a 1 MB HTML email doesn't blow the token
            # budget.
            body_text = ""
            try:
                if msg.is_multipart():
                    for part in msg.walk():
                        ct = (part.get_content_type() or "").lower()
                        if ct == "text/plain":
                            try:
                                body_text = part.get_payload(decode=True).decode(
                                    part.get_content_charset() or "utf-8", "replace")
                                break
                            except Exception:
                                continue
                    if not body_text:
                        for part in msg.walk():
                            if (part.get_content_type() or "").lower() == "text/html":
                                try:
                                    raw_html = _decode_payload(
                                        part.get_payload(decode=True),
                                        part.get_content_charset())
                                    body_text = re.sub(r"<[^>]+>", " ", raw_html)
                                    break
                                except Exception:
                                    continue
                else:
                    body_text = _decode_payload(
                        msg.get_payload(decode=True),
                        msg.get_content_charset())
            except Exception:
                body_text = ""
            klass = _classify_reply(subj, body_text or "")
            by_class[klass] = by_class.get(klass, 0) + 1

            # Out-of-office shouldn't count as a reply or pause the
            # sequence — the prospect is just away. Bump the
            # autoreplied counter so the operator sees how noisy
            # their inbox is.
            if klass == "out_of_office":
                autoreplied += 1
                matched -= 1
                continue

            if dry_run:
                continue

            # a275: when the AI classifier returned "unknown" (provider
            # misconfig or AI exception), don't flip status — count it
            # under by_class for visibility but leave the lead alone.
            # Pre-a275 every "unknown" got bucketed as "interested" and
            # promoted to email_status="replied" + a `good` feedback
            # signal, poisoning the learning loop.
            if klass == "unknown":
                continue
            now_iso = datetime.now(timezone.utc).isoformat()
            new_status = _status_for_class(klass) or "replied"
            # a301: wire `auto_advance_on_reply` setting. When on AND
            # the classifier is confident (interested / asking_info),
            # auto-advance to "qualified" so the dashboard's qualified
            # column populates without user clicks. Off by default —
            # operators who want manual review keep the current
            # "replied" landing. Was accepted by /api/settings + UI
            # but never read.
            try:
                _settings = await _db.get_settings(user_id) or {}
                if _settings.get("auto_advance_on_reply"):
                    if klass in ("interested", "asking_info"):
                        new_status = "qualified"
            except Exception:
                pass

            def _mut(lead: dict, _now: str = now_iso, _from: str = from_addr,
                     _subj: str = subj, _kl: str = klass,
                     _ns: str = new_status) -> dict:
                if lead.get("email_status") in ("won", "meeting_booked"):
                    return lead
                lead["email_status"] = _ns
                lead["email_status_date"] = _now
                lead["_reply_subject"] = _subj[:200]
                lead["_reply_from"] = _from[:200]
                lead["_reply_date"] = _now
                lead["_reply_class"] = _kl
                # Auto-pause the follow-up sequence — no point sending
                # the Day +4 bump to anyone who already replied
                # meaningfully. Kept paused for unsubscribe / wrong-person
                # too: never auto-mail those again.
                lead["_seq_paused"] = True
                h = lead.get("status_history", [])
                if not h or h[-1].get("status") != _ns:
                    h.append({"status": _ns, "date": _now,
                              "reason": f"imap_reply:{_kl}"})
                    if len(h) > 100:
                        h = h[-100:]
                lead["status_history"] = h
                return lead

            try:
                await _db.merge_lead(user_id, lid, _mut)
                # Feedback signal — but only positive when the AI
                # said the reply was actually interested / not_now.
                # not_interested / wrong_person feed back as "bad" so
                # the agent learns to avoid lookalike prospects.
                try:
                    if klass in ("interested", "not_now"):
                        await _db.save_lead_feedback(user_id, lid, "good", f"imap_reply:{klass}")
                    elif klass in ("not_interested", "wrong_person"):
                        await _db.save_lead_feedback(user_id, lid, "bad", f"imap_reply:{klass}")
                except Exception:
                    pass
            except Exception as e:
                print(f"  {_red('!')} merge_lead failed for {lid}: {e}", file=sys.stderr)
    finally:
        try:
            cli.close()
        except Exception:
            pass
        try:
            cli.logout()
        except Exception:
            pass

    return {"ok": True, "scanned": scanned, "matched": matched,
            "autoreplied": autoreplied, "by_class": by_class,
            "dry_run": dry_run}


def _td(days: int):
    from datetime import timedelta
    return timedelta(days=days)


# ── subcommands ─────────────────────────────────────────────────────

def _cmd_setup(args: argparse.Namespace) -> int:
    print(_bold("Huntova IMAP setup"))
    print(_dim("Stored in your OS keychain (or fallback) — same place your AI key lives.\n"))
    host = (args.host or "").strip() or input("IMAP host (e.g. imap.gmail.com): ").strip()
    if not host:
        print(_red("aborted: host required"), file=sys.stderr); return 1
    port = (args.port or "").strip() or input("Port [993]: ").strip() or "993"
    user = (args.user or "").strip() or input("Username (full email): ").strip()
    if not user:
        print(_red("aborted: user required"), file=sys.stderr); return 1
    if args.password:
        password = args.password
    else:
        from getpass import getpass
        password = getpass("Password / app-password: ").strip()
    if not password:
        print(_red("aborted: password required"), file=sys.stderr); return 1
    try:
        from secrets_store import set_secret
        set_secret(_KEY_HOST, host)
        set_secret(_KEY_PORT, str(port))
        set_secret(_KEY_USER, user)
        set_secret(_KEY_PASS, password)
    except Exception as e:
        print(_red(f"keychain write failed: {e}"), file=sys.stderr); return 1
    print(f"{_green('✓')} saved. Run `huntova inbox check` to test.")
    return 0


def _cmd_check(args: argparse.Namespace) -> int:
    from cli import _bootstrap_local_env  # late import — circular otherwise
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    res = asyncio.run(_scan_inbox(user_id, since_days=int(args.since or 14), dry_run=bool(args.dry_run)))
    if not res.get("ok"):
        print(_red(res.get("error", "scan failed")), file=sys.stderr); return 1
    print(f"  {_dim('scanned')} {res.get('scanned', 0)}")
    print(f"  {_green('replies matched')} {res.get('matched', 0)}")
    bc = res.get("by_class") or {}
    # Only positive paths show in green; status-flipping paths in dim.
    _label_color = {
        "interested": _green,
        "not_now": _green,
        "not_interested": _yellow,
        "wrong_person": _yellow,
        "unsubscribe": _yellow,
        "out_of_office": _dim,
    }
    for label, count in bc.items():
        if count <= 0:
            continue
        color = _label_color.get(label, _dim)
        print(f"    {color(label):24s} {count}")
    if res.get("autoreplied"):
        print(f"  {_yellow('auto-replies skipped')} {res.get('autoreplied', 0)}")
    if res.get("note"):
        print(f"  {_dim(res['note'])}")
    return 0


def _cmd_watch(args: argparse.Namespace) -> int:
    from cli import _bootstrap_local_env
    user_id = _bootstrap_local_env()
    if user_id is None:
        return 1
    interval = max(60, int(args.interval or 300))
    print(f"{_bold('huntova inbox watch')} — polling every {interval}s. Ctrl-C to stop.")
    while True:
        # Stability fix (audit wave 30): the previous version did
        # `time.sleep(interval)` after the scan finished, so the
        # actual cadence drifted to `scan_duration + interval`. A
        # scan can take 10-60s+ (IMAP connect/login/search/fetch +
        # AI classification per reply); on large mailboxes a poll
        # could stretch past several minutes — silently widening
        # the user's configured 5-min interval to 6+ min, drift
        # compounding across the day. Capture next-tick deadline
        # at top of loop and sleep until then so cadence is
        # wall-clock-stable regardless of scan duration.
        next_at = time.monotonic() + interval
        try:
            res = asyncio.run(_scan_inbox(user_id, since_days=int(args.since or 3)))
            ts = datetime.now().strftime("%H:%M:%S")
            if not res.get("ok"):
                print(f"  {ts} {_red('!')} {res.get('error')}", file=sys.stderr)
            else:
                m = res.get("matched", 0)
                if m:
                    print(f"  {ts} {_green('✓')} {m} new "
                          f"{'reply' if m == 1 else 'replies'}")
                elif args.verbose:
                    _scanned = res.get("scanned", 0)
                    print(f"  {ts} {_dim('scanned ' + str(_scanned) + ', no matches')}")
        except KeyboardInterrupt:
            print(f"\n{_dim('stopped')}"); return 0
        except Exception as e:
            print(f"  {_red('!')} poll error: {type(e).__name__}: {e}", file=sys.stderr)
        # Sleep until the deadline; if the scan already ran longer
        # than the interval, max(0, …) makes us re-poll immediately.
        time.sleep(max(0.0, next_at - time.monotonic()))


# ── argparse wiring ─────────────────────────────────────────────────

def register(subparsers) -> None:
    """Add `huntova inbox` and its three subcommands."""
    p = subparsers.add_parser(
        "inbox", help="poll IMAP and match replies to your outbound sends",
        description=("Reply detection. Connects to your IMAP mailbox, "
                     "matches incoming messages to outbound sends from "
                     "`huntova outreach send`, flips matching leads to "
                     "email_status=replied + appends to status_history."),
        epilog=("Examples:\n"
                "  huntova inbox setup\n"
                "  huntova inbox check\n"
                "  huntova inbox watch --interval 300\n\n"
                "Docs: https://github.com/enzostrano/huntova-public\n"),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = p.add_subparsers(dest="inbox_cmd", required=True)

    p_setup = sub.add_parser("setup", help="save IMAP creds to the OS keychain")
    p_setup.add_argument("--host", default="", help="IMAP host (e.g. imap.gmail.com)")
    p_setup.add_argument("--port", default="", help="IMAP port [993]")
    p_setup.add_argument("--user", default="", help="username (full email)")
    p_setup.add_argument("--password", default="", help="(prefer the prompt — passing this on the CLI leaks via shell history)")
    p_setup.set_defaults(func=_cmd_setup)

    # a291 fix: type=int so argparse rejects non-numeric input upfront
    # with a friendly "argument --since: invalid int value: '7d'"
    # instead of a stack trace from int() inside the handler.
    p_check = sub.add_parser("check", help="one-shot poll, print summary")
    p_check.add_argument("--since", type=int, default=14, help="look back N days [14]")
    p_check.add_argument("--dry-run", action="store_true", help="match but don't update leads")
    p_check.set_defaults(func=_cmd_check)

    p_watch = sub.add_parser("watch", help="poll loop until Ctrl-C")
    p_watch.add_argument("--interval", type=int, default=300, help="seconds between polls [300]; min 60")
    p_watch.add_argument("--since", type=int, default=3, help="look back N days each poll [3]")
    p_watch.add_argument("--verbose", action="store_true", help="log even when no matches")
    p_watch.set_defaults(func=_cmd_watch)
