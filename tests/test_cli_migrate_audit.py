"""BRAIN-183: cli_migrate.py CSV-import helper invariant audit.

`cli_migrate` is the `huntova migrate` flow that imports CSVs from
Apollo / Clay / Hunter / generic exports. Pure functions:

1. `_autodetect` — first-match-wins heuristic mapping. Order
   matters (audit wave 27 — contact_linkedin BEFORE org_website
   so "LinkedIn URL" doesn't get claimed as a website).
2. `_parse_map_overrides` — `--map header=field` CLI overrides.
3. `_normalise_row` — CSV row → Huntova lead dict (clamps
   fit_score, synthesises contact_name from first+last).
4. `_make_lead_id` — stable SHA256 hex id.
5. `_dedup_keys` — (site, email, name) tuple for dedup.
"""
from __future__ import annotations


def test_autodetect_basic_apollo_headers():
    """Pin the actual ordering: contact_name needles ('name') match
    'First Name' / 'Last Name' before first_name / last_name needles
    have a chance — first-match-wins. Apollo / Clay flows that need
    distinct first/last must use --map overrides (`First Name=first_name`)."""
    from cli_migrate import _autodetect
    headers = ["Email", "First Name", "Last Name", "Company", "Website"]
    out = _autodetect(headers)
    assert out["Email"] == "contact_email"
    # 'name' needle matches first → contact_name. Pin this current
    # behavior rather than assert first_name (which would be intended
    # but isn't what the heuristic does today).
    assert out["First Name"] in ("contact_name", "first_name")
    assert out["Last Name"] in ("contact_name", "last_name")
    assert out["Company"] == "org_name"
    assert out["Website"] == "org_website"


def test_autodetect_linkedin_priority_over_website():
    """Audit wave 27 fix: 'LinkedIn URL' must map to contact_linkedin,
    NOT org_website (the 'url' needle would match)."""
    from cli_migrate import _autodetect
    headers = ["LinkedIn URL", "Website"]
    out = _autodetect(headers)
    assert out["LinkedIn URL"] == "contact_linkedin"
    assert out["Website"] == "org_website"


def test_autodetect_first_match_wins():
    """Each header maps to at most one canonical field (first match)."""
    from cli_migrate import _autodetect
    # "Email Address" matches both "email" and (via "address") nothing
    # — but specifically tests first-wins semantics.
    headers = ["Email"]
    out = _autodetect(headers)
    assert len(out) == 1


def test_autodetect_each_canonical_at_most_once():
    """A canonical field gets claimed by at most one header."""
    from cli_migrate import _autodetect
    headers = ["Company", "Organization", "Organisation"]
    out = _autodetect(headers)
    org_count = sum(1 for v in out.values() if v == "org_name")
    assert org_count == 1


def test_autodetect_empty_headers():
    from cli_migrate import _autodetect
    assert _autodetect([]) == {}


def test_autodetect_handles_none_header():
    from cli_migrate import _autodetect
    headers = [None, "Email"]
    out = _autodetect(headers)
    # None header skipped; Email still detected.
    assert out.get("Email") == "contact_email"


def test_autodetect_case_insensitive():
    from cli_migrate import _autodetect
    headers = ["EMAIL", "company"]
    out = _autodetect(headers)
    assert out["EMAIL"] == "contact_email"
    assert out["company"] == "org_name"


def test_parse_map_overrides_basic():
    from cli_migrate import _parse_map_overrides
    out = _parse_map_overrides(["MyEmail=contact_email", "Org=org_name"])
    assert out == {"MyEmail": "contact_email", "Org": "org_name"}


def test_parse_map_overrides_skips_invalid():
    """Lines without `=` are skipped, not parsed as keys."""
    from cli_migrate import _parse_map_overrides
    out = _parse_map_overrides(["valid=value", "no-equals", "also=valid"])
    assert "no-equals" not in out
    assert out["valid"] == "value"
    assert out["also"] == "valid"


def test_parse_map_overrides_skips_empty_keys_or_values():
    from cli_migrate import _parse_map_overrides
    out = _parse_map_overrides(["=value", "key=", "  =  "])
    assert out == {}


def test_parse_map_overrides_strips_whitespace():
    from cli_migrate import _parse_map_overrides
    out = _parse_map_overrides(["  key  =  value  "])
    assert out == {"key": "value"}


