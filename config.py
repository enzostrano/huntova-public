"""
Huntova SaaS — Configuration
All constants, env vars, paths.
"""
import os


def _env(name: str, default: str = "") -> str:
    """Read env var and strip wrapping quotes / whitespace.

    Users frequently paste `KEY="value"` lines from .env templates into
    their shell, which leaves literal quote characters in the value and
    silently breaks downstream auth. Strip them once at load.
    """
    raw = (os.environ.get(name) or default).strip()
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in ('"', "'"):
        raw = raw[1:-1]
    return raw


# ── Version ──
VERSION = "2.0-saas"

# ── AI Provider ──
AI_PROVIDER = _env("HV_AI_PROVIDER", "anthropic")
GEMINI_API_KEY = _env("HV_GEMINI_KEY")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
GEMINI_MODEL = _env("HV_GEMINI_MODEL", "gemini-2.5-flash")
GEMINI_MODEL_PRO = _env("HV_GEMINI_MODEL_PRO", "gemini-2.5-pro")
OPENAI_API_KEY = _env("HV_OPENAI_KEY")
OPENAI_MODEL = _env("HV_OPENAI_MODEL", "gpt-4o-mini")
ANTHROPIC_API_KEY = _env("HV_ANTHROPIC_KEY")
ANTHROPIC_MODEL = _env("HV_ANTHROPIC_MODEL", "claude-sonnet-4-5-20250929")
ANTHROPIC_OPENAI_COMPAT_URL = "https://api.anthropic.com/v1/"  # Anthropic SDK preferred — see providers.py
LM_STUDIO_URL = "http://localhost:1234/v1"
LM_STUDIO_MODEL = "qwen/qwen3-32b"

# AI_PROVIDER drives the legacy `client = OpenAI(base_url=API_URL,
# api_key=API_KEY)` path that app.py / server.py use for every
# `client.chat.completions.create(...)` call. BYOK: pick whichever
# provider has a key set.
if AI_PROVIDER == "openai" and OPENAI_API_KEY:
    API_URL = None  # OpenAI SDK default
    API_KEY = OPENAI_API_KEY
    MODEL_ID = OPENAI_MODEL
elif AI_PROVIDER == "anthropic" and ANTHROPIC_API_KEY:
    # Anthropic isn't OpenAI-compat at the chat-completions level. The
    # legacy code path won't work directly — providers.py is the real
    # path. For now alias to Gemini as a safer default if Gemini key is
    # also set; otherwise the legacy `client.chat.completions.create`
    # calls will fail at the network layer (and the user can switch).
    if GEMINI_API_KEY:
        API_URL = GEMINI_URL
        API_KEY = GEMINI_API_KEY
        MODEL_ID = GEMINI_MODEL
    else:
        API_URL = ANTHROPIC_OPENAI_COMPAT_URL
        API_KEY = ANTHROPIC_API_KEY
        MODEL_ID = ANTHROPIC_MODEL
elif AI_PROVIDER == "gemini" or GEMINI_API_KEY:
    API_URL = GEMINI_URL
    API_KEY = GEMINI_API_KEY
    MODEL_ID = GEMINI_MODEL
else:
    API_URL = LM_STUDIO_URL
    API_KEY = "lm-studio"
    MODEL_ID = LM_STUDIO_MODEL

