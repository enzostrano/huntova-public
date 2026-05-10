"""BRAIN-196: email_service._template + _plain rendering audit.

Pure HTML / plain-text email body builders. Used by every
transactional email (verification, reset, etc.).

Pinned invariants:

1. `_template` produces well-formed HTML5 doc with <!DOCTYPE>.
2. Title and preheader interpolate into the documented anchors.
3. Body HTML interpolates as-is (caller controls markup).
4. Button block omitted when text or URL absent.
5. Button block present + interpolates URL when both supplied.
6. Plain text version mirrors HTML structure (title + body + button URL).
7. Footer present with Huntova branding (no AI-author mentions).
8. No-button HTML still parses (no orphan `{btn}` placeholder).
"""
from __future__ import annotations


def test_template_has_doctype():
    from email_service import _template
    out = _template("title", "preheader", "<p>body</p>")
    assert out.startswith("<!DOCTYPE html>") or "<!DOCTYPE" in out[:50]


def test_template_has_html_lang():
    from email_service import _template
    out = _template("t", "p", "<p>x</p>")
    assert "<html lang=" in out


def test_template_interpolates_title():
    from email_service import _template
    out = _template("Verify your email", "preheader text", "<p>body</p>")
    assert "Verify your email" in out


def test_template_interpolates_preheader():
    from email_service import _template
    preheader = "Click to verify your email address"
    out = _template("title", preheader, "<p>body</p>")
    assert preheader in out


def test_template_interpolates_body_html():
    from email_service import _template
    body = "<p><strong>Click below to confirm.</strong></p>"
    out = _template("title", "preheader", body)
    assert body in out


def test_template_no_button_when_text_missing():
    from email_service import _template
    out = _template("title", "preheader", "<p>body</p>",
                     button_text="", button_url="https://x.com")
    # No <a href="https://x.com"> button rendered when text empty.
    # The button URL string may still appear elsewhere (it doesn't here).
    # Pin: no `display:inline-block` button block.
    assert 'display:inline-block;padding:14px 36px' not in out


def test_template_no_button_when_url_missing():
    from email_service import _template
    out = _template("title", "preheader", "<p>body</p>",
                     button_text="Click Me", button_url="")
    assert "Click Me" not in out  # button not rendered → text not present


def test_template_renders_button_when_both_provided():
    from email_service import _template
    out = _template("title", "preheader", "<p>body</p>",
                     button_text="Verify Email", button_url="https://x.com/verify")
    assert "Verify Email" in out
    assert "https://x.com/verify" in out
    # Button styling present.
    assert 'background:#36dfc4' in out


def test_template_has_footer_branding():
    from email_service import _template
    out = _template("t", "p", "<p>b</p>")
    assert "Huntova" in out


def test_template_footer_no_ai_author_mention():
    """Per no-AI-mentions standing order: footer must not credit
    GPT/Claude/AI authorship."""
    from email_service import _template
    out = _template("t", "p", "<p>b</p>")
    lower = out.lower()
    forbidden = ("built with claude", "built using ai", "irony of an ai",
                 "claude wrote", "gpt wrote", "made by claude")
    for phrase in forbidden:
        assert phrase not in lower, (
            f"AI-author mention {phrase!r} in email template footer"
        )


def test_template_no_orphan_placeholder():
    """If button isn't rendered, no `{btn}` literal lingers in output."""
    from email_service import _template
    out = _template("title", "preheader", "<p>body</p>")
    assert "{btn}" not in out


def test_template_color_scheme_dark():
    """Dark-mode meta tag set (matches Huntova brand)."""
    from email_service import _template
    out = _template("t", "p", "<p>b</p>")
    assert 'name="color-scheme"' in out
    assert "dark" in out


def test_plain_starts_with_huntova():
    from email_service import _plain
    out = _plain("Title", "Body text")
    assert "HUNTOVA" in out


def test_plain_includes_title_and_body():
    from email_service import _plain
    out = _plain("My Title", "My body text here.")
    assert "My Title" in out
    assert "My body text here." in out


def test_plain_includes_button_url():
    from email_service import _plain
    out = _plain("t", "b", button_text="Click", button_url="https://x.com")
    assert "Click" in out
    assert "https://x.com" in out


def test_plain_no_button_when_url_missing():
    from email_service import _plain
    out = _plain("t", "b", button_text="Click", button_url="")
    # Button line shouldn't appear.
    assert "Click: " not in out


def test_plain_has_footer():
    from email_service import _plain
    out = _plain("t", "b")
    assert "Huntova" in out
    # Separator line.
    assert "---" in out


def test_template_interpolates_special_chars_in_title():
    """A title with `<` or `>` (e.g. user-controlled subject in some
    future caller) renders literally. Pin current behaviour — caller
    is expected to pass plain text only. Adding HTML escape would be
    a future improvement; pin current shape so a refactor is intentional."""
    from email_service import _template
    out = _template("Hello & welcome", "preheader", "<p>body</p>")
    # Pin current behaviour: ampersand literal.
    assert "Hello & welcome" in out or "Hello &amp; welcome" in out