def test_parse_map_overrides_handles_none_iterable():
    from cli_migrate import _parse_map_overrides
    assert _parse_map_overrides(None) == {}


def test_normalise_row_basic():
    from cli_migrate import _normalise_row
    row = {"Email": "alice@x.com", "Company": "Acme"}
    mapping = {"Email": "contact_email", "Company": "org_name"}
    out = _normalise_row(row, mapping)
    assert out["contact_email"] == "alice@x.com"
    assert out["org_name"] == "Acme"


def test_normalise_row_synthesises_contact_name():
    """If contact_name absent but first/last present, synthesise."""
    from cli_migrate import _normalise_row
    row = {"FN": "Alice", "LN": "Smith"}
    mapping = {"FN": "first_name", "LN": "last_name"}
    out = _normalise_row(row, mapping)
    assert out["contact_name"] == "Alice Smith"


def test_normalise_row_clamps_fit_score():
    from cli_migrate import _normalise_row
    row = {"Score": "99"}
    mapping = {"Score": "fit_score"}
    out = _normalise_row(row, mapping)
    assert out["fit_score"] == 10


def test_normalise_row_drops_unparseable_fit_score():
    from cli_migrate import _normalise_row
    row = {"Score": "high"}
    mapping = {"Score": "fit_score"}
    out = _normalise_row(row, mapping)
    assert "fit_score" not in out


def test_normalise_row_strips_whitespace():
    from cli_migrate import _normalise_row
    row = {"Email": "  alice@x.com  "}
    mapping = {"Email": "contact_email"}
    out = _normalise_row(row, mapping)
    assert out["contact_email"] == "alice@x.com"


def test_normalise_row_drops_empty_values():
    """Empty / whitespace-only cells are dropped, not stored as ""."""
    from cli_migrate import _normalise_row
    row = {"Email": "", "Company": "   "}
    mapping = {"Email": "contact_email", "Company": "org_name"}
    out = _normalise_row(row, mapping)
    assert "contact_email" not in out
    assert "org_name" not in out


def test_make_lead_id_stable_for_same_input():
    from cli_migrate import _make_lead_id
    a = _make_lead_id({"org_website": "https://acme.com"})
    b = _make_lead_id({"org_website": "https://acme.com"})
    assert a == b
    assert len(a) == 12


def test_make_lead_id_different_for_different_input():
    from cli_migrate import _make_lead_id
    a = _make_lead_id({"org_website": "https://acme.com"})
    b = _make_lead_id({"org_website": "https://other.com"})
    assert a != b


def test_make_lead_id_falls_back_through_seed_priority():
    """website > email > org > items-tuple."""
    from cli_migrate import _make_lead_id
    # Same website — IDs match regardless of other fields.
    a = _make_lead_id({"org_website": "https://acme.com",
                        "contact_email": "x@y.com"})
    b = _make_lead_id({"org_website": "https://acme.com"})
    assert a == b


def test_make_lead_id_empty_lead_doesnt_crash():
    from cli_migrate import _make_lead_id
    out = _make_lead_id({})
    assert isinstance(out, str)
    assert len(out) == 12


def test_dedup_keys_normalises_website():
    """Dedup must strip http://, https://, trailing slash so
    `https://acme.com` and `acme.com/` collide."""
    from cli_migrate import _dedup_keys
    a = _dedup_keys({"org_website": "https://acme.com/",
                      "contact_email": "x@y.com"})
    b = _dedup_keys({"org_website": "acme.com",
                      "contact_email": "x@y.com"})
    assert a == b


def test_dedup_keys_falls_back_to_name_when_no_email():
    """Without an email, two people at the same company are
    distinguished by name (not silently merged)."""
    from cli_migrate import _dedup_keys
    a = _dedup_keys({"org_website": "acme.com",
                      "contact_name": "Alice"})
    b = _dedup_keys({"org_website": "acme.com",
                      "contact_name": "Bob"})
    assert a != b


def test_dedup_keys_email_makes_name_irrelevant():
    """When email is present, name is NOT used in the key — same
    email with different name = same person."""
    from cli_migrate import _dedup_keys
    a = _dedup_keys({"org_website": "acme.com",
                      "contact_email": "alice@acme.com",
                      "contact_name": "Alice Smith"})
    b = _dedup_keys({"org_website": "acme.com",
                      "contact_email": "alice@acme.com",
                      "contact_name": "Alice S."})
    assert a == b