# ── Tier-based AI model selection ──
# Per-provider tier mapping. Picks a stronger model for "agency" tier
# when the provider has a clear flagship/standard split, or falls back
# to MODEL_ID (already provider-aware via the AI_PROVIDER branch above).
def _tier_models_for_provider() -> dict:
    """Resolve {tier: model_id} for the active provider.

    Reads HV_<PROVIDER>_MODEL_PRO / HV_<PROVIDER>_MODEL where set, so
    a user with HV_AI_PROVIDER=anthropic + HV_ANTHROPIC_KEY gets
    claude-sonnet for free/growth and claude-opus (or HV_ANTHROPIC_MODEL_PRO
    override) for agency. Was hardcoded to Gemini IDs — caused crashes
    when the agent passed a Gemini model string to a non-Gemini provider.
    """
    provider = (AI_PROVIDER or "gemini").lower()
    if provider == "gemini":
        std = GEMINI_MODEL
        pro = GEMINI_MODEL_PRO
    elif provider == "anthropic":
        std = ANTHROPIC_MODEL
        pro = os.environ.get("HV_ANTHROPIC_MODEL_PRO", "claude-opus-4-7-20251015")
    elif provider == "openai":
        std = OPENAI_MODEL
        pro = os.environ.get("HV_OPENAI_MODEL_PRO", "gpt-5")
    elif provider == "ollama":
        std = os.environ.get("HV_OLLAMA_MODEL", "llama3.2")
        pro = os.environ.get("HV_OLLAMA_MODEL_PRO", std)
    elif provider == "lmstudio":
        std = os.environ.get("HV_LMSTUDIO_MODEL", LM_STUDIO_MODEL)
        pro = os.environ.get("HV_LMSTUDIO_MODEL_PRO", std)
    else:
        # Generic OpenAI-compatible providers (groq, deepseek, mistral,
        # together, perplexity, openrouter, llamafile, custom) — fall
        # back to MODEL_ID since the provider's own default is already
        # baked into providers.py:_DEFAULT_MODEL.
        std = MODEL_ID
        pro = os.environ.get(f"HV_{provider.upper()}_MODEL_PRO", MODEL_ID)
    return {"agency": pro, "growth": std, "free": std}


TIER_MODELS = _tier_models_for_provider()
# Page text limits per tier — Pro can handle more context effectively
TIER_PAGE_LIMITS = {
    "agency": 6000,   # Pro handles long context well
    "growth": 4000,   # Flash standard
    "free":   2500,   # Flash with shorter context (faster, cheaper)
}

# ── Paths ──
BASE_DIR = os.environ.get("HV_BASE_DIR", os.path.dirname(os.path.abspath(__file__)))
PORT = int(os.environ.get("HV_PORT", "5000"))
SECRET_KEY = os.environ.get("HV_SECRET_KEY", "")
if not SECRET_KEY:
    if os.environ.get("RAILWAY_ENVIRONMENT") or os.environ.get("RENDER"):
        raise RuntimeError("FATAL: HV_SECRET_KEY must be set in production. Cannot start with dev fallback.")
    SECRET_KEY = "huntova-dev-secret-LOCAL-ONLY"
    # Suppress the warning for non-server CLI commands (every CLI import
    # would otherwise pollute output). Only print when uvicorn is in
    # progress of booting OR HV_VERBOSE_LOGS=1 is set.
    if os.environ.get("HV_VERBOSE_LOGS") or "uvicorn" in (os.environ.get("_") or ""):
        import sys as _sys
        print("[WARNING] Using dev SECRET_KEY — set HV_SECRET_KEY for production", file=_sys.stderr)

# ── Google OAuth ──
GOOGLE_CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI_PATH = "/auth/google/callback"

# ── Email (SMTP) ──
SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
SMTP_FROM_EMAIL = os.environ.get("SMTP_FROM_EMAIL", "noreply@huntova.com")
SMTP_FROM_NAME = os.environ.get("SMTP_FROM_NAME", "Huntova")

# ── Public URL ──
# Defaults to the production domain. Set HV_PUBLIC_URL in dev / staging
# to point share URLs (and OG images) at the right host.
PUBLIC_URL = os.environ.get("HV_PUBLIC_URL", "https://huntova.com")

# ── Admin ──
ADMIN_EMAILS = [e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()]

# ── Database ──
DATABASE_URL = os.environ.get("DATABASE_URL", "")

_app_mode_now = (os.environ.get("APP_MODE") or "cloud").strip().lower()
# Local CLI default points at a well-known public SearXNG instance with
# a JSON API. Cloud default keeps the original 127.0.0.1 expectation —
# Railway runs SearXNG as a sidecar there. Users on either path can
# always override SEARXNG_URL to point at a self-hosted instance for
# privacy + rate-limit reasons.
_searxng_default = "https://searx.be" if _app_mode_now == "local" else "http://127.0.0.1:8888"
_raw_searxng = os.environ.get("SEARXNG_URL", _searxng_default).strip()
# Ensure URL has scheme — Railway env vars sometimes omit https://
if _raw_searxng and not _raw_searxng.startswith("http"):
    _raw_searxng = "https://" + _raw_searxng
SEARXNG_URL = _raw_searxng

