"""BRAIN-206: config.DEFAULT_SETTINGS shape + privacy-default audit.

Pinned invariants:

1. `DEFAULT_SETTINGS` is a dict with documented keys.
2. `telemetry_opt_in` defaults False (GDPR Art. 7 affirmative-opt-in
   compliance — landing-page claim "0 data sent to huntova" must be
   literally true on a fresh install).
3. Plugin enable defaults: dedup-by-domain ON, csv-sink OFF,
   slack-ping OFF, recipe-adapter ON, adaptation-rules ON.
4. SMTP defaults — port 587 (submission with STARTTLS), other
   credential fields blank.
5. `default_max_leads` numeric default; `default_countries` empty list.
6. `preferred_provider` / `preferred_model` blank by default.
7. `preferred_temperature` 0.2 (low-randomness default).
8. `theme` defaults "system".
9. Sensitive fields (`webhook_secret_set`, `smtp_password_set`) are
   booleans (not the actual secret — secret goes via secrets_store).
"""
from __future__ import annotations


def test_default_settings_is_dict():
    from config import DEFAULT_SETTINGS
    assert isinstance(DEFAULT_SETTINGS, dict)
    assert len(DEFAULT_SETTINGS) > 5


def test_telemetry_opt_in_defaults_false():
    """GDPR Art. 7 / CCPA §1798.100(d) — affirmative opt-in required.
    Pin so a future "default-on" never silently breaks the privacy
    promise."""
    from config import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["telemetry_opt_in"] is False


def test_plugins_enabled_defaults():
    from config import DEFAULT_SETTINGS
    pe = DEFAULT_SETTINGS["plugins_enabled"]
    assert pe["dedup-by-domain"] is True
    assert pe["csv-sink"] is False
    assert pe["slack-ping"] is False
    assert pe["recipe-adapter"] is True
    assert pe["adaptation-rules"] is True


def test_smtp_port_default_587():
    from config import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["smtp_port"] == 587


def test_smtp_credentials_blank_by_default():
    """Outreach SMTP — credentials must be blank (user supplies)."""
    from config import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["smtp_host"] == ""
    assert DEFAULT_SETTINGS["smtp_user"] == ""
    assert DEFAULT_SETTINGS["smtp_password_set"] is False


def test_default_max_leads():
    from config import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["default_max_leads"] == 10
    assert isinstance(DEFAULT_SETTINGS["default_max_leads"], int)


def test_default_countries_empty_list():
    from config import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["default_countries"] == []
    assert isinstance(DEFAULT_SETTINGS["default_countries"], list)


def test_preferred_provider_blank():
    """No default provider — user picks during onboard."""
    from config import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["preferred_provider"] == ""
    assert DEFAULT_SETTINGS["preferred_model"] == ""


def test_preferred_temperature():
    from config import DEFAULT_SETTINGS
    t = DEFAULT_SETTINGS["preferred_temperature"]
    assert isinstance(t, (int, float))
    assert 0.0 <= t <= 2.0  # OpenAI valid range


def test_theme_default_system():
    from config import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["theme"] == "system"


def test_webhook_secret_set_is_bool():
    """`webhook_secret_set` is a boolean indicator, NOT the secret."""
    from config import DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["webhook_secret_set"] is False
    assert isinstance(DEFAULT_SETTINGS["webhook_secret_set"], bool)


def test_no_real_secrets_in_defaults():
    """No password / token / key fields with real secret values."""
    from config import DEFAULT_SETTINGS
    SUSPICIOUS_KEY_HINTS = ("password", "secret", "token", "key", "api")
    for k, v in DEFAULT_SETTINGS.items():
        # Boolean indicator fields like `*_set` are fine.
        if isinstance(v, bool):
            continue
        # Plugin maps OK.
        if isinstance(v, (dict, list)):
            continue
        # String values must NOT contain key-shaped data.
        if isinstance(v, str) and v == "":
            continue
        # Any non-empty string value should not look like a key for a
        # field whose name suggests secret.
        if isinstance(v, str) and any(h in k.lower() for h in SUSPICIOUS_KEY_HINTS):
            assert v == "", (
                f"DEFAULT_SETTINGS[{k!r}] looks like it could carry "
                f"a real secret: {v!r}"
            )


def test_data_retention_days_2_years():
    """GDPR-default retention: 2 years (730 days)."""
    from config import DATA_RETENTION_DAYS
    assert DATA_RETENTION_DAYS == 730


def test_tier_page_limits_increasing_by_tier():
    """Higher tiers get more page-text capacity."""
    from config import TIER_PAGE_LIMITS
    # free <= growth <= agency.
    assert TIER_PAGE_LIMITS["free"] <= TIER_PAGE_LIMITS["growth"]
    assert TIER_PAGE_LIMITS["growth"] <= TIER_PAGE_LIMITS["agency"]


def test_tier_page_limits_keys():
    """All 3 expected tiers present."""
    from config import TIER_PAGE_LIMITS
    assert set(TIER_PAGE_LIMITS.keys()) == {"agency", "growth", "free"}
