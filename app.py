#!/usr/bin/env python3
# ═══════════════════════════════════════════════════════════════
# Huntova v2.0 — CLOUD RELEASE (Gemini + SearXNG)
# SaaS-ready: can run standalone or as module imported by server.py
# ═══════════════════════════════════════════════════════════════
VERSION = "2.0-saas"
"""
Huntova AI-Agent — Business logic module.
Contains all agent pipeline logic, AI integration, scraping, email generation.
When imported by server.py, provides run_agent_scoped(ctx) for per-user execution.
When run directly, starts standalone mode with built-in HTTP server.
"""
import atexit, base64 as _b64, csv, hashlib, json, os, queue, random, re, signal
import subprocess, sys, threading, time, webbrowser
from dataclasses import dataclass
from datetime import datetime, timezone

from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse

import requests
from openai import OpenAI
try:
    from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeoutError
    HAS_PLAYWRIGHT = True
except ImportError:
    HAS_PLAYWRIGHT = False
    sync_playwright = None
    PlaywrightTimeoutError = TimeoutError

# Windows console
try: sys.stdout.reconfigure(encoding="utf-8")
except: pass
try:
    import ctypes; k=ctypes.windll.kernel32; k.SetConsoleMode(k.GetStdHandle(-11),7)
except: pass

# ───────────────────────────────────────────────────────────────
# THREAD-LOCAL CONTEXT (SaaS multi-user support)
# When running under server.py, each agent thread gets a UserAgentContext
# via _tl.ctx. Standalone mode uses globals (ctx is None).
# ───────────────────────────────────────────────────────────────
_tl = threading.local()

def _ctx():
    """Get current thread's UserAgentContext, or None in standalone mode."""
    return getattr(_tl, "ctx", None)

# ───────────────────────────────────────────────────────────────
# CONFIG
# ───────────────────────────────────────────────────────────────
_app_mode_app = (os.environ.get("APP_MODE") or "cloud").strip().lower()
_searxng_default_app = "https://searx.be" if _app_mode_app == "local" else "http://127.0.0.1:8888"
_raw_searxng_app = os.environ.get("SEARXNG_URL", _searxng_default_app).strip()
if _raw_searxng_app and not _raw_searxng_app.startswith("http"):
    _raw_searxng_app = "https://" + _raw_searxng_app
SEARXNG_URL     = _raw_searxng_app

# ── AI Provider: "gemini" or "local" ──
AI_PROVIDER     = os.environ.get("HV_AI_PROVIDER", "gemini")
GEMINI_API_KEY  = os.environ.get("HV_GEMINI_KEY", "")
GEMINI_URL      = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL    = os.environ.get("HV_GEMINI_MODEL", "gemini-2.5-flash")
LM_STUDIO_URL   = "http://localhost:1234/v1"
LM_STUDIO_MODEL = "qwen/qwen3-32b"

if AI_PROVIDER == "gemini":
    if not GEMINI_API_KEY:
        # Don't exit when imported by server.py — key may be set in env
        if __name__ == "__main__":
            print("ERROR: Set HV_GEMINI_KEY environment variable for cloud mode.")
            sys.exit(1)
    _API_URL = GEMINI_URL
    _API_KEY = GEMINI_API_KEY
    MODEL_ID = GEMINI_MODEL
else:
    _API_URL = LM_STUDIO_URL
    _API_KEY = "lm-studio"
    MODEL_ID = LM_STUDIO_MODEL
BASE_DIR        = os.environ.get("HV_BASE_DIR", r"E:\huntova_agent")
PORT            = int(os.environ.get("HV_PORT", "5000"))

# ── Agent tuning constants ──
MAX_RESULTS_PER_QUERY   = 8  # Aligned with config.py — more results = more lead opportunities
MIN_SCORE_TO_KEEP       = 7
MAX_RETRIES             = 2
DELAY_URL               = 0.4
DELAY_QUERY             = 0.3
DEEP_LINKS              = 6
DEEP_MIN                = 7
DEEP_DELAY              = 0.3
CHECKPOINT_N            = 5
SEARCH_TIMEOUT          = 15
FETCH_TIMEOUT_MS        = 20000
IDLE_TIMEOUT_MS         = 5000
SCREENSHOT_INTERVAL     = 1.5
SMART_BROWSE_BUDGET     = 6
SMART_BROWSE_SCROLLS    = 8
SMART_BROWSE_CLICKS     = 3
MAX_VIDEO_VISITS        = 3
VIDEO_FETCH_TIMEOUT     = 5000

USER_AGENT = (
    "Mozilla/5.0 (compatible; HuntovaBot/1.0; +http://localhost:5000/privacy) "
    "AppleWebKit/537.36 (KHTML, like Gecko)"
)
DATA_RETENTION_DAYS = 730  # 2 years GDPR default

MASTER_LEADS_JSON = os.path.join(BASE_DIR, "master_leads.json")
ARCHIVED_JSON     = os.path.join(BASE_DIR, "archived_leads.json")
SEEN_HISTORY_JSON = os.path.join(BASE_DIR, "seen_history.json")
DOMAIN_BLOCKLIST  = os.path.join(BASE_DIR, "domain_blocklist.json")
USER_BLOCKED_FILE = os.path.join(BASE_DIR, "user_blocked.json")

# Auto-skip domains — use the canonical list from config.py (225+ domains)
from config import MEGA_CORP_DOMAINS
SETTINGS_JSON     = os.path.join(BASE_DIR, "settings.json")
BASE_OUTPUT_DIR   = os.path.join(BASE_DIR, "reports")
_run_ts           = datetime.now().strftime("%Y-%m-%d_%H%M%S")
RUN_DIR           = os.path.join(BASE_OUTPUT_DIR, f"Report - {_run_ts}")
OUTPUT_CSV        = os.path.join(RUN_DIR, "leads.csv")
BACKUP_DIR        = os.path.join(BASE_DIR, "backups")
LOG_DIR           = os.path.join(BASE_DIR, "logs")
WAL_FILE          = os.path.join(BASE_DIR, "wal.jsonl")
HEARTBEAT_FILE    = os.path.join(BASE_DIR, ".heartbeat")
AGENT_STATE_FILE  = os.path.join(BASE_DIR, "agent_state.json")

# Default settings (overridden by settings.json)
DEFAULT_SETTINGS = {
    "booking_url": "",
    "from_name": "",
    "from_email": "",
    "phone": "",
    "website": "",
}

def load_settings():
    # In SaaS mode, check thread-local context first (per-user settings from DB)
    ctx = _ctx()
    if ctx and hasattr(ctx, '_user_settings') and ctx._user_settings:
        return {**DEFAULT_SETTINGS, **ctx._user_settings}
    s = _safe_read(SETTINGS_JSON, {})
    return {**DEFAULT_SETTINGS, **s}

def save_settings(s):
    _atomic_write(SETTINGS_JSON, s)

def generate_tone_email(lead, tone, booking_url="", settings=None):
    _gte_s = settings if settings else load_settings()
    _gte_w = _gte_s.get("wizard", {})
    _gte_name = _gte_s.get("from_name") or _gte_w.get("company_name", "our team")
    _gte_company = _gte_w.get("company_name", "our company")
    _gte_desc = _gte_w.get("business_description", "")
    import random as _rnd
    _dash = chr(8212)

    # ── Gather lead data ──
    _context = lead.get("event_name","") or lead.get("org_name","")
    _org = lead.get("org_name","")
    _contact = (lead.get("contact_name") or "").strip()
    _first = _contact.split()[0] if _contact else ""
    _category = (lead.get("event_type","") or "opportunity").lower()
    _recurring = lead.get("is_recurring", False)
    _country = lead.get("country","")
    _tools = lead.get("platform_used","")
    _gap = lead.get("production_gap","")
    _evidence = lead.get("evidence_quote","")
    _old_email = lead.get("email_body","")[:200]

    # ── AI email — profile-driven, business-agnostic ──
    try:
        _tone_map = {
            "friendly": "casual and warm, like a colleague not a salesperson",
            "consultative": "professional but human, show industry knowledge",
            "broadcast": "confident and direct, premium positioning",
        }
        _tone_style = _tone_map.get(tone, _tone_map["friendly"])
        _greeting = f"Hi {_first}," if _first else "Hi,"
        _recur_note = " This prospect has RECURRING needs — mention ongoing partnership or volume pricing." if _recurring else ""
        _booking_note = f"End with booking link: {booking_url}" if booking_url else "End with a casual one-line question."
        _prev_note = f"\nDo NOT repeat this previous email — write something totally different:\n{_old_email}\n" if _old_email and len(_old_email) > 50 else ""

        # Build smart proof line from past clients + case study
        _proof_line = ""
        _past_c = _gte_w.get("past_clients", "")
        _case_study = _gte_w.get("proof", "")
        if _past_c or _case_study:
            _proof_line = "\nSOCIAL PROOF (use naturally — don't force it):"
            if _past_c: _proof_line += f"\n- Companies we've worked with: {_past_c[:150]}"
            if _case_study: _proof_line += f"\n- Case study: {_case_study[:200]}"
            _proof_line += "\nWeave this into the credibility sentence. Example: 'We helped a similar [type] company [specific result from case study].'"
        # Inject differentiators for stronger positioning
        _diff_line = ""
        _differentiator = _gte_w.get("differentiator", _gte_w.get("differentiators", ""))
        _comp_diff = _gte_w.get("comp_diff", "")
        if _differentiator or _comp_diff:
            _diff_line = "\nDIFFERENTIATOR (weave naturally into the value sentence):"
            if isinstance(_differentiator, list):
                _differentiator = ", ".join(_differentiator)
            if _differentiator:
                _diff_line += f"\n- Our edge: {str(_differentiator)[:200]}"
            if _comp_diff:
                _diff_line += f"\n- vs competitors: {_comp_diff[:200]}"

        # Detect hiring-as-need signal
        _hiring_hook = ""
        _hiring_sigs = _gte_w.get("hiring_signals", "")
        if _hiring_sigs and lead.get("_hiring_signal_detected"):
            _hiring_hook = f"\nHIRING HOOK: This company appears to be hiring for a role we could help with. Open with something like 'I noticed you're looking for a [role] — while you search, we can help bridge the gap with [specific service].' This is extremely effective because it shows you understand their immediate pain."

        prompt = f"""Write a cold outreach email from {_gte_name} at {_gte_company}.

ABOUT US: {_gte_desc[:400] if _gte_desc else f'{_gte_company} provides professional B2B services.'}

PROSPECT: {_org} ({_category}) in {_country}.
Context: "{_context}"
Current tools/approach: {_tools}
Opportunity: {_gap}
Core problem we solve for companies like this: {_gte_w.get('pain_point', '') or 'help them improve their operations'}
Evidence from their site: "{_evidence[:200]}"
{_recur_note}{_proof_line}{_hiring_hook}{_diff_line}
{_prev_note}
EMAIL STRUCTURE (follow this exactly):
1. "{_greeting}" + one sentence about THEIR specific situation — show you actually researched them. Reference the evidence quote or a specific detail from their page.
2. One sentence identifying the specific risk or gap they face right now. Make it feel urgent but not pushy.
3. Two sentences explaining what {_gte_company} does and why it solves their exact problem. Be specific about deliverables, not vague about "solutions."
4. One sentence of credibility — reference similar companies you've helped (use social proof if available), concrete results, or industry experience.
5. One low-pressure CTA: suggest a 15-minute call or send a relevant resource. {_booking_note}
6. Sign off: {_gte_name}, {_gte_company}

TOTAL: 3-5 sentences. Under 80 words. Shorter is better — every sentence must earn its place.

TONE: {_tone_style}. Write like a fellow professional — warm, direct, knowledgeable. Not corporate, not salesy. The reader should feel like you genuinely understand their world.

BANNED WORDS (never use): leverage, streamline, enhance, elevate, seamless, transform, solution, excited, thrilled, synergy, cutting-edge, innovative, game-changing, empower, optimize, revolutionize, comprehensive, facilitate, "I came across", "I hope this finds you", "I wanted to reach out", "touching base".

Subject: specific to THEIR situation, curiosity-driven, max 8 words. Good subjects reference a specific detail from their page.
LinkedIn note: max 150 chars, personal, references something specific about them.

ALSO write 3 follow-up emails:
- email_followup_2 (Day 3): different angle, 2-3 sentences, do not repeat email 1
- email_followup_3 (Day 7): share an insight or resource, 2 sentences, helpful not pushy
- email_followup_4 (Day 14): breakup email, 1-2 sentences, "should I close your file" tone

Reply with ONLY this JSON (all 6 fields required):
{{"email_subject":"...","email_body":"...","linkedin_note":"...","email_followup_2":"...","email_followup_3":"...","email_followup_4":"..."}}"""

        raw = _ai_call(
            messages=[
                {"role":"system","content":"You write short cold emails. Reply with ONLY valid JSON."},
                {"role":"user","content":prompt}
            ],
            temperature=0.7, max_tokens=1500)
        js = extract_json(raw)
        if js:
            data = json.loads(js)
            body = _clean_thinking(data.get("email_body",""))
            subj = _clean_subject(data.get("email_subject",""))
            li = (data.get("linkedin_note","") or "")[:200]
            if len(body) > 50 and subj:
                result = {"email_subject": subj, "email_body": body, "linkedin_note": li}
                # Extract follow-up sequence
                for _fk in ("email_followup_2", "email_followup_3", "email_followup_4"):
                    _fv = (data.get(_fk) or "").strip()
                    if _fv and len(_fv) > 20:
                        result[_fk] = _fv
                _fc = sum(1 for k in ("email_followup_2", "email_followup_3", "email_followup_4") if result.get(k))
                emit_log(f"\u2728 AI wrote email ({tone}) + {_fc} follow-ups", "ai")
                return result
    except Exception as e:
        emit_log(f"AI email failed: {e}", "warn")

    # ── Template fallback (generic, profile-driven) ──
    _greet = f"Hi {_first}" if _first else "Hi"
    _gap_line = f" From what I can see, {_gap.lower().rstrip('.')}." if _gap else ""
    _recur_line = f"\n\nSince this looks like an ongoing need, we'd be happy to discuss a longer-term partnership." if _recurring else ""
    _about = _gte_desc[:150] if _gte_desc else f"we provide professional B2B services"
    _bk = f"Here's my calendar if you'd like to chat: {booking_url}" if booking_url else "Would a quick 15-minute call work?"

    _lead_ref = _org or _context or "your company"
    body = f"{_greet} {_dash}\n\n{_lead_ref} caught my eye.{_gap_line}\n\nI'm {_gte_name} from {_gte_company} {_dash} {_about}.{_recur_line}\n\nI think there's a good fit here. {_bk}\n\n{_gte_name}"

    subject = f"{_lead_ref[:40]} {_dash} quick idea"[:65]
    linkedin = f"Hey! {_lead_ref} caught my eye. I'm with {_gte_company}. Happy to connect."[:180]

    return {"email_subject": subject, "email_body": body, "linkedin_note": linkedin}





# ───────────────────────────────────────────────────────────────
# EVENT BUS (SSE pub/sub)
# ───────────────────────────────────────────────────────────────
class EventBus:
    def __init__(self):
        self._subscribers = set()
        self._lock = threading.Lock()
    def subscribe(self):
        q = queue.Queue(maxsize=200)
        with self._lock:
            self._subscribers.add(q)
        return q
    def unsubscribe(self, q):
        with self._lock:
            self._subscribers.discard(q)
    def emit(self, event, data):
        msg = f"event: {event}\ndata: {json.dumps(data, default=str)}\n\n"
        dead = []
        with self._lock:
            for q in self._subscribers:
                try:
                    q.put_nowait(msg)
                except queue.Full:
                    dead.append(q)
            for q in dead:
                self._subscribers.discard(q)

bus = EventBus()


def emit_screenshot(page, url=""):
    """Capture browser screenshot and send to dashboard via SSE."""
    ctx = _ctx()
    if ctx:
        ctx.emit_screenshot(page, url)
        return
    try:
        raw = page.screenshot(type="jpeg", quality=55, full_page=False, timeout=3000)
        b64 = _b64.b64encode(raw).decode('ascii')
        bus.emit("screenshot", {"img": b64, "url": url[:200], "ts": time.strftime("%H:%M:%S")})
    except:
        pass  # Never block agent for a screenshot

def emit_browsing_state(url, title="", method="requests", status="loading"):
    """Emit structured browsing state for live view fallback (no screenshot needed)."""
    data = {"url": url[:200], "title": title[:100], "method": method, "status": status, "ts": time.strftime("%H:%M:%S")}
    ctx = _ctx()
    if ctx:
        ctx.bus.emit("browsing_state", data)
    else:
        bus.emit("browsing_state", data)

_current_session_log = ""  # Set in run_agent(), avoids glob scan per log call

def emit_log(msg, level="info"):
    ctx = _ctx()
    if ctx:
        ctx.emit_log(msg, level)
        return
    _icons = {"info":"🔍","ok":"✅","warn":"⚠️","lead":"⭐","skip":"⏭","fetch":"📄","ai":"🤖","save":"💾","error":"❌"}
    icon = _icons.get(level, "🔍")
    ts = datetime.now().strftime("%H:%M:%S")
    text = f"{ts} {icon} {msg}"
    bus.emit("log", {"msg": text, "level": level, "ts": ts})
    # Write to log file
    try:
        log_path = os.path.join(LOG_DIR, "agent.log")
        with open(log_path, "a", encoding="utf-8") as lf:
            lf.write(f"[{datetime.now().isoformat()}] [{level.upper():5s}] {msg}\n")
        # Also write to latest session log (cached path, no glob scan)
        if _current_session_log:
            with open(_current_session_log, "a", encoding="utf-8") as _sf:
                _sf.write(f"[{datetime.now().strftime('%H:%M:%S')}] [{level.upper():5s}] {msg}\n")
    except Exception: pass


def emit_progress(**kw):
    ctx = _ctx()
    if ctx: ctx.emit_progress(**kw); return
    bus.emit("progress", kw)
def emit_status(text, state="running"):
    ctx = _ctx()
    if ctx: ctx.emit_status(text, state); return
    bus.emit("status", {"text": text, "state": state})
def emit_lead(lead):
    # Strip underscore-prefixed internal fields before emitting over
    # SSE — they're agent-side state (`_contact_source`, `_full_text`,
    # `_site_text`, `_guessed_emails`, etc.) that shouldn't leak to the
    # dashboard and would also fatten the SSE frame. The schema lists
    # `additionalProperties: false`, so an external auditor that
    # validates the wire format would also flag the leak.
    if isinstance(lead, dict):
        lead = {k: v for k, v in lead.items() if not (isinstance(k, str) and k.startswith("_"))}
    ctx = _ctx()
    if ctx: ctx.emit_lead(lead); return
    bus.emit("lead", lead)
def emit_gpu(data): bus.emit("gpu", data)
def emit_thought(msg, mood="thinking"):
    ctx = _ctx()
    if ctx: ctx.emit_thought(msg, mood); return
    bus.emit("thought", {"msg": msg, "mood": mood})


# ───────────────────────────────────────────────────────────────
# PERSISTENCE (atomic writes, thread-safe)
# ───────────────────────────────────────────────────────────────
_file_lock = threading.Lock()

def _atomic_write(path, data):
    tmp = path + ".tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except Exception:
        try: os.remove(tmp)
        except OSError: pass
        raise

def _safe_read(path, default=None):
    if default is None: default = []
    if not os.path.isfile(path): return default
    try:
        with open(path, "r", encoding="utf-8") as f: return json.load(f)
    except: return default

def ensure_dir(p): os.makedirs(p, exist_ok=True)

def load_leads():
    with _file_lock: leads = _safe_read(MASTER_LEADS_JSON, [])
    # Auto-purge corrupted AI thinking dumps from lead fields
    dirty = False
    _bad_kw = ("Thinking Process","**Sender","**Recipient","**Role:","**Tone:","**Format:","**Constraints","**Company Value","**Lead Info","Hi [name]","Follow format instructions","Exact format required","===SUBJECT===","===EMAIL===","I will follow","I need to reconcile")
    for ld in leads:
        for fld in ("email_subject","email_body","linkedin_note"):
            val = ld.get(fld,"")
            if val and any(kw in val for kw in _bad_kw):
                ld[fld] = ""
                dirty = True
    if dirty:
        with _file_lock: _atomic_write(MASTER_LEADS_JSON, leads)
    return leads

def save_leads_file(leads):
    with _file_lock:
        _atomic_write(MASTER_LEADS_JSON, leads)
        _wal_checkpoint()  # WAL is now consistent with file


# ───────────────────────────────────────────────────────────────
# WRITE-AHEAD LOG (WAL) — crash-proof persistence
# ───────────────────────────────────────────────────────────────
_wal_seq = 0

def _wal_write(op, data):
    """Append an operation to the WAL before mutating master_leads.json."""
    global _wal_seq
    with _file_lock:
        _wal_seq += 1
        entry = json.dumps({"op": op, "ts": datetime.now(timezone.utc).isoformat(), "seq": _wal_seq, "data": data}, default=str)
        with open(WAL_FILE, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
            f.flush()
            try: os.fsync(f.fileno())
            except: pass

def _wal_checkpoint():
    """Truncate WAL after successful save — data is now durable in master_leads.json."""
    try:
        with open(WAL_FILE, "w", encoding="utf-8") as f:
            pass  # Truncate
    except: pass

def _wal_replay():
    """On startup, replay any pending WAL entries that weren't checkpointed."""
    if not os.path.isfile(WAL_FILE): return
    try:
        with open(WAL_FILE, "r", encoding="utf-8") as f:
            lines = [l.strip() for l in f if l.strip()]
        if not lines: return
        leads = _safe_read(MASTER_LEADS_JSON, [])
        ids = {l.get("lead_id") for l in leads}
        replayed = 0
        for line in lines:
            try:
                entry = json.loads(line)
                if entry["op"] == "add":
                    lid = entry["data"].get("lead_id")
                    if lid and lid not in ids:
                        leads.append(entry["data"])
                        ids.add(lid)
                        replayed += 1
                elif entry["op"] == "update":
                    lid = entry["data"].get("lead_id")
                    idx = next((i for i, l in enumerate(leads) if l.get("lead_id") == lid), -1)
                    if idx >= 0:
                        for k, v in entry["data"].items():
                            if k != "lead_id": leads[idx][k] = v
                        replayed += 1
                elif entry["op"] == "delete":
                    lid = entry["data"].get("lead_id")
                    leads = [l for l in leads if l.get("lead_id") != lid]
                    replayed += 1
            except: pass
        if replayed:
            _atomic_write(MASTER_LEADS_JSON, leads)
            emit_log(f"WAL: Replayed {replayed} operations from crash journal", "save")
        _wal_checkpoint()
    except Exception as e:
        try: emit_log(f"WAL replay error: {e}", "warn")
        except: pass


# ───────────────────────────────────────────────────────────────
# HEARTBEAT + CRASH DETECTION
# ───────────────────────────────────────────────────────────────
def _heartbeat_loop():
    """Background thread: writes heartbeat every 10s for crash detection."""
    while True:
        try:
            with open(HEARTBEAT_FILE, "w", encoding="utf-8") as f:
                json.dump({"ts": time.time(), "pid": os.getpid(), "running": _agent_running}, f)
        except: pass
        time.sleep(10)

def _check_crash_recovery():
    """Disabled — always start fresh. Progress is saved via leads + seen_history."""
    return {"crashed": False}

def _clear_agent_state():
    """Clear agent state on startup so every launch is clean."""
    try: _atomic_write(AGENT_STATE_FILE, {"status": "idle"})
    except: pass


# ───────────────────────────────────────────────────────────────
# DEDUP + BLOCKLIST
# ───────────────────────────────────────────────────────────────
_seen_urls, _seen_fps = set(), set()
_domain_fails = {}

def load_seen_history():
    global _seen_urls, _seen_fps
    data = _safe_read(SEEN_HISTORY_JSON, {})
    _seen_urls = set(data.get("urls", []))
    _seen_fps = set(data.get("fingerprints", []))
    if _seen_urls: emit_log(f"History: {len(_seen_urls)} URLs, {len(_seen_fps)} fingerprints", "ok")

def save_seen_history():
    # In SaaS mode, seen history is persisted to DB in run_agent_scoped's finally block
    if _ctx():
        return
    try: _atomic_write(SEEN_HISTORY_JSON, {"urls": list(_seen_urls), "fingerprints": list(_seen_fps)})
    except: pass

def load_domain_blocklist():
    global _domain_fails
    raw = _safe_read(DOMAIN_BLOCKLIST, {})
    _domain_fails = raw if isinstance(raw, dict) else {}

def save_domain_blocklist():
    # In SaaS mode, domain fails are on ctx — no file needed
    if _ctx():
        return
    try: _atomic_write(DOMAIN_BLOCKLIST, _domain_fails)
    except: pass

def record_domain_fail(url):
    # Stability fix (Perplexity bug #69): use _hostish_netloc here so
    # the key written matches the key is_blocked reads. With raw
    # urlparse, schemeless URLs produced an empty key, then is_blocked
    # also read empty — both functions silently no-op'd, the fail
    # count never accumulated, and the domain was never auto-blocked.
    d = _hostish_netloc(url)
    if not d:
        return
    ctx = _ctx()
    if ctx:
        ctx.domain_fails[d] = ctx.domain_fails.get(d, 0) + 1
    else:
        _domain_fails[d] = _domain_fails.get(d, 0) + 1


def load_user_blocked():
    return _safe_read(USER_BLOCKED_FILE, {"domains":[], "org_names":[]})

def save_user_blocked(data):
    _atomic_write(USER_BLOCKED_FILE, data)

def is_user_blocked(url, org_name):
    """Check the user's manual blocklist for a given URL/org.

    Stability fix (Perplexity bug #68): two issues fixed together —
    (1) the previous version used raw urlparse(url).netloc which
        returns empty for schemeless URLs (competitor.com/page), so
        those bypassed the blocklist entirely (same root cause as
        bug #63). Now uses the _hostish_netloc helper.
    (2) `b in d or d in b` is too loose. If the user blocks "co" or
        "co.uk" the substring would match unrelated domains
        (facebook.com contains "co"). Replace with exact-or-subdomain-
        suffix match: d == b or d.endswith("." + b).
    """
    blocked = load_user_blocked()
    if url:
        try:
            d = _hostish_netloc(url)
            if d:
                _blocked_doms = [_hostish_netloc(b) for b in blocked.get("domains", [])]
                if any(b and (d == b or d.endswith("." + b)) for b in _blocked_doms):
                    return True
        except Exception:
            pass
    if org_name:
        n = org_name.lower().strip()
        if any((b or "").lower().strip() == n for b in blocked.get("org_names", [])):
            return True
    return False

def is_blocked(url):
    # Stability fix (Perplexity bug #69): match record_domain_fail by
    # using _hostish_netloc so schemeless URLs ("google.com/jobs")
    # don't bypass both the fail-count block AND the mega-corp block.
    d = _hostish_netloc(url)
    if not d:
        return False
    ctx = _ctx()
    fails = ctx.domain_fails if ctx else _domain_fails
    if fails.get(d, 0) >= 2:
        return True
    # Skip Fortune 500 / mega-corp domains. Normalize each entry the
    # same way so a list value with or without "www." matches.
    for mc in MEGA_CORP_DOMAINS:
        m = _hostish_netloc(mc) or (str(mc or "").lower().strip().replace("www.", ""))
        if m and (d == m or d.endswith("." + m)):
            return True
    return False


def classify_url(url: str) -> str:
    """Classify a URL for SSRF gating.

    Returns one of:
      "ok"            — public host, safe to fetch
      "private"       — resolves to private / loopback / link-local IP
      "unresolvable"  — DNS lookup failed (transient or permanent)
      "malformed"    — empty / invalid URL

    Callers can use the more specific return values to give better
    error messages (e.g. "Could not resolve hostname" vs "Webhook
    URL points at a private/loopback IP"). Existing callers using
    `is_private_url()` get a True for any non-"ok" result, preserving
    fail-closed behaviour.
    """
    try:
        import ipaddress
        import socket
        host = (urlparse(url).hostname or "").strip()
        if not host:
            return "malformed"
        h = host.lower().strip("[]")
        if h in ("localhost", "ip6-localhost", "ip6-loopback", "broadcasthost"):
            return "private"
        if h.endswith(".localhost") or h.endswith(".local"):
            return "private"
        try:
            ip = ipaddress.ip_address(h)
            return ("private" if (ip.is_private or ip.is_loopback or ip.is_link_local
                                   or ip.is_reserved or ip.is_multicast or ip.is_unspecified)
                    else "ok")
        except ValueError:
            pass
        try:
            infos = socket.getaddrinfo(h, None)
        except Exception:
            return "unresolvable"
        for info in infos:
            addr = info[4][0]
            try:
                ip = ipaddress.ip_address(addr)
            except ValueError:
                continue
            if (ip.is_private or ip.is_loopback or ip.is_link_local
                    or ip.is_reserved or ip.is_multicast or ip.is_unspecified):
                return "private"
        return "ok"
    except Exception:
        return "private"


def is_private_url(url: str) -> bool:
    """Return True if the URL resolves to a private / link-local / loopback
    host. Used as an SSRF guard on paths that fetch user-supplied URLs
    (wizard scan, research re-scrape, lead URL crawls). Keeps the agent
    from ever hitting 169.254.169.254 (cloud metadata), internal RFC1918
    ranges, or localhost, even if the URL came from a compromised or
    misconfigured SearXNG instance.

    Backwards-compat wrapper around `classify_url()`. Returns True for
    everything except "ok" so existing callers fail closed unchanged.
    """
    return classify_url(url) != "ok"

# ───────────────────────────────────────────────────────────────
# GDPR COMPLIANCE
# ───────────────────────────────────────────────────────────────
_robots_cache = {}  # domain -> (allowed: bool, cached_at: float)

def check_robots_txt(url):
    """Lightweight robots.txt check — only block truly forbidden paths."""
    # Disabled for now — too many false positives blocking legitimate prospect sites.
    # The agent uses polite delays (0.4s between URLs) and identifies as HuntovaBot.
    return True

def purge_expired_leads():
    """Auto-delete leads older than DATA_RETENTION_DAYS (GDPR Article 5(1)(e))."""
    try:
        from datetime import timedelta
        leads = load_leads()
        cutoff = (datetime.now(timezone.utc) - timedelta(days=DATA_RETENTION_DAYS)).isoformat()
        active = [l for l in leads if not l.get("found_date") or l["found_date"] > cutoff]
        purged = len(leads) - len(active)
        if purged > 0:
            save_leads_file(active)
            emit_log(f"GDPR: Purged {purged} leads older than {DATA_RETENTION_DAYS} days", "save")
        # Also purge archive
        archived = _safe_read(ARCHIVED_JSON, [])
        archived = [l for l in archived if (l.get("archived_date") or l.get("found_date") or "") > cutoff]
        _atomic_write(ARCHIVED_JSON, archived)
    except Exception as e:
        try: emit_log(f"GDPR purge error: {e}", "warn")
        except: pass

def gdpr_erasure(identifier):
    """GDPR Article 17 — Right to Erasure. Delete ALL data matching email or domain."""
    leads = load_leads()
    archived = _safe_read(ARCHIVED_JSON, [])
    before_count = len(leads) + len(archived)
    if "@" in identifier:
        # Erase by email
        email = identifier.lower().strip()
        leads = [l for l in leads if (l.get("contact_email") or "").lower() != email
                 and email not in [e.lower() for e in (l.get("_all_emails_found") or [])]]
        archived = [l for l in archived if (l.get("contact_email") or "").lower() != email]
    else:
        # Erase by domain
        domain = identifier.lower().replace("www.","").strip()
        leads = [l for l in leads if urlparse(l.get("org_website") or "").netloc.lower().replace("www.","") != domain
                 and urlparse(l.get("url") or "").netloc.lower().replace("www.","") != domain]
        archived = [l for l in archived if urlparse(l.get("org_website") or "").netloc.lower().replace("www.","") != domain]
    after_count = len(leads) + len(archived)
    save_leads_file(leads)
    _atomic_write(ARCHIVED_JSON, archived)
    # Rebuild seen history from remaining leads. Prefer the per-user
    # thread-local set if a ctx is active so concurrent gdpr_erasure
    # calls (theoretical at MAX_CONCURRENT_AGENTS=1, but adopting the
    # established pattern from record_domain_fail) don't race on the
    # module-level _seen_fps singleton.
    _new_fps = set()
    for l in leads:
        _new_fps.add(make_fingerprint(l))
        _new_fps.add(make_fingerprint_legacy(l))
    _gctx = _ctx()
    if _gctx and hasattr(_gctx, "seen_fps"):
        _gctx.seen_fps = _new_fps
    else:
        global _seen_urls, _seen_fps
        _seen_fps = _new_fps
    save_seen_history()
    return {"deleted": before_count - after_count, "remaining": len(leads)}

def load_master_leads(): return _safe_read(MASTER_LEADS_JSON, [])

def make_lead_id(lead):
    return hashlib.sha256(make_fingerprint(lead).encode()).hexdigest()[:12]

def save_master_leads(new_leads):
    if _ctx(): return  # SaaS mode: leads saved to DB, not shared file
    existing = load_master_leads()
    ids = {l.get("lead_id") for l in existing}
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    for lead in new_leads:
        lid = make_lead_id(lead)
        if lid in ids: continue
        e = dict(lead)
        e.update(lead_id=lid, found_date=now, run_id=_run_ts, email_status="new",
                 email_status_date=now, linkedin_status="not_sent",
                 linkedin_status_date=None, notes="",
                 status_history=[{"status": "new", "date": now}])
        existing.append(e); ids.add(lid); added += 1
    save_leads_file(existing)
    emit_log(f"Master: +{added} = {len(existing)} total", "save")


# ───────────────────────────────────────────────────────────────
# BACKUP
# ───────────────────────────────────────────────────────────────
def do_backup(reason="auto"):
    if _ctx(): return  # SaaS mode: no local backups
    ensure_dir(BACKUP_DIR)
    leads = load_leads()
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    path = os.path.join(BACKUP_DIR, f"backup_{ts}_{reason}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(leads, f, ensure_ascii=False, indent=2)
    _rotate_backups()
    return path

def _rotate_backups():
    """Tiered backup rotation: 10 hourly, 7 daily, 4 weekly, 12 monthly."""
    try:
        from datetime import timedelta
        files = sorted([f for f in os.listdir(BACKUP_DIR) if f.startswith("backup_")], reverse=True)
        now = datetime.now()
        keep = set()
        hourly = daily = weekly = monthly = 0
        for f in files:
            try:
                # Parse timestamp from filename: backup_YYYYMMDD_HHMMSS_reason.json
                parts = f.replace("backup_","").replace(".json","").split("_")
                dt = datetime.strptime(parts[0] + "_" + parts[1], "%Y%m%d_%H%M%S")
            except: keep.add(f); continue
            age = now - dt
            if age < timedelta(hours=10) and hourly < 10:
                keep.add(f); hourly += 1
            elif age < timedelta(days=7) and daily < 7:
                keep.add(f); daily += 1
            elif age < timedelta(weeks=4) and weekly < 4:
                keep.add(f); weekly += 1
            elif age < timedelta(days=365) and monthly < 12:
                keep.add(f); monthly += 1
        for f in files:
            if f not in keep:
                try: os.remove(os.path.join(BACKUP_DIR, f))
                except: pass
    except: pass

# Startup: only run when executed directly (not when imported by server.py)
_STANDALONE = __name__ == "__main__"
if _STANDALONE:
    _clear_agent_state()
    try: _wal_replay()
    except: pass
    try: do_backup("startup")
    except: pass
    try: purge_expired_leads()
    except: pass
    threading.Thread(target=_heartbeat_loop, daemon=True).start()


# ───────────────────────────────────────────────────────────────
# LIVE SCREENSHOT STREAMING
# ───────────────────────────────────────────────────────────────
_ss_page = None
_ss_active = threading.Event()
_ss_stop = threading.Event()
_ss_url = ""

def screenshot_streamer_loop():
    """Disabled — Playwright sync API cannot be called from background threads.
    Screenshots are now captured explicitly via emit_screenshot() from the agent thread."""
    pass  # Thread starts but does nothing — screenshots handled inline

def _page_op_start():
    """No-op — screenshot streaming disabled (Playwright not thread-safe)."""
    pass

def _page_op_done(url=""):
    """No-op — screenshot streaming disabled (Playwright not thread-safe)."""
    pass

# ───────────────────────────────────────────────────────────────
# UNIFIED HTTP HANDLER
# ───────────────────────────────────────────────────────────────
_agent_running = False
_agent_ctrl = {"action": None}
_agent_config = {"countries": [], "max_queries": 310, "results_per_query": 5}
_agent_ctrl_lock = threading.Lock()

def _get_agent_config():
    """Get agent config from thread-local ctx in SaaS mode, or module global in standalone."""
    ctx = _ctx()
    if ctx and hasattr(ctx, 'agent_config') and ctx.agent_config:
        return ctx.agent_config
    return _agent_config

def _check_stop():
    """Check if stop was requested. Returns True if agent should stop."""
    ctx = _ctx()
    if ctx:
        return ctx.check_stop()
    with _agent_ctrl_lock:
        return _agent_ctrl.get("action") == "stop"

def _check_budget():
    """Hunt budget gate. Returns a stop reason string if max_leads or
    timeout_minutes was exceeded, else None. Emits a status SSE event
    so the UI can show why the run stopped without polling.

    Both caps are opt-in (set via the Start Hunt popup). When unset,
    behaviour matches the pre-budget defaults: run until the user
    stops the agent or credits are exhausted.
    """
    ctx = _ctx()
    if not ctx:
        return None
    cfg = getattr(ctx, "agent_config", None) or {}
    _ml = cfg.get("max_leads")
    if _ml and len(ctx.all_leads) >= _ml:
        emit_log(f"Reached max-leads cap ({_ml}) — stopping cleanly", "warn")
        emit_status(f"Hit lead cap ({_ml})", "stopped")
        return "max_leads"
    _tm = cfg.get("timeout_minutes")
    _start = cfg.get("started_at")
    if _tm and _start and (time.time() - _start) >= _tm * 60:
        emit_log(f"Reached timeout ({_tm} min) — stopping cleanly", "warn")
        emit_status(f"Hit time cap ({_tm} min)", "stopped")
        return "timeout"
    return None

def _check_pause():
    """Block while paused. Returns True if stop requested during pause."""
    ctx = _ctx()
    if ctx:
        return ctx.check_pause()
    while True:
        with _agent_ctrl_lock:
            act = _agent_ctrl.get("action")
            if act == "stop":
                return True
            if act != "pause":
                return False
        time.sleep(0.5)

# ── Dashboard served from templates/index.html ──
_TEMPLATES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "templates")
_STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")

def _read_dashboard():
    with open(os.path.join(_TEMPLATES_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()




# ───────────────────────────────────────────────────────────────
# AGENT UTILITIES
# ───────────────────────────────────────────────────────────────
client = OpenAI(base_url=_API_URL, api_key=_API_KEY) if _API_KEY else None

# BYOK shim — drop-in for `client.chat.completions.create(**kwargs)`.
# Routes through providers.get_provider() so the user's chosen
# Gemini / Anthropic / OpenAI key handles the call regardless of
# which underlying SDK shape it expects. Returns an OpenAI-shaped
# response object so the existing `.choices[0].message.content`
# pattern is unchanged.
from providers import chat_compat as _byok_chat


class _ClientCompat:
    """Adapts the BYOK chat shim to the `client.chat.completions.create`
    namespace so existing call sites read like before. Only the chat
    completions surface is exposed — other OpenAI client features
    aren't currently used."""

    class _Chat:
        class _Completions:
            @staticmethod
            def create(**kwargs):
                return _byok_chat(**kwargs)
        completions = _Completions()
    chat = _Chat()


# Replace the legacy `client` global with the BYOK-aware adapter so
# all `client.chat.completions.create(...)` call sites Just Work for
# any configured provider. The original OpenAI(...) instance above
# is kept as `_legacy_client` for code that needs raw SDK access.
_legacy_client = client
client = _ClientCompat()

# Fallback model list — if primary model fails, try alternatives
_AI_FALLBACK_MODELS = [MODEL_ID]
if AI_PROVIDER == "gemini" and "gemini-2.5-flash" not in MODEL_ID:
    _AI_FALLBACK_MODELS.append("gemini-2.5-flash")

def _get_tier_model():
    """Get the AI model for the current user's tier.

    Provider-aware: returns the configured TIER_MODELS entry for the
    user's tier. TIER_MODELS is built per-provider in config.py so a
    user with HV_AI_PROVIDER=anthropic gets claude-* IDs, not Gemini.
    Falls back to MODEL_ID (also provider-aware) when tier is unknown.
    """
    from config import TIER_MODELS
    ctx = _ctx()
    if ctx:
        return TIER_MODELS.get(ctx.user_tier, MODEL_ID)
    return MODEL_ID  # standalone mode

def _get_tier_page_limit():
    """Get page text limit for the current user's tier.
    Agency → 6000 chars (Pro handles more context)
    Growth → 4000 chars
    Free → 2500 chars"""
    from config import TIER_PAGE_LIMITS
    ctx = _ctx()
    if ctx:
        return TIER_PAGE_LIMITS.get(ctx.user_tier, 4000)
    return 4000  # standalone mode

def _ai_json_kwargs(**kw):
    """Add response_format for JSON when using Gemini (reduces parse errors)
    + cap timeout per call so a stalled provider can't freeze the agent
    thread for the OpenAI SDK default of 600s.
    Stability fix per Perplexity round-5 review:
      - 30s baseline for cheap scorer/extractor calls
      - 60s for higher-effort planner/writer/critic calls (passing
        purpose='planner'|'writer'|'critic' or model contains 'pro' upgrades)
    Caller can still pass an explicit timeout=N to override.
    """
    _model = kw.get("model", MODEL_ID) or MODEL_ID
    if "gemini" in _model:
        kw["response_format"] = {"type": "json_object"}
    if "timeout" not in kw:
        purpose = kw.pop("_purpose", None)
        heavy = (
            purpose in ("planner", "writer", "critic")
            or "pro" in (_model or "").lower()
        )
        kw["timeout"] = 60 if heavy else 30
    return kw

def _ai_call(messages, temperature=0.3, max_tokens=2000, **extra_kw):
    """Make an AI completion call with tier-aware model + fallback. Returns raw content string."""
    # Use tier-appropriate model as primary
    _primary = _get_tier_model()
    _models = [_primary]
    # Add fallback if primary is Pro (fall back to Flash)
    if _primary != MODEL_ID:
        _models.append(MODEL_ID)
    # Add emergency fallback
    if "gemini-2.5-flash" not in _models and AI_PROVIDER == "gemini":
        _models.append("gemini-2.5-flash")
    last_err = None
    for m in _models:
        try:
            resp = client.chat.completions.create(**_ai_json_kwargs(
                model=m, messages=messages, temperature=temperature, max_tokens=max_tokens, **extra_kw))
            raw = (resp.choices[0].message.content or "").strip()
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            raw = re.sub(r"<think>.*$", "", raw, flags=re.DOTALL).strip()  # unclosed thinking
            return raw
        except Exception as e:
            last_err = e
            emit_log(f"AI model {m} failed: {e}", "warn")
            continue
    raise last_err or RuntimeError("All AI models failed")

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")
_NOREPLY_RE = re.compile(r"(noreply|no-reply|donotreply|mailer-daemon|postmaster|bounce|unsubscribe)", re.I)
_GENERIC_EMAIL_RE = re.compile(r"^(info|hello|contact|general|admin|support|noreply|no-reply|office|enquir|sales|team|hr|jobs|careers|press|media|marketing|billing|accounts|webmaster|hostmaster|abuse)@", re.I)

def extract_contacts_from_structured(lines):
    """Parse [JSON-LD-PERSON] and [JSON-LD-CONTACT] lines into contact data."""
    contacts = []
    for line in lines:
        if "[JSON-LD-PERSON]" in line:
            m = re.findall(r"(\w+)='([^']*)'", line)
            if m:
                d = dict(m)
                if d.get("name") and d["name"] != "":
                    contacts.append({
                        "name": d.get("name",""),
                        "role": d.get("role",""),
                        "email": d.get("email",""),
                        "phone": d.get("phone",""),
                        "source": "jsonld_person",
                        "confidence": 0.9
                    })
        elif "[JSON-LD-CONTACT]" in line:
            m = re.findall(r"(\w+)='([^']*)'", line)
            if m:
                d = dict(m)
                if d.get("email"):
                    contacts.append({
                        "name": "",
                        "role": d.get("type",""),
                        "email": d.get("email",""),
                        "phone": d.get("phone",""),
                        "source": "jsonld_contactpoint",
                        "confidence": 0.85
                    })
    return contacts

def validate_email(email):
    if not email or not isinstance(email, str): return None
    email = email.strip().lower()
    if not _EMAIL_RE.match(email): return None
    if _NOREPLY_RE.search(email.split("@")[0]): return None
    if email.endswith("@example.com") or email.endswith("@test.com"): return None
    return email

# ───────────────────────────────────────────────────────────────
# EMAIL VERIFICATION (MX lookup)
# ───────────────────────────────────────────────────────────────
def verify_email_mx(email):
    """Verify email has valid MX records via DNS resolution.

    Stability fix (multi-agent bug #12): the previous version called
    socket.setdefaulttimeout(3) globally and reset to None in finally.
    That setting is process-wide and affects every other thread's
    sockets — under our threaded agent runner it could shorten or
    lengthen unrelated HTTP calls mid-flight. It also didn't actually
    time-out getaddrinfo (a C-level call), so it provided no real
    protection while introducing a race. Now we run the lookup in a
    short-lived worker thread and bound the wait there.
    """
    if not email or "@" not in email: return False
    domain = email.split("@")[1]
    import socket
    from concurrent.futures import ThreadPoolExecutor, TimeoutError as _FutTimeout
    try:
        with ThreadPoolExecutor(max_workers=1) as _ex:
            fut = _ex.submit(socket.getaddrinfo, domain, None)
            try:
                fut.result(timeout=3)
                return True  # Domain resolves
            except _FutTimeout:
                return True  # Slow DNS — assume valid, don't block agent
            except socket.gaierror:
                return False  # Domain doesn't exist
            except Exception:
                return True  # Any other error — assume valid
    except Exception:
        return True  # Couldn't even spawn the executor — assume valid

# ───────────────────────────────────────────────────────────────
# DATE PARSING (find event dates in any format)
# ───────────────────────────────────────────────────────────────
_MONTH_MAP = {"jan":1,"feb":2,"mar":3,"apr":4,"may":5,"jun":6,"jul":7,"aug":8,"sep":9,"oct":10,"nov":11,"dec":12,
              "january":1,"february":2,"march":3,"april":4,"june":6,"july":7,"august":8,"september":9,"october":10,"november":11,"december":12}

def extract_event_dates(text):
    """Extract dates from text in any common format. Returns list of (date_str, datetime) tuples."""
    if not text: return []
    dates = []
    # Pattern 1: "15-17 June 2026" (date ranges)
    for m in re.finditer(r'(\d{1,2})\s*[-–]\s*(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+(\d{4})', text, re.I):
        try:
            month = _MONTH_MAP.get(m.group(3).lower()[:3])
            d1, d2 = int(m.group(1)), int(m.group(2))
            if month and 1 <= d1 <= 31 and 1 <= d2 <= 31:
                dates.append((m.group(0), datetime(int(m.group(4)), month, d1)))
        except: pass
    for m in re.finditer(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+(\d{1,2})(?:\s*[-–]\s*\d{1,2})?,?\s*(\d{4})', text, re.I):
        try:
            month = _MONTH_MAP.get(m.group(1).lower()[:3])
            if month: dates.append((m.group(0), datetime(int(m.group(3)), month, int(m.group(2)))))
        except: pass
    for m in re.finditer(r'(\d{1,2})\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\w*\.?\s+(\d{4})', text, re.I):
        try:
            month = _MONTH_MAP.get(m.group(2).lower()[:3])
            if month: dates.append((m.group(0), datetime(int(m.group(3)), month, int(m.group(1)))))
        except: pass
    # Pattern 2: ISO dates "2026-06-15"
    for m in re.finditer(r'(\d{4})-(\d{2})-(\d{2})', text):
        try: dates.append((m.group(0), datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))))
        except: pass
    # Pattern 3: "15/06/2026" or "06/15/2026"
    for m in re.finditer(r'(\d{1,2})/(\d{1,2})/(\d{4})', text):
        try:
            d1, d2, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if d1 > 12: dates.append((m.group(0), datetime(y, d2, d1)))  # DD/MM/YYYY
            else: dates.append((m.group(0), datetime(y, d1, d2)))  # MM/DD/YYYY
        except: pass
    # Filter: only keep dates from this year or next
    now = datetime.now()
    valid = [(s, d) for s, d in dates if now.year <= d.year <= now.year + 2]
    # Sort by date
    valid.sort(key=lambda x: x[1])
    return valid

def classify_event_timing(dates):
    """Classify urgency based on extracted dates. Returns (urgency, days_until, date_str)."""
    if not dates: return "unknown", None, None
    now = datetime.now()
    future = [(s, d) for s, d in dates if d > now]
    if not future:
        past = [(s, d) for s, d in dates if d <= now]
        if past:
            days_ago = (now - past[-1][1]).days
            if days_ago < 30: return "just_passed", -days_ago, past[-1][0]
            return "past", -days_ago, past[-1][0]
        return "unknown", None, None
    nearest = future[0]
    days_until = (nearest[1] - now).days
    if days_until <= 14: return "imminent", days_until, nearest[0]
    if days_until <= 60: return "upcoming", days_until, nearest[0]
    if days_until <= 180: return "planned", days_until, nearest[0]
    return "distant", days_until, nearest[0]

# ───────────────────────────────────────────────────────────────
# QUERY FEEDBACK LOOP
# ───────────────────────────────────────────────────────────────
_query_stats = {}  # Standalone fallback; production uses ctx._query_stats

def _get_query_stats():
    """Get per-run query stats dict from ctx, or module global in standalone."""
    ctx = _ctx()
    if ctx:
        if not hasattr(ctx, '_query_stats'):
            ctx._query_stats = {}
        return ctx._query_stats
    return _query_stats

def _track_query_result(query, lead_score):
    """Track which queries produce good leads."""
    qs = _get_query_stats()
    qh = hashlib.md5(query.encode()).hexdigest()[:8]
    if qh not in qs:
        qs[qh] = {"query": query[:80], "leads": 0, "high_score": 0, "total_urls": 0}
    qs[qh]["total_urls"] += 1
    if lead_score >= MIN_SCORE_TO_KEEP:
        qs[qh]["leads"] += 1
    if lead_score >= 8:
        qs[qh]["high_score"] += 1

def _sort_queries_by_yield(queries):
    """Sort queries so historically productive ones run first."""
    qs = _get_query_stats()
    def _yield_score(q):
        qh = hashlib.md5(q.encode()).hexdigest()[:8]
        st = qs.get(qh)
        if not st or st["total_urls"] == 0: return 0.5  # Unknown = middle priority
        return (st["high_score"] * 3 + st["leads"]) / max(st["total_urls"], 1)
    return sorted(queries, key=_yield_score, reverse=True)

# ───────────────────────────────────────────────────────────────
# SCORING VALIDATION (second-pass verification)
# ───────────────────────────────────────────────────────────────
def validate_score(lead, page_text):
    """Quick AI validation: is the score justified by the evidence?"""
    score = lead.get("fit_score", 0)
    if score < 5: return score  # Don't waste AI call on low scores
    evidence = lead.get("evidence_quote", "")
    why = lead.get("why_fit", "")
    org = lead.get("org_name", "")
    event = lead.get("event_name", "")
    sb = lead.get("score_breakdown", {})
    sb_summary = "; ".join(f"{k}={v.get('score',0)}" for k, v in sb.items() if isinstance(v, dict))
    try:
        prompt = f"""Verify this lead score. Is it justified?

ORG: {org} | EVENT: {event} | SCORE: {score}/10
SUB-SCORES: {sb_summary}
WHY: {why}
EVIDENCE: "{evidence[:300]}"
PAGE EXCERPT: {(page_text or '')[:500]}

Reply with ONLY a JSON object: {{"verified_score": 0-10, "adjustment_reason": "one line"}}
If the score is fair, return the same score. If it's too high (evidence doesn't support it), lower it. If too low, raise it."""
        resp = client.chat.completions.create(**_ai_json_kwargs(
            model=_get_tier_model(),
            messages=[{"role":"system","content":"You verify lead scores. Be strict — only evidence-backed scores survive. Output ONLY JSON."},
                      {"role":"user","content":prompt}],
            temperature=0.15, max_tokens=200))
        raw = (resp.choices[0].message.content or "").strip()
        js = extract_json(raw)
        if js:
            result = json.loads(js)
            verified = clamp_int(result.get("verified_score", score), 0, 10, score)
            if verified != score:
                reason = (result.get("adjustment_reason") or "").strip()[:100]
                emit_log(f"Score validated: {score} -> {verified} ({reason})", "ai")
            return verified
    except Exception as _ve:
        emit_log(f"Score validation skipped: {_ve}", "warn")
    return score  # On error, keep original

def clamp_int(v, lo, hi, default=0):
    try: return max(lo, min(hi, int(v)))
    except: return default

def normalize_url(url):
    try:
        p = urlparse(url.strip())
        q = [(k,v) for k,v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"gclid","fbclid","ref","source"}]
        return urlunparse(p._replace(fragment="", query=urlencode(q, doseq=True))).rstrip("/")
    except: return url.strip()

def _fp_normalize(s):
    s = str(s or "").lower().strip()
    s = re.sub(r"\b(the|a|an|and|of|for|in|on|at|series|annual|conference|webinar|summit|congress|forum|event|programme|virtual|online|digital|2025|2026|2027|inc|incorporated|corp|corporation|ltd|limited|llc|gmbh|sa|srl|bv|plc|ag|co|company|group|holdings)\b","",s)
    return re.sub(r"\W+","",s)

def _hostish_netloc(raw):
    """Extract a normalized hostname from a URL-or-host string.

    Stability fix (Perplexity bug #63): plain `urlparse('example.com')`
    returns netloc='' because there's no scheme — the hostname lands
    in path. That made make_fingerprint fall back to the org+event
    fingerprint for any schemeless org_website, so the SAME prospect
    submitted with 'example.com' versus 'https://example.com' got
    different lead_ids → duplicate leads + double-charged credits.
    Now we re-parse with a synthetic 'https://' prefix when netloc is
    empty.
    """
    raw = str(raw or "").strip()
    if not raw:
        return ""
    p = urlparse(raw)
    if not p.netloc and p.path and "://" not in raw and not raw.startswith("//"):
        p = urlparse("https://" + raw)
    return (p.netloc or "").lower().replace("www.", "")


def make_fingerprint(lead):
    """Primary fingerprint for dedup. INT-005: domain-based when available."""
    org = _fp_normalize(lead.get('org_name',''))
    evt = _fp_normalize(lead.get('event_name',''))[:24]
    dom = _hostish_netloc(lead.get('org_website',''))
    # INT-005: Use domain as primary dedup key when available.
    # Same company with different events/pages = same lead (prevents duplicate outreach).
    # Fall back to org+event when no domain is available.
    if dom:
        return f"{dom}|{_fp_normalize(lead.get('country',''))}"
    return f"{org}|{evt}|{_fp_normalize(lead.get('country',''))}"

def make_fingerprint_legacy(lead):
    """Legacy fingerprint format for backward-compat with existing seen_fingerprints in DB."""
    org = _fp_normalize(lead.get('org_name',''))
    evt = _fp_normalize(lead.get('event_name',''))[:24]
    return f"{org or urlparse(lead.get('org_website','') or '').netloc.lower().replace('www.','')}|{evt}|{_fp_normalize(lead.get('country',''))}"


def parse_delimited(text):
    """Parse AI output — aggressively strips thinking/reasoning dumps."""
    if not text: return None
    t = text
    # Strip <think> tags
    t = re.sub(r"<think>.*?</think>","",t,flags=re.DOTALL).strip()
    t = re.sub(r"<\|.*?\|>","",t).strip()

    # ── STRATEGY 1: Delimiter format ──
    if "===SUBJECT===" in t or "===EMAIL===" in t:
        result = {}
        for key in ("SUBJECT","EMAIL","LINKEDIN","FINDINGS","CONTACT_NAME","CONTACT_ROLE","WEBSITE"):
            pattern = f"==={key}===" + r"\s*\n(.*?)(?=\n===|$)"
            m = re.search(pattern, t, re.DOTALL)
            if m: result[key.lower()] = m.group(1).strip()
        if result.get("email") and len(result["email"]) > 50:
            # Clean any residual thinking from the email
            email = _clean_thinking(result["email"])
            if len(email) > 50:
                return {
                    "email_subject": _clean_subject(result.get("subject","")),
                    "email_body": email,
                    "linkedin_note": result.get("linkedin",""),
                    "key_findings": [f.strip() for f in result.get("findings","").split("\n") if f.strip() and len(f.strip()) > 5][:5],
                    "contact_name": result.get("contact_name",""),
                    "contact_role": result.get("contact_role",""),
                    "org_website": result.get("website",""),
                }

    # ── STRATEGY 2: JSON ──
    if "{" in t:
        t_clean = re.sub(r"^.*?(?=\{)", "", t, count=1, flags=re.DOTALL)
        try:
            depth, in_str, esc_flag = 0, False, False
            start = t_clean.index("{")
            for i in range(start, len(t_clean)):
                c = t_clean[i]
                if esc_flag: esc_flag = False; continue
                if c == "\\": esc_flag = True; continue
                if c == '"': in_str = not in_str; continue
                if in_str: continue
                if c == "{": depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        js = t_clean[start:i+1]
                        rd = json.loads(js)
                        body = rd.get("email_body","")
                        if body and len(body) > 50 and "Thinking" not in body[:30]:
                            rd["email_body"] = _clean_thinking(body)
                            rd["email_subject"] = _clean_subject(rd.get("email_subject",""))
                            return rd
                        break
        except: pass

    # ── STRATEGY 3: Find email-like text ──
    # Look for greeting lines
    greeting_match = re.search(r"((?:Hi|Hello|Dear)\b.*?)$", t, re.MULTILINE)
    if greeting_match:
        email_start = greeting_match.start()
        email_text = t[email_start:]
        email_text = _clean_thinking(email_text)
        if len(email_text) > 80:
            return {
                "email_subject": "",
                "email_body": email_text,
                "linkedin_note": "",
                "key_findings": [],
            }

    return None

def _clean_thinking(text):
    """Strip reasoning/thinking artifacts from text, but preserve legitimate email content."""
    lines = text.split("\n")
    clean = []
    for line in lines:
        l = line.strip()
        # Keep empty lines as paragraph breaks
        if not l:
            if clean: clean.append("")
            continue
        # Only skip lines that are clearly AI reasoning artifacts, not email content
        if any(kw in l.lower() for kw in ("thinking process","**sender","**recipient","**tone description","**format","**constraints","**role description","**company info","**lead info","**rules","follow format","exact format","=== analysis","=== approach","=== strategy")): continue
        if l.startswith("Format:") or l.startswith("Constraints:") or l.startswith("Rules:"): continue
        # Skip markdown headers that are clearly metadata (not email content)
        if l.startswith("##") and any(kw in l.lower() for kw in ("approach","analysis","strategy","reasoning","thinking","output")): continue
        clean.append(line)
    # Remove leading/trailing empty lines
    while clean and not clean[0].strip(): clean.pop(0)
    while clean and not clean[-1].strip(): clean.pop()
    return "\n".join(clean)

def _clean_subject(subj):
    """Clean subject line — strip thinking prefixes."""
    s = subj.strip()
    # Remove "Subject:" prefix
    s = re.sub(r"^Subject:\s*", "", s, flags=re.I)
    # If it contains thinking words, it's garbage
    if any(kw in s.lower() for kw in ("thinking","process","analyze","**","format","constraints")):
        return ""
    if len(s) > 80: return s[:80]
    return s

def extract_json(text):
    if not text: return None
    t = re.sub(r"<think>.*?</think>","",text,flags=re.DOTALL).strip()
    # Also strip unclosed <think> tags (truncated responses)
    t = re.sub(r"<think>.*$","",t,flags=re.DOTALL).strip()
    t = re.sub(r"<\|.*?\|>","",t).strip()
    t = re.sub(r"```(?:json)?","",t).strip()
    # Try array first (for query generation responses)
    arr_start = t.find("[")
    obj_start = t.find("{")
    if arr_start >= 0 and (obj_start < 0 or arr_start < obj_start):
        depth, in_str, esc = 0, False, False
        for i in range(arr_start, len(t)):
            c = t[i]
            if esc: esc = False; continue
            if c == "\\": esc = True; continue
            if c == '"': in_str = not in_str; continue
            if in_str: continue
            if c == "[": depth += 1
            elif c == "]":
                depth -= 1
                if depth == 0:
                    candidate = t[arr_start:i+1]
                    # Validate it's real JSON, not prose brackets like "[see above]"
                    try:
                        json.loads(candidate)
                        return candidate
                    except json.JSONDecodeError:
                        break  # Not valid JSON — fall through to object branch
        # Truncated array — try to close and validate
        if depth > 0:
            fragment = t[arr_start:]
            if in_str:
                fragment += '"'
            candidate = fragment + "]" * depth
            try:
                json.loads(candidate)
                return candidate
            except json.JSONDecodeError:
                # Trim back to last complete element
                last_comma = fragment.rfind(",")
                if last_comma > 0:
                    trimmed = fragment[:last_comma] + "]" * depth
                    try:
                        json.loads(trimmed)
                        return trimmed
                    except json.JSONDecodeError:
                        pass
        # Array branch failed — fall through to try object branch
    # Strip thinking preamble — models often dump reasoning before JSON
    t = re.sub(r"^.*?(?=\{)", "", t, count=1, flags=re.DOTALL)
    start = t.find("{")
    if start == -1: return None
    depth, in_str, esc = 0, False, False
    for i in range(start, len(t)):
        c = t[i]
        if esc: esc = False; continue
        if c == "\\": esc = True; continue
        if c == '"': in_str = not in_str; continue
        if in_str: continue
        if c == "{": depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0: return t[start:i+1]
    # Truncated object — try to close and validate
    if depth > 0:
        fragment = t[start:]
        # If we're inside an open string, close it first
        if in_str:
            fragment += '"'
        # Try closing braces
        candidate = fragment + "}" * depth
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            # Try trimming back to last complete key-value pair
            # Find last complete "key": value pattern
            last_comma = fragment.rfind(",")
            if last_comma > 0:
                trimmed = fragment[:last_comma] + "}" * depth
                try:
                    json.loads(trimmed)
                    return trimmed
                except json.JSONDecodeError:
                    pass
            return None
    return None

def count_sentences(s):
    if not s: return 0
    return len([p for p in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", s.strip())) if p.strip()])


# ───────────────────────────────────────────────────────────────
# SMART CONTACT FINDER
# ───────────────────────────────────────────────────────────────
_CONTACT_PAGE_KW = re.compile(r"(contact|about|team|people|staff|our-team|about-us|get-in-touch|who-we-are|leadership|management|board|directors|executive|founders|meet-the-team|our-people|connect|enquir|inquir|support|help)", re.I)

def find_contact_pages(page, base_url, n=5):
    """Find /contact, /about, /team pages on the same domain. Also tries common paths."""
    if page is None: return []
    domain = urlparse(base_url).netloc.lower()
    parsed = urlparse(base_url)
    base = f"{parsed.scheme}://{parsed.netloc}"
    seen = {normalize_url(base_url)}
    out = []
    # First: scan links on current page
    try:
        for a in page.query_selector_all("a[href]")[:300]:
            href = (a.get_attribute("href") or "").strip()
            text = (a.inner_text() or "").strip()
            if not href or href.startswith("#") or href.startswith("mailto:"): continue
            full = normalize_url(urljoin(base_url, href))
            if urlparse(full).netloc.lower() != domain or full in seen: continue
            if _CONTACT_PAGE_KW.search(href) or _CONTACT_PAGE_KW.search(text):
                seen.add(full); out.append(full)
                if len(out) >= n: break
    except: pass
    # Second: try common contact page paths if we didn't find enough
    if len(out) < 2:
        common_paths = ["/contact","/about","/team","/about-us","/contact-us",
                        "/our-team","/people","/leadership","/get-in-touch","/staff"]
        for cp in common_paths:
            full = normalize_url(f"{base}{cp}")
            if full not in seen:
                seen.add(full); out.append(full)
                if len(out) >= n: break
    return out[:n]

def extract_emails_from_text(text):
    """Extract all valid emails from page text."""
    raw = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text or "")
    return [e for e in raw if validate_email(e)]

def extract_linkedin_urls(html):
    """Extract LinkedIn company and personal URLs from HTML."""
    urls = {"org": None, "contact": None}
    for m in re.findall(r'https?://(?:www\.)?linkedin\.com/(?:company|in)/[a-zA-Z0-9_-]+/?', html or ""):
        if "/company/" in m and not urls["org"]: urls["org"] = m.rstrip("/")
        elif "/in/" in m and not urls["contact"]: urls["contact"] = m.rstrip("/")
    return urls

def enrich_contact(page, base_url, lead):
    """Enhanced contact enrichment: crawl contact pages, extract emails, phones, LinkedIn, guess patterns."""
    emit_log(f"🔍 Hunting contact info for: {lead.get('org_name','?')}", "fetch")
    emit_thought("Good lead! Hunting for contact details...", "search")

    pages_to_check = find_contact_pages(page, base_url, n=5)
    all_emails = []
    all_phones = []
    all_names = []  # Track names found on team/about pages for email guessing

    for curl in pages_to_check:
        if _check_stop(): break
        try:
            emit_log(f"🕸️ Crawling contact page: {curl[:60]}", "fetch")
            page.goto(curl, timeout=FETCH_TIMEOUT_MS, wait_until="domcontentloaded")
            try: page.wait_for_load_state("networkidle", timeout=8000)
            except: pass
            text = page.inner_text("body") or ""
            html = page.content()
            all_emails.extend(extract_emails_from_text(text))
            # mailto: links
            for el in page.query_selector_all('a[href^="mailto:"]')[:10]:
                href = (el.get_attribute("href") or "").replace("mailto:","").split("?")[0].strip()
                v = validate_email(href)
                if v: all_emails.append(v)
            # LinkedIn
            li = extract_linkedin_urls(html)
            if li["org"] and not lead.get("org_linkedin"): lead["org_linkedin"] = li["org"]
            if li["contact"] and not lead.get("contact_linkedin"): lead["contact_linkedin"] = li["contact"]
            # Contact page URL
            if re.search(r"contact|get-in-touch|enquir|inquir", curl, re.I):
                lead["contact_page_url"] = curl
            # Phone numbers
            phones = extract_phone_numbers(text, html)
            all_phones.extend(phones)
            # Social profiles (from contact/about pages)
            socials = extract_social_profiles(html)
            if socials.get("twitter") and not lead.get("_social_twitter"):
                lead["_social_twitter"] = socials["twitter"]
            if socials.get("linkedin") and not lead.get("org_linkedin"):
                lead["org_linkedin"] = socials["linkedin"]
            # Extract names from team/about pages for email pattern guessing
            if re.search(r"team|people|staff|about|leadership", curl, re.I):
                # Look for name patterns near role keywords
                _role_kw = re.compile(r'(director|manager|head of|vp |vice president|coordinator|chief|ceo|coo|cmo|cto|cfo|founder|president|partner|owner|principal)', re.I)
                for line in text.split("\n"):
                    if _role_kw.search(line):
                        # Try to extract a name (2-3 capitalized words before/near the role)
                        name_match = re.search(r'([A-Z][a-z]+ [A-Z][a-z]+(?:\s+[A-Z][a-z]+)?)', line)
                        if name_match:
                            all_names.append({"name": name_match.group(1), "context": line.strip()[:100]})
        except: pass
        time.sleep(0.4)

    # Pick best email (prefer personal name > any non-generic > generic)
    _generic_re = r"^(info|hello|contact|general|admin|support|noreply|no-reply|office|enquir|sales|team|hr|jobs|careers|press|media|marketing|billing|accounts|webmaster|hostmaster|abuse)@"
    if all_emails:
        _personal = [e for e in all_emails if not re.search(_generic_re, e, re.I)]
        if _personal:
            lead["contact_email"] = validate_email(_personal[0])
            lead["_contact_source"] = "crawl_personal"
            lead["_contact_confidence"] = 0.8
        elif all_emails:
            # Accept generic only as last resort, mark as low confidence
            _valid_generic = validate_email(all_emails[0])
            if _valid_generic:
                lead["contact_email"] = _valid_generic
                lead["_contact_source"] = "crawl_generic"
                lead["_contact_confidence"] = 0.3
        if lead.get("contact_email"):
            emit_log(f"📧 Found email: {lead['contact_email']} (source: {lead.get('_contact_source','unknown')})", "ok")
            emit_thought("Found a contact email!", "happy")

    # Email pattern guessing as fallback
    if not lead.get("contact_email") and lead.get("contact_name"):
        domain = urlparse(lead.get("org_website") or base_url).netloc.lower().replace("www.","")
        if domain and "." in domain:
            patterns = guess_email_patterns(domain, lead["contact_name"])
            if patterns:
                lead["_guessed_emails"] = patterns[:4]
                emit_log(f"🔮 Guessed {len(patterns[:4])} email patterns for {lead['contact_name']}", "ai")
    # If we found names on team pages but don't have a contact name yet
    elif not lead.get("contact_name") and all_names:
        # Pick the most senior/relevant person (prefer leadership roles)
        _leaders = [n for n in all_names if re.search(r"CEO|founder|director|head of|VP|chief|managing|partner|owner|president", n["context"], re.I)]
        best = _leaders[0] if _leaders else all_names[0]
        lead["contact_name"] = best["name"]
        if not lead.get("_contact_source"):
            lead["_contact_source"] = "team_page"
            lead["_contact_confidence"] = 0.7
        # Extract role
        role_match = re.search(r'((?:head of|director of|vp of|chief|manager|coordinator)\s*\w+(?:\s+\w+)?)', best["context"], re.I)
        if role_match and not lead.get("contact_role"):
            lead["contact_role"] = role_match.group(1).strip()[:80]
        emit_log(f"👤 Found team member: {best['name']} — {best['context'][:50]}", "ai")
        # Now try email pattern guessing
        domain = urlparse(lead.get("org_website") or base_url).netloc.lower().replace("www.","")
        if domain and "." in domain:
            patterns = guess_email_patterns(domain, best["name"])
            if patterns:
                lead["_guessed_emails"] = patterns[:4]

    # Phone numbers
    if all_phones and not lead.get("contact_phone"):
        lead["contact_phone"] = all_phones[0]
        emit_log(f"📞 Found phone: {all_phones[0]}", "ok")

    # Store all found emails for reference (even generic ones)
    if len(all_emails) > 1:
        lead["_all_emails_found"] = list(set(all_emails))[:8]

    return lead


# ───────────────────────────────────────────────────────────────
# ADVANCED EXTRACTION HELPERS
# ───────────────────────────────────────────────────────────────

def extract_video_embeds(html):
    """Extract video embed info from HTML (YouTube, Vimeo, Wistia, Vidyard, Brightcove)."""
    videos = []
    html_s = html or ""
    # YouTube embeds + links
    for vid_id in re.findall(r'(?:youtube\.com/embed/|youtube\.com/watch\?v=|youtu\.be/)([\w-]{11})', html_s):
        videos.append({"platform":"youtube","id":vid_id,"url":f"https://youtube.com/watch?v={vid_id}"})
    # YouTube channels
    for ch in re.findall(r'youtube\.com/(?:channel|c|@)([\w-]+)', html_s):
        videos.append({"platform":"youtube_channel","id":ch,"url":f"https://youtube.com/@{ch}"})
    # Vimeo
    for vid_id in re.findall(r'(?:vimeo\.com/(?:video/)?|player\.vimeo\.com/video/)(\d+)', html_s):
        videos.append({"platform":"vimeo","id":vid_id,"url":f"https://vimeo.com/{vid_id}"})
    # Wistia
    for vid_id in re.findall(r'(?:wistia\.com/medias/|wistia\.net/embed/iframe/)([\w]+)', html_s):
        videos.append({"platform":"wistia","id":vid_id,"url":f"https://fast.wistia.com/medias/{vid_id}"})
    # Vidyard
    for vid_id in re.findall(r'(?:vidyard\.com/share/|play\.vidyard\.com/)([\w]+)', html_s):
        videos.append({"platform":"vidyard","id":vid_id,"url":f"https://share.vidyard.com/watch/{vid_id}"})
    # Brightcove
    if "players.brightcove.net" in html_s or "brightcove" in html_s.lower():
        videos.append({"platform":"brightcove","id":"detected","url":""})
    # Livestream/streaming embeds
    for m in re.findall(r'(livestream\.com/accounts/\d+/events/\d+)', html_s):
        videos.append({"platform":"livestream","id":"detected","url":f"https://{m}"})
    # Zoom webinar recordings
    for m in re.findall(r'(zoom\.us/rec/(?:share|play)/[\w.-]+)', html_s):
        videos.append({"platform":"zoom_recording","id":"detected","url":f"https://{m}"})
    # Deduplicate
    seen = set()
    unique = []
    for v in videos:
        key = f"{v['platform']}:{v['id']}"
        if key not in seen:
            seen.add(key); unique.append(v)
    return unique[:15]

def fetch_video_metrics(page, video, return_url=""):
    """Visit a video page and extract real metrics (views, subscribers, etc.)."""
    metrics = {"platform": video.get("platform",""), "url": video.get("url",""),
               "title":"", "view_count":0, "channel":"", "subscribers":"", "thumbnail":"", "production_score":1}
    url = video.get("url","")
    plat = video.get("platform","")
    try:
        if plat == "youtube" and url:
            # oEmbed first (fast, no page load)
            try:
                import urllib.request as _ur
                _oe = _ur.urlopen(f"https://www.youtube.com/oembed?url={url}&format=json", timeout=5)
                _od = json.loads(_oe.read().decode())
                metrics["title"] = _od.get("title","")
                metrics["channel"] = _od.get("author_name","")
                metrics["thumbnail"] = _od.get("thumbnail_url","")
            except: pass
            # Visit page for view count + subscriber count
            try:
                _page_op_start()
                page.goto(url, timeout=VIDEO_FETCH_TIMEOUT, wait_until="domcontentloaded")
                _body = page.inner_text("body") or ""
                # View count
                _vm = re.search(r"([\d,]+)\s*views", _body)
                if _vm: metrics["view_count"] = int(_vm.group(1).replace(",",""))
                # Meta tag fallback
                if not metrics["view_count"]:
                    _mc = page.query_selector('meta[itemprop="interactionCount"]')
                    if _mc: metrics["view_count"] = int((_mc.get_attribute("content") or "0").replace(",",""))
                # Subscriber count
                _sm = re.search(r"([\d.]+[KMB]?)\s*subscribers", _body, re.I)
                if _sm: metrics["subscribers"] = _sm.group(1)
                _page_op_done(url)
                emit_screenshot(page, url)
            except:
                _page_op_done()
            # Score
            v = metrics["view_count"]
            metrics["production_score"] = 5 if v > 1000000 else 4 if v > 100000 else 3 if v > 10000 else 2 if v > 1000 else 1
        elif plat == "vimeo" and url:
            try:
                import urllib.request as _ur
                _oe = _ur.urlopen(f"https://vimeo.com/api/oembed.json?url={url}", timeout=5)
                _od = json.loads(_oe.read().decode())
                metrics["title"] = _od.get("title","")
                metrics["channel"] = _od.get("author_name","")
                metrics["thumbnail"] = _od.get("thumbnail_url","")
                metrics["production_score"] = 3  # Vimeo = usually decent quality
            except: pass
        elif plat in ("wistia","vidyard","brightcove"):
            metrics["production_score"] = 4  # Professional hosting = invested in video
            metrics["title"] = f"[{plat} hosted content]"
    except: pass
    # Navigate back to original page
    if return_url:
        try:
            _page_op_start()
            page.goto(return_url, timeout=FETCH_TIMEOUT_MS, wait_until="domcontentloaded")
            _page_op_done(return_url)
        except:
            _page_op_done()
    return metrics

def assess_video_quality(metrics_list):
    """Assess overall video production quality from a list of video metrics."""
    if not metrics_list: return {"quality":"none_found", "notes":["No video content found"]}
    scores = [m.get("production_score",1) for m in metrics_list]
    avg = sum(scores) / len(scores)
    views = [m.get("view_count",0) for m in metrics_list]
    avg_views = sum(views) // max(len(views),1)
    subs = [m.get("subscribers","") for m in metrics_list if m.get("subscribers")]
    pro_hosts = [m for m in metrics_list if m.get("platform") in ("wistia","vidyard","brightcove")]
    notes = []
    if pro_hosts: notes.append(f"Uses professional hosting ({pro_hosts[0]['platform']})")
    if avg_views > 0: notes.append(f"Average {avg_views:,} views per video")
    if subs: notes.append(f"Channel: {subs[0]} subscribers")
    quality = "professional" if avg >= 3.5 or pro_hosts else "decent" if avg >= 2.5 else "basic" if avg >= 1.5 else "poor"
    return {"quality": quality, "avg_views": avg_views, "max_score": max(scores), "notes": notes,
            "has_professional_hosting": bool(pro_hosts)}

def extract_tech_stack(html):
    """Detect technology stack from HTML source (scripts, meta, classes, headers)."""
    tech = []
    h = (html or "").lower()
    # Analytics & tracking
    _tech_sigs = {
        "Google Analytics": ["google-analytics.com","googletagmanager.com","gtag(","ga('send"],
        "HubSpot": ["js.hs-scripts.com","hbspt.forms","hs-banner"],
        "Salesforce": ["salesforce.com","pardot.com","force.com"],
        "Marketo": ["marketo.com","munchkin","mktoForms"],
        "Mailchimp": ["mailchimp.com","mc.us","list-manage.com"],
        "ActiveCampaign": ["activecampaign.com","_ac_tracking"],
        "Intercom": ["intercom.io","intercomSettings"],
        "Drift": ["drift.com","driftt.com"],
        "Zendesk": ["zendesk.com","zdassets.com"],
        "Calendly": ["calendly.com"],
        "Eventbrite": ["eventbrite.com/e/","evbuc.com"],
        "WordPress": ["wp-content","wp-includes","wordpress"],
        "Shopify": ["cdn.shopify.com","shopify.com"],
        "Squarespace": ["squarespace.com","sqsp.net"],
        "Wix": ["wix.com","parastorage.com"],
        "Webflow": ["webflow.io","webflow.com"],
        "React": ["react.production.min","__react","reactDOM"],
        "Next.js": ["_next/static","__next"],
        "Stripe": ["stripe.com/v3","js.stripe.com"],
        "Cloudflare": ["cdnjs.cloudflare.com","cf-beacon"],
        "Facebook Pixel": ["connect.facebook.net","fbevents.js","fbq("],
        "LinkedIn Insight": ["snap.licdn.com","linkedin.com/px"],
        "Twitter Pixel": ["static.ads-twitter.com","twq("],
        "Hotjar": ["hotjar.com","hj("],
        "Segment": ["cdn.segment.com","analytics.js"],
        "Typeform": ["typeform.com"],
        "SurveyMonkey": ["surveymonkey.com"],
        "Zoom Integration": ["zoom.us/j/","zoom.us/meeting"],
        "ON24": ["on24.com","gateway.on24"],
        "Cvent": ["cvent.com","web.cvent"],
        "Google Ads": ["googleads.g.doubleclick","adservice.google"],
        "Cookie Consent": ["cookiebot","cookieconsent","onetrust.com","trustarc.com"],
    }
    for name, sigs in _tech_sigs.items():
        if any(s in h for s in sigs):
            tech.append(name)
    return tech

def extract_social_profiles(html):
    """Extract all social media profile URLs from HTML."""
    socials = {}
    h = html or ""
    # Twitter/X
    for m in re.findall(r'https?://(?:www\.)?(?:twitter\.com|x\.com)/([\w]+)', h):
        if m.lower() not in ("share","intent","search","hashtag","home","i"):
            socials["twitter"] = f"https://x.com/{m}"; break
    # Facebook
    for m in re.findall(r'https?://(?:www\.)?facebook\.com/([\w.]+)', h):
        if m.lower() not in ("share","sharer","dialog","plugins","tr","events"):
            socials["facebook"] = f"https://facebook.com/{m}"; break
    # Instagram
    for m in re.findall(r'https?://(?:www\.)?instagram\.com/([\w.]+)', h):
        if m.lower() not in ("p","explore","accounts","stories"):
            socials["instagram"] = f"https://instagram.com/{m}"; break
    # LinkedIn company
    for m in re.findall(r'https?://(?:www\.)?linkedin\.com/company/([\w-]+)', h):
        socials["linkedin"] = f"https://linkedin.com/company/{m}"; break
    # YouTube channel
    for m in re.findall(r'https?://(?:www\.)?youtube\.com/(?:channel/|c/|@)([\w-]+)', h):
        socials["youtube"] = f"https://youtube.com/@{m}"; break
    # TikTok
    for m in re.findall(r'https?://(?:www\.)?tiktok\.com/@([\w.]+)', h):
        socials["tiktok"] = f"https://tiktok.com/@{m}"; break
    # GitHub
    for m in re.findall(r'https?://(?:www\.)?github\.com/([\w-]+)', h):
        if m.lower() not in ("topics","features","pricing","enterprise","explore"):
            socials["github"] = f"https://github.com/{m}"; break
    # Podcast links
    for plat in ["podcasts.apple.com","open.spotify.com/show","anchor.fm"]:
        if plat in h.lower():
            socials["podcast"] = plat; break
    return socials

def extract_event_signals_html(html, text):
    """Extract event-related signals from HTML structure (forms, calendars, countdowns, etc.)."""
    signals = []
    h = (html or "").lower()
    t = (text or "").lower()
    # Registration/signup forms
    if re.search(r'<form[^>]*(?:register|signup|sign-up|rsvp|attend|book)', h):
        signals.append("registration_form")
    if re.search(r'<input[^>]*(?:email|name)[^>]*>.*?(?:register|submit|sign up|rsvp)', h, re.DOTALL):
        signals.append("email_capture_form")
    # Calendar integrations
    cal_sigs = ["add to calendar","add-to-calendar","ics download",".ics","webcal://",
                "calendar.google.com/event","outlook.office.com","addevent.com"]
    for cs in cal_sigs:
        if cs in h: signals.append("calendar_integration"); break
    # Countdown timers
    if re.search(r'(countdown|timer|days?\s*:\s*hours?\s*:\s*min|time-?remaining)', h):
        signals.append("countdown_timer")
    # Speaker/agenda sections
    if re.search(r'<(?:div|section)[^>]*(?:id|class)\s*=\s*["\'](?:[^"\']*(?:speaker|agenda|schedule|program|lineup)[^"\']*)["\']', h):
        signals.append("speaker_section")
    if re.search(r'<(?:div|section)[^>]*(?:id|class)\s*=\s*["\'](?:[^"\']*(?:sponsor|partner|exhibitor)[^"\']*)["\']', h):
        signals.append("sponsor_section")
    # Ticket/pricing
    if re.search(r'(?:€|\$|£|USD|EUR|GBP)\s*\d+', t):
        signals.append("pricing_visible")
    price_matches = re.findall(r'(?:€|\$|£)\s*(\d[\d,]*)', t)
    if price_matches:
        try:
            max_price = max(int(p.replace(",","")) for p in price_matches if int(p.replace(",","")) < 100000)
            if max_price >= 100: signals.append(f"ticket_price_{max_price}")
        except: pass
    # Attendee count
    attendee_match = re.search(r'(\d[\d,]*)\+?\s*(?:attendees|participants|delegates|registrants|viewers)', t)
    if attendee_match:
        signals.append(f"attendees_{attendee_match.group(1)}")
    # Speaker count
    speaker_match = re.search(r'(\d+)\+?\s*(?:speakers|presenters|panelists|experts)', t)
    if speaker_match:
        signals.append(f"speakers_{speaker_match.group(1)}")
    # Past event archives (recurring signal)
    if re.search(r'(past events?|previous events?|event archive|20\d\d edition|last year)', t):
        signals.append("past_events_archived")
    # Multi-day event
    if re.search(r'(day\s*[123]|multi-?day|3-day|2-day|\d+\s*days?\s*(?:event|conference|summit))', t):
        signals.append("multi_day_event")
    # Networking/expo
    if re.search(r'(networking|expo hall|exhibition|virtual booth|breakout room|roundtable)', t):
        signals.append("networking_features")
    # CEU/CPE/certification
    if re.search(r'(ce credits?|ceu|cpe|cmle|continuing education|certification|accredited)', t):
        signals.append("professional_credits")
    return signals

def extract_phone_numbers(text, html=""):
    """Extract phone numbers from text and HTML."""
    phones = []
    combined = (text or "") + " " + (html or "")
    # International formats: +1-234-567-8901, +44 20 7946 0958, etc.
    for m in re.findall(r'\+?\d{1,3}[\s.-]?\(?\d{1,4}\)?[\s.-]?\d{1,4}[\s.-]?\d{1,4}[\s.-]?\d{0,4}', combined):
        digits = re.sub(r'\D', '', m)
        if 7 <= len(digits) <= 15:
            phones.append(m.strip())
    # Tel: links
    for m in re.findall(r'tel:([\+\d\s.-]+)', (html or "")):
        phones.append(m.strip())
    # Deduplicate
    seen = set()
    unique = []
    for p in phones:
        d = re.sub(r'\D', '', p)
        if d not in seen and len(d) >= 7:
            seen.add(d); unique.append(p)
    return unique[:5]

def guess_email_patterns(domain, known_name=None):
    """Generate probable email patterns for a domain given a contact name."""
    if not domain or not known_name:
        return []
    # Clean domain
    domain = domain.lower().strip()
    if domain.startswith("www."): domain = domain[4:]
    # Parse name
    parts = known_name.strip().split()
    if len(parts) < 2: return []
    first = parts[0].lower()
    last = parts[-1].lower()
    fi = first[0]  # first initial
    # Common patterns
    patterns = [
        f"{first}@{domain}",
        f"{first}.{last}@{domain}",
        f"{fi}{last}@{domain}",
        f"{first}{last}@{domain}",
        f"{first}_{last}@{domain}",
        f"{last}@{domain}",
        f"{fi}.{last}@{domain}",
        f"{first}-{last}@{domain}",
    ]
    return patterns

def extract_company_metadata(html, text):
    """Extract company metadata: industry, employee count, founding year, revenue signals."""
    meta = {}
    h = (html or "").lower()
    t = (text or "").lower()
    # Employee count from structured data or text
    emp_match = re.search(r'(\d[\d,]*)\+?\s*(?:employees|team members|staff|people)', t)
    if emp_match:
        try: meta["employee_count"] = int(emp_match.group(1).replace(",",""))
        except: pass
    # Founded year
    founded = re.search(r'(?:founded|established|since|est\.?)\s*(?:in\s+)?(\d{4})', t)
    if founded:
        try:
            yr = int(founded.group(1))
            if 1900 <= yr <= 2026: meta["founded_year"] = yr
        except: pass
    # Revenue/funding signals
    if re.search(r'(?:series [a-e]|raised|funding|venture|backed by|portfolio)', t):
        meta["has_funding"] = True
    if re.search(r'(?:revenue|arr|mrr|billion|million)\s*(?:of\s+)?(?:€|\$|£)', t):
        meta["revenue_signal"] = True
    # Industry keywords from meta tags
    for m in re.findall(r'<meta[^>]*name=["\'](?:keywords|description)["\'][^>]*content=["\']([^"\']+)["\']', h):
        meta["meta_keywords"] = m[:200]
        break
    # Schema.org Organization data
    for m in re.findall(r'"@type"\s*:\s*"Organization"[^}]*}', h):
        if '"numberOfEmployees"' in m:
            emp = re.search(r'"numberOfEmployees"[^}]*"value"\s*:\s*"?(\d+)', m)
            if emp: meta["employee_count"] = int(emp.group(1))
    # Location from structured data
    addr_match = re.search(r'"address"[^}]*"addressCountry"\s*:\s*"([^"]+)"', h)
    if addr_match:
        meta["structured_country"] = addr_match.group(1)
    return meta

def extract_sitemap_urls(base_url):
    """Try to fetch sitemap.xml and extract event-related URLs."""
    event_urls = []
    try:
        parsed = urlparse(base_url)
        sitemap_url = f"{parsed.scheme}://{parsed.netloc}/sitemap.xml"
        r = requests.get(sitemap_url, timeout=8, headers={"User-Agent": USER_AGENT})
        if r.status_code == 200 and "<urlset" in r.text.lower():
            # Extract URLs matching event keywords
            _ev_kw = re.compile(r'(event|conference|summit|webinar|workshop|seminar|symposium|congress|forum|meetup|session|agenda|speaker|schedule|program)', re.I)
            for loc in re.findall(r'<loc>([^<]+)</loc>', r.text):
                if _ev_kw.search(loc):
                    event_urls.append(loc.strip())
                    if len(event_urls) >= 10: break
    except: pass
    return event_urls

def extract_whois_age(domain):
    """Estimate domain age from WHOIS-like signals (non-blocking best effort)."""
    try:
        # Use Wayback Machine CDX API to check first archived date
        clean = domain.lower().replace("www.","")
        r = requests.get(f"https://web.archive.org/cdx/search/cdx?url={clean}&output=json&limit=1&fl=timestamp",
                        timeout=5, headers={"User-Agent": USER_AGENT})
        if r.status_code == 200:
            data = r.json()
            if len(data) > 1 and isinstance(data[1], list) and len(data[1]) > 0:
                ts = str(data[1][0])
                year = int(ts[:4])
                return {"first_archived": year, "domain_age_years": 2026 - year}
    except: pass
    return None

# ─────────────────────────────────────────────────────────────
# PASS 2: DEEP INVESTIGATION
# Enriches high-scoring leads with multi-source intelligence.
# Runs AFTER analyse_lead + deep_qualify + enrich_contact.
# ─────────────────────────────────────────────────────────────

def deep_investigate(lead, page, base_url, page_text=""):
    """
    Pass 2 deep investigation for leads that scored >= MIN_SCORE_TO_KEEP.
    Builds a structured dossier with timing signals, video assessment,
    decision maker intelligence, and budget estimation.
    Returns enriched lead dict.
    """
    dossier = {
        "timing_signals": [],
        "timing_urgency": "none",     # none | low | medium | high | critical
        "video_assessment": None,
        "video_quality": "unknown",   # unknown | poor | basic | decent | professional
        "decision_maker": None,
        "budget_confidence": "low",   # low | medium | high
        "competitive_intel": [],
        "specific_gaps": [],
        "tone_profile": "formal",     # formal | casual | corporate | academic | friendly
        "dossier_summary": "",
    }

    org_name = lead.get("org_name", "")
    org_website = lead.get("org_website") or base_url
    emit_log(f"🔎 PASS2 Deep investigation: {org_name[:50]}", "ai")

    text_lower = (page_text or "").lower()
    p1_data = lead.get("_pass1", {})

    # ═══ MODULE 1: TIMING SIGNALS ═══
    import datetime as _dt
    now = _dt.datetime.now()
    current_year = now.year
    current_month = now.month

    # Upcoming event detection
    month_names = ["january","february","march","april","may","june",
                   "july","august","september","october","november","december"]
    for i, mn in enumerate(month_names):
        month_num = i + 1
        if mn in text_lower and str(current_year) in text_lower:
            if month_num >= current_month:
                dossier["timing_signals"].append(f"Upcoming event: {mn.title()} {current_year}")
        # Also check next year
        if mn in text_lower and str(current_year + 1) in text_lower:
            dossier["timing_signals"].append(f"Future event: {mn.title()} {current_year + 1}")

    # "Registrations open" / "Early bird" = imminent event
    imminent = ["registration is open","registrations open","register now","early bird",
                "book your place","secure your spot","limited spots","save your seat",
                "call for speakers","call for proposals","submit your abstract"]
    for sig in imminent:
        if sig in text_lower:
            dossier["timing_signals"].append(f"Active registration: '{sig}'")

    # Hiring signals (= budget available)
    hiring = ["we're hiring","we are hiring","join our team","open positions",
              "career opportunities","job openings","hiring now","looking for"]
    for sig in hiring:
        if sig in text_lower:
            dossier["timing_signals"].append(f"Hiring: '{sig}'")

    # Recent vendor change signals
    vendor_change = ["new partnership","recently switched","migrated to","upgraded to",
                     "looking for a new","seeking proposals","rfp","request for proposal",
                     "tender","procurement"]
    for sig in vendor_change:
        if sig in text_lower:
            dossier["timing_signals"].append(f"Vendor change signal: '{sig}'")

    # Score timing urgency
    tc = len(dossier["timing_signals"])
    if tc >= 3: dossier["timing_urgency"] = "critical"
    elif tc == 2: dossier["timing_urgency"] = "high"
    elif tc == 1: dossier["timing_urgency"] = "medium"
    else: dossier["timing_urgency"] = "low"

    if dossier["timing_signals"]:
        emit_log(f"⏰ Timing: {dossier['timing_urgency']} ({tc} signals)", "ai")

    # ═══ MODULE 2: VIDEO INTELLIGENCE ═══
    # Only run video analysis if user's business involves video/media services
    _user_services = load_settings().get("wizard", {}).get("services", [])
    _is_media_business = any(s in str(_user_services).lower() for s in ["video", "streaming", "broadcast", "media", "film", "animation"])
    if _is_media_business:
      # Extract embeds from page HTML + search SearXNG, then visit videos for real metrics
      try:
        emit_log(f"📹 Video intelligence: analysing content", "fetch")
        emit_thought("Checking their video content quality...", "search")
        _all_videos = extract_video_embeds(page_text)  # from HTML on page

        # Also search for videos via the standard search function
        try:
            video_query = f"{org_name} video recording"
            _vresults = search(video_query, max_results=5)
            org_words = org_name.lower().split()[:3]
            for vr in _vresults:
                vurl = vr.url or ""
                vtitle = (vr.title or "").lower()
                if any(w in vtitle for w in org_words if len(w) > 3):
                    plat = "youtube" if "youtube.com" in vurl or "youtu.be" in vurl else "vimeo" if "vimeo.com" in vurl else "other"
                    _all_videos.append({"platform":plat, "url":vurl, "id":vurl, "title":vr.title or ""})
        except: pass

        # Dedup by URL
        _seen_vids = set()
        _unique_vids = []
        for v in _all_videos:
            if v["url"] not in _seen_vids:
                _seen_vids.add(v["url"]); _unique_vids.append(v)

        # Visit top videos for real metrics
        if _unique_vids and page:
            _metrics = []
            for vid in _unique_vids[:MAX_VIDEO_VISITS]:
                if vid.get("platform") in ("youtube","vimeo","wistia","vidyard","brightcove"):
                    emit_log(f"📹 Visiting: {vid.get('url','')[:50]}", "fetch")
                    m = fetch_video_metrics(page, vid, return_url=base_url)
                    _metrics.append(m)
            assessment = assess_video_quality(_metrics)
            dossier["video_assessment"] = _metrics[:3]
            dossier["video_quality"] = assessment["quality"]
            dossier["video_metrics"] = {"avg_views": assessment.get("avg_views",0), "videos_found": len(_unique_vids),
                                         "has_professional_hosting": assessment.get("has_professional_hosting",False)}
            for note in assessment.get("notes",[]):
                dossier["specific_gaps"].append(note)
            if assessment["quality"] in ("basic","poor"):
                dossier["specific_gaps"].append("Video quality is " + assessment["quality"] + " — upgrade opportunity")
            emit_log(f"📹 Video: {assessment['quality']} ({len(_metrics)} analysed, {len(_unique_vids)} found)", "ai")
        elif not _unique_vids:
            dossier["video_quality"] = "none_found"
            dossier["specific_gaps"].append("No video content found — potential greenfield opportunity")
            emit_log("📹 No videos found — greenfield", "ai")
      except Exception as e:
        emit_log(f"📹 Video intelligence failed: {e}", "warn")

    # ═══ MODULE 3: DECISION MAKER INTELLIGENCE ═══
    # Enhance existing contact info with role detection
    if not lead.get("contact_name") or not lead.get("contact_email"):
        try:
            # Search for decision maker
            # Derive decision-maker roles from business profile
            _w = load_settings().get("wizard", {})
            dm_roles = "CEO OR founder OR managing director OR head of operations OR procurement"
            dm_query = f"{org_name} {dm_roles} site:linkedin.com"
            emit_log(f"👤 Decision maker search", "fetch")

            _dmresults = search(dm_query, max_results=5)

            for dr in _dmresults:
                dtitle = dr.title or ""
                durl = dr.url or ""
                if "linkedin.com/in/" in durl:
                    # Parse name and role from LinkedIn title (format: "Name - Role - Company")
                    parts = dtitle.split(" - ")
                    if len(parts) >= 2:
                        name = parts[0].strip()
                        role = parts[1].strip() if len(parts) >= 2 else ""
                        # Accept any decision-maker — role relevance is determined by the user's business profile, not a hardcoded list
                        if name and len(name) > 2:
                            dossier["decision_maker"] = {
                                "name": name[:60],
                                "role": role[:80],
                                "linkedin": durl,
                            }
                            if not lead.get("contact_name"):
                                lead["contact_name"] = name[:60]
                            if not lead.get("contact_role"):
                                lead["contact_role"] = role[:80]
                            if not lead.get("contact_linkedin"):
                                lead["contact_linkedin"] = durl
                            emit_log(f"👤 Found: {name[:30]} — {role[:40]}", "ai")
                            break

        except Exception as e:
            emit_log(f"👤 DM search failed: {e}", "warn")

    # ═══ MODULE 4: BUDGET CONFIDENCE ═══
    budget_positive_count = 0
    budget_signals = ["sponsor", "premium", "enterprise", "corporate",
                      "pricing", "subscription", "annual plan", "contract",
                      "partnership", "clients include", "trusted by", "fortune"]
    for bs in budget_signals:
        if bs in text_lower:
            budget_positive_count += 1

    if p1_data.get("budget") == "strong": budget_positive_count += 2
    elif p1_data.get("budget") == "moderate": budget_positive_count += 1
    if p1_data.get("size") == "large": budget_positive_count += 2
    elif p1_data.get("size") == "medium": budget_positive_count += 1
    if dossier["timing_signals"]: budget_positive_count += 1

    if budget_positive_count >= 4: dossier["budget_confidence"] = "high"
    elif budget_positive_count >= 2: dossier["budget_confidence"] = "medium"
    else: dossier["budget_confidence"] = "low"

    # ═══ MODULE 5: COMPETITIVE INTEL ═══
    competitors = {"zoom":"Zoom","teams":"Microsoft Teams","webex":"Webex",
                   "gotomeeting":"GoToMeeting","on24":"ON24","hopin":"Hopin",
                   "streamyard":"StreamYard","vmix":"vMix","obs":"OBS Studio",
                   "wirecast":"Wirecast","vimeo":"Vimeo Livestream",
                   "brightcove":"Brightcove","cvent":"Cvent","6connex":"6Connex"}
    for key, name in competitors.items():
        if key in text_lower:
            dossier["competitive_intel"].append(name)

    if dossier["competitive_intel"]:
        emit_log(f"🏁 Tools detected: {', '.join(dossier['competitive_intel'][:4])}", "ai")

    # ═══ MODULE 6: TONE PROFILE ═══
    # Detect communication style from page text
    text_sample = (page_text or "")[:2000].lower()
    if any(w in text_sample for w in ["dear colleague","esteemed","distinguished","professor"]):
        dossier["tone_profile"] = "academic"
    elif any(w in text_sample for w in ["hey","awesome","cool","exciting","!"*3]):
        dossier["tone_profile"] = "casual"
    elif any(w in text_sample for w in ["leverage","synergy","stakeholder","roi","kpi"]):
        dossier["tone_profile"] = "corporate"
    elif any(w in text_sample for w in ["we'd love","join us","community","together"]):
        dossier["tone_profile"] = "friendly"
    else:
        dossier["tone_profile"] = "formal"

    # ═══ MODULE 7: SPECIFIC GAPS (from page content) ═══
    gap_checks = [
        ("webcam", "Events appear to use basic webcam setups"),
        ("screen share", "Relies on screen sharing rather than produced content"),
        ("no graphics", "No branded graphics or overlays visible"),
        ("zoom grid", "Uses default Zoom grid layout"),
        ("pre-recorded", "Uses pre-recorded content instead of live production"),
    ]
    for keyword, gap_desc in gap_checks:
        if keyword in text_lower and gap_desc not in dossier["specific_gaps"]:
            dossier["specific_gaps"].append(gap_desc)

    # If they have events but no video content = gap
    if p1_data.get("has_events") and not p1_data.get("has_video"):
        dossier["specific_gaps"].append("Hosts events but no video/streaming content found")

    # ═══ MODULE 8: VIDEO EMBED DEEP ANALYSIS ═══
    try:
        html_content = ""
        try: html_content = page.content()
        except: pass
        vid_embeds = extract_video_embeds(html_content)
        if vid_embeds:
            dossier["video_embeds"] = vid_embeds[:5]
            platforms = list(set(v["platform"] for v in vid_embeds))
            emit_log(f"📹 Found {len(vid_embeds)} video embeds: {', '.join(platforms[:4])}", "ai")
            # Enrich with YouTube metadata via oEmbed (non-blocking)
            for vid in vid_embeds[:2]:
                if vid["platform"] == "youtube":
                    try:
                        oembed_url = f"https://www.youtube.com/oembed?url={vid['url']}&format=json"
                        r = requests.get(oembed_url, timeout=5)
                        if r.status_code == 200:
                            meta = r.json()
                            vid["title"] = meta.get("title","")[:100]
                            vid["channel"] = meta.get("author_name","")[:60]
                            vid["thumbnail"] = meta.get("thumbnail_url","")
                            emit_log(f"📹 YT: {vid['title'][:50]} by {vid['channel'][:30]}", "ai")
                    except: pass
                elif vid["platform"] == "vimeo":
                    try:
                        oembed_url = f"https://vimeo.com/api/oembed.json?url={vid['url']}"
                        r = requests.get(oembed_url, timeout=5)
                        if r.status_code == 200:
                            meta = r.json()
                            vid["title"] = meta.get("title","")[:100]
                            vid["channel"] = meta.get("author_name","")[:60]
                    except: pass
    except Exception as e:
        emit_log(f"📹 Video embed analysis failed: {e}", "warn")

    # ═══ MODULE 9: TECH STACK INTELLIGENCE ═══
    try:
        html_content = html_content if html_content else ""
        tech = extract_tech_stack(html_content)
        if tech:
            dossier["tech_stack"] = tech
            emit_log(f"🔧 Tech stack: {', '.join(tech[:6])}", "ai")
            # Identify CRM/marketing tools = budget indicator
            _marketing_tech = [t for t in tech if t in ("HubSpot","Salesforce","Marketo","ActiveCampaign","Mailchimp","Segment")]
            if _marketing_tech:
                dossier["budget_confidence"] = "high" if dossier["budget_confidence"] != "high" else "high"
                dossier["specific_gaps"].append(f"Uses {', '.join(_marketing_tech)} = marketing budget exists")
            # Event platforms detected in tech
            _event_tech = [t for t in tech if t in ("Eventbrite","ON24","Cvent","Calendly","Zoom Integration")]
            if _event_tech:
                dossier["competitive_intel"].extend(_event_tech)
    except Exception as e:
        emit_log(f"🔧 Tech stack analysis failed: {e}", "warn")

    # ═══ MODULE 10: SOCIAL MEDIA INTELLIGENCE ═══
    try:
        html_content = html_content if html_content else ""
        socials = extract_social_profiles(html_content)
        if socials:
            dossier["social_profiles"] = socials
            emit_log(f"📱 Social: {', '.join(socials.keys())}", "ai")
            # YouTube channel = they produce video content (good signal)
            if "youtube" in socials:
                if dossier["video_quality"] == "none_found":
                    dossier["video_quality"] = "has_channel"
                    dossier["specific_gaps"].append("Has YouTube channel but quality unknown")
            # No social media = smaller/less digital org
            if len(socials) <= 1:
                dossier["specific_gaps"].append("Minimal social media presence — may need digital uplift")
    except Exception as e:
        emit_log(f"📱 Social analysis failed: {e}", "warn")

    # ═══ MODULE 11: SITEMAP MINING ═══
    try:
        sitemap_urls = extract_sitemap_urls(org_website)
        if sitemap_urls:
            dossier["sitemap_event_urls"] = sitemap_urls[:8]
            emit_log(f"🗺️ Sitemap: {len(sitemap_urls)} event URLs found", "ai")
            # More event pages = more organized/recurring events
            if len(sitemap_urls) >= 5:
                dossier["timing_signals"].append(f"Sitemap has {len(sitemap_urls)} event pages — well-organized event program")
                if dossier["timing_urgency"] == "low": dossier["timing_urgency"] = "medium"
    except Exception as e:
        emit_log(f"🗺️ Sitemap mining failed: {e}", "warn")

    # ═══ MODULE 12: EVENT SIGNALS FROM HTML ═══
    try:
        ev_signals = extract_event_signals_html(html_content, page_text)
        if ev_signals:
            dossier["html_event_signals"] = ev_signals
            emit_log(f"📋 HTML event signals: {', '.join(ev_signals[:5])}", "ai")
            # Specific high-value signals
            if "registration_form" in ev_signals or "email_capture_form" in ev_signals:
                dossier["timing_signals"].append("Active registration form on page")
            if "sponsor_section" in ev_signals:
                if dossier["budget_confidence"] == "low": dossier["budget_confidence"] = "medium"
            if "professional_credits" in ev_signals:
                dossier["green_flags"] = dossier.get("green_flags", [])
                dossier["green_flags"].append("Offers professional credits/CEU — serious event")
            # Attendee count
            for sig in ev_signals:
                if sig.startswith("attendees_"):
                    count_str = sig.split("_")[1].replace(",","")
                    try:
                        count = int(count_str)
                        if count >= 100: dossier["budget_confidence"] = "high"
                        lead["audience_size_guess"] = f"{count}+"
                    except: pass
                if sig.startswith("ticket_price_"):
                    price = sig.split("_")[-1]
                    dossier["budget_confidence"] = "high"
                    dossier["specific_gaps"].append(f"Ticket price ${price} = paid event with budget")
    except Exception as e:
        emit_log(f"📋 Event signal analysis failed: {e}", "warn")

    # ═══ MODULE 13: DOMAIN AGE CHECK ═══
    try:
        _d = urlparse(org_website).netloc.lower()
        age_info = extract_whois_age(_d)
        if age_info:
            dossier["domain_age"] = age_info
            if age_info.get("domain_age_years", 0) >= 5:
                emit_log(f"🏛️ Domain age: {age_info['domain_age_years']} years (established)", "ai")
            elif age_info.get("domain_age_years", 0) <= 1:
                dossier["specific_gaps"].append("Very new domain — may be starting up")
    except: pass

    # ═══ MODULE 14: COMPANY METADATA ═══
    try:
        comp_meta = extract_company_metadata(html_content, page_text)
        if comp_meta:
            dossier["company_metadata"] = comp_meta
            if comp_meta.get("employee_count"):
                ec = comp_meta["employee_count"]
                if ec >= 50:
                    dossier["budget_confidence"] = "high"
                    emit_log(f"🏢 {ec} employees — enterprise-level", "ai")
                elif ec >= 10:
                    if dossier["budget_confidence"] == "low": dossier["budget_confidence"] = "medium"
            if comp_meta.get("has_funding"):
                dossier["budget_confidence"] = "high"
                dossier["specific_gaps"].append("Has funding — budget available for production upgrades")
    except: pass

    # Recalculate timing urgency with new signals
    tc = len(dossier["timing_signals"])
    if tc >= 4: dossier["timing_urgency"] = "critical"
    elif tc >= 3: dossier["timing_urgency"] = "high"
    elif tc >= 2: dossier["timing_urgency"] = "medium"

    # ═══ AI SYNTHESIS — STRATEGIC DOSSIER ═══
    # Take all heuristic signals and have AI create an actionable intel briefing
    try:
        raw_signals = []
        if dossier["timing_signals"]:
            raw_signals.append(f"TIMING ({dossier['timing_urgency']}): {'; '.join(dossier['timing_signals'][:6])}")
        if dossier["video_assessment"]:
            vids = dossier["video_assessment"]
            raw_signals.append(f"VIDEO ({dossier['video_quality']}): {'; '.join(v.get('title','?')[:60] for v in vids[:3])}")
        if dossier.get("video_embeds"):
            ve = dossier["video_embeds"]
            platforms = list(set(v["platform"] for v in ve))
            raw_signals.append(f"VIDEO EMBEDS: {len(ve)} found ({', '.join(platforms[:4])})")
        if dossier["decision_maker"]:
            dm = dossier["decision_maker"]
            raw_signals.append(f"DECISION MAKER: {dm['name']} — {dm['role']}")
        if dossier["budget_confidence"] != "low":
            raw_signals.append(f"BUDGET: {dossier['budget_confidence']} confidence")
        if dossier["competitive_intel"]:
            raw_signals.append(f"CURRENT TOOLS: {', '.join(list(set(dossier['competitive_intel']))[:6])}")
        if dossier.get("tech_stack"):
            raw_signals.append(f"TECH: {', '.join(dossier['tech_stack'][:6])}")
        if dossier.get("social_profiles"):
            raw_signals.append(f"SOCIAL: {', '.join(dossier['social_profiles'].keys())}")
        if dossier.get("html_event_signals"):
            raw_signals.append(f"EVENT SIGNALS: {', '.join(dossier['html_event_signals'][:5])}")
        if dossier.get("company_metadata"):
            cm = dossier["company_metadata"]
            parts = []
            if cm.get("employee_count"): parts.append(f"{cm['employee_count']} employees")
            if cm.get("founded_year"): parts.append(f"est. {cm['founded_year']}")
            if cm.get("has_funding"): parts.append("funded")
            if parts: raw_signals.append(f"COMPANY: {', '.join(parts)}")
        if dossier["specific_gaps"]:
            raw_signals.append(f"PRODUCTION GAPS: {'; '.join(dossier['specific_gaps'][:6])}")
        raw_signals.append(f"TONE: {dossier['tone_profile']}")

        if len(raw_signals) >= 3:
            _synth_prompt = f"""You are an elite B2B sales strategist preparing a Deal Intelligence Briefing.

LEAD: {org_name}
CONTEXT: {lead.get('event_name','?')} ({lead.get('event_type','?')})
COUNTRY: {lead.get('country','?')}
FIT SCORE: {lead.get('fit_score','?')}/10
SERVICE OPPORTUNITY: {lead.get('production_gap','?')}
CURRENT TOOLS: {lead.get('current_tools','unknown')}
TOOL WEAKNESSES: {lead.get('tool_weaknesses','unknown')}

RAW INTELLIGENCE:
{chr(10).join(raw_signals)}

PAGE EXCERPT:
{(page_text or '')[:1500]}

Return ONLY valid JSON with this structure:
{{"recommended_approach":{{"lead_with":"the #1 angle to open with — what would make them stop and read","mention":"specific details from their event/page to reference","avoid":"topics or approaches that would backfire with this lead"}},
"competitive_analysis":{{"current_platform":"what they use now","weaknesses":["specific weakness 1","specific weakness 2"],"switch_trigger":"what event/pain would make them switch NOW"}},
"objection_prep":[{{"objection":"likely pushback","counter":"how to respond"}}],
"urgency":{{"level":"act_now|soon|watch|wait","reason":"why this timing","trigger":"specific upcoming event or deadline or null"}},
"one_line_pitch":"the single best sentence to use in the email",
"summary":"2-3 sentence strategic briefing for the sales rep"}}"""

            emit_log(f"🧠 AI synthesising deal briefing for {org_name[:40]}...", "ai")
            _synth_resp = client.chat.completions.create(**_ai_json_kwargs(
                model=_get_tier_model(),
                messages=[
                    {"role": "system", "content": "You are an elite B2B sales strategist. Return ONLY valid JSON. Be ruthlessly specific — generic advice is useless."},
                    {"role": "user", "content": _synth_prompt}
                ],
                temperature=0.3, max_tokens=1200
            ))
            _synth_text = (_synth_resp.choices[0].message.content or "").strip()
            if "</think>" in _synth_text:
                _synth_text = _synth_text.split("</think>")[-1].strip()
            _synth_js = extract_json(_synth_text)
            if _synth_js:
                _briefing = json.loads(_synth_js)
                dossier["deal_briefing"] = _briefing
                lead["deal_briefing"] = _briefing
                dossier["ai_briefing"] = _briefing.get("summary","")
                dossier["dossier_summary"] = _briefing.get("summary","")[:200]
                emit_log(f"📋 Deal Briefing: {dossier['dossier_summary'][:100]}...", "ai")
            else:
                # Fallback: use raw text as briefing
                if len(_synth_text) > 30:
                    dossier["ai_briefing"] = _synth_text[:800]
                    dossier["dossier_summary"] = _synth_text[:200]
                else:
                    raise ValueError("AI synthesis too short")
        else:
            raise ValueError("Not enough signals for AI synthesis")
    except Exception:
        # Fallback to rule-based summary
        summary_parts = []
        if dossier["timing_urgency"] in ("high", "critical"):
            summary_parts.append(f"URGENT: {len(dossier['timing_signals'])} timing signals")
        if dossier["video_quality"] in ("basic", "none_found"):
            summary_parts.append(f"Video: {dossier['video_quality']} — upgrade opportunity")
        if dossier["decision_maker"]:
            dm = dossier["decision_maker"]
            summary_parts.append(f"Contact: {dm['name']} ({dm['role']})")
        if dossier["budget_confidence"] in ("medium", "high"):
            summary_parts.append(f"Budget: {dossier['budget_confidence']} confidence")
        if dossier["competitive_intel"]:
            summary_parts.append(f"Tools: {', '.join(dossier['competitive_intel'][:3])}")
        if dossier["specific_gaps"]:
            summary_parts.append(f"Gaps: {len(dossier['specific_gaps'])} found")
        dossier["dossier_summary"] = " | ".join(summary_parts) if summary_parts else "Standard lead — no special signals"

    emit_log(f"📋 Dossier: {dossier['dossier_summary'][:100]}", "ai")
    emit_thought(f"Investigation complete. {dossier['dossier_summary'][:80]}", "thinking")

    # Attach to lead
    lead["_pass2"] = dossier
    return lead



# ─────────────────────────────────────────────────────────────
# PASS 3: RANK & REWRITE
# Compares all qualifying leads, assigns priority scores,
# tags Top 10, and rewrites emails with full dossier context.
# ─────────────────────────────────────────────────────────────

def calculate_priority_score(lead):
    """Calculate weighted priority score from Pass 1 + Pass 2 data."""
    score = 0
    p1 = lead.get("_pass1", {})
    p2 = lead.get("_pass2", {})
    fit = lead.get("fit_score", 0)

    # Base: AI fit score (0-10, weighted heavily)
    score += fit * 10  # max 100

    # Timing urgency (most important multiplier)
    timing_map = {"critical": 40, "high": 30, "medium": 15, "low": 5, "none": 0}
    score += timing_map.get(p2.get("timing_urgency", "none"), 0)

    # Budget confidence
    budget_map = {"high": 25, "medium": 15, "low": 5}
    score += budget_map.get(p2.get("budget_confidence", "low"), 0)

    # Service opportunity (AI-scored, niche-aware via wizard context)
    svc_opp = lead.get("service_opportunity_score", 0) or 0
    if svc_opp >= 8: score += 18
    elif svc_opp >= 6: score += 12
    elif svc_opp >= 4: score += 6
    else: score += 2

    # Decision maker found (huge bonus — direct contact)
    if p2.get("decision_maker"):
        score += 20
    elif lead.get("contact_email"):
        score += 10
    elif lead.get("contact_name"):
        score += 5

    # Specific gaps identified (more gaps = more pitch material)
    gaps = len(p2.get("specific_gaps", []))
    score += min(gaps * 5, 20)

    # Buying intent detected (active need signal)
    if p1.get("has_events") or any("BUYING INTENT" in str(g) for g in p1.get("green", [])):
        score += 5

    # Has recurring/ongoing need (strong signal)
    if lead.get("is_recurring"):
        score += 8

    # Content freshness bonus
    fresh_map = {"current_year": 10, "last_year": 5, "aging": -5, "stale": -15}
    score += fresh_map.get(p1.get("freshness", "unknown"), 0)

    # Green flags bonus
    score += min(len(p1.get("green", [])) * 3, 15)

    # Red flags penalty
    score -= min(len(p1.get("red", [])) * 5, 20)

    # Company size (medium is sweet spot)
    size_map = {"large": 10, "medium": 15, "small": -5}
    score += size_map.get(p1.get("size", "unknown"), 0)

    # Competitive intel (knowing their tools = better pitch)
    if p2.get("competitive_intel"):
        score += min(len(p2["competitive_intel"]) * 3, 12)

    return max(0, min(score, 250))  # cap at 250


def rank_and_rewrite(leads):
    """
    Pass 3: Compare all leads, assign priority scores, tag Top 10,
    and rewrite emails for top leads with full dossier intelligence.
    """
    qualifying = [l for l in leads if l.get("fit_score", 0) >= MIN_SCORE_TO_KEEP]
    if not qualifying:
        emit_log("PASS3: No qualifying leads to rank", "warn")
        return leads

    emit_log(f"🏆 PASS3: Ranking {len(qualifying)} leads...", "ai")
    emit_thought("Comparing all leads to find the hottest opportunities...", "thinking")
    emit_status("Pass 3: Ranking leads", "busy")

    # Calculate priority scores
    for lead in qualifying:
        lead["priority_score"] = calculate_priority_score(lead)

    # Sort by priority score
    qualifying.sort(key=lambda x: x.get("priority_score", 0), reverse=True)

    # Tag Top 10
    top_10 = qualifying[:10]
    for i, lead in enumerate(top_10):
        lead["priority_rank"] = i + 1
        lead["is_top10"] = True
        emit_log(f"🥇 #{i+1}: {lead.get('org_name','?')} — priority {lead.get('priority_score',0)}", "lead")

    # Tag rest
    for lead in qualifying[10:]:
        lead["is_top10"] = False
        lead["priority_rank"] = 0

    # ═══ REWRITE EMAILS FOR TOP 10 ═══
    emit_log(f"✍️ Rewriting emails for Top 10 with full intelligence...", "ai")
    emit_thought("Crafting hyper-personalized emails for the hottest leads...", "thinking")

    for _rw_idx, lead in enumerate(top_10, start=1):
        if _check_stop():
            emit_log(f"Pass 3 stopped at lead {_rw_idx}/{len(top_10)} by user", "warn")
            break
        _rw_org = (lead.get("org_name") or "this lead")[:40]
        emit_thought(f"Drafting email {_rw_idx}/{len(top_10)} — {_rw_org}…", "excited")
        try:
            p1 = lead.get("_pass1", {})
            p2 = lead.get("_pass2", {})

            # Build rich context for the AI
            context_parts = []

            # Org info
            context_parts.append(f"ORGANISATION: {lead.get('org_name','?')}")
            context_parts.append(f"CONTEXT: {lead.get('event_name','?')} ({lead.get('event_type','?')})")
            if lead.get("is_recurring"):
                context_parts.append("THIS IS AN ONGOING/RECURRING NEED — mention long-term partnership value")

            # Decision maker
            dm = p2.get("decision_maker")
            if dm:
                context_parts.append(f"CONTACT PERSON: {dm['name']} — {dm['role']}")
                context_parts.append("ADDRESS THE EMAIL TO THIS SPECIFIC PERSON BY NAME")
            elif lead.get("contact_name"):
                context_parts.append(f"CONTACT: {lead['contact_name']} ({lead.get('contact_role','')})")

            # Timing signals
            ts = p2.get("timing_signals", [])
            if ts:
                context_parts.append(f"TIMING URGENCY ({p2.get('timing_urgency','?')}): {'; '.join(ts[:3])}")
                context_parts.append("REFERENCE THE TIMING — mention their upcoming event/deadline specifically")

            # Video quality
            vq = p2.get("video_quality", "unknown")
            if vq in ("basic", "none_found"):
                context_parts.append(f"VIDEO QUALITY: {vq}")
                va = p2.get("video_assessment")
                if va:
                    context_parts.append(f"THEIR VIDEO: '{va[0]['title']}'")
                    context_parts.append("REFERENCE their specific video content and how you'd improve it")

            # Specific gaps
            gaps = p2.get("specific_gaps", [])
            if gaps:
                context_parts.append(f"GAPS IDENTIFIED: {'; '.join(gaps[:3])}")
                context_parts.append("REFERENCE AT LEAST ONE SPECIFIC GAP in the email")

            # Competitive intel
            ci = p2.get("competitive_intel", [])
            if ci:
                context_parts.append(f"CURRENT TOOLS: {', '.join(ci)}")
                context_parts.append("Show you know what they use and explain how you complement/improve it")

            # Tone profile
            tone = p2.get("tone_profile", "formal")
            context_parts.append(f"MATCH THIS TONE: {tone}")

            # Budget
            bc = p2.get("budget_confidence", "low")
            if bc == "high":
                context_parts.append("HIGH BUDGET SIGNALS — can suggest premium package")
            elif bc == "medium":
                context_parts.append("MODERATE BUDGET — lead with value proposition, mention 20% discount")

            # Evidence
            eq = lead.get("evidence_quote", "")
            if eq:
                context_parts.append(f"EVIDENCE FROM THEIR SITE: \"{eq}\"")

            # AI briefing from deep investigation
            ai_brief = p2.get("ai_briefing", "")
            if ai_brief:
                context_parts.append(f"AI STRATEGIC BRIEFING:\n{ai_brief}")

            # Compose the rewrite prompt
            rewrite_context = "\n".join(context_parts)

            _settings = load_settings()
            _booking = _settings.get("booking_url","")
            _booking_line = f"\nEnd email with booking link: {_booking}" if _booking else ""
            _wiz = _settings.get("wizard", {})
            _company = _wiz.get("company_name", "our company")
            _from_name = _settings.get("from_name") or _wiz.get("company_name", "our team")

            _fresh_ctx = _build_ai_context()
            rewrite_prompt = f"""{_fresh_ctx}

This is a TOP 10 PRIORITY lead. You are writing the FINAL version of this outreach. This email will be sent to a real person. It must be indistinguishable from an email written by a human who spent 20 minutes researching this prospect.

{rewrite_context}
{_booking_line}

YOUR TASK — Write an email that makes the reader think "How did they know that about us?"

PROCESS:
1. Study the intelligence above. Find the most SURPRISING or SPECIFIC detail about this prospect.
2. Open with THAT detail — something only someone who actually researched them would know.
3. Connect their specific situation to ONE concrete way {_company} would improve it. Be precise about the deliverable.
4. If a decision maker name is known, address them by name. If a specific event date or name is known, reference it.
5. If recurring: mention that series pricing makes each event cheaper, not more expensive.
6. End with ONE low-friction question they can answer in under 10 words.
7. Sign off with {_from_name}, {_company}

TONE: {tone}

HARD CONSTRAINTS:
- 3-5 sentences maximum, under 80 words. Shorter emails get more replies.
- Subject: max 6 words, must reference something specific to THEM.
- NEVER start with "I" or "We". Start with THEM.
- NEVER use template phrases ("I came across", "I'd love to connect", "I noticed that", "I hope this finds you")
- NEVER use placeholders, "...", or generic filler.
- NEVER use banned words: leverage, streamline, enhance, elevate, seamless, transform, solution, excited, thrilled, synergy, cutting-edge, innovative, game-changing, empower, optimize, next-level, revolutionize
- LinkedIn note: under 180 chars, sounds like a real person who genuinely noticed their work.

MANDATORY: You must also write 3 follow-up emails. The JSON response MUST include all 6 fields below. Do NOT omit the follow-up fields.

Respond ONLY with this exact JSON structure (all 6 fields required):
{{"email_subject":"max 6 words","email_body":"3-5 sentences primary email","linkedin_note":"under 180 chars","email_followup_2":"Day 3: different angle, 2-3 sentences, do not repeat email 1","email_followup_3":"Day 7: share insight or resource, 2 sentences, helpful not pushy","email_followup_4":"Day 14: breakup, 1-2 sentences, should I close your file tone"}}"""

            emit_log(f"✍️ Rewriting email for #{lead.get('priority_rank',0)}: {lead.get('org_name','?')[:40]}", "ai")

            # ── Plugin pre_draft hook (round-69 Kimi spec) ──
            # Plugins can mutate the rewrite prompt — e.g. inject
            # extra context (recent posts, hiring signals), rewrite
            # tone instructions, or prepend brand-voice guides.
            try:
                from plugins import get_registry as _pg_pd, HookContext as _PHC_pd
                rewrite_prompt = _pg_pd().run("pre_draft", _PHC_pd(
                    settings=load_settings() or {},
                    provider_name=os.environ.get("HV_AI_PROVIDER", "gemini"),
                    user_id=getattr(_ctx(), "user_id", None),
                    meta={"phase": "pre_draft", "lead_id": lead.get("id")},
                ), lead, rewrite_prompt) or rewrite_prompt
            except Exception as _phk_pd:
                emit_log(f"plugin pre_draft failed: {_phk_pd}", "warn")

            resp = client.chat.completions.create(**_ai_json_kwargs(
                model=_get_tier_model(),
                messages=[
                    {"role": "system", "content": _build_ai_context() + "\n\nYou are writing the final, send-ready version of a cold outreach email for a TOP PRIORITY lead. This must read like a hand-crafted email from a human who deeply researched the prospect. Output ONLY valid JSON."},
                    {"role": "user", "content": rewrite_prompt}
                ],
                temperature=0.4, max_tokens=2500
            ))
            raw = (resp.choices[0].message.content or "").strip()
            js = extract_json(raw)
            if js:
                data = json.loads(js)
                if data.get("email_body") and len(data["email_body"]) > 80:
                    lead["email_body"] = data["email_body"].strip()
                    lead["email_subject"] = (data.get("email_subject") or lead.get("email_subject","")).strip()
                    if data.get("linkedin_note") and len(data["linkedin_note"]) > 20:
                        lead["linkedin_note"] = data["linkedin_note"].strip()
                    # Extract follow-up sequence (Pass 3 only)
                    for _fuKey in ("email_followup_2","email_followup_3","email_followup_4"):
                        _fuVal = (data.get(_fuKey) or "").strip()
                        if _fuVal and len(_fuVal) > 20:
                            lead[_fuKey] = _fuVal
                    _fuCount = sum(1 for k in ("email_followup_2","email_followup_3","email_followup_4") if lead.get(k))
                    # ── Plugin post_draft hook (round-69 Kimi spec) ──
                    # Plugins can sanitise / rewrite / spell-check the
                    # final email body. Returns the (possibly mutated)
                    # body string. Useful for compliance scrubbers,
                    # banned-phrase removers, or grammar passes.
                    try:
                        from plugins import get_registry as _pg_pd2, HookContext as _PHC_pd2
                        _new_body = _pg_pd2().run("post_draft", _PHC_pd2(
                            settings=load_settings() or {},
                            provider_name=os.environ.get("HV_AI_PROVIDER", "gemini"),
                            user_id=getattr(_ctx(), "user_id", None),
                            meta={"phase": "post_draft", "lead_id": lead.get("id")},
                        ), lead, lead["email_body"])
                        if isinstance(_new_body, str) and _new_body.strip():
                            lead["email_body"] = _new_body.strip()
                    except Exception as _phk_pd2:
                        emit_log(f"plugin post_draft failed: {_phk_pd2}", "warn")
                    emit_log(f"✅ Email rewritten for {lead.get('org_name','?')[:40]} (+{_fuCount} follow-ups)", "ai")
                else:
                    emit_log(f"⚠️ Rewrite too short for {lead.get('org_name','?')[:40]} — keeping original", "warn")
            else:
                emit_log(f"⚠️ Rewrite failed (no JSON) for {lead.get('org_name','?')[:40]}", "warn")

        except Exception as e:
            emit_log(f"⚠️ Rewrite error for {lead.get('org_name','?')[:40]}: {e}", "warn")

        time.sleep(0.5)  # Brief pause between rewrites

    # ═══ LOG SUMMARY ═══
    emit_log(f"🏆 PASS3 COMPLETE — Top 10 ranked and rewritten", "ok")
    emit_thought(f"Done! Top 10 leads identified and emails personalized.", "done")

    # Return all leads with the qualifying ones updated
    return leads


def extract_structured(page, html, text=""):
    """Enhanced structured data extraction — pulls everything useful from a page."""
    lines = []
    # ── JSON-LD structured data (events, organizations, etc.) ──
    try:
        _ld_elements = page.query_selector_all('script[type="application/ld+json"]') if page else []
        for el in _ld_elements:
            raw = (el.inner_text() or "").strip()
            try:
                data = json.loads(raw)
                if isinstance(data, list):
                    for item in data[:3]:
                        _extract_jsonld_item(item, lines)
                elif isinstance(data, dict) and isinstance(data.get("@graph"), list):
                    # JSON-LD `@graph` envelope: unpack so the contained
                    # entities (Organization, Event, ContactPoint, …) are
                    # actually processed instead of being silently dropped
                    # because the wrapper has no `@type`.
                    for item in data["@graph"][:3]:
                        _extract_jsonld_item(item, lines)
                else:
                    _extract_jsonld_item(data, lines)
            except: pass
    except: pass
    # ── OpenGraph tags ──
    try:
        og = {el.get_attribute("property"): el.get_attribute("content") for el in page.query_selector_all('meta[property^="og:"]')}
        if og: lines.append("[OG] " + " | ".join(f"{k}={v!r}" for k,v in list(og.items())[:8]))
    except: pass
    # ── Twitter Card tags ──
    try:
        tw = {el.get_attribute("name"): el.get_attribute("content") for el in page.query_selector_all('meta[name^="twitter:"]')}
        if tw: lines.append("[TWITTER-CARD] " + " | ".join(f"{k}={v!r}" for k,v in list(tw.items())[:5]))
    except: pass
    # ── Headings with event signals ──
    try:
        for h in page.query_selector_all("h1, h2, h3")[:15]:
            txt = (h.inner_text() or "").strip()[:120]
            if re.search(r"(online|virtual|zoom|teams|register|watch|live|stream|webinar|conference|summit|workshop|seminar|forum|symposium|congress|speaker|agenda|schedule|sponsor)", txt, re.I):
                lines.append(f"[H] {txt}")
    except: pass
    # ── Emails (mailto + text) ──
    try:
        for el in page.query_selector_all('a[href^="mailto:"]')[:8]:
            href = (el.get_attribute("href") or "").replace("mailto:","").split("?")[0].strip()
            if href and validate_email(href): lines.append(f"[EMAIL] {href}")
    except: pass
    # ── LinkedIn URLs ──
    try:
        for el in page.query_selector_all('a[href*="linkedin.com"]')[:5]:
            href = (el.get_attribute("href") or "").strip()
            if href: lines.append(f"[LINKEDIN] {href}")
    except: pass
    # ── Video embeds ──
    try:
        videos = extract_video_embeds(html)
        for v in videos[:5]:
            lines.append(f"[VIDEO] {v['platform']}: {v['url']}")
    except: pass
    # ── Social profiles ──
    try:
        socials = extract_social_profiles(html)
        for platform, url in socials.items():
            lines.append(f"[SOCIAL] {platform}: {url}")
    except: pass
    # ── Regex-based email + LinkedIn extraction (works without Playwright) ──
    if page is None and html:
        try:
            _rg_emails = extract_emails_from_text(html)
            for _re in _rg_emails[:5]:
                lines.append(f"[EMAIL] {_re}")
        except: pass
        try:
            _rg_li = extract_linkedin_urls(html)
            if _rg_li.get("org"): lines.append(f"[LINKEDIN] {_rg_li['org']}")
            if _rg_li.get("contact"): lines.append(f"[LINKEDIN] {_rg_li['contact']}")
        except: pass
    # ── Tech stack (top 8) ──
    try:
        tech = extract_tech_stack(html)
        if tech: lines.append(f"[TECH] {', '.join(tech[:8])}")
    except: pass
    # ── Event signals from HTML structure ──
    try:
        ev_sigs = extract_event_signals_html(html, text)
        if ev_sigs: lines.append(f"[EVENT-SIGNALS] {', '.join(ev_sigs[:8])}")
    except: pass
    # ── Phone numbers ──
    try:
        phones = extract_phone_numbers(text, html)
        for p in phones[:3]:
            lines.append(f"[PHONE] {p}")
    except: pass
    # ── Company metadata ──
    try:
        meta = extract_company_metadata(html, text)
        if meta:
            parts = []
            if "employee_count" in meta: parts.append(f"employees={meta['employee_count']}")
            if "founded_year" in meta: parts.append(f"founded={meta['founded_year']}")
            if meta.get("has_funding"): parts.append("has_funding")
            if meta.get("revenue_signal"): parts.append("revenue_signals")
            if meta.get("structured_country"): parts.append(f"country={meta['structured_country']}")
            if parts: lines.append(f"[COMPANY] {', '.join(parts)}")
    except: pass
    return "\n".join(lines)

def _extract_jsonld_item(data, lines):
    """Extract useful info from a single JSON-LD item."""
    if not isinstance(data, dict): return
    t = data.get("@type","")
    if isinstance(t, list): t = t[0] if t else ""
    event_types = ("Event","OnlineEvent","EducationEvent","BusinessEvent","SocialEvent",
                   "CourseInstance","MusicEvent","Festival","Hackathon","ExhibitionEvent")
    org_types = ("Organization","Corporation","LocalBusiness","NGO","EducationalOrganization",
                 "GovernmentOrganization","MedicalOrganization")
    person_types = ("Person","ProfilePage")
    if t in event_types:
        lines.append(f'[JSON-LD] type={t} name={data.get("name","")!r} date={data.get("startDate","")} endDate={data.get("endDate","")} location={json.dumps(data.get("location",""))[:120]} organizer={json.dumps(data.get("organizer",""))[:80]}')
        if data.get("offers"):
            offers = data["offers"]
            if isinstance(offers, dict):
                lines.append(f'[JSON-LD-TICKET] price={offers.get("price","")} currency={offers.get("priceCurrency","")} availability={offers.get("availability","")}')
    elif t in org_types:
        lines.append(f'[JSON-LD-ORG] name={data.get("name","")!r} url={data.get("url","")} employees={data.get("numberOfEmployees","")}')
        # Extract contact points from organizations
        cp = data.get("contactPoint") or data.get("contactPoints") or []
        if isinstance(cp, dict): cp = [cp]
        for c in (cp[:3] if isinstance(cp, list) else []):
            if isinstance(c, dict):
                _email = c.get("email","")
                _phone = c.get("telephone","")
                _ctype = c.get("contactType","")
                if _email or _phone:
                    lines.append(f'[JSON-LD-CONTACT] type={_ctype!r} email={_email!r} phone={_phone!r}')
        # Extract founder/member
        for _fkey in ("founder","founders","member","employee"):
            _fm = data.get(_fkey)
            if _fm:
                if isinstance(_fm, dict): _fm = [_fm]
                if isinstance(_fm, list):
                    for _p in _fm[:3]:
                        if isinstance(_p, dict):
                            _pname = _p.get("name","")
                            _prole = _p.get("jobTitle","") or _p.get("roleName","") or _fkey.title()
                            _pemail = _p.get("email","")
                            if _pname:
                                lines.append(f'[JSON-LD-PERSON] name={_pname!r} role={_prole!r} email={_pemail!r}')
    elif t in person_types:
        _name = data.get("name","")
        _role = data.get("jobTitle","") or data.get("roleName","")
        _email = data.get("email","")
        _phone = data.get("telephone","")
        _org = ""
        if data.get("worksFor"):
            _wf = data["worksFor"]
            _org = _wf.get("name","") if isinstance(_wf, dict) else str(_wf)[:60]
        if _name:
            lines.append(f'[JSON-LD-PERSON] name={_name!r} role={_role!r} email={_email!r} phone={_phone!r} org={_org!r}')
    elif t == "WebPage":
        if data.get("speakable"): lines.append("[JSON-LD] Page has speakable content")
    elif t == "BreadcrumbList":
        items = data.get("itemListElement", [])
        crumbs = [i.get("name","") for i in items[:5] if isinstance(i, dict)]
        if crumbs: lines.append(f"[BREADCRUMB] {' > '.join(crumbs)}")
    # Recurse into nested items
    for _nk in ("mainEntity","about","author","creator"):
        _nested = data.get(_nk)
        if isinstance(_nested, dict):
            _extract_jsonld_item(_nested, lines)
        elif isinstance(_nested, list):
            for _ni in _nested[:3]:
                if isinstance(_ni, dict):
                    _extract_jsonld_item(_ni, lines)


# ───────────────────────────────────────────────────────────────
# TLD → COUNTRY MAPPING
# ───────────────────────────────────────────────────────────────
TLD_COUNTRY = {
    ".fr":"France",".de":"Germany",".nl":"Netherlands",".be":"Belgium",".lu":"Luxembourg",
    ".at":"Austria",".ch":"Switzerland",".it":"Italy",".es":"Spain",".pt":"Portugal",
    ".gr":"Greece",".mt":"Malta",".cy":"Cyprus",".se":"Sweden",".dk":"Denmark",".no":"Norway",
    ".fi":"Finland",".is":"Iceland",".pl":"Poland",".cz":"Czech Republic",".sk":"Slovakia",
    ".hu":"Hungary",".ro":"Romania",".bg":"Bulgaria",".hr":"Croatia",".si":"Slovenia",
    ".rs":"Serbia",".ba":"Bosnia",".al":"Albania",".me":"Montenegro",".mk":"North Macedonia",
    ".ee":"Estonia",".lv":"Latvia",".lt":"Lithuania",".ie":"Ireland",".ae":"UAE",
}

def guess_country_from_tld(url):
    try:
        host = urlparse(url).netloc.lower()
        for tld, country in TLD_COUNTRY.items():
            if host.endswith(tld): return country
    except: pass
    return None


BANNED_WORDS = [
    "leverage","streamline","enhance","elevate","seamless","transform","solution",
    "excited","thrilled","reach out","touch base","synergy","cutting-edge","innovative",
    "game-changing","empower","optimize","paradigm","holistic","robust","scalable",
    "impactful","actionable","dynamic","next-level","best-in-class","world-class",
    "end-to-end","turnkey","full-service","unlock","supercharge","revolutionize",
    "comprehensive","facilitate",
]


def _build_ai_context():
    """Build AI context dynamically from wizard settings."""
    _s = load_settings()
    _w = _s.get("wizard", {})
    company = _w.get("company_name", "our company")
    desc = _w.get("business_description", "")
    tone = _w.get("email_tone", "friendly")
    price = _w.get("price_range", "flexible")
    site_ctx = _w.get("_site_context", "")
    avoid = _w.get("red_flags", [])
    services = _w.get("services", [])
    edge = _w.get("differentiators", _w.get("edge", []))
    clients = _w.get("ideal_clients", _w.get("clients", []))
    past_wins = _w.get("past_wins", "")
    target = _w.get("target_clients", "")

    tone_map = {"formal":"Professional and measured. Use titles (Mr./Ms./Dr.). Structured paragraphs. No slang.",
                "friendly":"Warm and approachable. Use first names. Sound like a helpful colleague who genuinely wants to solve their problem.",
                "direct":"Confident and surgically precise. Lead with the value proposition in sentence one. Zero filler words. Every sentence is a punch.",
                "casual":"Creative and human. Write like a smart friend who noticed something interesting about their work. Contractions, dashes, personality."}

    c = f"""You are {company}'s B2B sales analyst. You read web pages and instantly judge: buyer or waste of time. Your scores and emails directly drive revenue.

ABOUT {company.upper()}:
"""
    if desc: c += f"{desc}\n"
    btype = _w.get("business_type", "")
    if btype: c += f"Business type: {btype}\n"
    how = _w.get("how_it_works", "")
    if how: c += f"How it works: {how}\n"
    delivery = _w.get("delivery_method", "")
    if delivery: c += f"Delivery method: {delivery}\n"
    rev_model = _w.get("revenue_model", "")
    if rev_model: c += f"Revenue model: {rev_model}\n"
    if target: c += f"Target clients: {target}\n"
    facts = _w.get("confirmed_facts", [])
    if facts: c += f"Confirmed facts: {', '.join(facts[:5])}\n"
    if site_ctx: c += f"From website: {site_ctx[:800]}\n"
    if services: c += f"Services: {', '.join(services)}\n"
    if edge: c += f"Key differentiators: {', '.join(edge)}\n"
    if past_wins: c += f"Past wins: {past_wins[:500]}\n"
    # Previously dead wizard fields — now active in scoring
    _dream = _w.get("dream_client", "")
    if _dream: c += f"DREAM CLIENT (score 9-10 if matched): {_dream[:300]}\n"
    _pain = _w.get("pain_point", "")
    if _pain: c += f"Core problem we solve: {_pain[:300]}\n"
    _proof = _w.get("proof", "")
    if _proof: c += f"Case study / proof point: {_proof[:300]}\n"
    _comp_diff = _w.get("comp_diff", "")
    if _comp_diff: c += f"What we do that competitors don't: {_comp_diff[:200]}\n"

    # Inject accumulated knowledge from past training sessions
    knowledge = _w.get("_knowledge", [])
    if knowledge:
        c += f"\n=== ACCUMULATED INTELLIGENCE ({len(knowledge)} training sessions) ===\n"
        for k in knowledge[-10:]:
            c += f"[{k.get('date','?')[:10]}] {k.get('content','')[:300]}\n"
        c += "\n"

    # Inject per-user learning profile from feedback
    _lp = _w.get("_learning_instructions", "")
    if _lp:
        c += f"\n=== USER FEEDBACK LEARNING (this user's preferences from Good Fit / Bad Fit ratings) ===\n"
        c += f"{_lp[:600]}\n"
        c += "IMPORTANT: Follow these learned preferences when scoring. They reflect what THIS specific user considers a good or bad prospect.\n\n"

    c += f"\nWHAT WE OFFER: {', '.join(services) if services else desc[:200]}\n"
    c += f"WHO WE SELL TO: {target}\n"
    c += f"OUR EDGE: {', '.join(edge) if edge else 'professional quality'}\n"
    c += f"A good lead is a company/person that NEEDS our services and has budget.\n"
    c += f"A bad lead is: a competitor, a job listing, a news article, a platform vendor, or someone who already has what we offer in-house.\n"

    c += f"""
ANALYSIS FRAMEWORK:
1. PAGE TYPE: Company/org that matches our target client profile=promising. Blog/news/job listing/competitor/vendor=score 0.
2. BUYER SIGNALS: Budget evidence (sponsors, paid tiers, professional site, hiring), recurring need, growth signals, pain points we solve, timing (upcoming projects/dates), scale.
3. DISQUALIFY if: no specific org, no visible need for our services, no budget evidence, or content older than 18 months → score 0-3.
4. COMPETITIVE INTEL: What tools/services do they currently use? What's lacking? What would {company} improve? This is the "service_gap" — be specific.

SCORING: Use the detailed scoring rubric provided in the analysis instructions below — do not invent your own scale.
Rules: 7+ needs 2+ concrete signals. 9+ needs budget AND recurring evidence. 5+ needs specific org. why_fit must cite page evidence. evidence_quote must be verbatim. When in doubt, score LOWER.
"""

    if avoid:
        avd = {"avoid_solo":"Solo freelancers / one-person operations",
               "avoid_zoom_basic":"Happy with basic tools, no upgrade interest",
               "avoid_free_events":"Free/volunteer/community activities with no budget",
               "avoid_competitors":"Competitors offering similar services to ours",
               "avoid_no_budget":"Tiny startups / bootstrapped companies with no budget",
               "avoid_inhouse":"Organisations with existing in-house capabilities covering our services",
               "avoid_platforms":"Platform vendors / SaaS tools (competitors to the service)"}
        c += "\nAUTO-REJECT (score 0-2 maximum):\n"
        for a in avoid: c += f"- {avd.get(a, a)}\n"

    if clients:
        clm = {"enterprise":"Large corporations (1000+ employees)","midsize":"Mid-size companies (100-1000 employees)",
               "associations":"Associations, membership organisations & professional bodies",
               "education":"Universities, training providers & educational institutions","healthcare":"Healthcare, pharma & biotech",
               "finance":"Finance, banking & insurance","tech":"Technology, SaaS & software companies",
               "agencies":"Marketing & creative agencies","government":"Government & public sector",
               "small_business":"Small businesses (10-100 employees)"}
        c += f"\nPRIORITY CLIENT TYPES (these are {company}'s ideal buyers — a match here is necessary but not sufficient for a high score):\n"
        for cl in clients: c += f"- {clm.get(cl, cl)}\n"

    # Inject wizard buyability/targeting rules
    if _w.get("icp_size"):
        c += f"\nIDEAL CLIENT SIZE: {', '.join(_w['icp_size'])}\n"
    if _w.get("buyer_roles"):
        c += f"\nBUYER ROLES WE TARGET: {', '.join(_w['buyer_roles'])}\n"
    if _w.get("triggers"):
        c += f"\nBUYING TRIGGERS: {', '.join(_w['triggers'])}\n"
    if _w.get("exclusions"):
        c += f"\nEXCLUDE: {', '.join(_w['exclusions'])}\n"
    if _w.get("reject_enterprise"):
        c += "\nRULE: Reject giant enterprises / Fortune 500 / mega-corps\n"
    if _w.get("reject_government"):
        c += "\nRULE: Reject government and public institutions\n"
    if _w.get("reject_strong_inhouse"):
        c += "\nRULE: Reject companies with large dedicated internal teams unless clear overflow/external need\n"
    if _w.get("reject_no_contact"):
        c += "\nRULE: Reject leads with no reachable contact path\n"
    _regions = _w.get("regions", [])
    if _regions and not any(r.lower() in ("global", "worldwide") for r in _regions):
        c += f"\nTARGET GEOGRAPHY: {', '.join(_regions)}\nPenalise fit_score by 2 for companies clearly outside these regions. Do NOT auto-reject — the company may still be relevant — but geographic mismatch is a negative signal.\n"

    # Deal size and sales cycle context for smarter qualification
    _deal = _w.get("deal_size", "")
    if _deal:
        c += f"\nTYPICAL DEAL SIZE: {_deal}. Penalise prospects whose likely budget is far outside this range — score buyability lower for mismatched deal sizes.\n"
    _cycle = _w.get("sales_cycle", "")
    if _cycle:
        c += f"\nSALES CYCLE: {_cycle}. Prospects with decision timelines that clearly conflict with this cycle are lower-priority.\n"

    # Geography targeting context
    _regions = _w.get("regions", _w.get("geography", []))
    if _regions and isinstance(_regions, list):
        _non_global = [r for r in _regions if r.lower() != "global"]
        if _non_global:
            c += f"\nTARGET GEOGRAPHY: {', '.join(_non_global)}. Prospects outside these regions should score lower on fit (reduce by 2-3 points). Only score 7+ if the prospect is clearly within target geography or serves these markets.\n"

    # New intelligence signals for smarter scoring
    if _w.get("past_clients"):
        c += f"\nSIMILAR CLIENT SIGNAL: Companies similar to these past clients are HIGH-VALUE prospects: {_w['past_clients'][:300]}\nIf the page describes a company with similar characteristics, boost fit_score by 1-2 points.\n"
    if _w.get("hiring_signals"):
        c += f"\nHIRING-AS-NEED SIGNAL: If the page shows the company is hiring for these roles, it means they NEED our services: {_w['hiring_signals'][:300]}\nA company that's been posting this job for weeks probably can't find someone — they need outsourced help NOW. Boost timing_score by 2.\n"
    if _w.get("buyer_search_terms"):
        c += f"\nBUYER LANGUAGE: Our real buyers use these terms when they need our service: {_w['buyer_search_terms'][:200]}\nIf the page contains these exact phrases or close variants, it's a strong buying signal.\n"
    if _w.get("competitors"):
        c += f"\nCOMPETITOR AWARENESS: Our competitors are: {_w['competitors'][:200]}\nIf the page mentions evaluating/using/switching from a competitor, the prospect is an ACTIVE buyer — boost timing_score.\nBut if the page IS a competitor's website, score 0.\n"

    c += f"""
EMAIL WRITING:
Tone: {tone_map.get(tone, tone_map['friendly'])}
Price: {price}

Structure: 1)HOOK: Reference THEIR specific situation/detail. Never start with "I"/"We". 2)INSIGHT: One specific gap we can fill. Be concrete, not vague. 3)VALUE: Concrete picture of improvement. 4)OFFER: Free audit/consultation/discount — tangible, low-risk. 5)CTA: One question answerable in <10 words.

Rules: Subject max 6 words, specific to THEM. Never sound like a template. First half is about THEM. No exclamation marks. No AI/Huntova references. No filler ("I came across","I hope this finds you well"). No "..." placeholders. Ongoing needs → mention long-term partnership value. LinkedIn note <180 chars.

ACCURACY: country=specific name (never "EU"/"Europe"). org_name=the prospect company name. event_name=context or opportunity that brought them up. evidence_quote=verbatim from page. contacts=extracted not invented. production_gap=specific service opportunity, evidence-based.

PSYCHOLOGY: Pattern interrupt (specific reference). Short sentences. Frame as loss not gain. Be specific=credible. Offer free value first. Position as small upgrade.
"""

    # ── Inject Agent DNA if available (cached on ctx to avoid N+1 DB queries) ──
    ctx = _ctx()
    if ctx:
        # Use cached DNA from ctx (loaded once at agent start) — NOT a DB query per lead
        _dna = getattr(ctx, '_cached_dna', None)
        if _dna:
            if _dna.get("business_context"):
                c += f"\n\n═══ AGENT INTELLIGENCE PROFILE (v{_dna.get('version',1)}) ═══\n{_dna['business_context']}\n"
            if _dna.get("scoring_rules"):
                c += f"\n═══ SCORING RULES ═══\n{_dna['scoring_rules']}\n"
            if _dna.get("email_rules"):
                c += f"\n═══ EMAIL RULES ═══\n{_dna['email_rules']}\n"
            # Inject anti-patterns from hunting strategy for auto-rejection
            _strategy = _dna.get("hunting_strategy") or _dna.get("strategy")
            if isinstance(_strategy, dict) and _strategy.get("anti_patterns") and isinstance(_strategy["anti_patterns"], dict):
                ap = _strategy["anti_patterns"]
                c += "\n═══ AUTO-REJECT PATTERNS (from DNA — score 0 if matched) ═══\n"
                if isinstance(ap.get("competitor_signals"), list) and ap["competitor_signals"]:
                    c += f"COMPETITOR SIGNALS (score 0): {', '.join(str(s) for s in ap['competitor_signals'][:10])}\n"
                if isinstance(ap.get("noise_signals"), list) and ap["noise_signals"]:
                    c += f"NOISE SIGNALS (score 0-2): {', '.join(str(s) for s in ap['noise_signals'][:10])}\n"
                if isinstance(ap.get("wrong_audience_signals"), list) and ap["wrong_audience_signals"]:
                    c += f"WRONG AUDIENCE (score 0-2): {', '.join(str(s) for s in ap['wrong_audience_signals'][:10])}\n"
                if isinstance(ap.get("service_mismatch_signals"), list) and ap["service_mismatch_signals"]:
                    c += f"SERVICE MISMATCH (score 0): {', '.join(str(s) for s in ap['service_mismatch_signals'][:10])}\n"

    return c



LEAD_SCHEMA = {
    "type": "object",
    "properties": {
        "org_name":            {"type": "string"},
        "country":             {"type": "string"},
        "city":                {"type": "string"},
        "region":              {"type": "string", "enum": ["EU", "USA", "Middle East", "UK", "Other"]},
        "event_name":          {"type": "string"},
        "event_type":          {"type": "string"},
        "platform_used":       {"type": "string"},
        "is_virtual_only":     {"type": "boolean"},
        "is_recurring":        {"type": "boolean"},
        "frequency":           {"type": "string"},
        "audience_size_guess": {"type": "string"},
        "org_website":         {"type": "string"},
        "org_linkedin":        {"type": "string"},
        "contact_name":        {"type": "string"},
        "contact_role":        {"type": "string"},
        "contact_department":  {"type": "string"},
        "contact_email":       {"type": "string"},
        "contact_phone":       {"type": "string"},
        "contact_linkedin":    {"type": "string"},
        "contact_page_url":    {"type": "string"},
        "evidence_quote":      {"type": "string"},
        "production_gap":      {"type": "string"},
        "fit_score":           {"type": "integer", "minimum": 0, "maximum": 10},
        "why_fit":             {"type": "string"},
        "email_subject":       {"type": "string"},
        "email_body":          {"type": "string"},
        "linkedin_note":       {"type": "string"},
    },
    "required": [
        "org_name","country","region","event_name","event_type","platform_used",
        "is_virtual_only","is_recurring","frequency","audience_size_guess",
        "evidence_quote","production_gap","fit_score","why_fit",
        "email_subject","email_body","linkedin_note",
    ],
    "additionalProperties": False,
}





def generate_queries_ai(wizard_data, countries, max_queries=100):
    """Two-phase AI query generation: ANALYSE → STRATEGISE → SEARCH."""
    
    company = wizard_data.get("company_name", "our company")
    desc = wizard_data.get("business_description", "")
    services = wizard_data.get("services", [])
    edge = wizard_data.get("differentiators", wizard_data.get("edge", []))
    clients = wizard_data.get("ideal_clients", wizard_data.get("clients", []))
    site_ctx = wizard_data.get("_site_context", "")
    target = wizard_data.get("target_clients", "")
    avoid = wizard_data.get("red_flags", [])
    past_wins = wizard_data.get("past_wins", "")
    price = wizard_data.get("price_range", "flexible")
    
    profile = []
    if company and company != "our company": profile.append(f"Company: {company}")
    if desc: profile.append(f"Description: {desc[:800]}")
    if site_ctx: profile.append(f"Website content: {site_ctx[:1000]}")
    if services: profile.append(f"Services: {', '.join(services)}")
    if edge: profile.append(f"Differentiators: {', '.join(edge)}")
    if clients: profile.append(f"Ideal clients: {', '.join(clients)}")
    if past_wins: profile.append(f"Past wins: {past_wins[:400]}")
    if price: profile.append(f"Price range: {price}")
    if avoid: profile.append(f"Avoid: {', '.join(avoid)}")
    # New wizard intelligence fields
    _past_clients = wizard_data.get("past_clients", "")
    _buyer_terms = wizard_data.get("buyer_search_terms", "")
    _hiring_signals = wizard_data.get("hiring_signals", "")
    _competitors = wizard_data.get("competitors", "")
    if _past_clients: profile.append(f"Past/example clients: {_past_clients[:400]}")
    if _buyer_terms: profile.append(f"What buyers search for: {_buyer_terms[:300]}")
    if _hiring_signals: profile.append(f"Job postings that signal need: {_hiring_signals[:300]}")
    if _competitors: profile.append(f"Competitors: {_competitors[:200]}")

    profile_text = "\n".join(profile) if profile else "General B2B service provider"
    countries_text = ", ".join(countries) if countries else "worldwide"
    
    # ═══════════════════════════════════════════════════════════
    # PHASE 1: Deep business analysis → Hunt Strategy
    # ═══════════════════════════════════════════════════════════
    
    _svc_list = ', '.join(services) if services else desc[:200]
    strategy_prompt = f"""You are a senior B2B growth strategist who has built $50M+ in sales pipeline for service businesses. Your job is to reverse-engineer the EXACT buyer journey for this specific business and create a surgical lead hunting plan.

BUSINESS PROFILE:
{profile_text}

TARGET REGIONS: {countries_text}

Think like the BUYER, not the seller. Put yourself in the shoes of someone who NEEDS this service and is about to start looking.

For each section below, generate examples SPECIFIC TO THIS BUSINESS using only the profile above. Do NOT use generic examples. Do NOT assume any particular industry.

═══ ANSWER EVERY QUESTION WITH EXTREME SPECIFICITY ═══

1. BUYER PROFILE — WHO signs the contract for {company}'s services?
- What are the exact job titles of people who make this purchasing decision?
- What type of organisation do they work for? (Not just "companies" — be specific about size, industry, characteristics)
- What department owns the budget?
- What's their internal pain? What makes them look bad to their boss if they don't solve this?

2. BUYING TRIGGERS — What makes someone search for {company}'s services RIGHT NOW?
- What specific situations create urgency? Think about timing triggers.
- Generate 3-4 triggers specific to this business type.
- What INTERNAL changes trigger a search? (New hire, budget cycle, strategic shift)
- What EXTERNAL changes trigger a search? (Industry changes, competitive pressure, regulatory shifts)

3. DIGITAL FOOTPRINT — Where do buyers for {company} leave traces online?
- What types of web pages would someone who NEEDS {_svc_list} appear on?
- Industry directory listings, company websites, procurement pages, project announcements?
- What types of pages LOOK like leads but are NOT? (Competitors, vendor marketing, news articles, job boards)

4. KEYWORD FORENSICS — What words appear on pages where {company}'s ideal buyers are?
- Based on the services above, what words indicate a page is a BUYER page vs a WASTE page?
- Industry-specific terminology that only appears on genuine prospect pages in the target sectors

5. INDUSTRY RANKING — WHERE is the money?
- Rank the top 5 industries for this business by conversion likelihood
- For each: WHY they buy, HOW MUCH they typically spend, and WHAT makes them different from other industries
- Which industries have the shortest sales cycles? Which have the highest deal values?

6. TIMING & SEASONALITY
- When do different industries plan their purchases of these services?
- What months are "dead" vs. "hot" for prospecting in each industry?
- Are there industry-specific calendar patterns? (Fiscal year, project cycles, seasonal needs)

7. SEARCH STRATEGY — HOW would an expert human researcher find these buyers?
- Think like an investigative journalist. What search queries would you use on Google/SearXNG?
- What combinations of industry terms + need signals + location terms work best?
- What are the "long tail" searches that find hidden gems?
- What local-language terms would find prospects in non-English markets?

Respond with a detailed, structured strategy. Name real industries, real job titles, real buyer situations, and real search patterns."""

    try:
        emit_log("🧠 Phase 1: Analysing your business to create hunt strategy...", "ai")
        emit_thought("Deep-analysing your business model, buyers, and market...", "thinking")
        
        # Stability fix (Perplexity bug #36): explicit 60s timeout so a
        # stuck Gemini upstream can't hang the wizard's strategy
        # generation forever. Same pattern as _ai_json_kwargs.
        resp1 = client.chat.completions.create(
            model=_get_tier_model(),
            messages=[
                {"role": "system", "content": "You are a $500/hr B2B growth consultant. Your strategies have generated $50M+ in pipeline. Think with extreme depth and precision. Every recommendation must be specific enough to act on immediately — no generic advice."},
                {"role": "user", "content": strategy_prompt}
            ],
            temperature=0.35,
            max_tokens=2048,
            timeout=60,
        )
        
        strategy = (resp1.choices[0].message.content or "").strip()
        emit_log(f"✅ Hunt strategy created ({len(strategy)} chars)", "ok")
        emit_thought("Strategy ready. Now generating precision search queries...", "thinking")
        
        # Save strategy for reference
        try:
            wizard_data["_hunt_strategy"] = strategy[:3000]
            s = load_settings(); s["wizard"] = wizard_data; save_settings(s)
        except: pass
        
    except Exception as e:
        emit_log(f"⚠️ Strategy generation failed: {e} — using direct approach", "warn")
        strategy = f"Business: {profile_text}"
    
    # ═══════════════════════════════════════════════════════════
    # PHASE 2: Strategy → Precision Search Queries
    # ═══════════════════════════════════════════════════════════
    
    query_prompt = f"""You are an elite search intelligence operator. Your job is to craft {max_queries} search queries that find REAL BUYERS — not noise — via SearXNG (a meta-search engine).

BUSINESS PROFILE:
{profile_text}

HUNT STRATEGY:
{strategy[:2000]}

TARGET REGIONS: {countries_text}
{wizard_data.get('_learning_context', '')}

═══ HOW SEARXNG WORKS — CRITICAL ═══

SearXNG is a meta-search engine. It aggregates results from Google, Bing, DuckDuckGo, etc. It works best with SHORT, NATURAL queries that a human would type.

QUERY ENGINEERING RULES:
1. LENGTH: 3-7 words max. Shorter = more results. "pharma services 2026 Germany" beats "pharmaceutical industry annual services provider Germany 2026"
2. NO site: OPERATORS — they break on SearXNG. Use country names instead.
3. QUOTES: Only for common 2-word phrases. Never for 3+ word phrases.
4. NATURAL LANGUAGE: Write queries the way a human would search. Short and direct.
5. AVOID JARGON: Use words that appear on BUYER PAGES, not marketing jargon.
6. NEVER use words like "looking for", "hiring", "jobs", "freelance" — these find job listings, not prospects
8. Focus on finding ORGANISATION WEBSITES and BUYER PAGES, not job boards or news articles

═══ QUERY STRATEGY — THINK LIKE 9 DIFFERENT RESEARCHERS ═══

You must generate queries across ALL of these angles. Do not over-index on any single approach.

CRITICAL: Generate example queries using ONLY the business profile above. A {company} that offers {_svc_list} needs queries about {target}, not generic templates.

A) DIRECT BUYER DISCOVERY (20%): Find companies/people that need our services
   Goal: Land on pages of organisations that would buy what we offer
   Pattern: [industry term] + [need signal] + [year] + [country]

B) ORGANISATION HUNT (15%): Find companies in our target client industries
   Goal: Land on organisation websites showing they need our type of services
   Pattern: [industry] + [org type] + [region]

C) RECURRING NEED SIGNALS (10%): Find organisations with ongoing needs for our services
   Goal: Pages showing recurring projects, repeat purchases, or ongoing service needs
   Pattern: [frequency] + [service need] + [industry] + [year]

D) DIRECTORY / PLATFORM TRACES (10%): Find prospects listed on industry directories or platforms
   Goal: Find potential buyers listed in industry-specific directories or marketplaces
   Pattern: [directory/platform] + [industry] + [region]

E) BUDGET / GROWTH SIGNALS (10%): Find companies showing signs of growth or spending
   Goal: Pages showing budget, hiring, expansion, investment — signs they can pay
   Pattern: [industry] + [growth signal] + [region]

F) ACTIVE BUYING SIGNALS (10%): Find pages showing active procurement or project planning
   Goal: Procurement pages, project announcements, tender notices, partnership pages
   Pattern: [procurement signal] + [service type] + [region]

G) COMPETITOR ALTERNATIVE SEEKERS (10%): Find companies evaluating competitors or alternatives
   Goal: Pages where prospects compare solutions, review competitors, or seek alternatives to existing tools/providers
   Pattern: [competitor name] + "alternative" OR "vs" OR "review" OR "switch from"
   Pattern: "looking for" + [service type] + [industry]
   Pattern: [competitor name] + "pricing" (prospects researching = active buyers)

H) HIRING-AS-NEED INDICATOR (10%): Companies hiring for roles that signal they need outsourced help
   Goal: Find companies posting jobs for roles they can't fill — meaning they need external help NOW
   Pattern: [buyer job title] + "hiring" + [industry] + [region]
   Why: A company posting for a role they can't fill probably needs external help in that area TODAY.
   IMPORTANT: These are NOT job listings for US — they are COMPANIES with an unfilled need.

I) LOCAL LANGUAGE (5%): Target non-English markets with native terms for our services
   Generate local-language queries relevant to the services and industries in the profile above.

═══ ANTI-NOISE RULES ═══
NEVER generate queries that would find:
- Competitor companies offering similar services to ours (except for Category G — competitor alternatives)
- Platform vendor/marketing pages (software pricing, feature comparison, demos)
- Pure job board listings (Indeed, LinkedIn Jobs, Glassdoor) — but company career pages ARE useful for Category H
- News articles, blog posts, opinion pieces (unless about a specific company's expansion/growth)
- The sender's own company website
- Generic tutorial/how-to content
- Outdated content from 2+ years ago

═══ DIVERSITY RULES ═══
- Spread queries across ALL target regions — not just the first country listed
- Vary industry terms — don't repeat the same industry 50 times
- Mix specific and broad — some queries should cast a wide net, others should be surgical
- Include both English and local language variants for non-English markets
- Every query must be UNIQUE — no duplicates or near-duplicates

Output ONLY valid JSON. No explanation. No markdown.
{{"queries": ["query1", "query2", ...]}}"""

    try:
        emit_log(f"🔎 Phase 2: Generating {max_queries} precision search queries...", "ai")
        
        resp2 = client.chat.completions.create(**_ai_json_kwargs(
            model=_get_tier_model(),
            messages=[
                {"role": "system", "content": "You are an elite search intelligence operator who finds hidden B2B buyers that competitors miss. You think like an investigative journalist — using creative query angles, local languages, and platform-specific searches to uncover leads that basic searches would never find. Output ONLY valid JSON: {\"queries\": [\"query1\", \"query2\", ...]}. No explanation."},
                {"role": "user", "content": query_prompt}
            ],
            temperature=0.6,
            max_tokens=4096
        ))
        
        raw = (resp2.choices[0].message.content or "").strip()
        # Strip think tags if present
        if "<think>" in raw:
            raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
            raw = re.sub(r"<think>.*$", "", raw, flags=re.DOTALL).strip()
        js = extract_json(raw)
        if js:
            parsed = json.loads(js)
            # Gemini with response_format wraps arrays in objects like {"queries": [...]}
            if isinstance(parsed, dict):
                # Find the first list value in the object
                for v in parsed.values():
                    if isinstance(v, list):
                        parsed = v
                        break
            queries = parsed
            if isinstance(queries, list) and len(queries) >= 3:
                # Clean up — strip site: operators that the AI sometimes adds despite instructions
                clean = []
                for q in queries:
                    if not isinstance(q, str) or not (5 < len(q) < 200): continue
                    q = re.sub(r'\bsite:\S+', '', q).strip()
                    if len(q) > 5: clean.append(q)
                # Deduplicate. Strip ALL quote characters (not just
                # leading/trailing) and collapse whitespace so two queries
                # that only differ in quoting (`"pharma sales" Germany`
                # vs `pharma sales germany`) coalesce to one slot. Without
                # this, the AI burns query-budget on near-duplicates.
                seen = set()
                unique = []
                for q in clean:
                    key = re.sub(r'\s+', ' ', q.lower().replace('"', '')).strip()
                    if key not in seen:
                        seen.add(key)
                        unique.append(q)
                
                # Post-filter: remove queries targeting excluded regions or job boards
                _bad_q = re.compile(r"\b(uk|united kingdom|hiring|jobs|freelance|looking for.*producer|career|recruitment)\b", re.I)
                unique = [q for q in unique if not _bad_q.search(q)]
                emit_log(f"✅ Generated {len(unique)} unique precision queries", "ok")
                emit_thought(f"Hunt strategy ready — {len(unique)} custom queries designed for {company}!", "done")
                return unique
        
        # Debug: log what we got so we can diagnose
        try:
            if js:
                emit_log(f"⚠️ Query gen: parsed type={type(parsed).__name__}, len={len(parsed) if hasattr(parsed,'__len__') else '?'}, raw[:150]={raw[:150]}", "warn")
            else:
                emit_log(f"⚠️ Query gen: no JSON extracted from {len(raw)} chars: {raw[:200]}", "warn")
        except Exception:
            pass
        emit_log("⚠️ Query generation bad format — using fallback", "warn")
    except Exception as e:
        emit_log(f"⚠️ Query generation failed: {e} — using fallback", "warn")
    
    return _fallback_queries(wizard_data, countries)



def _classify_archetype(wiz):
    """Classify business into a primary archetype. Deterministic rules first, AI fallback."""
    desc = (wiz.get("business_description", "") or "").lower()
    services = [s.lower() for s in (wiz.get("services", []) or [])]
    svc_text = " ".join(services)
    all_text = desc + " " + svc_text

    # Deterministic keyword classification — ORDER MATTERS (service_agency before media_publisher)
    _RULES = [
        ("recruiter", ["recruiting", "recruitment", "staffing", "talent acquisition", "headhunt", "placement", "hr outsourc", "executive search"]),
        ("software", ["saas", "software", "platform", "app development", "cloud", "api", "devops", "tech product"]),
        ("consultant", ["consulting", "advisory", "strategy", "transformation", "management consulting", "business consulting"]),
        ("professional_firm", ["law firm", "legal", "accounting", "tax", "audit", "notary", "compliance"]),
        ("manufacturer", ["manufacturing", "factory", "production line", "industrial", "fabricat"]),
        ("distributor", ["distribution", "wholesale", "import", "export", "reseller", "supply chain"]),
        ("local_b2b", ["cleaning", "maintenance", "catering", "facility", "security service", "office service"]),
        # service_agency MUST be before media_publisher — production/studio/done-for-you = agency, not publisher
        ("service_agency", [
            "agency", "production agency", "production studio", "production company",
            "remote production", "live production", "video production", "event production",
            "post-production", "post production", "live streaming", "streaming service",
            "done-for-you", "managed service", "outsourced", "studio",
            "marketing agency", "design agency", "pr agency", "creative agency",
            "digital agency", "digital marketing", "brand strategy", "content marketing",
            "seo agency", "social media agency",
        ]),
        ("media_publisher", ["publishing", "content creation", "journalism", "editorial", "news outlet", "magazine"]),
    ]

    scores = {}
    for archetype, keywords in _RULES:
        score = sum(1 for kw in keywords if kw in all_text)
        if score > 0:
            scores[archetype] = score

    if scores:
        sorted_archetypes = sorted(scores.items(), key=lambda x: -x[1])
        primary = sorted_archetypes[0][0]
        secondary = sorted_archetypes[1][0] if len(sorted_archetypes) > 1 else None
        confidence = min(95, 40 + sorted_archetypes[0][1] * 15)
    else:
        primary = "other"
        secondary = None
        confidence = 20

    return {
        "primary": primary,
        "secondary": secondary,
        "confidence": confidence,
    }


def _build_hunt_brain(wiz):
    """Build a normalized hunt brain from wizard data. Stored alongside raw wizard profile."""
    from datetime import datetime, timezone

    # Classify archetype
    arch = _classify_archetype(wiz)

    # Clean fields (reuse existing cleaning logic)
    _LEGACY = set()  # No hardcoded service names to strip
    _VAGUE = {"services", "solutions", "company", "business", "organization", "professional",
              "consulting", "management", "agency", "other", "custom", "general", "various"}

    def _clean(items, strip_legacy=True):
        out = []
        for item in (items or []):
            if not isinstance(item, str): continue
            s = item.strip().replace("_", " ")
            if not s or len(s) < 3: continue
            if strip_legacy and s.lower() in _LEGACY: continue
            if s.lower() in _VAGUE: continue
            if ". " in s or "..." in s: continue
            out.append(s)
        return out

    services = _clean(wiz.get("services", []))
    industries = _clean(wiz.get("icp_industries", wiz.get("industries_served", [])))
    roles = _clean(wiz.get("buyer_roles", []), strip_legacy=False)
    triggers = _clean(wiz.get("triggers", []), strip_legacy=False)
    exclusions = _clean(wiz.get("exclusions", []), strip_legacy=False)

    # Build offer summary from description
    desc = wiz.get("business_description", "") or ""
    for lt in _LEGACY:
        desc = re.sub(rf'\b{re.escape(lt)}\b', '', desc, flags=re.IGNORECASE)
    desc = re.sub(r'\s+', ' ', desc).strip()

    # Confidence
    confidence = 0
    blocking = []
    warnings = []
    if services: confidence += 25
    else: blocking.append("no_services")
    if industries: confidence += 20
    elif wiz.get("target_clients"): confidence += 10
    else: blocking.append("no_icp")
    if roles: confidence += 15
    else: warnings.append("no_buyer_roles")
    if triggers: confidence += 10
    else: warnings.append("no_triggers")
    if desc and len(desc) > 20: confidence += 15
    if wiz.get("_interview_complete"): confidence += 10
    if exclusions: confidence += 5
    else: warnings.append("weak_exclusions")

    can_hunt = len(blocking) == 0 and confidence >= 25

    brain = {
        "hunt_brain_version": 1,
        "built_at": datetime.now(timezone.utc).isoformat(),
        "source": "wizard_save",
        "archetype": arch["primary"],
        "archetype_secondary": arch["secondary"],
        "archetype_confidence": arch["confidence"],
        "offer_summary": desc[:300],
        "services_clean": services,
        "ideal_company_types": _clean(wiz.get("icp_size", []), strip_legacy=False),
        "ideal_company_size": _clean(wiz.get("icp_size", []), strip_legacy=False),
        "excluded_company_types": [],
        "preferred_industries": industries,
        "excluded_industries": _clean(wiz.get("excluded_industries", []), strip_legacy=False),
        "preferred_regions": _clean(wiz.get("regions", []), strip_legacy=False),
        "excluded_regions": [],
        "buyer_roles_clean": roles,
        "triggers_clean": triggers,
        "exclusions_clean": exclusions,
        "reachability_rules": {
            "require_named_contact": wiz.get("reject_no_contact", True),
        },
        "buyability_rules": {
            "reject_enterprise": wiz.get("reject_enterprise", True),
            "reject_government": wiz.get("reject_government", True),
            "reject_strong_inhouse": wiz.get("reject_strong_inhouse", True),
        },
        "inhouse_tolerance": "low" if wiz.get("reject_strong_inhouse", True) else "medium",
        "enterprise_tolerance": "reject" if wiz.get("reject_enterprise", True) else "allow",
        "outreach_tone": wiz.get("outreach_tone", "consultative"),
        "example_good_clients": wiz.get("example_good_clients", []),
        "example_bad_clients": wiz.get("example_bad_clients", []),
        "profile_confidence": confidence,
        "can_hunt": can_hunt,
        "blocking_flags": blocking,
        "warning_flags": warnings,
    }
    return brain


def _normalize_hunt_profile(wiz):
    """Legacy wrapper — now delegates to _build_hunt_brain."""
    brain = _build_hunt_brain(wiz)
    return {
        "profile": wiz,
        "services_clean": brain["services_clean"],
        "industries_clean": brain["preferred_industries"],
        "roles_clean": brain["buyer_roles_clean"],
        "triggers_clean": brain["triggers_clean"],
        "confidence": brain["profile_confidence"],
    }


def _classify_prospect_page(text, html, url, brain):
    """Stage 2: Cheap deterministic check — is this page likely a prospect company page?
    Returns (pass: bool, reason: str). Runs BEFORE expensive AI analysis."""
    text_lower = text[:3000].lower()
    url_lower = url.lower()
    html_lower = (html or "")[:5000].lower()

    # ── Positive signals: looks like a company/org/event page ──
    _company_signals = [
        "about us", "our team", "our services", "contact us", "our clients",
        "case studies", "our approach", "leadership", "management team",
        "founded in", "established in", "headquartered", "offices in",
        "we help", "we provide", "we offer", "we specialize", "we deliver",
        "our mission", "our vision", "careers", "join our team",
    ]
    _company_score = sum(1 for sig in _company_signals if sig in text_lower)

    # ── Negative signals: editorial/reference/non-prospect page ──
    _editorial_signals = [
        "published on", "by the editorial team", "read more articles",
        "share this article", "comments section", "leave a comment",
        "subscribe to newsletter", "related articles", "trending topics",
        "in this article", "table of contents", "key takeaways",
        "according to a study", "researchers found", "the study shows",
    ]
    _editorial_score = sum(1 for sig in _editorial_signals if sig in text_lower)

    _reference_signals = [
        "definition:", "what is ", "meaning of ", "glossary",
        "encyclopedia", "dictionary entry", "see also:",
        "references:", "citations:", "bibliography",
    ]
    _reference_score = sum(1 for sig in _reference_signals if sig in text_lower)

    _help_signals = [
        "help center", "knowledge base", "support article",
        "troubleshooting", "step-by-step guide", "faq",
        "how to use", "getting started", "documentation",
    ]
    _help_score = sum(1 for sig in _help_signals if sig in text_lower)

    # ── Decision logic ──
    # Strong negative: clearly not a prospect page
    if _editorial_score >= 3:
        return False, "editorial/article page"
    if _reference_score >= 2:
        return False, "reference/definition page"
    if _help_score >= 3:
        return False, "help/documentation page"

    # If no company signals at all and some negative signals, reject
    if _company_score == 0 and (_editorial_score + _reference_score + _help_score) >= 2:
        return False, "no company signals + informational content"

    # Check page structure: does it have basic company page markers?
    _has_contact = bool(re.search(r'(mailto:|tel:|phone|email us|contact form)', html_lower))
    _has_nav = bool(re.search(r'(about|services|team|portfolio|clients|contact)', html_lower[:2000]))

    # Very short pages with no company markers
    if len(text.strip()) < 200 and _company_score == 0:
        return False, "too short with no company signals"

    # ── Archetype-specific checks ──
    _arch = brain.get("archetype", "other") if brain else "other"
    if _arch == "recruiter":
        _job_listing_signals = ["apply now", "submit your cv", "job requirements:", "qualifications:", "responsibilities:"]
        if sum(1 for s in _job_listing_signals if s in text_lower) >= 2:
            return False, "individual job listing (want company page)"
    elif _arch == "software":
        if any(s in text_lower for s in ["user rating", "app review", "download for free", "install now"]):
            return False, "app store/review page"

    # ── Wizard-driven lookalike rejection ──
    try:
        _wiz = load_settings().get("wizard", {})
        _look_raw = _wiz.get("lookalikes", "")
        if _look_raw:
            _look_phrases = [p.strip().lower() for p in re.split(r'[,\n;]|(?:\.\s)', _look_raw) if len(p.strip()) > 4]
            _look_hits = sum(1 for p in _look_phrases if p in text_lower)
            if _look_hits >= 2:
                return False, f"lookalike company (user-defined, {_look_hits} signals)"
    except Exception:
        pass

    return True, "ok"


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# DNA v2 — Two-Stage Agent Intelligence System
# Stage 1: Hunting Strategy  → WHO are we looking for, WHERE do they live online
# Stage 2: Query Generation  → 50-80 searches that find those exact pages
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

_DNA_STAGE_1_SYSTEM = """You are a senior B2B sales strategist. You've spent 20 years finding clients for every type of business — from local tradespeople to global SaaS companies.

Your job is NOT to write search queries. Your job is to THINK about where prospects exist online before anyone searches for anything.

You think in terms of HUNTING CHANNELS — the specific types of web pages where a prospect's OWN content lives. Not news about them. Not directories listing them. Their OWN pages that reveal they need this service.

CRITICAL: Every hunting channel you propose must be SEARCHABLE via a meta-search engine (SearXNG — aggregates Google, Bing, DuckDuckGo, Brave, Startpage, Qwant). That means:

SEARCHABLE channels (propose these):
- Company event/conference/webinar pages
- Association/industry body pages
- Company /about, /team, /services pages
- Corporate job postings on company websites
- Company project/portfolio/case study pages
- Registration/speaker/sponsor pages for events
- Company blog posts announcing their own events or projects
- Industry forum threads (Reddit, niche forums)

NON-SEARCHABLE channels (NEVER propose these):
- LinkedIn posts or profiles (cannot crawl LinkedIn)
- Social media posts (Twitter/X, Instagram, Facebook — cannot reliably search or crawl)
- Private Slack/Discord communities
- Email newsletters (not on the web)
- Paid databases (Crunchbase Pro, ZoomInfo, etc.)
- App marketplaces (iOS/Android app stores)

CRITICAL: Your entire response must be a single raw JSON object starting with { — no text before it, no explanation, no thinking out loud, no markdown. Respond with ONLY the JSON."""

_DNA_STAGE_2_SYSTEM = """You are a search query engineer who specializes in SearXNG — an open-source meta-search engine that aggregates Google, Bing, DuckDuckGo, Brave, Startpage, and Qwant.

You know three critical things:
1. SearXNG does NOT support advanced operators (inurl:, intitle:, site:, filetype:). Using them returns garbage.
2. SearXNG handles quoted phrases inconsistently across engines. NEVER use quotes in queries.
3. SearXNG returns results from all engines — some engines are less precise. Your queries must be robust enough that MOST engines return relevant results.

Your process:
1. Read the hunting strategy carefully
2. Generate 100-120 CANDIDATE queries across all channels
3. For each candidate, mentally simulate: "what would the #1 result be on Google/Bing/DuckDuckGo?"
4. KILL any query where the likely #1 result is NOT a prospect's own website
5. KILL queries that are duplicates or would return overlapping results
6. Return the best 60-80 surviving queries

CRITICAL: Your entire response must be a single raw JSON object starting with { — no text before it, no explanation, no thinking out loud, no markdown. Respond with ONLY the JSON."""


def _dna_build_stage_1_prompt(wizard_data):
    """Build the Stage 1 user prompt from wizard data."""

    company = wizard_data.get("company_name", "the company")
    desc = wizard_data.get("business_description", "")
    services = wizard_data.get("services", [])
    industries = wizard_data.get("icp_industries", wizard_data.get("industries_served", []))
    target = wizard_data.get("target_clients", "")
    differentiators = wizard_data.get("differentiators", wizard_data.get("edge", []))
    tone = wizard_data.get("outreach_tone", wizard_data.get("email_tone", "friendly"))
    regions = wizard_data.get("regions", [])
    buyer_roles = wizard_data.get("buyer_roles", [])
    triggers = wizard_data.get("triggers", [])
    exclusions = wizard_data.get("exclusions", [])
    site_ctx = wizard_data.get("_site_context", wizard_data.get("_site_text", ""))[:1500]
    how_it_works = wizard_data.get("how_it_works", "")
    price_tier = wizard_data.get("price_range", wizard_data.get("price_tier", ""))
    sales_cycle = wizard_data.get("sales_cycle", "")

    regions_str = ", ".join(regions) if regions else "Global"
    services_str = ", ".join(services) if services else "Not specified"
    industries_str = ", ".join(industries) if industries else "Not specified"
    triggers_str = ", ".join(triggers) if triggers else "Not specified"
    roles_str = ", ".join(buyer_roles) if buyer_roles else "Not specified"
    exclusions_str = ", ".join(exclusions) if exclusions else "None specified"
    diff_str = ", ".join(differentiators) if differentiators else "Not specified"

    feedback_good = wizard_data.get("_feedback_good", []) or []
    feedback_bad = wizard_data.get("_feedback_bad", []) or []
    feedback_ctx = ""
    if feedback_good:
        feedback_ctx += "\n\nPOSITIVE PATTERNS — the user clicked GOOD FIT on these leads. Bias hunting_channels, scoring_guide.bonus_signals, and scoring_guide.must_have_signals toward what these have in common:\n"
        for l in feedback_good[:5]:
            feedback_ctx += f"- {l.get('org_name','?')} ({l.get('country','?')}) — why_fit: {(l.get('why_fit','') or '')[:140]} | gap: {(l.get('production_gap','') or '')[:120]}\n"
    if feedback_bad:
        feedback_ctx += "\n\nAVOID PATTERNS — the user clicked BAD FIT on these. Add their shared traits to anti_patterns (competitor_signals / wrong_audience_signals / service_mismatch_signals) and to scoring_guide.instant_reject:\n"
        for l in feedback_bad[:5]:
            feedback_ctx += f"- {l.get('org_name','?')} ({l.get('country','?')}) — why_fit: {(l.get('why_fit','') or '')[:140]} | gap: {(l.get('production_gap','') or '')[:120]}\n"
    if feedback_good or feedback_bad:
        feedback_ctx += "\nThe POSITIVE/AVOID lists above OVERRIDE generic guesses — when in doubt, mirror the positive set and reject anything resembling the avoid set.\n"

    return f"""A new client just hired you as their salesperson. Here's their business:

COMPANY: {company}
WHAT THEY DO: {desc}
SERVICES: {services_str}
HOW THEY DELIVER (hard constraint — prospects MUST need this delivery model): {how_it_works}
DIFFERENTIATORS: {diff_str}
PRICE TIER: {price_tier}
{f'SALES CYCLE: Typical deal closes in {sales_cycle} — timing expectations and urgency signals should reflect this.' if sales_cycle else ''}

CRITICAL CONSTRAINT — Read "HOW THEY DELIVER" carefully. The delivery model is a FILTER, not a suggestion. If the prospect needs a delivery model that {company} doesn't offer, they are WRONG regardless of industry fit.

WHO THEY SELL TO:
- Target clients: {target}
- Industries: {industries_str}
- Company sizes: {', '.join(wizard_data.get('icp_size', [])) or 'Not specified'}
- Decision makers: {roles_str}
- Buying triggers: {triggers_str}
- Target regions: {regions_str}

DO NOT TARGET: {exclusions_str}

{f'THEIR WEBSITE SAYS: {site_ctx[:800]}' if site_ctx else ''}

SEARCH INTELLIGENCE FROM THE USER:
- Where prospects show up online: {wizard_data.get('web_discovery_pages', 'Not specified')}
- Observable buying signals: {wizard_data.get('buying_signals', 'Not specified')}
- Disqualification signals: {wizard_data.get('disqualification_signals', 'Not specified')}
- Companies that look similar but are wrong: {wizard_data.get('lookalikes', 'Not specified')}
- Past/example clients (find SIMILAR companies): {wizard_data.get('past_clients', 'Not specified')}
- What buyers Google when they need this service: {wizard_data.get('buyer_search_terms', 'Not specified')}
- Job postings that signal a company needs this service: {wizard_data.get('hiring_signals', 'Not specified')}
- Known competitors: {wizard_data.get('competitors', 'Not specified')}
- Dream client (score 9-10 when matched): {wizard_data.get('dream_client', 'Not specified')}
- Core problem we solve: {wizard_data.get('pain_point', 'Not specified')}
- Case study / proof: {wizard_data.get('proof', 'Not specified')}
- What we do that competitors don't: {wizard_data.get('comp_diff', 'Not specified')}
{feedback_ctx}

YOUR TASK — Design a hunting strategy for this SPECIFIC business.

The golden rule: You search for what the CLIENT does, not what {company} does.

Think about it this way:
- {company} sells something → WHO buys that thing?
- Those buyers → WHAT are they doing online that reveals they need it?
- Those online activities → WHAT specific web pages would they create?
- Those web pages → WHAT would you type into Google to find those pages?

PRODUCE A JSON OBJECT WITH THESE EXACT FIELDS:

{{
  "client_profile": {{
    "who_is_the_buyer": "One paragraph. Paint a vivid picture of the ideal prospect based on the wizard answers above. What kind of company are they? What's their day-to-day? What problem do they have that {company} solves? Be as specific as possible — use concrete details from the user's answers, not generic descriptions.",

    "what_triggers_a_purchase": "List 5-8 specific, observable events that signal a company needs {company}'s service right now. Not vague things like 'growth' — specific things like 'just posted a job for a marketing coordinator' or 'announced a product launch date on their blog' or 'their website still has a WordPress theme from 2019'.",

    "service_boundaries": "Based on HOW {company} DELIVERS their service, what types of prospects are WRONG even if they're in the right industry? Be very specific. Derive mismatches directly from the delivery model and exclusions the user provided. List every mismatch clearly."
  }},

  "hunting_channels": [
    {{
      "channel_name": "Short descriptive name for this type of web page",
      "why_prospects_are_here": "One sentence explaining why this channel contains prospects",
      "what_to_look_for": "What specific pages/content you expect to find",
      "example_queries": ["2-3 example search queries for this channel — these are EXAMPLES for Stage 2, not final queries"],
      "expected_results": "What Google's top results SHOULD be when using these queries",
      "priority": "high / medium / low"
    }}
  ],

  "anti_patterns": {{
    "competitor_signals": ["List 5+ specific words/phrases that indicate a result is a COMPETITOR of {company}, not a client. For example, if {company} does web design, a competitor signal would be 'we build websites for...'"],
    "noise_signals": ["List 5+ specific words/phrases that indicate a result is a news article, blog post, directory, or aggregator — NOT a prospect's own site. e.g. 'Top 10...', 'Top 50...', 'Best ... companies', '...industry report'"],
    "wrong_audience_signals": ["List 5+ signals that a result is the wrong TYPE of company. e.g. for B2B, consumer retail sites are wrong. For premium services, budget/discount sites are wrong."],
    "service_mismatch_signals": ["List 5+ specific words/phrases on a prospect's website that reveal they need something {company} DOESN'T offer. Derive these directly from the delivery model constraints and exclusions provided by the user. These are INSTANT REJECT signals."]
  }},

  "scoring_guide": {{
    "score_10": "What a perfect prospect looks like — specific observable signals on their website",
    "score_7_8": "A good prospect — what signals are present",
    "score_4_6": "Maybe, needs more investigation — what's ambiguous",
    "score_1_3": "Probably not — what signals make you doubtful",
    "score_0": "Definitely not — instant rejection criteria",
    "must_have_signals": ["3-5 things that MUST be true for a score above 5"],
    "bonus_signals": ["3-5 things that boost a score from 7 to 9-10"],
    "instant_reject": ["5+ things that mean instant score 0, regardless of other signals. MUST include service delivery mismatch — if the prospect needs something {company} doesn't offer (wrong delivery model, wrong format, wrong location type), that's an instant 0."]
  }},

  "email_strategy": {{
    "tone": "{tone}",
    "opening_hook_strategy": "How to start the email — what to reference from the prospect's own site/content to show you did research",
    "value_proposition_angle": "How to frame {company}'s value in terms of the prospect's problem, not {company}'s features",
    "call_to_action": "What to ask for — meeting, demo, reply, etc.",
    "subject_line_patterns": ["3 subject line templates with [prospect_name] and [specific_detail] placeholders"],
    "things_to_never_say": ["5+ phrases to avoid in outreach — generic, salesy, or off-putting language"]
  }}
}}

CRITICAL: The hunting_channels array is the MOST IMPORTANT part. You need 6-12 channels.
Each channel must be a SPECIFIC type of web page where you'd find prospects.

GOOD channels: "Job postings for [specific role]", "Company /about pages in [industry/region]", "Forum threads asking for [service type]", "[Industry] directories with company profiles", "LinkedIn posts from [buyer role] about [pain point]"

BAD channels: "Google search results", "Industry websites", "Social media" (too vague), "Business directories" (finds listings, not prospects)

Think hard. Where does THIS specific type of buyer leave traces online?"""


def _dna_build_stage_2_prompt(wizard_data, strategy):
    """Build the Stage 2 prompt using wizard data + the Stage 1 strategy."""

    company = wizard_data.get("company_name", "the company")
    regions = wizard_data.get("regions", [])
    regions_str = ", ".join(regions) if regions else "Global"

    channels = strategy.get("hunting_channels", [])
    channels_text = ""
    for i, ch in enumerate(channels, 1):
        channels_text += f"""
CHANNEL {i}: {ch.get('channel_name', '?')}
  Why: {ch.get('why_prospects_are_here', '?')}
  Look for: {ch.get('what_to_look_for', '?')}
  Example queries: {json.dumps(ch.get('example_queries', []))}
  Expected results: {ch.get('expected_results', '?')}
  Priority: {ch.get('priority', 'medium')}
"""

    anti = strategy.get("anti_patterns", {})
    competitor_signals = ", ".join(anti.get("competitor_signals", []))
    noise_signals = ", ".join(anti.get("noise_signals", []))

    profile = strategy.get("client_profile", {})
    buyer_desc = profile.get("who_is_the_buyer", "Not specified")
    purchase_triggers = profile.get("what_triggers_a_purchase", "Not specified")

    cur_year = datetime.now(timezone.utc).year
    next_year = cur_year + 1

    # Build anti-pattern context
    wrong_audience = ", ".join(anti.get("wrong_audience_signals", []))
    mismatch = ", ".join(anti.get("service_mismatch_signals", []))

    # Get web discovery hints from wizard (new fields)
    discovery_pages = wizard_data.get("web_discovery_pages", "")
    buying_signals = wizard_data.get("buying_signals", "")
    disqualification_signals = wizard_data.get("disqualification_signals", "")
    lookalikes = wizard_data.get("lookalikes", "")

    discovery_ctx = ""
    if discovery_pages:
        discovery_ctx += f"\nTHE USER TOLD US WHERE PROSPECTS SHOW UP ONLINE:\n{discovery_pages}\n"
    if buying_signals:
        discovery_ctx += f"\nOBSERVABLE BUYING SIGNALS THE USER IDENTIFIED:\n{buying_signals}\n"
    if lookalikes:
        discovery_ctx += f"\nCOMPANIES THAT LOOK SIMILAR BUT ARE WRONG:\n{lookalikes}\n"

    fb_good = wizard_data.get("_feedback_good", []) or []
    fb_bad = wizard_data.get("_feedback_bad", []) or []
    if fb_good:
        discovery_ctx += "\nPOSITIVE PATTERNS (user clicked GOOD FIT) — your queries SHOULD be biased to surface more pages like these. Echo their industry/role/region/gap shape:\n"
        for l in fb_good[:5]:
            discovery_ctx += f"- {l.get('org_name','?')} ({l.get('country','?')}) — why_fit: {(l.get('why_fit','') or '')[:140]} | gap: {(l.get('production_gap','') or '')[:120]}\n"
    if fb_bad:
        discovery_ctx += "\nAVOID PATTERNS (user clicked BAD FIT) — DO NOT generate queries that would surface pages resembling these. If a query you drafted would likely return one of these, KILL IT:\n"
        for l in fb_bad[:5]:
            discovery_ctx += f"- {l.get('org_name','?')} ({l.get('country','?')}) — why_fit: {(l.get('why_fit','') or '')[:140]} | gap: {(l.get('production_gap','') or '')[:120]}\n"

    return f"""You're generating search queries to find potential clients for {company}.

THE BUYER: {buyer_desc}

PURCHASE TRIGGERS (observable events that signal need): {purchase_triggers}

TARGET REGIONS: {regions_str}

ANTI-PATTERNS:
- Competitor signals (NEVER find these): {competitor_signals}
- Noise signals (NEVER find these): {noise_signals}
- Wrong audience: {wrong_audience}
- Service mismatch (instant reject): {mismatch}
{discovery_ctx}
YOUR HUNTING CHANNELS:
{channels_text}

═══ OUTPUT FORMAT ═══

Return a JSON object with your final queries AFTER self-filtering:

{{
  "queries": [
    {{
      "q": "the search query text (3-8 words, plain language, NO operators, NO quotes)",
      "channel": "which hunting channel this targets",
      "intent": "what TYPE of page this should find (e.g. 'company event page', 'association about page', 'corporate job listing')",
      "expects": "what the #1 result should be (e.g. 'a logistics company in Hamburg with an events section')"
    }}
  ]
}}

═══ QUERY ENGINEERING RULES ═══

RULE 1 — SEARXNG COMPATIBILITY (non-negotiable):
  - NEVER use operators: inurl:, intitle:, site:, filetype:, OR, AND, -keyword
  - NEVER use quoted phrases: "virtual summit", "webinar series" — write without quotes
  - NEVER use parentheses or special syntax
  - ONLY plain natural language words separated by spaces
  - The search engine is SearXNG (meta-search), NOT Google. Keep queries simple.

RULE 2 — PROSPECT PAGES, NOT NEWS:
  Your query must find a PROSPECT'S OWN WEBSITE. Not a news article about them. Not a blog reviewing them. Not a directory listing them.

  The test: "If I type this into Google, what is the #1 result?"
  - If it's a company's own page → GOOD
  - If it's a news article, blog, directory, or platform → KILL IT

  Pattern that works: [what the company IS/DOES] + [location or context word]
  Pattern that fails: [what the company SAYS/ANNOUNCES] + [editorial framing]

  GOOD QUERIES (return company pages):
    "architecture firm Milan projects" → an architecture firm's portfolio page
    "healthcare company Berlin about us" → a company's about page
    "manufacturing company expansion Hamburg" → a company's news/about page
    "fintech startup Amsterdam team" → a startup's about page
    "logistics company Rotterdam hiring" → a company's careers page
    "SaaS company London case studies" → a company's proof page

  BAD QUERIES (return garbage):
    "best [service] platforms {cur_year}" → returns software review sites
    "how to choose a [service] provider" → returns blog posts
    "top [industry] companies" → returns competitor directories
    "announcing new partnership" → returns press releases
    "earnings call Q3 {cur_year}" → returns financial news
    "[service] tips and tricks" → returns content marketing
    "industry report {cur_year}" → returns analyst reports, not prospects

RULE 3 — GEOGRAPHIC SPECIFICITY:
  ~40% of queries must include a SPECIFIC city or country from: {regions_str}
  Use real cities: London, Berlin, Munich, Milan, Rome, Madrid, Barcelona, Amsterdam, Rotterdam, Paris, Lyon, Stockholm, Copenhagen, Oslo, Helsinki, Dublin, Zurich, Vienna, Brussels, Warsaw, Prague, Lisbon, Budapest
  Rotate through different cities. Never use just "Europe" or "Global" — too vague.

RULE 4 — TIME FRESHNESS:
  ~30% of queries must include "{cur_year}" or "{next_year}" to find current content.
  NEVER use 2024 or earlier — stale results waste time.

RULE 5 — THE FORBIDDEN LIST:
  KILL any query containing:
  - "how to" / "what is" / "best" / "top 10" / "top 50" / "guide" / "tips" / "tutorial"
  - "trends" / "report" / "analysis" / "forecast" / "study" / "research"
  - "news" / "blog" / "article" / "review" / "podcast" / "newsletter"
  - "announces" / "launches" / "unveils" / "reveals" / "introduces"
  - "investor" / "earnings" / "shareholder" / "dividend" / "SEC" / "quarterly" / "annual report"
  - "services" / "solutions" / "provider" / "agency" / "consultancy" (finds competitors)
  - "free" / "cheap" / "discount" / "coupon" (finds consumer content)
  - "buy" / "shop" / "store" / "cart" / "order" (finds e-commerce)
  - "{company}" (the client's own name — never search for yourself)
  - Any competitor signal words listed above

RULE 6 — DIVERSITY:
  No two queries should return the same results. Vary across:
  - Different cities/countries
  - Different hunting channels
  - Different angles (event pages vs about pages vs job pages vs project pages)
  - Different sub-industries within the target market
  - Different company types within the ICP

RULE 7 — SPECIFICITY:
  Every query needs at least 3 meaningful words (not counting city/year).
  "companies Berlin" is too vague. "engineering company Berlin projects" is specific.

═══ SELF-CRITIQUE BEFORE RETURNING ═══

Before finalizing your list, run each query through these kill checks:

KILL CHECKLIST (remove the query if ANY are true):
  ☐ Contains an operator (inurl:, site:, intitle:, filetype:)
  ☐ Contains quoted phrases
  ☐ The likely #1 result is a news article, blog, or directory
  ☐ Contains a forbidden word from Rule 5
  ☐ Contains a competitor signal word
  ☐ Is too vague (fewer than 3 meaningful words)
  ☐ Would return the same results as another query already in the list
  ☐ Contains a year before {cur_year}
  ☐ The expected top result is a platform vendor, not a prospect
  ☐ Would likely surface a page resembling the AVOID PATTERNS list (if any)
  ☐ Misses the shape of the POSITIVE PATTERNS list when one is present (your final set should over-index toward those)

DIVERSITY CHECKLIST (ensure your final set has):
  ☐ At least 5 different cities/countries represented
  ☐ Every high-priority hunting channel has 10+ queries
  ☐ Every medium-priority channel has 5+ queries
  ☐ No more than 3 queries with the same first 2 words
  ☐ Mix of: company discovery, event pages, job signals, about pages, project pages

DELIVER 60-80 FINAL QUERIES after self-filtering. Quality over quantity — 60 precise queries beat 80 sloppy ones."""


def _dna_build_business_context(wizard_data, strategy):
    """Build the business_context string from strategy data.
    This is what the scoring AI reads to understand the business."""

    company = wizard_data.get("company_name", "the company")
    desc = wizard_data.get("business_description", "")
    profile = strategy.get("client_profile", {})
    anti = strategy.get("anti_patterns", {})
    scoring = strategy.get("scoring_guide", {})

    lines = [
        f"COMPANY: {company}",
        f"WHAT THEY DO: {desc}",
        "",
        f"IDEAL PROSPECT: {profile.get('who_is_the_buyer', 'Not specified')}",
        "",
        f"SERVICE BOUNDARIES (hard filter): {profile.get('service_boundaries', 'Not specified')}",
        "",
        f"PURCHASE TRIGGERS: {profile.get('what_triggers_a_purchase', 'Not specified')}",
        "",
        "REJECT IMMEDIATELY IF:",
    ]
    for sig in anti.get("service_mismatch_signals", [])[:5]:
        lines.append(f"  - SERVICE MISMATCH (instant 0): {sig}")
    for sig in anti.get("competitor_signals", [])[:5]:
        lines.append(f"  - Competitor signal: {sig}")
    for sig in anti.get("noise_signals", [])[:5]:
        lines.append(f"  - Noise signal: {sig}")
    for sig in anti.get("wrong_audience_signals", [])[:5]:
        lines.append(f"  - Wrong audience: {sig}")

    lines.append("")
    lines.append(f"PERFECT PROSPECT (10/10): {scoring.get('score_10', 'N/A')}")
    lines.append(f"GOOD PROSPECT (7-8): {scoring.get('score_7_8', 'N/A')}")
    lines.append(f"MAYBE (4-6): {scoring.get('score_4_6', 'N/A')}")
    lines.append(f"PROBABLY NOT (1-3): {scoring.get('score_1_3', 'N/A')}")
    lines.append(f"INSTANT REJECT (0): {scoring.get('score_0', 'N/A')}")

    return "\n".join(lines)


def _dna_build_scoring_rules(scoring):
    """Build the scoring_rules string from strategy scoring guide."""

    lines = [
        "SCORING RULES (0-10 scale):",
        "",
        "MUST-HAVE signals (need ALL for score > 5):",
    ]
    for sig in scoring.get("must_have_signals", []):
        lines.append(f"  - {sig}")

    lines.append("")
    lines.append("BONUS signals (+1 each, can push to 9-10):")
    for sig in scoring.get("bonus_signals", []):
        lines.append(f"  + {sig}")

    lines.append("")
    lines.append("INSTANT REJECT (score = 0 regardless):")
    for sig in scoring.get("instant_reject", []):
        lines.append(f"  x {sig}")

    lines.append("")
    lines.append(f"SCORE 10: {scoring.get('score_10', '')}")
    lines.append(f"SCORE 7-8: {scoring.get('score_7_8', '')}")
    lines.append(f"SCORE 4-6: {scoring.get('score_4_6', '')}")
    lines.append(f"SCORE 1-3: {scoring.get('score_1_3', '')}")
    lines.append(f"SCORE 0: {scoring.get('score_0', '')}")

    return "\n".join(lines)


def _dna_build_email_rules(email_strat, wizard_data):
    """Build the email_rules string from strategy email strategy."""

    company = wizard_data.get("company_name", "the company")
    tone = email_strat.get("tone", "friendly")

    lines = [
        f"EMAIL RULES for {company}:",
        f"Tone: {tone}",
        "",
        f"OPENING: {email_strat.get('opening_hook_strategy', 'Reference something specific from their website.')}",
        "",
        f"VALUE PROP: {email_strat.get('value_proposition_angle', 'Frame in terms of their problem, not our features.')}",
        "",
        f"CTA: {email_strat.get('call_to_action', 'Ask for a short meeting.')}",
        "",
        "SUBJECT LINE TEMPLATES:",
    ]
    for pattern in email_strat.get("subject_line_patterns", []):
        lines.append(f"  - {pattern}")

    lines.append("")
    lines.append("NEVER SAY:")
    for phrase in email_strat.get("things_to_never_say", []):
        lines.append(f"  x {phrase}")

    lines.append("")
    lines.append("FORMAT: Max 6 sentences. No corporate jargon. No 'I hope this email finds you well.'")
    lines.append("Every email must reference something SPECIFIC from the prospect's website or content.")

    return "\n".join(lines)


def _dna_fallback(wizard_data, version):
    """Minimal fallback if Stage 1 fails entirely."""
    from datetime import datetime, timezone as _tz

    company = wizard_data.get("company_name", "the company")
    desc = wizard_data.get("business_description", "")
    services = wizard_data.get("services", [])
    target = wizard_data.get("target_clients", "")
    tone = wizard_data.get("outreach_tone", wizard_data.get("email_tone", "friendly"))

    return {
        "business_context": f"{company}: {desc}\nServices: {', '.join(services)}\nTarget: {target}",
        "search_queries": [],
        "scoring_rules": "Score 7+ to accept. Reject competitors, job boards, news articles.",
        "email_rules": f"Sender: {company}. Tone: {tone}. Max 6 sentences.",
        "hunting_strategy": None,
        "version": version,
        "dna_version": "v2-fallback",
        "generated_at": datetime.now(_tz.utc).isoformat(),
    }


def generate_agent_dna(wizard_data, feedback_good=None, feedback_bad=None, existing_dna=None):
    """Generate Agent DNA v2 — two-stage strategy → queries pipeline.

    Stage 1: Build a hunting strategy (who, where, how to score, how to email)
    Stage 2: Generate 60-80 search queries grounded in the strategy

    Returns: business_context, search_queries, scoring_rules, email_rules,
             hunting_strategy, query_count, channels_used, dna_version
    """
    from datetime import datetime, timezone as _tz

    version = (existing_dna.get("version", 0) + 1) if existing_dna else 1

    # Inject feedback into wizard_data for Stage 1
    wd = dict(wizard_data)
    if feedback_good:
        wd["_feedback_good"] = feedback_good
    if feedback_bad:
        wd["_feedback_bad"] = feedback_bad

    # ── STAGE 1: Hunting Strategy ──────────────────────────────────────────────
    emit_log("Stage 1: Building hunting strategy...", "info")

    try:
        _s1_prompt = _dna_build_stage_1_prompt(wd)
        _s1_msgs = [
            {"role": "system", "content": _DNA_STAGE_1_SYSTEM},
            {"role": "user", "content": _s1_prompt}
        ]
        # Try up to 2 times
        _s1_json = None
        for _s1_attempt in range(2):
            stage_1_raw = _ai_call(
                messages=_s1_msgs,
                temperature=0.4 + (_s1_attempt * 0.1),
                max_tokens=6000,
            )
            _s1_json = extract_json(stage_1_raw)
            if _s1_json:
                break
            emit_log(f"Stage 1 attempt {_s1_attempt+1}: no JSON (raw length: {len(stage_1_raw) if stage_1_raw else 0}), retrying...", "warn")
        if not _s1_json:
            raise ValueError(f"No JSON in Stage 1 response after 2 attempts")
        # Parse with repair fallback
        try:
            strategy = json.loads(_s1_json)
        except json.JSONDecodeError as _jde:
            emit_log(f"Stage 1 JSON malformed, attempting AI repair...", "warn")
            _repair_raw = _ai_call(
                messages=[
                    {"role": "system", "content": "Fix this broken JSON. Return ONLY valid JSON. Escape all quotes inside string values."},
                    {"role": "user", "content": _s1_json[:8000]}
                ],
                temperature=0.1, max_tokens=6000,
            )
            _repair_js = extract_json(_repair_raw)
            if _repair_js:
                strategy = json.loads(_repair_js)
            else:
                raise ValueError(f"Stage 1 JSON repair failed: {_jde}")

        if not strategy.get("hunting_channels"):
            raise ValueError("Strategy missing hunting_channels")

        emit_log(f"Strategy built: {len(strategy.get('hunting_channels', []))} hunting channels", "info")

    except Exception as e:
        emit_log(f"Stage 1 failed: {e}", "error")
        return _dna_fallback(wizard_data, version)

    # ── STAGE 2: Query Generation ──────────────────────────────────────────────
    emit_log("Stage 2: Generating search queries...", "info")

    try:
        _s2_prompt = _dna_build_stage_2_prompt(wd, strategy)
        _s2_msgs = [
            {"role": "system", "content": _DNA_STAGE_2_SYSTEM},
            {"role": "user", "content": _s2_prompt}
        ]
        # Try up to 2 times — AI sometimes returns non-JSON on first attempt
        _s2_json = None
        for _s2_attempt in range(2):
            stage_2_raw = _ai_call(
                messages=_s2_msgs,
                temperature=0.3 + (_s2_attempt * 0.1),
                max_tokens=12000,
            )
            _s2_json = extract_json(stage_2_raw)
            if _s2_json:
                break
            emit_log(f"Stage 2 attempt {_s2_attempt+1}: no JSON found (raw length: {len(stage_2_raw) if stage_2_raw else 0}), retrying...", "warn")
        if not _s2_json:
            # AI self-repair fallback (same as Stage 1)
            emit_log("Stage 2 JSON failed, attempting AI repair...", "warn")
            _repair_raw = _ai_call(
                messages=[
                    {"role": "system", "content": "Fix this broken JSON. Return ONLY valid JSON. Escape all quotes inside string values."},
                    {"role": "user", "content": (stage_2_raw or "")[:8000]}
                ],
                temperature=0.1, max_tokens=8000,
            )
            _s2_json = extract_json(_repair_raw)
            if not _s2_json:
                raise ValueError(f"No JSON in Stage 2 response after 2 attempts + repair (raw length: {len(stage_2_raw) if stage_2_raw else 0})")
        # Parse with repair fallback
        try:
            query_data = json.loads(_s2_json)
        except json.JSONDecodeError as _jde:
            emit_log("Stage 2 JSON malformed, attempting AI repair...", "warn")
            _repair_raw = _ai_call(
                messages=[
                    {"role": "system", "content": "Fix this broken JSON. Return ONLY valid JSON. Escape all quotes inside string values."},
                    {"role": "user", "content": _s2_json[:8000]}
                ],
                temperature=0.1, max_tokens=8000,
            )
            _repair_js = extract_json(_repair_raw)
            if _repair_js:
                query_data = json.loads(_repair_js)
            else:
                raise ValueError(f"Stage 2 JSON repair failed: {_jde}")
        raw_queries = query_data.get("queries", [])
        # If queries is empty but response has a list at top level, use that
        if not raw_queries and isinstance(query_data, list):
            raw_queries = query_data

        emit_log(f"Generated {len(raw_queries)} raw queries", "info")

    except Exception as e:
        emit_log(f"Stage 2 failed: {e}", "error")
        # Use the example queries from Stage 1 channels as fallback
        raw_queries = []
        for ch in strategy.get("hunting_channels", []):
            for eq in ch.get("example_queries", []):
                raw_queries.append({"q": eq, "channel": ch.get("channel_name", "")})

    # ── POST-PROCESSING: Filter bad queries ────────────────────────────────────
    company = wizard_data.get("company_name", "").lower()
    services = wizard_data.get("services", [])

    # Build blocklist of words that would find competitors. Defensively
    # treat each item as a string — wizard_data and AI-generated
    # `competitor_signals` can both deliver None / int / list-of-list
    # entries that crash `.lower().split()`.
    service_words = set()
    for s in (services or []):
        for w in (str(s) if s else "").lower().split():
            if len(w) > 3:
                service_words.add(w)

    # Add anti-pattern words from strategy
    anti = strategy.get("anti_patterns", {}) or {}
    for sig in (anti.get("competitor_signals") or []):
        for w in (str(sig) if sig else "").lower().split():
            if len(w) > 4:
                service_words.add(w)

    _BAD_STARTS = [
        "how to ", "what is ", "best ", "top 10 ", "top 50 ",
        "guide to ", "tips for ", "why ", "ways to ",
    ]
    _NOISE_WORDS = {
        "blog", "article", "news", "review", "reviews", "trends",
        "guide", "tutorial", "report", "analysis", "podcast",
    }

    # Financial/investor terms that return investor relations pages, not prospects
    _INVESTOR_WORDS = {
        "investor", "investors", "earnings", "shareholder", "shareholders",
        "quarterly", "dividend", "ipo", "sec", "filing", "filings",
        "10-k", "10-q", "annual report", "fiscal",
    }

    import datetime as _dt_q
    _current_year = _dt_q.datetime.now().year

    # Extended banned patterns (catch-all for garbage queries)
    _BANNED_PATTERNS = [
        r'\binurl:', r'\bintitle:', r'\bsite:', r'\bfiletype:',  # Search operators
        r'"[^"]{3,}"',  # Quoted phrases
        r'\bOR\b', r'\bAND\b', r'\bNOT\b',  # Boolean operators
    ]
    _CONSUMER_WORDS = {
        "buy", "shop", "store", "cart", "order", "coupon", "discount",
        "free", "cheap", "deal", "sale", "promo",
    }
    _EDITORIAL_WORDS = {
        "announces", "launches", "unveils", "reveals", "introduces",
        "announces", "awarded", "wins",
    }

    clean_queries = []
    seen_texts = set()
    seen_word_sets = []  # For near-duplicate detection
    _rejected_schema = 0
    _rejected_content = 0
    _rejected_dedup = 0

    for item in raw_queries:
        # ── SCHEMA ENFORCEMENT ──
        if isinstance(item, dict):
            q = item.get("q", "")
            if not q or not isinstance(q, str) or not q.strip():
                _rejected_schema += 1
                continue
        elif isinstance(item, str):
            q = item
        else:
            continue

        if not isinstance(q, str) or len(q.strip()) < 5:
            continue

        q = q.strip()

        # ── Check for banned patterns BEFORE destructive normalisation ──
        # The `r'"[^"]{3,}"'` regex needs to see the original quotes to
        # detect quoted-phrase queries (which SearXNG doesn't honour).
        # Pre-a65 we stripped quotes first, so this rule was a dead
        # check — quoted phrases sailed through.
        _has_banned = False
        for bp in _BANNED_PATTERNS:
            if re.search(bp, q, re.IGNORECASE):
                _has_banned = True
                break
        if _has_banned:
            _rejected_content += 1
            continue

        # ── Now destructively normalise: strip operators, quotes,
        # whitespace ──
        q = re.sub(r'\b(inurl|intitle|site|filetype):\S+\s*', '', q).strip()
        q = q.replace('"', '').strip()
        q = re.sub(r'\s+', ' ', q).strip()

        if len(q) < 5:
            continue

        # ── Fix stale year references ──
        q = re.sub(r'\b2024\b', str(_current_year), q)
        q = re.sub(r'\b202[0-3]\b', str(_current_year), q)  # Any year before 2024

        ql = q.lower()

        # ── Exact dedup ──
        if ql in seen_texts:
            _rejected_dedup += 1
            continue
        seen_texts.add(ql)

        if company and company in ql:
            _rejected_content += 1
            continue

        if any(ql.startswith(b) for b in _BAD_STARTS):
            _rejected_content += 1
            continue

        q_words = set(ql.split())
        if q_words & _NOISE_WORDS:
            _rejected_content += 1
            continue

        if q_words & _INVESTOR_WORDS:
            _rejected_content += 1
            continue

        if q_words & _CONSUMER_WORDS:
            _rejected_content += 1
            continue

        if q_words & _EDITORIAL_WORDS:
            _rejected_content += 1
            continue

        if service_words:
            overlap = q_words & service_words
            if len(overlap) >= 2 and len(q_words) > 0 and len(overlap) / len(q_words) > 0.5:
                _rejected_content += 1
                continue

        # ── Near-duplicate detection (queries with >70% word overlap) ──
        _is_near_dup = False
        for existing_words in seen_word_sets:
            if not existing_words or not q_words:
                continue
            _overlap = len(q_words & existing_words)
            _max_len = max(len(q_words), len(existing_words))
            if _max_len > 0 and _overlap / _max_len > 0.7:
                _is_near_dup = True
                break
        if _is_near_dup:
            _rejected_dedup += 1
            continue

        seen_word_sets.append(q_words)
        clean_queries.append(q)

    emit_log(f"Filtered to {len(clean_queries)} clean queries (from {len(raw_queries)}: {_rejected_schema} no schema, {_rejected_content} bad content, {_rejected_dedup} duplicates)", "info")

    # ── ASSEMBLE DNA ───────────────────────────────────────────────────────────
    profile = strategy.get("client_profile", {})
    scoring = strategy.get("scoring_guide", {})
    email_strat = strategy.get("email_strategy", {})

    business_context = _dna_build_business_context(wizard_data, strategy)
    scoring_rules = _dna_build_scoring_rules(scoring)
    email_rules = _dna_build_email_rules(email_strat, wizard_data)

    _good_count = len(feedback_good) if feedback_good else 0
    _bad_count = len(feedback_bad) if feedback_bad else 0
    _adapted = bool(_good_count or _bad_count)

    dna = {
        # ── Original fields (backward compatible) ──
        "business_context": business_context,
        "search_queries": clean_queries,
        "scoring_rules": scoring_rules,
        "email_rules": email_rules,

        # ── New v2 fields ──
        "hunting_strategy": strategy,
        "query_count": len(clean_queries),
        "channels_used": list({
            item.get("channel", "general") if isinstance(item, dict) else "general"
            for item in raw_queries
        }),

        # ── Adaptation provenance (for huntova-doctor probes) ──
        "_adapted_from_feedback": _adapted,
        "_feedback_good_count": _good_count,
        "_feedback_bad_count": _bad_count,

        # ── Metadata ──
        "version": version,
        "dna_version": "v2",
        "generated_at": datetime.now(_tz.utc).isoformat(),
    }

    return dna


def _generate_training_dossier(wiz, brain):
    """Generate a detailed per-account training dossier from wizard + brain data."""
    from datetime import datetime, timezone

    arch = brain.get("archetype", "other")
    services = brain.get("services_clean", [])
    industries = brain.get("preferred_industries", [])
    roles = brain.get("buyer_roles_clean", [])
    triggers = brain.get("triggers_clean", [])
    exclusions = brain.get("exclusions_clean", [])
    examples_good = wiz.get("example_good_clients", [])
    examples_bad = wiz.get("example_bad_clients", [])
    desc = brain.get("offer_summary", "") or wiz.get("business_description", "")
    sizes = brain.get("ideal_company_size", [])
    regions = brain.get("preferred_regions", wiz.get("regions", []))

    # ── Deterministic: business_identity ──
    _ENGAGEMENT_MAP = {
        "consultant": "retained advisory / project-based",
        "service_agency": "retainer / project-based",
        "software": "subscription / license",
        "recruiter": "placement fee / retainer",
        "professional_firm": "hourly / retainer / project",
        "manufacturer": "wholesale / contract",
        "distributor": "wholesale / resale",
        "local_b2b": "recurring service contract",
        "media_publisher": "advertising / sponsorship / subscription",
    }
    _BUDGET_MAP = {
        "consultant": "mid-premium",
        "service_agency": "mid-range",
        "software": "subscription-based",
        "recruiter": "placement-fee-based",
        "professional_firm": "hourly/premium",
        "manufacturer": "volume-based",
        "distributor": "margin-based",
        "local_b2b": "budget-mid",
    }

    business_identity = {
        "company_name": wiz.get("company_name", ""),
        "what_they_sell": desc[:200] if desc else ", ".join(services) if services else "B2B services",
        "how_they_sell": _ENGAGEMENT_MAP.get(arch, "project-based"),
        "b2b_b2c": "B2B",
        "typical_client_type": ", ".join(sizes[:2]) + " " + ", ".join(industries[:2]) if industries else "various companies",
        "contract_style": _ENGAGEMENT_MAP.get(arch, "varies"),
        "budget_tier": _BUDGET_MAP.get(arch, "varies"),
    }

    # ── Deterministic: offer_model ──
    offer_model = {
        "primary_services": services[:5],
        "secondary_offers": [],
        "delivery_model": wiz.get("delivery_method", "mixed"),
        "engagement_type": _ENGAGEMENT_MAP.get(arch, "project-based"),
    }

    # ── Deterministic: ideal_customer_profile ──
    ideal_customer_profile = {
        "company_types": sizes if sizes else ["mid-market"],
        "size_bands": sizes if sizes else [],
        "industries": industries,
        "geographies": [r for r in regions if r and isinstance(r, str)],
        "maturity_stage": [t for t in triggers if t.lower() in ("growth", "scaling", "expansion")] or ["growth"],
        "buyer_roles": roles,
    }

    # ── Deterministic: anti_icp ──
    anti_icp = {
        "bad_company_types": [],
        "bad_sizes": [],
        "bad_industries": wiz.get("excluded_industries", []),
        "bad_geographies": wiz.get("excluded_regions", []),
        "bad_roles": [],
        "avoid_internal_heavy": wiz.get("reject_strong_inhouse", True),
        "avoid_unreachable": wiz.get("reject_no_contact", True),
        "avoid_competitors": [],
    }
    if wiz.get("reject_enterprise", True):
        anti_icp["bad_company_types"].append("giant enterprise / Fortune 500")
        anti_icp["bad_sizes"].append("Fortune 500")
    if wiz.get("reject_government", True):
        anti_icp["bad_company_types"].append("government / public institution")
    for exc in exclusions:
        if exc.lower() not in [x.lower() for x in anti_icp["bad_company_types"]]:
            anti_icp["bad_company_types"].append(exc)
    # Learn from bad examples
    for bad in examples_bad:
        reason = (bad.get("reason") or "").lower()
        if "competitor" in reason or "agency" in reason:
            anti_icp["avoid_competitors"].append(bad.get("name", ""))
        if "too big" in reason or "large" in reason or "enterprise" in reason:
            anti_icp["bad_sizes"].append(bad.get("name", ""))

    # ── Deterministic: buyability_spec ──
    _BUYABILITY_TEMPLATES = {
        "consultant": {
            "realistic_buyer": "Companies with a recognized need for external advisory and no large internal strategy team",
            "too_hard": "Fortune 500 procurement, government tenders, companies with 10+ internal consultants",
        },
        "recruiter": {
            "realistic_buyer": "Growing companies actively hiring, especially those without a full internal recruiting team",
            "too_hard": "Companies with large internal talent acquisition teams, companies not hiring",
        },
        "software": {
            "realistic_buyer": "Companies evaluating or replacing a tool in our category, mid-market or growing",
            "too_hard": "Companies locked into enterprise agreements, companies with custom-built internal tools",
        },
    }
    _buy_template = _BUYABILITY_TEMPLATES.get(arch, {
        "realistic_buyer": "Companies with a clear need for our services and reasonable size for outbound",
        "too_hard": "Giant enterprises, government, companies with large internal capability in our area",
    })

    buyability_spec = {
        "realistic_buyer": _buy_template["realistic_buyer"],
        "too_hard": _buy_template["too_hard"],
        "budget_clues": [t for t in triggers if any(kw in t.lower() for kw in ("fund", "budget", "invest", "growth", "expan"))] or ["growth signals", "expansion"],
        "urgency_clues": [t for t in triggers if any(kw in t.lower() for kw in ("deadline", "compliance", "regulat", "launch", "migrat"))] or ["change/compliance triggers"],
        "external_need_signals": ["no internal team for this", "first-time buyer", "scaling beyond current capacity"],
        "internal_team_caution": "Reject if large dedicated internal team" if wiz.get("reject_strong_inhouse", True) else "Accept even with internal team",
    }

    # ── Deterministic: reachability_spec ──
    reachability_spec = {
        "minimum_contact": "named person OR LinkedIn OR departmental email",
        "preferred_contact": "named decision-maker with direct email",
        "fallback_contact": "company LinkedIn + contact page URL",
        "unreachable_patterns": ["only generic info@", "no web presence", "no named contacts"],
    }

    # ── Deterministic: trigger_library ──
    trigger_library = {
        "growth": [t for t in triggers if any(kw in t.lower() for kw in ("growth", "hiring", "expand", "scal"))] or ["growth", "expansion"],
        "change": [t for t in triggers if any(kw in t.lower() for kw in ("transform", "restructur", "migrat", "switch"))] or ["transformation"],
        "pain": [t for t in triggers if any(kw in t.lower() for kw in ("gap", "problem", "compliance", "quality", "regulat"))] or ["visible gap"],
        "procurement": ["vendor review", "budget cycle", "RFP"],
        "recurring": [t for t in triggers if any(kw in t.lower() for kw in ("recurr", "ongoing", "monthly", "quarter"))] or ["ongoing need"],
    }

    # ── Deterministic: search_strategy_spec ──
    _SEARCH_STRATEGY = {
        "consultant": {
            "best_patterns": ["industry + need + advisory + region", "buyer_role + service + evaluation"],
            "high_signal_pages": ["company about page", "team/leadership page", "service page", "case study page"],
            "blocked_result_classes": ["definition/explainer", "academic journal", "news article", "wiki"],
        },
        "recruiter": {
            "best_patterns": ["company + hiring signal + role + region", "growing company + team expansion"],
            "high_signal_pages": ["careers page", "team page", "company about page", "press/news page with hiring"],
            "blocked_result_classes": ["individual job listing", "job board", "career advice", "salary comparison"],
        },
        "software": {
            "best_patterns": ["buyer_role + tool category + evaluation + region", "company + replacing/migrating tool"],
            "high_signal_pages": ["company product/tech stack page", "team page", "about page"],
            "blocked_result_classes": ["product review", "comparison article", "editorial", "app store listing"],
        },
    }
    _ss = _SEARCH_STRATEGY.get(arch, {
        "best_patterns": ["service + industry + region", "buyer_role + need + company_type"],
        "high_signal_pages": ["company about page", "team page", "service page"],
        "blocked_result_classes": ["wiki", "forum", "news article", "definition page"],
    })
    search_strategy_spec = {
        "best_patterns": _ss["best_patterns"],
        "bad_patterns": ["generic industry term alone", "definition queries", "broad news searches"],
        "high_signal_pages": _ss["high_signal_pages"],
        "low_signal_pages": ["blog post", "news article", "definition", "directory listing", "forum thread"],
        "blocked_result_classes": _ss["blocked_result_classes"],
    }

    # ── Example boundaries ──
    example_boundaries = {
        "good_clients": [{"name": g.get("name",""), "reason": g.get("reason","")} for g in examples_good if g.get("name")],
        "bad_clients": [{"name": b.get("name",""), "reason": b.get("reason","")} for b in examples_bad if b.get("name")],
    }

    # ── Outreach posture ──
    outreach_posture = {
        "tone": wiz.get("outreach_tone", "consultative"),
        "lead_angle": f"how we can help with {services[0]}" if services else "how we can add value",
        "avoid_saying": ["innovative solutions", "leverage", "cutting-edge", "we are the best"],
    }

    # ── Confidence ──
    _conf = brain.get("profile_confidence", 50)
    weak_spots = []
    if not examples_good: weak_spots.append("no example good clients provided")
    if not examples_bad: weak_spots.append("no example bad clients provided")
    if len(services) < 2: weak_spots.append("only one service listed")
    if not triggers: weak_spots.append("no buying triggers defined")

    # ── Acceptance spec (user rules override archetype defaults) ──
    _ARCH_THRESHOLDS = {
        "consultant": {"fit": 7, "buy": 4, "reach": 3},
        "recruiter": {"fit": 6, "buy": 4, "reach": 3},
        "software": {"fit": 7, "buy": 4, "reach": 3},
        "professional_firm": {"fit": 7, "buy": 5, "reach": 4},
        "manufacturer": {"fit": 6, "buy": 4, "reach": 3},
        "distributor": {"fit": 6, "buy": 4, "reach": 3},
        "service_agency": {"fit": 7, "buy": 4, "reach": 3},
    }
    _at = _ARCH_THRESHOLDS.get(arch, {"fit": 7, "buy": 4, "reach": 3})

    hard_rejects = []
    if wiz.get("reject_enterprise", True): hard_rejects.append("giant enterprise / Fortune 500")
    if wiz.get("reject_government", True): hard_rejects.append("government / public institution")
    if wiz.get("reject_strong_inhouse", True): hard_rejects.append("strong internal team without overflow need")
    if wiz.get("reject_no_contact", True): hard_rejects.append("no actionable contact path")
    # Add user exclusions as hard rejects
    for exc in exclusions:
        if exc not in hard_rejects:
            hard_rejects.append(exc)

    acceptance_spec = {
        "fit_threshold": _at["fit"],
        "buyability_threshold": _at["buy"],
        "reachability_threshold": _at["reach"],
        "hard_rejects": hard_rejects,
        "soft_penalties": ["no named contact (only org LinkedIn)", "very small company", "no visible trigger signal"],
    }

    dossier = {
        "training_dossier_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": "wizard_save",
        "archetype": arch,

        "business_identity": business_identity,
        "offer_model": offer_model,
        "ideal_customer_profile": ideal_customer_profile,
        "anti_icp": anti_icp,
        "buyability_spec": buyability_spec,
        "reachability_spec": reachability_spec,
        "trigger_library": trigger_library,
        "search_strategy_spec": search_strategy_spec,
        "example_boundaries": example_boundaries,
        "outreach_posture": outreach_posture,
        "acceptance_spec": acceptance_spec,

        "confidence": {
            "overall": _conf,
            "weak_spots": weak_spots,
            "missing_info": [],
            "recommended_questions": [],
        },
    }

    return dossier


def _generate_brain_queries(brain, countries, max_queries=50):
    """Generate queries using archetype-specific grammar from the hunt brain."""
    arch = brain.get("archetype", "other")
    services = brain.get("services_clean", [])
    industries = brain.get("preferred_industries", [])
    roles = brain.get("buyer_roles_clean", [])
    triggers = brain.get("triggers_clean", [])
    sizes = brain.get("ideal_company_size", [])
    goods = brain.get("example_good_clients", [])

    country_list = countries if countries else [""]
    queries = []

    # ── Archetype-specific query patterns ──
    # KEY PRINCIPLE: Search for PROSPECTS' own content (their websites, events,
    # job posts, industry pages), NOT for "hiring agency" content which barely
    # exists on the web. Search engines need queries that match real web pages.
    if arch == "consultant":
        problems = triggers if triggers else ["transformation", "restructuring", "optimization"]
        for ind in (industries[:3] or ["mid-market company"]):
            for prob in problems[:3]:
                for c in country_list:
                    queries.append(f"{ind} {prob} {c}")
            for svc in services[:2]:
                for c in country_list:
                    queries.append(f"{ind} {svc} {c}")
                    queries.append(f"{svc} consulting firms {c}")
        for role in roles[:2]:
            for ind in (industries[:2] or ["growing company"]):
                for c in country_list[:3]:
                    queries.append(f"{role} {ind} {c}")

    elif arch == "recruiter":
        for ind in (industries[:3] or ["tech startup", "scale-up"]):
            for c in country_list:
                queries.append(f"{ind} careers {c}")
                queries.append(f"{ind} open positions {c}")
                queries.append(f"fast growing {ind} {c}")
            for role in roles[:3]:
                for c in country_list:
                    queries.append(f"{role} jobs {ind} {c}")

    elif arch == "software":
        for svc in services[:3]:
            for ind in (industries[:3] or ["SMB", "mid-market"]):
                for c in country_list:
                    queries.append(f"{svc} software {c}")
                    queries.append(f"best {svc} tools {ind} {c}")
                    queries.append(f"{ind} {svc} solutions {c}")
        for role in roles[:2]:
            for ind in (industries[:2] or ["company"]):
                for c in country_list[:3]:
                    queries.append(f"{role} {ind} {c}")

    elif arch == "professional_firm":
        needs = triggers if triggers else ["compliance", "regulatory", "audit"]
        for need in needs[:3]:
            for ind in (industries[:3] or ["mid-market company"]):
                for c in country_list:
                    queries.append(f"{ind} {need} {c}")
        for svc in services[:3]:
            for ind in (industries[:3] or ["company"]):
                for c in country_list:
                    queries.append(f"{ind} {svc} {c}")
                    queries.append(f"{svc} firms {c}")

    elif arch in ("manufacturer", "distributor"):
        for svc in services[:3]:
            for c in country_list:
                queries.append(f"{svc} suppliers {c}")
                queries.append(f"{svc} manufacturers {c}")
            for ind in (industries[:3] or ["industrial"]):
                for c in country_list:
                    queries.append(f"{ind} {svc} {c}")
                    queries.append(f"{ind} companies {c}")

    elif arch == "service_agency":
        # For service agencies, find PROSPECTS who need the service — not competitor agencies.
        import datetime as _dt_q
        _year = _dt_q.datetime.now().year
        _years = [str(_year), str(_year + 1)]

        # Extract core keywords from user's services
        _svc_keywords = []
        for svc in services[:4]:
            _words = svc.lower().split()
            if len(_words) >= 2:
                _svc_keywords.append(" ".join(_words[:2]))
            _svc_keywords.append(svc.lower())

        # A) Find prospect organizations by industry + need
        for ind in (industries[:4] or ["company"]):
            for c in country_list[:6]:
                queries.append(f"{ind} companies {c}")
                queries.append(f"{ind} companies looking for {services[0].lower() if services else 'services'} {c}")

        # B) Find companies by their service needs
        for sk in _svc_keywords[:4]:
            for c in country_list[:5]:
                queries.append(f"{sk} services {c}")
                queries.append(f"outsource {sk} {c}")
                queries.append(f"hire {sk} agency {c}")

        # C) Find companies by buyer role + need
        for role in (roles[:3] or ["director"]):
            for ind in (industries[:3] or ["company"]):
                for c in country_list[:4]:
                    queries.append(f"{role} {ind} {c}")

        # D) Find companies with active hiring signals (from wizard)
        _hiring = wiz.get("hiring_signals", "")
        if _hiring:
            _hire_terms = [h.strip() for h in _hiring.split(",") if h.strip()][:3]
            for ht in _hire_terms:
                for c in country_list[:5]:
                    queries.append(f"{ht} job {c}")

        # E) Find companies by buyer search terms (from wizard)
        _buyer_terms = wiz.get("buyer_search_terms", "")
        if _buyer_terms:
            _bt = [b.strip().strip('"') for b in _buyer_terms.split(",") if b.strip()][:4]
            for bt in _bt:
                for c in country_list[:4]:
                    queries.append(f"{bt} {c}")

        # F) Industry associations and directories (high-value prospect lists)
        for ind in (industries[:3] or services[:2]):
            for c in country_list[:5]:
                queries.append(f"{ind} association {c}")
                queries.append(f"{ind} directory {c}")

    elif arch == "local_b2b":
        # [service] + [business_type] + [local_area]
        for svc in services[:3]:
            for c in country_list:
                queries.append(f"commercial {svc} provider {c}")
                queries.append(f"business {svc} contractor {c}")
                queries.append(f"corporate {svc} service {c}")

    else:
        # Generic: find prospects by industry + service keywords
        for svc in (services[:3] or ["professional services"]):
            for ind in (industries[:3] or ["company"]):
                for c in country_list:
                    queries.append(f"{ind} {svc} {c}")
            for c in country_list[:5]:
                queries.append(f"{svc} companies {c}")
        for ind in (industries[:3] or ["growing company"]):
            for c in country_list[:5]:
                queries.append(f"{ind} companies {c}")
                queries.append(f"{ind} firms {c}")

    # ── Add example-client-inspired queries ──
    for good in (goods or [])[:3]:
        name = good.get("name", "")
        if name and len(name) > 2:
            queries.append(f"companies like {name}")
            for c in country_list[:3]:
                queries.append(f"{name} competitors {c}")

    # ── Add role-based queries across all archetypes ──
    for role in roles[:2]:
        for ind in (industries[:2] or ["company"]):
            for c in country_list[:5]:
                queries.append(f"{role} {ind} {c}")

    # ── Add directory/association queries ──
    for ind in (industries[:3] or services[:3]):
        for c in country_list[:5]:
            queries.append(f"{ind} association {c}")
            queries.append(f"{ind} directory {c}")

    # ── Validate and deduplicate ──
    _JUNK_PREFIXES = ("or ", "and ", "this ", "that ", "which ")
    clean = []
    for q in queries:
        q = q.strip()
        if len(q) < 8 or len(q) > 80: continue
        if any(q.lower().startswith(p) for p in _JUNK_PREFIXES): continue
        if ". " in q or "..." in q: continue
        clean.append(q)

    clean = list(dict.fromkeys(clean))
    random.shuffle(clean)

    emit_log(f"Brain generated {len(clean)} {arch}-style queries", "ok")
    return clean[:max_queries]


def _fallback_queries(wizard_data, countries):
    """Generate clean structured queries from wizard profile fields only. No raw text parsing."""
    services = wizard_data.get("services", [])
    industries = wizard_data.get("icp_industries", wizard_data.get("industries_served", []))
    clients = wizard_data.get("ideal_clients", wizard_data.get("clients", []))
    buyer_roles = wizard_data.get("buyer_roles", [])
    triggers = wizard_data.get("triggers", [])
    target = wizard_data.get("target_clients", "")
    company_name = wizard_data.get("company_name", "")

    # Clean structured terms from profile fields — never from raw text
    svc_terms = [s.replace("_", " ").strip() for s in services if s and len(s.strip()) > 2][:5]
    ind_terms = [i.replace("_", " ").strip() for i in industries if i and len(i.strip()) > 2][:5]
    cli_terms = [c.replace("_", " ").strip() for c in clients if c and len(c.strip()) > 2][:5]
    role_terms = [r.replace("_", " ").strip() for r in buyer_roles if r and len(r.strip()) > 2][:4]

    # Fallback if profile is very thin
    if not svc_terms and not ind_terms and not cli_terms:
        if target:
            cli_terms = [w.strip() for w in target.split(",")[:4] if len(w.strip()) > 2]
        if not cli_terms:
            cli_terms = ["company", "business"]
        if not svc_terms:
            svc_terms = ["services", "solutions"]

    queries = []
    country_list = countries if countries else [""]

    # A) Service + industry + country (core discovery)
    for svc in svc_terms[:3]:
        for ind in (ind_terms[:3] or cli_terms[:3]):
            for country in country_list:
                queries.append(f'{ind} {svc} {country}')

    # B) Industry + company type
    for ind in (ind_terms[:4] or cli_terms[:4]):
        for country in country_list:
            queries.append(f'{ind} companies {country}')
            queries.append(f'{ind} firms {country}')

    # C) Buyer role + industry
    for role in role_terms[:3]:
        for ind in (ind_terms[:3] or cli_terms[:3]):
            queries.append(f'{role} {ind}')

    # D) Growth/expansion signals
    for ind in (ind_terms[:3] or cli_terms[:3]):
        for country in country_list:
            queries.append(f'{ind} expanding {country}')
            queries.append(f'{ind} hiring {country}')

    # E) Directory/association
    for ind in (ind_terms[:3] or cli_terms[:3]):
        for country in country_list:
            queries.append(f'{ind} association {country}')
            queries.append(f'{ind} directory {country}')

    # ── Validate every query ──
    _JUNK_PREFIXES = ("or ", "and ", "this ", "that ", "which ", "also ", "the ")
    _JUNK_CHARS = set("{}[]()\\|@#$%^&*+=<>")
    clean = []
    for q in queries:
        q = q.strip()
        if len(q) < 8 or len(q) > 80: continue
        if any(q.lower().startswith(p) for p in _JUNK_PREFIXES): continue
        if any(c in q for c in _JUNK_CHARS): continue
        if q.count('"') % 2 != 0: continue  # unmatched quotes
        if ". " in q or "..." in q: continue  # sentence fragments
        clean.append(q)

    queries = list(dict.fromkeys(clean))
    random.shuffle(queries)

    emit_log(f"Generated {len(queries)} structured queries", "ok")
    return queries[:200]






# ───────────────────────────────────────────────────────────────
# SEARCH / FETCH / DEEP-QUALIFY
# ───────────────────────────────────────────────────────────────
@dataclass
class SearchResult:
    url: str; title: str; snippet: str

# Stability fix (Perplexity bug #66): module-level requests.Session
# pools the TCP+TLS connection to the SearXNG host across the 50-300
# queries fired per agent run. Without it, every search() call paid a
# fresh handshake — wasted RTTs and fd churn under load.
_search_session = requests.Session()


def _ddg_fallback_search(query: str, max_results: int) -> list:
    """Scrape DuckDuckGo's no-JS HTML endpoint as a fallback when
    SearXNG is unavailable or has the JSON API disabled. Returns a
    list of SearchResult.

    DDG's HTML endpoint stays well-mannered if we identify ourselves
    and don't hammer it. Practically, this is the "first-time tester
    can run a hunt without setting up Docker" path.
    """
    import urllib.parse as _up
    try:
        url = "https://html.duckduckgo.com/html/?q=" + _up.quote(query)
        r = _search_session.get(url, headers={
            "User-Agent": "Mozilla/5.0 (compatible; Huntova/0.1; +https://huntova.com)",
            "Accept": "text/html,application/xhtml+xml",
        }, timeout=6)
        r.raise_for_status()
        html = r.text
    except Exception as e:
        emit_log(f"DDG fallback failed: {e}", "warn")
        return []

    # DDG html result blocks:
    #   <a rel="nofollow" class="result__a" href="//duckduckgo.com/l/?uddg=ENCODED&...">Title</a>
    #   <a class="result__snippet" ...>Snippet</a>
    # Modern variant skips the redirect and returns direct hrefs. Handle both.
    results: list = []
    # Match each result block
    block_pat = re.compile(
        r'<a[^>]*class="[^"]*result__a[^"]*"[^>]*href="([^"]+)"[^>]*>(.*?)</a>(?:.*?<a[^>]*class="[^"]*result__snippet[^"]*"[^>]*>(.*?)</a>)?',
        re.DOTALL | re.IGNORECASE,
    )
    seen_urls: set[str] = set()
    for match in block_pat.finditer(html):
        raw_href = match.group(1)
        title_html = match.group(2) or ""
        snippet_html = match.group(3) or ""
        # Resolve DDG redirect to the underlying URL
        if "uddg=" in raw_href:
            try:
                parsed = _up.urlparse(raw_href)
                qs = _up.parse_qs(parsed.query)
                target = qs.get("uddg", [""])[0]
                if target:
                    raw_href = _up.unquote(target)
            except Exception:
                pass
        # Normalise scheme
        if raw_href.startswith("//"):
            raw_href = "https:" + raw_href
        if not raw_href.startswith("http"):
            continue
        if raw_href in seen_urls:
            continue
        seen_urls.add(raw_href)
        # Strip HTML tags from title/snippet
        title = re.sub(r"<[^>]+>", "", title_html).strip()
        snippet = re.sub(r"<[^>]+>", "", snippet_html).strip()
        # Decode HTML entities (lightweight — & " < > nbsp)
        for esc, chr_ in (("&amp;", "&"), ("&quot;", '"'), ("&lt;", "<"),
                          ("&gt;", ">"), ("&nbsp;", " "), ("&#x27;", "'"),
                          ("&#39;", "'")):
            title = title.replace(esc, chr_)
            snippet = snippet.replace(esc, chr_)
        results.append(SearchResult(url=raw_href, title=title, snippet=snippet))
        if len(results) >= max_results:
            break
    return results


def search(query, max_results=MAX_RESULTS_PER_QUERY, language="en", time_range="", categories="general"):
    """Search using SearXNG, with a DuckDuckGo HTML fallback.

    Returns list of SearchResult or empty list on failure.

    Args:
        language: Search language (en, de, fr, es, etc.) — affects which results surface
        time_range: Freshness filter — "" (all), "day", "week", "month", "year"
        categories: SearXNG categories — "general", "news", "social media", etc.
    """
    _search_url = SEARXNG_URL.rstrip("/") + "/search"
    params = {
        "q": query,
        "format": "json",
        "language": language,
        "safesearch": "0",
        "categories": categories,
    }
    if time_range:
        params["time_range"] = time_range
    try:
        if _check_stop(): return []
        r = _search_session.get(_search_url, params=params, timeout=6)
        r.raise_for_status()
        data = r.json()
        results = []
        for x in (data.get("results") or []):
            url = (x.get("url") or "").strip()
            if not url or not url.startswith("http"):
                continue
            _title = (x.get("title") or "").strip()
            _snippet = (x.get("content") or "").strip()
            # Reject non-Latin script results (unless searching in that language)
            if language in ("en", "de", "fr", "es", "it", "nl", "pt", "sv", "da", "no", "fi", "pl", "ro"):
                _non_latin = sum(1 for c in _title if '\u0400' <= c <= '\u04FF'
                                 or '\u4E00' <= c <= '\u9FFF'
                                 or '\u0600' <= c <= '\u06FF'
                                 or '\uAC00' <= c <= '\uD7AF')
                if len(_title) > 3 and _non_latin / len(_title) > 0.3:
                    continue
            results.append(SearchResult(
                url=url,
                title=_title,
                snippet=_snippet,
            ))
            if len(results) >= max_results:
                break
        # SearXNG returned 0 results — likely JSON API disabled or rate
        # limited. Try the DDG fallback so first-run testers without a
        # self-hosted SearXNG still get something useful.
        if not results:
            return _ddg_fallback_search(query, max_results)
        return results
    except requests.exceptions.Timeout:
        emit_log(f"Search timeout for: {query[:50]}", "warn")
        return _ddg_fallback_search(query, max_results)
    except requests.exceptions.ConnectionError:
        emit_log("SearXNG unreachable — falling back to DuckDuckGo", "info")
        return _ddg_fallback_search(query, max_results)
    except Exception as e:
        emit_log(f"Search error: {e}", "warn")
        return _ddg_fallback_search(query, max_results)

def supplementary_search(org_name, base_url):
    """Run extra targeted searches for a high-scoring lead to find more intel."""
    extra_urls = []
    domain = urlparse(base_url).netloc.lower().replace("www.","")
    # Build extra search queries from user's wizard data — SearXNG compatible (no operators, no quotes)
    _wiz_extra = load_settings().get("wizard", {})
    _svc_terms = _wiz_extra.get("services", [])[:2]
    _svc_str = " ".join([s for s in _svc_terms if isinstance(s, str)]) if _svc_terms else ""
    # Plain language queries only — SearXNG doesn't support site:, quotes, or OR
    queries = [
        f'{org_name} about team clients',
        f'{org_name} {_svc_str}'.strip(),
        f'{org_name} hiring careers',
    ]
    for q in queries:
        try:
            results = search(q, max_results=3)
            for r in results:
                rurl = normalize_url(r.url)
                if rurl not in extra_urls and urlparse(rurl).netloc.lower().replace("www.","") == domain:
                    extra_urls.append(rurl)
        except: pass
    return extra_urls[:6]

_COOKIE_RE = re.compile(r"^(accept|agree|got it|close|dismiss|ok|allow|consent|i understand)$", re.I)
_LOADMORE_RE = re.compile(r"^(load|show|see|view|expand|read)\s+(more|all|speakers|agenda|schedule|details|sessions|events)", re.I)

def smart_browse(page):
    """Scroll, expand accordions, click 'load more' — like a human would."""
    if page is None: return
    _start = time.time()
    def _budget(): return time.time() - _start < SMART_BROWSE_BUDGET
    try:
        # Dismiss cookie popups
        for btn in page.query_selector_all("button, a, [role='button']")[:30]:
            try:
                txt = (btn.inner_text() or "").strip()
                if _COOKIE_RE.match(txt) and btn.is_visible():
                    btn.click(timeout=1000); time.sleep(0.3); break
            except: pass
        if not _budget(): return
        # Scroll page incrementally
        for _ in range(SMART_BROWSE_SCROLLS):
            if not _budget(): break
            try:
                page.evaluate("window.scrollBy(0, 600)")
                time.sleep(0.35)
            except: break
        try: page.evaluate("window.scrollTo(0, 0)")
        except: pass
        if not _budget(): return
        # Click 'load more' / 'show more' buttons (only buttons, NOT links)
        _clicked = 0
        for btn in page.query_selector_all("button, [role='button']")[:60]:
            if _clicked >= SMART_BROWSE_CLICKS or not _budget(): break
            try:
                txt = (btn.inner_text() or "").strip()
                if _LOADMORE_RE.match(txt) and btn.is_visible():
                    btn.click(timeout=1500); time.sleep(0.5); _clicked += 1
            except: pass
        if not _budget(): return
        # Expand accordions
        _exp = 0
        for el in page.query_selector_all("details:not([open]), [aria-expanded='false']")[:5]:
            if _exp >= 5 or not _budget(): break
            try: el.click(timeout=1000); _exp += 1
            except: pass
        if _exp: time.sleep(0.3)
    except: pass

def fetch_page_requests(url, limit=10000):
    """Lightweight page fetch using requests + Jina Reader fallback for JS-heavy sites."""
    def _strip_html(html):
        t = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL|re.IGNORECASE)
        t = re.sub(r"<style[^>]*>.*?</style>", "", t, flags=re.DOTALL|re.IGNORECASE)
        t = re.sub(r"<[^>]+>", " ", t)
        return re.sub(r"\s+", " ", t).strip()

    for attempt in range(1, MAX_RETRIES + 2):
        try:
            if _check_stop(): return "", ""
            headers = {"User-Agent": USER_AGENT}
            # Stability fix (Perplexity bug #67): the previous version
            # called requests.get() WITHOUT stream=True, then checked
            # len(r.content) > 2_000_000 — but by then `r.content` had
            # already buffered the entire response body into memory.
            # A 50MB PDF or media file wasted bandwidth + RAM before
            # we decided to bail. Now we stream, check Content-Length
            # up front when present, and abort during iter_content if
            # the running total exceeds 2MB.
            with requests.get(url, headers=headers,
                              timeout=SEARCH_TIMEOUT + (attempt-1)*5,
                              verify=False, stream=True) as r:
                r.raise_for_status()
                # Check final URL after redirects against blocklist
                if r.url != url and is_blocked(r.url):
                    return "", r.url
                cl = r.headers.get("Content-Length")
                if cl:
                    try:
                        if int(cl) > 2_000_000:
                            return "", r.url
                    except ValueError:
                        pass
                _chunks = []
                _total = 0
                for chunk in r.iter_content(chunk_size=65536, decode_unicode=False):
                    if not chunk:
                        continue
                    _total += len(chunk)
                    if _total > 2_000_000:
                        return "", r.url
                    _chunks.append(chunk)
                _raw = b"".join(_chunks)
                html = _raw.decode(r.encoding or "utf-8", errors="replace")
                text = _strip_html(html)
            # If text is too short, likely a JS-rendered page — try Jina Reader
            if len(text) < 300 and not _check_stop():
                try:
                    jina_url = f"https://r.jina.ai/{url}"
                    jr = requests.get(jina_url, headers={"Accept": "text/plain", "User-Agent": USER_AGENT}, timeout=20)
                    if jr.status_code == 200 and len(jr.text) > 100:
                        jtext = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', jr.text)
                        jtext = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', jtext)
                        jtext = re.sub(r'#+\s*', '', jtext)
                        jtext = re.sub(r'\s+', ' ', jtext).strip()
                        if len(jtext) > len(text):
                            text = jtext
                            emit_log(f"(Jina Reader: {len(text)} chars)", "fetch")
                except Exception:
                    pass
            return text[:limit], html
        except requests.exceptions.Timeout:
            if attempt <= MAX_RETRIES: time.sleep(1.0*attempt)
            else: emit_log(f"Timeout: {url[:60]}", "warn"); return "", ""
        except Exception as e:
            emit_log(f"Fetch error: {e}", "warn"); return "", ""
    return "", ""

def crawl_prospect(url, max_subpages=4):
    """Crawl a prospect's website — fetch homepage + key subpages for rich context.
    Returns (combined_text, main_html, pages_crawled)."""
    _ua = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}

    def _fetch(u):
        # Cap size at 2MB; stream so we don't buffer 50MB PDFs into RAM
        # before the size check. Mirrors the streaming pattern fetch_url()
        # uses upstream (bug #67 carry-over).
        _CAP = 2_000_000
        try:
            if _check_stop(): return "", ""
            with requests.get(u, headers=_ua, timeout=10, verify=False, stream=True) as r:
                if r.url != u and is_blocked(r.url):
                    return "", ""
                if r.status_code != 200:
                    return "", ""
                # Cheap pre-check via Content-Length when the server
                # advertises it; falls back to streamed iter when missing.
                cl = r.headers.get("Content-Length")
                try:
                    if cl and int(cl) > _CAP:
                        return "", ""
                except (TypeError, ValueError):
                    pass
                buf = bytearray()
                for chunk in r.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    buf.extend(chunk)
                    if len(buf) > _CAP:
                        return "", ""
                html = buf.decode(r.encoding or "utf-8", errors="replace")
                t = re.sub(r"<script[^>]*>.*?</script>", "", html, flags=re.DOTALL|re.IGNORECASE)
                t = re.sub(r"<style[^>]*>.*?</style>", "", t, flags=re.DOTALL|re.IGNORECASE)
                t = re.sub(r"<[^>]+>", " ", t)
                t = re.sub(r"\s+", " ", t).strip()
                return t[:8000], html
        except Exception:
            pass
        # Jina fallback for JS-heavy sites
        if _check_stop(): return "", ""
        try:
            with requests.get(f"https://r.jina.ai/{u}", headers={"Accept": "text/plain", "User-Agent": _ua["User-Agent"]}, timeout=15, stream=True) as jr:
                if jr.status_code != 200:
                    return "", ""
                cl2 = jr.headers.get("Content-Length")
                try:
                    if cl2 and int(cl2) > _CAP:
                        return "", ""
                except (TypeError, ValueError):
                    pass
                buf2 = bytearray()
                for chunk in jr.iter_content(chunk_size=64 * 1024):
                    if not chunk:
                        continue
                    buf2.extend(chunk)
                    if len(buf2) > _CAP:
                        return "", ""
                jtxt = buf2.decode(jr.encoding or "utf-8", errors="replace")
                if len(jtxt) > 100:
                    jt = re.sub(r'!\[[^\]]*\]\([^)]+\)', '', jtxt)
                    jt = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', jt)
                    jt = re.sub(r'#+\s*', '', jt)
                    jt = re.sub(r'\s+', ' ', jt).strip()
                    return jt[:8000], ""
        except Exception:
            pass
        return "", ""

    # Step 1: Fetch the main page
    main_text, main_html = _fetch(url)
    if not main_text or len(main_text) < 100:
        return main_text, main_html, 1

    # Step 2: Extract internal links from HTML
    domain = urlparse(url).netloc.lower()
    base = f"{urlparse(url).scheme}://{urlparse(url).netloc}"
    _priority_paths = re.compile(r'(about|team|people|staff|contact|get.in.touch|service|client|case.stud|portfolio|work|partner|pricing|testimonial|career|blog|leadership|management|impressum|our.team|meet.the|who.we.are|founders|board|directors)', re.I)

    found_links = []
    for m in re.finditer(r'href=["\']([^"\']+)["\']', main_html or ""):
        href = m.group(1).strip()
        if href.startswith("#") or href.startswith("mailto:") or href.startswith("tel:"):
            continue
        if href.startswith("/"):
            href = base + href
        elif not href.startswith("http"):
            continue
        if urlparse(href).netloc.lower() != domain:
            continue
        if href == url or href == url + "/":
            continue
        # Prioritize important pages
        score = 1 if _priority_paths.search(href) else 0
        found_links.append((score, href))

    # Deduplicate and sort by priority
    seen = {url}
    priority_links = []
    for score, link in sorted(found_links, key=lambda x: -x[0]):
        norm = link.split("?")[0].split("#")[0].rstrip("/")
        if norm not in seen:
            seen.add(norm)
            priority_links.append(link)
            if len(priority_links) >= max_subpages:
                break

    # If no priority links found, try common paths
    if len(priority_links) < 2:
        for path in ["/about", "/about-us", "/contact", "/contact-us", "/team", "/our-team", "/people", "/services", "/impressum", "/get-in-touch"]:
            test_url = base + path
            if test_url not in seen:
                priority_links.append(test_url)
                seen.add(test_url)
                if len(priority_links) >= max_subpages:
                    break

    # Step 3: Fetch subpages
    all_text = [f"=== HOMEPAGE ({url}) ===\n{main_text}"]
    pages_crawled = 1
    for sub_url in priority_links:
        if _check_stop(): break
        sub_text, _ = _fetch(sub_url)
        if sub_text and len(sub_text) > 50:
            path = urlparse(sub_url).path or "/"
            all_text.append(f"\n=== {path} ===\n{sub_text[:4000]}")
            pages_crawled += 1
        time.sleep(0.3)

    combined = "\n".join(all_text)
    return combined[:20000], main_html, pages_crawled


def fetch_page(page, url, limit=10000):
    if page is None:
        return fetch_page_requests(url, limit)
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            if _check_stop(): return "", ""
            _page_op_start()
            page.goto(url, timeout=FETCH_TIMEOUT_MS + (attempt-1)*5000, wait_until="domcontentloaded")
            try: page.wait_for_load_state("networkidle", timeout=IDLE_TIMEOUT_MS)
            except: pass
            # Capture screenshot immediately + resume streaming
            try: emit_screenshot(page, url)
            except: pass
            _page_op_done(url)
            _page_op_start()    # pause for smart browsing + extraction
            smart_browse(page)
            try: emit_screenshot(page, url)  # capture after scroll/expand
            except: pass
            text = re.sub(r"\n{3,}", "\n\n", re.sub(r"[ \t]+", " ", page.inner_text("body") or ""))
            html = page.content()
            _page_op_done(url)
            return text[:limit], html
        except PlaywrightTimeoutError:
            _page_op_done(url)
            if attempt <= MAX_RETRIES: time.sleep(1.0*attempt)
            else: emit_log(f"Timeout: {url[:60]}", "warn"); return "", ""
        except Exception as e:
            _page_op_done(url)
            emit_log(f"Fetch error: {e}", "warn"); return "", ""
    return "", ""

_DEEP_KW = re.compile(r"(contact|about|partner|team|people|staff|pricing|faq|testimonial|case-stud|client|portfolio|resource|service|career|hiring|blog|news|press|leadership|approach|method|solution|industry|work-with|our-work)", re.I)

def gather_links(page, base_url, n=DEEP_LINKS):
    if page is None: return []
    domain = urlparse(base_url).netloc.lower()
    try:
        seen = {normalize_url(base_url)}; out = []
        for a in page.query_selector_all("a[href]")[:400]:
            href = (a.get_attribute("href") or "").strip()
            text = (a.inner_text() or "").strip()
            if not href or href.startswith("#") or href.startswith("mailto:"): continue
            full = normalize_url(urljoin(base_url, href))
            if urlparse(full).netloc.lower() != domain or full in seen: continue
            if _DEEP_KW.search(href) or _DEEP_KW.search(text):
                seen.add(full); out.append(full)
                if len(out) >= n: break
        return out
    except: return []

def deep_qualify(page, base_url, base_text):
    extra = gather_links(page, base_url)
    if not extra: return base_text
    parts = [base_text]
    for eurl in extra:
        if _check_stop(): break
        if _check_budget(): break
        emit_log(f"Deep: {eurl[:60]}", "fetch")
        et, eh = fetch_page(page, eurl, 4000)
        if et.strip():
            parts.append(f"\n-- {eurl} --\n{et}")
            # Extract extra structured data from deep pages
            try:
                deep_struct = extract_structured(page, eh, et)
                if deep_struct.strip():
                    parts.append(f"[DEEP-STRUCT] {deep_struct[:500]}")
            except: pass
        time.sleep(DEEP_DELAY)
    return "\n".join(parts)[:15000]



# ─────────────────────────────────────────────────────────────
# PASS 1: RED FLAG FILTER (quick_screen)
# ─────────────────────────────────────────────────────────────

_RF_DEAD_SITE = ["under construction","site is down","website closed",
    "we are no longer","this site has been","domain for sale","parked domain",
    "this page is not available","account suspended","website expired",
    "no longer operational","permanently closed",
    "website coming soon","site coming soon","page coming soon","launch coming soon"]

_RF_NO_BUDGET = ["solo entrepreneur","solopreneur","one-man","one-woman","one person team",
    "hobby project","passion project","volunteer-run","volunteer run",
    "non-profit volunteer","community group","student project","student-run",
    "free webinar series","free online meetup","informal gathering"]

_RF_PLATFORM_VENDOR = ["saas platform","we help you host",
    "start your free trial","sign up for free","book a demo",
    "pricing starts at","per user per month","enterprise plan",
    "compare plans","see all features","integrations marketplace",
    "software pricing page","platform features"]

def quick_screen(url, title, snippet, page_text, html=""):
    """Pass 1 Red Flag Filter. Returns findings dict if survives, None if killed."""
    import datetime as _dt
    findings = {"pass1_survived":True,"red_flags":[],"green_flags":[],
        "estimated_company_size":"unknown","has_events_section":False,
        "has_contact_info":False,"has_video_content":False,
        "platform_detected":None,"content_freshness":"unknown","budget_signals":"unknown","kill_reason":None}

    text_lower = (page_text or "").lower()[:8000]
    html_lower = (html or "").lower()[:15000]
    combined = (title or "").lower() + " " + (snippet or "").lower() + " " + text_lower[:3000]

    # CHECK 0: Non-English page content — reject before wasting AI calls
    _sample = (page_text or "")[:2000]
    _non_latin_count = sum(1 for c in _sample
        if '\u0400' <= c <= '\u04FF' or '\u4E00' <= c <= '\u9FFF'
        or '\u0600' <= c <= '\u06FF' or '\uAC00' <= c <= '\uD7AF')
    if len(_sample) > 100 and _non_latin_count / len(_sample) > 0.15:
        emit_log("PASS1 KILL non-English page content", "skip"); return None

    # CHECK 1: Dead/Broken Site
    for sig in _RF_DEAD_SITE:
        if sig in combined:
            emit_log(f"PASS1 KILL dead site: {sig}", "skip"); emit_thought("Dead site. Skipping.","skip"); return None
    if len(page_text.strip()) < 200:
        emit_log(f"PASS1 KILL page too short ({len(page_text.strip())} chars)", "skip"); return None

    # CHECK 2: Platform/Vendor/SaaS
    vendor_hits = [s for s in _RF_PLATFORM_VENDOR if s in combined]
    if len(vendor_hits) >= 2:
        emit_log(f"PASS1 KILL vendor: {vendor_hits[:2]}", "skip"); emit_thought("Software vendor, not a client.","skip"); return None

    # CHECK 3: No Budget Signals
    for sig in _RF_NO_BUDGET:
        if sig in combined:
            findings["red_flags"].append(f"No budget: {sig}"); findings["budget_signals"] = "very_low"; break

    # Platform detection from HTML
    _plat_map = {"zoom.us":"zoom","zoom.com":"zoom","teams.microsoft.com":"teams",
        "meet.google.com":"google_meet","webex.com":"webex","streamyard":"streamyard",
        "vmix":"vmix","obsproject":"obs","vimeo.com/live":"vimeo_live",
        "youtube.com/live":"youtube_live","restream.io":"restream",
        "on24.com":"on24","hopin.com":"hopin","bigmarker.com":"bigmarker","livestorm":"livestorm"}
    detected = [p for ind, p in _plat_map.items() if ind in html_lower or ind in text_lower]
    if detected:
        findings["platform_detected"] = detected[0]
        advanced = {"vmix","obs","streamyard","restream","vimeo_live","youtube_live"}
        if set(detected) & advanced:
            findings["green_flags"].append(f"Advanced platform: {detected}")
        elif set(detected) <= {"zoom","teams","google_meet","webex"}:
            upgrade_sigs = ["improve our","better production","upgrade our","looking for production",
                "production company","need help with","professional streaming","broadcast quality"]
            if not any(u in combined for u in upgrade_sigs):
                findings["red_flags"].append("Basic platform only, no upgrade intent")

    # CHECK 5: Content Freshness
    cy = _dt.datetime.now().year
    years = [int(y) for y in re.findall(r'\b(20[12]\d)\b', text_lower)]
    if years:
        newest = max(years); age = cy - newest
        if age == 0: findings["content_freshness"]="current_year"; findings["green_flags"].append(f"Content from {cy}")
        elif age == 1: findings["content_freshness"]="last_year"
        elif age <= 3: findings["content_freshness"]="aging"; findings["red_flags"].append(f"Content from {newest}")
        else: findings["content_freshness"]="stale"; findings["red_flags"].append(f"Severely outdated ({newest})")
    cr_years = re.findall(r'\u00a9\s*(20[12]\d)', html_lower)
    if cr_years and cy - max(int(y) for y in cr_years) >= 3:
        findings["red_flags"].append(f"Copyright outdated: {max(cr_years)}")

    # CHECK 6: Company Size
    for size, sigs in {"large":["our global team","offices worldwide","500+ employees","enterprise","publicly traded"],
        "medium":["our team of","employees","staff members","headquarters","multiple offices","department of"],
        "small":["small team","boutique","startup","founded by","family-owned","local business"]}.items():
        if any(s in combined for s in sigs): findings["estimated_company_size"] = size; break

    # CHECK 7: Budget Signals
    budget_pos = ["sponsorship opportunities","exhibition","sponsor packages","registration fee",
        "ticket price","early bird","premium pass","vip pass","corporate rate","delegates",
        "annual conference","annual summit","flagship event",
        "enterprise plan","custom pricing","request a quote","contact sales",
        "pricing page","schedule a demo","book a consultation","get a proposal",
        "annual contract","professional services","managed service"]
    bhits = [b for b in budget_pos if b in combined]
    if len(bhits) >= 2: findings["budget_signals"]="strong"; findings["green_flags"].append(f"Budget: {bhits[:3]}")
    elif bhits: findings["budget_signals"]="moderate"; findings["green_flags"].append(f"Budget: {bhits[0]}")

    # CHECK 8: Events Section
    ev_sigs = ["upcoming events","event calendar","our events","past events","event schedule",
        "conference program","agenda","webinar series","event series","register now",
        "webinar registration","virtual conference","virtual summit","online event"]
    ev_hits = [e for e in ev_sigs if e in combined]
    # Discount "free webinar/event" as weak signal
    _free_event = any(f"free {e}" in combined for e in ["webinar","event","meetup","session","workshop"])
    if ev_hits and not _free_event:
        findings["has_events_section"]=True; findings["green_flags"].append(f"Events: {ev_hits[:3]}")
    elif ev_hits and _free_event:
        findings["has_events_section"]=True  # still has events, but no green flag credit
        findings["red_flags"].append("Events are free (low budget signal)")

    # CHECK 9: Contact Info
    has_email = bool(re.search(r'[\w.+-]+@[\w-]+\.[\w.-]+', text_lower))
    has_li = "linkedin.com/company" in html_lower or "linkedin.com/in/" in html_lower
    has_contact = "contact us" in combined or "/contact" in html_lower
    findings["has_contact_info"] = any([has_email, has_li, has_contact])
    if has_email: findings["green_flags"].append("Email on page")
    if has_li: findings["green_flags"].append("LinkedIn found")

    # CHECK 10: Video Content (enhanced with embed detection)
    vid_sigs = ["youtube.com/watch","youtube.com/channel","vimeo.com/","livestream",
        "live stream","recorded session","watch the recording","replay available",
        "wistia.com","vidyard.com","brightcove","webinar recording","on-demand",
        "youtube.com/embed","player.vimeo.com","streaming platform"]
    if any(v in combined or v in html_lower for v in vid_sigs):
        findings["has_video_content"]=True; findings["green_flags"].append("Video content found")
    # Also check for video embeds in HTML
    try:
        vid_embeds = extract_video_embeds(html)
        if vid_embeds:
            findings["has_video_content"]=True
            if "Video content found" not in findings["green_flags"]:
                findings["green_flags"].append(f"Video embeds: {len(vid_embeds)} found")
            findings["video_platforms"] = list(set(v["platform"] for v in vid_embeds))
    except: pass

    # CHECK 11: Tech Stack Budget Indicators
    try:
        tech = extract_tech_stack(html)
        _premium_tech = {"HubSpot","Salesforce","Marketo","ActiveCampaign","Segment",
                        "Drift","Intercom","Zendesk","Stripe","LinkedIn Insight"}
        premium_found = [t for t in tech if t in _premium_tech]
        if len(premium_found) >= 2:
            findings["green_flags"].append(f"Premium tech: {', '.join(premium_found[:3])}")
            if findings["budget_signals"] == "unknown": findings["budget_signals"] = "moderate"
        elif premium_found:
            findings["green_flags"].append(f"Tech: {premium_found[0]}")
    except: pass

    # CHECK 12: Social Media Presence
    try:
        socials = extract_social_profiles(html)
        if len(socials) >= 3:
            findings["green_flags"].append(f"Active on {len(socials)} social platforms")
        elif len(socials) == 0 and findings["estimated_company_size"] not in ("large","medium"):
            findings["red_flags"].append("No social media presence found")
    except: pass

    # CHECK 13: Event Structure Signals (from HTML)
    try:
        ev_signals = extract_event_signals_html(html, page_text)
        if "registration_form" in ev_signals or "email_capture_form" in ev_signals:
            findings["green_flags"].append("Active registration form")
            findings["has_events_section"] = True
        if "sponsor_section" in ev_signals:
            findings["green_flags"].append("Sponsor section found")
            if findings["budget_signals"] in ("unknown","very_low"): findings["budget_signals"] = "moderate"
        if "countdown_timer" in ev_signals:
            findings["green_flags"].append("Event countdown active")
        if "professional_credits" in ev_signals:
            findings["green_flags"].append("Offers CEU/CPE credits")
        if "networking_features" in ev_signals:
            findings["green_flags"].append("Networking/expo features")
        for sig in ev_signals:
            if sig.startswith("attendees_"):
                try:
                    cnt = int(sig.split("_")[1].replace(",",""))
                    if cnt >= 50: findings["green_flags"].append(f"{cnt}+ attendees")
                    if cnt >= 200: findings["budget_signals"] = "strong"
                except: pass
            if sig.startswith("speakers_"):
                try:
                    cnt = int(sig.split("_")[1])
                    if cnt >= 5: findings["green_flags"].append(f"{cnt} speakers")
                except: pass
        if "past_events_archived" in ev_signals:
            findings["green_flags"].append("Past events archived (recurring)")
        if "multi_day_event" in ev_signals:
            findings["green_flags"].append("Multi-day event")
    except: pass

    # CHECK 14: Phone Number Presence
    try:
        phones = extract_phone_numbers(page_text, html)
        if phones:
            findings["has_contact_info"] = True
            findings["_phones_found"] = phones[:3]
    except: pass

    # CHECK 15: Buying Intent Signals — pages that show ACTIVE purchasing intent
    _intent_sigs = ["request for proposal","rfp","request for quote","rfq","vendor selection",
        "looking for a partner","seeking proposals","procurement","tender notice",
        "invite to bid","expression of interest","outsource","outsourcing",
        "need help with","looking for help","looking for a","searching for a provider",
        "switching from","migrating from","replacing our current","evaluating alternatives",
        "budget approved","recently funded","series a","series b","just raised",
        "expanding our team","growing rapidly","scaling up","new office"]
    _intent_hits = [s for s in _intent_sigs if s in combined]
    if _intent_hits:
        findings["green_flags"].append(f"BUYING INTENT: {', '.join(_intent_hits[:3])}")
        if len(_intent_hits) >= 2:
            findings["budget_signals"] = "strong"

    # CHECK 16: Company Authenticity — real company site vs directory/aggregator
    _auth_sigs = ["our team","meet the team","about us","our story","our mission",
        "founded in","established","case studies","our clients","testimonials",
        "client logos","partners","our approach","how we work","our process",
        "careers","job openings","join us","office address","headquarters",
        "privacy policy","terms of service","cookie policy"]
    _auth_hits = sum(1 for s in _auth_sigs if s in combined)
    if _auth_hits >= 4:
        findings["green_flags"].append(f"Authentic company site ({_auth_hits} signals)")
    elif _auth_hits == 0:
        _dir_sigs = ["listing","directory","browse all","search results","showing results",
            "page 1 of","next page","filter by","sort by","view all categories",
            "top 10","best of","comparison","vs","review roundup"]
        if any(d in combined for d in _dir_sigs):
            findings["red_flags"].append("Directory/aggregator page (not a company)")

    # ═══ KILL DECISIONS (conservative — prefer false positives over false negatives) ═══
    rc, gc = len(findings["red_flags"]), len(findings["green_flags"])

    # Only kill when evidence is OVERWHELMING — be permissive, let AI decide
    if rc >= 6 and gc == 0:
        emit_log(f"PASS1 KILL {rc} red flags, 0 green", "skip"); return None
    if findings["content_freshness"]=="stale" and not findings["has_events_section"] and findings["budget_signals"] in ("very_low",) and gc == 0 and not findings.get("has_contact_info"):
        emit_log("PASS1 KILL stale+no events+no budget+no contact", "skip"); return None

    # Check wizard red flags (additive, never kill alone)
    try:
        _wiz_rf = load_settings().get("wizard", {}).get("red_flags", [])
        if "avoid_solo" in _wiz_rf and findings["estimated_company_size"] == "small":
            findings["red_flags"].append("Wizard: avoid small/solo companies")
        if "avoid_free_events" in _wiz_rf and "Events are free" in str(findings["red_flags"]):
            findings["red_flags"].append("Wizard: avoid free events")
        if "avoid_inhouse" in _wiz_rf:
            _inhouse = ["in-house team","internal team","dedicated team","own department","built internally"]
            if any(sig in combined for sig in _inhouse):
                findings["red_flags"].append("Wizard: has in-house team covering our services")
    except: pass

    # ── NEW: Wire wizard search intelligence fields into screening ──
    # Uses word-boundary matching (not substring) so user-entered phrases
    # don't false-positive on partial matches. E.g. user enters
    # lookalikes="Zoom" → a page that says "Zoom for webinars" used to be
    # killed by the literal substring match; now \bZoom\b only fires on
    # the actual word boundary.
    def _phrase_hits(raw: str, text: str, limit: int = 3) -> list:
        if not raw:
            return []
        phrases = [p.strip().lower() for p in re.split(r'[,\n;]|(?:\.\s)', raw) if len(p.strip()) > 4]
        hits = []
        for p in phrases:
            # Escape user input so regex metacharacters in their wizard
            # input don't blow up; pattern ends up \b<escaped>\b.
            if re.search(r'\b' + re.escape(p) + r'\b', text):
                hits.append(p)
                if len(hits) >= limit:
                    break
        return hits
    try:
        _wiz = load_settings().get("wizard", {})

        # DISQUALIFICATION SIGNALS → hard red flags
        for dh in _phrase_hits(_wiz.get("disqualification_signals", ""), combined):
            findings["red_flags"].append(f"Disqualified: {dh}")

        # LOOKALIKES → fast reject (user told us what looks right but is wrong)
        for lh in _phrase_hits(_wiz.get("lookalikes", ""), combined):
            findings["red_flags"].append(f"Lookalike (wrong): {lh}")

        # BUYING SIGNALS → early green flags
        for bh in _phrase_hits(_wiz.get("buying_signals", ""), combined):
            findings["green_flags"].append(f"Buying signal: {bh}")

        # WEB DISCOVERY PAGES → page-type weighting
        for dch in _phrase_hits(_wiz.get("web_discovery_pages", ""), combined, limit=2):
            findings["green_flags"].append(f"Discovery page match: {dch}")
    except Exception:
        pass

    # Re-check after wizard flags — only kill with extreme evidence
    rc, gc = len(findings["red_flags"]), len(findings["green_flags"])
    if rc >= 7 and gc <= 1:
        emit_log(f"PASS1 KILL {rc} red flags (incl wizard rules)", "skip"); return None

    if gc >= 3:
        emit_log(f"⭐ Strong match — analysing deeper", "ai")
    else:
        emit_log(f"Potential match — analysing", "ai")
    if findings["green_flags"]: emit_thought(f"Promising: {', '.join(findings['green_flags'][:3])}", "thinking")
    return findings


# ───────────────────────────────────────────────────────────────
# ANALYSE LEAD
# ───────────────────────────────────────────────────────────────
def analyse_lead(url, title, snippet, page_text, structured=""):
    _tier_model = _get_tier_model()
    _page_limit = _get_tier_page_limit()
    # analyse_lead log is in the caller (emit_log "🤖 Evaluating:") — don't duplicate
    emit_thought("Analysing page content. Is this a good fit?", "thinking")
    sb = f"\nSTRUCTURED DATA:\n{structured[:1500]}" if structured.strip() else ""
    tld_hint = guess_country_from_tld(url)
    tld_line = f"\nTLD suggests country: {tld_hint}" if tld_hint else ""

    _settings = load_settings()
    _booking = _settings.get("booking_url","")
    _booking_line = f"\nBOOKING LINK: End every email with 'Pick a time that works for a quick chat: {_booking}'" if _booking else ""
    
    # Inject wizard configuration into AI context
    _wiz = _settings.get("wizard", {})
    HUNTOVA_CONTEXT = _build_ai_context()
    # Log context info to server console only
    if not hasattr(analyse_lead, '_logged_ctx'):
        _has_dna = "AGENT INTELLIGENCE PROFILE" in HUNTOVA_CONTEXT
        print(f"[AGENT] AI context: {len(HUNTOVA_CONTEXT)} chars, DNA: {_has_dna}")
        analyse_lead._logged_ctx = True
    prompt = f"""{HUNTOVA_CONTEXT}

PAGE: {url}
Title: {title}
Snippet: {snippet}{tld_line}
Text:
{page_text[:_page_limit]}
{sb}{_booking_line}

═══ ANALYSIS INSTRUCTIONS ═══

Score this page as a potential B2B lead based on the company profile above.

TODAY'S DATE: {datetime.now().strftime('%B %d, %Y')}

FIRST — classify this page (before doing anything else):
- If the event/opportunity has ALREADY PASSED (date is before today) and there is NO upcoming edition → SCORE 0 immediately
- Organisation that matches our target client profile → PROMISING, analyse deeply
- Company/org in our target industry with clear need for our services → PROMISING
- Association / society / professional body that fits → PROMISING
- Blog post / news article → SCORE 0 unless it reveals a real prospect
- Platform vendor page or SaaS tool → SCORE 0
- Competitor / agency offering similar services → SCORE 0
- Job listing / careers page → SCORE 0
- Generic company page with no relevance to our services → SCORE 0
- Expired one-off event with no future activity → SCORE 1-2 max

THEN — extract intelligence:
- ORGANISATION: Who is the company/org on this page? Not a platform or directory — the actual entity.
- OPPORTUNITY/CONTEXT NAME: What is the specific opportunity, project, or context? Is there a recurring need? How often?
- BUDGET SIGNALS: Sponsors? Paid tiers? Premium design? Hiring? Growth indicators?
- SERVICE GAP: What could WE specifically help them with? What is lacking or could be improved? Be precise.
- CURRENT TOOLS: What tools/services do they currently use that we could improve on?
- CONTACTS: Scan for names, email addresses, LinkedIn URLs, phone numbers, "contact us" links, staff pages. Extract EVERYTHING you find.
- GEOGRAPHY: Determine the SPECIFIC country. Use TLD hints, addresses, phone numbers, language, currency. NEVER output "EU" or "USA" or "Europe" — always the real country name.

SCORE BREAKDOWN — Rate each dimension 1-10 with a one-line reason:
(a) fit_score — how well does this org match our ideal client profile?
(b) service_opportunity — how much opportunity is there for us to add value with our services?
(c) budget_signals — evidence they have budget (sponsors, paid tiers, professional site, hiring)?
(d) timing — is there an upcoming need, active project, or urgency signal?
(e) accessibility — can we reach a decision maker (contact info found, small org, clear structure)?

EVIDENCE DOSSIER — Find 3-5 specific data points from the page. Each must have a concrete observation and a verbatim quote from the text.

EMAIL RULES:
- Write a COMPLETE cold email (3-5 sentences, under 80 words). Reference a specific detail from THIS page.
- Start with THEIR situation/challenge, not who you are. First word should NOT be "I" or "We".
- Include a concrete offer: free audit, consultation, 20% introductory discount, or demo.
- For ongoing/recurring needs: mention long-term partnership or volume pricing as a natural advantage.
- Subject: max 6 words, specific to THEM. LinkedIn: under 180 chars.
- NEVER use "..." or placeholder text. Every field must be fully written.
- If you cannot write a real, specific email → set fit_score to 0.

MULTI-DIMENSIONAL SCORING — evaluate each dimension separately:

FIT SCORE (0-10): How well does this prospect match our ideal client profile?
- 10: Perfect match — exact industry, exact size, exact need
- 7-9: Strong match — clear alignment with our services
- 4-6: Possible match — related but unclear
- 1-3: Weak match — tangential
- 0: Not a match at all

BUYABILITY SCORE (0-10): Would this company realistically buy external help like ours through cold outreach?
- 10: Perfect outbound target — right size, clear external buying pattern, reachable decision maker
- 7-9: Good target — likely buys external, reasonable size
- 4-6: Uncertain — might buy but unclear signals
- 1-3: Poor target — too large, too internalized, procurement-heavy, or unrealistic for cold outreach
- 0: Impossible — giant enterprise, government, mega-brand, strong internal team with no external need

REACHABILITY SCORE (0-10): Can we actually reach and engage a decision maker?
- 10: Named decision maker with direct contact info
- 7-9: Departmental contact or clear pathway to buyer
- 4-6: Organization contact but no specific buyer identified
- 1-3: Generic info@ or no visible contact
- 0: No contact path at all

SERVICE OPPORTUNITY SCORE (0-10): How much value could we add with our specific services?
- 10: Clear gap that exactly matches what we offer, visible pain
- 7-9: Strong opportunity — they need improvement in our area
- 4-6: Some opportunity but not urgent or clear
- 1-3: Minimal opportunity — they seem well-served already
- 0: No visible opportunity for our services

TIMING SCORE (0-10): Is there evidence of active or imminent need?
- 10: Active procurement, live project, immediate deadline
- 7-9: Recent activity suggesting upcoming need (new hire, expansion, planning)
- 4-6: General ongoing activity but no urgency signal
- 1-3: Stale or dormant — no recent activity
- 0: No timing signals at all

INTERNAL CAPABILITY: Set has_internal_team to true if the company clearly has a large dedicated internal team that covers what we offer. This is a penalty signal — strong internal teams mean less likely to buy external help unless overflow/specialist need is visible.

A lead must score well on ALL FIVE dimensions to be truly qualified.
A company can be highly relevant (fit=9) but a terrible lead (buyability=2, reachability=1).

CRITICAL SCORING DISCIPLINE:

FIT ≠ LEAD. A company can match the market perfectly (fit=9) but still be a terrible lead if:
- There is no visible need RIGHT NOW (timing=1) → cap fit_score at 5
- There is no way to reach anyone (reachability=0) → cap fit_score at 4
- They look right but match a known LOOKALIKE pattern → score 0 regardless

GENERIC BUSINESS PAGES ARE NOT LEADS:
If the page is just a company /about page with no active projects, no hiring signals, no upcoming needs, no purchasing intent — it is NOT a lead even if the company is in the right industry. Score fit 3-5 max. A lead requires EVIDENCE OF ACTIVE NEED, not just industry match.

DIRECTORY/AGGREGATOR PAGES ARE NEVER LEADS:
If the page lists MANY companies (a directory, top-10 list, comparison table, search results page, marketplace listing) — the page itself is NOT a lead. Score 0. The individual companies listed MIGHT be leads, but the aggregator page is not.

BUYING INTENT SIGNALS (score 8+ when detected):
These phrases indicate ACTIVE purchasing intent — boost timing_score significantly:
- "request for proposal", "RFP", "vendor selection", "seeking proposals"
- "outsource", "looking for a partner/provider", "need help with"
- "switching from [competitor]", "evaluating alternatives", "replacing current"
- "budget approved", "recently funded", "Series A/B raised"
- "expanding rapidly", "scaling up", "new office opening"

TIMING IS THE MULTIPLIER:
- Fit 8 + Timing 9 = genuine lead (active project, just posted job, upcoming deadline, purchasing intent)
- Fit 8 + Timing 2 = just a company that exists in the market (not a lead yet)
- Do NOT give high fit_scores to companies that merely exist in the right space

SCORING CALIBRATION (use these as anchors — based on business profile above):
- 10: Perfect match — active need for our exact services RIGHT NOW, confirmed budget, decision maker visible, upcoming timing
- 9: Strong match — clear active need, good fit, 3+ positive signals (recurring OR budget OR upcoming), contact accessible
- 7-8: Good match — likely needs our services AND shows timing/need evidence, organisation reachable
- 5-6: Market fit but no active need visible — right industry, right size, but no evidence they need us NOW
- 3-4: Weak match — tangential connection, no need evidence, or generic business page
- 1-2: Poor match — wrong industry, no visible need, or consumer/irrelevant
- 0: Not a lead — job listing, news article, blog, wiki, platform vendor, competitor, lookalike-but-wrong

SCORE INFLATION GUARD:
Of every 10 pages you analyse, realistically expect: 0-1 score 9-10, 1-3 score 7-8, 2-4 score 5-6, the rest 0-4.
If you are giving 7+ to more than 3 of every 10 pages, you are inflating scores.
Before giving ANY score of 7 or higher, verify ALL FOUR of these conditions:
(1) You found SPECIFIC evidence of a need for the seller's exact service on this page
(2) That need is active or imminent, not historical or hypothetical
(3) A real contact path exists (not just a generic website)
(4) The company is the right size and type for cold outreach (not enterprise, not government, not a solo freelancer)
If ANY of these four is unverified, the score MUST be 6 or below. When uncertain between two bands, always score the lower one.

INTEGRITY CHECKS (verify before outputting):
- Is org_name the ORGANISER, not the platform? (If event is on Zoom, org is NOT Zoom)
- Is evidence_quote an actual verbatim string from the page text?
- Is the fit_score supported by BOTH market fit AND active need evidence?
- If fit_score > 6, is timing_score also > 3? If not, reduce fit_score.
- Could this email ONLY be sent to this specific prospect?

Respond with ONLY a JSON object. Keep string values SHORT (under 100 chars each). Fields:
{{"org_name":"string","country":"string","city":"string or null","region":"EU|USA|Middle East|UK|Other",
"event_name":"context or opportunity that surfaced this prospect","event_type":"industry or business category","platform_used":"tools or platforms they currently use",
"is_virtual_only":true/false,"is_recurring":true/false,"frequency":"string","audience_size_guess":"string",
"org_website":"string or null","org_linkedin":"string or null",
"contact_name":"string or null","contact_role":"string or null",
"contact_email":"string or null","contact_phone":"string or null","contact_linkedin":"string or null","contact_page_url":"string or null",
"evidence_quote":"one verbatim sentence from page","production_gap":"specific service gap or opportunity we can fill",
"fit_score":0-10,"buyability_score":0-10,"reachability_score":0-10,"service_opportunity_score":0-10,"timing_score":0-10,"has_internal_team":true/false,"why_fit":"one sentence","fit_rationale":"which specific evidence drove the fit score","timing_rationale":"what timing/urgency signal was found or not found","buyability_rationale":"why this company can or cannot buy via cold outreach",
"current_tools":"what they use now","tool_weaknesses":"short description",
"email_subject":"max 6 words","email_body":"3-5 sentences under 80 words","linkedin_note":"under 150 chars"}}"""

    data = None
    _tier_model = _get_tier_model()
    for attempt in range(1, MAX_RETRIES + 2):
        try:
            _create_kwargs = {
                "model": _tier_model,
                "messages": [{"role":"system","content":"You are a ruthlessly precise B2B lead analyst. Output ONLY a single valid JSON object (not an array). No markdown, no explanation, no wrapping in []."},{"role":"user","content":prompt}],
                "temperature": 0.35,
                "max_tokens": 16000,
                # Stability fix (Perplexity bug #36): 60s upper bound on
                # the per-lead AI call so a stuck Gemini stream can't
                # hang the entire agent run. analyse_lead is on the hot
                # path; without this a single bad upstream pause stops
                # all leads behind it.
                "timeout": 60,
            }
            # Force JSON output for Gemini models
            if "gemini" in _tier_model:
                _create_kwargs["response_format"] = {"type": "json_object"}
            resp = client.chat.completions.create(**_create_kwargs)
            raw = (resp.choices[0].message.content or "").strip()
            _finish = getattr(resp.choices[0], 'finish_reason', None)
            # Strip think tags if present (Qwen), Gemini doesn't use them
            if "<think>" in raw:
                raw = re.sub(r"<think>.*?</think>", "", raw, flags=re.DOTALL).strip()
                raw = re.sub(r"<think>.*$", "", raw, flags=re.DOTALL).strip()
            js = extract_json(raw)
            if not js:
                emit_log(f"⚠️ No JSON in response ({len(raw)} chars, finish={_finish}): {raw[:150]}", "warn")
                if _finish == "length" and attempt <= MAX_RETRIES:
                    # Truncated — retry with less page text to give model more room for output
                    prompt = prompt.replace(page_text[:4000], page_text[:2000])
                    emit_log("Retrying with shorter input...", "ai")
                raise ValueError("No JSON")
            data = json.loads(js); break
        except (json.JSONDecodeError, ValueError) as e:
            if attempt <= MAX_RETRIES: time.sleep(1.5*attempt)
            else: emit_log(f"Analysis failed: {e}", "warn"); return None
        except Exception as e:
            if attempt <= MAX_RETRIES: time.sleep(2.0*attempt)
            else: emit_log(f"Analysis failed: {e}", "warn"); return None

    if data is None: return None

    # Normalise — flatten lists/dicts Gemini sometimes returns for string fields
    def _to_str(v):
        while isinstance(v, list):
            v = v[0] if v else ""
        if isinstance(v, dict):
            v = v.get("name") or v.get("value") or v.get("url") or str(v)
        if v is None: return ""
        if not isinstance(v, str): v = str(v)
        return v.strip()

    for f in ["org_name","country","city","region","event_name","event_type","platform_used",
              "frequency","audience_size_guess","evidence_quote","production_gap","why_fit",
              "email_subject","email_body","linkedin_note"]:
        data[f] = _to_str(data.get(f))
    for f in ["org_website","org_linkedin","contact_name","contact_role","contact_department",
              "contact_email","contact_phone","contact_linkedin","contact_page_url"]:
        data[f] = _to_str(data.get(f)) or None
    data["platform_used"] = data["platform_used"] or "unknown"
    data["is_virtual_only"] = bool(data.get("is_virtual_only", False))
    data["is_recurring"] = bool(data.get("is_recurring", False))
    data["contact_email"] = validate_email(data.get("contact_email"))
    # ── Enrich missing contact fields from JSON-LD structured data ──
    structured_lines = structured.split("\n") if structured else []
    if structured_lines:
        _jld_contacts = extract_contacts_from_structured(structured_lines)
        for _jc in _jld_contacts:
            if not data.get("contact_email") and _jc.get("email"):
                _ve = validate_email(_jc["email"])
                if _ve and not _GENERIC_EMAIL_RE.match(_ve):
                    data["contact_email"] = _ve
                    data["_contact_source"] = _jc.get("source", "jsonld")
                    data["_contact_confidence"] = _jc.get("confidence", 0.85)
            if not data.get("contact_name") and _jc.get("name"):
                data["contact_name"] = _jc["name"][:80]
                if not data.get("_contact_source"):
                    data["_contact_source"] = _jc.get("source", "jsonld")
            if not data.get("contact_role") and _jc.get("role"):
                data["contact_role"] = _jc["role"][:80]
            if not data.get("contact_phone") and _jc.get("phone"):
                _ph = _jc["phone"].strip()
                if len(re.sub(r"\D", "", _ph)) >= 7:
                    data["contact_phone"] = _ph
    data["fit_score"] = clamp_int(data.get("fit_score",0), 0, 10, 0)
    data["buyability_score"] = clamp_int(data.get("buyability_score",0), 0, 10, 0)
    data["reachability_score"] = clamp_int(data.get("reachability_score",0), 0, 10, 0)
    data["service_opportunity_score"] = clamp_int(data.get("service_opportunity_score",0), 0, 10, 0)
    data["timing_score"] = clamp_int(data.get("timing_score",0), 0, 10, 0)

    # ── Detect hiring-as-need signal for email personalization ──
    _wiz = load_settings().get("wizard", {})
    _hsigs = (_wiz.get("hiring_signals", "") or "").lower()
    if _hsigs and page_text:
        _page_low = page_text[:5000].lower()
        _hiring_terms = [h.strip() for h in _hsigs.split(",") if h.strip()]
        if any(ht in _page_low for ht in _hiring_terms) or any(w in _page_low for w in ["hiring","careers","job opening","we're looking for","join our team"]):
            data["_hiring_signal_detected"] = True

    # ── SCORE RECALIBRATION: fit without need ≠ lead ──
    _fit = data["fit_score"]
    _timing = data["timing_score"]
    _opp = data["service_opportunity_score"]
    _buy = data["buyability_score"]
    _reach = data["reachability_score"]

    # Rule 1: High fit but no timing evidence → cap fit at 5
    # A company that matches the market but shows no active need is not a lead yet
    # Timing alone is sufficient to cap — a zero-urgency company is not a lead regardless of opportunity
    if _fit > 5 and _timing <= 2:
        data["fit_score"] = min(_fit, 5)
        data["_recal"] = f"fit {_fit}→{data['fit_score']}: timing={_timing} — no active need evidence"

    # Rule 2: High fit but zero reachability → cap at 4
    if _fit > 4 and _reach == 0:
        data["fit_score"] = min(data["fit_score"], 4)
        data["_recal"] = f"fit capped at 4: no contact path"

    # Rule 3: Lookalike penalty — check wizard lookalike signals against page
    try:
        _wiz_look = load_settings().get("wizard", {}).get("lookalikes", "")
        if _wiz_look and data["fit_score"] > 0:
            _look_phrases = [p.strip().lower() for p in re.split(r'[,\n;]|(?:\.\s)', _wiz_look) if len(p.strip()) > 4]
            _page_combined = (title + " " + snippet + " " + page_text[:2000]).lower()
            _look_count = sum(1 for p in _look_phrases if p in _page_combined)
            if _look_count >= 2:
                data["fit_score"] = 0
                data["_recal"] = f"fit→0: matched {_look_count} lookalike signals"
    except Exception:
        pass

    # Rule 4: Disqualification signals — user-defined hard reject phrases
    try:
        _wiz_disq = load_settings().get("wizard", {}).get("disqualification_signals", "")
        if _wiz_disq and data["fit_score"] > 0:
            _disq_phrases = [p.strip().lower() for p in re.split(r'[,\n;]|(?:\.\s)', _wiz_disq) if len(p.strip()) > 4]
            _page_combined_dq = (title + " " + snippet + " " + page_text[:2000]).lower()
            _disq_hits = [p for p in _disq_phrases if p in _page_combined_dq]
            if len(_disq_hits) >= 2:
                data["fit_score"] = 0
                data["_recal"] = f"fit→0: matched {len(_disq_hits)} disqualification signals"
    except Exception:
        pass

    # Normalize deal intelligence fields (may be absent — Gemini compact schema)
    data["current_tools"] = _to_str(data.get("current_tools"))
    data["tool_weaknesses"] = _to_str(data.get("tool_weaknesses"))
    data.setdefault("contact_department", None)
    sb = data.get("score_breakdown") or {}
    if isinstance(sb, dict):
        for dim in ["event_fit","production_gap","budget_signals","timing","accessibility"]:
            sub = sb.get(dim) if isinstance(sb.get(dim), dict) else {}
            sb[dim] = {"score": clamp_int(sub.get("score",0), 0, 10, 0), "reason": _to_str(sub.get("reason"))[:200]}
    else:
        sb = {}
    data["score_breakdown"] = sb
    ed = data.get("evidence_dossier") or []
    if isinstance(ed, list):
        data["evidence_dossier"] = [{"point": _to_str(e.get("point"))[:200], "quote": _to_str(e.get("quote"))[:300]} for e in ed[:5] if isinstance(e, dict)]
    else:
        data["evidence_dossier"] = []

    # ── QUOTE VERIFICATION — check evidence_quote against actual page text ──
    _eq = _to_str(data.get("evidence_quote", "")).strip()
    _page_lower = page_text[:8000].lower() if page_text else ""
    if _eq and len(_eq) > 15 and _page_lower:
        _eq_lower = _eq.lower()
        if _eq_lower in _page_lower:
            data["_quote_verified"] = "exact"
        else:
            # Fuzzy: check word overlap
            _eq_words = [w for w in _eq_lower.split() if len(w) > 3]
            if _eq_words:
                _matched = sum(1 for w in _eq_words if w in _page_lower)
                _overlap = _matched / len(_eq_words)
                if _overlap >= 0.7:
                    data["_quote_verified"] = "close"
                elif _overlap >= 0.4:
                    data["_quote_verified"] = "partial"
                else:
                    data["_quote_verified"] = "unverified"
            else:
                data["_quote_verified"] = "unverified"
    elif not _eq or len(_eq) <= 15:
        data["_quote_verified"] = "missing"
    else:
        data["_quote_verified"] = "no_page_text"

    # ── DATA CONFIDENCE — computed from verified signals ──
    _conf = 0
    _conf_signals = 0
    _conf_total = 5  # max possible signals
    # Signal 1: evidence quote verified
    _qv = data.get("_quote_verified", "missing")
    if _qv in ("exact", "close"): _conf += 1; _conf_signals += 1
    elif _qv == "partial": _conf += 0.5; _conf_signals += 1
    # Signal 2: contact email found
    if data.get("contact_email"): _conf += 1; _conf_signals += 1
    # Signal 3: contact name found
    if data.get("contact_name"): _conf += 1; _conf_signals += 1
    # Signal 4: org website confirmed
    if data.get("org_website"): _conf += 0.5; _conf_signals += 1
    elif data.get("org_linkedin"): _conf += 0.5; _conf_signals += 1
    # Signal 5: timing rationale present and specific
    _tr = _to_str(data.get("timing_rationale", ""))
    if len(_tr) > 20: _conf += 1; _conf_signals += 1
    data["_data_confidence"] = round(_conf / _conf_total, 2)
    data["_confidence_signals"] = _conf_signals

    # ── LEARNING-AWARE RANKING ──
    # Compute _rank_score combining scoring dimensions + confidence + contactability + learning profile
    _rs = 0.0
    _rr = []  # rank reasons
    # Base score: timing*3 + fit*2 + service_opp (same weights as frontend "hot" sort)
    _rs += data["timing_score"] * 3
    _rs += data["fit_score"] * 2
    _rs += data.get("service_opportunity_score", 0)
    # Confidence bonus/penalty (amplified — outcome data shows confidence predicts user action)
    _dc_val = data.get("_data_confidence", 0)
    if _dc_val >= 0.6:
        _rs += 5; _rr.append("High confidence evidence (+5)")
    elif _dc_val < 0.3:
        _rs -= 8; _rr.append("Low confidence evidence (-8)")
    # Quote verification bonus/penalty
    _qv_val = data.get("_quote_verified", "missing")
    if _qv_val in ("exact", "close"):
        _rs += 3; _rr.append("Quote verified (+3)")
    elif _qv_val == "unverified":
        _rs -= 4; _rr.append("Quote unverified (-4)")
    # Contactability bonus (amplified — personal contact strongly predicts engagement)
    if data.get("contact_email"):
        _cc = data.get("_contact_confidence", 0.5)
        if _cc >= 0.7:
            _rs += 5; _rr.append("Strong contact (+5)")
        else:
            _rs += 1; _rr.append("Contact found (+1)")
    else:
        _rs -= 3; _rr.append("No contact email (-3)")
    # Learning profile match
    _lp_instr = _w.get("_learning_instructions", "") if '_w' in dir() else ""
    # Check learned preferences from wizard context
    _wiz_lp = load_settings().get("wizard", {}).get("_learning_instructions", "")
    if _wiz_lp:
        _org_low = (data.get("org_name", "") + " " + data.get("why_fit", "") + " " + data.get("event_type", "")).lower()
        # Check for avoided patterns (from learning profile)
        _avoided_terms = ["competitor", "government", "enterprise", "too large", "too small"]
        for _at in _avoided_terms:
            if _at in _org_low:
                _rs -= 4; _rr.append(f"Matches avoided pattern: {_at} (-4)"); break
    # Recurring need bonus
    if data.get("is_recurring"):
        _rs += 2; _rr.append("Ongoing need (+2)")
    data["_rank_score"] = round(max(0, _rs), 1)
    data["_rank_reasons"] = _rr

    # Auto-fix country if generic
    if data["country"] in ("EU","USA","Europe","United States of America"):
        if data["country"] in ("USA","United States of America"): data["country"] = "United States"
        elif tld_hint: data["country"] = tld_hint
    # Fix region from country
    eu_countries = {"France","Germany","Netherlands","Belgium","Luxembourg","Austria","Switzerland",
                    "Italy","Spain","Portugal","Greece","Malta","Cyprus","Sweden","Denmark","Norway",
                    "Finland","Iceland","Poland","Czech Republic","Slovakia","Hungary","Romania","Bulgaria",
                    "Croatia","Slovenia","Serbia","Bosnia","Albania","Montenegro","North Macedonia",
                    "Estonia","Latvia","Lithuania","Ireland"}
    if data["country"] in eu_countries: data["region"] = "EU"
    elif data["country"] in ("United States",): data["region"] = "USA"
    elif data["country"] in ("UAE","Dubai","Abu Dhabi","Sharjah"): data["region"] = "Middle East"

    # NOTE: UK region filter removed — if user doesn't want UK leads,
    # they exclude UK from country selection in the start popup.

    # Placeholder email detection + re-gen.
    # Previously we only flagged on `[` count >= 2, which missed the most
    # common leak ("[Your Name]" has a single `[`). Now we match the real
    # patterns AI models leave behind — bracketed English placeholder names,
    # handlebars, angle-bracket variables, and curly-brace template slots.
    # Catching these before save prevents sending obviously-AI emails that
    # burn deliverability and trust.
    if data["fit_score"] > 0:
        subj, body = data.get("email_subject",""), data.get("email_body","")
        combined = (subj + "\n" + body)
        placeholder_hit = bool(re.search(
            r"\[(your\s*)?(name|company|org|organization|first\s*name|last\s*name|title|role|product|service|industry)\]|"
            r"\[INSERT\b|\[TODO\b|\[FILL\s*IN\b|"
            r"\{\{[^}]+\}\}|"
            r"<<[^>]+>>|"
            r"\{(?:name|company|org|first_name|last_name|title|role)\}",
            combined, re.I))
        bad = (
            len(subj) < 8
            or len(body) < 80
            or subj.strip() == "..."
            or body.count("...") >= 2
            or placeholder_hit
        )
        if bad:
            emit_log(f"✏️ Placeholder email — re-generating for {data.get('org_name','?')}", "ai")
            emit_thought("Email has placeholders. Rewriting...", "thinking")
            if not _regen_email(data, page_text):
                    data["_email_needs_regen"] = True
                    emit_log(f"Email regen failed for {data.get('org_name','?')} — lead kept, email flagged for rewrite", "warn")

    # Sentence check — flag for rewrite but do not penalize lead quality score
    if data["fit_score"] > 0:
        sc = count_sentences(data.get("email_body",""))
        if sc < 4 or sc > 10: data["_email_needs_regen"] = True

    # Banned word check
    if data["fit_score"] > 0:
        bl = data.get("email_body","").lower(); sl = data.get("email_subject","").lower()
        found = [w for w in BANNED_WORDS if w in bl or w in sl]
        if found:
            emit_log(f"Banned: {found[:4]} — fixing", "ai")
            try:
                fp = f'Rewrite removing: {", ".join(found)}.\nSUBJECT: {data["email_subject"]}\nBODY: {data["email_body"]}\nRespond ONLY JSON: {{"email_subject":"...","email_body":"..."}}'
                fr = client.chat.completions.create(**_ai_json_kwargs(model=_get_tier_model(),messages=[{"role":"system","content":"Rewrite as JSON only."},{"role":"user","content":fp}],temperature=0.3,max_tokens=1200))
                fj = extract_json((fr.choices[0].message.content or "").strip())
                if fj:
                    fd = json.loads(fj)
                    if fd.get("email_body") and len(fd["email_body"])>80: data["email_body"] = fd["email_body"].strip()
                    if fd.get("email_subject") and len(fd["email_subject"])>8: data["email_subject"] = fd["email_subject"].strip()
            except: data["fit_score"] = max(0, data["fit_score"]-1)

    return data


def _regen_email(data, page_text):
    try:
        HUNTOVA_CONTEXT = _build_ai_context()
        p = f"""{HUNTOVA_CONTEXT}

Write a cold outreach email for this specific lead. The email must feel hand-written and reference details from their actual page.

LEAD INTEL:
- Organisation: {data.get('org_name','?')}
- Event: {data.get('event_name','?')} ({data.get('event_type','?')})
- Country: {data.get('country','?')}
- Platform: {data.get('platform_used','?')}
- Production gap: {data.get('production_gap','?')}
- Recurring: {data.get('is_recurring', False)}
- Evidence: {data.get('evidence_quote','')[:200]}

PAGE CONTENT:
{page_text[:3000]}

REQUIREMENTS:
- 3-5 sentences, under 80 words. Start with THEIR situation, not who you are.
- Reference a specific detail from the page.
- Include a concrete offer (free mockup, audit, or introductory discount).
- Subject: max 6 words, specific to them.
- LinkedIn note: under 180 chars, personal and specific.
- NEVER use placeholders, filler phrases, or "...".

Respond ONLY with JSON: {{"email_subject":"...","email_body":"...","linkedin_note":"..."}}"""
        r = client.chat.completions.create(**_ai_json_kwargs(model=_get_tier_model(),messages=[{"role":"system","content":"You are an elite cold email writer. Every email must feel hand-crafted for this specific prospect. Output ONLY valid JSON."},{"role":"user","content":p}],temperature=0.55,max_tokens=1500))
        js = extract_json((r.choices[0].message.content or "").strip())
        if not js: return False
        d = json.loads(js)
        if d.get("email_body") and len(d["email_body"])>80 and "..." not in d.get("email_subject",""):
            data["email_subject"] = d["email_subject"].strip()
            data["email_body"] = d["email_body"].strip()
            if d.get("linkedin_note") and len(d["linkedin_note"])>20: data["linkedin_note"] = d["linkedin_note"].strip()
            emit_log("✅ Email regenerated successfully", "ok"); return True
        return False
    except: return False


# ───────────────────────────────────────────────────────────────
# SAVE CSV
# ───────────────────────────────────────────────────────────────
CSV_FIELDS = [
    "fit_score","org_name","event_name","country","city","region","platform_used",
    "is_recurring","frequency","contact_name","contact_role","contact_email",
    "contact_linkedin","org_linkedin","org_website","contact_page_url",
    "production_gap","why_fit","email_subject","email_body","linkedin_note","url"]

def save_csv(leads):
    if not leads: return
    if _ctx(): return  # SaaS mode: no local CSV writes
    ensure_dir(RUN_DIR)
    with open(OUTPUT_CSV, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_FIELDS, extrasaction="ignore")
        w.writeheader(); w.writerows(sorted(leads, key=lambda x: x.get("fit_score",0), reverse=True))
    emit_log(f"CSV: {OUTPUT_CSV}", "save")


# ───────────────────────────────────────────────────────────────
# EMERGENCY SAVE
# ───────────────────────────────────────────────────────────────
_all_leads, _save_done = [], False

def _emergency_save():
    global _save_done
    ctx = _ctx()
    _leads = ctx.all_leads if ctx else _all_leads
    _sd = getattr(ctx, '_save_done', False) if ctx else _save_done
    if _sd or not _leads: return
    if ctx: ctx._save_done = True
    else: _save_done = True
    try:
        ensure_dir(RUN_DIR); save_csv(_leads); save_master_leads(_leads)
        save_seen_history(); save_domain_blocklist()
        emit_log(f"Emergency save: {len(_leads)} leads", "save")
    except Exception as e: emit_log(f"Emergency save failed: {e}", "error")

def _sig(s, f):
    emit_log("Interrupted — saving...", "warn"); _emergency_save(); sys.exit(0)

if _STANDALONE:
    signal.signal(signal.SIGINT, _sig); signal.signal(signal.SIGTERM, _sig)
    atexit.register(_emergency_save)


# ───────────────────────────────────────────────────────────────
# MAIN
# ───────────────────────────────────────────────────────────────
def run_agent():
    """Agent main loop — runs in background thread."""

    ensure_dir(RUN_DIR)
    ensure_dir(LOG_DIR)
    # Create timestamped session log
    _session_log = os.path.join(LOG_DIR, f"session_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
    _ctx().current_session_log = _session_log  # Route session log through ctx
    try:
        with open(_session_log, "w", encoding="utf-8") as _lf:
            _lf.write(f"=== Huntova Agent Session — {datetime.now().isoformat()} ===\n")
            _lf.write(f"Model: {MODEL_ID}\n\n")
    except Exception: pass
    emit_status("Checking AI engine…", "running")
    emit_thought("Getting ready to hunt...", "boot")

    if AI_PROVIDER == "local":
        if not check_lm_studio():
            emit_status("LM Studio not running", "idle"); emit_log("AI engine not available", "error"); return
        emit_log(f"✅ AI engine ready", "ok")
    else:
        _tm = _get_tier_model()
        _ctx_tier = _ctx().user_tier if _ctx() else "standalone"
        _model_label = "Gemini Pro" if "pro" in _tm.lower() else "Gemini Flash"
        # Verify Gemini API is actually reachable before running
        try:
            _health = client.chat.completions.create(model=_tm, messages=[{"role":"user","content":"respond with OK"}], max_tokens=5, temperature=0)
            if _health and _health.choices:
                emit_log(f"{_model_label} AI ready", "ok")
            else:
                emit_log("AI engine returned empty response — cannot start", "error")
                emit_status("AI service unavailable", "idle"); return
        except Exception as _ae:
            emit_log(f"AI engine not responding: {str(_ae)[:100]} — cannot start", "error")
            emit_status("AI service unavailable", "idle"); return
    emit_status("Checking search engine…", "running")
    _has_searxng = check_searxng()
    if _has_searxng:
        emit_log(f"Search engine connected", "ok")
    else:
        emit_log(f"Search engine not responding — cannot start", "error")
        emit_status("Search engine offline", "idle")
        return
    emit_status("Loading your hunt profile…", "running")

    # Only load from files in standalone mode — in SaaS mode, run_agent_scoped() already
    # loaded per-user state from DB into the globals. Loading from files would clobber it.
    if not _ctx():
        load_seen_history(); load_domain_blocklist()

    # ── Normalize and validate wizard profile before hunting ──
    _wiz_data = load_settings().get("wizard", {})
    _sel_countries = _get_agent_config().get("countries", [])

    # Single reusable event loop for all DB calls in run_agent (CLAUDE.md
    # rule #8). Was previously 5 separate loops in this function — each
    # one leaks an epoll/kqueue fd until close, and over enough Railway
    # redeploys we hit EMFILE. Closed at the bottom of run_agent.
    import asyncio as _aio_agent
    _agent_loop = _aio_agent.new_event_loop()

    # Inject learning profile into wizard context for AI scoring
    # Stability fix (multi-agent bug #13): the previous version
    # referenced `ctx.user_id` before `ctx` was defined in this function,
    # so the NameError was silently swallowed by the bare except below
    # and the learning profile NEVER actually loaded. Fixed by reading
    # _ctx() locally.
    try:
        _lp_ctx = _ctx()
        if _lp_ctx:
            import db as _db_lp
            _lp = _agent_loop.run_until_complete(_db_lp.get_learning_profile(_lp_ctx.user_id))
            if _lp and _lp.get("instruction_summary"):
                _wiz_data["_learning_instructions"] = _lp["instruction_summary"]
                emit_log("Loaded learning profile v" + str(_lp.get("version", 1)), "ok")
    except Exception as _lp_err:
        print(f"[AGENT] Learning profile load error: {_lp_err}")

    # Load saved hunt brain — require it for production hunts
    _brain = _wiz_data.get("normalized_hunt_profile")
    _dossier_saved = _wiz_data.get("training_dossier")
    if not _brain or _brain.get("hunt_brain_version", 0) < 1:
        # No saved brain — block hunt in SaaS mode
        if _ctx():
            emit_log("Complete the setup wizard before running the agent.", "error")
            emit_status("Setup required — complete the wizard first", "idle")
            emit_thought("I need to learn about your business first. Open the wizard to get started.", "idle")
            try: _agent_loop.close()
            except Exception: pass
            return
        # Standalone mode: allow runtime rebuild
        _brain = _build_hunt_brain(_wiz_data)
        _brain["source"] = "runtime_rebuild"
        emit_log("Built hunt brain at runtime (standalone mode)", "info")
    else:
        emit_log(f"Business profile loaded", "ok")
    if _dossier_saved:
        emit_log(f"Targeting strategy loaded", "ok")

    # Log brain info to server console only (not user-facing)
    print(f"[AGENT] Brain: archetype={_brain.get('archetype','?')} conf={_brain.get('profile_confidence',0)} services={_brain.get('services_clean',[])} industries={_brain.get('preferred_industries',[])} roles={_brain.get('buyer_roles_clean',[])}")

    # Log dossier if available
    # ── Load acceptance spec from dossier (or defaults) ──
    _dossier = _wiz_data.get("training_dossier")
    # In lightweight mode (no Playwright), the AI can't verify contacts or deeply
    # investigate pages, so buyability/reachability scores are unreliable.
    # Use relaxed thresholds — let more leads through, user decides quality.
    _is_lightweight = not HAS_PLAYWRIGHT
    _accept_spec = {
        "fit_threshold": 5,
        "buyability_threshold": 2 if _is_lightweight else 3,
        "reachability_threshold": 1,
        "hard_rejects": [],
    }
    if _dossier and _dossier.get("training_dossier_version", 0) >= 1:
        _d_arch = _dossier.get("archetype", "?")
        _d_sell = _dossier.get("business_identity", {}).get("what_they_sell", "?")[:60]
        _d_buyer = _dossier.get("ideal_customer_profile", {}).get("buyer_roles", [])
        _d_conf = _dossier.get("confidence", {}).get("overall", 0)
        print(f"[AGENT] Dossier v{_dossier.get('training_dossier_version',0)}: {_d_arch} | sells: {_d_sell} | buyers: {_d_buyer[:3]} | conf: {_d_conf}")
        # Use dossier acceptance spec — but cap to lightweight-friendly values
        _das = _dossier.get("acceptance_spec", {})
        _accept_spec["hard_rejects"] = _das.get("hard_rejects", [])
        # Enforce the stricter of dossier archetype thresholds and defaults
        if _das.get("fit_threshold"):
            _accept_spec["fit_threshold"] = max(_das["fit_threshold"], _accept_spec["fit_threshold"])
        if _das.get("buyability_threshold"):
            _accept_spec["buyability_threshold"] = max(_das["buyability_threshold"], _accept_spec["buyability_threshold"])
        if _das.get("reachability_threshold"):
            _accept_spec["reachability_threshold"] = max(_das["reachability_threshold"], _accept_spec["reachability_threshold"])
    emit_log(f"Quality filters set", "ok")
    print(f"[AGENT] Acceptance: fit>={_accept_spec['fit_threshold']} buy>={_accept_spec['buyability_threshold']} reach>={_accept_spec['reachability_threshold']} rejects={len(_accept_spec['hard_rejects'])}")

    # Quality gate
    if not _brain.get("can_hunt", True):
        _blocking = _brain.get("blocking_flags", [])
        emit_log(f"Cannot start: profile too weak ({', '.join(_blocking)}). Complete the Huntova wizard first.", "error")
        emit_status("Profile incomplete — retrain first", "idle")
        emit_thought("I need more information about your business before I can find good leads.", "idle")
        try: _agent_loop.close()
        except Exception: pass
        return

    # Additional strictness: require at least 2 of services/industries/roles
    _has_svc = bool(_brain.get("services_clean"))
    _has_ind = bool(_brain.get("preferred_industries"))
    _has_roles = bool(_brain.get("buyer_roles_clean"))
    if sum([_has_svc, _has_ind, _has_roles]) < 2:
        emit_log("Profile too thin: need at least services + industries OR services + buyer roles. Retrain.", "error")
        emit_status("Profile too thin — retrain first", "idle")
        try: _agent_loop.close()
        except Exception: pass
        return

    for _wf in _brain.get("warning_flags", []):
        print(f"[AGENT] Profile warning: {_wf}")  # Server-side only

    # Use brain fields for query generation instead of raw wizard data
    # Inject brain into wiz_data so existing code reads clean fields
    _wiz_data["services"] = _brain.get("services_clean", _wiz_data.get("services", []))
    _wiz_data["icp_industries"] = _brain.get("preferred_industries", [])
    _wiz_data["buyer_roles"] = _brain.get("buyer_roles_clean", [])
    _wiz_data["triggers"] = _brain.get("triggers_clean", [])
    _wiz_data["exclusions"] = _brain.get("exclusions_clean", [])
    _wiz_data["business_description"] = _brain.get("offer_summary", _wiz_data.get("business_description", ""))

    # ── Determine tier and batch limits BEFORE query generation ──
    _rpp = _get_agent_config().get("results_per_query", MAX_RESULTS_PER_QUERY)
    _tier = "standalone"
    ctx = _ctx()
    if ctx:
        _db2 = __import__("db")
        _user = _agent_loop.run_until_complete(_db2.get_user_by_id(ctx.user_id))
        _tier = _user.get("tier", "free") if _user else "free"
        _credits = _agent_loop.run_until_complete(_db2.check_and_reset_credits(ctx.user_id))
        emit_log(f"{_credits} credits available", "ok")
    _batch_limits = {"free": (10, 5), "growth": (25, 50), "agency": (40, 100), "standalone": (25, 20)}
    _batch_size, max_batches = _batch_limits.get(_tier, (25, 8))

    # ── Load Agent DNA (the intelligence profile) ──
    _agent_dna = None
    ctx = _ctx()
    if ctx:
        try:
            _db_dna = __import__("db")
            _agent_dna = _agent_loop.run_until_complete(_db_dna.get_agent_dna(ctx.user_id))
        except Exception as _dna_err:
            print(f"[AGENT] DNA load error: {_dna_err}")
            _agent_dna = None
    # Always regenerate DNA at start of each run to use latest prompt/profile
    if ctx and _wiz_data.get("company_name"):
        emit_log("Generating search queries from your profile...", "info")
        try:
            _dna = generate_agent_dna(_wiz_data)
            if _dna and _dna.get("search_queries"):
                _agent_dna = _dna
                try:
                    _agent_loop.run_until_complete(_db_dna.save_agent_dna(ctx.user_id, _dna))
                except Exception as _save_err:
                    print(f"[AGENT] DNA save error: {_save_err}")
        except Exception as _gen_err:
            print(f"[AGENT] DNA generation error: {_gen_err}")
    # Cache DNA on ctx
    if ctx:
        ctx._cached_dna = _agent_dna
    if _agent_dna and _agent_dna.get("search_queries"):
        _dna_qcount = len(_agent_dna.get('search_queries',[]))
        emit_log(f"🔍 {_dna_qcount} custom search queries ready", "ok")
    else:
        emit_log("No custom queries — using AI-generated queries", "info")

    # ── Generate queries ──
    queries = []

    # PRIMARY: Agent DNA queries — specifically generated for this user's ICP
    if _agent_dna and _agent_dna.get("search_queries"):
        _dna_queries = [q for q in _agent_dna["search_queries"] if isinstance(q, str) and len(q) > 5]
        if len(_dna_queries) >= 5:
            queries = _dna_queries
            pass  # Already logged above

    # SECONDARY: AI generation as fallback
    if len(queries) < 5 and _brain.get("profile_confidence", 0) >= 30:
        for _ai_attempt in range(2):
            try:
                _ai_queries = generate_queries_ai(_wiz_data, _sel_countries, 50)
                if _ai_queries and len(_ai_queries) >= 5:
                    queries = _ai_queries
                    emit_log(f"AI generated {len(queries)} queries (attempt {_ai_attempt+1})", "ok")
                    break
            except Exception as _qe:
                emit_log(f"AI query gen attempt {_ai_attempt+1} failed: {_qe}", "warn")

    # TERTIARY: Brain-based templates
    if len(queries) < 5 and _brain.get("hunt_brain_version", 0) >= 1:
        _brain_queries = _generate_brain_queries(_brain, _sel_countries, _batch_size * 2)
        if _brain_queries:
            queries = _brain_queries
            emit_log(f"Using {len(queries)} brain queries as fallback", "ok")

    # LAST RESORT: structured fallback
    if len(queries) < 5:
        queries = _fallback_queries(_wiz_data, _sel_countries)
        if len(queries) > 40:
            queries = queries[:40]
        emit_log(f"Using {len(queries)} structured fallback queries", "info")
    
    # Sort queries by historical yield (best-performing first), then shuffle within tiers
    queries = _sort_queries_by_yield(queries)
    # Light shuffle within chunks of 10 to add variety without burying good queries
    for i in range(0, len(queries), 10):
        chunk = queries[i:i+10]
        random.shuffle(chunk)
        queries[i:i+10] = chunk

    # Cap queries to batch size (tier already determined above)
    if len(queries) > _batch_size:
        queries = queries[:_batch_size]

    # Plugin pre_search hook — let plugins mutate the query list (add
    # niche-specific queries, strip out blacklisted ones, etc.) before
    # we burn AI tokens scoring whatever they return. Plugin errors
    # are isolated by the registry so a buggy plugin can't kill the
    # hunt; we just keep the original queries.
    try:
        from plugins import get_registry, HookContext
        _reg = get_registry()
        if not getattr(_reg, "_discovered_at_startup", False):
            try:
                _reg.discover()
                _reg._discovered_at_startup = True
            except Exception:
                pass
        # Plugins read top-level user_settings keys (plugin_csv_sink_path,
        # webhook_url, smtp_*, slack-ping config). Pass the full settings
        # dict, not just the wizard nested object — otherwise csv-sink,
        # slack-ping, and the webhook plugin all silently no-op even
        # when the user has them configured in the dashboard.
        _hook_ctx = HookContext(
            settings=load_settings() or {},
            provider_name=os.environ.get("HV_AI_PROVIDER", "gemini"),
            user_id=getattr(_ctx(), "user_id", None),
            meta={"phase": "pre_search"},
        )
        _new = _reg.run("pre_search", _hook_ctx, queries)
        if isinstance(_new, list) and _new:
            queries = _new[:max(_batch_size, len(_new))]
            emit_log(f"Plugins shaped query list — {len(queries)} queries after pre_search hook", "info")
    except Exception as _phk_err:
        # Never let plugin discovery problems break a hunt.
        emit_log(f"plugin pre_search failed: {_phk_err}", "warn")
    batch_num = 1
    _used_queries = set(q for q in queries)
    _stop_reason = "complete"
    _consecutive_dry_batches = 0
    _total_urls_checked = 0  # SERP results seen
    _pages_analysed = 0     # pages actually fetched and AI-scored
    _exploration_phase = 0
    _EXPLORATION_PHASES = [
        "direct_icp",        # Phase 0: direct ICP + service queries
        "adjacent_problems", # Phase 1: adjacent service/problem queries
        "buyer_roles",       # Phase 2: buyer role + pain queries
        "directories",       # Phase 3: directory/association/platform traces
        "growth_signals",    # Phase 4: growth/procurement/expansion signals
        "localized",         # Phase 5: localized/native-language variants
        "broadened",         # Phase 6: broadened/relaxed queries
    ]
    _MIN_URLS_BEFORE_EXHAUST = 50  # minimum URLs to check before allowing exhaustion
    _MIN_PHASES_BEFORE_EXHAUST = 3  # minimum exploration phases before allowing exhaustion
    # Adaptive learning: track which queries produced leads vs junk
    _successful_patterns = []  # query strings that produced at least 1 accepted lead
    _failed_patterns = []      # query strings that produced 0 leads
    _current_query = ""        # track which query is currently running
    _query_lead_count = 0      # leads found from current query
    emit_log(f"🚀 Starting hunt — {len(queries)} searches queued", "ok")
    emit_thought("Starting the hunt! Let's find some leads.", "ready")

    # ── Build ICP keyword set for relevance gating (cached for this run) ──
    # Two-tier system: exact phrases (high confidence) + single keywords (broad net)
    _ik = set()       # Single keywords
    _ik_phrases = set()  # Multi-word phrases (more precise, count as 2 hits each)
    for ind in _wiz_data.get("icp_industries", _wiz_data.get("industries_served", [])):
        ind_clean = ind.lower().strip()
        if len(ind_clean) > 5: _ik_phrases.add(ind_clean)
        for w in ind_clean.split("/"):
            for ww in w.split():
                if len(ww) > 3: _ik.add(ww.strip())
    for phrase in [_wiz_data.get("buying_signals", ""), _wiz_data.get("target_clients", ""),
                   _wiz_data.get("web_discovery_pages", ""), _wiz_data.get("business_description", "")]:
        for w in (phrase or "").lower().split():
            if len(w) > 4: _ik.add(w)
    # New: Add buyer search terms as high-confidence phrases
    for bt in (_wiz_data.get("buyer_search_terms", "") or "").lower().split(","):
        bt = bt.strip().strip('"').strip("'")
        if len(bt) > 5: _ik_phrases.add(bt)
        for w in bt.split():
            if len(w) > 4: _ik.add(w)
    # New: Add past client types and hiring signals as keywords
    for field in ["past_clients", "hiring_signals"]:
        for w in (_wiz_data.get(field, "") or "").lower().split():
            if len(w) > 4: _ik.add(w)
    for ind in _brain.get("preferred_industries", []):
        ind_clean = ind.lower().strip()
        if len(ind_clean) > 5: _ik_phrases.add(ind_clean)
        for w in ind_clean.split():
            if len(w) > 3: _ik.add(w)
    for role in _brain.get("buyer_roles_clean", []):
        role_clean = role.lower().strip()
        if len(role_clean) > 5: _ik_phrases.add(role_clean)
        for w in role_clean.split():
            if len(w) > 4: _ik.add(w)
    for svc in _brain.get("services_clean", []):
        svc_clean = svc.lower().strip()
        if len(svc_clean) > 5: _ik_phrases.add(svc_clean)
        for w in svc_clean.split():
            if len(w) > 4: _ik.add(w)
    # Remove generic words that match anything
    _ik -= {"company","business","service","services","team","work","about","contact",
            "help","make","more","they","with","that","this","have","from","their",
            "your","will","been","other","what","which","when","where","there","would",
            "could","should","just","also","into","only","most","some","than","very",
            "even","over","such","after","before","people","based","using","these",
            "those","being","first","years","management","solutions","offer","provides",
            "global","world","leading","every","under","through","between","while"}
    run_agent._icp_keywords = _ik
    run_agent._icp_phrases = _ik_phrases
    if _ik:
        emit_log(f"Quality filters set", "ok")
        print(f"[AGENT] ICP gate active: {len(_ik)} keywords — {sorted(list(_ik))[:25]}...")
    else:
        print("[AGENT] WARNING: No ICP keywords — relevance gate disabled")

    # ── Use saved wizard/business profile as source of truth ──
    # Old: did a weak urllib scan here. Now: relies on the wizard scan done during onboarding.
    _wiz_check = load_settings().get("wizard", {})
    if _wiz_check.get("_site_context") or _wiz_check.get("business_description"):
        pass  # Already logged above
    else:
        emit_log("No business profile found — results may be generic. Complete the wizard first.", "warn")

    # Resume support — skip already-processed queries
    _resume_qi = _get_agent_config().pop("resume_from_query", 0)
    if _resume_qi > 0:
        queries = queries[_resume_qi:]
        emit_log(f"Resuming from query {_resume_qi} ({len(queries)} remaining)", "ok")

    # Pre-run backup + agent state
    try: do_backup("pre_run")
    except: pass
    _agent_state = {"run_id": _run_ts, "started_at": datetime.now(timezone.utc).isoformat(),
                    "total_queries": len(queries), "status": "running", "last_query_index": 0, "leads_found": 0}
    try: _atomic_write(AGENT_STATE_FILE, _agent_state)
    except: pass

    urls_seen = skipped = 0; start = time.time()
    # _agent_loop was already created above at the top of run_agent's
    # prelude — reused here for all per-lead DB calls (avoids creating
    # 200+ loops). Single source of truth, single fd.
    # Clear lead buffer for this run
    _ctx().all_leads.clear()
    _ctx()._save_done = False; _found_domains = {}

    # Use Playwright if available, otherwise fall back to requests
    _pw_ctx = None; browser = None; page = None
    if HAS_PLAYWRIGHT:
        try:
            _pw_ctx = sync_playwright().start()
            browser = _pw_ctx.chromium.launch(headless=True)
            page = browser.new_page(); page.set_extra_http_headers({"User-Agent": USER_AGENT})
            pass  # Don't show browser engine to user
        except Exception as _pw_err:
            emit_log(f"Playwright unavailable ({_pw_err}), using lightweight mode", "warn")
            # Stability fix (multi-agent bug #22): if start() succeeded
            # but launch()/new_page() failed, _pw_ctx was being set to
            # None without stopping it — leaking the Playwright supervisor
            # process and its IPC. Tear down whatever we managed to bring
            # up before nilling the handles.
            if browser is not None:
                try: browser.close()
                except Exception: pass
            if _pw_ctx is not None:
                try: _pw_ctx.stop()
                except Exception: pass
            _pw_ctx = None; browser = None; page = None
    else:
        pass  # Don't show internal browser mode to user

    # Stability fix (round-3 multi-agent + round-7 Perplexity approval):
    # the outer batch loop now runs inside try/finally so a crash does NOT
    # leak Chromium subprocesses. Cleanup at the bottom is moved into the
    # finally branch — same calls, just guaranteed to run on exception paths.
    try:

      while True:  # ── outer batch loop ──
        _batch_start_leads = len(_ctx().all_leads)
        for qi, query in enumerate(queries, 1):
            if _check_pause() or _check_stop():
                emit_thought("Stopping. All leads saved.", "idle")
                emit_log("Stopped by user", "warn"); _stop_reason = "stopped"; emit_status("Stopped", "stopped"); break
            # Hunt budget gate — user-set max_leads / timeout_minutes
            # caps from the Start Hunt popup. Empty/unset = unlimited
            # (existing default behaviour). emit_status is fired inside
            # _check_budget so the UI surfaces the reason for the stop.
            _bud = _check_budget()
            if _bud:
                emit_thought("Budget reached. All leads saved.", "idle")
                _stop_reason = _bud; break

            _current_query = query
            _query_lead_count = 0
            _batch_label = f"B{batch_num} " if batch_num > 1 else ""
            short = (query[:50]+"...") if len(query)>50 else query
            emit_log(f"🔎 {_batch_label}Query {qi}/{len(queries)}: {short}", "info")
            emit_thought("Searching: " + short, "search")

            we = sum(1 for l in _ctx().all_leads if l.get("contact_email"))
            rc = sum(1 for l in _ctx().all_leads if l.get("is_recurring"))
            emit_progress(current=qi,total=len(queries),urls=urls_seen,leads=len(_ctx().all_leads),
                          skipped=skipped,with_email=we,recurring=rc)
            emit_status(f"{_batch_label}Query {qi}/{len(queries)} · {len(_ctx().all_leads)} leads", "running")

            # Freshness-first: try recent results first, fall back to all-time
            results = search(query, _rpp, time_range="year")
            if len(results) < max(3, _rpp // 2):
                # Not enough recent results — broaden to all time
                _all_time = search(query, _rpp)
                # Merge without duplicates (use normalized URLs for consistency)
                _seen_r = {normalize_url(r.url) for r in results}
                for _atr in _all_time:
                    _norm = normalize_url(_atr.url)
                    if _norm not in _seen_r:
                        results.append(_atr)
                        _seen_r.add(_norm)
                    if len(results) >= _rpp:
                        break
            if not results:
                # Query reformulation: simplify long queries that return nothing
                _words = query.split()
                if len(_words) > 4:
                    # Try a shorter version — drop the last 1-2 words
                    _shorter = " ".join(_words[:max(3, len(_words) - 2)])
                    results = search(_shorter, _rpp)
                    if results:
                        emit_log(f"🔄 Reformulated: '{short}' → '{_shorter[:40]}' ({len(results)} results)", "info")
                if not results:
                    emit_log(f"⏭ No results for query {qi}/{len(queries)}: {short}", "skip")
                    time.sleep(max(DELAY_QUERY, 1.5))
                    continue

            # Plugin post_search hook (round-69 Kimi spec): dedup,
            # filter, enrich raw search results before the agent
            # spends AI tokens scoring them. The bundled
            # dedup-by-domain plugin lives here — silently dead code
            # without this call. Plugin errors caught by the registry
            # so a buggy plugin can't kill the hunt.
            try:
                from plugins import get_registry, HookContext as _HC
                _post_results = [{"url": r.url, "title": r.title, "snippet": r.snippet} for r in results]
                _filtered = get_registry().run("post_search", _HC(
                    settings=load_settings() or {},
                    provider_name=os.environ.get("HV_AI_PROVIDER", "gemini"),
                    user_id=getattr(_ctx(), "user_id", None),
                    meta={"phase": "post_search", "query": query},
                ), _post_results)
                if isinstance(_filtered, list) and len(_filtered) != len(_post_results):
                    # Plugins dropped some — rebuild results to match
                    _kept_urls = {r.get("url") for r in _filtered if isinstance(r, dict)}
                    results = [r for r in results if r.url in _kept_urls]
                    emit_log(f"Plugin post_search filtered to {len(results)} results", "info")
            except Exception as _phk2:
                emit_log(f"plugin post_search failed: {_phk2}", "warn")

            emit_log(f"📋 {len(results)} results for: {short}", "info")
            stopped = False
            _q_blocked = 0; _q_seen = 0; _q_fetched = 0
            for r in results:
                # Per-URL budget probe — fires near-instantly on user-set
                # max_leads / timeout caps so a long deep_qualify pass
                # doesn't stretch a 5-min cap into 15+ min. Outer query
                # loop sees _stop_reason and exits before the next query.
                _bud_url = _check_budget()
                if _bud_url:
                    emit_thought("Budget reached. All leads saved.", "idle")
                    _stop_reason = _bud_url; break
                url = normalize_url(r.url)
                if url in _ctx().seen_urls:
                    _q_seen += 1; continue
                if is_blocked(url):
                    _blocked_domain = urlparse(url).netloc.lower()
                    print(f"[AGENT] Blocked domain: {_blocked_domain} from {url[:80]}")
                    _q_blocked += 1; continue
                _ctx().seen_urls.add(url); urls_seen += 1
                # Incremental seen-URL persistence: save every 20 new URLs
                if urls_seen % 20 == 0:
                    try:
                        _new_batch = _ctx().seen_urls - _ctx()._initial_seen
                        if _new_batch:
                            import db as _db_seen
                            # Stability fix (bug #13 follow-up): was creating a
                            # fresh event loop every 20 URLs — under a long
                            # run that's tens of leaked epoll/kqueue fds.
                            # Reuse the shared _agent_loop instead.
                            _agent_loop.run_until_complete(_db_seen.add_seen_urls_bulk(_ctx().user_id, list(_new_batch)))
                            _ctx()._initial_seen.update(_new_batch)
                    except Exception:
                        pass

                _total_urls_checked += 1

                # ── SERP-level pre-filter: kill obvious collisions before fetching ──
                _serp_text = ((r.title or "") + " " + (r.snippet or "")).lower()
                _serp_domain = urlparse(url).netloc.lower()

                # Language gate: reject results with non-Latin domain TLDs or heavy non-Latin text
                _non_english_tlds = (".ru", ".by", ".ua", ".kz", ".uz", ".az", ".ge", ".am",
                                     ".kg", ".tj", ".md", ".cn", ".kr", ".jp", ".ir", ".sa")
                if any(_serp_domain.endswith(tld) for tld in _non_english_tlds):
                    print(f"[AGENT] SERP reject non-English TLD: {_serp_domain}")
                    skipped += 1; _q_blocked += 1; continue
                # Check snippet for non-Latin characters
                _serp_combined = (r.title or "") + " " + (r.snippet or "")
                _non_latin_chars = sum(1 for c in _serp_combined
                    if '\u0400' <= c <= '\u04FF' or '\u4E00' <= c <= '\u9FFF'
                    or '\u0600' <= c <= '\u06FF' or '\uAC00' <= c <= '\uD7AF')
                if len(_serp_combined) > 10 and _non_latin_chars / len(_serp_combined) > 0.2:
                    print(f"[AGENT] SERP reject non-English content: {_serp_domain}")
                    skipped += 1; _q_blocked += 1; continue

                # Brand/entity disambiguation: famous unrelated brands
                _brand_collisions = {
                    "enterprise": ["enterprise rent", "enterprise car", "enterprise.com/rent", "rental"],
                    "virtual": ["virtualdj", "virtual dj", "virtual reality", "vr headset", "virtual machine", "vmware"],
                    "event": ["eventbrite.com", "event planning tool", "event management software", "tourism calendar"],
                }
                _serp_rejected = False
                for _term, _collisions in _brand_collisions.items():
                    if _term in query.lower():
                        if any(c in _serp_text or c in _serp_domain for c in _collisions):
                            _serp_rejected = True
                            break

                # Generic junk result types — ONLY block things that are DEFINITELY not leads
                _junk_serp_signals = [
                    # Auth/account pages
                    "login", "sign in", "create account", "forgot password",
                    # App/download
                    "download app", "app store", "google play",
                    # Reviews/ratings
                    "write a review", "tripadvisor", "trustpilot",
                    # Forums/community
                    "forum thread", "discussion board", "reddit.com",
                    # Reference/wiki
                    "wikipedia.org", "dictionary", "wiktionary",
                    # NOTE: "career", "free trial", "pricing plans" REMOVED — these are legitimate prospect signals
                    # Government/public services
                    "usps.com", "post office", "government service", "public notice",
                    "irs.gov", "hmrc", "tax filing",
                    # NOTE: "webinar recording", "past webinars" REMOVED — these indicate prospects with events
                    # Store locators / consumer
                    "store locator", "find a store", "nearest location", "opening hours",
                    "add to cart", "buy now", "shop now", "checkout",
                ]
                # Also reject by domain pattern — common non-prospect domains
                _junk_domains = [
                    "techradar.com", "zdnet.com", "theverge.com", "engadget.com",
                    "usps.com", "post.ch", "bpost.be", "poste.it",
                    "coursera.org", "udemy.com", "skillshare.com",
                    "yelp.com", "trustpilot.com", "g2.com", "capterra.com",
                    "crunchbase.com", "pitchbook.com", "owler.com",
                    # Financial / investor relations
                    "seekingalpha.com", "marketwatch.com", "benzinga.com",
                    "prnewswire.com", "businesswire.com", "globenewswire.com",
                    "rttnews.com", "morningstar.com", "fool.com", "barrons.com",
                    # Ticket / entertainment aggregators
                    "ticketmaster.com", "stubhub.com", "viagogo.com",
                    "bandsintown.com", "songkick.com", "seetickets.com",
                    "fnacspectacles.com", "infoconcert.com", "eventcartel.com",
                    # Gaming
                    "steamcommunity.com", "steampowered.com", "ign.com",
                    # Consumer brands (never B2B prospects)
                    "on-running.com", "casio.com", "imdb.com",
                    # Academic
                    "researchgate.net", "academia.edu",
                ]
                if not _serp_rejected:
                    if any(sig in _serp_text for sig in _junk_serp_signals):
                        _serp_rejected = True
                    elif any(jd in _serp_domain for jd in _junk_domains):
                        _serp_rejected = True

                # ── Prospect-vs-informational classifier ──
                if not _serp_rejected:
                    _info_signals = [
                        "what is ", "what are ", "definition of ", "meaning of ",
                        "how to ", "guide to ", "introduction to ", "explained",
                        "everything you need to know", "complete guide",
                        "tips for ", "ways to ", "steps to ", "how do ",
                        "vs ", " versus ", " comparison",
                        "salary", "pay scale", "compensation",
                    ]
                    # "top 10"/"list of"/"best of" are USEFUL for service agencies
                    # — they're prospect lists (e.g. "top 10 conferences in Europe")
                    _arch_info = _brain.get("archetype", "other") if _brain else "other"
                    if _arch_info not in ("service_agency",):
                        _info_signals.extend(["top 10 ", "top 20 ", "best of ", "list of "])
                    if any(sig in _serp_text for sig in _info_signals):
                        _serp_rejected = True
                    # Domain-type heuristic: publisher/media/reference sites
                    _publisher_domains = [
                        "medium.com", "substack.com", "hubspot.com/blog",
                        "forbes.com", "inc.com", "entrepreneur.com",
                        "britannica.com", "investopedia.com", "healthline.com",
                        "webmd.com", "mayoclinic.org",
                        "nerdwallet.com", "bankrate.com",
                    ]
                    if any(pd in _serp_domain for pd in _publisher_domains):
                        _serp_rejected = True

                # ── Archetype-specific result filter ──
                if not _serp_rejected and _brain.get("hunt_brain_version", 0) >= 1:
                    _arch = _brain.get("archetype", "other")
                    if _arch == "consultant":
                        # Reject definitions, journals, explainers, media articles
                        _consult_reject = ["what is consulting", "consulting definition", "types of consulting",
                                          "journal of", "academic paper", "research paper", "case study template"]
                        if any(cr in _serp_text for cr in _consult_reject):
                            _serp_rejected = True
                    elif _arch == "recruiter":
                        # Reject job board LISTINGS (we want companies who are hiring, not individual job posts)
                        _recruit_reject = ["apply now", "submit resume", "job application",
                                          "salary range", "benefits package", "remote position available"]
                        if any(rr in _serp_text for rr in _recruit_reject):
                            _serp_rejected = True
                    elif _arch == "software":
                        # Reject review/comparison editorial content
                        _sw_reject = ["review of", "product review", "honest review",
                                     "best alternatives", "top alternatives", "vs comparison",
                                     "g2 crowd", "capterra review", "software advice"]
                        if any(sr in _serp_text for sr in _sw_reject):
                            _serp_rejected = True
                    elif _arch == "professional_firm":
                        # Reject legal/accounting news, journal content
                        _pf_reject = ["law review", "legal journal", "accounting today",
                                     "new regulation", "legislative update", "court ruling"]
                        if any(pf in _serp_text for pf in _pf_reject):
                            _serp_rejected = True
                    elif _arch == "service_agency":
                        # Reject tool vendors and platform marketing pages — but NOT event pages
                        # Event/conference pages ARE the prospects we want to find
                        _sa_reject = [
                            "software pricing", "platform features", "start free trial",
                            "tool comparison", "marketing platform review",
                            "event management platform", "event registration platform",
                            "call for papers", "submit abstract", "conference proceedings",
                        ]
                        _sa_reject_domains = [
                            "conferencealerts.com", "allconferences.com", "conferenceindex.org",
                            "10times.com", "meetup.com", "lanyrd.com",
                            "papercall.io", "easychair.org",
                        ]
                        if any(sa in _serp_text for sa in _sa_reject):
                            _serp_rejected = True
                        elif any(sd in _serp_domain for sd in _sa_reject_domains):
                            _serp_rejected = True

                if _serp_rejected:
                    print(f"[AGENT] SERP reject: {urlparse(url).netloc.lower()} title='{(r.title or '')[:50]}'")
                    skipped += 1; _q_blocked += 1
                    continue

                # ── SERP-level ICP relevance check (before crawling) ──
                # If title+snippet has ZERO overlap with user's ICP keywords/phrases, skip
                if not _serp_rejected and hasattr(run_agent, '_icp_keywords') and run_agent._icp_keywords:
                    _serp_icp_hits = sum(1 for kw in run_agent._icp_keywords if kw in _serp_text)
                    _serp_phrase_hits = 0
                    if hasattr(run_agent, '_icp_phrases'):
                        _serp_phrase_hits = sum(1 for ph in run_agent._icp_phrases if ph in _serp_text)
                    if _serp_icp_hits == 0 and _serp_phrase_hits == 0:
                        print(f"[AGENT] SERP ICP reject (0 hits): {_serp_domain} '{(r.title or '')[:50]}'")
                        skipped += 1; _q_blocked += 1
                        continue

                # Pre-filter: skip obvious junk from title/snippet before loading page
                _title_low = (r.title or "").lower()
                _snip_low = (r.snippet or "").lower()
                _combined = _title_low + " " + _snip_low
                # Pre-filter: only skip things that are DEFINITELY not leads
                _skip_signals = ["recruitment agency",
                    "terms of service","privacy policy","cookie policy","login page","sign in to continue",
                    "404 not found","page not found","access denied",
                    "podcast episode","listen on spotify","listen on apple",
                    "press release archive",
                    "buy now","add to cart","shop now","pricing page",
                    "domain for sale","parked domain","website expired",
                    "account suspended","under construction","coming soon",
                    "download our app","app store","google play",
                    "write a review","leave a review","customer reviews",
                    "comparison chart","vs alternative","top 10 best",
                    "affiliate disclosure","sponsored post","advertisement",
                    # Financial / investor relations (not prospects)
                    "investor day","earnings call","shareholder meeting",
                    "quarterly results","annual report","sec filing",
                    "stock price","dividend",
                    # Consumer entertainment (not B2B)
                    "concert tickets","buy tickets","tour dates","album review",
                    "movie review","game review","tv show",
                    "running shoes","athletic shoes","sportswear",
                    # Recipe/lifestyle (SearXNG noise)
                    "recipe","cooking instructions","ingredients list"]
                if any(sig in _combined for sig in _skip_signals):
                    emit_log(f"(PRE-SKIP: {r.title[:50]})", "skip"); skipped += 1; continue  # no sleep — no network hit

                # Domain dedup: skip if we already have 2+ leads from this domain (allow related pages)
                _page_domain = urlparse(url).netloc.lower().replace("www.","")
                _domain_count = _found_domains.get(_page_domain, 0)
                if _domain_count >= 2 and _page_domain not in ("youtube.com","linkedin.com","eventbrite.com","facebook.com"):
                    emit_log(f"(DOMAIN DUP: {_page_domain} x{_domain_count})", "skip"); skipped += 1; continue

                # Skip file downloads that crash Playwright
                _url_low = url.lower()
                if any(_url_low.endswith(ext) for ext in (".pdf",".docx",".doc",".xlsx",".xls",".pptx",".ppt",".zip",".rar",".mp4",".mp3",".wav",".exe",".dmg")):
                    emit_log(f"(FILE DOWNLOAD — skipping: {url[-30:]})", "skip"); skipped += 1; continue

                # GDPR: Check robots.txt before crawling
                if not check_robots_txt(url):
                    emit_log(f"(ROBOTS.TXT BLOCKED: {url[:50]})", "skip"); skipped += 1; continue

                _q_fetched += 1
                _domain = urlparse(url).netloc.lower().replace("www.","")
                emit_log(f"🌐 Crawling {_domain}...", "fetch")
                emit_thought(f"Researching {_domain}...", "fetch")
                emit_browsing_state(url, r.title or "", "requests", "loading")
                # Crawl homepage + subpages for rich context
                text, html, _pages_crawled = crawl_prospect(url, max_subpages=7)
                _page_title = r.title or ""
                _title_m = re.search(r"<title[^>]*>([^<]+)</title>", html[:2000], re.I) if html else None
                if _title_m: _page_title = _title_m.group(1).strip()[:100]
                if _pages_crawled > 1:
                    emit_log(f"📄 Crawled {_pages_crawled} pages on {_domain}", "fetch")
                emit_browsing_state(url, _page_title, "requests", "analysing")
                if _check_pause() or _check_stop():
                    emit_thought("Stopping. All leads saved.", "idle")
                    emit_log("Stopped by user", "warn"); break
                if not text.strip():
                    emit_log(f"Empty site — skipping", "skip"); record_domain_fail(url); skipped += 1
                    time.sleep(DELAY_URL); continue

                _page_op_start()
                try: structured = extract_structured(page, html, text)
                finally: _page_op_done(url)

                # Extract LinkedIn from HTML before analysis
                li_urls = extract_linkedin_urls(html)

                # ══ PASS 1: Red Flag Filter ══
                p1 = quick_screen(url, r.title, r.snippet, text, html)
                if p1 is None:
                    skipped += 1; time.sleep(DELAY_URL); continue
                _p1ctx = ""
                if p1.get("green_flags"): _p1ctx += "\nPASS1 GREEN: " + ", ".join(p1["green_flags"])
                if p1.get("red_flags"): _p1ctx += "\nPASS1 RED: " + ", ".join(p1["red_flags"])
                if p1.get("platform_detected"): _p1ctx += f"\nPLATFORM: {p1['platform_detected']}"
                if p1.get("budget_signals") != "unknown": _p1ctx += f"\nBUDGET: {p1['budget_signals']}"
                if p1.get("estimated_company_size") != "unknown": _p1ctx += f"\nSIZE: {p1['estimated_company_size']}"

                # ══ STAGE 2a: DNA anti-pattern check (cheap text scan before AI) ══
                _dna_ap = None
                if ctx and hasattr(ctx, '_cached_dna') and ctx._cached_dna:
                    _s_raw = ctx._cached_dna.get("hunting_strategy") or ctx._cached_dna.get("strategy")
                    if isinstance(_s_raw, dict):
                        _dna_ap = _s_raw.get("anti_patterns")
                if _dna_ap:
                    _page_low = (text[:4000] + " " + (r.title or "")).lower()
                    for _ap_cat, _ap_label in [("competitor_signals","competitor"),("service_mismatch_signals","service mismatch")]:
                        _ap_list = _dna_ap.get(_ap_cat, [])
                        if isinstance(_ap_list, list):
                            _ap_matches = [s for s in _ap_list if isinstance(s, str) and s.lower() in _page_low]
                            if len(_ap_matches) >= 2:
                                emit_log(f"SKIP DNA {_ap_label}: {', '.join(_ap_matches[:3])} — {_page_title[:40]}", "skip")
                                skipped += 1; time.sleep(DELAY_URL); continue

                # ══ STAGE 2b: Prospect-page classifier (cheap, before expensive AI) ══
                _s2_pass, _s2_reason = _classify_prospect_page(text, html, url, _brain)
                if not _s2_pass:
                    emit_log(f"SKIP pre-AI: {_s2_reason} — {r.title[:50]}", "skip")
                    skipped += 1; time.sleep(DELAY_URL); continue

                # ══ STAGE 3: ICP relevance gate — does this page have ANY connection to our ICP? ══
                # Two-tier system: phrase matches count as 2 hits, keyword matches count as 1
                if hasattr(run_agent, '_icp_keywords') and run_agent._icp_keywords:
                    _page_sample = (text[:4000] + " " + (r.title or "") + " " + (r.snippet or "")).lower()
                    _icp_hits = sum(1 for kw in run_agent._icp_keywords if kw in _page_sample)
                    # Phrase matches are higher confidence — each counts as 2 keyword hits
                    _icp_phrase_hits = 0
                    if hasattr(run_agent, '_icp_phrases'):
                        _icp_phrase_hits = sum(1 for ph in run_agent._icp_phrases if ph in _page_sample)
                    _total_relevance = _icp_hits + (_icp_phrase_hits * 2)
                    if _total_relevance < 2:
                        print(f"[AGENT] ICP gate KILL ({_icp_hits}kw+{_icp_phrase_hits}ph): {_domain} '{(r.title or '')[:50]}'")
                        emit_log(f"SKIP no ICP relevance ({_icp_hits} keywords, {_icp_phrase_hits} phrases) — {_page_title[:40]}", "skip")
                        skipped += 1; time.sleep(DELAY_URL); continue

                emit_log(f"🤖 Evaluating: {_page_title[:60]}", "ai")
                if _check_stop(): break
                # ── Plugin pre_score hook (round-69 Kimi spec) ──
                # Plugins attach signals (tech stack, hiring, social
                # data) to the candidate dict BEFORE the LLM scores it,
                # so the model has richer context to reason over.
                _candidate = {
                    "url": url, "title": r.title, "snippet": r.snippet,
                    "page_text_preview": (text or "")[:1500],
                    "structured": structured,
                }
                try:
                    from plugins import get_registry as _pg, HookContext as _PHC
                    _candidate = _pg().run("pre_score", _PHC(
                        settings=_wiz_data or {},
                        provider_name=os.environ.get("HV_AI_PROVIDER", "gemini"),
                        user_id=getattr(_ctx(), "user_id", None),
                        meta={"phase": "pre_score"},
                    ), _candidate) or _candidate
                except Exception as _phk3:
                    emit_log(f"plugin pre_score failed: {_phk3}", "warn")
                lead = analyse_lead(url, r.title, r.snippet, text, structured + _p1ctx)
                _pages_analysed += 1
                if _check_pause() or _check_stop():
                    emit_thought("Stopping. All leads saved.", "idle")
                    emit_log("Stopped by user", "warn"); break
                if lead is None:
                    emit_log(f"❌ AI returned nothing for: {_page_title[:50]}", "skip")
                    skipped += 1; time.sleep(DELAY_URL); continue
                # Carry pre_score plugin enrichments forward into the lead
                if isinstance(_candidate, dict):
                    for _k, _v in _candidate.items():
                        if _k.startswith("plugin_") and _k not in lead:
                            lead[_k] = _v
                # ── Plugin post_score hook (round-69 Kimi spec) ──
                # Plugins can rewrite the fit_score after the LLM
                # returns, e.g. boost-by-tech-stack or penalise-by-
                # blocklist. Returns (lead, score). Errors caught.
                try:
                    from plugins import get_registry as _pg_ps, HookContext as _PHC_ps
                    _pre_score_val = float(lead.get("fit_score", 0) or 0)
                    _ps_ret = _pg_ps().run("post_score", _PHC_ps(
                        settings=_wiz_data or {},
                        provider_name=os.environ.get("HV_AI_PROVIDER", "gemini"),
                        user_id=getattr(_ctx(), "user_id", None),
                        meta={"phase": "post_score"},
                    ), lead, _pre_score_val)
                    if isinstance(_ps_ret, tuple) and len(_ps_ret) == 2:
                        _ps_lead, _ps_score = _ps_ret
                        if isinstance(_ps_lead, dict):
                            lead = _ps_lead
                        try:
                            _new_score = float(_ps_score)
                            if abs(_new_score - _pre_score_val) >= 0.01:
                                lead["fit_score"] = _new_score
                                emit_log(f"plugin post_score adjusted fit {_pre_score_val:.1f}→{_new_score:.1f}", "info")
                        except (TypeError, ValueError):
                            pass
                except Exception as _phk_ps:
                    emit_log(f"plugin post_score failed: {_phk_ps}", "warn")
                # ── Plugin post_qualify hook (round-69 Kimi spec) ──
                # Fires AFTER the AI scored the lead but BEFORE we
                # decide to keep/skip. This is where Apollo / Hunter /
                # custom enrichers add data that isn't worth burning
                # on every search result. Errors caught.
                try:
                    from plugins import get_registry as _pg2, HookContext as _PHC2
                    lead = _pg2().run("post_qualify", _PHC2(
                        settings=_wiz_data or {},
                        provider_name=os.environ.get("HV_AI_PROVIDER", "gemini"),
                        user_id=getattr(_ctx(), "user_id", None),
                        meta={"phase": "post_qualify"},
                    ), lead) or lead
                except Exception as _phk4:
                    emit_log(f"plugin post_qualify failed: {_phk4}", "warn")
                _ai_score = lead.get("fit_score", 0)
                _ai_org = lead.get("org_name", "?")[:30]
                emit_log(f"⚡ Score: {_ai_score}/10 — {_ai_org} | buy={lead.get('buyability_score',0)} reach={lead.get('reachability_score',0)}", "ai")
                emit_browsing_state(url, _page_title, "requests", "scored")

                # Attach Pass 1 intelligence
                if p1:
                    lead["_pass1"] = {"green":p1.get("green_flags",[]),"red":p1.get("red_flags",[]),
                        "platform":p1.get("platform_detected"),"budget":p1.get("budget_signals","unknown"),
                        "size":p1.get("estimated_company_size","unknown"),"freshness":p1.get("content_freshness","unknown"),
                        "has_events":p1.get("has_events_section",False),"has_contact":p1.get("has_contact_info",False),
                        "has_video":p1.get("has_video_content",False)}

                score = lead["fit_score"]

                # Post-analysis org-name filter — catch platforms/giants the AI missed
                org = (lead.get("org_name") or "").lower()
                _blocked_orgs = ["zoom", "microsoft", "teams", "webex", "goto", "hopin",
                    "airmeet", "on24", "bigmarker", "demio", "livestorm", "streamyard",
                    "restream", "vmix", "obs", "vimeo", "brightcove", "cvent", "eventbrite",
                    "bizzabo", "swoogo", "hubilo", "goldcast", "google", "amazon", "meta",
                    "facebook", "apple", "netflix", "salesforce", "oracle", "ibm", "cisco",
                    "adobe", "samsung", "siemens", "sap", "deloitte", "pwc", "kpmg",
                    "mckinsey", "accenture", "hubspot", "mailchimp", "canva"]
                if any(b in org for b in _blocked_orgs):
                    lead["fit_score"] = 0; score = 0
                    emit_log(f"(SKIPPED — BLOCKED ORG: {lead.get('org_name','?')[:40]})", "skip")
                    skipped += 1; continue

                if is_user_blocked(url, lead.get("org_name","")):
                    emit_log(f"(USER BLOCKED) {lead.get('org_name','?')}", "skip")
                    skipped += 1; continue

                # Merge LinkedIn URLs found in HTML
                if li_urls["org"] and not lead.get("org_linkedin"): lead["org_linkedin"] = li_urls["org"]
                if li_urls["contact"] and not lead.get("contact_linkedin"): lead["contact_linkedin"] = li_urls["contact"]

                # Deep qualify
                if score >= DEEP_MIN:
                    if _check_stop(): continue  # stop signaled — skip rest of URL, exit via outer check
                    emit_log(f"🔬 Deep-qualifying (score {score})...", "info")
                    _page_op_start()
                    try: merged = deep_qualify(page, url, text)
                    finally: _page_op_done(url)
                    if _check_pause() or _check_stop():
                        emit_thought("Stopping. All leads saved.", "idle")
                        emit_log("Stopped by user", "warn"); break
                    if len(merged) > len(text) + 100:
                        lead = analyse_lead(url, r.title, r.snippet, merged, structured) or lead
                        score = lead["fit_score"]

                # Supplementary search for high-value leads
                if score >= 8:
                    try:
                        _org = lead.get("org_name","")
                        if _org:
                            emit_log(f"🔎 Supplementary search for {_org[:40]}...", "fetch")
                            sup_urls = supplementary_search(_org, lead.get("org_website","") or url)
                            sup_text_parts = []
                            for surl in sup_urls[:3]:
                                if surl != url and surl not in _ctx().seen_urls:
                                    try:
                                        _st, _sh = fetch_page(page, surl, 3000)
                                        if _st.strip():
                                            sup_text_parts.append(f"\n-- {surl} --\n{_st}")
                                    except: pass
                                    time.sleep(0.3)
                            if sup_text_parts:
                                merged_sup = text + "\n".join(sup_text_parts)
                                lead = analyse_lead(url, r.title, r.snippet, merged_sup[:12000], structured) or lead
                                score = lead["fit_score"]
                                emit_log(f"🔎 Supplementary: {len(sup_text_parts)} extra pages analyzed", "ai")
                    except Exception as e:
                        emit_log(f"Supplementary search failed: {e}", "warn")

                # Smart contact enrichment (only with Playwright — needs page navigation)
                if score >= MIN_SCORE_TO_KEEP and page is not None:
                    emit_browsing_state(url, _page_title, "requests", "enriching")
                    _page_op_start()
                    try: lead = enrich_contact(page, url, lead)
                    finally: _page_op_done(url)
                    if _check_pause() or _check_stop():
                        emit_thought("Stopping. All leads saved.", "idle")
                        emit_log("Stopped by user", "warn"); break

                # ══ PASS 2: Deep Investigation (high-value leads only, requires Playwright) ══
                if score >= 8 and page is not None:
                    # Return page to original URL (enrich_contact may have navigated away)
                    _page_op_start()
                    try: page.goto(url, timeout=FETCH_TIMEOUT_MS, wait_until="domcontentloaded")
                    except: pass
                    finally: _page_op_done(url)
                    _page_op_start()
                    try: lead = deep_investigate(lead, page, url, text)
                    finally: _page_op_done(url)
                    if _check_pause() or _check_stop():
                        emit_thought("Stopping. All leads saved.", "idle")
                        emit_log("Stopped by user", "warn"); break

                # ══ INTELLIGENCE ENRICHMENT ══

                # 1. Score validation — second AI pass to verify score is evidence-backed
                if score >= MIN_SCORE_TO_KEEP:
                    if _check_stop(): continue  # stop signaled — skip rest of URL, exit via outer check
                    verified = validate_score(lead, text)
                    if verified != score:
                        lead["fit_score_original"] = score
                        lead["fit_score"] = verified
                        score = verified

                # 2. Date parsing — extract real event dates for timing
                try:
                    _dates = extract_event_dates(text)
                    if _dates:
                        _timing, _days, _date_str = classify_event_timing(_dates)
                        lead["_event_dates"] = [{"text": s, "date": d.isoformat()} for s, d in _dates[:5]]
                        lead["_event_timing"] = _timing
                        lead["_days_until_event"] = _days
                        if _timing == "imminent" and _days is not None and _days <= 14:
                            emit_log(f"⏰ EVENT IN {_days} DAYS: {_date_str}", "lead")
                        elif _timing == "upcoming" and _days is not None:
                            emit_log(f"📅 Event in {_days} days: {_date_str}", "info")
                except: pass

                # 3. Email verification — MX check
                if lead.get("contact_email"):
                    if not verify_email_mx(lead["contact_email"]):
                        emit_log(f"📧 Email MX failed: {lead['contact_email']} — marking unverified", "warn")
                        lead["_email_verified"] = False
                    else:
                        lead["_email_verified"] = True

                # 4. Track query performance for feedback loop
                _track_query_result(query, score)

                # Dedup — INT-005: check both new (domain-based) and legacy fingerprints
                fp = make_fingerprint(lead)
                fp_legacy = make_fingerprint_legacy(lead)
                if fp in _ctx().seen_fps or fp_legacy in _ctx().seen_fps:
                    emit_log(f"Duplicate: {lead.get('org_name','?')}", "skip"); skipped += 1; continue
                _ctx().seen_fps.add(fp)
                _ctx().seen_fps.add(fp_legacy)  # Store both to match old and new runs

                org = lead.get("org_name","?"); country = lead.get("country","?")

                # Track org domain to prevent duplicates from same org
                _org_domain = urlparse(lead.get("org_website","") or url).netloc.lower().replace("www.","")
                if _org_domain: _found_domains[_org_domain] = _found_domains.get(_org_domain, 0) + 1

                # ── Multi-dimensional scoring gate ──
                # Extract multi-dimensional scores
                fit = lead.get("fit_score", 0) or 0
                buyability = lead.get("buyability_score") or 0  # no fallback — missing = fail safe
                reachability = lead.get("reachability_score") or 0  # no fallback — missing = fail safe
                svc_opp = lead.get("service_opportunity_score") or 0
                timing = lead.get("timing_score") or 0
                has_internal = lead.get("has_internal_team", False)

                # Multi-dimensional acceptance thresholds (from dossier or defaults)
                _FIT_MIN = _accept_spec["fit_threshold"]
                _BUY_MIN = _accept_spec["buyability_threshold"]
                _REACH_MIN = _accept_spec["reachability_threshold"]

                if fit < _FIT_MIN:
                    emit_log(f"SKIP fit={fit}: {org} — below fit threshold", "skip")
                    skipped += 1; continue

                if buyability < _BUY_MIN:
                    emit_log(f"SKIP buyability={buyability}: {org} — poor outbound target", "skip")
                    skipped += 1; continue

                if reachability < _REACH_MIN:
                    emit_log(f"SKIP reachability={reachability}: {org} — no viable contact path", "skip")
                    skipped += 1; continue

                # ── Hard-reject safety gates — profile-driven ──
                _wiz_rules = load_settings().get("wizard", {})
                _org_lower = org.lower()
                _buyability_fail = False

                # Giant enterprises — only reject if profile says so (default: reject)
                if _wiz_rules.get("reject_enterprise", True):
                    if any(mc in _org_lower for mc in ["fortune 500","fortune500","nasdaq","nyse","ftse","s&p 500"]):
                        _buyability_fail = True
                        emit_log(f"REJECT: {org} — giant enterprise (profile rule)", "skip")

                # Government — only reject if profile says so (default: reject)
                if not _buyability_fail and _wiz_rules.get("reject_government", True):
                    if any(kw in _org_lower for kw in ["government of","ministry of","department of","european commission","united nations","world bank"]):
                        _buyability_fail = True
                        emit_log(f"REJECT: {org} — government/institution (profile rule)", "skip")

                # Strong in-house team — only reject if profile says so (default: reject)
                if not _buyability_fail and _wiz_rules.get("reject_strong_inhouse", True):
                    _ai_flagged_internal = lead.get("has_internal_team", False)
                    _page_lower = (lead.get("evidence_quote","") + " " + lead.get("production_gap","")).lower()
                    _text_detected_internal = any(kw in _page_lower for kw in ["in-house team","internal team","dedicated team","built internally"])
                    if _ai_flagged_internal:
                        # AI explicitly classified as having internal team — strong signal, reject
                        _buyability_fail = True
                        emit_log(f"REJECT: {org} — AI flagged has_internal_team=true (profile rule)", "skip")
                    elif _text_detected_internal and not any(kw in _page_lower for kw in ["overflow","outsourc","external","partner","supplement"]):
                        # Text-detected internal team without outsourcing signals — reject
                        _buyability_fail = True
                        emit_log(f"REJECT: {org} — strong in-house team (profile rule)", "skip")

                # No contact path — require a real outbound-actionable contact, not just a website
                # In lightweight mode (no Playwright), contact enrichment doesn't run,
                # so we accept leads with org_website as a minimum contact path
                if not _buyability_fail and _wiz_rules.get("reject_no_contact", True):
                    _has_real_contact = bool(
                        lead.get("contact_name") or
                        lead.get("contact_email") or
                        lead.get("contact_linkedin") or
                        lead.get("org_linkedin") or
                        lead.get("contact_page_url") or
                        lead.get("org_website")  # Website counts as contact path
                    )
                    if not _has_real_contact:
                        _buyability_fail = True
                        emit_log(f"REJECT: {org} — no actionable contact (profile rule)", "skip")

                if _buyability_fail:
                    skipped += 1
                    continue

                # ── INT-001: Geography enforcement gate ──
                # User selects target countries at hunt start; reject leads outside those countries.
                # _sel_countries is set at line 7291 from _get_agent_config().
                if _sel_countries:
                    _lead_country = (lead.get("country") or "").strip()
                    if _lead_country and _lead_country not in ("unknown", "Unknown", ""):
                        # Normalize common variants for matching
                        _country_match = _lead_country in _sel_countries
                        if not _country_match:
                            # Try case-insensitive match
                            _sel_lower = {c.lower() for c in _sel_countries}
                            _country_match = _lead_country.lower() in _sel_lower
                        if not _country_match:
                            emit_log(f"SKIP geography: {org} in {_lead_country} — not in target countries", "skip")
                            skipped += 1
                            continue

                # ── INT-002: Dossier hard_rejects enforcement ──
                # Custom exclusions from wizard (e.g., "nonprofits", "educational institutions")
                # are stored in _accept_spec["hard_rejects"] but were never checked against leads.
                _custom_rejects = _accept_spec.get("hard_rejects", [])
                if _custom_rejects:
                    _lead_text = f"{_org_lower} {(lead.get('event_name','') or '').lower()} {(lead.get('event_type','') or '').lower()} {(lead.get('why_fit','') or '').lower()}"
                    _custom_rejected = False
                    for _cr in _custom_rejects:
                        _cr_lower = _cr.lower().strip()
                        # Skip the standard reject rules (already handled above)
                        if _cr_lower in ("giant enterprise / fortune 500", "government / public institution",
                                         "strong internal team without overflow need", "no actionable contact path"):
                            continue
                        if len(_cr_lower) > 3 and _cr_lower in _lead_text:
                            emit_log(f"REJECT: {org} — matches custom exclusion '{_cr}'", "skip")
                            _custom_rejected = True
                            break
                    if _custom_rejected:
                        skipped += 1
                        continue

                # ── INT-003: Content freshness gate ──
                # Stale content (>3 years old) is a strong signal the company is inactive.
                # Previously just a red flag; now enforced as a gate.
                _p1_data = lead.get("_pass1", {})
                _lead_freshness = _p1_data.get("freshness", "unknown") if _p1_data else "unknown"
                if _lead_freshness == "stale":
                    # Allow override: if lead has strong green flags or high service opportunity,
                    # the company may still be active despite old web content
                    if svc_opp < 7 and not _p1_data.get("has_contact"):
                        emit_log(f"SKIP freshness: {org} — severely outdated content", "skip")
                        skipped += 1
                        continue

                # ── INT-004: Company size preference gate ──
                # When user indicated they want to avoid small/solo companies via wizard red_flags,
                # enforce it as a gate rather than just a red flag annotation.
                _est_size = _p1_data.get("size", "unknown") if _p1_data else "unknown"
                if _est_size == "small":
                    _wiz_red_flags = _wiz_rules.get("red_flags", [])
                    if "avoid_solo" in _wiz_red_flags:
                        emit_log(f"SKIP size: {org} — small/solo company (wizard preference)", "skip")
                        skipped += 1
                        continue

                if fit >= _FIT_MIN:
                    lead["url"] = url
                    lead["lead_id"] = make_lead_id(lead)
                    lead["found_date"] = datetime.now(timezone.utc).isoformat()
                    lead["email_status"] = lead.get("email_status", "new")

                    # Save to DB first, THEN deduct credit (prevents phantom credit loss).
                    # Stability fix (Perplexity bug #59): only deduct
                    # when upsert_lead reports it INSERTED a new row.
                    # On UPDATE (re-discovery via different URL on the
                    # same domain producing the same lead_id) we
                    # refresh the lead but don't double-charge.
                    ctx = _ctx()
                    _lead_saved = False
                    _was_new = False
                    if ctx:
                        try:
                            _db = __import__("db")
                            _was_new = bool(_agent_loop.run_until_complete(_db.upsert_lead(ctx.user_id, lead["lead_id"], lead)))
                            _lead_saved = True
                        except Exception as _save_err:
                            emit_log(f"Failed to save lead to DB: {_save_err}", "error")

                    if _lead_saved and ctx and _was_new:
                        # Per-lead credit deduct (skipped in local/BYOK mode;
                        # user pays their own provider directly).
                        from policy import policy as _policy
                        if _policy.deduct_on_save():
                            _cost = _policy.cost_per_lead({"tier": getattr(ctx, "tier", "free")})
                            _has_credits = _agent_loop.run_until_complete(_db.deduct_credit(ctx.user_id, _cost))
                            if not _has_credits:
                                # Stability fix (multi-agent bug #19): the lead
                                # was already inserted but deduct just told us
                                # we're out of credits. Without this rollback
                                # the user keeps the free lead in their CRM.
                                try:
                                    _agent_loop.run_until_complete(_db.permanent_delete_lead(ctx.user_id, lead["lead_id"]))
                                except Exception as _rb_err:
                                    emit_log(f"Could not roll back lead after credit-exhausted: {_rb_err}", "warn")
                                emit_log("Out of credits — stopping agent", "warn")
                                emit_status("Out of credits", "stopped")
                                ctx.bus.emit("credits_exhausted", {"user_id": ctx.user_id})
                                break
                            ctx.credits_used += _cost
                    elif _lead_saved and ctx and not _was_new:
                        # Already-known lead refreshed without charging.
                        emit_log(f"↻ Refreshed existing lead {lead.get('org_name','?')[:40]} (no charge)", "info")
                        # Skip the new-lead emit/append path below — otherwise
                        # the same lead duplicates in `all_leads`, SSE emits
                        # `lead` twice, and post_save plugins (csv-sink,
                        # slack-ping, generic-webhook) fire a second time on
                        # the same prospect. Just refresh the CRM view so
                        # the user sees any updated fields.
                        _c = _ctx()
                        if _c: _c.bus.emit("crm_refresh", {})
                        else: bus.emit("crm_refresh", {})
                        continue
                    elif not _lead_saved and ctx:
                        continue  # Skip this lead entirely — don't charge

                    _ctx().all_leads.append(lead)
                    save_master_leads([lead])  # Save to disk — crash safe
                    _query_lead_count += 1
                    emit_log(f"🎯 LEAD fit={fit} buy={buyability} reach={reachability} opp={svc_opp} time={timing} — {org} [{country}]", "lead")
                    emit_thought("New lead found! Drafting email and saving.", "excited")
                    emit_lead(lead)
                    _c = _ctx()
                    if _c: _c.bus.emit("crm_refresh", {})
                    else: bus.emit("crm_refresh", {})
                    # Plugin post_save hook — fire-and-forget side effects
                    # (CRM push, Slack notify, webhook, etc.). Plugin
                    # errors are caught by the registry so they can't
                    # break the hunt loop.
                    try:
                        from plugins import get_registry, HookContext as _HC
                        _reg2 = get_registry()
                        _reg2.run("post_save", _HC(
                            settings=load_settings() or {},
                            provider_name=os.environ.get("HV_AI_PROVIDER", "gemini"),
                            user_id=getattr(ctx, "user_id", None),
                            meta={"phase": "post_save"},
                        ), lead)
                    except Exception:
                        pass
                    we = sum(1 for l in _ctx().all_leads if l.get("contact_email"))
                    rc = sum(1 for l in _ctx().all_leads if l.get("is_recurring"))
                    emit_progress(current=qi,total=len(queries),urls=urls_seen,leads=len(_ctx().all_leads),
                                  skipped=skipped,with_email=we,recurring=rc)

                    # ══ LEAD-TRIGGERED EXPANSION: Find similar companies ══
                    # When a high-scoring lead is found, generate lookalike queries
                    # Stored in separate list to avoid mutating queries during iteration
                    if fit >= 8 and len(_ctx().all_leads) <= 30:
                        try:
                            _lead_industry = lead.get("event_type", "") or lead.get("category", "")
                            _lead_country = lead.get("country", "")
                            _platform = lead.get("platform_used", "") or ""
                            _gap = lead.get("production_gap", "") or ""
                            _lookalike_queries = []
                            if _lead_industry and _lead_country:
                                _lookalike_queries.append(f"{_lead_industry} companies {_lead_country} 2025 2026")
                            if _platform and _lead_industry:
                                _lookalike_queries.append(f"{_lead_industry} using {_platform[:30]}")
                            if _lead_industry and _gap:
                                _gap_kw = _gap[:40].split(".")[0]
                                _lookalike_queries.append(f"{_lead_industry} {_gap_kw}")
                            # Queue for next batch — don't mutate queries list during iteration.
                            # Stability fix (multi-agent bug #27): the dedup
                            # check used .lower() but the append stored the
                            # original-case string. The next-batch dedup at
                            # the top of the outer loop is case-sensitive,
                            # so "EVENT companies USA" and "event companies
                            # usa" both leaked through and got searched.
                            # Now we store the normalized form everywhere.
                            if not hasattr(run_agent, '_expansion_queries'):
                                run_agent._expansion_queries = []
                            _existing = {q.lower() for q in queries} | {q.lower() for q in getattr(run_agent, '_expansion_queries', [])}
                            _new_count = 0
                            for _lq in _lookalike_queries:
                                _lq_norm = _lq.strip().lower()
                                if _lq_norm and _lq_norm not in _existing:
                                    run_agent._expansion_queries.append(_lq_norm)
                                    _existing.add(_lq_norm)
                                    _new_count += 1
                            if _new_count:
                                emit_log(f"🧬 Lead expansion: +{_new_count} similar-company queries queued from {org[:30]}", "ai")
                        except Exception:
                            pass

                else:
                    _rescued = False
                    # ══ BORDERLINE LEAD RESCUE: Score 5-6 with high opportunity gets a second look ══
                    if 5 <= fit <= 6 and svc_opp >= 7 and not has_internal:
                        try:
                            _rescue_text, _rescue_html, _rescue_pages = crawl_prospect(url, max_subpages=2)
                            if _rescue_text and len(_rescue_text) > len(text) + 500:
                                emit_log(f"🔄 Borderline rescue: re-scoring {_ai_org} with {_rescue_pages} extra pages", "ai")
                                _rescue_lead = analyse_lead(url, r.title, r.snippet, _rescue_text[:8000], structured)
                                if _rescue_lead:
                                    _new_fit = _rescue_lead.get("fit_score", 0) or 0
                                    _new_buy = _rescue_lead.get("buyability_score", 0) or 0
                                    _new_reach = _rescue_lead.get("reachability_score", 0) or 0
                                    if _new_fit >= _FIT_MIN and _new_buy >= _BUY_MIN and _new_reach >= _REACH_MIN:
                                        lead = _rescue_lead
                                        fit = _new_fit; buyability = _new_buy; reachability = _new_reach
                                        svc_opp = lead.get("service_opportunity_score", 0) or 0
                                        timing = lead.get("timing_score", 0) or 0
                                        score = fit
                                        emit_log(f"✅ Rescue succeeded: {_ai_org} now scores {fit}", "ok")
                                        _rescued = True
                        except Exception:
                            pass
                    if _rescued:
                        # Re-enter acceptance flow — process this rescued lead
                        lead["url"] = url
                        lead["lead_id"] = make_lead_id(lead)
                        lead["found_date"] = datetime.now(timezone.utc).isoformat()
                        lead["email_status"] = lead.get("email_status", "new")
                        lead["_rescued"] = True
                        ctx = _ctx()
                        _lead_saved = False
                        _was_new = False
                        if ctx:
                            try:
                                _db = __import__("db")
                                _was_new = bool(_agent_loop.run_until_complete(_db.upsert_lead(ctx.user_id, lead["lead_id"], lead)))
                                _lead_saved = True
                            except Exception as _save_err:
                                emit_log(f"Failed to save rescued lead: {_save_err}", "error")
                        # Stability fix (Perplexity bug #59, rescued-lead
                        # path): only deduct on a true insert. A re-rescue
                        # of an already-saved prospect refreshes the row
                        # but doesn't charge.
                        if _lead_saved and ctx and _was_new:
                            from policy import policy as _policy
                            if _policy.deduct_on_save():
                                _cost = _policy.cost_per_lead({"tier": getattr(ctx, "tier", "free")})
                                _has_credits = _agent_loop.run_until_complete(_db.deduct_credit(ctx.user_id, _cost))
                                if not _has_credits:
                                    try:
                                        _agent_loop.run_until_complete(_db.permanent_delete_lead(ctx.user_id, lead["lead_id"]))
                                    except Exception as _rb_err:
                                        emit_log(f"Could not roll back rescued lead after credit-exhausted: {_rb_err}", "warn")
                                    emit_log("Out of credits — stopping agent", "warn")
                                    emit_status("Out of credits", "stopped")
                                    ctx.bus.emit("credits_exhausted", {"user_id": ctx.user_id}); break
                                ctx.credits_used += _cost
                        elif _lead_saved and ctx and not _was_new:
                            emit_log(f"↻ Refreshed existing rescued lead (no charge)", "info")
                        elif not _lead_saved and ctx:
                            continue
                        _ctx().all_leads.append(lead)
                        save_master_leads([lead])
                        org = lead.get("org_name", "?"); country = lead.get("country", "?")
                        emit_log(f"🎯 RESCUED LEAD fit={fit} buy={buyability} reach={reachability} — {org} [{country}]", "lead")
                        emit_lead(lead)
                    else:
                        _why = lead.get("why_fit","")[:60]
                        emit_log(f"✗ {_ai_org} — score {fit}/10, not a fit{(' — ' + _why) if _why else ''}", "skip"); skipped += 1
                    _track_query_result(query, score)

                time.sleep(DELAY_URL)

            if _q_blocked or _q_seen:
                emit_log(f"📊 Query {qi}: {_q_fetched} fetched, {_q_blocked} blocked, {_q_seen} already seen", "info")

            # Per-URL budget probe set _stop_reason — exit outer query
            # loop now so we don't start the next query.
            if _stop_reason in ("max_leads", "timeout"):
                break

            # Save agent state EVERY query (crash-proof — survives standby/power loss)
            try:
                _agent_state["last_query_index"] = qi
                _agent_state["leads_found"] = len(_ctx().all_leads)
                _atomic_write(AGENT_STATE_FILE, _agent_state)
            except: pass
            # Save seen history every query (so resume skips already-checked URLs)
            save_seen_history()
            # Full checkpoint (leads + CSV + blocklist) every 5 queries
            if qi % CHECKPOINT_N == 0:
                if _ctx().all_leads:
                    emit_log(f"Checkpoint: {len(_ctx().all_leads)} leads", "save")
                    save_csv(_ctx().all_leads); save_master_leads(_ctx().all_leads)
                save_domain_blocklist()
            # Record query outcome for adaptive learning.
            # Stability fix (multi-agent bug #24): consumers only ever
            # slice [-20:] / [-5:] from these lists, so unbounded append
            # over a long multi-batch run was just dead memory. Cap at
            # 50 (well above the largest slice) using deque semantics.
            if _query_lead_count > 0:
                _successful_patterns.append(_current_query)
                if len(_successful_patterns) > 50:
                    del _successful_patterns[:-50]
            else:
                _failed_patterns.append(_current_query)
                if len(_failed_patterns) > 50:
                    del _failed_patterns[:-50]

            if _check_stop() or _check_pause():
                emit_thought("Stopping. All leads saved.", "idle")
                emit_log("Stopped by user", "warn"); _stop_reason = "stopped"; emit_status("Stopped", "stopped"); break
            time.sleep(DELAY_QUERY)

        # ── Batch continuation logic ──
        if _stop_reason == "stopped":
            break
        if _stop_reason in ("max_leads", "timeout"):
            break
        if _check_stop():
            _stop_reason = "stopped"; break
        # Re-check budget between batches so a near-miss inside the
        # batch loop doesn't spawn another full batch worth of work.
        # Skip if the budget already fired inside the batch — _check_
        # budget() emits a status event each call, and round-7 audit
        # finding #6 caught the duplicate "stopped" SSE that resulted.
        if not _stop_reason:
            _bud_post = _check_budget()
            if _bud_post:
                _stop_reason = _bud_post; break
        # Check credits in SaaS mode
        ctx = _ctx()
        if ctx:
            _cr_remaining = _agent_loop.run_until_complete(__import__("db").check_and_reset_credits(ctx.user_id))
            if _cr_remaining <= 0:
                emit_log("Out of credits — hunt complete", "warn")
                _stop_reason = "credits"; emit_status("Out of credits", "stopped"); break
        # Track yield: how many leads did this batch produce?
        _batch_leads = len(_ctx().all_leads) - _batch_start_leads if '_batch_start_leads' in dir() else len(_ctx().all_leads)
        # If batch 1 produced 0 leads and DNA was used, regenerate DNA for next batch
        if _batch_leads == 0 and batch_num == 1 and _agent_dna and ctx:
            emit_log("Queries didn't find leads — regenerating smarter queries...", "info")
            try:
                _new_dna = generate_agent_dna(_wiz_data)
                if _new_dna and _new_dna.get("search_queries"):
                    _agent_dna = _new_dna
                    ctx._cached_dna = _new_dna
                    _agent_loop.run_until_complete(__import__("db").save_agent_dna(ctx.user_id, _new_dna))
                    emit_log(f"Regenerated {len(_new_dna.get('search_queries',[]))} new queries", "ok")
            except Exception as _regen_err:
                print(f"[AGENT] DNA regen failed: {_regen_err}")
        if _batch_leads == 0:
            _consecutive_dry_batches += 1
        else:
            _consecutive_dry_batches = 0

        # Exhaustion check — based on REAL work (pages analysed), not SERP noise
        _can_exhaust = (
            _pages_analysed >= _MIN_URLS_BEFORE_EXHAUST and
            _exploration_phase >= _MIN_PHASES_BEFORE_EXHAUST and
            _consecutive_dry_batches >= 3
        )
        if _can_exhaust:
            emit_log(f"Search exhausted after {_exploration_phase + 1} phases, {_pages_analysed} pages analysed, {_consecutive_dry_batches} dry batches", "info")
            _stop_reason = "exhausted"; break

        # If dry but not exhausted yet, advance exploration phase
        if _batch_leads == 0 and _exploration_phase < len(_EXPLORATION_PHASES) - 1:
            _exploration_phase += 1
            _phase_name = _EXPLORATION_PHASES[_exploration_phase]
            emit_log(f"Switching search direction → {_phase_name.replace('_', ' ')}", "info")
            emit_thought(f"Trying a different approach: {_phase_name.replace('_', ' ')}...", "thinking")
            emit_status(f"Exploring: {_phase_name.replace('_', ' ')}", "running")
        # Check batch limit (safety cap)
        batch_num += 1
        if batch_num > max_batches:
            emit_log(f"Maximum search depth reached after {batch_num-1} batches", "info")
            _stop_reason = "exhausted"; break
        emit_log(f"Generating batch {batch_num} — learning from {len(_successful_patterns)} hits, {len(_failed_patterns)} misses...", "info")
        emit_thought("Adapting search strategy based on what worked...", "thinking")
        emit_status(f"Batch {batch_num} — adapting queries", "running")

        # Generate new queries — influenced by what worked
        _wiz_data = load_settings().get("wizard", {})
        _sel_countries = _get_agent_config().get("countries", [])

        # Extract patterns from successful queries to bias next batch
        _success_words = set()
        for sq in _successful_patterns[-20:]:  # last 20 successes
            for w in sq.lower().split():
                if len(w) > 3 and w not in ("2026","2025","the","and","for","with"):
                    _success_words.add(w)

        # Phase-aware query generation
        _phase_name = _EXPLORATION_PHASES[min(_exploration_phase, len(_EXPLORATION_PHASES) - 1)]
        _phase_hint = ""
        if _phase_name == "adjacent_problems":
            _phase_hint = "\nFOCUS: Search for companies with problems/gaps that our services solve. Not direct service searches — look for the PAIN."
        elif _phase_name == "buyer_roles":
            _phase_hint = "\nFOCUS: Search for the specific buyer ROLES we target. Include job titles, department names, and decision-maker types."
        elif _phase_name == "directories":
            _phase_hint = "\nFOCUS: Search industry directories, associations, and membership lists. These list companies by category."
        elif _phase_name == "growth_signals":
            _phase_hint = "\nFOCUS: Search for companies showing growth, hiring, expansion, new offices, funding rounds — signals they need external help."
        elif _phase_name == "localized":
            _phase_hint = "\nFOCUS: Use native-language terms for non-English markets. German, French, Spanish, Italian, Dutch search terms."
        elif _phase_name == "broadened":
            _phase_hint = "\nFOCUS: Broaden the search. Try adjacent industries, wider geography, or related service categories."

        # Generate next batch using brain if available
        if _brain.get("hunt_brain_version", 0) >= 1 and _brain.get("archetype") != "other":
            new_queries = _generate_brain_queries(_brain, _sel_countries, _batch_size * 2)
        else:
            new_queries = _fallback_queries(_wiz_data, _sel_countries)

        # Try AI enhancement
        if _wiz_data.get("business_description") or _wiz_data.get("services"):
            try:
                _aug_wiz = dict(_wiz_data)
                _learn_ctx = ""
                if _successful_patterns:
                    _learn_ctx = f"\nQueries that found good leads: {', '.join(_successful_patterns[-5:])}"
                if _failed_patterns:
                    _learn_ctx += f"\nQueries that found nothing: {', '.join(_failed_patterns[-5:])}"
                _aug_wiz["_learning_context"] = _learn_ctx + _phase_hint
                _ai_new = generate_queries_ai(_aug_wiz, _sel_countries, _batch_size)
                if _ai_new and len(_ai_new) > len(new_queries):
                    new_queries = _ai_new
            except Exception:
                pass

        # Filter out queries we already tried
        new_queries = [q for q in new_queries if q not in _used_queries]

        # Boost queries that share words with successful patterns
        if _success_words and len(new_queries) > _batch_size:
            def _success_score(q):
                return sum(1 for w in q.lower().split() if w in _success_words)
            new_queries.sort(key=_success_score, reverse=True)

        # Prepend lead-expansion queries (highest priority — proven similar to good leads)
        if hasattr(run_agent, '_expansion_queries') and run_agent._expansion_queries:
            _exp = [q for q in run_agent._expansion_queries if q not in _used_queries]
            run_agent._expansion_queries = []
            if _exp:
                emit_log(f"🧬 Injecting {len(_exp)} lead-expansion queries into batch {batch_num}", "ai")
                new_queries = _exp[:10] + new_queries  # Expansion first, then AI-generated

        if len(new_queries) < 5:
            emit_log("Search space exhausted — no new queries to try", "info")
            _stop_reason = "exhausted"; break
        queries = new_queries[:_batch_size]
        _used_queries.update(queries)
        emit_log(f"Batch {batch_num}: {len(queries)} adapted queries", "ok")
      # ── end outer batch loop ──
    finally:
      if browser:
          try: browser.close()
          except Exception as _bc_err:
              emit_log(f"browser.close failed: {_bc_err}", "warn")
      if _pw_ctx:
          try: _pw_ctx.stop()
          except Exception as _pw_err:
              emit_log(f"playwright.stop failed: {_pw_err}", "warn")

    # ══ PASS 3: Rank & Rewrite ══
    if _ctx().all_leads:
        _ranked = rank_and_rewrite(_ctx().all_leads)
        _ctx().all_leads.clear()
        _ctx().all_leads.extend(_ranked)
        # Persist Pass 3 enrichments (follow-ups, priority, is_top10) immediately
        try:
            _agent_loop.run_until_complete(_db.save_leads_bulk(_ctx().user_id, _ctx().all_leads))
            emit_log("Pass 3 data saved", "ok")
        except Exception as _p3e:
            emit_log(f"Pass 3 save failed: {_p3e}", "warn")

    # Close the reusable event loop
    try:
        _agent_loop.close()
    except Exception:
        pass

    if _ctx().all_leads:
        save_csv(_ctx().all_leads); save_master_leads(_ctx().all_leads)
    save_seen_history(); save_domain_blocklist(); _ctx()._save_done = True
    # Mark agent state as completed
    try:
        _agent_state["status"] = "completed"
        _agent_state["leads_found"] = len(_ctx().all_leads)
        _atomic_write(AGENT_STATE_FILE, _agent_state)
        do_backup("post_run")
    except: pass

    regions, rc, we = {}, 0, 0
    for l in _ctx().all_leads:
        c = l.get("country","?"); regions[c] = regions.get(c,0)+1
        if l.get("is_recurring"): rc += 1
        if l.get("contact_email"): we += 1

    _status_map = {"stopped": "Stopped", "credits": "Out of credits",
                   "exhausted": "Search complete", "complete": "Search complete"}
    _final_label = _status_map.get(_stop_reason, "Search complete")
    _log_level = "ok"
    if len(_ctx().all_leads) == 0:
        if _pages_analysed == 0:
            _final_label = "No results found"
            emit_log(f"No URLs could be fetched after {batch_num} batch(es). Check SearXNG connection or adjust business profile.", "warn")
            emit_thought("Couldn't reach any pages — search engine or targets may be blocking requests. Try again or adjust your business profile.", "idle")
            _log_level = "warn"
        else:
            emit_log(f"Analysed {_pages_analysed} pages but none qualified. Try adjusting your business profile or target countries.", "warn")
            emit_thought(f"Analysed {_pages_analysed} pages but none matched your ICP strongly enough. This niche may be harder to reach — try broadening your industry, loosening location filters, or editing your wizard profile.", "idle")
            _log_level = "warn"
    else:
        emit_thought("All done! Ready for the next run.", "done")
    emit_status(f"{_final_label} — {len(_ctx().all_leads)} leads", "done")
    emit_log(f"{_final_label} — {len(_ctx().all_leads)} leads | URLs checked: {urls_seen} | Pages analysed: {_pages_analysed} | Batches: {batch_num}", _log_level)

    # ── Generate Scan Report ──
    try:
        hot = sum(1 for l in _ctx().all_leads if l.get("fit_score", 0) >= 9)
        warm = sum(1 for l in _ctx().all_leads if 7 <= l.get("fit_score", 0) < 9)
        top10 = sorted([l for l in _ctx().all_leads if l.get("fit_score", 0) >= 7],
                       key=lambda x: x.get("priority_score", x.get("fit_score", 0) * 10), reverse=True)[:10]
        
        report_lines = [
            f"{'='*50}",
            f"HUNTOVA SCAN REPORT — {datetime.now().strftime('%Y-%m-%d %H:%M')}",
            f"{'='*50}",
            f"Total leads found: {len(_ctx().all_leads)}",
            f"Hot leads (9-10):  {hot}",
            f"Warm leads (7-8):  {warm}",
            f"With email:        {we}",
            f"Ongoing needs:     {rc}",
            f"Countries:         {len(regions)}",
            f"",
            f"TOP {len(top10)} PRIORITY LEADS:",
            f"{'-'*50}",
        ]
        for i, l in enumerate(top10):
            ps = l.get("priority_score", l.get("fit_score", 0) * 10)
            report_lines.append(
                f"  #{i+1} [{l.get('fit_score',0)}/10] {l.get('org_name','?')[:35]} "
                f"| {l.get('country','?')} | {l.get('event_type','?')}"
                f"{' | 📧' if l.get('contact_email') else ''}"
                f"{' | 🔁' if l.get('is_recurring') else ''}"
            )
            why = l.get("why_fit", "")[:60]
            if why:
                report_lines.append(f"       → {why}")
        
        report_lines.append(f"{'='*50}")
        report_text = "\n".join(report_lines)
        
        # Save to file
        report_path = os.path.join(RUN_DIR, "scan_report.txt")
        with open(report_path, "w", encoding="utf-8") as rf:
            rf.write(report_text)
        emit_log(f"📋 Scan report saved: {report_path}", "save")
        
        # Emit as SSE event for dashboard
        _scan_report_data = {
            "total": len(_ctx().all_leads), "hot": hot, "warm": warm,
            "with_email": we, "recurring": rc, "countries": len(regions),
            "top10": [{
                "rank": i+1,
                "org": l.get("org_name","?"),
                "score": l.get("fit_score", 0),
                "priority": l.get("priority_score", 0),
                "country": l.get("country","?"),
                "has_email": bool(l.get("contact_email")),
                "is_recurring": bool(l.get("is_recurring")),
                "why": l.get("why_fit","")[:80],
            } for i, l in enumerate(top10)],
            "report_text": report_text,
            "timestamp": datetime.now().isoformat(),
        }
        _c = _ctx()
        if _c: _c.bus.emit("scan_report", _scan_report_data)
        else: bus.emit("scan_report", _scan_report_data)
    except Exception as e:
        emit_log(f"Report generation error: {e}", "warn")


def check_lm_studio():
    try:
        r = requests.get(f"{LM_STUDIO_URL}/models", timeout=5); r.raise_for_status()
        names = [m.get("id","") for m in r.json().get("data",[])]
        ok = any(MODEL_ID in n for n in names)
        emit_log(f"LM Studio {'OK' if ok else 'running, model not listed: ' + MODEL_ID}", "ok" if ok else "warn")
        return ok
    except Exception as e: emit_log(f"LM Studio: {e}", "warn"); return False

def check_searxng():
    """Verify SearXNG is reachable and returns valid JSON."""
    _search_url = SEARXNG_URL.rstrip("/") + "/search"
    try:
        r = requests.get(_search_url, params={"q": "test", "format": "json"}, timeout=5)
        r.raise_for_status()
        data = r.json()
        return isinstance(data.get("results"), list)
    except Exception:
        return False


def run_agent_scoped(ctx):
    """Run agent with per-user context (SaaS mode).
    Sets thread-local context so all emit_*/check_* functions use ctx.
    The existing run_agent() code works unchanged — it calls emit_log, _check_stop, etc.
    which now check _tl.ctx first."""
    import asyncio
    _tl.ctx = ctx
    user_settings = None

    # Load user's seen history from DB
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)  # required for asyncio.gather in non-main thread
        _db = __import__("db")
        try:
            _seen_urls, _seen_fps, _domain_fails, user_settings = loop.run_until_complete(
                asyncio.gather(
                    _db.get_seen_urls(ctx.user_id),
                    _db.get_seen_fingerprints(ctx.user_id),
                    _db.get_domain_blocklist(ctx.user_id),
                    _db.get_settings(ctx.user_id),
                )
            )
            ctx.seen_urls = _seen_urls
            ctx.seen_fps = _seen_fps
            ctx.domain_fails = _domain_fails
        finally:
            loop.close()
    except Exception as e:
        ctx.emit_log(f"Failed to load user data: {e}", "warn")

    # Snapshot initial state so we can compute deltas to persist later
    ctx._initial_seen = set(ctx.seen_urls)
    ctx._initial_fps = set(ctx.seen_fps)
    _initial_domain_fails = dict(ctx.domain_fails)

    # ── INSTRUMENTATION: log state loaded from DB ──
    _instr_uid = ctx.user_id
    _instr_rid = getattr(ctx, 'run_id', '?')
    print(f"[INSTR] user={_instr_uid} run={_instr_rid} START seen_urls={len(ctx.seen_urls)} seen_fps={len(ctx.seen_fps)} domain_fails={len(ctx.domain_fails)} settings={'yes' if user_settings else 'no'}")

    # Store user settings on context so load_settings() reads from ctx, not shared file
    ctx._user_settings = user_settings

    # Override globals for this thread's scope
    # NOTE: These globals are a concurrency risk with MAX_CONCURRENT_AGENTS > 1.
    # The thread-local _tl.ctx is the safe path; these are bridges for legacy run_agent() code.
    # All per-run state lives on ctx — no module-global bridges needed.
    # run_agent() accesses ctx.seen_urls, ctx.seen_fps, ctx.all_leads via _ctx().
    # Helper functions (is_blocked, record_domain_fail) already check _ctx() first.
    ctx.all_leads = []

    # agent_config is now read via _get_agent_config() which checks ctx first — no global bridge needed

    _instr_start = time.time()
    try:
        run_agent()
    finally:
        _instr_elapsed = round(time.time() - _instr_start, 1)

        # ── INSTRUMENTATION: log state at end of run ──
        _new_seen = len(ctx.seen_urls - ctx._initial_seen)
        _new_fps = len(ctx.seen_fps - ctx._initial_fps)
        _new_domain_fails = len(set(ctx.domain_fails.keys()) - set(_initial_domain_fails.keys()))
        print(f"[INSTR] user={_instr_uid} run={_instr_rid} END elapsed={_instr_elapsed}s leads={len(ctx.all_leads)} new_seen={_new_seen} new_fps={_new_fps} new_domain_fails={_new_domain_fails}")

        # Save back to DB.
        # Stability fix (multi-agent bug #15): the previous version
        # opened the event loop INSIDE try/except but only closed it on
        # the success path (loop.close() at the bottom). Any exception
        # in between leaked the loop's epoll/kqueue fd. Now in a proper
        # try/finally so it always closes.
        loop = None
        try:
            loop = asyncio.new_event_loop()
            _db = __import__("db")
            # Persist newly-seen URLs (delta from initial snapshot)
            new_seen = ctx.seen_urls - ctx._initial_seen
            if new_seen:
                loop.run_until_complete(_db.add_seen_urls_bulk(ctx.user_id, list(new_seen)))
            # Persist new fingerprints
            _initial_fps = getattr(ctx, '_initial_fps', set())
            new_fps = ctx.seen_fps - _initial_fps
            for fp in new_fps:
                try:
                    loop.run_until_complete(_db.add_seen_fingerprint(ctx.user_id, fp))
                except Exception:
                    pass
            # Persist domain fail updates back to DB (set absolute count, not +1)
            for domain, count in ctx.domain_fails.items():
                old_count = _initial_domain_fails.get(domain, 0)
                if count > old_count:
                    try:
                        loop.run_until_complete(_db.set_domain_fail_count(ctx.user_id, domain, count))
                    except Exception:
                        pass
            # Save all leads to DB
            if ctx.all_leads:
                loop.run_until_complete(_db.save_leads_bulk(ctx.user_id, ctx.all_leads))
        except Exception as e:
            try: ctx.emit_log(f"Failed to save user data: {e}", "warn")
            except: pass
        finally:
            if loop is not None:
                try: loop.close()
                except Exception: pass
        _tl.ctx = None