# ── Directories ──
BASE_OUTPUT_DIR = os.path.join(BASE_DIR, "reports")
BACKUP_DIR = os.path.join(BASE_DIR, "backups")
LOG_DIR = os.path.join(BASE_DIR, "logs")
def _resolve_asset_dir(name: str) -> str:
    """Find an asset directory at runtime.

    Dev checkout / `pip install -e .` → asset dir lives next to config.py.
    `pipx install` from git URL or wheel → asset dir lands at
    `sys.prefix/<name>` because setup.py declared it via `data_files`.
    Falls back to the config.py-adjacent path so the helpful "Directory
    'static' does not exist" error message names the expected location.
    """
    import sys as _sys
    _here = os.path.dirname(os.path.abspath(__file__))
    _local = os.path.join(_here, name)
    if os.path.isdir(_local):
        return _local
    _prefix_local = os.path.join(_sys.prefix, name)
    if os.path.isdir(_prefix_local):
        return _prefix_local
    # `data_files` on some platforms lands under sys.prefix/share/<pkg>/
    _share = os.path.join(_sys.prefix, "share", "huntova", name)
    if os.path.isdir(_share):
        return _share
    return _local  # original — let downstream raise with a clear path


STATIC_DIR = _resolve_asset_dir("static")
TEMPLATES_DIR = _resolve_asset_dir("templates")

# ── Agent tuning ──
MAX_RESULTS_PER_QUERY = 8  # More results per query = more chances to find leads
MIN_SCORE_TO_KEEP = 7
MAX_RETRIES = 2
DELAY_URL = 0.4
DELAY_QUERY = 1.0
DEEP_LINKS = 6
DEEP_MIN = 7
DEEP_DELAY = 0.3
CHECKPOINT_N = 5
SEARCH_TIMEOUT = 15
FETCH_TIMEOUT_MS = 8000
IDLE_TIMEOUT_MS = 5000
SCREENSHOT_INTERVAL = 1.5
SMART_BROWSE_BUDGET = 6
SMART_BROWSE_SCROLLS = 8
SMART_BROWSE_CLICKS = 3
MAX_VIDEO_VISITS = 3
VIDEO_FETCH_TIMEOUT = 5000

USER_AGENT = (
    "Mozilla/5.0 (compatible; HuntovaBot/1.0; +https://huntova.com/privacy) "
    "AppleWebKit/537.36 (KHTML, like Gecko)"
)
DATA_RETENTION_DAYS = 730  # 2 years GDPR default

# ── Tiers & Credits ──
TIERS = {
    "free":    {"name": "Free",    "price": 0,   "credits": 3,   "max_leads_month": 3,   "currency": "eur"},
    "growth":  {"name": "Growth",  "price": 49,  "credits": 25,  "max_leads_month": 25,  "currency": "eur"},
    "agency":  {"name": "Agency",  "price": 149, "credits": 50,  "max_leads_month": 999, "currency": "eur"},
}
# All scans cost 1 credit per lead regardless of tier
COST_PER_LEAD = 1

# ── Concurrency ──
# Module-level globals were refactored to ctx in commits bc2b020..64050bb.
# Single agent only — multi-agent requires full global-to-ctx refactor (incomplete)
MAX_CONCURRENT_AGENTS = 1

# ── Session ──
SESSION_EXPIRY_HOURS = 72
SESSION_COOKIE_NAME = "hv_session"

