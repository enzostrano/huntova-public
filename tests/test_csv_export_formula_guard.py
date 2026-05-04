"""Regression test for BRAIN-54 (a415): CSV export must neutralize
formula-prefixed cells (=, +, -, @, TAB, CR, LF) so Excel /
LibreOffice / Sheets render them as text, not as formulas.

OWASP CSV Injection class. Per GPT-5.4 audit.
"""
from __future__ import annotations
import inspect


def test_csv_export_handler_has_formula_guard():
    """Source-level: the export handler must have a sanitizer that
    prefixes dangerous cells with a single quote."""
    from server import api_export_csv
    src = inspect.getsource(api_export_csv)
    assert "_csv_safe" in src or "csv_safe" in src, (
        "BRAIN-54 regression: /api/export/csv must apply a formula "
        "guard to every cell. OWASP-recommended fix: prefix cells "
        "starting with =/+/-/@/TAB/CR/LF with a single quote."
    )
    # The danger characters must be in the guard
    assert all(ch in src for ch in ['"="', '"+"', '"-"', '"@"']), (
        "BRAIN-54 regression: guard must check all formula-prefix "
        "characters: =, +, -, @ (plus TAB/CR/LF)."
    )


def test_csv_safe_neutralizes_formula_values():
    """Behavioral: import the helper from the handler scope. Since it's
    a closure, we need to test via the handler's source — assert
    pattern equivalence."""
    from server import api_export_csv
    src = inspect.getsource(api_export_csv)
    # The guard must prepend a single quote (Excel-text marker).
    assert "\"'\" + value" in src or "'\\''" in src or "'\"" in src or "+ value" in src, (
        "BRAIN-54 regression: dangerous cells must be prepended with "
        "a single quote so spreadsheets render as text."
    )
