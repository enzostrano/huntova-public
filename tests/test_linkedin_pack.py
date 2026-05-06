"""Tests for the LinkedIn outreach pack helper.

The validator enforces hard contracts regardless of what the AI returned —
we never trust the model on URL shape, char caps, or language. These tests
hit the validator directly (no AI mock needed) and verify each guarantee.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def app_mod(local_env, monkeypatch):
    """Import app inside the local sandbox so module-level env reads use the test paths."""
    import importlib
    import sys
    if "app" in sys.modules:
        importlib.reload(sys.modules["app"])
    import app as _app
    return _app


# ─── _linkedin_search_url_for ─────────────────────────────────────────────────

def test_search_url_builder_uses_search_endpoint(app_mod):
    url = app_mod._linkedin_search_url_for("Eni S.p.A.", role="Head of IR")
    assert url.startswith("https://www.linkedin.com/search/results/people/?keywords=")


def test_search_url_builder_url_encodes_special_chars(app_mod):
    url = app_mod._linkedin_search_url_for("L'Oréal", role="Directrice Communication")
    # Spaces become + or %20, accents become percent-escapes — never raw
    assert " " not in url.split("?", 1)[1]
    assert "é" not in url


def test_search_url_builder_falls_back_when_company_empty(app_mod):
    url = app_mod._linkedin_search_url_for("", role="", name="")
    assert url.startswith("https://www.linkedin.com/search/results/people/?keywords=")


def test_search_url_builder_includes_name_when_provided(app_mod):
    url = app_mod._linkedin_search_url_for("Acme", role="CFO", name="Mario Rossi")
    assert "Mario" in url and "Rossi" in url and "Acme" in url


# ─── _validate_linkedin_pack: connection-request char cap ────────────────────

def test_validate_truncates_long_connection_request(app_mod):
    long_msg = "x" * 350
    pack = app_mod._validate_linkedin_pack(
        {"connection_request_message": long_msg, "linkedin_search_url":
         "https://www.linkedin.com/search/results/people/?keywords=Acme"},
        "Acme",
    )
    assert len(pack["connection_request_message"]) <= 290
    assert "connection_request_truncated_to_290" in (pack.get("warnings") or [])


def test_validate_keeps_short_connection_request_intact(app_mod):
    msg = "Buongiorno Dott. Esposito, ho seguito i risultati Q1."
    pack = app_mod._validate_linkedin_pack(
        {"connection_request_message": msg, "linkedin_search_url":
         "https://www.linkedin.com/search/results/people/?keywords=Eni"},
        "Eni",
    )
    assert pack["connection_request_message"] == msg
    assert "connection_request_truncated_to_290" not in (pack.get("warnings") or [])


def test_validate_handles_non_string_connection_request(app_mod):
    pack = app_mod._validate_linkedin_pack(
        {"connection_request_message": ["array", "instead", "of", "string"]},
        "Acme",
    )
    assert pack["connection_request_message"] == ""
    assert "connection_request_was_not_string" in (pack.get("warnings") or [])


# ─── _validate_linkedin_pack: URL guard ──────────────────────────────────────

def test_validate_replaces_invented_profile_url_with_search(app_mod):
    fake_slug = "https://www.linkedin.com/in/marco-delfrate-fake-slug-2026/"
    pack = app_mod._validate_linkedin_pack(
        {"linkedin_search_url": fake_slug,
         "decision_maker_role": "Head of IR"},
        "Intesa Sanpaolo",
    )
    assert pack["linkedin_search_url"].startswith(
        "https://www.linkedin.com/search/results/people/?keywords=")
    assert "linkedin_url_was_invented_or_malformed_replaced_with_search" in (pack.get("warnings") or [])


def test_validate_accepts_search_url_unchanged(app_mod):
    real_search = ("https://www.linkedin.com/search/results/people/"
                   "?keywords=Generali%20Investor%20Relations")
    pack = app_mod._validate_linkedin_pack(
        {"linkedin_search_url": real_search}, "Generali",
    )
    assert pack["linkedin_search_url"] == real_search


def test_validate_builds_search_url_when_missing(app_mod):
    pack = app_mod._validate_linkedin_pack({}, "Ferrari")
    assert pack["linkedin_search_url"].startswith(
        "https://www.linkedin.com/search/results/people/?keywords=")
    assert "Ferrari" in pack["linkedin_search_url"]


def test_validate_handles_random_garbage_url(app_mod):
    pack = app_mod._validate_linkedin_pack(
        {"linkedin_search_url": "javascript:alert(1)"}, "Acme",
    )
    assert pack["linkedin_search_url"].startswith(
        "https://www.linkedin.com/search/results/people/?keywords=")


# ─── _validate_linkedin_pack: language clamp ─────────────────────────────────

@pytest.mark.parametrize("lang_in,expected", [
    ("en", "en"), ("it", "it"), ("fr", "fr"), ("de", "de"),
    ("es", "es"), ("pt", "pt"), ("EN", "en"), ("It", "it"), (" fr ", "fr"),
])
def test_validate_accepts_known_languages(app_mod, lang_in, expected):
    pack = app_mod._validate_linkedin_pack({"working_language": lang_in}, "Acme")
    assert pack["working_language"] == expected


@pytest.mark.parametrize("bad_lang", ["jp", "klingon", "zh", "", None, 42, ["it"]])
def test_validate_clamps_unknown_or_invalid_language_to_en(app_mod, bad_lang):
    pack = app_mod._validate_linkedin_pack({"working_language": bad_lang}, "Acme")
    assert pack["working_language"] == "en"


# ─── _validate_linkedin_pack: name handling ──────────────────────────────────

@pytest.mark.parametrize("nm_in", ["", "null", "None", "N/A", "n/a"])
def test_validate_normalizes_placeholder_names_to_none(app_mod, nm_in):
    pack = app_mod._validate_linkedin_pack({"decision_maker_name": nm_in}, "Acme")
    assert pack["decision_maker_name"] is None


def test_validate_keeps_real_name(app_mod):
    pack = app_mod._validate_linkedin_pack(
        {"decision_maker_name": "Francesco Esposito"}, "Eni",
    )
    assert pack["decision_maker_name"] == "Francesco Esposito"


def test_validate_coerces_non_string_name_to_none(app_mod):
    pack = app_mod._validate_linkedin_pack(
        {"decision_maker_name": ["unexpected", "list"]}, "Acme",
    )
    assert pack["decision_maker_name"] is None
    assert "decision_maker_name_coerced_to_null" in (pack.get("warnings") or [])


# ─── _validate_linkedin_pack: follow-up DM cap ───────────────────────────────

def test_validate_truncates_long_follow_up_dm(app_mod):
    long_dm = "y" * 700
    pack = app_mod._validate_linkedin_pack({"follow_up_dm": long_dm}, "Acme")
    assert len(pack["follow_up_dm"]) <= 500
    assert "follow_up_dm_truncated_to_500" in (pack.get("warnings") or [])


def test_validate_handles_missing_follow_up_dm(app_mod):
    pack = app_mod._validate_linkedin_pack({}, "Acme")
    assert pack["follow_up_dm"] == ""


# ─── _validate_linkedin_pack: rejects non-dict ───────────────────────────────

@pytest.mark.parametrize("bad", [None, "string", 42, [1, 2], ["a", "b"]])
def test_validate_rejects_non_dict_pack(app_mod, bad):
    pack = app_mod._validate_linkedin_pack(bad, "Acme")
    assert pack.get("error") == "non_dict_pack"


# ─── _generate_linkedin_pack end-to-end (with mocked AI) ─────────────────────

def test_generate_pack_returns_error_on_empty_lead(app_mod):
    pack = app_mod._generate_linkedin_pack({}, brain={}, page_text="")
    assert pack.get("error") == "lead_has_no_company_or_url"


def test_generate_pack_returns_error_on_non_dict_lead(app_mod):
    pack = app_mod._generate_linkedin_pack("not a dict", brain={}, page_text="")
    assert pack.get("error") == "lead_not_dict"


def test_generate_pack_handles_ai_call_failure(app_mod, monkeypatch):
    def _boom(*args, **kwargs):
        raise RuntimeError("provider down")
    monkeypatch.setattr(app_mod, "_ai_call", _boom)
    pack = app_mod._generate_linkedin_pack(
        {"organization": "Acme", "url": "https://acme.example"},
        brain={}, page_text="",
    )
    assert pack.get("error", "").startswith("ai_call_failed")
    # Even on failure, fallback search URL should be present so the user
    # still has something usable.
    assert pack.get("linkedin_search_url", "").startswith(
        "https://www.linkedin.com/search/results/people/")


def test_generate_pack_handles_ai_returning_non_json(app_mod, monkeypatch):
    monkeypatch.setattr(app_mod, "_ai_call", lambda *a, **k: "not JSON at all, sorry")
    pack = app_mod._generate_linkedin_pack(
        {"organization": "Acme", "url": "https://acme.example"},
        brain={}, page_text="",
    )
    assert pack.get("error") == "ai_returned_non_dict"
    assert pack.get("linkedin_search_url", "").startswith(
        "https://www.linkedin.com/search/results/people/")


def test_generate_pack_validates_ai_output_end_to_end(app_mod, monkeypatch):
    import json
    fake_payload = {
        "decision_maker_role": "Head of Investor Relations",
        "decision_maker_name": "Francesco Esposito",
        # Invented profile slug — validator must replace with search URL
        "linkedin_search_url": "https://www.linkedin.com/in/fake-slug",
        "working_language": "it",
        "language_reason": "FTSE-MIB italian listed company",
        # Too long — validator must truncate to ≤290
        "connection_request_message": "x" * 320,
        "follow_up_dm": "Grazie per la connessione, sarebbe interessante scambiare due idee.",
        "verifiable_observation": "Q1 webcast 24 April 2026",
    }
    fake_ai_response = json.dumps(fake_payload)
    monkeypatch.setattr(app_mod, "_ai_call", lambda *a, **k: fake_ai_response)
    pack = app_mod._generate_linkedin_pack(
        {"organization": "Eni", "url": "https://www.eni.com"},
        brain={
            "buyer_roles_clean": ["Head of Investor Relations"],
            "offer_summary": "broadcast IR production",
        },
        page_text="Eni Q1 2026 results webcast — 24 April 2026...",
    )
    # Validator should have:
    #   - replaced the invented slug with a search URL
    #   - truncated the connection request to ≤290
    #   - kept the language "it"
    #   - preserved the verbatim name from the page
    assert pack["linkedin_search_url"].startswith(
        "https://www.linkedin.com/search/results/people/"), pack
    assert len(pack["connection_request_message"]) <= 290, pack
    assert pack["working_language"] == "it"
    assert pack["decision_maker_name"] == "Francesco Esposito"
    assert "linkedin_url_was_invented_or_malformed_replaced_with_search" in (pack.get("warnings") or [])
    assert "connection_request_truncated_to_290" in (pack.get("warnings") or [])