# ── Mega-corp domains (auto-skip) ──
MEGA_CORP_DOMAINS = {
    "google.com","youtube.com","microsoft.com","apple.com","amazon.com","meta.com","facebook.com",
    "netflix.com","salesforce.com","oracle.com","ibm.com","intel.com","cisco.com","adobe.com",
    "samsung.com","siemens.com","sap.com","dell.com","hp.com","nvidia.com","qualcomm.com",
    "paypal.com","uber.com","airbnb.com","spotify.com","twitter.com","x.com","linkedin.com",
    "tiktok.com","bytedance.com","alibaba.com","tencent.com","baidu.com","tesla.com",
    "bmw.com","mercedes-benz.com","volkswagen.com","toyota.com","shell.com","bp.com",
    "jpmorgan.com","goldmansachs.com","morganstanley.com","citigroup.com","hsbc.com",
    "deloitte.com","pwc.com","ey.com","kpmg.com","mckinsey.com","bcg.com","bain.com",
    "unilever.com","nestle.com","coca-cola.com","pepsico.com","procter-gamble.com",
    "johnson-johnson.com","pfizer.com","novartis.com","roche.com","merck.com","bayer.com",
    "redcross.org","unicef.org","who.int","worldbank.org","un.org","imf.org",
    "mit.edu","harvard.edu","stanford.edu","oxford.ac.uk","cambridge.org",
    "accenture.com","infosys.com","wipro.com","tcs.com","capgemini.com",
    "zoom.us","zoom.com","gotomeeting.com","gotowebinar.com","goto.com",
    "webex.com","teams.microsoft.com","livestorm.co","hopin.com","airmeet.com",
    "bigmarker.com","on24.com","demio.com","streamyard.com","restream.io",
    "riverside.fm","vmix.com","obs.live","vimeo.com","brightcove.com",
    "eventbrite.com","cvent.com","bizzabo.com","swoogo.com","whova.com",
    "hubilo.com","goldcast.io","welcome.com","run.events","6connex.com",
    "atlassian.com","slack.com","dropbox.com","twilio.com","shopify.com",
    "stripe.com","square.com","zendesk.com","hubspot.com","mailchimp.com",
    "canva.com","figma.com","notion.so","asana.com","monday.com",
    "servicenow.com","workday.com","snowflake.com","databricks.com",
    "verizon.com","att.com","tmobile.com","comcast.com","eaton.com",
    "bbc.com","cnn.com","reuters.com","bloomberg.com","nytimes.com",
    "theguardian.com","washingtonpost.com","forbes.com","techcrunch.com",
    # Retail / consumer (never B2B prospects)
    "bestbuy.com","walmart.com","target.com","costco.com","homedepot.com","lowes.com",
    "macys.com","nordstrom.com","ikea.com","wayfair.com","etsy.com","ebay.com",
    "aliexpress.com","wish.com","zappos.com","gap.com","zara.com","hm.com",
    "wholefoodsmarket.com","kroger.com","walgreens.com","cvs.com",
    # More news / media / blogs (not prospects)
    "medium.com","substack.com","wired.com","arstechnica.com","theinformation.com",
    "businessinsider.com","cnbc.com","foxnews.com","abcnews.go.com","nbcnews.com",
    "huffpost.com","buzzfeed.com","vice.com","vox.com","theatlantic.com",
    "wsj.com","ft.com","economist.com","time.com","newsweek.com",
    # Social / content platforms (not prospects)
    "reddit.com","quora.com","stackoverflow.com","stackexchange.com",
    "pinterest.com","instagram.com","tumblr.com","twitch.tv",
    # Job boards (not prospects)
    "indeed.com","glassdoor.com","monster.com","reed.co.uk","stepstone.com",
    "upwork.com","fiverr.com","freelancer.com","toptal.com",
    # Directories / aggregators (not prospects)
    "yelp.com","tripadvisor.com","g2.com","capterra.com","trustpilot.com",
    "crunchbase.com","pitchbook.com","owler.com","zoominfo.com",
    "yellowpages.com","whitepages.com","bbb.org",
    # Education / government
    "coursera.org","udemy.com","edx.org","khanacademy.org",
    "gov.uk","europa.eu","whitehouse.gov","congress.gov",
    # Reference
    "wikipedia.org","wikimedia.org","britannica.com","dictionary.com",
    # Microsoft / Yahoo / Google subdomains that slip through
    "yahoo.com","live.com","msn.com","bing.com","microsoft.ai",
    "outlook.com","hotmail.com","aol.com","ask.com",
    # Additional noise
    "github.com","gitlab.com","bitbucket.org",
    "archive.org","scribd.com","slideshare.net","issuu.com",
    "booking.com","expedia.com","hotels.com",
    # ── CIS / Russian social, media, entertainment ──
    "vk.com","vkvideo.ru","ok.ru","mail.ru","yandex.ru","yandex.com",
    "rambler.ru","gazeta.ru","rbc.ru","ria.ru","tass.ru","lenta.ru",
    "iz.ru","kp.ru","mk.ru","aif.ru","fontanka.ru","pikabu.ru",
    "livejournal.com","habr.com","dtf.ru","vc.ru","dzen.ru",
    "afisha.ru","kassir.ru","ponominalu.ru","bileter.ru","concert.ru",
    "timepad.ru","radario.ru","kinopoisk.ru","sxodim.com",
    "onliner.by","internet-bilet.ua","concert.ua","bilet.ua",
    "klops.ru","e1.ru","ngs.ru","74.ru","66.ru","nn.ru",
    # ── French media / entertainment ──
    "fnacspectacles.com","infoconcert.com","leparisien.fr","lefigaro.fr",
    "lemonde.fr","20minutes.fr","liberation.fr","ouest-france.fr",
    "allocine.fr","sortiraparis.com","digitick.com","francebillet.com",
    # ── German / Spanish / Italian media ──
    "spiegel.de","bild.de","sueddeutsche.de","faz.net","stern.de",
    "elpais.com","elmundo.es","lavanguardia.com","abc.es",
    "corriere.it","repubblica.it","lastampa.it","ilsole24ore.com",
    # ── Ticket aggregators / consumer event sites ──
    "ticketmaster.com","ticketmaster.co.uk","ticketmaster.de","ticketmaster.fr",
    "ticketmaster.it","ticketmaster.es","ticketmaster.nl",
    "livenation.com","seetickets.com","dice.fm","skiddle.com",
    "stubhub.com","viagogo.com","bandsintown.com","songkick.com",
    "eventcartel.com","ticketswap.com","ticketea.com",
    # ── Financial news / investor relations ──
    "rttnews.com","seekingalpha.com","marketwatch.com","barrons.com",
    "morningstar.com","fool.com","benzinga.com","thestreet.com",
    "prnewswire.com","businesswire.com","globenewswire.com","accesswire.com",
    "investopedia.com","nerdwallet.com","bankrate.com",
    # ── Missing mega-corps / brands ──
    "delta.com","gm.com","dow.com","ford.com","ge.com","honeywell.com",
    "3m.com","boeing.com","airbus.com","caterpillar.com",
    "nike.com","adidas.com","puma.com","reebok.com","newbalance.com",
    "sony.com","lg.com","panasonic.com","philips.com","bosch.com",
    "casio.com","canon.com","nikon.com","on-running.com",
    # ── Gaming ──
    "steamcommunity.com","steampowered.com","epicgames.com","roblox.com",
    "ign.com","gamespot.com","kotaku.com","polygon.com",
    # ── Academic / research (non-prospects) ──
    "researchgate.net","academia.edu","sciencedirect.com","springer.com",
    "wiley.com","nature.com","ncbi.nlm.nih.gov","pubmed.ncbi.nlm.nih.gov",
    "ucanr.edu","iere.org",
    # ── Misc consumer / non-B2B ──
    "healthline.com","webmd.com","mayoclinic.org",
    "imdb.com","rottentomatoes.com","metacritic.com",
    "weather.com","accuweather.com",
    "doordash.com","grubhub.com","ubereats.com",
}

