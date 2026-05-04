"""Regression tests for BRAIN-82 (a450): _gen_dna background
closure must capture epoch at spawn time + discard terminal
write if reset has happened since.

Per GPT-5.4 durable-workflow-stale-write audit. After BRAIN-78
(durable DNA state) + BRAIN-80 (durable reset) + BRAIN-81 (epoch
ratchet), a slow _gen_dna closure could still resurrect derived
state into a wizard the user just reset.
"""
from __future__ import annotations
import inspect


def test_gen_dna_captures_epoch_at_spawn():
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    has_capture = (
        "_dna_spawn_epoch" in src
        or "_captured_dna_epoch" in src
        or "_dna_epoch_at_spawn" in src
        or "spawn_epoch" in src
    )
    assert has_capture, (
        "BRAIN-82 regression: _gen_dna must capture epoch at spawn time."
    )


def test_dna_ready_mutator_skips_on_epoch_mismatch():
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    rm_idx = src.find("def _ready_mutator")
    assert rm_idx != -1
    block = src[rm_idx:rm_idx + 1500]
    has_epoch_compare = (
        "_wizard_epoch" in block
        or "_cur_epoch" in block
        or "spawn_epoch" in block
    )
    assert has_epoch_compare, (
        "BRAIN-82 regression: _ready_mutator must compare current "
        "epoch to captured spawn epoch."
    )


def test_dna_failed_mutator_skips_on_epoch_mismatch():
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    fm_idx = src.find("def _failed_mutator")
    assert fm_idx != -1
    block = src[fm_idx:fm_idx + 1500]
    has_epoch_compare = (
        "_wizard_epoch" in block
        or "_cur_epoch" in block
        or "spawn_epoch" in block
    )
    assert has_epoch_compare, (
        "BRAIN-82 regression: _failed_mutator must also bail on "
        "epoch mismatch."
    )


def test_epoch_captured_BEFORE_spawn_bg():
    from server import api_wizard_complete
    src = inspect.getsource(api_wizard_complete)
    spawn_idx = src.find("_spawn_bg(_gen_dna())")
    capture_idx = -1
    for needle in ("_dna_spawn_epoch", "_captured_dna_epoch",
                   "_dna_epoch_at_spawn", "spawn_epoch"):
        i = src.find(needle)
        if i != -1:
            capture_idx = i if capture_idx == -1 else min(capture_idx, i)
    assert spawn_idx != -1
    assert capture_idx != -1
    assert capture_idx < spawn_idx, (
        "BRAIN-82 regression: epoch capture must precede _spawn_bg."
    )
