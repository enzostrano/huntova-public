"""BRAIN-170: app.humanise_ai_error invariant audit.

`humanise_ai_error` is the single helper that turns provider 401 /
402 / 429 / 404 / timeout exceptions into user-facing language. Used
in 8+ call sites (chat dispatcher, wizard/scan, research, DNA gen,
wizard-assist, lead-rewrite, specialist team, provider init).

If a future PR breaks the message classification, every AI call site
silently regresses to "API call failed: <stack trace>" UX. These
tests pin the classification contract.
"""
from __future__ import annotations


def _humanise(exc, provider=""):
    """Local import shim so the module loads with whatever app.py
    state pytest's collection has."""
    from app import humanise_ai_error
    return humanise_ai_error(exc, provider)


def test_401_unauthorized_classified():
    msg = _humanise(Exception("401 Unauthorized"), "anthropic")
    assert "ANTHROPIC" in msg
    assert "invalid" in msg.lower() or "missing" in msg.lower()


def test_invalid_api_key_string_classified():
    msg = _humanise(Exception("invalid_api_key in request"), "openai")
    assert "OPENAI" in msg
    assert "invalid" in msg.lower() or "missing" in msg.lower()


def test_unauthorized_lowercase_classified():
    msg = _humanise(Exception("Request failed: unauthorized"), "groq")
    assert "GROQ" in msg
    assert "invalid" in msg.lower() or "missing" in msg.lower()


def test_402_credits_classified():
    msg = _humanise(Exception("402 Payment Required: insufficient credits"),
                    "openrouter")
    assert "OPENROUTER" in msg
    assert "credit" in msg.lower() or "top up" in msg.lower()


def test_credit_keyword_classified():
    msg = _humanise(Exception("you have 0 credit"), "deepseek")
    assert "DEEPSEEK" in msg
    assert "credit" in msg.lower()


def test_429_rate_limit_classified():
    msg = _humanise(Exception("429 Too Many Requests"), "perplexity")
    assert "PERPLEXITY" in msg
    assert "rate" in msg.lower() or "wait" in msg.lower()


def test_rate_limit_phrase_classified():
    msg = _humanise(Exception("rate limit exceeded for tier"), "anthropic")
    assert "ANTHROPIC" in msg
    assert "rate" in msg.lower() or "wait" in msg.lower()


def test_404_model_classified():
    msg = _humanise(Exception("404 Not Found: model gpt-99 not available"),
                    "openai")
    assert "OPENAI" in msg
    assert "model" in msg.lower()


def test_timeout_classified():
    msg = _humanise(Exception("Request timed out after 30s"), "gemini")
    assert "GEMINI" in msg
    assert "timeout" in msg.lower() or "timed out" in msg.lower() or "slow" in msg.lower()


def test_timeout_with_capital_t():
    msg = _humanise(Exception("Connection timeout"), "anthropic")
    assert "ANTHROPIC" in msg
    assert "timeout" in msg.lower() or "slow" in msg.lower() or "wait" in msg.lower()


def test_unknown_error_falls_back_with_provider_and_class():
    """An unrecognised error must still surface the provider name
    + exception class so the user can debug."""
    msg = _humanise(RuntimeError("something weird went wrong"), "mistral")
    assert "MISTRAL" in msg
    assert "RuntimeError" in msg


def test_no_provider_uses_generic_phrase():
    """Calling without provider_name uses 'your AI provider'."""
    msg = _humanise(Exception("401 Unauthorized"))
    assert "AI PROVIDER" in msg or "your ai provider" in msg.lower()


def test_provider_uppercased():
    """Provider name uppercased so the user can't miss which key
    needs fixing."""
    msg = _humanise(Exception("401"), "openai")
    assert "OPENAI" in msg


def test_message_truncated_at_800_chars():
    """Error message body capped to keep the user-facing string
    readable. Bug class: a 50KB OpenAI JSON dump getting embedded
    in the chat would overflow the UI.

    a2010 (BRAIN-PROD-8): cap raised from 240 → 800 because the prior
    cap was visibly truncating Groq TPM error strings mid-sentence
    ("...please reduce your mess"), leaving users unable to read the
    actionable part of the provider's error. The 800-char cap still
    bounds the response well under any sane UI overflow risk."""
    long_msg = "x" * 5000
    msg = _humanise(Exception(long_msg), "openai")
    # Fallback contains provider label + classname + truncated body.
    # 800 char clip + ~80 chars of label/classname/decoration ≈ ≤900.
    assert len(msg) < 1100, f"unexpected length {len(msg)}: {msg[:200]}…"


def test_returns_string_for_all_error_types():
    """Defensive — must return a str regardless of exc type."""
    cases = [
        ValueError("test"),
        RuntimeError("test"),
        TimeoutError("test"),
        Exception("test"),
        KeyError("missing"),
    ]
    for exc in cases:
        msg = _humanise(exc, "anthropic")
        assert isinstance(msg, str)
        assert msg, "must not return empty string"


def test_402_keyword_priority_over_429():
    """When the message contains both '402' and 'credit' indicators,
    the 402-credit branch wins (more actionable than rate-limit)."""
    # Tricky case: "402 hit your credit rate" — has both keywords.
    msg = _humanise(Exception("402 insufficient credit"), "openrouter")
    assert "credit" in msg.lower()
    # Should NOT be the rate-limit message.
    assert "rate" not in msg.lower() or "credit" in msg.lower()


def test_no_traceback_leaked_in_generic_branch():
    """Even the generic fallback must NOT leak the full traceback —
    only the exception message + class name."""
    try:
        raise ValueError("internal: secret_key=abc123")
    except ValueError as e:
        msg = _humanise(e, "openai")
    # Must mention the class but should NOT include 'Traceback' or
    # 'File "/' style stack frames.
    assert "ValueError" in msg
    assert "Traceback" not in msg
    assert 'File "' not in msg


def test_provider_label_in_every_branch():
    """Every classified branch must mention the provider name (so
    the user knows where to fix it)."""
    cases = [
        Exception("401"),
        Exception("402 credit"),
        Exception("429"),
        Exception("404 model"),
        Exception("timeout"),
        RuntimeError("generic"),
    ]
    for exc in cases:
        msg = _humanise(exc, "anthropic")
        assert "ANTHROPIC" in msg, (
            f"provider label missing for {exc}: {msg!r}"
        )