# ── Default settings ──
DEFAULT_SETTINGS = {
    "booking_url": "",
    "from_name": "",
    "from_email": "",
    "phone": "",
    "website": "",
    # Plugins (Settings → Plugins tab). Maps bundled plugin name → enabled bool.
    # Inline config (csv path, slack webhook URL) lives alongside; secrets
    # (slack webhook URL, SMTP password) go via secrets_store, never inline.
    "plugins_enabled": {
        "csv-sink": False,
        "dedup-by-domain": True,
        "slack-ping": False,
        "recipe-adapter": True,
        "adaptation-rules": True,
    },
    "plugin_csv_sink_path": "",
    # Generic outbound webhook (Settings → Webhooks tab). Fires on post_save.
    "webhook_url": "",
    # webhook_secret is stored via secrets_store, NOT in plain settings JSON.
    "webhook_secret_set": False,
    # Outreach SMTP (Settings → Outreach tab). Password via secrets_store.
    "smtp_host": "",
    "smtp_port": 587,
    "smtp_user": "",
    "smtp_password_set": False,
    # Preferences (Settings → Preferences tab).
    "theme": "system",          # dark | light | system
    "reduced_motion": False,
    # Default OFF so the landing claim "0 data sent to huntova" is literally
    # true on a fresh install. GDPR Art.7 / CCPA §1798.100(d) require
    # affirmative opt-in for collection, not opt-out. Users can flip this on
    # via `huntova telemetry enable` if they want to share usage stats.
    "telemetry_opt_in": False,
}
